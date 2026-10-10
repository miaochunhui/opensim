# Release Gate File-Only Contradiction Regression

Scope: FILE_ONLY_PROTOCOL_CONTRADICTION_NOT_RELEASE.
Original goal: UNSATISFIABLE_FINAL_PATH. Final trusted-human sentinel: NOT_TESTED.
These tests do not complete the original final-human reachability task.

## Existing Constraint Conflict

On base 97477ba, unchanged tools/release_gate.py read_audit_scope accepts native
G3-accessibility only as PARTIAL or AUTOMATED_SCOPE_PASS (lines 692-693).
The same evaluate call later requires that pinned native G3 status be PASS
(lines 1098-1099). Exceptions cannot bypass this check (line 950).
The accepted sets are disjoint. The unconditional trusted-human verifier
refusal at lines 1155-1156 is therefore not reached by these complete fixtures.
This is an existing fail-closed protocol conflict, not evidence of false release
success. Gate changes require a separate chair decision and independent review.

## Actual Coverage

tests/test_release_gate_terminal.py uses real external files, Git commits, PNG
bytes, receipts and the unchanged gate and pinned pure audit reader. It imports
the existing Fixture builder, then replaces its historical validator with the
current candidate bytes, commits the fixture and rebinds every identity and pin
before consumption. All five source files and every required tool must match
the candidate bytes. Four viewports each include eleven whole/tile states:
44 coverage files, 88 PNGs, three POST metadata files, sixteen injections.
The real reader validates these dependencies, including hashes and dimensions.
Those numbers are fixed synthetic fixture geometry, not product coverage counts.
The reader dependency list is checked against the actual complete file set and
each stored SHA; its count is derived from that inventory, not a hardcoded 135.

Only two new tests are retained: a complete AUTOMATED_SCOPE_PASS reader positive
control (AUTOMATIC_SCOPE_OBSERVED / NOT_CERTIFIED), and a complete real gate CLI
regression with that same legal native status. The latter also checks its own
reader positive control before running the unchanged CLI, which must return
rc1, BLOCKED, with the exact first cause:
PARTIAL audit cannot certify G3-accessibility. That message is literal even when
the actual native status is AUTOMATED_SCOPE_PASS. No final human sentinel is
reached or counted. No success certification is mocked or manufactured.

Existing frozen tests/test_release_gate.py already covers PARTIAL at line 810,
fabricated PASS at line 833, and preserved NOT_TESTED at line 752. Those cases
are not duplicated or rerun here. The three-state constraint table is:

| Native G3 | Real reader | Same complete evaluate | Final human sentinel |
| --- | --- | --- | --- |
| PARTIAL | legal automatic scope | G3 rejection (existing test) | NOT_TESTED |
| AUTOMATED_SCOPE_PASS | legal automatic scope (new test) | G3 rejection (new CLI) | NOT_TESTED |
| PASS | reader rejection (existing test) | cannot reach G3 certification | NOT_TESTED |

The old five-test exploratory run is preserved separately; its redundant cases
are removed from the final module and must not inflate new coverage counts.

The fixtures have seven evidence and launcher file records, all G1..G6 records,
all manual file records and no approval or exception. Synthetic launcher text
is FIXTURE_NOT_PRODUCER_RUN; manual owner is FIXTURE_NOT_HUMAN; HTTP/CDP,
public, local and performance records are synthetic protocol inputs. No producer,
solver, browser, network, public CLI, deployment or human approval is executed.
No synthetic PASS is usable as public, human or release acceptance evidence.

Each consumption snapshots all fixture inputs with SHA, size, device/inode and
mtime, compares them afterwards and rechecks the clean fixture Git identity.
Real CLI argv/rc/stdout/stderr, first cause, gate report and durable log hash are
retained. The candidate tool/test/document SHA and HEAD are compared before and
after the suite. Only expected observation files may appear in each CLI case.
CLI stdout/stderr are captured as bytes without text newline conversion. Empty
streams remain actual zero-byte files, not EMPTY placeholders. launcher.json
records each stream path, actual byte count and SHA, and the test reads every
file back against the original subprocess bytes and size.
This proves file-input stability and no fixture publication side effects; it is
not a sandbox proof against arbitrary malicious code or a production rollback.

## Narrow Rerun

Use infra identity and a previously nonexistent output directory:

```bash
sudo -n -u infra python3 -B /data/dev/opensim-resume-release-gate-terminal-20261010/tests/test_release_gate_terminal.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/terminal-file-only-new-run
```

The direct runner selects only the two AS_PASS tests, never the old full suite.
It writes run-start.json, progress.log, execution.log, report.json,
source-binding.json and per-case observations. A test-suite PASS means the
specified rejection and reader observation regressions worked, not a release.
The one actual gate subprocess must remain BLOCKED/rc1. Preserve failed
run directories and use a fresh directory after diagnosing any fixture change.

The branch is independent of frozen main. Only this document and the new test
file are submitted, with no gate/source/producer or production modification.
S6 review is pending; the chair owns protocol resolution and any integration.

## Unimplemented Resolution

Do not change the reader to accept fabricated PASS or promote automatic scope
into human acceptance. A possible separate design is to represent the native
automatic observation independently of a trusted final G3 decision, bind both
to the same immutable candidate and enforce human provenance through a trusted
verifier. Such a verifier and that protocol change are NOT_IMPLEMENTED here.
User production authorization and actual human checks cannot be supplied by
these tests. The original final-human target remains incomplete.

## Retained Evidence

2026-10-10 20:00:00 +08:00: final narrowed precommit module ran two tests,
zero failures/errors, actual launcher rc0, candidate source/HEAD stable.
External root:
/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/terminal-as-pass-v2-20261010T2000/
Actual launcher log is the same root name followed by .launcher.log;
its terminal COMMAND_EXIT_CODE is 0. report.json SHA256:
deeb083193e0ddf24e7a711f7d67003066e9a2b466b0cabcd1893b8ca75c16a2.
source-binding.json SHA256:
c6196a9d4b0d83599ef490485a42ce471d4344eede5141bfced63598ac8b9f49.
The real gate subprocess launcher is in
cases/test_automated_scope_cli_reaches_g3_certification_rejection/launcher.json:
rc1 with the exact G3 rejection above. Its gate.json SHA256:
a281401bc78f1a3b6faca9fb45fb804ea5ac2df2a64edf454855c315d28506c9.

Earlier exploratory root terminal-file-only-v1-20261010T1957 remains unchanged:
five tests/rc0, including three redundant cases removed on user direction.
It is not the final coverage count. No test-suite red occurred; the intentional
actual gate BLOCKED/rc1 is the negative control, not a failed test-suite launcher.
The document gained this evidence section after the precommit run; a fresh
postcommit run must bind the final document and committed test bytes.
