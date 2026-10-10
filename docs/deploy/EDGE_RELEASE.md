# OpenSim Edge / Cert Release

## Status

**NOT_PRODUCTION_READY. No signing, transfer, renewal registration, service
restart or live configuration write is enabled by this implementation.**
The tools intentionally fail closed rather than copy the old unsafe tools.
Passing file fixtures is not production recovery or public release acceptance.

The exclusive changes are `tools/edge_release.py`, `tools/cert_release.py`,
their two test files, and this document. Infra tools remain read-only.
Production domain is exactly `opensim.ai4everyone.top`; edges are exactly
`192.168.1.183` and `192.168.1.184`. No unit, envfile, binary or frpc is changed.

## Integration API

`edge_release.py inspect|dryrun|apply|revert` requires `--edge`,
`--transaction`, and `--out` (new directory below
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-edge/`).
Production inspect runs locally on the selected edge under the established
root chain. It emits a `PLAN_ONLY` receipt, latest SHA256, all conservatively
recognized baseline domains and a unique transaction-tagged append delta.
Unsupported/imported/empty domain inventories fail, not become empty PASS.

`dryrun --candidate PATH` requires an already staged, byte-exact candidate and
calls the actual `/usr/local/bin/caddy-edge validate --adapter caddyfile`
with the existing `/opt/docs-site/runtime/caddy.env`. It does not stage files,
read env contents, or restart. Failed validation is nonzero; private diagnostic
bytes stay in the exception in memory and are never copied into public logs.
`PLAN_ONLY` is neither Caddy validation unless present nor TLS acceptance.

**Production apply/revert always return 1**, even as root. There is no flag
that bypasses `EDGE-SHARED-LOCK-01`. Creating a private lock does not make the
legacy, non-participating shared writers safe. No SSH staging/install path
is invented. The verified existing login is `infra@.183/.184`; this does not
establish a root-only peer certificate transport.

Pure integration functions:

- `plan_append(live_bytes, transaction) -> (candidate_bytes, receipt)` preserves
  all baseline bytes and appends only the unique OpenSim block.
- `plan_revert(live_bytes, receipt, backup_bytes) -> reverted_bytes` validates
  the backup and receipt. Exact installed live returns the original backup;
  unrelated drift permits removal of only the exact own delta. Own-block,
  duplicate-marker, backup or receipt drift refuses without write.
- `apply_fixture(root, transaction, expected_sha, validate=None)` and
  `revert_fixture(root, transaction)` are for real file tests only. A
  `fixture-*` directory beneath seat-edge and exact `FIXTURE_ONLY` sentinel
  are mandatory. A pre-existing fixture lock, baseline SHA, fsync/atomic
  replace, byte verification and existing metadata are checked. Optional
  validator failure stops before install. They are not production APIs.

`cert_release.py inspect|check|apply|renew|revert --domain DOMAIN --out DIR`
only accepts the exact nonempty OpenSim domain. Inspect requires local .183
root and the existing executable/acme lock; it obtains a nonblocking lock
without creating it, does not read `account.conf`, and explicitly reports
unverified common-writer locking and broker.
Check runs root locally on an allowed edge and checks only the installed
OpenSim crt/key: full chain under system CA trust, server hostname/purpose,
exact sole DNS SAN, time validity with seven-day margin and DER public-key
equality. Python never reads key bytes; local OpenSSL reads the installed key.
The key is opened once with no symlink following, checked for root/docsweb
ownership and private mode, and passed by descriptor to OpenSSL.
Only public metadata is logged. Check is not runtime TLS or renewal proof.
**apply/renew/revert always return 1** before accessing keys or credentials.
In particular a renewal callback cannot falsely succeed or restart docs-site.
No callback is installed and no existing acme registration is modified.

## Bounded Gaps

`EDGE-SHARED-LOCK-01`, owner release coordinator + shared edge owner:
read-only discovery on 2026-10-10 found no
`/var/lock/gitlab-edge.lock` on .183; reviewed legacy edge writers do not lock.
Close with either a verified common-owner lock protocol OR an explicitly
authorized exclusive release window coordinated with every shared writer.
Lack of legacy locking alone does not prohibit an authorized exclusive
window; this seat has no such authorization. Root-held current hash/backup
comparisons must cover replacement and explicit config/restart/recovery
authorization must be recorded. Then implement and verify
.183 followed by .184, stop on first failure, enumerate every existing domain,
compare before/after TLS canaries (no `-k`), and run exact own-block/drift-safe
recovery under that same lock. `admin off` requires authorized restart, never
pretend reload/SIGUSR1. Must not overwrite unrelated unit/env/binary/frpc.

`CERT-ROOT-BROKER-01`, owner release coordinator + certificate owner:
.183 existing `/var/lock/dsh-acme.lock` and acme executable were observed;
all-writer locking and root-only broker were not established. The legacy
cert deployer transfers key ownership to infra in `/tmp` and restarts docs-site,
so it is not an acceptable implementation. Close only with an existing,
verified root-only end-to-end broker (no private bytes through infra, worktree
or 3FS), exact domain restriction, real pair validation, atomic last-good pair
recovery, fail-loud peer/notification failure, and separate ongoing renewal
restart authorization. Do not obtain credentials or change identity to
manufacture this prerequisite. DNS-01 signing/registration stays disabled.

Additional exact-path read-only checks found no broker at `/opt/cert-broker`,
`/usr/local/sbin/cert-broker`, `/usr/local/bin/cert-broker`,
`/usr/local/sbin/opensim-cert-broker`, or
`/usr/local/libexec/opensim-cert-broker` on the queried edges.
.183 had no `/root/.ssh/config`, `id_ed25519`, `id_rsa`, or `id_ecdsa`;
.184 had no queried edge/acme lock.
.183 `/root/deploy-opencad-cert.sh` and `/root/deploy-openmath-cert.sh`
exist, but approved operation-token inspection confirms the infra/temp-key
pattern, not a root-only broker. The read-only R640 shared cert deploy source
also explicitly states root-to-root is unavailable and uses infra staging.
These observations do not prove no other mechanism exists.
Minimum coordinator decision: supply an existing verified broker entrypoint
and established invocation identity without credential changes, or keep
signing/renewal blocked. Independently approve an exclusive edge window,
per-edge restart/recovery, and later renewal windows if production is wanted.

## Verification

Run as infra; `-B` avoids bytecode in the worktree. Fresh output directory
names are required; no report is overwritten.

```bash
sudo -n -u infra python3 -B /data/dev/opensim-edge-v1/tests/test_edge_release.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-edge/edge-v1
sudo -n -u infra python3 -B /data/dev/opensim-edge-v1/tests/test_cert_release.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-edge/cert-v1
```

The edge test optionally adds `--real-caddy-negative`: through the existing
SSH chain, it verifies baseline live SHA against the read-only infra baseline,
feeds baseline and exact candidate to each actual Caddy via stdin, requires
baseline validation success and candidate missing-OpenSim-TLS failure, probes
CAD/math with verified TLS, and checks live SHA unchanged. It does not upload,
restart, or claim all-domain/runtime/new-site/public acceptance.

`--remote-metadata` records exact-path presence/ownership, existing callback
operation tokens (never credential/key bodies), and edge/docs-site start
timestamps using one shell-quoted remote argument vector.
An early unquoted diagnostic regex accidentally attempted invalid unit
restarts; returned invalid-unit/authentication failures are an execution
deviation, not authorized operations. Subsequent read-only inspection found
both services on both edges active with original 2026-10-06 start timestamps.
No corrective restart or other production mutation was performed.

Latest verified evidence: `seat-edge/edge-v5/report.json` contains 16 passing
file/CLI tests and both actual Caddy negative controls, unchanged live SHA,
four verified CAD/math TLS+200 probes, and exact-path metadata.
`seat-edge/cert-v5/report.json` contains 15 passing actual OpenSSL tests.
These are local tooling and read-only evidence, not deployment completion.

Cert tests generate synthetic CA/leaf private keys only in process RAM/memfd;
only public CA/chain PEM and redacted reports enter external evidence.
Actual OpenSSL rejects malformed/missing/untrusted chains, wrong/additional
SANs, near/expired/not-yet-valid dates, and empty/mismatched public keys.
Generated evidence is scanned for private PEM headers. No production key,
credential, DNS record, renewal or service is touched.
