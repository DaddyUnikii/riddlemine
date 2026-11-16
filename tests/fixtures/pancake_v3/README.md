# Pinned local-real Pancake V3 fixture

This directory contains **test-only** unmodified creation bytecode, compiler
standard-JSON inputs (including corresponding Solidity source), and a loopback
fixture runner. It is not a public deployment, router upgrade, or security
certificate. Source-built pool init hash matches RDL's fixed 0.25% pool hash.

- Pancake V3 source commit: `986847948755cba528324d41be19480731c36c2a`.
- OpenZeppelin dependency commit: `04695aecbd4d17dddfd55de766d10e3805d6f42f`.
- Solidity: `0.7.6`, Istanbul; optimizer 400 for pool/deployer, 1,000,000 for
  factory/router, 2,000 for NPM; metadata bytecode hash `none`.
- `artifacts/manifest.json` records measured SHA-256 hashes of bundled files.
  `artifacts/solidity-provenance.json` links compiler inputs to upstream sources.
- `sources/rdl-current/src/lib.fe` and `artifacts/Riddle.bin` are the **historical
  baseline 5e5719e**, retained solely for differential tests. Tests of the
  revised token must pass the current repository root to `run_fixture` or
  `--source-root`; the runner records its actual source hashes separately.

The runner constructor-deploys official dependencies on Anvil chain **31337**,
then copies their exact runtime and actual constructor-written storage to fixed
dependency fixture addresses. This address relocation is explicit. The pool is
really created via NPM → Factory → PoolDeployer → CREATE2, with no pool relocation
or pool/token balance-storage edits. WBNB uses upstream WETH9 creation bytecode;
wrapped balances come from real native deposits. NPM NFT metadata is not tested.

All write endpoints require numeric loopback HTTP, Anvil metadata, chain31337
and no fork. Processes owned by `run_fixture` stop in `finally`. No public RPC,
private-key signing, public deployment, or real funds are used by these tests.

From repository root after installing `requirements.txt` and Fe/Anvil:

```sh
python tests/fixtures/pancake_v3/run_local.py --case all \
  --source-root . --fe "$(command -v fe)" \
  --rpc http://127.0.0.1:18578 --out /path/to/local-evidence
```

Five standard-route observations are expected: genuine untaxed NPM mint, two
standard taxed sell routes reverting `IIA`, and two successful standard buy
routes whose gross output exceeds the recipient's net amount. Those expected
failures diagnose the existing router limitation; they are not adapter results.

Pancake Solidity sources and bytecode remain under their upstream SPDX licenses,
including GPL-2.0-or-later for the core/router/NPM contracts; the upstream GPLv2
license is retained under `sources/pancake-v3/projects/v3-periphery/LICENSE`.
They are test dependencies, not relicensed as Apache-2.0. See SPDX headers in the
included compiler source inputs for individual dependency licenses.

To recompile and compare all five official creation artifacts using the bundled
complete compiler inputs (no RPC):

```sh
npm ci --prefix tests/fixtures/pancake_v3/toolchain --ignore-scripts --no-audit --no-fund
node tests/fixtures/pancake_v3/rebuild.cjs
```

This checks the compiler hash and exact resulting bytes, without overwriting a
pinned artifact or fetching any Solidity source. The upstream `build_pancake.cjs`
is retained for provenance only; its original full source-tree layout is not
vendored. Use `rebuild.cjs` for this repository's compact fixture.
