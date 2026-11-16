"""Deployment preflight tests: fake RPC/signing only, never real keys or funds."""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_testnet.py"
WBNB = "0xae13d989dac2f0debff460ac112a837c89baa7cd"
NPM = "0x427bf5b37357632377ecbec9de3626c71a5396c1"
FACTORY = "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
DEPLOYER = "0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9"


class SignerReached(RuntimeError):
    pass


class DeploymentPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="riddlemine-preflight-")
        self.addCleanup(self.directory.cleanup)
        key_file = Path(self.directory.name) / "placeholder.txt"
        # A deliberate non-key fixture. A fake signer intercepts every use.
        key_file.write_text("non-secret test fixture", encoding="utf-8")
        self.env = {
            "DEPLOYER_KEY_FILE": str(key_file),
            "BOT": "0x0000000000000000000000000000000000000123",
            "DEPLOYMENTS_FILE": str(Path(self.directory.name) / "deployment.local.json"),
            "IEO_SECS": "1800",
        }
        self.chain_id = "0x61"
        self.code = {x: "0x60006000" for x in (WBNB, NPM, FACTORY, DEPLOYER)}
        self.views = {
            (NPM, "WETH9()"): WBNB,
            (NPM, "factory()"): FACTORY,
            (FACTORY, "poolDeployer()"): DEPLOYER,
            (FACTORY, "feeAmountTickSpacing(uint24)"): 50,
        }
        self.signer_calls = 0
        self.rpc_calls = []
        self.chain = types.ModuleType("chain")
        setattr(self.chain, "Signer", self.signer)
        setattr(self.chain, "rpc", self.rpc)

    def signer(self, _fixture):
        self.signer_calls += 1
        raise SignerReached("signer boundary reached")

    def rpc(self, method, params):
        self.rpc_calls.append((method, params))
        if method == "eth_chainId":
            return self.chain_id
        if method == "eth_blockNumber":
            return "0x100"
        if method == "eth_getCode":
            return self.code.get(params[0].lower(), "0x")
        if method == "eth_call":
            target = params[0]["to"].lower()
            selector = params[0]["data"][2:10]
            for (address, signature), value in self.views.items():
                if target == address and selector == keccak(text=signature)[:4].hex():
                    number = int(value, 16) if isinstance(value, str) else value
                    return "0x" + number.to_bytes(32, "big").hex()
        raise AssertionError(f"unexpected RPC: {method}")

    def execute(self, run_name="__main__"):
        with patch.dict(os.environ, self.env), patch.dict(sys.modules, {"chain": self.chain}):
            return runpy.run_path(str(SCRIPT), run_name=run_name)

    def assert_rejected_before_signer(self, fragment):
        try:
            self.execute()
        except SignerReached:
            self.fail("invalid deployment configuration reached the signer")
        except (ValueError, RuntimeError) as exc:
            self.assertIn(fragment, str(exc).lower())
        else:
            self.fail("invalid deployment configuration was not rejected")
        self.assertEqual(self.signer_calls, 0)

    def test_wrong_chain_rejected_before_signer(self):
        self.chain_id = "0x38"
        self.assert_rejected_before_signer("chain")

    def test_missing_position_manager_rejected(self):
        self.code[NPM] = "0x"
        self.assert_rejected_before_signer("code")

    def test_missing_wrapped_bnb_rejected(self):
        self.code[WBNB] = "0x"
        self.assert_rejected_before_signer("code")

    def test_wrong_wrapped_native_identity_rejected(self):
        self.views[(NPM, "WETH9()")] = "0x999"
        self.assert_rejected_before_signer("wbnb")

    def test_wrong_factory_identity_rejected(self):
        self.views[(NPM, "factory()")] = "0x999"
        self.assert_rejected_before_signer("factory")

    def test_wrong_pool_deployer_identity_rejected(self):
        self.views[(FACTORY, "poolDeployer()")] = "0x999"
        self.assert_rejected_before_signer("deployer")

    def test_wrong_tick_spacing_rejected(self):
        self.views[(FACTORY, "feeAmountTickSpacing(uint24)")] = 60
        self.assert_rejected_before_signer("spacing")

    def test_zero_bot_rejected(self):
        self.env["BOT"] = "0x" + "0" * 40
        self.assert_rejected_before_signer("bot")

    def test_zero_sale_duration_rejected(self):
        self.env["IEO_SECS"] = "0"
        self.assert_rejected_before_signer("ieo_secs")

    def test_valid_preflight_can_reach_signer(self):
        with self.assertRaises(SignerReached):
            self.execute()
        self.assertEqual(self.signer_calls, 1)
        self.assertTrue(any(method == "eth_chainId" for method, _ in self.rpc_calls))

    def test_import_does_not_deploy_or_read_keys(self):
        try:
            self.execute("import_only")
        except SignerReached:
            self.fail("importing the deployment script invoked a signer")
        self.assertEqual(self.signer_calls, 0)
        self.assertEqual(self.rpc_calls, [])


if __name__ == "__main__":
    unittest.main()
