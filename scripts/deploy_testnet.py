"""Deploy Riddlemine to BSC testnet after a read-only dependency preflight.

Usage: DEPLOYER_KEY_FILE=... BOT=0x... python3 scripts/deploy_testnet.py
Writes DEPLOYMENTS_FILE (default deployments/bsc-testnet.json).
Importing this module never reads keys or sends transactions.
"""
import json
import os
from pathlib import Path

import rlp
from eth_utils import keccak, to_checksum_address

from chain import Signer, rpc

WBNB = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"
NPM = "0x427bF5b37357632377eCbEC9de3626C71A5396c1"
FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
POOL_DEPLOYER = "0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9"
SCALE = 10_000
DAY = 86_400


def read_uint(address, signature, block, *args):
    payload = keccak(text=signature)[:4] + b"".join(x.to_bytes(32, "big") for x in args)
    result = rpc("eth_call", [{"to": address, "data": "0x" + payload.hex()}, block])
    if not isinstance(result, str) or not result.startswith("0x") or len(result) != 66:
        raise RuntimeError(f"invalid response from {signature}")
    return int(result, 16)


def preflight():
    """Fail before signing when the chain or fixed dependencies are wrong.

    Non-empty code and identity getters are compatibility checks, not proof
    that arbitrary bytecode is safe. These fixed addresses remain trusted.
    """
    if int(rpc("eth_chainId", []), 16) != 97:
        raise RuntimeError("wrong RPC chain: expected BSC testnet (97)")
    block = rpc("eth_blockNumber", [])
    for address in (WBNB, NPM, FACTORY, POOL_DEPLOYER):
        code = rpc("eth_getCode", [address, block])
        if not isinstance(code, str) or not code.startswith("0x") or len(code) <= 2:
            raise RuntimeError(f"missing contract code at {address}")
    if read_uint(NPM, "WETH9()", block) != int(WBNB, 16):
        raise RuntimeError("position manager WBNB mismatch")
    if read_uint(NPM, "factory()", block) != int(FACTORY, 16):
        raise RuntimeError("position manager factory mismatch")
    if read_uint(FACTORY, "poolDeployer()", block) != int(POOL_DEPLOYER, 16):
        raise RuntimeError("factory pool deployer mismatch")
    if read_uint(FACTORY, "feeAmountTickSpacing(uint24)", block, 2500) != 50:
        raise RuntimeError("unexpected tick spacing for the 0.25% pool")
    return block


def main():
    ieo_secs = int(os.environ.get("IEO_SECS", "1800"))
    if ieo_secs <= 0:
        raise ValueError("IEO_SECS must be positive")
    bot = os.environ["BOT"]
    if len(bot) != 42 or not bot.startswith("0x") or int(bot, 16) == 0:
        raise ValueError("BOT must be a nonzero 20-byte address")
    bot = to_checksum_address(bot)
    block = preflight()
    # No key is read and no signer created until the checks above succeed.
    me = Signer(Path(os.environ["DEPLOYER_KEY_FILE"]).read_text().strip())
    out = {
        "chainId": 97, "scale": SCALE, "deployer": me.address, "bot": bot,
        "wbnb": WBNB, "npm": NPM, "preflightBlock": block,
    }

    def at(nonce):
        return to_checksum_address(keccak(rlp.encode([bytes.fromhex(me.address[2:]), nonce]))[12:])

    # Ieo is nonce n; Mining must be nonce n+1. Do not share this signer
    # with another deployment process while these transactions are sent.
    mining_at = at(me.nonce + 1)
    out["ieo"] = me.deploy("out/Ieo.bin", 0, ieo_secs, me.address, mining_at, DAY, SCALE)
    out["mining"] = me.deploy("out/Mining.bin", out["ieo"], 60, 7 * DAY, 30 * DAY, bot)
    if out["mining"] != mining_at:
        raise RuntimeError("mining address mismatch")
    out["distributor"] = me.deploy("out/Distributor.bin", out["mining"], me.address, WBNB, 3 * DAY)
    out["token"] = me.deploy("out/Riddle.bin", out["ieo"], out["mining"], out["distributor"], WBNB, NPM)
    out["locker"] = me.deploy("out/Locker.bin", out["ieo"], out["token"], WBNB, NPM, out["distributor"])
    me.tx(out["ieo"], "bind(address,address)", out["token"], out["locker"])
    me.tx(out["mining"], "bind(address)", out["token"])
    me.tx(out["distributor"], "bind(address,address)", out["token"], out["locker"])
    path = Path(os.environ.get("DEPLOYMENTS_FILE", "deployments/bsc-testnet.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
