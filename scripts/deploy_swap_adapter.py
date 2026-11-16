"""Read-only adapter preflight, or explicit --deploy to BSC testnet (97).

Usage: TOKEN=0x... python3 scripts/deploy_swap_adapter.py
Only --deploy reads DEPLOYER_KEY_FILE and sends a transaction. The token must
expose TaxViews, and its official 0.25% pool must already have been created.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
from chain import Signer, rpc
from eth_utils import keccak, to_checksum_address

WBNB = "0xae13d989dac2f0debff460ac112a837c89baa7cd"
FACTORY = "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
NPM = "0x427bf5b37357632377ecbec9de3626c71a5396c1"
POOL_DEPLOYER = "0x41ff9aa7e16b8b1a8a8dc4f0efacd93d02d071c9"
POOL_HASH = bytes.fromhex("6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2")


def pool_address(token):
    pair = sorted((int(token, 16), int(WBNB, 16)))
    salt = keccak(b"".join(value.to_bytes(32, "big") for value in (*pair, 2500)))
    return to_checksum_address(keccak(b"\xff" + bytes.fromhex(POOL_DEPLOYER[2:]) + salt + POOL_HASH)[12:])


def read_uint(target, signature, block, *args):
    data = keccak(text=signature)[:4] + b"".join(value.to_bytes(32, "big") for value in args)
    result = rpc("eth_call", [{"to": target, "data": "0x" + data.hex()}, block])
    if not isinstance(result, str) or len(result) != 66 or not result.startswith("0x"):
        raise RuntimeError(f"invalid response from {signature}")
    return int(result, 16)


def read_address(target, signature, block, *args):
    value = read_uint(target, signature, block, *args)
    if not 0 < value < 2**160:
        raise RuntimeError(f"invalid address from {signature}")
    return to_checksum_address(value.to_bytes(20, "big"))


def address(value, label):
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        raise ValueError(f"{label} must be a nonzero 20-byte address")
    try:
        valid = 0 < int(value, 16) < 2**160
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{label} must be a nonzero 20-byte address")
    return to_checksum_address(value)


def require_code(target, block):
    code = rpc("eth_getCode", [target, block])
    if not isinstance(code, str) or not code.startswith("0x") or len(code) <= 2:
        raise RuntimeError(f"missing contract code at {target}")


def preflight(token):
    if int(rpc("eth_chainId", []), 16) != 97:
        raise RuntimeError("adapter deployment requires chain 97")
    block = rpc("eth_blockNumber", [])
    if token.lower() == WBNB:
        raise RuntimeError("token and WBNB must be distinct")
    require_code(token, block)
    pool = read_address(token, "pool()", block)
    if pool.lower() != pool_address(token).lower():
        raise RuntimeError("token pool binding mismatch")
    require_code(pool, block)
    require_code(to_checksum_address(WBNB), block)
    if read_address(pool, "factory()", block).lower() != FACTORY:
        raise RuntimeError("pool factory mismatch")
    if read_uint(pool, "fee()", block) != 2500:
        raise RuntimeError("pool fee mismatch")
    token0 = read_address(pool, "token0()", block)
    token1 = read_address(pool, "token1()", block)
    if (int(token0, 16), int(token1, 16)) != tuple(sorted((int(token, 16), int(WBNB, 16)))):
        raise RuntimeError("pool tokens mismatch")
    require_code(to_checksum_address(FACTORY), block)
    registered = read_address(to_checksum_address(FACTORY), "getPool(address,address,uint24)", block, int(token, 16), int(WBNB, 16), 2500)
    if registered.lower() != pool.lower():
        raise RuntimeError("factory registry mismatch")
    npm = read_address(token, "positionManager()", block)
    if npm.lower() != NPM:
        raise RuntimeError("token position manager mismatch")
    require_code(npm, block)
    rate = read_uint(token, "currentTaxBps()", block)
    if not 100 <= rate <= 500:
        raise RuntimeError("invalid tax quote: expected 100..500 bps")
    distributor = read_address(token, "distributor()", block)
    if distributor.lower() == pool.lower():
        raise RuntimeError("invalid pool/distributor identity")
    return {"preflightBlock": block, "token": token, "pool": pool, "wbnb": to_checksum_address(WBNB)}


def main(deploy=False):
    token = address(os.environ["TOKEN"], "TOKEN")
    out = preflight(token)
    if not deploy:
        print(json.dumps(out, indent=2))
        return out
    binary = Path(os.environ.get("SWAP_ADAPTER_BIN", str(Path(__file__).resolve().parents[1] / "out/SwapAdapter.bin")))
    try:
        bytecode = bytes.fromhex(binary.read_text().strip().removeprefix("0x"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("missing or invalid SwapAdapter build artifact; run fe build") from exc
    if not bytecode or len(bytecode) > 49152:
        raise RuntimeError("invalid SwapAdapter build artifact length")
    out["artifactSha256"] = hashlib.sha256(bytecode).hexdigest()
    signer = Signer(Path(os.environ["DEPLOYER_KEY_FILE"]).read_text().strip())
    payload = bytecode + b"".join(int(out[key], 16).to_bytes(32, "big") for key in ("token", "wbnb", "pool"))
    receipt = signer.send(None, payload)
    adapter = address(receipt["contractAddress"], "adapter receipt")
    require_code(adapter, "latest")
    for key in ("token", "wbnb", "pool"):
        actual = read_address(adapter, key + "()", "latest")
        if actual.lower() != out[key].lower():
            raise RuntimeError("deployed adapter binding mismatch: " + key)
    out.update({"chainId": 97, "adapter": adapter, "deployer": signer.address, "transactionHash": receipt["transactionHash"]})
    path = Path(os.environ.get("ADAPTER_DEPLOYMENTS_FILE", "deployments/bsc-testnet-swap-adapter.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true", help="sign and deploy after a read-only preflight")
    main(deploy=parser.parse_args().deploy)
