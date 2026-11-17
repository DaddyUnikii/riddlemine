"""Net-aware adapter against genuine local Pancake V3, built from fresh source.

RUN_LOCAL_V3=1 enables actual loopback EVM execution; a skip is not validation.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/pancake_v3"
RUNNER = ROOT / "tests/run_swap_adapter_official.py"


@unittest.skipUnless(os.environ.get("RUN_LOCAL_V3") == "1", "set RUN_LOCAL_V3=1 for genuine local V3 execution")
class AdapterV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="rdl-adapter-v3-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.evidence = Path(cls.directory.name) / "evidence"
        # No inherited out/ artifact may be used by this integration.
        candidate = Path(cls.directory.name) / "candidate"
        shutil.copytree(ROOT / "src", candidate / "src")
        shutil.copy2(ROOT / "fe.toml", candidate / "fe.toml")
        compiler = os.environ.get("FE_BIN") or shutil.which("fe")
        if not compiler:
            raise RuntimeError("set FE_BIN to the pinned compiler")
        command = [sys.executable, str(RUNNER), "--fixture", str(FIXTURE), "--root", str(candidate), "--out", str(cls.evidence), "--fe", compiler]
        result = subprocess.run(command, capture_output=True, text=True, timeout=240)
        cls.runner_exit = result.returncode
        cls.runner_output = result.stdout + result.stderr
        cls.source_sha256 = hashlib.sha256((candidate / "src/swap_adapter.fe").read_bytes()).hexdigest()
        if result.returncode:
            return
        cls.summary = json.loads((cls.evidence / "adapter-summary.json").read_text())
        cls.rows = {row["case"]: row for row in cls.summary["results"]}

    def assert_runner_success(self):
        self.assertEqual(self.runner_exit, 0, self.runner_output)

    def test_official_full_buy_and_sell_routes_pay_net_and_keep_tax(self):
        self.assert_runner_success()
        for method in ("sellExactInput", "sellExactOutput", "buyExactInput", "buyExactOutput"):
            with self.subTest(method=method):
                row = self.rows[method]
                self.assertEqual(row["status"], "PASS")
                self.assertEqual(row["rate"], 500)
                self.assertGreater(row["callback_debt"], 0)
                self.assertGreater(row["received"], 0)

    def test_price_limited_inputs_are_partial_and_outputs_fail_atomically(self):
        self.assert_runner_success()
        for method in ("sellExactInput", "buyExactInput"):
            self.assertEqual(self.rows[method + "Partial"]["status"], "PASS")
        for method in ("sellExactOutput", "buyExactOutput"):
            self.assertEqual(self.rows[method + "PartialReject"]["status"], "PASS_REVERT_ROLLBACK")

    def test_deadline_net_minimum_allowance_and_price_controls_roll_back(self):
        self.assert_runner_success()
        for name in ("expiredDeadline", "buyNetMinimum", "sellZeroAllowance", "buyZeroAllowance", "invalidPriceLimit"):
            self.assertEqual(self.rows[name]["status"], "PASS_REVERT_ROLLBACK")
        self.assertEqual(len(self.rows), 21)
        self.assertEqual(self.summary["adapter_cases"], 21)

    def test_official_routes_use_decayed_300_and_100_bps_rates(self):
        self.assert_runner_success()
        for rate in (300, 100):
            for method in ("sellExactInput", "sellExactOutput", "buyExactInput", "buyExactOutput"):
                with self.subTest(rate=rate, method=method):
                    name = method + str(rate) + "bps"
                    self.assertIn(name, self.rows, "missing genuine-pool decay control")
                    self.assertEqual(self.rows[name]["rate"], rate)
                    self.assertEqual(self.rows[name]["status"], "PASS")

    def test_current_source_artifact_and_owned_node_are_verified(self):
        self.assert_runner_success()
        source = json.loads((self.evidence / "adapter-source.json").read_text())
        self.assertEqual(source["source_sha256"], self.source_sha256)
        cleanup = json.loads((self.evidence / "cleanup.json").read_text())
        self.assertTrue(cleanup["owned_anvil_stopped"])
        self.assertFalse(self.summary["fork_used"])
        self.assertFalse(self.summary["public_rpc_used"])
        self.assertTrue(self.summary["pool_created_by_genuine_create2"])


if __name__ == "__main__":
    unittest.main()
