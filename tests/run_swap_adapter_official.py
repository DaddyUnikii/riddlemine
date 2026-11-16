#!/usr/bin/env python3
"""Actual Pancake V3 integration; explicit local fixture path required.

Uses unmodified pinned official contracts, genuine pool CREATE2/NPM mint,
real WETH deposits and current Fe bytecode. Fixture relocates only dependency
runtime plus constructor-written storage. No public RPC, fork, or IEO claim.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from eth_abi import decode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fe", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("pancake_local_fixture", args.fixture / "run_local.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    manifest = module.verify_artifacts()
    compiler = Path(args.fe)
    if hashlib.sha256(compiler.read_bytes()).hexdigest() != manifest["fe"]["linux_amd64_sha256"]:
        raise ValueError("FE_BIN must name the pinned Fe 26.5.0 compiler")
    adapter_artifacts = out / "adapter-artifacts"
    command = [str(compiler), "build", str(args.root), "--contract", "SwapAdapter", "--out-dir", str(adapter_artifacts), "--emit", "bytecode,runtime-bytecode,abi,metadata"]
    build = subprocess.run(command, capture_output=True, text=True, timeout=180)
    (out / "adapter-build.log").write_text(build.stdout + build.stderr)
    module.save(out / "adapter-build.json", {"command": command, "exit": build.returncode, "compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest()})
    if build.returncode:
        raise RuntimeError("fresh adapter build failed; see adapter-build.log")
    results = []
    with module.run_fixture(args.root, out=out, fe_bin=args.fe, rpc_url="http://127.0.0.1:18561") as f:
        adapter_bin = adapter_artifacts / "SwapAdapter.bin"
        adapter, deployed = f.deploy_adapter(adapter_bin)
        module.save(out / "adapter-deploy-receipt.json", deployed)
        module.save(out / "adapter-source.json", {
            "source": str(args.root / "src" / "swap_adapter.fe"),
            "source_sha256": hashlib.sha256((args.root / "src" / "swap_adapter.fe").read_bytes()).hexdigest(),
            "creation_raw_sha256": hashlib.sha256(bytes.fromhex(adapter_bin.read_text().strip().removeprefix("0x"))).hexdigest(),
            "adapter": adapter,
        })
        assert f.address(adapter, "token()") == f.token
        assert f.address(adapter, "wbnb()") == module.WBNB
        assert f.address(adapter, "pool()") == f.pool

        def state(payer):
            return {"payer_rdl": f.balance(f.token,payer), "payer_wbnb":f.balance(module.WBNB,payer),
                    "pool_rdl":f.balance(f.token,f.pool), "pool_wbnb":f.balance(module.WBNB,f.pool),
                    "dist_rdl":f.balance(f.token,f.dist), "adapter_rdl":f.balance(f.token,adapter),
                    "adapter_wbnb":f.balance(module.WBNB,adapter),
                    "pool_slot0":f.rpc.call(f.lp,f.pool,module.calldata("slot0()")),
                    "tax_rate":f.uint(f.token,"currentTaxBps()"),
                    "rdl_allowance":f.uint(f.token,"allowance(address,address)",["address","address"],[payer,adapter]),
                    "wbnb_allowance":f.uint(module.WBNB,"allowance(address,address)",["address","address"],[payer,adapter])}

        def trace_nodes(node):
            yield node
            for child in node.get("calls",[]):
                yield from trace_nodes(child)

        def positive(method, amount, bound, price_limit=0, name=None):
            name = name or method
            sell = method.startswith("sell")
            payer = f.seller if sell else f.buyer
            before = state(payer)
            rate = f.uint(f.token,"currentTaxBps()")
            data = f.adapter_data(method,amount,bound,sqrt_price_limit_x96=price_limit)
            quoted = decode(["uint256","uint256"],bytes.fromhex(f.rpc.call(payer,adapter,data)[2:]))
            receipt = f.ok(payer,adapter,data)
            after = state(payer)
            trace = f.rpc.request("debug_traceTransaction",[receipt["transactionHash"],{"tracer":"callTracer"}])
            module.save(out / (name + "-receipt.json"),receipt)
            module.save(out / (name + "-trace.json"),trace)
            nodes = list(trace_nodes(trace))
            callback_selector = module.hx(module.keccak(text="pancakeV3SwapCallback(int256,int256,bytes)")[:4])
            callbacks = [n for n in nodes if n.get("to","").lower()==adapter.lower() and n.get("input","").startswith(callback_selector)]
            assert len(callbacks)==1
            d0,d1,opaque = decode(["int256","int256","bytes"],bytes.fromhex(callbacks[0]["input"][10:]))
            debt = d0 if (f.token if sell else module.WBNB)==f.token0 else d1
            gross_output = -(d1 if (f.token if sell else module.WBNB)==f.token0 else d0)
            assert debt > 0 and gross_output > 0 and opaque==b""
            spent = before["payer_rdl"]-after["payer_rdl"] if sell else before["payer_wbnb"]-after["payer_wbnb"]
            received = after["payer_wbnb"]-before["payer_wbnb"] if sell else after["payer_rdl"]-before["payer_rdl"]
            assert (spent,received)==quoted
            assert after["adapter_rdl"]==before["adapter_rdl"]==0 and after["adapter_wbnb"]==before["adapter_wbnb"]==0
            if sell:
                assert after["pool_rdl"]-before["pool_rdl"]==debt
                assert spent==module.minimum_gross_for_net(debt,rate)
                assert after["dist_rdl"]-before["dist_rdl"]==spent*rate//10000
                assert received==gross_output
            else:
                assert after["pool_wbnb"]-before["pool_wbnb"]==debt==spent
                assert before["pool_rdl"]-after["pool_rdl"]==gross_output
                assert after["dist_rdl"]-before["dist_rdl"]==gross_output*rate//10000
                assert received==gross_output-gross_output*rate//10000
            if method.endswith("ExactInput"):
                assert spent<=amount and received>=bound
            else:
                assert spent<=bound and received>=amount
                assert gross_output==(amount if sell else module.minimum_gross_for_net(amount,rate))
            row = {"case":name,"method":method,"price_limit":price_limit,"status":"PASS","spent":spent,"received":received,"callback_debt":debt,"gross_output":gross_output,"rate":rate,"tx":receipt["transactionHash"]}
            results.append(row)
            module.save(out / (name + "-assertions.json"), row)

        cases = [("sellExactInput",1000*10**18,1), ("buyExactInput",10**16,1),
                 ("sellExactOutput",10**15,1000*10**18), ("buyExactOutput",1000*10**18,10**17)]
        for values in cases:
            f.branch(lambda values=values: positive(*values))
        def partial_input(method):
            sell = method.startswith("sell")
            input_token = f.token if sell else module.WBNB
            slot = decode(["uint160","int24","uint16","uint16","uint16","uint32","bool"],bytes.fromhex(f.rpc.call(f.lp,f.pool,module.calldata("slot0()"))[2:]))
            price = slot[0]
            price_limit = price * 999999 // 1000000 if input_token==f.token0 else price * 1000001 // 1000000
            budget = 1000*10**18 if sell else 10**16
            positive(method,budget,1,price_limit,name=method+"Partial")
            assert results[-1]["spent"] < budget

        for method in ("sellExactInput","buyExactInput"):
            f.branch(lambda method=method: partial_input(method))

        def revert_control(name,method,amount,bound,*,deadline=None,price_limit=0,allowance_zero=False):
            payer = f.seller if method.startswith("sell") else f.buyer
            if allowance_zero:
                input_token = f.token if method.startswith("sell") else module.WBNB
                f.ok(payer,input_token,module.calldata("approve(address,uint256)",["address","uint256"],[adapter,0]))
            before = state(payer)
            data = f.adapter_data(method,amount,bound,deadline=deadline,sqrt_price_limit_x96=price_limit)
            try:
                f.rpc.call(payer,adapter,data)
            except module.RpcError as exc:
                error = str(exc)
            else:
                raise AssertionError("revert control succeeded: "+name)
            receipt = f.rpc.send(payer,adapter,data)
            assert int(receipt["status"],16)==0 and state(payer)==before
            trace = f.rpc.request("debug_traceTransaction",[receipt["transactionHash"],{"tracer":"callTracer"}])
            module.save(out / (name+"-receipt.json"),receipt)
            module.save(out / (name+"-trace.json"),trace)
            row={"case":name,"status":"PASS_REVERT_ROLLBACK","error":error,"tx":receipt["transactionHash"]}
            results.append(row)
            module.save(out / (name+"-assertions.json"),row)

        controls=[
            ("expiredDeadline","sellExactInput",1000*10**18,1,{"deadline":0}),
            ("buyNetMinimum","buyExactInput",10**16,10**30,{}),
            ("sellZeroAllowance","sellExactInput",1000*10**18,1,{"allowance_zero":True}),
            ("buyZeroAllowance","buyExactInput",10**16,1,{"allowance_zero":True}),
            ("invalidPriceLimit","sellExactInput",1000*10**18,1,{"price_limit":4295128739}),
        ]
        for name,method,amount,bound,kw in controls:
            f.branch(lambda name=name,method=method,amount=amount,bound=bound,kw=kw: revert_control(name,method,amount,bound,**kw))
        def partial_output(method):
            sell = method.startswith("sell")
            input_token = f.token if sell else module.WBNB
            slot = decode(["uint160","int24","uint16","uint16","uint16","uint32","bool"],bytes.fromhex(f.rpc.call(f.lp,f.pool,module.calldata("slot0()"))[2:]))
            price = slot[0]
            limit = price*999999//1000000 if input_token==f.token0 else price*1000001//1000000
            revert_control(method+"PartialReject",method,10**15 if sell else 1000*10**18,1000*10**18 if sell else 10**17,price_limit=limit)
            assert "partial exact output" in results[-1]["error"]

        for method in ("sellExactOutput","buyExactOutput"):
            f.branch(lambda method=method: partial_output(method))

        def decayed_routes(elapsed, expected_rate):
            # A real positive pool trade starts the unchanged RDL clock.
            f.ok(f.buyer, adapter, f.adapter_data("buyExactInput", 10**12, 1))
            assert f.uint(f.token, "currentTaxBps()") == 500
            f.rpc.request("evm_increaseTime", [elapsed])
            f.rpc.request("evm_mine", [])
            assert f.uint(f.token, "currentTaxBps()") == expected_rate
            for method, amount, bound in cases:
                f.branch(lambda method=method, amount=amount, bound=bound: positive(
                    method, amount, bound, name=method + str(expected_rate) + "bps"))

        for elapsed, rate in ((15 * 86400, 300), (30 * 86400, 100)):
            f.branch(lambda elapsed=elapsed, rate=rate: decayed_routes(elapsed, rate))
        decay_names = {method + str(rate) + "bps" for method, _, _ in cases for rate in (300, 100)}
        expected = {x[0] for x in cases} | {"sellExactInputPartial","buyExactInputPartial","sellExactOutputPartialReject","buyExactOutputPartialReject"} | {x[0] for x in controls} | decay_names
        assert {r["case"] for r in results}==expected and len(results)==len(expected)==21
        summary = {"status":"PASS","adapter_cases":21,"official_npm_control":1,"results":results,
                   "pool_created_by_genuine_create2":True,"fork_used":False,"public_rpc_used":False,
                   "limits":["dependency relocation declared by fixture","no IEO launch E2E","adversarial callbacks remain unit fixture coverage"]}
        module.save(out / "adapter-summary.json",summary)
        print(json.dumps(summary,indent=2))


if __name__=="__main__":
    main()
