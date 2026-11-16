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
| BSC testnet deploy and demo (sale, launch, mining) | `scripts/`, `deployments/` | historical deployment; immutable |
| Net-aware RDL/WBNB adapter (buy/sell, exact-input/output) | `src/swap_adapter.fe` | local-real V3 tested; not deployed |

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

License: Apache-2.0 for Riddlemine; bundled official test dependencies keep
[their upstream licenses](tests/fixtures/pancake_v3/README.md).

## Deploy order

Every contract needs some of the others' addresses, so they are deployed in
this order and bound once by the deployer:

1. `Ieo(mode, 10 days, dev, mining, 10 days, scale)`, `Mining(ieo, 60, 7 days, 30 days, bot)`, `Distributor`
   (`scale` is 1 on mainnet; on testnet it divides every BNB amount of the sale,
   caps included, so the demo runs on faucet BNB)
2. `Riddle(ieo, mining, distributor, wbnb, npm)` (computes its own v3 pool address)
3. `Locker(ieo, token, wbnb, npm, fee_to = distributor)`
4. `ieo.bind(token, locker)`, `mining.bind(token)`, `distributor.bind(token, locker)`

## Security revision and reproducible checks

The security-hardening branch blocks `exitEarly()` until launch, ignores
zero-token operations when starting tax decay, and clears both finite LP
allowances after mint/increase. It also rejects sale scales that round the
advertised minimum/first-slot price to zero, refuses a preinitialized pool at
a different launch price, and keeps zero-liquidity LP inputs for a later
permissionless increase instead of reverting an entire distribution.
The original testnet addresses recorded in
`deployments/bsc-testnet.json` are not upgraded by source changes: these
contracts are not upgradeable.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
fe fmt --check
fe test
fe build
.venv/bin/python -m unittest discover -s tests -p 'test_deploy_preflight.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_voucher_cli.py' -v
```

Time-dependent tax regressions exercise built Fe bytecode on Anvil 1.9.0:

```sh
# Terminal 1: disposable local chain only, no keys or public funds required.
anvil --host 127.0.0.1 --port 18556 --chain-id 97 --silent
# Terminal 2:
LOCAL_EVM_RPC=http://127.0.0.1:18556 RUN_LOCAL_V3=1 \
  .venv/bin/python -m unittest discover -s tests -v
```

With `LOCAL_EVM_RPC` unset, the seven tax-clock Anvil tests explicitly skip.
With `RUN_LOCAL_V3` unset, the optional genuine-V3 classes explicitly skip;
neither skip is an EVM validation pass. The adapter unit EVM class owns its
own disposable loopback chain31337 and must execute, not skip. Set `FE_BIN` to
the pinned Fe26.5.0 binary if it is not on `PATH`. CI enables both groups.

`Riddle.currentTaxBps()` returns the rate a qualifying transfer would use
right now (500 bps before the first positive pool transfer). `pool()`,
`positionManager()` and `distributor()` expose the immutable trading bindings.
These views neither start the clock nor grant a tax exemption.

`deploy_testnet.py` verifies chain 97, non-empty dependency code, the manager's
WBNB/factory identity, the pool deployer and tick spacing before reading a key
or creating a signer. These are compatibility checks, not certification of
the selected contracts. Run a read-only preflight without signing:

```sh
PYTHONPATH=scripts .venv/bin/python -c 'from deploy_testnet import preflight; print(preflight())'
```

The voucher CLI reads a key file instead of exposing it in process arguments:

```sh
BOT_KEY_FILE=/secure/path/bot.key .venv/bin/python scripts/voucher.py 97 0xMINER_ADDRESS DAY
```

Keep key files outside the repo with mode `0600`; use ignored `secrets/` if a
local directory is unavoidable. Never pass private keys in CLI arguments or
commit bidder-key JSON files. The pinned Python versions and verified Fe
binary checksum used in CI match those exercised locally.

## Net-aware swaps: keep both buy and sell tax

**Standard Pancake V3 routers remain incompatible with taxed RDL sells.**
They pay a gross callback debt but the pool receives less after tax (`IIA`).
Standard buy output checks are gross, not the recipient's net RDL. The new
`SwapAdapter(token, wbnb, pool)` is a separate route, not a router upgrade or
an exemption. Frontends must explicitly integrate it. Existing deployed RDL
addresses lacking the new tax views cannot use this adapter.

All four methods take `(amount, limit, deadline, uint160 sqrtPriceLimitX96)`
and return `(spent, received)`:

- `sellExactInput`: gross RDL budget, minimum WBNB received.
- `buyExactInput`: WBNB budget, minimum **net** RDL received.
- `sellExactOutput`: WBNB wanted, maximum gross RDL debit.
- `buyExactOutput`: **net** RDL wanted, maximum WBNB debit.

The caller grants a finite input-token approval to the adapter. Output goes
straight from the pool to that caller; there is no owner, sweep, withdrawal,
refundable custody, arbitrary pool, or arbitrary recipient path. Zero price
limit selects the pool direction's default boundary; a nonzero limit is used
as supplied. Exact-input partial fills preserve unspent funds; exact-output
partial fills revert atomically. Use a meaningful minimum/maximum and deadline:
these protect the supplied limit, not against all MEV or price movement.

Sell callback payment uses the **minimum** gross amount whose floor-rounded
net equals pool debt: at 500bps, 1000 net needs 1052 gross, not 1051. Returned
sell `spent` means transfer/allowance debit; if the distributor itself is the
payer, its existing tax rebate makes wallet decrease equal net debt instead.
Tax starts only on the first positive qualifying pool transfer and still
falls 500→100 bps over 30 days. Quotes do not start it.

To retain fresh receipt/trace evidence, from the repository root:

```sh
.venv/bin/python tests/run_swap_adapter_official.py \
  --fixture tests/fixtures/pancake_v3 --root . --out /path/to/new-evidence \
  --fe /absolute/path/to/fe-26.5.0
```

The official implementation fixture source-builds the pool/router/NPM and
really creates the pool via CREATE2, without archive RPC. It explicitly
relocates constructor-deployed **dependency** runtime/storage to fixed fixture
addresses, never the pool or RDL. It executes 21 adapter cases (all four full
routes at 500/300/100 bps, price-limited partial fills, and rejected unsafe inputs)
plus real NPM mint. Unit mocks provide separate adversarial-callback/reentry
coverage and are not counted as official-pool cases. See the fixture README
for compiler inputs, hashes, license and rebuild instructions.

`scripts/deploy_swap_adapter.py` is **read-only by default**, requires chain97,
and checks current token views, pool CREATE2 identity, factory registry,
fee/tokens and code before any signer/key boundary. Supplying `--deploy` is an
explicit public transaction path, not used during this revision:

```sh
TOKEN=0xYOUR_REVISED_TOKEN .venv/bin/python scripts/deploy_swap_adapter.py
```

Remaining limits: a wrongly preinitialized price can still stall launch (no
recovery policy added); postlaunch LP increase still has zero amount minima
(no oracle/TWAP policy); other fee-tier pools retain the existing untaxed
behavior. Full launch certification for all accepted extreme scales/token
orders, NFT metadata, public-chain/front-end integration and an external
security audit are outside the demonstrated local coverage. No merge or
public deployment is implied by passing tests.
