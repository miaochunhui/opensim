# Fixed Application Release

This tool only manages the OpenSim application release and its dedicated
`opensim-web.service`. It does not implement DNS, TLS, edge configuration,
browser certification, notification, public synchronization or all-site hosting.
All examples are interfaces for an authorized caller, not production permission.
No production installation or restart was performed during this implementation.

## Trust And Integration

`tools/release_app.py` separates three trust domains:

1. `prepare()` runs as infra against an explicitly pinned, clean committed source.
   It exports blobs rather than copying mutable checkout files, runs the exported
   page generator without root privileges, and validates matching numerical
   evidence. Its result is a data bundle, never a supplied command or unit.
2. Production `--apply` and `--revert` require the reviewed controller at
   `/usr/local/libexec/opensim-release-app.py`. The file and every ancestor must
   be root-owned and not group/other writable. Root execution of this checkout
   script is rejected. The caller must separately obtain authorization to install
   that controller using the existing infra-to-root identity chain; verify its
   exact reviewed bytes before running it. Do not sudo a mutable worktree script.
   This bootstrap is intentionally not automated by the tool.
3. `--check --probe` runs as infra, never root. It fingerprints the fixed release,
   then executes only that release's solver as a numerical comparison oracle.
   The server also runs as infra. Privileged installation never imports or
   executes release code, build scripts or dependency installation commands.

The production caller must inspect and independently pin both the full commit
SHA and full manifest SHA256 from preparation. A hash authenticates only the
selected bytes, not who approved them. Neither a clean checkout nor numerics
PASS constitutes the complete `docs/PUBLIC_SYNC.md` release gate.
The coordinator must enforce browser/manual gates, exception expiry and
authorization before invoking `--apply`. The root-owned controller remains a
separate trusted code artifact and should be replaced only through its approved
review/bootstrap process when changing controller versions.

A controlled app log directory must already exist at
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/app-logs`, owned by infra.
The installer never chowns an existing shared directory, touches credentials,
changes login methods, or performs a recursive permission sweep.

## CLI And Python API

Every invocation requires a fresh `--out` directory below
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/`. Reports and execution logs
preserve first causes and subprocess exit codes. Existing evidence is not
overwritten. A failed action exits 1, including evidence-write failure.
`execution.log` records a provisional action result (`evidence_pending=1`).
Only the subsequently written `report.json` and final console verdict confirm
the invocation result. A log-write failure produces a failed report retaining
the original action result and first cause; a report-write failure emits no
final success verdict. Existing output directories are rejected without writes.
If a late persistence check fails after the exact report bytes exist, the tool
guardedly replaces only those bytes with a failed report. Revocation failure is
explicit and unverified. Consumers must require a successful process exit as
well as matching final evidence; no standalone JSON can prove disk durability
after an interrupted process.
Installed-file checks also reject missing, non-directory or unusable log paths.
Paths resolving through the existing 3FS mount are accepted; other symlink
parents, hardlinked inputs and unbounded payloads are rejected.

```bash
sudo -n -u infra python3 -B tools/release_app.py \
  --prepare --source-root /data/dev/opensim-release-v1 \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --evidence /hf3fs-jd/data/tmp/opensim-fluid-v1/NUMERICS_REPORT.json \
  --bundle /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/bundle-UNIQUE \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/prepare-UNIQUE
```

After the controller bootstrap has separately been authorized and verified,
the following is a read-only plan; it does not create release/state directories
or operate services. Add `--apply` only when installing is explicitly authorized.

```bash
sudo -n -u infra sudo -n /usr/bin/python3 -I -B \
  /usr/local/libexec/opensim-release-app.py \
  --bundle /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/bundle-UNIQUE \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --manifest-sha256 FULL_64_CHARACTER_MANIFEST_SHA256 \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/plan-UNIQUE
```

`--apply` without `--authorize-restart opensim-web.service` writes files only.
With that extra authorization it calls daemon-reload and restarts exactly
`opensim-web.service`; no other service is stopped, restarted or enabled.
Initial restart starts the dedicated app; reboot enablement remains a separate
explicitly authorized caller action. No `[Install]` target or enablement symlink
is silently added. The caller must not enable an unknown pre-existing unit.

```bash
sudo -n -u infra python3 -B tools/release_app.py --check --probe \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/check-UNIQUE
sudo -n -u infra sudo -n /usr/bin/python3 -I -B \
  /usr/local/libexec/opensim-release-app.py --revert \
  --authorize-restart opensim-web.service \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/revert-UNIQUE
```

An authorized revert of the first install stops only this app and restores
exact unit absence. An upgrade revert restores the byte-identical prior managed
unit and optionally restarts only that service. A release that was restarted
requires fresh service authorization for revert. All immutable releases,
receipts and historical evidence are retained, not deleted.

Python interface for coordinator tests:

```python
from tools.release_app import Layout, prepare, plan, install, check, revert

result = prepare(source_root, full_commit_sha, numerics_path, new_bundle_path)
layout = Layout()                       # fixed production paths
preview = plan(layout, bundle, manifest_sha256, full_commit_sha)
receipt = install(layout, bundle, manifest_sha256, full_commit_sha,
                  authorize_restart=False)
files = check(layout)                   # FILES_VALIDATED, not runtime PASS
runtime = check(layout, with_probe=True) # non-root, fixed private endpoint
restored = revert(layout, authorize_restart=False)
```

`Layout(fixture_root)` is non-root only and must be below the external seat
directory. Fixture operations never invoke systemd. Fixture probes accept only
`http://127.0.0.1:PORT` and report `LOCAL_TEST`, never public delivery.

## Manifest And State

Production release paths are
`/opt/opensim/releases/<full-commit>-<full-manifest-sha256>/`; the service pins
that directory directly, not a mutable worktree, symlink or `current` directory.
Files are `0444`, directories `0555`; production ownership is root. The app
cannot modify its source. All payload mtimes are the pinned commit time, making
PAGE metadata independent of export/install time. PAGE build uses the original
five runtime source hashes. A single relative `../evidence/numerics.json` path
is used for both rendering and serving, preserving complete HTML byte identity.

Manifest schema 1:

```json
{
  "schema": 1,
  "commit": "FULL_40_CHARACTER_COMMIT_SHA",
  "source_epoch": 1790000000,
  "source_sha256": {"solver.py": "SHA256", "OTHER_FOUR_RUNTIME_PATHS": "SHA256"},
  "page_build": "12_CHARACTER_SOURCE_BUILD",
  "files": {"source/solver.py": {"sha256": "SHA256", "bytes": 123}}
}
```

The abbreviated example is not a valid manifest. The exact payload paths are:

- `source/solver.py`, `source/server.py`, `source/tools/render_page.py`.
- `source/web/app.js`, `source/web/app.css`, `source/requirements.txt`.
- `evidence/numerics.json`, `build/index-v1.html`, `build/page-check.json`.

Unexpected files/directories, path traversal, symlinks, hardlinks, byte/hash
drift, writable release modes and source timestamps fail closed. Dependencies
must be exactly pinned numpy/scipy entries; the non-root probe checks installed
versions. Missing/empty/incomplete evidence or solver SHA mismatch is rejected.
Original numerical reports may reference external raw artifacts; the bundle
embeds the complete report, not a fabricated replacement or substituted PASS.
Independent raw-artifact acceptance remains the numerical gate owner's task.

Current receipt: `/var/lib/opensim-release-app/current.json`, root-owned `0644`,
inside a root-owned directory. It contains no secrets and is readable by the
non-root checker. Schema 1 keys are:

- `schema`, `state`, `commit`, `manifest_sha256`, `release`, `unit_sha256`.
- `previous`: previous active managed receipt or null; history is bounded.
- `before_unit_base64`: exact prior managed unit bytes or null, never unknown unit.
- `restart_authorized`, `collected_at`.

States: `INSTALLING`, `FILES_INSTALLED`, `SERVICE_RESTARTED_UNVERIFIED`,
`REVERTED`. Output-only states also include `BUNDLE_PREPARED`, `DRY_RUN`,
`FILES_VALIDATED`, `LOCAL_APP_PASS`, `FAILED`. File success is not service PASS.
If the install crashes before/after its unit write, the persisted `INSTALLING`
receipt permits guarded revert only when live bytes equal a known before/after
snapshot. Unknown unit or release drift is never overwritten. Failed revert
after a unit restore but before receipt publication requires explicit operator
inspection; do not retry by force or delete the receipt.

All state/unit replacement uses a held install lock, fsync, exact before-byte
and after-byte checks. Staging directories from a failed copy are retained for
inspection. These are cooperative managed transactions, not protection against
a malicious root concurrently changing files. Other privileged deployers must
use the same lock and honor the exact managed resource boundaries.

## Compute Acceptance And Limits

The service template is controller-owned, always `User=infra`, fixed
`192.168.1.30:8772`, fixed release source and bounded resource settings.
It accepts no command, arbitrary unit template, environment or port from a
release package. Python ignores `PYTHON*` environment and user site packages;
the fixed source directory remains importable for `solver` and `tools`.
No pip install, mutable workspace build, shell command or release exec runs
under root.

The probe checks runtime identity before/after, complete generated PAGE and
JS/CSS bytes, both fresh-pressure circle and rectangle computations, full FEM
mesh and velocity arrays, all metrics, solver/source/config digests and compute
time. Listening sockets, process existence, systemctl success and health 200
alone cannot produce `LOCAL_APP_PASS`. Private app PASS still does not prove
public HTTPS/DNS, two edges, accessibility, performance or perpetual availability.
The final public gate remains `tools/public_gate.py`.

Before a production switch, the tool rejects alternate fragments, application,
prefix and global service drop-in directories, generated/control overrides and
unexpected effective `LoadState`, `FragmentPath` or `DropInPaths`. Before restart
and during real probing, `NeedDaemonReload` must be `no`. Sites that use a global
`service.d` are intentionally rejected pending owner review, not silently ignored.
The coordinator must preserve authorized enablement policy and avoid concurrent
external systemd mutation. These checks do not certify a hostile root or replace
production operator review. Any conflict fails closed rather than forcing a switch.

Run independent real-filesystem tests and retained logs:

```bash
sudo -n -u infra python3 -B tests/test_release_app.py \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/test-UNIQUE
```

The test runner exports two real clean fixture commits, runs independent
numerics, installs actual files, verifies first-install removal and upgrade
byte-for-byte restoration, rejects live drift and crash states, and serves a
fixed-release loopback process for true compute. It never restarts production.
