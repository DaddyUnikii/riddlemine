"""Genuine local IEO/Locker/Distributor lifecycle controls, not mock claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/fixtures/pancake_v3/lifecycle.py"


@unittest.skipUnless(os.environ.get("RUN_LOCAL_V3") == "1", "set RUN_LOCAL_V3=1 for genuine local V3 execution")
class LifecycleV3Tests(unittest.TestCase):
    def run_case(self, name):
        self.assertTrue(RUNNER.is_file(), "portable lifecycle runner is missing")
        with tempfile.TemporaryDirectory(prefix="rdl-lifecycle-v3-") as directory:
            out = Path(directory) / "case"
            command = [sys.executable, str(RUNNER), "--ingot", str(ROOT), "--out", str(out), "--case", name, "--stage", "guarded"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=200)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            cleanup = json.loads((out / "process-cleanup.json").read_text())
            self.assertTrue(cleanup["terminated"])
            rows = {}
            for path in out.glob("*result.json"):
                rows[path.name] = json.loads(path.read_text())
            if (out / "paired-resume.json").exists():
                rows["paired-resume.json"] = json.loads((out / "paired-resume.json").read_text())
            return rows

    def test_wrong_preinitialized_price_rejects_launch_atomically(self):
        rows = self.run_case("wrong-price")
        row = rows["result.json"]
        self.assertEqual(row["receipt_status"], 0)
        self.assertEqual(row["revert"], "wrong launch price")
        self.assertEqual(row["before"], row["after"])
        self.assertEqual(row["position"]["id"], 0)

    def test_distribution_waits_for_counterpart_then_resumes_liquidity(self):
        rows = self.run_case("distributions")
        first = rows["result.json"]
        second = rows["second-result.json"]
        resumed = rows["paired-resume.json"]
        self.assertEqual(first["receipt_status"], 1)
        self.assertEqual(second["receipt_status"], 1)
        self.assertEqual(second["distributor_balance"], 0)
        self.assertEqual(second["locker_balances"][1], 0)
        self.assertEqual(second["dev_received"], 1000 * 10**18)
        self.assertGreater(resumed["after"]["liquidity"], resumed["before"]["liquidity"])


if __name__ == "__main__":
    unittest.main()
