"""P16-S3: the RH-2 probe verdict of release_rehearsal.py (pure; no Docker). The real rehearsal is a manual integration
run and is not collected here; this pins how its probe timeline is judged.

Run with the other production tests:
  python -B -m unittest discover -s deploy/production/tests -p 'test*.py'
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_rehearsal import probe_verdict  # noqa: E402

A, B = "s3-rh-a", "s3-rh-b"
DISCONNECTED = "error RemoteDisconnected"


def sample(post, shell=A):
    entry = {"post": post, "shell": shell}
    if post == 409:
        entry["release_mismatch"] = True
    return entry


def timeline(*middle):
    """404 on A, the freeze, backend B refusing A, then *middle, then web serving B."""
    return [sample(404), sample(404), sample(502), sample(504), sample(409), *middle, sample(409, B), sample(409, B)]


class ProbeVerdict(unittest.TestCase):
    def test_clean_release(self):
        verdict = probe_verdict(timeline(sample(409)), A, B)
        self.assertEqual(verdict["violations"], [])
        self.assertEqual(verdict["web_switch_errors"], [])

    def test_connection_error_during_the_web_switch_is_accepted(self):
        # Regression (completion run 2026-10-08): `up -d --no-deps web` replaced the container and one probe round
        # got no HTTP answer (RemoteDisconnected for the POST and GET /) between the last A shell and the first B shell.
        verdict = probe_verdict(timeline(sample(DISCONNECTED, DISCONNECTED)), A, B)
        self.assertEqual(verdict["violations"], [])
        self.assertEqual(verdict["web_switch_errors"], [5])

    def test_connection_error_outside_the_web_switch_fails(self):
        samples = [sample(404), sample(DISCONNECTED), sample(502), sample(409), sample(409, B)]
        self.assertIn("probe errors outside the web switch at samples [1]", probe_verdict(samples, A, B)["violations"])

    def test_pass_through_after_the_gate_fails(self):
        verdict = probe_verdict(timeline(sample(404)), A, B)
        self.assertIn("a write with release A passed after backend B started", verdict["violations"])

    def test_web_before_backend_fails(self):
        samples = [sample(404), sample(502), sample(404, B), sample(409, B)]
        self.assertIn("web served B before backend B refused A (writes reopened early)", probe_verdict(samples, A, B)["violations"])

    def test_no_freeze_seen_fails(self):
        samples = [sample(404), sample(409), sample(409, B)]
        self.assertIn("the probe never saw the freeze (backend stopped)", probe_verdict(samples, A, B)["violations"])

    def test_unexpected_status_fails(self):
        verdict = probe_verdict(timeline(sample(500)), A, B)
        self.assertIn("unexpected probe answers ['500']", verdict["violations"])

    def test_never_served_b_fails(self):
        samples = [sample(404), sample(502), sample(409)]
        self.assertIn("web never served B", probe_verdict(samples, A, B)["violations"])


if __name__ == "__main__":
    unittest.main()
