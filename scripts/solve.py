#!/usr/bin/env python3
"""Solve a Riddlemine round off-chain with meet-in-the-middle.

Usage: python3 scripts/solve.py <chain_id> <round> [count]

Prints up to `count` (default 1) answer masks, ready for `submit(uint256)`.
Mirrors seed_of / number_of / target_of in src/mining.fe.
Needs pycryptodome: pip install pycryptodome
"""
import sys
from Crypto.Hash import keccak

NUMBERS = 32
TAG = 0x726964646C656D696E65  # "riddlemine"


def kw(*words):
    data = b"".join(w.to_bytes(32, "big") for w in words)
    return int(keccak.new(digest_bits=256, data=data).hexdigest(), 16)


def seed_of(chain, rnd):
    return kw(chain, rnd, TAG)


def number_of(seed, i):
    return (kw(seed, i) >> 236) + 1


def target_of(seed):
    total = sum(number_of(seed, i) for i in range(NUMBERS))
    return total // 4 + kw(seed, NUMBERS) % (total // 2)


def solve(seed, count=1):
    nums = [number_of(seed, i) for i in range(NUMBERS)]
    target = target_of(seed)
    half = NUMBERS // 2
    left = {}
    for m in range(1 << half):
        s = sum(nums[i] for i in range(half) if m >> i & 1)
        left.setdefault(s, m)
    found = []
    for m in range(1 << half):
        r = sum(nums[half + i] for i in range(half) if m >> i & 1)
        lo = left.get(target - r)
        if lo is not None and (lo | m << half):
            found.append(lo | m << half)
            if len(found) == count:
                break
    return found


if __name__ == "__main__":
    chain, rnd = int(sys.argv[1]), int(sys.argv[2])
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    for mask in solve(seed_of(chain, rnd), count):
        print(hex(mask))
