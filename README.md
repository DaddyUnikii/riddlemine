# Riddlemine

A fair-launch token mined by AI agents, written in [Fe](https://fe-lang.org).
Entry for the [Fe Builders Challenge 2026](https://fe-lang.org/contest/).

Every ~1-minute round the contract derives a subset-sum puzzle from the round
seed. Agents submit answers, the contract checks them on-chain and splits the
round's emission. A bid-or-slot IEO funds a collect-only LP position, IEO
stakes are non-transferable and boost mining, and the trade tax decays from
5% to 1% with no owner setter.

Built in small PRs. The submission write-up is in [SUBMISSION.md](SUBMISSION.md).

## Status

| Module | File | State |
| --- | --- | --- |
| RDL token (fixed supply, no owner), trade tax 5% falling to 1% over 30 days, no setter | `src/lib.fe` | done |
| IEO: bid mode (sqrt-weighted, refunds) and slot mode, soft cap, batched refunds | `src/ieo.fe` | done |
| Launch (LP seed, leftover burn/mining/bonus), dev pull, IEO stake unlock and early exit | `src/ieo.fe` | done |
| LP locker: PancakeSwap v3 full-range position, no exit, fees only to `fee_to`, permissionless `increase()` | `src/locker.fe` | done |
| Mining: subset-sum riddle per 60 s round, stake gate, IEO boost up to 2x, halving emission, 30-day vesting | `src/mining.fe`, `scripts/solve.py` | done |
| Distributor: tax + LP fees split every 3 days (25% burn, 25% LP, 50% dev, dev share to miners once the pool is dry) | `src/distributor.fe` | done |
| Tweet voucher: bot-signed daily voucher, +10% boost, total capped at 2x | `src/mining.fe`, `scripts/voucher.py` | done |
| BSC testnet deploy and demo (sale, launch, mining) | `scripts/`, `deployments/` | done |

| Allocation | Share | Amount |
| --- | --- | --- |
| IEO | 10% | 100,000,000 RDL |
| Launch LP | 8% | 80,000,000 RDL |
| Mining | 82% | 820,000,000 RDL |
| Dev | 0% | 0 |

```sh
fe test
fe build
```

License: Apache-2.0

## Deploy order

Every contract needs some of the others' addresses, so they are deployed in
this order and bound once by the deployer:

1. `Ieo(mode, 10 days, dev, mining, 10 days, scale)`, `Mining(ieo, 60, 7 days, 30 days, bot)`, `Distributor`
   (`scale` is 1 on mainnet; on testnet it divides every BNB amount of the sale,
   caps included, so the demo runs on faucet BNB)
2. `Riddle(ieo, mining, distributor, wbnb, npm)` (computes its own v3 pool address)
3. `Locker(ieo, token, wbnb, npm, fee_to = distributor)`
4. `ieo.bind(token, locker)`, `mining.bind(token)`, `distributor.bind(token, locker)`
