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
            send=lambda target, payload: sent.append((target, payload)) or {"contractAddress": adapter, "transactionHash": "0xfixture", "blockNumber": "0x101"},
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

    def install_post_deploy_fixture(self):
        """Fake only the chain boundary; execute the real deployment script.

        The head advances after send and deliberately differs from both the
        preflight and receipt blocks. Each block has independently mutable data.
        The placeholder passed to Signer is deliberately not a private key.
        """
        self.adapter_address = "0x000000000000000000000000000000000000ada1"
        self.receipt_block = "0x101"
        self.moving_head = "0x202"
        self.receipt = {"contractAddress": self.adapter_address, "transactionHash": "0x" + "ab" * 32,
                        "blockNumber": self.receipt_block, "status": "0x1"}
        self.sent = []
        self.post_deploy_reads = []
        valid = {"token()": TOKEN, "wbnb()": WBNB, "pool()": POOL}
        self.post_deploy_states = {
            block: {"code": "0x6000", "views": dict(valid)}
            for block in (self.receipt_block, self.moving_head, "latest")
        }

        def send(target, payload):
            self.sent.append((target, payload))
            return dict(self.receipt)

        def signer(fixture):
            self.assertEqual(fixture, "deliberate non-key fixture")
            self.signer_calls += 1
            return types.SimpleNamespace(address="0x000000000000000000000000000000000000f00d", send=send)

        def rpc(method, params):
            if not self.sent:
                return self.rpc(method, params)
            self.rpc_calls.append((method, params))
            if method == "eth_blockNumber":
                return self.moving_head
            self.post_deploy_reads.append((method, params))
            if method not in ("eth_getCode", "eth_call"):
                raise AssertionError(f"unexpected post-deploy RPC {method}")
            target = params[0] if method == "eth_getCode" else params[0]["to"]
            self.assertEqual(target.lower(), self.adapter_address, "post-deploy target tripwire")
            state = self.post_deploy_states.get(params[1], {"code": "0x", "views": {}})
            if method == "eth_getCode":
                return state["code"]
            selector = params[0]["data"]
            for signature, value in state["views"].items():
                if selector == "0x" + keccak(text=signature)[:4].hex():
                    return "0x" + int(value, 16).to_bytes(32, "big").hex()
            raise AssertionError(f"unexpected post-deploy view {params}")

        setattr(self.chain, "Signer", signer)
        setattr(self.chain, "rpc", rpc)

    def test_post_deploy_code_and_all_bindings_use_receipt_block(self):
        self.install_post_deploy_fixture()
        self.execute("__main__", "--deploy")
        self.assertEqual(self.signer_calls, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.output.is_file())
        expected = [("eth_getCode", [self.adapter_address, self.receipt_block])]
        expected += [("eth_call", [{"to": self.adapter_address, "data": "0x" + keccak(text=signature)[:4].hex()}, self.receipt_block])
                     for signature in ("token()", "wbnb()", "pool()")]
        actual = []
        for method, params in self.post_deploy_reads:
            target = params[0] if method == "eth_getCode" else params[0]["to"]
            normalized = [target.lower(), params[1]] if method == "eth_getCode" else [{**params[0], "to": target.lower()}, params[1]]
            actual.append((method, normalized))
        self.assertEqual(actual, expected, "receipt-block tripwire: never verify against the moving head or preflight block")
        import json
        out = json.loads(self.output.read_text())
        self.assertEqual(out["adapter"].lower(), self.adapter_address)
        self.assertEqual(out["preflightBlock"], "0x100")

    def test_receipt_binding_stays_valid_when_moving_head_is_invalid(self):
        self.install_post_deploy_fixture()
        for block in (self.moving_head, "latest"):
            self.post_deploy_states[block] = {"code": "0x", "views": {}}
        self.execute("__main__", "--deploy")
        self.assertTrue(self.output.is_file(), "receipt-block validity must not depend on a later head")
        self.assertEqual({params[1] for _, params in self.post_deploy_reads}, {self.receipt_block})

    def test_missing_adapter_code_at_receipt_block_is_not_success(self):
        self.install_post_deploy_fixture()
        self.post_deploy_states[self.receipt_block]["code"] = "0x"
        with self.assertRaisesRegex(RuntimeError, "missing contract code"):
            self.execute("__main__", "--deploy")
        self.assertFalse(self.output.exists(), "missing receipt-block code must not write a success file")
        self.assertEqual(len(self.post_deploy_reads), 1)
        method, params = self.post_deploy_reads[0]
        self.assertEqual((method, params[0].lower(), params[1]), ("eth_getCode", self.adapter_address, self.receipt_block))

    def test_receipt_block_binding_mismatch_is_not_success(self):
        for signature, key in (("token()", "token"), ("wbnb()", "wbnb"), ("pool()", "pool")):
            with self.subTest(binding=key):
                self.install_post_deploy_fixture()
                self.post_deploy_states[self.receipt_block]["views"][signature] = "0x0000000000000000000000000000000000000bad"
                try:
                    self.execute("__main__", "--deploy")
                except RuntimeError as exc:
                    self.assertIn("adapter binding mismatch: " + key, str(exc).lower())
                else:
                    self.fail("receipt-block binding mismatch was reported as success")
                self.assertFalse(self.output.exists(), "no success file may survive an invalid binding")
                self.assertEqual({params[1] for _, params in self.post_deploy_reads}, {self.receipt_block})
                self.assertNotIn(self.moving_head, {params[1] for _, params in self.post_deploy_reads})

    def test_missing_receipt_block_number_is_not_success(self):
        self.install_post_deploy_fixture()
        del self.receipt["blockNumber"]
        try:
            self.execute("__main__", "--deploy")
        except RuntimeError as exc:
            self.assertIn("no block number", str(exc).lower())
        else:
            self.fail("receipt without blockNumber was accepted")
        self.assertFalse(self.output.exists(), "unverifiable deployment must not write a success file")
        self.assertEqual(self.post_deploy_reads, [], "unpinned head must never be used as a fallback")

    def test_token_equal_to_wbnb_rejected(self):
        self.env["TOKEN"] = WBNB
        self.views[WBNB, "pool()"] = self.views[TOKEN, "pool()"]
        self.rejected("distinct")

    def test_wrong_deployed_adapter_binding_is_not_reported_as_success(self):
        adapter = "0x000000000000000000000000000000000000ada1"
        fake = types.SimpleNamespace(
            address="0x000000000000000000000000000000000000f00d",
            send=lambda target, payload: {"contractAddress": adapter, "transactionHash": "0xfixture", "blockNumber": "0x101"},
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
