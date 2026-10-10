# OpenSim Certificate Transport

## Checkpoint And Scope

**OFFLINE ONLY / NOT PRODUCTION READY.** This module never connects to an edge,
reads an installed secret, installs a controller, persists a recipient, writes a
certificate/key pair, signs via ACME, changes credentials, or restarts services.
All production CLI actions return nonzero, without a bootstrap bypass flag.
The existing edge/cert tools remain unchanged.

Review checkpoint for independent security seat
`01a124d0-8b01-7af0-ad7f-e3b820c3f671`: the implementation interfaces are
`RecipientSession(source_pin, now, ttl).offer() -> (canonical_request,
request_sha256, recipient_sha256)`, `seal(request, source_memfd, chain,
replay=ReplayGuard, request_pin, recipient_pin, now) -> ciphertext_frame`, and
`RecipientSession.accept(frame, explicit_trust, now, minimum_seconds) ->
public_validation_report`. `accept` never returns private material.
This document is an on-disk checkpoint, not a claim that a review message was
delivered or that independent review passed.

## Protocol

The only direction is `192.168.1.183` to `192.168.1.184`, and the only domain is
`opensim.ai4everyone.top`. A canonical version-1 request specifies the exact
`transfer` action, endpoints/domain, 256-bit random nonce, integer issue/expiry
times (at most 120 seconds), ephemeral recipient certificate and its complete
SHA256 pin, plus an independently approved complete source-chain SHA256 pin.
Empty, noncanonical, duplicate, unknown, missing, oversized, future or expired
requests are rejected. Neither CN fallback, wildcard nor additional SANs pass.

The synthetic target creates a new 3072-bit RSA encryption key in RAM and
publishes only its self-signed recipient certificate. This is an encryption
certificate, NOT an SSH login credential or a trusted CA. The source must
receive request/recipient pins from trusted controller state independently of
the untrusted infra relay. Pins received only in the same relay message do not
authenticate the recipient. The source pin must likewise come from trusted
root inspection of the exact approved OpenSim certificate, not the offered
payload. An infra operator capable of choosing those trusted pins can substitute
a recipient and steal a key: the protocol does not protect against that.

Inside the source endpoint only, the source signs the canonical complete
request/certificate/key content using the existing synthetic certificate key
(SHA256 via OpenSSL `dgst`). The signature and content are then encrypted using
OpenSSL CMS AuthEnvelopedData with AES-256-GCM and RSA-OAEP/SHA256. No symmetric
key or private PEM is passed in argv/env. OpenSSL private descriptors are sealed
memfds and closed after use; subprocess stdout/stderr stay in endpoint memory.
Failures expose only operation and exit code, never OpenSSL diagnostic bodies.

The source uses one `ReplayGuard` for its controller lifetime, not a new guard
per message. A nonce is burned before plaintext read/sign/encrypt; repeated or
failed requests cannot be retried. This is process-local state, not a durable
replay service. Trusted bootstrap must not reauthorize an old request after
process restart. Both endpoints recheck expiry after their crypto operations.

The relay handles only public request and ciphertext. The target burns its
single session before parsing any response, including invalid responses. It
checks wall time and monotonic deadline, rejects truncated/trailing DER input,
enforces the AuthEnvelopedData /
AES-256-GCM / RSA-OAEP suite from the OpenSSL CMS structure (no CBC downgrade),
including explicit SHA256 OAEP and MGF1 parameters with empty label, one
recipient, 12-byte nonce and 16-byte GCM tag. A bounded DER reader checks those
fields before private decryption; implicit SHA1 defaults are rejected.
decrypts/authenticates, checks the
exact inner request and pinned source chain, verifies the source signature,
then verifies chain under independently supplied synthetic CA trust (not the
presented root), server purpose, exact sole DNS SAN, time validity with a
seven-day margin, and DER certificate/key equality. No retry after tampering,
failure or success; concurrent attempts admit at most one. Closing or restarting
loses the recipient; this module has no session restoration API.
Offers also enforce wall and monotonic expiry under the session lock and close
expired sessions before exporting even the public request.

Encryption alone is not source authentication. The pinned source certificate,
signature and independently supplied trust are all needed. This is not a
freshness/revocation service or proof that the source controller was uncompromised.
The certificate signature operation assumes OpenSSL-supported EC/RSA key types;
unsupported material fails nonzero without algorithm fallback.

## Trust And Production Debt

Python bytes and memfds are RAM storage, NOT secure erasure, mlock, swap/core
protection or a malicious-code sandbox. Offline source/recipient/relay roles
run under the same infra UID, which can inspect those processes and memfds.
Tests demonstrate the honest relay data path does not receive source plaintext;
they do not demonstrate OS separation from hostile infra. Production root
controllers must not run from this infra-writable repository.

Production needs an independently reviewed, root-owned and non-infra-writable
controller and all code/dependencies/parent directories it trusts, on both
fixed edges. Trusted state must bind operation authorization, exact source
certificate and recipient/request pins, domain/direction, owner and expiry,
with no arbitrary paths/commands. It must open the real source key once with
no symlink following, verify root ownership, regular type, one link and
0400/0600 mode on that descriptor, and retain private bytes only within root.
`check_key_metadata` is an offline-testable policy helper, not a real key reader.
The executing controller must confirm the actual local edge identity.

The existing identity chain is strictly `infra SSH -> remote sudo -n`.
`ssh_plan` returns (does not execute) a fixed controller invocation with
`StrictHostKeyChecking=yes`, batch mode, no agent forwarding/TTY/forwarding.
Existing known_hosts and SSH configuration/host identity require owner review;
do not disable checking, use a new login key or invent root-peer credentials.
Pin conveyance must be protected from infra substitution by reviewed controller
authorization state; host-key checking by itself does not create that state.

Missing bootstrap, trust ownership, authorized certificate-owner window or
operation authorization means **nonzero refusal**. This version deliberately
does not implement persistence or permit production use even if a caller
claims those prerequisites. A reviewed controller must own session lifecycle,
wall/monotonic expiry, replay state, private IPC, core/swap/process observability
policy, bounded resource admission and root-only last-good pair validation.
Atomic pair install/recovery, all-writer ACME/edge locking or approved exclusive
window, renewal ownership and restart/recovery authorization remain separate
unimplemented prerequisites. No signing, renewal, installation, public TLS,
canary or production recovery acceptance has been established here.

## Verification

Run as infra without installing dependencies. Each evidence output must be a
new directory, never reused; only redacted reports/logs are persisted.

```bash
sudo -n -u infra python3 -B /data/dev/opensim-edge-v1/tests/test_cert_transport.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-cert-transport/offline-new-run
sudo -n -u infra python3 -B /data/dev/opensim-edge-v1/tools/cert_transport.py transfer
```

The test runner records actual test count, individual outcomes, safe OpenSSL
failure coordinates, module SHA256, protocol verdict and production debt.
It runs actual OpenSSL-generated synthetic keys only in RAM/sealed memfds,
actual encryption/decryption/signature and chain validation, plus negative
controls. Evidence is scanned for private PEM before and after writing, with
a runtime-constructed positive detector control; no key/CA fixture is persisted.

Latest checkpoint on 2026-10-10: `seat-cert-transport/offline-v5/report.json`
and `execution.log` have **46/46 PASS**, zero errors/failures and zero private
PEM evidence hits, with `production_ready=false` and independent review
`PENDING`. Actual roundtrip and signed wrong-domain rejection, tamper, replay
at both endpoints, expiry during crypto, concurrent replay, wrong key/recipient,
untrusted chain, source ownership policy, no plaintext relay delivery,
strict SSH plan and production refusal are covered. The CLI `transfer` was
separately observed to exit 1 with `CERT_TRANSPORT_BOOTSTRAP_REQUIRED`.

`offline-v1` remains FAIL (38 tests / one error): an isolated child generated
its certificate after the fixed test-start time and correctly failed
not-yet-valid chain verification. The harness now samples time after fixture
creation and uses current time for the isolated child roundtrip. It does not
weaken certificate validity checks. Subsequent v2/v3/v4 remain separate
38/43/46-test checkpoints, not retroactive acceptance of v1.

The existing shared secret scanner reported one public PEM-regex literal in
this module before it was split without semantic change. The post-fix
`precommit-secret-scan-v3.json` has no findings; the same scanner's positive
control finds the two existing public PEM regex literals in the previous
edge/cert commit. Those older tools remain read-only and unchanged, and their
findings are not private-key exposure or permission to edit their history.
