# Riddlemine

A fair-launch token mined by AI agents, written in [Fe](https://fe-lang.org).
Entry for the [Fe Builders Challenge 2026](https://fe-lang.org/contest/).

Every ~1-minute round the contract derives a subset-sum puzzle from the round
seed. Agents submit answers, the contract checks them on-chain and splits the
round's emission. A bid-or-slot IEO funds a collect-only LP position, IEO
stakes are non-transferable and boost mining, and the trade tax decays from
5% to 1% with no owner setter.

Work in progress, built in small PRs.

## Status

| Module | File | State |
| --- | --- | --- |
| RDL token (fixed supply, no owner) | `src/lib.fe` | done |
| IEO: bid mode (sqrt-weighted, refunds) and slot mode, soft cap, batched refunds | `src/ieo.fe` | done |
| Launch, LP locker, IEO stake unlock | | next |
| Mining (subset-sum rounds), boosts, vesting | | planned |
| Trade tax, `distribute()` | | planned |

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
