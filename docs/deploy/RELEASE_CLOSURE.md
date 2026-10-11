# Release Closure Development

The new opensim-release-closure-request-v1 wraps a complete original V2 ledger
pin, a measured predicate policy/ledger pair, and deployment provenance pins.
Existing V1/V2/native-assessment consumers and coverage loops are unchanged.
The original evaluator must reach its final BridgeBlocked observation; an
earlier failure is not recoverable by supplementary claims.

Every predicate binds rule/URL/object/viewport/state, original file/row hash,
candidate sources/build/tool and complete scans. FAIL/nonzero/sidecar veto
cannot be covered by a supplement; original NOT_TESTED remains in the result.
Independent measured-data validators do not authenticate who ran a producer.
Actual human/public results still pass the original gate. TEST_ONLY fixtures
are never production acceptance. Production trust.verify is not changed.

The terminal function is a pure contract, not an authentication entrypoint.
It accepts only validated context and verified provenance from its caller.
Tests call the real signed-file verifier first, then the pure terminal function
with test_only=True, yielding NOT_CERTIFIED/test scope. No test manufactures a
production trust result or changes the original reader verdict.
There is no CLI test bypass or self-declared TRUSTED field in the new request.

## Fixed Interface

The candidate commits both the new gate and measured evaluator. CLI admission
requires the executing bytes to equal those blobs, not just self-reported SHA.
The original V2 reader still requires all existing G1/G2/G4 rules, native audit
readback with PNG/POST/log/receipt checks, public G5/FEM and six human materials.
Only its last noncertifying context is used; earlier FAIL is never bypassed.

```bash
sudo -n -u infra python3 -B tools/release_gate.py \
  --source-root /ABSOLUTE/CLEAN/CANDIDATE --commit FULL_SHA40 \
  --closure-request /hf3fs-jd/data/tmp/opensim-fluid-v1/NEW/request.json \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/NEW/gate.json
```

Request exact fields: schema=opensim-release-closure-request-v1,
original_ledger, policy, measurements, provenance. Each file reference has
exact path/sha256. Provenance keys are human:MANUAL_ID, deployment:INDEX and
user-approval:register only for an actual approved exception register. Each
value contains envelope/revocations pins. Deployment index0 binds the entire
original ledger; subsequent indices bind measured policy/ledger/baseline and
supplements in order. Envelope record_id and source.record_id equal that key,
source.sha256 equals the independently read material digest. Revocation source
equals the authorized channel receipt. No hash cycle includes a final gate.

The new closure uses S4 tools/release_trust.py at fixed source pin in
CLOSURE_TRUST_IMPLEMENTATION; old native/V2 bridge pins remain unchanged.
Production verify gets only envelope/revocations, candidate/material/role;
never caller root, clock or fixture channel. It must return exact result-v1,
PROVENANCE_VERIFIED/PRODUCTION_PROVENANCE_ONLY, NOT_CERTIFIED and an unexpired
protected channel proof. All roles/materials/candidate/frame hashes are checked
independently. Original files, inode identities, module sources and the whole
channel are verified again before and after publication. The caller must also
retain actual process exit status and exact final report hash.

## Measurement Boundary

Policy is shared-r41 with the unmodified union of required G3/G6 rules, all
eight original viewports, six states and six modes. Each object kind generates
its own required coordinates. Capability scans must include the complete
inventory at each coordinate; absence may close only standard-conditional
dialog/table/code/media rules, never a present object. A zero-failure label
alone closes nothing. Original PARTIAL/NOT_TESTED checks remain in originals;
precise positive supplements may close them but never overwrite source FAIL.

This module evaluates composited RGB measurements, focus/order/semantics,
targets/spacing, form/error/live state, long content, tables/media and current
graphic alternatives. It is not screenshot OCR, a live browser producer or a
screen-reader test. Its native original audit evidence is read by the original
gate, not converted into fabricated complete measurements. Current native v4/v7
reports do not themselves provide the entire new measured wire profile; that
producer mapping is NOT_IMPLEMENTED and cannot be called certified. Capability
inventory truth still needs independently authenticated producer execution.

## Actual Development Tests

The new test runner consumes original test_release_gate.Fixture actual files
and real readers. Synthetic frames, PNGs, HTTP metadata and human fixtures are
TEST_ONLY_NOT_PRODUCT_NOT_RELEASE; no actual public/human testing is claimed.
Only the fixture Git hooks bypass argument is removed before execution; no
reader or signature result is mocked. All private fixture keys stay in RAM.
Full-file closure and real S4 Ed25519 enrollment/envelope/revocation verification
compose into the test-scoped terminal. Wrong source/candidate/role/signature,
expiry and revocation reject. The production CLI rejects the same signatures
without independent production enrollment, and missing human remains closed.
Real /dev/full failure cannot leave an authoritative successful report.

```bash
sudo -n -u infra python3 -B tests/test_release_closure.py \
  --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-release-closure-implementation-20261011/NEW
```

No deployed trust authority, actual human acceptance, public producer rerun or
release certification is delivered. At the original S1 b7e17b58 implementation
checkpoint, sync_release.py still invoked the ledger interface. The separately
reviewed S4 e4f12162 implementation now adds exact desired-v2 and the actual
closure-request gate process, preserving desired-v1 and apply refusal. The new
f32d-based reviewed-closure candidate imports those exact runtime bytes, with
the existing owned bundle-mode tests retained. It does not modify the immutable
consumer sibling package or grant production enrollment. Raw audit statuses
remain unchanged even if supplemental predicates close their obligations.
