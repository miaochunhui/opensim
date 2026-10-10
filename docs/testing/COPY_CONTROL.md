# Copy Control Regression

Development only. WEB-CONTENT-004 requires a named, keyboard-operable copy control and
perceivable feedback while retaining pre/code whitespace and local horizontal scrolling.
This narrow implementation copies the numerical report preview, not its complete source.

The original renderer generates the markup. The app reads code.textContent without trim
or JSON reserialization and uses the standard Clipboard API. A native button keeps focus.
Chinese polite atomic status records copying, copied or failed. Rejected/missing API and
empty content preserve an explicit error plus manual-selection/retry guidance; pending
copy prevents duplicate writes, without disabling unrelated FEM/export interaction.

Run the three-method narrow module in a new external child:

```bash
sudo -n -u infra python3 -B /data/dev/opensim-s1-copy-control-20261011/tests/test_copy_control.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-copy-control-20261011/independent-review-fresh
```

--method may select test_original_renderer_exact_preview_and_absent_evidence,
test_real_clipboard_click_keyboard_failure_and_retry or test_existing_page_regression.
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

The original missing-control red must remain. PASS closes only this new same-source narrow
copy scope, not the288 old matrix failures, unknown browser obligations, full matrices,
public acceptance, human tests or release certification. New integration requires a new
candidate/source/build binding. No680/fullUI/matrix rerun or rule exception is performed.
Production/root/SSH/push/credential/trust operations are forbidden in this development.
