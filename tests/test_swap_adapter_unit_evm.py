"""Fe bytecode unit tests on loopback chain31337. Etched MOCK pool/factory.

These cases exercise the real SwapAdapter and Riddle bytecode, but DO NOT
establish compatibility with the official PancakeSwap implementation.
"""
import json
import os
from pathlib import Path
import subprocess
import shutil
import socket
import time
import unittest
import urllib.request

from eth_utils import keccak

ROOT = Path(__file__).resolve().parents[1]
FE = os.environ.get("FE_BIN") or shutil.which("fe")
FACTORY = "0x0bfbCf9fa4f9c56b0f40a671ad40e0805a091865"
MAX_I256 = 2**255 - 1


def word(n):
    if isinstance(n, str):
        n = int(n, 16)
    return (n % 2**256).to_bytes(32, "big")


def calldata(signature, *args):
    return "0x" + (keccak(text=signature)[:4] + b"".join(word(n) for n in args)).hex()


def bound_pool(token, wbnb):
    t0, t1 = sorted((int(token, 16), int(wbnb, 16)))
    salt = keccak(word(t0) + word(t1) + word(2500))
    h = keccak(b"\xff" + bytes.fromhex("41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9") + salt + bytes.fromhex("6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2"))
    return "0x" + h[-20:].hex()


def reserve_loopback_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("loopback RPC redirects forbidden")


class AdapterUnitEvmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FE:
            raise RuntimeError("set FE_BIN to the pinned Fe compiler")
        # One full build is faster than five complete semantic passes, and
        # makes the harness reproducible without stale fixture bytecode.
        result = subprocess.run([FE, "build", "--emit", "bytecode,runtime-bytecode,abi"], cwd=ROOT, capture_output=True, text=True, timeout=180)
        build_output = [{"command": result.args, "exit_code": result.returncode, "output": result.stdout + result.stderr}]
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        build_path = ROOT / ".local-evidence" / (os.environ.get("EVIDENCE_RUN", "latest") + "-builds.json")
        build_path.parent.mkdir(parents=True, exist_ok=True)
        build_path.write_text(json.dumps(build_output, indent=2))
        cls.transcript = []
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        cls.port = reserve_loopback_port()
        cls.url = "http://127.0.0.1:" + str(cls.port)
        cls.node = subprocess.Popen(["anvil", "--host", "127.0.0.1", "--port", str(cls.port), "--chain-id", "31337", "--silent"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        cls.addClassCleanup(cls.shutdown)
        for _ in range(100):
            if cls.node.poll() is not None:
                raise RuntimeError("own Anvil exited; do not attach to someone else's port")
            try:
                if cls.rpc("eth_chainId", []) == "0x7a69":
                    break
            except Exception:
                pass
            time.sleep(.05)
        else:
            raise RuntimeError("own loopback Anvil not ready")
        metadata = cls.rpc("anvil_metadata", [])
        if not metadata["clientVersion"].startswith("anvil/") or metadata.get("forkedNetwork"):
            raise RuntimeError("own node must be unforked Anvil31337")
        cls.accounts = cls.rpc("eth_accounts", [])

    @classmethod
    def shutdown(cls):
        cls.node.terminate()
        try:
            cls.node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.node.kill()
            cls.node.wait()
        path = ROOT / ".local-evidence" / (os.environ.get("EVIDENCE_RUN", "latest") + "-rpc.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cls.transcript, indent=2))

    @classmethod
    def rpc(cls, method, params):
        req = urllib.request.Request(cls.url, data=json.dumps({"jsonrpc":"2.0", "id":1, "method":method, "params":params}).encode(), headers={"Content-Type":"application/json"})
        with cls.opener.open(req, timeout=20) as response:
            data = json.load(response)
        cls.transcript.append({"method": method, "params": params, "response": data})
        if "error" in data:
            raise RuntimeError(data["error"])
        return data["result"]

    def tx(self, to=None, data="0x", sender=None, success=True):
        args = {"from": sender or self.owner, "data": data, "gas": "0x989680"}
        if to:
            args["to"] = to
        if not success:
            try:
                self.rpc("eth_call", [args, "latest"])
            except RuntimeError:
                pass
        h = self.rpc("eth_sendTransaction", [args])
        for _ in range(100):
            receipt = self.rpc("eth_getTransactionReceipt", [h])
            if receipt:
                self.assertEqual(receipt["status"], "0x1" if success else "0x0", receipt)
                return receipt
            time.sleep(.02)
        self.fail("no receipt")

    def deploy(self, name, *args):
        code = (ROOT / "out" / (name + ".bin")).read_text().strip().removeprefix("0x")
        return self.tx(data="0x" + code + b"".join(word(n) for n in args).hex())["contractAddress"]

    def view(self, token, signature, *args):
        return int(self.rpc("eth_call", [{"to":token, "data":calldata(signature, *args)}, "latest"]), 16)

    def balance(self, token, owner):
        return self.view(token, "balanceOf(address)", owner)

    def setUp(self):
        self.snapshot = self.rpc("evm_snapshot", [])
        self.addCleanup(lambda: self.rpc("evm_revert", [self.snapshot]))
        self.owner, self.npm, self.dist = self.accounts[:3]
        self.wbnb = self.deploy("UnitAdapterWbnb")
        self.token = self.deploy("Riddle", self.owner, self.accounts[3], self.dist, self.wbnb, self.npm)
        self.pool = bound_pool(self.token, self.wbnb)
        for address, name in [(self.pool, "UnitAdapterPool"), (FACTORY, "UnitAdapterFactory")]:
            code = (ROOT / "out" / (name + ".runtime.bin")).read_text().strip()
            self.rpc("anvil_setCode", [address, "0x" + code.removeprefix("0x")])
        self.configure()
        self.tx(FACTORY, calldata("configure(address)", self.pool))
        self.tx(self.token, calldata("approve(address,uint256)", self.npm, 10000000))
        self.tx(self.token, calldata("transferFrom(address,address,uint256)", self.owner, self.pool, 1000000), sender=self.npm)
        self.tx(self.wbnb, calldata("mint(address,uint256)", self.pool, 1000000))
        self.tx(self.wbnb, calldata("mint(address,uint256)", self.owner, 1000000))
        self.adapter = self.deploy("SwapAdapter", self.token, self.wbnb, self.pool)
        self.tx(self.token, calldata("approve(address,uint256)", self.adapter, 1000000))
        self.tx(self.wbnb, calldata("approve(address,uint256)", self.adapter, 1000000))
        self.deadline = int(self.rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16) + 1000

    def configure(self, mode=0, fill=0):
        self.tx(self.pool, calldata("configure(address,address,uint256,uint256)", self.token, self.wbnb, mode, fill))

    def swap(self, kind, amount, bound, sender=None, success=True, deadline=None, limit=0):
        return self.tx(self.adapter, calldata(kind + "(uint256,uint256,uint256,uint160)", amount, bound, self.deadline if deadline is None else deadline, limit), sender=sender, success=success)

    def test_sell_exact_input_pays_minimal_gross_from_real_rate(self):
        before = self.balance(self.token, self.owner)
        pool_before = self.balance(self.token, self.pool)
        out_before = self.balance(self.wbnb, self.owner)
        self.swap("sellExactInput", 1053, 1001)
        self.assertEqual(before - self.balance(self.token, self.owner), 1053)
        self.assertEqual(self.balance(self.token, self.pool) - pool_before, 1001)
        self.assertEqual(self.balance(self.wbnb, self.owner) - out_before, 1001)
        self.assertEqual(self.balance(self.wbnb, self.adapter), 0)
        self.assertEqual(self.balance(self.token, self.adapter), 0)

    def test_buy_exact_input_checks_net_recipient_output(self):
        before = self.balance(self.wbnb, self.owner)
        out_before = self.balance(self.token, self.owner)
        self.swap("buyExactInput", 1000, 950)
        self.assertEqual(before - self.balance(self.wbnb, self.owner), 1000)
        self.assertEqual(self.balance(self.token, self.owner) - out_before, 950)
        self.assertEqual(self.balance(self.token, self.dist), 50)
        self.assertEqual(self.balance(self.token, self.adapter), 0)

    def test_sell_exact_output_returns_minimum_gross_not_whole_maximum(self):
        before = self.balance(self.token, self.owner)
        out_before = self.balance(self.wbnb, self.owner)
        self.swap("sellExactOutput", 1000, 1100)
        self.assertEqual(before - self.balance(self.token, self.owner), 1052)
        self.assertEqual(self.balance(self.wbnb, self.owner) - out_before, 1000)
        self.assertEqual(self.view(self.token, "allowance(address,address)", self.owner, self.adapter), 1000000 - 1052)

    def test_buy_exact_output_grosses_up_requested_net(self):
        before = self.balance(self.wbnb, self.owner)
        out_before = self.balance(self.token, self.owner)
        self.swap("buyExactOutput", 1000, 1100)
        self.assertEqual(before - self.balance(self.wbnb, self.owner), 1052)
        self.assertEqual(self.balance(self.token, self.owner) - out_before, 1000)
        self.assertEqual(self.balance(self.token, self.dist), 52)

    def test_constructor_rejects_wrong_official_pool_metadata(self):
        self.tx(self.pool, calldata("configure(address,address,uint256,uint256)", self.wbnb, self.wbnb, 0, 0))
        code = (ROOT / "out" / "SwapAdapter.bin").read_text().strip().removeprefix("0x")
        self.tx(data="0x" + code + b"".join(word(n) for n in (self.token, self.wbnb, self.pool)).hex(), success=False)

    def test_sell_distributor_payer_tracks_gross_transfer_with_fee_rebate(self):
        self.tx(self.token, calldata("transfer(address,uint256)", self.dist, 10000))
        self.tx(self.token, calldata("approve(address,uint256)", self.adapter, 10000), sender=self.dist)
        before = self.balance(self.token, self.dist)
        self.swap("sellExactOutput", 1000, 1100, sender=self.dist)
        self.assertEqual(before - self.balance(self.token, self.dist), 1000)
        self.assertEqual(self.view(self.token, "allowance(address,address)", self.dist, self.adapter), 10000 - 1052)

    def wallet_state(self):
        return [self.balance(t, a) for t in (self.token, self.wbnb) for a in (self.owner, self.pool, self.dist, self.adapter)] + [self.view(t, "allowance(address,address)", self.owner, self.adapter) for t in (self.token, self.wbnb)] + [self.view(self.token, "currentTaxBps()")]

    def assert_swap_rolls_back(self, kind, amount, bound, **kw):
        before = self.wallet_state()
        self.swap(kind, amount, bound, success=False, **kw)
        self.assertEqual(self.wallet_state(), before)

    def test_partial_sell_exact_input_preserves_unspent_wallet_budget(self):
        self.configure(fill=400)
        before = self.balance(self.token, self.owner)
        self.swap("sellExactInput", 1053, 400)
        self.assertEqual(before - self.balance(self.token, self.owner), 421)
        self.assertEqual(self.balance(self.wbnb, self.owner), 1000400)

    def test_partial_buy_exact_input_measures_net_output(self):
        self.configure(fill=400)
        self.swap("buyExactInput", 1000, 380)
        self.assertEqual(self.balance(self.wbnb, self.owner), 999600)
        self.assertEqual(self.balance(self.token, self.dist), 20)

    def test_exact_output_partial_fill_reverts_for_both_sides(self):
        self.configure(fill=400)
        self.assert_swap_rolls_back("sellExactOutput", 1000, 1100)
        self.assert_swap_rolls_back("buyExactOutput", 1000, 1100)

    def test_minimum_net_output_failure_rolls_back_for_both_sides(self):
        self.assert_swap_rolls_back("sellExactInput", 1000, 951)
        self.assert_swap_rolls_back("buyExactInput", 1000, 951)

    def test_exact_output_maximum_spend_failure_rolls_back_for_both_sides(self):
        self.assert_swap_rolls_back("sellExactOutput", 1000, 1051)
        self.assert_swap_rolls_back("buyExactOutput", 1000, 1051)

    def test_expired_zero_overflow_and_invalid_limit_inputs_revert(self):
        for kind in ("sellExactInput", "buyExactInput", "sellExactOutput", "buyExactOutput"):
            with self.subTest(kind=kind):
                self.assert_swap_rolls_back(kind, 1000, 1100, deadline=0)
                self.assert_swap_rolls_back(kind, 0, 1100)
                self.assert_swap_rolls_back(kind, MAX_I256 + 1, 1100)
                self.assert_swap_rolls_back(kind, 1000, 1100, limit=4295128739)
                self.assert_swap_rolls_back(kind, 1000, 1100, limit=1461446703485210103287273052203988822378723970342)

    def test_zero_allowance_prevents_payment_and_rolls_back(self):
        for token, kind in [(self.token, "sellExactInput"), (self.wbnb, "buyExactInput")]:
            self.tx(token, calldata("approve(address,uint256)", self.adapter, 0))
            self.assert_swap_rolls_back(kind, 1000, 1)

    def test_idle_and_forged_callbacks_never_use_allowance(self):
        payload = calldata("pancakeV3SwapCallback(int256,int256,bytes)", 1000, -1000, 96, 0)
        before = self.wallet_state()
        self.tx(self.adapter, payload, success=False)
        self.rpc("anvil_impersonateAccount", [self.pool])
        self.rpc("anvil_setBalance", [self.pool, hex(10**18)])
        self.tx(self.adapter, payload, sender=self.pool, success=False)
        self.assertEqual(self.wallet_state(), before)

    def test_adversarial_callback_modes_revert_and_roll_back(self):
        reasons = {1:"invalid swap direction", 2:"callback over budget", 3:"invalid output magnitude", 4:"invalid callback data", 5:"unauthorized callback", 6:"callback not paid", 7:"callback mismatch"}
        for mode in range(1, 8):
            with self.subTest(mode=mode):
                self.configure(mode=mode)
                self.assert_swap_rolls_back("sellExactInput", 1000, 1)
                errors = [x["response"]["error"] for x in self.transcript if "error" in x["response"]]
                self.assertIn(reasons[mode], str(errors[-1]))
                self.assert_swap_rolls_back("buyExactInput", 1000, 1)

    def test_dust_budget_leaves_rounding_residual_in_wallet(self):
        before = self.balance(self.token, self.owner)
        self.swap("sellExactInput", 20, 19)
        self.assertEqual(before - self.balance(self.token, self.owner), 19)
        self.assertEqual(self.balance(self.token, self.dist), 0)

    def test_buy_exact_output_to_distributor_is_untaxed(self):
        self.tx(self.wbnb, calldata("mint(address,uint256)", self.dist, 2000))
        self.tx(self.wbnb, calldata("approve(address,uint256)", self.adapter, 2000), sender=self.dist)
        before = self.balance(self.token, self.dist)
        self.swap("buyExactOutput", 1000, 1000, sender=self.dist)
        self.assertEqual(self.balance(self.token, self.dist) - before, 1000)
        self.assertEqual(self.balance(self.wbnb, self.dist), 1000)

    def test_rate_source_tracks_real_tax_decay_and_views_are_static(self):
        self.assertEqual(self.view(self.token, "currentTaxBps()"), 500)
        self.rpc("evm_increaseTime", [15 * 86400])
        self.rpc("evm_mine", [])
        self.assertEqual(self.view(self.token, "currentTaxBps()"), 500)
        self.deadline += 15 * 86400
        self.swap("buyExactInput", 1000, 950)
        self.rpc("evm_increaseTime", [15 * 86400])
        self.rpc("evm_mine", [])
        self.assertEqual(self.view(self.token, "currentTaxBps()"), 300)
        self.deadline += 15 * 86400
        before = self.balance(self.token, self.owner)
        self.swap("sellExactOutput", 1000, 1100)
        self.assertEqual(before - self.balance(self.token, self.owner), 1030)
        self.rpc("evm_increaseTime", [15 * 86400])
        self.rpc("evm_mine", [])
        self.assertEqual(self.view(self.token, "currentTaxBps()"), 100)
        self.deadline += 15 * 86400
        before = self.balance(self.wbnb, self.owner)
        self.swap("buyExactOutput", 1000, 1100)
        self.assertEqual(before - self.balance(self.wbnb, self.owner), 1010)

    def test_swap_return_values_match_balance_and_allowance_debits(self):
        for kind, amount, bound, expected in [("sellExactInput",20,19,(19,19)), ("buyExactInput",1000,950,(1000,950)), ("sellExactOutput",1000,1100,(1052,1000)), ("buyExactOutput",1000,1100,(1052,1000))]:
            raw = bytes.fromhex(self.rpc("eth_call", [{"from":self.owner,"to":self.adapter,"data":calldata(kind + "(uint256,uint256,uint256,uint160)",amount,bound,self.deadline,0)}, "latest"])[2:])
            self.assertEqual(len(raw),64)
            self.assertEqual((int.from_bytes(raw[:32],"big"),int.from_bytes(raw[32:],"big")),expected)

    def test_metadata_views_match_immutable_dependencies(self):
        self.assertEqual(self.view(self.adapter,"token()"),int(self.token,16))
        self.assertEqual(self.view(self.adapter,"wbnb()"),int(self.wbnb,16))
        self.assertEqual(self.view(self.adapter,"pool()"),int(self.pool,16))

    def test_historical_token_without_tax_views_fails_at_pool_selector(self):
        # This is the manifest-pinned 5e5719e source baseline, not a claim about
        # any deployed testnet runtime. Rebuild it before using the artifact.
        fixture = ROOT / "tests/fixtures/pancake_v3"
        manifest = json.loads((fixture / "artifacts/manifest.json").read_text())
        self.assertEqual(manifest["rdl_revision"], "5e5719ee389318d06493870c42670a509692a2ea")
        from hashlib import sha256
        for relative in ("sources/rdl-current/src/lib.fe", "artifacts/Riddle.bin", "artifacts/Riddle.runtime.bin"):
            self.assertEqual(sha256((fixture / relative).read_bytes()).hexdigest(), manifest["files"][relative]["sha256"])
        evidence = ROOT / ".local-evidence" / (os.environ.get("EVIDENCE_RUN", "latest") + "-legacy-witness")
        evidence.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [FE, "build", str(fixture / "sources/rdl-current/src/lib.fe"), "--standalone",
             "--contract", "Riddle", "--out-dir", str(evidence / "bytecode"), "--emit", "bytecode,runtime-bytecode,abi"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
        (evidence / "build.json").write_text(json.dumps({"command": result.args, "exit_code": result.returncode,
                                                       "output": result.stdout + result.stderr}, indent=2))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("Riddle.bin", "Riddle.runtime.bin"):
            self.assertEqual(bytes.fromhex((evidence / "bytecode" / name).read_text().strip().removeprefix("0x")),
                             bytes.fromhex((fixture / "artifacts" / name).read_text().strip().removeprefix("0x")))
        baseline = (fixture / "artifacts/Riddle.bin").read_text().strip().removeprefix("0x")
        code = baseline + b"".join(word(value) for value in
                                   (self.owner, self.accounts[3], self.dist, self.wbnb, self.npm)).hex()
        legacy_receipt = self.tx(data="0x" + code)
        legacy = legacy_receipt["contractAddress"]
        runtime = self.rpc("eth_getCode", [legacy, legacy_receipt["blockNumber"]])
        self.assertEqual(bytes.fromhex(runtime[2:]), bytes.fromhex((fixture / "artifacts/Riddle.runtime.bin").read_text().strip()))
        self.assertEqual(self.view(legacy, "totalSupply()"), 10**27)
        self.assertEqual(self.balance(legacy, self.owner), 180 * 10**24)
        pool = bound_pool(legacy, self.wbnb)
        self.assertNotEqual(pool, self.pool, "legacy control must have its own CREATE2-derived pool")
        for target, name in [(pool, "UnitAdapterPool"), (FACTORY, "UnitAdapterFactory")]:
            runtime = (ROOT / "out" / (name + ".runtime.bin")).read_text().strip().removeprefix("0x")
            self.rpc("anvil_setCode", [target, "0x" + runtime])
        self.tx(pool, calldata("configure(address,address,uint256,uint256)", legacy, self.wbnb, 0, 0))
        self.tx(FACTORY, calldata("configure(address)", pool))
        for target in (legacy, self.wbnb, pool, FACTORY):
            self.assertNotEqual(self.rpc("eth_getCode", [target, "latest"]), "0x", target)
        token0, token1 = sorted((int(legacy, 16), int(self.wbnb, 16)))
        self.assertEqual((self.view(pool, "token0()"), self.view(pool, "token1()")), (token0, token1),
                         "legacy pool metadata fixture is not the configured CREATE2 pair")
        self.assertEqual(self.view(pool, "fee()"), 2500)
        self.assertEqual(self.view(pool, "factory()"), int(FACTORY, 16))
        self.assertEqual(self.view(FACTORY, "getPool(address,address,uint24)", token0, token1, 2500), int(pool, 16))
        missing_views = ("pool()", "positionManager()", "distributor()", "currentTaxBps()")
        for signature in missing_views:
            with self.subTest(missing_view=signature), self.assertRaisesRegex(RuntimeError, "execution reverted"):
                self.rpc("eth_call", [{"to": legacy, "data": calldata(signature)}, "latest"])
            error = self.transcript[-1]["response"]["error"]
            self.assertEqual(error["code"], 3)
            self.assertEqual(error["data"], "0x")
        adapter_code = (ROOT / "out/SwapAdapter.bin").read_text().strip().removeprefix("0x")
        payload = "0x" + adapter_code + b"".join(word(value) for value in (legacy, self.wbnb, pool)).hex()
        failed = self.tx(data=payload, success=False)
        trace = self.rpc("debug_traceTransaction", [failed["transactionHash"], {"tracer": "callTracer"}])
        (evidence / "legacy-constructor.json").write_text(json.dumps({"receipt": failed, "trace": trace}, indent=2))
        self.assertEqual(trace["type"], "CREATE")
        self.assertEqual(trace["input"], payload)
        self.assertEqual(trace["error"], "execution reverted")
        calls = trace.get("calls", [])
        self.assertEqual(len(calls), 1, "constructor must fail on the first TaxViews call, not pool metadata")
        missing_pool = calls[0]
        self.assertEqual(missing_pool["type"], "STATICCALL")
        self.assertEqual(missing_pool["to"].lower(), legacy.lower())
        self.assertEqual(missing_pool["input"], calldata("pool()"))
        self.assertEqual(missing_pool["error"], "execution reverted")
        self.assertEqual(missing_pool.get("output", "0x"), "0x")
        self.assertEqual(missing_pool.get("calls", []), [])

        # Same constructor bytecode + current Riddle reaches all binding and
        # mock metadata calls successfully after restoring the factory control.
        self.tx(FACTORY, calldata("configure(address)", self.pool))
        positive_payload = "0x" + adapter_code + b"".join(word(value) for value in (self.token, self.wbnb, self.pool)).hex()
        positive = self.tx(data=positive_payload)
        positive_trace = self.rpc("debug_traceTransaction", [positive["transactionHash"], {"tracer": "callTracer"}])
        (evidence / "current-constructor.json").write_text(json.dumps({"receipt": positive, "trace": positive_trace}, indent=2))
        self.assertNotIn("error", positive_trace)
        expected_calls = [(self.token, "pool()", self.pool), (self.token, "positionManager()", self.npm),
                          (self.token, "distributor()", self.dist)]
        current0, current1 = sorted((int(self.token, 16), int(self.wbnb, 16)))
        expected_calls += [(self.pool, "token0()", current0), (self.pool, "token1()", current1),
                           (self.pool, "fee()", 2500), (self.pool, "factory()", FACTORY)]
        positive_calls = positive_trace.get("calls", [])
        self.assertEqual(len(positive_calls), len(expected_calls) + 1)
        for call, (target, signature, expected) in zip(positive_calls, expected_calls):
            self.assertEqual(call["type"], "STATICCALL")
            self.assertEqual(call["to"].lower(), target.lower())
            self.assertEqual(call["input"], calldata(signature))
            self.assertEqual(call["output"], "0x" + word(expected).hex())
            self.assertNotIn("error", call)
        registry = positive_calls[-1]
        self.assertEqual(registry["type"], "STATICCALL")
        self.assertEqual(registry["to"].lower(), FACTORY.lower())
        self.assertEqual(registry["input"], calldata("getPool(address,address,uint24)", current0, current1, 2500))
        self.assertEqual(registry["output"], "0x" + word(self.pool).hex())
        self.assertNotIn("error", registry)
        for signature, expected in (("token()", self.token), ("wbnb()", self.wbnb), ("pool()", self.pool)):
            self.assertEqual(self.view(positive["contractAddress"], signature), int(expected, 16))

    def test_payment_token_reentry_is_blocked_with_outer_swap_successful(self):
        self.tx(self.wbnb,calldata("reentry(address)",self.adapter))
        self.swap("buyExactInput",1000,950)
        self.assertEqual(self.view(self.wbnb,"reentryAttempts()"),1)
        self.assertEqual(self.view(self.wbnb,"reentrySucceeded()"),0)
        self.swap("buyExactInput",1000,950)
        self.assertEqual(self.view(self.wbnb,"reentryAttempts()"),2)
        self.assertEqual(self.view(self.wbnb,"reentrySucceeded()"),0)


if __name__ == "__main__":
    unittest.main()
