# Copy Control Regression

Development only. WEB-CONTENT-004 requires a named, keyboard-operable copy control and
perceivable feedback while retaining pre/code whitespace and local horizontal scrolling.
This narrow implementation copies the numerical report preview, not its complete source.

The original renderer generates the markup. The app reads code.textContent without trim
or JSON reserialization and uses the standard Clipboard API. A native button keeps focus.
Chinese polite atomic status records copying, copied or failed. Rejected/missing API and
empty content preserve an explicit error plus manual-selection/retry guidance; pending
copy prevents duplicate writes, without disabling unrelated FEM/export interaction.

Run the four-method narrow module in a new external child:

```bash
sudo -n -u infra python3 -B /data/dev/opensim-s1-copy-control-20261011/tests/test_copy_control.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-copy-control-20261011/independent-review-fresh
```

--method may select test_original_renderer_exact_preview_and_absent_evidence,
test_real_clipboard_click_keyboard_failure_and_retry, test_real_tabindex_minus_one_is_rejected
or test_existing_page_regression.
The embedded Chromium test is immutable repository source inside this Python file; it is
materialized with exclusive creation only into the fresh run directory. Original browser
audit/server/solver are unchanged dependencies. No product HTML is hand edited.

Real loopback Chromium at320x568/1440x900 checks native mouse/Tab/Enter/Space,
real clipboard write/read and byte SHA against original pre text, Chinese visible status,
focus,44px targets, no page overflow, pre formatting, two original FEM POST responses,
and actual health/page/JS/CSS identities before/after. Screenshot/POST/log bytes are pinned.
Whitespace/empty text and denied/missing/synchronous clipboard faults are TEST_ONLY
controlled DOM/API conditions, not real permission UI, touch or screen-reader evidence.
The duplicate-write case holds a real clipboard operation, then explicitly releases it.

## Sequential Tab Coverage

The earlier d09146c browser sequence clicked before Enter. That mouse focus cannot prove
Tab reachability; old evidence remains immutable and is not upgraded by this delta.
At both viewports the new positive sequence starts at the keyboard-opened summary before
any mouse copy. Real CDP Tab events must reach the native copy button; the observed focus
trajectory is recorded, a focused screenshot is captured, then Enter and Space copy.
No direct focus() call or synthetic DOM keyboard event may supply that reachability.

The test dynamically sets only the live test DOM button tabindex to -1. The same real Tab
oracle must reject after traversing the page without reaching the button. The original
attribute is restored in finally and another real Tab traversal must reach the button.
The separate negative browser child also proves mouse plus the earlier Enter path can
still copy with tabindex=-1, while the Tab oracle reports its exact AssertionError and rc1.
Its outer test passes only for that precise expected rejection, not arbitrary failure.
All controlled exclusions are TEST_ONLY, not product edits or human observations.

The two browser methods alone can run together in a fresh child, avoiding old page tests:

```bash
sudo -n -u infra python3 -B /data/dev/opensim-s1-copy-control-20261011/tests/test_copy_control.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-copy-control-20261011/tab-independent-fresh --method test_real_clipboard_click_keyboard_failure_and_retry --method test_real_tabindex_minus_one_is_rejected
```

The negative child has a separate tab-exclusion output directory. Both actual child exit
codes, first cause, exact named checks, source-before/after and Tab traces are retained.
The negative browser report stays FAIL even when its expected-rejection outer test passes.

The original missing-control red must remain. PASS closes only this new same-source narrow
copy scope, not the288 old matrix failures, unknown browser obligations, full matrices,
public acceptance, human tests or release certification. New integration requires a new
candidate/source/build binding. No680/fullUI/matrix rerun or rule exception is performed.
Production/root/SSH/push/credential/trust operations are forbidden in this development.
