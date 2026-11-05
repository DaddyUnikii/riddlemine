#!/usr/bin/env python3
"""Sign a daily tweet voucher for a miner (what the bot does after it checks
the miner's tweet).

Usage: python3 scripts/voucher.py <bot_privkey_hex> <chain_id> <miner> <day>

Prints day, v, r, s for `redeemVoucher(uint256,uint8,uint256,uint256)`.
`day` is the mining contract's `currentDay()`.
Needs pycryptodome and coincurve.
"""
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


if __name__ == "__main__":
    key, chain, miner, day = sys.argv[1], int(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4])
    v, r, s = sign(key, chain, miner, day)
    print("signer", address_of(key))
    print(day, v, r, s)
