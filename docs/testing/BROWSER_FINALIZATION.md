# Browser Report Finalization

## Scope

`tools/browser_check.mjs` keeps `schema_version=1`, existing `mode` values,
acceptance checks and `release_verdict=NOT_CERTIFIED`. This change concerns
durable evidence finalization only. It does not complete G3/G6, certify a
product build, or substitute AX inspection for desktop/mobile screen readers.
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

Three unittest methods execute eight subprocess cases: one actual Chromium
selftest positive; six actual filesystem fault cases (`log-directory`:
EISDIR, `/dev/full` log write: ENOSPC, `/dev/full` log/post-publication fsync:
EINVAL, same-inode same-size byte rewrite, unrelated replacement directory);
and a missing-Chromium ENOENT followed by real fsync failure to verify first
cause retention. The fsync device cases exercise real kernel I/O errors at
their named finalization stages, not a claim of storage-device failure on 3FS.
The selftest still performs its existing ten checks, captures real PNGs and
downloads a fixture; its HTML is only a harness fixture, not product output.

Every invocation owns an ephemeral loopback fixture server and Chromium.
The test retains stdout/stderr, provisional/final/failure artifacts and
per-case summaries under
`/hf3fs-jd/data/tmp/opensim-fluid-v1/browser/finalize-test-*`; the parent
`finalize-suite-*` contains the unittest log and a summary linking all cases.
Negative fixtures are accepted only with `--selftest`, never a target URL.
No installation, production mutation, old full-product run or FEM run is
part of this focused validation. Final candidate integration belongs to the chair.

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
