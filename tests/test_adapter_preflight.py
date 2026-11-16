"""Adapter deployment preflight tests: fake RPC/signer only, no real funds."""
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
SCRIPT = ROOT / "scripts" / "deploy_swap_adapter.py"
TOKEN = "0x0000000000000000000000000000000000000123"
WBNB = "0xae13d989dac2f0debff460ac112a837c89baa7cd"
POOL_DEPLOYER = "0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9"
POOL_HASH = bytes.fromhex("6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2")
PAIR = sorted((int(TOKEN, 16), int(WBNB, 16)))
POOL_SALT = keccak(b"".join(value.to_bytes(32, "big") for value in (*PAIR, 2500)))
POOL = "0x" + keccak(b"\xff" + bytes.fromhex(POOL_DEPLOYER[2:]) + POOL_SALT + POOL_HASH)[12:].hex()
NPM = "0x427bf5b37357632377ecbec9de3626c71a5396c1"
FACTORY = "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
DIST = "0x0000000000000000000000000000000000000d15"


class SignerBoundary(RuntimeError):
    pass


class AdapterPreflightTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rdl-adapter-preflight-")
        self.addCleanup(self.directory.cleanup)
        self.key = Path(self.directory.name) / "placeholder.txt"
        self.key.write_text("deliberate non-key fixture", encoding="utf-8")
        self.binary = Path(self.directory.name) / "SwapAdapter.bin"
        self.binary.write_text("60006000", encoding="utf-8")
        self.output = Path(self.directory.name) / "adapter.json"
        self.env = {
            "TOKEN": TOKEN, "DEPLOYER_KEY_FILE": str(self.key),
            "SWAP_ADAPTER_BIN": str(self.binary), "ADAPTER_DEPLOYMENTS_FILE": str(self.output),
        }
        self.chain_id = "0x61"
        self.rate = 500
        self.signer_calls = 0
        self.rpc_calls = []
        self.code = {address: "0x60006000" for address in (TOKEN, WBNB, POOL, NPM, FACTORY)}
        self.views = {
            (TOKEN, "pool()"): POOL,
            (TOKEN, "positionManager()"): NPM,
            (TOKEN, "distributor()"): DIST,
            (TOKEN, "currentTaxBps()"): 500,
            (POOL, "factory()"): FACTORY,
            (POOL, "fee()"): 2500,
            (POOL, "token0()"): TOKEN,
            (POOL, "token1()"): WBNB,
            (FACTORY, "getPool(address,address,uint24)"): POOL,
        }
        self.chain = types.ModuleType("chain")
        setattr(self.chain, "Signer", self.signer)
        setattr(self.chain, "rpc", self.rpc)

    def signer(self, _fixture):
        self.signer_calls += 1
        raise SignerBoundary("fake signer boundary reached")

    def rpc(self, method, params):
        self.rpc_calls.append((method, params))
        if method == "eth_chainId":
            return self.chain_id
        if method == "eth_blockNumber":
            return "0x100"
        if method == "eth_getCode":
            return self.code.get(params[0].lower(), "0x")
        if method == "eth_call":
            to = params[0]["to"].lower()
            selector = params[0]["data"][2:10]
            for (address, signature), value in self.views.items():
                if to == address and selector == keccak(text=signature)[:4].hex():
                    number = int(value, 16) if isinstance(value, str) else value
                    return "0x" + number.to_bytes(32, "big").hex()
        raise AssertionError(f"unexpected fake RPC {method}")

    def execute(self, run_name="__main__", *args):
        with patch.dict(os.environ, self.env), patch.dict(sys.modules, {"chain": self.chain}), patch.object(sys, "argv", [str(SCRIPT), *args]):
            return runpy.run_path(str(SCRIPT), run_name=run_name)

    def rejected(self, expected):
        try:
            self.execute("__main__", "--deploy")
        except SignerBoundary:
            self.fail("invalid adapter configuration reached signer")
        except (ValueError, RuntimeError) as exc:
            self.assertIn(expected, str(exc).lower())
        else:
            self.fail("invalid configuration was accepted")
        self.assertEqual(self.signer_calls, 0)

    def test_wrong_chain_rejected_before_key_read(self):
        self.chain_id = "0x38"
        self.rejected("chain")

    def test_missing_token_code_rejected_before_key_read(self):
        self.code[TOKEN] = "0x"
        self.rejected("code")

    def test_wrong_pool_binding_rejected_before_key_read(self):
        self.views[TOKEN, "pool()"] = "0x0000000000000000000000000000000000000bad"
        self.rejected("pool")

    def test_missing_pool_code_rejected_before_key_read(self):
        self.code[POOL] = "0x"
        self.rejected("code")

    def test_wrong_pool_identity_rejected_before_key_read(self):
        self.views[POOL, "factory()"] = "0x0000000000000000000000000000000000000bad"
        self.rejected("factory")

    def test_wrong_pool_fee_rejected_before_key_read(self):
        self.views[POOL, "fee()"] = 500
        self.rejected("fee")

    def test_wrong_pair_rejected_before_key_read(self):
        self.views[POOL, "token1()"] = DIST
        self.rejected("tokens")

    def test_wrong_factory_registry_rejected_before_key_read(self):
        self.views[FACTORY, "getPool(address,address,uint24)"] = DIST
        self.rejected("registry")

    def test_wrong_token_position_manager_rejected_before_key_read(self):
        self.views[TOKEN, "positionManager()"] = DIST
        self.rejected("position manager")

    def test_out_of_range_tax_quote_rejected_before_key_read(self):
        self.views[TOKEN, "currentTaxBps()"] = 10000
        self.rejected("tax quote")

    def test_pool_as_distributor_rejected_before_key_read(self):
        self.views[TOKEN, "distributor()"] = POOL
        self.rejected("distributor")

    def test_read_only_default_does_not_reach_signer(self):
        try:
            self.execute()
        except SignerBoundary:
            self.fail("read-only preflight reached signer")
        self.assertEqual(self.signer_calls, 0)

    def test_valid_deploy_preflight_reaches_fake_signer(self):
        with self.assertRaises(SignerBoundary):
            self.execute("__main__", "--deploy")
        self.assertEqual(self.signer_calls, 1)

    def test_import_never_reads_key_or_calls_rpc(self):
        self.execute("adapter_import_only")
        self.assertEqual(self.rpc_calls, [])
        self.assertEqual(self.signer_calls, 0)

    def test_preflight_reads_a_single_pinned_block(self):
        self.execute()
        for method, params in self.rpc_calls:
            if method in ("eth_getCode", "eth_call"):
                self.assertEqual(params[1], "0x100")

    def test_missing_artifact_rejected_before_key_read(self):
        self.binary.unlink()
        try:
            self.execute("__main__", "--deploy")
        except SignerBoundary:
            self.fail("missing artifact reached signer")
        except (ValueError, RuntimeError) as exc:
            self.assertIn("artifact", str(exc).lower())
        else:
            self.fail("missing artifact accepted")
        self.assertEqual(self.signer_calls, 0)

    def test_deploy_broadcasts_validated_constructor_bytes_to_fake_signer(self):
        sent = []
        adapter = "0x000000000000000000000000000000000000ada1"
        fake = types.SimpleNamespace(
            address="0x000000000000000000000000000000000000f00d",
            send=lambda target, payload: sent.append((target, payload)) or {"contractAddress": adapter, "transactionHash": "0xfixture"},
        )
        setattr(self.chain, "Signer", lambda _fixture: fake)
        self.code[adapter] = "0x6000"
        self.views[adapter, "token()"] = TOKEN
        self.views[adapter, "wbnb()"] = WBNB
        self.views[adapter, "pool()"] = POOL
        self.execute("__main__", "--deploy")
        self.assertEqual(len(sent), 1, "adapter was not sent to fake signer")
        target, payload = sent[0]
        self.assertIsNone(target)
        expected = bytes.fromhex("60006000") + b"".join(int(value, 16).to_bytes(32, "big") for value in (TOKEN, WBNB, POOL))
        self.assertEqual(payload, expected)
        self.assertTrue(self.output.is_file())
        import json
        self.assertEqual(json.loads(self.output.read_text())["adapter"].lower(), adapter)

    def test_token_equal_to_wbnb_rejected(self):
        self.env["TOKEN"] = WBNB
        self.views[WBNB, "pool()"] = self.views[TOKEN, "pool()"]
        self.rejected("distinct")

    def test_wrong_deployed_adapter_binding_is_not_reported_as_success(self):
        adapter = "0x000000000000000000000000000000000000ada1"
        fake = types.SimpleNamespace(
            address="0x000000000000000000000000000000000000f00d",
            send=lambda target, payload: {"contractAddress": adapter, "transactionHash": "0xfixture"},
        )
        setattr(self.chain, "Signer", lambda _fixture: fake)
        self.code[adapter] = "0x6000"
        self.views[adapter, "token()"] = DIST
        self.views[adapter, "wbnb()"] = WBNB
        self.views[adapter, "pool()"] = POOL
        try:
            self.execute("__main__", "--deploy")
        except RuntimeError as exc:
            self.assertIn("adapter binding", str(exc).lower())
        else:
            self.fail("wrong adapter binding reported success")
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
