"""Read-only runtime inventory; report package and executable paths, never secrets."""

import argparse
import importlib.util
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    packages = {}
    for name in ("numpy", "scipy", "requests", "websocket", "websockets", "playwright"):
        spec = importlib.util.find_spec(name)
        packages[name] = spec.origin if spec is not None else None
    result = {"packages": packages, "executables": {
        name: shutil.which(name) for name in ("node", "chromium", "google-chrome", "caddy", "dig")
    }}
    encoded = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
