"""Bounded, stateless HTTP application for the OpenSim fluid FEM workbench."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import platform
import re
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlsplit

import numpy
import scipy

from solver import InputError, SolverError, solve_case
from tools.render_page import SOURCE_PATHS, _source_metadata, render_page

ROOT = Path(__file__).resolve().parent
MAX_BODY = 16384
ASSET_TYPES = {"js": "text/javascript", "css": "text/css"}
FINGERPRINT_PATH = re.compile(r"/web/app\.([a-f0-9]{64})\.(js|css)")
RUNTIME_SOURCES = {
    relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    for relative in SOURCE_PATHS
}


def assert_runtime_sources() -> None:
    """A loaded process must not label old code with new on-disk identities."""
    for relative, expected in RUNTIME_SOURCES.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Runtime source changed: {relative}; start a new instance")


def reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def authority_parts(authority: str) -> tuple[str, int | None]:
    """Validate an HTTP authority without accepting userinfo or URL suffixes."""
    if not re.fullmatch(
        r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]*)(?::[0-9]{1,5})?",
        authority,
    ):
        raise ValueError("Invalid HTTP authority")
    parsed = urlsplit("//" + authority)
    port = parsed.port
    if not parsed.hostname or port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid HTTP host or port")
    return parsed.hostname.lower(), port


def accepts_gzip(value: str) -> bool:
    weights = {}
    for item in value.split(","):
        parts = [part.strip().lower() for part in item.split(";")]
        coding = parts[0]
        if not coding:
            continue
        weight = 1.0
        if len(parts) > 1:
            parameter = parts[1].split("=", 1)
            if (len(parts) != 2 or len(parameter) != 2 or parameter[0].strip() != "q"
                    or not re.fullmatch(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)",
                                        parameter[1].strip())):
                weight = 0.0
            else:
                weight = float(parameter[1].strip())
        weights[coding] = min(weights.get(coding, 1.0), weight)
    return weights.get("gzip", weights.get("*", 0.0)) > 0


def etag_matches(value: str, etag: str) -> bool:
    # If-None-Match uses weak comparison for GET and HEAD.
    return any(item.strip().removeprefix("W/") == etag or item.strip() == "*"
               for item in value.split(","))


class ApplicationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], evidence: Path | None = None):
        assert_runtime_sources()
        super().__init__(address, Handler)
        self.solve_slot = BoundedSemaphore(1)
        self.http_slots = BoundedSemaphore(24)
        self.evidence = evidence

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address

    def process_request(self, request, client_address):
        if not self.http_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.http_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.http_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenSim/1"

    def log_message(self, fmt: str, *args: object) -> None:
        logging.info("%s %s", self.client_address[0], fmt % args)

    def respond(
        self, status: int, body: bytes, content_type: str,
        cache: str = "no-store", etag: str | None = None,
    ) -> None:
        compressible = len(body) > 512
        compressed = compressible and accepts_gzip(
            ",".join(self.headers.get_all("Accept-Encoding", [])))
        if compressed:
            body = gzip.compress(body, mtime=0)
        if etag:
            etag = etag[:-1] + ("-gzip\"" if compressed else "-identity\"")
            if etag_matches(",".join(self.headers.get_all("If-None-Match", [])), etag):
                status = 304
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        )
        if etag:
            self.send_header("ETag", etag)
        if compressed:
            self.send_header("Content-Encoding", "gzip")
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        if status != 304:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and status != 304:
            self.wfile.write(body)

    def json_response(self, status: int, value: dict) -> None:
        self.respond(
            status, json.dumps(value, ensure_ascii=False, allow_nan=False,
                               separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def error_response(self, status: int, code: str, message: str, field: str | None = None) -> None:
        error = {"code": code, "message": message}
        if field is not None:
            error["field"] = field
        self.json_response(status, {"error": error})

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = self.request_path()
        if path is None:
            return
        if not self.runtime_ready():
            return
        if path == "/api/health":
            self.json_response(200, {
                "status": "ok", "schema_version": 1,
                "source_sha256": RUNTIME_SOURCES,
                "page_build": _source_metadata()[2],
            })
            return
        if path in ("/", "/index-v1.html"):
            try:
                html = render_page(evidence=self.server.evidence)
                body = html.encode("utf-8") if isinstance(html, str) else html
                assert_runtime_sources()
            except Exception:
                logging.exception("page generation failed")
                if not self.runtime_ready():
                    return
                self.error_response(500, "PAGE_ERROR", "页面生成失败，请联系维护者。")
                return
            self.respond(200, body, "text/html; charset=utf-8")
            return
        fingerprint = FINGERPRINT_PATH.fullmatch(path)
        if path in ("/web/app.js", "/web/app.css") or fingerprint:
            extension = fingerprint[2] if fingerprint else path.rsplit(".", 1)[1]
            file = ROOT / f"web/app.{extension}"
            try:
                body = file.read_bytes()
            except OSError:
                logging.exception("required static asset unavailable: %s", path)
                self.error_response(500, "ASSET_MISSING", "应用资源缺失，请联系维护者。")
                return
            digest = hashlib.sha256(body).hexdigest()
            if fingerprint and fingerprint[1] != digest:
                self.error_response(404, "NOT_FOUND", "资源指纹不存在。")
                return
            if not self.runtime_ready():
                return
            cache = ("public, max-age=31536000, immutable" if fingerprint
                     else "public, max-age=0, must-revalidate")
            self.respond(200, body, ASSET_TYPES[extension] + "; charset=utf-8",
                         cache, '"' + digest + '"')
            return
        self.error_response(404, "NOT_FOUND", "路径不存在。")

    def do_POST(self) -> None:
        path = self.request_path()
        if path is None:
            return
        if not self.runtime_ready():
            return
        if path != "/api/solve":
            self.error_response(404, "NOT_FOUND", "路径不存在。")
            return
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self.error_response(415, "CONTENT_TYPE", "请使用 application/json 提交参数。")
            return
        # The reverse proxy must preserve the public Host. Forwarded headers
        # are not trusted; TLS can terminate upstream of this HTTP listener.
        origins = self.headers.get_all("Origin", [])
        if origins:
            try:
                if len(origins) != 1:
                    raise ValueError("Repeated Origin")
                origin = origins[0]
                parsed_origin = urlsplit(origin)
                if (parsed_origin.scheme not in ("http", "https")
                        or origin != f"{parsed_origin.scheme}://{parsed_origin.netloc}"):
                    raise ValueError("Origin must contain only scheme and authority")
                hostname, port = authority_parts(parsed_origin.netloc)
                host, host_port = authority_parts(self.headers["Host"])
                default_port = 443 if parsed_origin.scheme == "https" else 80
                valid_origin = (host == hostname and
                                (host_port or default_port) == (port or default_port))
            except ValueError:
                valid_origin = False
            if not valid_origin:
                self.error_response(403, "ORIGIN", "不接受跨站计算请求。")
                return
        raw_length = self.headers.get("Content-Length")
        if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
            self.error_response(400, "BODY_LENGTH", "不接受分块传输或重复的 Content-Length。")
            return
        if raw_length is None or not raw_length.isascii() or not raw_length.isdecimal():
            self.error_response(400, "BODY_LENGTH", "请求必须提供有效的 Content-Length。")
            return
        if len(raw_length) > 5:
            self.error_response(413, "BODY_SIZE", "参数文件超过 16 KB。")
            return
        length = int(raw_length)
        if not 0 < length <= MAX_BODY:
            self.error_response(413, "BODY_SIZE", "参数文件为空或超过 16 KB。")
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete request body")
            config = json.loads(raw, parse_constant=reject_constant, object_pairs_hook=unique_object)
        except socket.timeout:
            logging.warning("incomplete request body timed out")
            self.error_response(408, "BODY_TIMEOUT", "参数传输未完成，请检查连接后重新提交。")
            return
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            logging.warning("invalid JSON: %s", exc)
            self.error_response(400, "BAD_JSON", "JSON 格式错误、含重复键或非有限数。")
            return
        if not self.server.solve_slot.acquire(blocking=False):
            self.error_response(503, "BUSY", "当前有计算正在进行，请稍后手动再次运行。")
            return
        try:
            result = solve_case(config)
            assert_runtime_sources()
            canonical = json.dumps(result["config"], sort_keys=True, allow_nan=False,
                                   separators=(",", ":")).encode("utf-8")
            result["reproducibility"] = {
                "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "config_sha256": hashlib.sha256(canonical).hexdigest(),
                "solver_sha256": RUNTIME_SOURCES["solver.py"],
                "source_sha256": RUNTIME_SOURCES,
                "python": platform.python_version(),
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
            }
            # Serialize within the exception boundary to reject invalid solver outputs.
            body = json.dumps(result, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
        except InputError as exc:
            self.error_response(422, "INVALID_CONFIG", str(exc), getattr(exc, "field", None))
            return
        except SolverError:
            logging.exception("finite element solve failed")
            self.error_response(500, "SOLVER_FAILED", "有限元求解失败，已保留服务端原因。")
            return
        except Exception:
            logging.exception("unexpected solve failure")
            self.error_response(500, "INTERNAL_ERROR", "计算出现内部错误，已保留服务端原因。")
            return
        finally:
            self.server.solve_slot.release()
        self.respond(200, body, "application/json; charset=utf-8")

    def request_path(self) -> str | None:
        target = self.path
        if (not target.startswith("/") or target.startswith("//")
                or not target.isascii() or any(ord(char) < 33 or ord(char) == 127
                                              for char in target)
                or "\\" in target or "#" in target
                or re.search(r"%(?![a-fA-F0-9]{2})", target)):
            self.error_response(400, "BAD_PATH", "请求路径格式错误。")
            return None
        try:
            path = urlsplit(target).path
        except ValueError:
            self.error_response(400, "BAD_PATH", "请求路径格式错误。")
            return None
        try:
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                raise ValueError("Exactly one Host is required")
            authority_parts(hosts[0])
        except ValueError:
            self.error_response(400, "BAD_HOST", "请求必须提供唯一且有效的 Host。")
            return None
        return path

    def runtime_ready(self) -> bool:
        try:
            assert_runtime_sources()
        except (OSError, RuntimeError):
            logging.exception("runtime source identity is no longer valid")
            self.error_response(503, "SOURCE_CHANGED", "服务源码已变更，请维护者启动新版本实例；旧实例不再接受计算。")
            return False
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--log-dir", type=Path,
        default=Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/logs"),
    )
    args = parser.parse_args()
    if args.evidence is not None and not args.evidence.is_file():
        parser.error("evidence file does not exist")
    args.log_dir.mkdir(parents=True, exist_ok=True)
    log = args.log_dir / (datetime.now(timezone.utc).strftime("server-%Y%m%dT%H%M%SZ") + ".log")
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log, encoding="utf-8"), logging.StreamHandler()],
    )
    with ApplicationServer((args.host, args.port), args.evidence) as app:
        logging.info("OPENSIM_SERVER_READY host=%s port=%s log=%s", args.host, app.server_port, log)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            logging.info("OPENSIM_SERVER_STOPPED reason=keyboard_interrupt")


if __name__ == "__main__":
    main()
