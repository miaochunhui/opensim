# Browser Report Finalization

## Scope

`tools/browser_check.mjs` keeps `schema_version=1`, existing `mode` values,
acceptance checks and `release_verdict=NOT_CERTIFIED`. The protocol concerns
durable evidence finalization; the focused view-binding addition below prevents
mislabelled field/mesh captures. Neither completes G3/G6, certifies a
product build, or substitutes AX inspection for desktop/mobile screen readers.
The separate audit tool and its PARTIAL coverage remain unchanged.

## Protocol

The main path finishes browser/server cleanup and awaits queued log writes.
`finalizeBrowser` then performs these steps in order:

1. Exclusively create, file-sync, directory-sync and read back
   `report.provisional.json`. Its verdict is `PROVISIONAL_NOT_FINAL`, exit code
   is 2, and durability state is `PROVISIONAL_NOT_CONSUMABLE`.
2. Append a candidate terminal line to the actual `run.log`, file-sync it,
   read its descriptor, directory-sync, and verify its exact inode and bytes.
3. Exclusively create `report.json`, file-sync and directory-sync it, then
   verify both final log and report again. No success log append follows it.

The final report adds `durability.protocol=browser-durable-final-v1`,
`durability.state=FINAL_AFTER_DURABLE_LOG`, and `durability.log` containing
`file`, `bytes`, and `sha256` for the complete terminal log. A final state is
not itself a passing acceptance verdict: failed browser checks can also be
durably recorded. Consumers must check the original mode, verdict, exit code,
scope and release limitations, require successful invocation completion,
reject any `report.failure.json`, and verify the recorded log bytes/hash.
Provisional, preserved and revoked reports are never success evidence.

Readback uses bounded descriptor reads (16 MiB maximum), `O_NOFOLLOW` and
`O_NONBLOCK`, regular-file checks, descriptor stat before/after, named-path
lstat, dev/inode/birthtime identity, size/mtime/ctime stability during reading,
and full byte equality. Log bytes are read from the opened append descriptor,
not an unbounded path read. A size limit violation fails loudly.

## Failure And Ownership

A persistence error produces actual CLI exit 2, verdict FAIL,
`persistence_failure` with original stage/code/syscall/path/stack, and
`first_failure` retaining an earlier browser or cleanup failure. Close errors
are secondary to a preceding I/O error. A durable `report.failure.json` is
attempted; if unavailable, stderr retains the complete failure and the CLI
does not advertise a nonexistent report path.

Only a report created by this invocation and still matching its exact inode
and bytes is eligible for revocation. It is hard-linked to a unique
`revoked-owned-*.json`, checked again and unlinked from the final name, with
directory synchronization. Same-inode changed text and unrelated replacement
files/directories are preserved as `UNKNOWN_OR_CHANGED_NOT_REMOVED`; the
failure marker and rc=2 invalidate that run. Unknown bytes are not rewritten.

This requires exclusive ownership of each randomly named run directory.
POSIX has no atomic compare-by-inode-and-bytes-and-unlink operation; these
checks are not a sandbox against a hostile writer racing between the final
comparison and unlink. Crash/power-loss recovery and malicious concurrent
directory replacement are NOT_TESTED. A reader must not consume a visible
candidate report while the producer is still running.

## Reproduction

From the audit worktree:

```bash
sudo -n -u infra python3 -B tests/test_browser_finalize.py
```

Four unittest methods execute eleven subprocess cases: one actual Chromium
selftest positive; three actual DOM view-fault cases; six actual filesystem
fault cases (`log-directory`:
EISDIR, `/dev/full` log write: ENOSPC, `/dev/full` log/post-publication fsync:
EINVAL, same-inode same-size byte rewrite, unrelated replacement directory);
and a missing-Chromium ENOENT followed by real fsync failure to verify first
cause retention. The fsync device cases exercise real kernel I/O errors at
their named finalization stages, not a claim of storage-device failure on 3FS.
The successful selftest performs its existing ten checks plus one view-binding
check, captures real PNGs and downloads a fixture; its HTML is only a harness
fixture, not product output. View faults must return actual rc=1 and a durable
FAIL report, not persistence rc=2, skip or expectedFailure.

Every invocation owns an ephemeral loopback fixture server and Chromium.
The test retains stdout/stderr, provisional/final/failure artifacts and
per-case summaries under
`/hf3fs-jd/data/tmp/opensim-fluid-v1/browser/finalize-test-*`; the parent
`finalize-suite-*` contains the unittest log and a summary linking all cases.
Negative fixtures are accepted only with `--selftest`, never a target URL.
No installation, production mutation, old full-product run or FEM run is
part of this focused validation. Final candidate integration belongs to the chair.

## Selected View Binding

The product exposes independent `#view-field` and `#view-mesh` toggles, not
mutually exclusive tabs. `selectView` reads their actual `aria-pressed` values,
uses existing real pointer/touch or keyboard activation only when a toggle
differs from its desired value, and confirms the result after rendering frames.
It never assigns product DOM state. Re-selecting an already correct view must
not switch it off. Core flow selects field before its baseline canvas, selects mesh
before mesh capture, and selects field again after reset before field capture.
The named mesh screenshot probe follows the same contract.

`screenshot(name, expectedView)` checks the actual state before capture, before
and after each real PNG capture, and after scroll restoration. Explicit field
requires field=true and mesh=false; mesh requires the inverse. Missing,
duplicate or invalid controls and unexpected selection throw `BROWSER-VIEW`
with expected/actual state. No differently named capture is substituted.
Every capture manifest, report capture entry and PNG entry includes actual
`selected_view` (`field`, `mesh`, `field+mesh`, `none`, or `unavailable` when both
controls are absent on a diagnostic/non-product page). Explicit view captures
never accept `unavailable`. Generic diagnostics record state without claiming
field/mesh. View changes fail even for generic captures.

The real DOM selftest proves touch selection of mesh, keyboard selection of
field, repeated-selection stability, rejection of wrong/both/none/invalid
states and no artifact creation for an initial wrong view. Three subprocess
faults (`--view-fixture wrong-selection`, `stuck-toggle`, `capture-change`)
prove rc=1, first-cause state and no misleading completed manifest. For a
later segment failure, earlier valid PNGs remain as partial evidence and the
run is FAIL. These samples check DOM state binding, not the physical meaning
of fixture pixels, human device use, or product FEM correctness. State sampling
is not atomic against hostile page code toggling away and back during capture;
no malicious same-UID or page-code sandbox claim is made.

## Validation Handoff (2026-10-10)

All paths below are relative to the external browser evidence root above.
The initial `finalize-suite-1791621934844136039/summary.json` is FAIL:
three methods, seven failures, zero errors. Its first case summary is
`finalize-test-1791621934869609911/summary.json`: `AssertionError: 10 != 9`.
The new test incorrectly expected nine checks; the unchanged browser
selftest actually produces ten. Six fault subcases and the positive case
stopped at that count assertion; the earlier-browser-failure case passed.
This was not evidence that the finalizer was accepted. These original logs,
reports and failure summaries remain intact and are not retroactively PASS.

The test expectation was corrected to ten without removing browser checks.
Before rerunning, the finalizer also replaced its initial unbounded path log
read with a bounded read on the opened descriptor, preserved primary I/O
errors against secondary close errors, and stopped returning a report path
when writing the failure marker failed. These hardening changes are distinct
from the diagnosed count error, not invented causes of the old seven failures.

Final `finalize-suite-1791622209638594524/summary.json` and `unittest.log`
were read after completion: PASS, three methods, eight cases, zero failures,
zero errors, 50.239 seconds. The tested browser tool SHA256 is
`85a2301509cea645f69209b2f50e3afb0d549c8ab5686c92b10ccf6b57fc038a`.
Positive `finalize-test-1791622252769514838/summary.json` records rc=0,
Chrome 140.0.7339.16, ten checks and three hashed PNGs. All seven negative
invocations record actual rc=2, including the retained ENOENT first cause.
`finalize-test-1791622238138685487/summary.json` verifies same-inode changed
bytes remain; `finalize-test-1791622231020243125/summary.json` verifies only
exact-owned report revocation after actual EINVAL fsync. The suite links every
case report. Syntax and diff-whitespace checks passed; no full-product or FEM
acceptance was run. The chair must test the final integrated candidate.

## Focused P3 Validation (2026-10-10)

`finalize-suite-1791625719834987312/summary.json` and `unittest.log` were
read after actual rc=0: PASS, four unittest methods, eleven subprocess cases,
zero failures/errors, 77.600 seconds. Tool SHA256 before/after every case and
in the actual reports matches
`d2448e302347da7b7eddb89105c46c78496b48f2899ab2a9e43a47f0de174862`.
The seven existing persistence/first-cause negatives still return rc=2.
Positive `finalize-test-1791625766902867909` returns rc=0, eleven checks,
five hashed PNGs; field/mesh manifests and PNG entries have matching actual
`selected_view`. Its final complete log is 1,334 bytes with SHA256
`4a5b2e1df08c6fee9b507d2f178a32660a0f86c2a8ad406175d580a54ba04ba8`.

All three new DOM faults return actual rc=1 with durable FAIL and
`BROWSER-VIEW` as the original failure: `finalize-test-1791625774750171951`
(wrong-selection), `finalize-test-1791625782154252794` (stuck-toggle),
`finalize-test-1791625789386789949` (capture-change). The first writes no
wrong-view capture; the last preserves earlier valid PNGs but creates no
completed field manifest. Test assertions verify complete terminal log hashes
and failure details. No original red evidence was overwritten.

The clean browser worktree lacked the integrated finalization test/docs.
Its three-file baseline commit `0c8ee8d` is byte-equivalent to frozen main
`75beb17` for those paths, and is NOT an integration request. Only the subsequent
focused P3 commit is for chair review/integration; its test diff appends view
coverage while retaining the main finalization cases. Syntax and diff-whitespace
checks pass. Full browser, product FEM and human device validation were NOT_RUN.
