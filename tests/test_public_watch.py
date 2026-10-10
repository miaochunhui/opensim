"""Fault/recovery notification transitions, without contacting the relay."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from public_watch import RELAY, notification_for, relay_ack


class WatchTransitionTests(unittest.TestCase):
    def report(self, verdict):
        return {"verdict": verdict, "scope": "PUBLIC_CODE_AND_FEM",
                "collected_at": "2026-10-10T09:00:00+00:00",
                "url": "https://opensim.ai4everyone.top",
                "failure": {"reason": "DNS unresolved"}}

    def test_failure_and_recovery_have_distinct_titles(self):
        fail = notification_for(None, self.report("FAIL"), Path("/evidence/a.json"))
        recovered = notification_for({"notified_verdict": "FAIL"}, self.report("PASS"),
                                     Path("/evidence/b.json"))
        self.assertNotEqual(fail[0], recovered[0])
        self.assertIn("故障", fail[0])
        self.assertIn("恢复", recovered[0])

    def test_unchanged_state_does_not_flood(self):
        for verdict in ("PASS", "FAIL"):
            self.assertIsNone(notification_for({"notified_verdict": verdict},
                                               self.report(verdict), Path("/evidence/a.json")))

    def test_local_and_running_are_not_accepted(self):
        for changes in ({"scope": "LOCAL_TEST"}, {"verdict": "RUNNING"}):
            report = {**self.report("PASS"), **changes}
            with self.assertRaises(ValueError):
                notification_for(None, report, Path("/evidence/a.json"))

    def test_queue_ack_is_not_delivery(self):
        self.assertEqual(relay_ack(200, RELAY, b"spooled\n"), "QUEUED_NOT_DELIVERY_VERIFIED")
        self.assertEqual(relay_ack(200, RELAY, b"sent"), "RELAY_SENT")
        for status, url, body in ((200, RELAY, b"unrelated"), (403, RELAY, b"sent"),
                                  (200, "http://127.0.0.1/notify", b"sent")):
            with self.subTest(status=status, url=url), self.assertRaises(RuntimeError):
                relay_ack(status, url, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
