# Riddlemine

**Pitch:** A fair-launch token on BNB Chain where every 60-second round is a
subset-sum riddle that agents solve off-chain and the contract checks
on-chain, with no owner, no mint and no admin switch anywhere.

## The problem

Most "fair launch" tokens are fair only on paper: an owner can change the
tax, mint more, pause trading or pull the liquidity. Mining-style launches
usually hand rewards to whoever runs the most bots, with nothing to tie the
miner to the project.

## Approach

Everything is decided at deploy time and enforced by code:

- **RDL token:** 1B fixed supply, minted once. Trade tax to and from the pool
  starts at 5% and falls linearly to 1% over 30 days, with no setter.
- **IEO:** bid mode (allocation by sqrt(bid), refunds above the target) or
  slot mode (rising slot price). Below 50% the sale fails and refunds 90%.
- **Launch:** the IEO seeds a full-range PancakeSwap v3 position owned by a
  locker that can never exit. Fees only flow to the distributor.
- **Mining:** each round derives 32 numbers and a target from
  `keccak(chainid, round, "riddlemine")`. A winning answer is a 32-bit mask
  whose numbers sum to the target. 10 winners per round, a staking gate, a
  boost of up to 2x for IEO stakers, halving emission and 30-day vesting.
- **Tweet voucher:** a bot signs a daily voucher after it sees a miner's
  tweet; redeeming it adds 10% boost (still capped at 2x).
- **Distributor:** every 3 days, permissionless: 25% of collected RDL burned,
  25% back into the LP, 50% to dev (redirected to miners once mining is dry).

All of this was written during the contest, in 10 small PRs.

## Fe highlight

Fe's effect system makes every piece of state a handler touches explicit.
A bid can read the context and write the order book and the event log, and
nothing else; the compiler rejects any other access:

```rust
#[payable]
Bid -> u256 uses (ctx, mut book, mut log) {
    assert!(book.mode == MODE_BID, "not a bid sale")
    let who = ctx.caller()
    let after = book.contributed.get(key: who) + ctx.value().as_wei()
    assert!(after <= MAX_BID / book.scale, "bid above wallet cap")
    ...
}
```

The riddle itself is a few lines of `keccak_words`, mirrored byte for byte in
`scripts/solve.py`:

```rust
pub fn seed_of(chain: u256, round: u256) -> u256 {
    keccak_words([chain, round, 0x726964646c656d696e65]) // "riddlemine"
}
```

## Architecture

| Contract | File | Role |
| --- | --- | --- |
| `Riddle` | `src/lib.fe` | RDL token, decaying trade tax, computes its own v3 pool address |
| `Ieo` | `src/ieo.fe` | sale, settle, launch, IEO stake unlock and early exit |
| `Locker` | `src/locker.fe` | creates the pool, holds the LP NFT forever, `collect()` to the distributor |
| `Mining` | `src/mining.fe` | rounds, stake gate, boost, vouchers, vesting |
| `Distributor` | `src/distributor.fe` | splits tax and LP fees every 3 days |

Deploy order and binding are in the README.

## Build and test

```sh
fe --version   # fe 26.5.0
fe test        # 51 tests
fe build
```

The scripts need Python 3 with `eth-account` and `pycryptodome`
(`pip install eth-account pycryptodome`).

## Demo

Live on BSC testnet (chain 97), deployed with `scale = 10,000` so the sale
fits faucet BNB. Addresses and every transaction are in
`deployments/bsc-testnet.json`.

| | Address |
| --- | --- |
| RDL | [0xf65E38d64b5854298EaE18CbCd232b0421109092](https://testnet.bscscan.com/address/0xf65E38d64b5854298EaE18CbCd232b0421109092) |
| Ieo | [0x82632FC8DE348A7e582645889bD3CE282d4fb8D1](https://testnet.bscscan.com/address/0x82632FC8DE348A7e582645889bD3CE282d4fb8D1) |
| Mining | [0x63C082b7045cd3Cd9CED776D6E66ee7876266F04](https://testnet.bscscan.com/address/0x63C082b7045cd3Cd9CED776D6E66ee7876266F04) |
| Locker | [0x7c038092c0749b68aAa94D816F4D847Eee1527D7](https://testnet.bscscan.com/address/0x7c038092c0749b68aAa94D816F4D847Eee1527D7) |
| Distributor | [0x6799219494184Aaa67f0Cf8441ff4bE7f7d69cCd](https://testnet.bscscan.com/address/0x6799219494184Aaa67f0Cf8441ff4bE7f7d69cCd) |
| RDL/WBNB pool | [0xec678a5a49861e057c601d2251e6025e9c7bef1c](https://testnet.bscscan.com/address/0xec678a5a49861e057c601d2251e6025e9c7bef1c) |

What the demo did, reproducible with the scripts:

1. `deploy_testnet.py`: deploy and bind all five contracts.
2. `demo_bids.py`: 100 wallets bid the scaled cap, reaching half the target.
3. `demo_launch.py`: finalize, settle all 100, launch. The pool opened with
   40M RDL and 0.018 WBNB; 842.5M RDL went to mining and 45M were burned.
4. `demo_mine.py`: one bidder bought RDL in the pool, staked the gate,
   redeemed a voucher (boost 2x), and won rounds 14, 15 and 16, leaving
   468 RDL vesting.

The first testnet deployment found a real bug: the IEO forwarded only 500k
gas to the locker, too little to create a v3 pool, so launch reverted. It is
fixed in PR #9 and the record is kept in
`deployments/bsc-testnet-v1-failed-launch.json`.

## Limitations

- The contract checks answers; it cannot prove an AI found them. A laptop
  solves a round in milliseconds (`scripts/solve.py`, meet-in-the-middle),
  so "mined by AI agents" means agents are the intended miners, not a
  guarantee.
- The tweet voucher trusts one bot key fixed at deploy. The contract cannot
  see X.
- If someone creates the pool first at another price, the locker adds
  liquidity at that price. Launch right after the sale settles.
- No audit. Tokenomics do not guarantee any price.

## Declaration

- **Pre-existing work:** none. The repository started on November 5, 2026
  (commit `4576584`, README and license only).
- **Third-party code:** none copied. PancakeSwap v3 addresses and the pool
  init code hash come from PancakeSwap's docs and were checked on-chain.
- **AI assistance:** built with an AI agent (Hark) as pair programmer. The
  agent drafted most of the Fe code, tests, scripts and docs from my design
  decisions, and ran the testnet deploy. I chose the mechanics (IEO modes,
  tax curve, boost, voucher, distributor split), reviewed and merged every
  PR, and funded the testnet deployer.

## Team

Solo: DaddyUnikii.

## License

Apache-2.0
