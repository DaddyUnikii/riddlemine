#!/usr/bin/env python3
"""Sign a daily tweet voucher for a miner (what the bot does after it checks
the miner's tweet).

Usage: BOT_KEY_FILE=/secure/path/bot.key python3 scripts/voucher.py <chain_id> <miner> <day>
Never pass private keys as command-line arguments (visible to process tools).

Prints day, v, r, s for `redeemVoucher(uint256,uint8,uint256,uint256)`.
`day` is the mining contract's `currentDay()`.
Needs pycryptodome and coincurve.
"""
import os
from pathlib import Path
import sys
from Crypto.Hash import keccak
from coincurve import PrivateKey

TAG = 0x726464762D766F7563686572  # "rddv-voucher"


def kw(*words):
    data = b"".join(w.to_bytes(32, "big") for w in words)
    return keccak.new(digest_bits=256, data=data).digest()


def voucher_digest(chain, miner, day):
    return kw(TAG, chain, miner, day)


def sign(key_hex, chain, miner, day):
    sig = PrivateKey(bytes.fromhex(key_hex.removeprefix("0x"))).sign_recoverable(
        voucher_digest(chain, miner, day), hasher=None
    )
    r, s, rec = sig[:32], sig[32:64], sig[64]
    return 27 + rec, "0x" + r.hex(), "0x" + s.hex()


def address_of(key_hex):
    pub = PrivateKey(bytes.fromhex(key_hex.removeprefix("0x"))).public_key.format(compressed=False)[1:]
    return "0x" + keccak.new(digest_bits=256, data=pub).digest()[-20:].hex()


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: BOT_KEY_FILE=... voucher.py <chain_id> <miner> <day>")
    try:
        chain, miner, day = int(sys.argv[1]), int(sys.argv[2], 16), int(sys.argv[3])
        if not (0 < chain < 2**256 and 0 < miner < 2**160 and 0 <= day < 2**256):
            raise ValueError("invalid voucher arguments")
        key = Path(os.environ["BOT_KEY_FILE"]).read_text().strip()
        v, r, s = sign(key, chain, miner, day)
        signer = address_of(key)
    except (KeyError, OSError, ValueError):
        # Never expose key contents or exception text from key parsing.
        raise SystemExit("invalid arguments or unreadable/invalid BOT_KEY_FILE") from None
    print("signer", signer)
    print(day, v, r, s)


if __name__ == "__main__":
    main()
