#!/usr/bin/env python3
"""Exercise real locally deployed V3 dependencies with read-only candidate source.

No fork RPC, signing keys, parent edits, or fabricated responses. Dependency
runtime/constructor-storage relocation is declared fixture setup. The pool,
RDL, IEO, mining, locker, and distributor use genuine contract transactions.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address
import rlp

INPUTS = Path(__file__).resolve().parent
BASE = INPUTS.parents[2]
FE = Path(os.environ.get("FE_BIN") or shutil.which("fe") or "fe")
spec = importlib.util.spec_from_file_location("local_v3_fixture_copy", INPUTS / "run_local.py")
v3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v3)
# The copied helper reads only its pinned dependency creation bytecode here.
v3.ROOT = INPUTS


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def walk_calls(node):
    yield node
    for child in node.get("calls", []):
        yield from walk_calls(child)


def revert_text(node):
    value = node.get("output", "")
    if value.startswith("0x08c379a0"):
        return decode(["string"], bytes.fromhex(value[10:]))[0]
    return None


class LifecycleRun:
    def __init__(self, rpc, out, ingot):
        self.rpc, self.out, self.ingot = rpc, out, ingot
        self.artifacts = out / "candidate-artifacts"
        self.fixture = v3.Fixture(rpc, out / "dependency-fixture")
        self.owner, self.other, self.buyer = self.fixture.lp, self.fixture.seller, self.fixture.buyer
        self.contracts = {}
        self.receipts = []

    def deploy(self, name, types=(), values=()):
        raw = bytes.fromhex((self.artifacts / (name + ".bin")).read_text().strip().removeprefix("0x"))
        receipt = self.ok(self.owner, data="0x" + (raw + encode(list(types), list(values))).hex())
        return to_checksum_address(receipt["contractAddress"])

    def send(self, sender, to=None, data="0x", value=0):
        receipt = self.rpc.send(sender, to, data, value, gas=14_000_000)
        self.receipts.append(receipt)
        save(self.out / "receipts-progress.json", self.receipts)
        return receipt

    def ok(self, sender, to=None, data="0x", value=0):
        receipt = self.send(sender, to, data, value)
        if int(receipt["status"], 16) != 1:
            save(self.out / "unexpected-revert.json", {"receipt": receipt, "trace": self.trace(receipt)})
            raise AssertionError("unexpected revert " + receipt["transactionHash"])
        return receipt

    def trace(self, receipt):
        return self.rpc.request("debug_traceTransaction", [receipt["transactionHash"], {"tracer": "callTracer"}])

    def view(self, address, signature, types=(), values=()):
        return self.rpc.call(self.owner, address, v3.calldata(signature, types, values))

    def uint(self, address, signature, types=(), values=()):
        return int(self.view(address, signature, types, values), 16)

    def balance(self, token, account):
        return self.uint(token, "balanceOf(address)", ["address"], [account])

    def allowance(self, token, owner):
        return self.uint(token, "allowance(address,address)", ["address", "address"], [owner, v3.NPM])

    def position(self):
        token_id = self.uint(self.contracts["locker"], "tokenId()")
        if token_id == 0:
            return {"id": 0, "liquidity": 0}
        data = self.view(v3.NPM, "positions(uint256)", ["uint256"], [token_id])
        fields = decode(["uint96", "address", "address", "address", "uint24", "int24", "int24", "uint128", "uint256", "uint256", "uint128", "uint128"], bytes.fromhex(data[2:]))
        return {"id": token_id, "liquidity": fields[7], "tick_lower": fields[5], "tick_upper": fields[6]}

    def expected_price(self):
        raised = self.uint(self.contracts["ieo"], "raised()")
        sold = self.uint(self.contracts["ieo"], "soldTokens()")
        rdl = 80_000_000 * 10**18 * sold // (100_000_000 * 10**18)
        native = raised * 9000 // 10000
        a0, a1 = (rdl, native) if int(self.contracts["token"], 16) < int(v3.WBNB, 16) else (native, rdl)
        return math.isqrt((a1 << 192) // a0), rdl, native

    def setup(self):
        fixture = self.fixture
        fixture.install_dependency("PancakeV3PoolDeployer", v3.DEPLOYER)
        fixture.install_dependency("PancakeV3Factory", v3.FACTORY, ["address"], [v3.DEPLOYER])
        self.ok(self.owner, v3.DEPLOYER, v3.calldata("setFactoryAddress(address)", ["address"], [v3.FACTORY]))
        fixture.install_dependency("WETH9", v3.WBNB)
        fixture.install_dependency("NonfungiblePositionManager", v3.NPM, ["address"] * 4, [v3.DEPLOYER, v3.FACTORY, v3.WBNB, self.owner])
        assert self.uint(v3.FACTORY, "feeAmountTickSpacing(uint24)", ["uint24"], [2500]) == 50
        nonce = int(self.rpc.request("eth_getTransactionCount", [self.owner, "latest"]), 16)
        predicted_mining = to_checksum_address(keccak(rlp.encode([bytes.fromhex(self.owner[2:]), nonce + 1]))[-20:])
        ieo = self.deploy("Ieo", ["uint8", "uint256", "address", "address", "uint256", "uint256"], [1, 1000, self.owner, predicted_mining, 864000, 10000])
        mining = self.deploy("Mining", ["address", "uint256", "uint256", "uint256", "address"], [ieo, 60, 604800, 2592000, self.other])
        assert mining == predicted_mining
        dist = self.deploy("Distributor", ["address", "address", "address", "uint256"], [mining, self.owner, v3.WBNB, 259200])
        token = self.deploy("Riddle", ["address"] * 5, [ieo, mining, dist, v3.WBNB, v3.NPM])
        locker = self.deploy("Locker", ["address"] * 5, [ieo, token, v3.WBNB, v3.NPM, dist])
        pool = v3.pool_address(token)
        self.contracts = {"ieo": ieo, "mining": mining, "dist": dist, "token": token, "locker": locker, "pool": pool}
        self.ok(self.owner, ieo, v3.calldata("bind(address,address)", ["address"] * 2, [token, locker]))
        self.ok(self.owner, mining, v3.calldata("bind(address)", ["address"], [token]))
        self.ok(self.owner, dist, v3.calldata("bind(address,address)", ["address"] * 2, [token, locker]))
        # Real payable slots, not synthetic book state; full sale allows early finalize.
        first = self.uint(ieo, "slotCost(uint256)", ["uint256"], [1])
        self.ok(self.buyer, ieo, v3.calldata("buySlots(uint256)", ["uint256"], [1]), first)
        rest = self.uint(ieo, "slotCost(uint256)", ["uint256"], [49999])
        self.ok(self.buyer, ieo, v3.calldata("buySlots(uint256)", ["uint256"], [49999]), rest)
        assert self.uint(ieo, "participants()") == 1
        self.ok(self.owner, ieo, v3.calldata("finalize()"))
        self.ok(self.owner, ieo, v3.calldata("settle(uint256)", ["uint256"], [1]))
        assert self.uint(ieo, "soldTokens()") == 100_000_000 * 10**18
        expected, rdl, native = self.expected_price()
        save(self.out / "setup.json", {"contracts": self.contracts, "expected_price": expected, "lp_rdl": rdl,
                                       "lp_native": native, "real_slot_payments": [first, rest],
                                       "position_before_launch": self.position(), "dependencies": fixture.dependencies,
                                       "scope": "source-built official V3; exact dependency relocation; no token/pool/book storage writes; genuine IEO/Mining/Locker/Distributor"})
        return self

    def initialize_pool(self, price):
        t0, t1 = sorted([self.contracts["token"], v3.WBNB], key=lambda a: int(a, 16))
        receipt = self.ok(self.other, v3.NPM, v3.calldata("createAndInitializePoolIfNecessary(address,address,uint24,uint160)", ["address", "address", "uint24", "uint160"], [t0, t1, 2500, price]))
        observed = decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"], bytes.fromhex(self.view(self.contracts["pool"], "slot0()")[2:]))
        assert observed[0] == price
        return receipt

    def launch(self, expect_success=True):
        receipt = self.send(self.owner, self.contracts["ieo"], v3.calldata("launch()"))
        trace = self.trace(receipt)
        save(self.out / "launch-receipt.json", receipt)
        save(self.out / "launch-trace.json", trace)
        if expect_success:
            assert int(receipt["status"], 16) == 1
            assert self.uint(self.contracts["ieo"], "launched()") == 1
            assert self.position()["liquidity"] > 0
        return receipt, trace

    def wrong_price(self, stage):
        expected, _, _ = self.expected_price()
        # Lower price remains valid at either token order; no arbitrary out-of-range fixture.
        self.initialize_pool(expected // 4)
        before = {"ieo_rdl": self.balance(self.contracts["token"], self.contracts["ieo"]),
                  "ieo_native": int(self.rpc.request("eth_getBalance", [self.contracts["ieo"], "latest"]), 16)}
        receipt, trace = self.launch(expect_success=False)
        status = int(receipt["status"], 16)
        observed = decode(["uint160", "int24", "uint16", "uint16", "uint16", "uint32", "bool"], bytes.fromhex(self.view(self.contracts["pool"], "slot0()")[2:]))[0]
        after = {"ieo_rdl": self.balance(self.contracts["token"], self.contracts["ieo"]),
                 "ieo_native": int(self.rpc.request("eth_getBalance", [self.contracts["ieo"], "latest"]), 16)}
        row = {"case": "official_wrong_launch_price", "stage": stage, "receipt_status": status,
               "expected_price": expected, "pool_price": observed, "before": before, "after": after,
               "position": self.position(), "revert": revert_text(trace)}
        save(self.out / "result.json", row)
        print(json.dumps(row, sort_keys=True), flush=True)
        if stage == "baseline":
            assert status == 1 and observed == expected // 4 and before != after
        else:
            assert status == 0 and before == after and self.position()["id"] == 0
            assert any(revert_text(n) == "wrong launch price" for n in walk_calls(trace)), "not the price guard"
            assert self.uint(self.contracts["ieo"], "launched()") == 0
            assert self.allowance(self.contracts["token"], self.contracts["locker"]) == 0
            assert self.allowance(v3.WBNB, self.contracts["locker"]) == 0

    def distributions(self, stage):
        expected, _, _ = self.expected_price()
        self.initialize_pool(expected)  # exact preinitialization control
        self.launch()
        self.rpc.request("evm_increaseTime", [864000])
        self.rpc.request("evm_mine", [])
        # Buyer now has an honest unlocked allocation; no RDL fixture storage edit.
        self.ok(self.buyer, self.contracts["ieo"], v3.calldata("withdraw(uint256)", ["uint256"], [20000 * 10**18]))
        self.ok(self.buyer, self.contracts["token"], v3.calldata("transfer(address,uint256)", ["address", "uint256"], [self.contracts["dist"], 1000 * 10**18]))
        self.rpc.request("evm_increaseTime", [259200])
        self.rpc.request("evm_mine", [])
        row = {"case": "official_rdl_only_distribution", "stage": stage,
               "locker_balances_before": [self.balance(self.contracts["token"], self.contracts["locker"]), self.balance(v3.WBNB, self.contracts["locker"])],
               "position_before": self.position()}
        receipt = self.send(self.owner, self.contracts["dist"], v3.calldata("distribute()"))
        trace = self.trace(receipt)
        save(self.out / "distribution-receipt.json", receipt)
        save(self.out / "distribution-trace.json", trace)
        row.update(receipt_status=int(receipt["status"], 16),
                   locker_balances_after=[self.balance(self.contracts["token"], self.contracts["locker"]), self.balance(v3.WBNB, self.contracts["locker"])],
                   distributor_balance=self.balance(self.contracts["token"], self.contracts["dist"]),
                   burned=self.balance(self.contracts["token"], "0x000000000000000000000000000000000000dead"),
                   dev_received=self.balance(self.contracts["token"], self.owner), position_after=self.position())
        save(self.out / "result.json", row)
        print(json.dumps(row, sort_keys=True), flush=True)
        assert row["receipt_status"] == 1 and row["distributor_balance"] == 0
        # Genuine full-range seed can leave paired integer dust. Its first
        # RDL-only distribution can consume that dust; no first-call DoS claim.
        assert row["locker_balances_after"][0] >= 249 * 10**18
        assert row["dev_received"] == 500 * 10**18
        assert self.allowance(self.contracts["token"], self.contracts["locker"]) == 0
        assert self.allowance(v3.WBNB, self.contracts["locker"]) == 0
        self.ok(self.buyer, self.contracts["token"], v3.calldata("transfer(address,uint256)", ["address", "uint256"], [self.contracts["dist"], 1000 * 10**18]))
        self.rpc.request("evm_increaseTime", [259200])
        self.rpc.request("evm_mine", [])
        second_receipt = self.send(self.owner, self.contracts["dist"], v3.calldata("distribute()"))
        second_trace = self.trace(second_receipt)
        second = {"case": "official_rdl_only_distribution_after_counterpart_dust_exhausted", "stage": stage,
                  "receipt_status": int(second_receipt["status"], 16),
                  "distributor_balance": self.balance(self.contracts["token"], self.contracts["dist"]),
                  "locker_balances": [self.balance(self.contracts["token"], self.contracts["locker"]), self.balance(v3.WBNB, self.contracts["locker"])],
                  "dev_received": self.balance(self.contracts["token"], self.owner), "position": self.position()}
        save(self.out / "second-distribution-receipt.json", second_receipt)
        save(self.out / "second-distribution-trace.json", second_trace)
        save(self.out / "second-result.json", second)
        print(json.dumps(second, sort_keys=True), flush=True)
        if stage == "baseline":
            assert second["receipt_status"] == 0 and second["distributor_balance"] == 1000 * 10**18
            core_mint = [n for n in walk_calls(second_trace) if n.get("to", "").lower() == self.contracts["pool"].lower() and n.get("input", "").startswith("0x3c8a7d8d")]
            assert len(core_mint) == 1 and core_mint[0].get("error"), "must fail in genuine pool mint"
            # uint128 amount is the fourth word in mint(address,int24,int24,uint128,bytes).
            amount = int(core_mint[0]["input"][10 + 3 * 64:10 + 4 * 64], 16)
            assert amount == 0 and core_mint[0].get("output", "0x") == "0x", "not a zero-liquidity core failure"
        else:
            assert second["receipt_status"] == 1 and second["distributor_balance"] == 0
            assert second["locker_balances"][0] >= 499 * 10**18 and second["locker_balances"][1] == 0
            assert second["dev_received"] == 1000 * 10**18
        if stage != "baseline":
            # Accumulated paired fees/donations later enable liquidity; burn/LP/dev ratios unchanged.
            self.ok(self.buyer, v3.WBNB, v3.calldata("deposit()"), 10**17)
            self.ok(self.buyer, v3.WBNB, v3.calldata("transfer(address,uint256)", ["address", "uint256"], [self.contracts["dist"], 10**17]))
            self.rpc.request("evm_increaseTime", [259200])
            self.rpc.request("evm_mine", [])
            before = self.position()
            self.ok(self.owner, self.contracts["dist"], v3.calldata("distribute()"))
            assert self.position()["liquidity"] > before["liquidity"]
            save(self.out / "paired-resume.json", {"before": before, "after": self.position(),
                                                  "locker_balances_after": [self.balance(self.contracts["token"], self.contracts["locker"]), self.balance(v3.WBNB, self.contracts["locker"])]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingot", type=Path, default=BASE)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", choices=["wrong-price", "distributions"], required=True)
    parser.add_argument("--stage", choices=["baseline", "guarded"], default="guarded")
    args = parser.parse_args()
    out = args.out.resolve()
    assert not out.exists(), "fresh evidence directory required"
    out.mkdir(parents=True)
    manifest = v3.verify_artifacts(INPUTS)
    if not FE.is_file() or hashlib.sha256(FE.read_bytes()).hexdigest() != manifest["fe"]["linux_amd64_sha256"]:
        raise ValueError("FE_BIN must name the pinned Fe 26.5.0 Linux amd64 compiler")
    build = subprocess.run([str(FE), "build", str(args.ingot), "--out-dir", str(out / "candidate-artifacts")], capture_output=True, text=True, timeout=150)
    (out / "candidate-build.log").write_text(build.stdout + build.stderr)
    save(out / "candidate-build.json", {"exit": build.returncode, "source_root": str(args.ingot), "compiler_sha256": hashlib.sha256(FE.read_bytes()).hexdigest()})
    if build.returncode:
        raise RuntimeError("candidate lifecycle build failed")
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    command = [shutil.which("anvil"), "--host", "127.0.0.1", "--port", str(port), "--chain-id", "31337", "--accounts", "5", "--timestamp", "1800000000", "--steps-tracing", "--silent"]
    save(out / "command.json", {"command": command, "ingot": str(args.ingot), "source_sha256": {str(p.relative_to(args.ingot)): hashlib.sha256(p.read_bytes()).hexdigest() for p in args.ingot.glob("src/*.fe")}})
    rpc = v3.Rpc(f"http://127.0.0.1:{port}", out / "rpc-transcript.jsonl")
    process = None
    try:
        with (out / "anvil.log").open("w") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
            for _ in range(200):
                if process.poll() is not None:
                    raise RuntimeError("Anvil terminated before readiness")
                try:
                    metadata = rpc.verify()
                    break
                except (OSError, RuntimeError):
                    time.sleep(0.05)
            else:
                raise RuntimeError("Anvil not ready")
            save(out / "local-safety.json", {"metadata": metadata, "endpoint": rpc.url, "fork": False, "public_rpc": False})
            run = LifecycleRun(rpc, out, args.ingot).setup()
            if args.case == "wrong-price":
                run.wrong_price(args.stage)
            else:
                run.distributions(args.stage)
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            save(out / "process-cleanup.json", {"pid": process.pid, "return_code": process.returncode, "terminated": process.poll() is not None})


if __name__ == "__main__":
    main()
