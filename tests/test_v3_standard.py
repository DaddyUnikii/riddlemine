"""Fresh official-router controls on disposable Anvil31337, never public RPC.

Set RUN_LOCAL_V3=1 to execute. Skips are not V3 validation success.
"""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures/pancake_v3/run_local.py"


@unittest.skipUnless(os.environ.get("RUN_LOCAL_V3") == "1", "set RUN_LOCAL_V3=1 for genuine local V3 execution")
class StandardV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("standard_official_v3_fixture", FIXTURE)
        cls.v3 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.v3)
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        directory = cls.stack.enter_context(tempfile.TemporaryDirectory(prefix="rdl-standard-v3-"))
        cls.evidence = Path(directory)
        cls.fixture = cls.stack.enter_context(cls.v3.run_fixture(ROOT, out=cls.evidence, fe_bin=os.environ.get("FE_BIN")))

    def setUp(self):
        self.snapshot = self.fixture.rpc.request("evm_snapshot", [])
        self.addCleanup(self.restore_snapshot)

    def restore_snapshot(self):
        self.assertTrue(self.fixture.rpc.request("evm_revert", [self.snapshot]))

    def test_official_npm_mint_is_untaxed_and_pool_created_by_factory(self):
        f = self.fixture
        self.assertGreater(f.uint(f.pool, "liquidity()"), 0)
        self.assertEqual(f.balance(f.token, f.dist), 0)
        self.assertEqual(f.address(self.v3.FACTORY, "getPool(address,address,uint24)", ["address", "address", "uint24"], [f.token, self.v3.WBNB, 2500]), f.pool)
        provenance = json.loads((self.evidence / "provenance.json").read_text())
        self.assertFalse(provenance["pool_relocated"])
        self.assertFalse(provenance["fork_used"])
        self.assertFalse(provenance["token_storage_cheats"])
        self.assertEqual(provenance["candidate_source"]["build_exit_code"], 0)

    def test_standard_sell_exact_input_reverts_iia_and_rolls_back(self):
        result = self.fixture.standard_sell_iia()
        self.assertEqual(result["callback_debt_rdl"], 1000 * 10**18)
        self.assertEqual(result["net_credit_rdl"], 950 * 10**18)
        self.assertTrue(result["state_rolled_back"])

    def test_standard_sell_exact_output_reverts_iia_and_rolls_back(self):
        result = self.fixture.standard_sell_iia(exact_output=True)
        self.assertEqual(result["revert"], "IIA")
        self.assertGreater(result["tax_shortfall_rdl"], 0)
        self.assertTrue(result["state_rolled_back"])

    def test_standard_buy_exact_input_minimum_is_gross_not_net(self):
        result = self.fixture.standard_buy()
        self.assertLess(result["buyer_rdl_delta"], result["minimum_or_requested_rdl"])
        self.assertEqual(result["buyer_rdl_delta"] + result["distributor_tax"], result["gross_rdl_output"])

    def test_standard_buy_exact_output_request_is_gross_not_net(self):
        result = self.fixture.standard_buy(exact_output=True)
        self.assertEqual(result["buyer_rdl_delta"], 950 * 10**18)
        self.assertEqual(result["gross_rdl_output"], 1000 * 10**18)


if __name__ == "__main__":
    unittest.main()
