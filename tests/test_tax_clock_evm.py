"""Tax-clock regressions against built Fe bytecode on a local Anvil node.

Run with LOCAL_EVM_RPC=http://127.0.0.1:18556 and optional FE_BIN=/path/to/fe.
No signing keys, public-chain transactions, or balances patched inside RDL.
Account impersonation is fixture setup only, after the permissionless action.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from urllib.parse import urlsplit
import urllib.request

from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
IEO = "0x00000000000000000000000000000000000001e0"
MINING = "0x0000000000000000000000000000000000000313"
DIST = "0x0000000000000000000000000000000000000d15"
WBNB = "0x0000000000000000000000000000000000000b0b"
NPM = "0x00000000000000000000000000000000000009a1"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("local EVM RPC redirects are not allowed")


def word(value):
    number = int(value, 16) if isinstance(value, str) else value
    return number.to_bytes(32, "big")


def calldata(signature, *args):
    return "0x" + (keccak(text=signature)[:4] + b"".join(map(word, args))).hex()


def pool_address(token):
    token0, token1 = sorted([int(token, 16), int(WBNB, 16)])
    salt = keccak(word(token0) + word(token1) + word(2500))
    digest = keccak(
        b"\xff" + bytes.fromhex("41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9")
        + salt
        + bytes.fromhex("6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2")
    )
    return "0x" + digest[-20:].hex()


@unittest.skipUnless(os.environ.get("LOCAL_EVM_RPC"), "set LOCAL_EVM_RPC to run local EVM tests")
class TaxClockEvmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["LOCAL_EVM_RPC"]
        url = urlsplit(cls.url)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.query or url.fragment):
            raise RuntimeError("LOCAL_EVM_RPC must be a plain loopback HTTP URL")
        cls.opener = urllib.request.build_opener(NoRedirect())
        compiler = os.environ.get("FE_BIN") or shutil.which("fe")
        if not compiler:
            raise RuntimeError("install Fe or set FE_BIN")
        build = subprocess.run([compiler, "build"], cwd=ROOT, capture_output=True, text=True, timeout=120)
        if build.returncode != 0:
            raise RuntimeError(build.stdout + build.stderr)
        cls.bytecode = bytes.fromhex((ROOT / "out" / "Riddle.bin").read_text().strip().removeprefix("0x"))

    @classmethod
    def rpc(cls, method, params):
        request = urllib.request.Request(
            cls.url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with cls.opener.open(request, timeout=30) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def setUp(self):
        self.assertEqual(int(self.rpc("eth_chainId", []), 16), 97, "expected local chain 97")
        snapshot = self.rpc("evm_snapshot", [])
        self.addCleanup(lambda: self.assertTrue(self.rpc("evm_revert", [snapshot])))
        self.owner = self.rpc("eth_accounts", [])[0]
        args = b"".join(map(word, [IEO, MINING, DIST, WBNB, NPM]))
        self.token = self.transact(self.owner, payload="0x" + (self.bytecode + args).hex())["contractAddress"]
        self.pool = pool_address(self.token)
        self.assertEqual(self.balance(self.owner), 0)
        self.assertEqual(self.balance(self.pool), 0)
        self.assertEqual(self.rpc("eth_getCode", [self.pool, "latest"]), "0x")

    def transact(self, sender, target=None, payload="0x"):
        tx = {"from": sender, "data": payload, "gas": "0x989680"}
        if target:
            tx["to"] = target
        txhash = self.rpc("eth_sendTransaction", [tx])
        for _ in range(100):
            receipt = self.rpc("eth_getTransactionReceipt", [txhash])
            if receipt is not None:
                self.assertEqual(receipt["status"], "0x1", receipt)
                return receipt
            time.sleep(0.05)
        self.fail("local transaction was not mined")

    def view(self, signature, *args):
        return int(self.rpc("eth_call", [{"to": self.token, "data": calldata(signature, *args)}, "latest"]), 16)

    def balance(self, who):
        return self.view("balanceOf(address)", who)

    def impersonate(self, address):
        self.rpc("anvil_impersonateAccount", [address])
        self.rpc("anvil_setBalance", [address, hex(10**18)])

    def liquidity_fixture(self):
        self.impersonate(IEO)
        self.transact(IEO, self.token, calldata("approve(address,uint256)", NPM, 10000))
        self.impersonate(NPM)
        self.transact(NPM, self.token, calldata("transferFrom(address,address,uint256)", IEO, self.pool, 10000))

    def advance_15_days(self):
        self.rpc("evm_increaseTime", [15 * 86400])
        self.rpc("evm_mine", [])

    def first_buy(self):
        before = self.balance(DIST)
        self.impersonate(self.pool)
        self.transact(self.pool, self.token, calldata("transfer(address,uint256)", self.owner, 1000))
        return self.balance(DIST) - before

    def test_zero_transfer_to_uncreated_pool_does_not_age_tax(self):
        self.transact(self.owner, self.token, calldata("transfer(address,uint256)", self.pool, 0))
        self.advance_15_days()
        self.liquidity_fixture()
        self.assertEqual(self.balance(DIST), 0)
        self.assertEqual(self.first_buy(), 50)
        self.assertEqual(self.balance(self.owner), 950)

    def test_zero_transfer_from_needs_no_allowance_but_does_not_age_tax(self):
        self.assertEqual(self.view("allowance(address,address)", self.pool, self.owner), 0)
        self.transact(self.owner, self.token, calldata("transferFrom(address,address,uint256)", self.pool, self.owner, 0))
        self.advance_15_days()
        self.liquidity_fixture()
        self.assertEqual(self.first_buy(), 50)

    def test_untaxed_liquidity_transfer_does_not_age_tax(self):
        self.liquidity_fixture()
        self.assertEqual(self.balance(DIST), 0)
        self.advance_15_days()
        self.assertEqual(self.first_buy(), 50)

    def test_tax_quote_is_inert_until_first_positive_trade(self):
        self.assertEqual(self.view("currentTaxBps()"), 500)
        self.assertEqual(self.view("currentTaxBps()"), 500)
        self.advance_15_days()
        self.assertEqual(self.view("currentTaxBps()"), 500)
        self.liquidity_fixture()
        self.assertEqual(self.first_buy(), 50)

    def test_tax_quote_matches_decay_and_floor(self):
        self.liquidity_fixture()
        self.assertEqual(self.first_buy(), 50)
        self.advance_15_days()
        self.assertEqual(self.view("currentTaxBps()"), 300)
        self.assertEqual(self.first_buy(), 30)
        self.advance_15_days()
        self.assertEqual(self.view("currentTaxBps()"), 100)
        self.assertEqual(self.first_buy(), 10)

    def test_tax_metadata_matches_constructor_bindings(self):
        self.assertEqual(self.view("pool()"), int(self.pool, 16))
        self.assertEqual(self.view("positionManager()"), int(NPM, 16))
        self.assertEqual(self.view("distributor()"), int(DIST, 16))

    def test_real_positive_transfer_still_starts_tax_decay(self):
        self.liquidity_fixture()
        self.impersonate(IEO)
        self.transact(IEO, self.token, calldata("transfer(address,uint256)", self.pool, 1000))
        self.assertEqual(self.balance(DIST), 50)
        self.advance_15_days()
        self.assertEqual(self.first_buy(), 30)


if __name__ == "__main__":
    unittest.main()
