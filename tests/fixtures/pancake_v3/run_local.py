#!/usr/bin/env python3
"""Deterministic source-deployed Pancake V3 fixture, strictly no fork RPC.

Dependencies are first constructor-deployed locally, then their exact unmodified
runtime and constructor-written storage are installed at canonical fixture
addresses. The real pool is CREATE2-deployed by the official deployer through the
official factory: its source-built init hash matches the Fe token exactly.
No RDL/pool/WETH balance storage is written; WETH deposits and NPM mint are txs.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import socket
import hashlib
import ipaddress
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from eth_abi import decode, encode
from eth_utils import keccak, to_checksum_address

ROOT = Path(__file__).resolve().parent
NPM = to_checksum_address('0x427bF5b37357632377eCbEC9de3626C71A5396c1')
ROUTER = to_checksum_address('0x1b81D678ffb9C0263b24A97847620C99d213eB14')
WBNB = to_checksum_address('0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd')
FACTORY = to_checksum_address('0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865')
DEPLOYER = to_checksum_address('0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9')
INIT_HASH = bytes.fromhex('6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2')
E18 = 10**18
MAX = 2**256 - 1
FEE = 2500
LP_RDL = 80_000_000 * E18
LP_WBNB = 360 * E18
MINT_SIG = 'mint((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256))'
MINT_TUPLE = '(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,address,uint256)'
SWAP_TUPLE = '(address,address,uint24,address,uint256,uint256,uint256,uint160)'
INPUT_SIG = 'exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))'
OUTPUT_SIG = 'exactOutputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))'


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def hx(value):
    return '0x' + value.hex()


def calldata(sig, types=(), values=()):
    return hx(keccak(text=sig)[:4] + encode(list(types), list(values)))


def minimum_gross_for_net(net, bps):
    if not 0 <= bps < 10000 or net < 0:
        raise ValueError('Invalid net amount or tax rate')
    return 0 if net == 0 else (net - 1) * 10000 // (10000 - bps) + 1


def verify_artifacts(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / 'artifacts/manifest.json').read_text())
    # Check deployable artifacts first so corruption is caught even in partial copies.
    for relative, entry in manifest['files'].items():
        if not relative.startswith('artifacts/'):
            continue
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('SHA256 mismatch: ' + relative)
    for relative, entry in manifest['files'].items():
        if relative.startswith('artifacts/'):
            continue
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('SHA256 mismatch: ' + relative)
    return manifest


def pool_address(token, fee=FEE):
    t0, t1 = sorted([token, WBNB], key=lambda a: int(a, 16))
    salt = keccak(encode(['address', 'address', 'uint24'], [t0, t1, fee]))
    return to_checksum_address(hx(keccak(b'\xff' + bytes.fromhex(DEPLOYER[2:]) + salt + INIT_HASH)[12:]))


class RpcError(RuntimeError):
    def __init__(self, error):
        self.error = error
        super().__init__(json.dumps(error, sort_keys=True))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('REFUSED RPC redirect')


class Rpc:
    def __init__(self, url, transcript=None):
        parsed = urllib.parse.urlsplit(url)
        try:
            local = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            local = False
        if (parsed.scheme != 'http' or not local or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not parsed.port):
            raise ValueError('REFUSED: numeric loopback-only plain HTTP RPC URL required')
        self.url = url
        self.seq = 0
        self.verified = False
        self.transcript = transcript
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, params):
        if method.startswith(('eth_send', 'anvil_', 'evm_')) and method != 'anvil_metadata' and not self.verified:
            raise ValueError('REFUSED: Anvil / chain 31337 / no-fork verification required before writing')
        self.seq += 1
        request = {'jsonrpc': '2.0', 'id': self.seq, 'method': method, 'params': params}
        req = urllib.request.Request(self.url, json.dumps(request).encode(), {'Content-Type': 'application/json'})
        with self.opener.open(req, timeout=60) as response:
            result = json.load(response)
        if self.transcript:
            with Path(self.transcript).open('a') as log:
                log.write(json.dumps({'request': request, 'response': result}, sort_keys=True) + '\n')
        if 'error' in result:
            raise RpcError(result['error'])
        return result['result']

    def verify(self):
        if int(self.request('eth_chainId', []), 16) != 31337:
            raise ValueError('REFUSED: wrong chain ID')
        meta = self.request('anvil_metadata', [])
        if not meta['clientVersion'].startswith('anvil/') or meta.get('forkedNetwork'):
            raise ValueError('REFUSED: non-Anvil or forked state')
        self.verified = True
        return meta

    def call(self, sender, to, data):
        return self.request('eth_call', [{'from': sender, 'to': to, 'data': data}, 'latest'])

    def send(self, sender, to=None, data='0x', value=0, gas=10_000_000):
        tx = {'from': sender, 'data': data, 'value': hex(value), 'gas': hex(gas)}
        if to:
            tx['to'] = to
        stamp = int(self.request('eth_getBlockByNumber', ['latest', False])['timestamp'], 16)
        self.request('evm_setNextBlockTimestamp', [stamp + 1])
        txhash = self.request('eth_sendTransaction', [tx])
        for _ in range(200):
            receipt = self.request('eth_getTransactionReceipt', [txhash])
            if receipt is not None:
                return receipt
            time.sleep(0.05)
        raise RuntimeError('Transaction receipt polling timed out')


class Fixture:
    """Reusable hooks for real-pool adapter integration; fixture-only dependency relocation."""
    def __init__(self, rpc, out, riddle_bin=None):
        self.rpc = rpc
        self.out = Path(out)
        self.riddle_bin = Path(riddle_bin or ROOT / 'artifacts/Riddle.bin')
        self.lp, self.seller, self.buyer, self.dist = map(to_checksum_address, rpc.request('eth_accounts', [])[:4])
        self.results = []
        self.receipts = []
        self.dependencies = {}
        self.token = None
        self.pool = None

    def timestamp(self):
        return int(self.rpc.request('eth_getBlockByNumber', ['latest', False])['timestamp'], 16)

    def uint(self, address, sig, types=(), values=()):
        return int(self.rpc.call(self.lp, address, calldata(sig, types, values)), 16)

    def address(self, address, sig, types=(), values=()):
        return to_checksum_address('0x' + self.rpc.call(self.lp, address, calldata(sig, types, values))[-40:])

    def balance(self, token, who):
        return self.uint(token, 'balanceOf(address)', ['address'], [who])

    def ok(self, sender, to=None, data='0x', value=0, gas=10_000_000):
        receipt = self.rpc.send(sender, to, data, value, gas)
        if int(receipt['status'], 16) != 1:
            save(self.out / 'unexpected-revert.json', {'receipt': receipt, 'trace': self.rpc.request('debug_traceTransaction', [receipt['transactionHash'], {'tracer': 'callTracer'}])})
            raise AssertionError('Unexpected transaction revert: ' + receipt['transactionHash'])
        self.receipts.append(receipt)
        save(self.out / 'setup-receipts-progress.json', self.receipts)
        return receipt

    def approve(self, owner, token, spender):
        return self.ok(owner, token, calldata('approve(address,uint256)', ['address', 'uint256'], [spender, MAX]))

    def record(self, name, fields):
        row = {'case': name, **fields}
        self.results.append(row)
        save(self.out / (name + '.json'), row)
        save(self.out / 'results-progress.json', self.results)
        return row

    def install_dependency(self, name, target, constructor_types=(), constructor_values=()):
        # This is allowed dependency-fixture relocation, never a pool or RDL balance cheat.
        creation = bytes.fromhex((ROOT / 'artifacts' / (name + '.bin')).read_text().strip().removeprefix('0x'))
        receipt = self.ok(self.lp, data=hx(creation + encode(list(constructor_types), list(constructor_values))))
        deployed = to_checksum_address(receipt['contractAddress'])
        runtime = self.rpc.request('eth_getCode', [deployed, 'latest'])
        assert runtime != '0x'
        trace = self.rpc.request('debug_traceTransaction', [receipt['transactionHash'], {'disableMemory': True, 'disableStorage': True}])
        assert trace['structLogs'], 'Anvil must start with --steps-tracing to prove constructor storage writes'
        writes = {}
        for step in trace['structLogs']:
            if step['op'] == 'SSTORE':
                assert step['depth'] == 1, 'Dependency constructor unexpectedly wrote external state'
                key, value = step['stack'][-1], step['stack'][-2]
                writes['0x' + key.removeprefix('0x').zfill(64)] = '0x' + value.removeprefix('0x').zfill(64)
        assert self.rpc.request('eth_getCode', [target, 'latest']) == '0x'
        self.rpc.request('anvil_setCode', [target, runtime])
        for key, value in sorted(writes.items()):
            observed = self.rpc.request('eth_getStorageAt', [deployed, key, 'latest'])
            assert int(observed, 16) == int(value, 16)
            self.rpc.request('anvil_setStorageAt', [target, key, value])
            assert self.rpc.request('eth_getStorageAt', [target, key, 'latest']) == observed
        assert self.rpc.request('eth_getCode', [target, 'latest']) == runtime
        raw = bytes.fromhex(runtime[2:])
        (self.out / (name + '.runtime.hex')).write_text(runtime + '\n')
        entry = {'fixture_address': target, 'constructor_deployment_address': deployed, 'constructor_receipt': receipt,
                 'runtime_sha256': hashlib.sha256(raw).hexdigest(), 'runtime_keccak256': hx(keccak(raw)),
                 'creation_sha256': hashlib.sha256(creation).hexdigest(), 'constructor_storage_writes': writes,
                 'relocation': 'exact runtime and constructor-written storage only; dependency fixture, not public deployment',
                 'constructor_types': list(constructor_types), 'constructor_values': list(constructor_values)}
        self.dependencies[name] = entry
        save(self.out / 'dependency-fixture-installations.json', self.dependencies)
        return target

    def setup(self):
        import math
        metadata = self.rpc.verify()
        pool_init = bytes.fromhex((ROOT / 'artifacts/PancakeV3Pool.bin').read_text().strip())
        assert keccak(pool_init) == INIT_HASH, 'Pinned official pool bytecode init hash mismatch'
        self.install_dependency('PancakeV3PoolDeployer', DEPLOYER)
        self.install_dependency('PancakeV3Factory', FACTORY, ['address'], [DEPLOYER])
        self.ok(self.lp, DEPLOYER, calldata('setFactoryAddress(address)', ['address'], [FACTORY]))
        self.install_dependency('WETH9', WBNB)
        self.install_dependency('SwapRouter', ROUTER, ['address'] * 3, [DEPLOYER, FACTORY, WBNB])
        # Descriptor is deliberately not exercised; mint/positions/ownerOf are genuine NPM paths.
        self.install_dependency('NonfungiblePositionManager', NPM, ['address'] * 4, [DEPLOYER, FACTORY, WBNB, self.lp])
        assert self.address(FACTORY, 'poolDeployer()') == DEPLOYER
        assert self.address(DEPLOYER, 'factoryAddress()') == FACTORY
        assert self.uint(FACTORY, 'feeAmountTickSpacing(uint24)', ['uint24'], [FEE]) == 50
        for address in [NPM, ROUTER]:
            assert self.address(address, 'factory()') == FACTORY
            assert self.address(address, 'deployer()') == DEPLOYER
            assert self.address(address, 'WETH9()') == WBNB
        raw = bytes.fromhex(self.riddle_bin.read_text().strip().removeprefix('0x'))
        deployed = self.ok(self.lp, data=hx(raw + encode(['address'] * 5, [self.lp, self.seller, self.dist, WBNB, NPM])))
        self.token = to_checksum_address(deployed['contractAddress'])
        self.pool = pool_address(self.token)
        assert self.balance(self.token, self.lp) == 180_000_000 * E18
        assert self.balance(self.token, self.seller) == 820_000_000 * E18
        self.ok(self.lp, WBNB, calldata('deposit()'), LP_WBNB + 100 * E18)
        self.ok(self.buyer, WBNB, calldata('deposit()'), 20 * E18)
        for owner, token, spender in [(self.lp, self.token, NPM), (self.lp, WBNB, NPM), (self.seller, self.token, ROUTER), (self.buyer, WBNB, ROUTER)]:
            self.approve(owner, token, spender)
        t0, t1 = sorted([self.token, WBNB], key=lambda a: int(a, 16))
        a0, a1 = (LP_RDL, LP_WBNB) if t0 == self.token else (LP_WBNB, LP_RDL)
        sqrt_price = math.isqrt((a1 << 192) // a0)
        create = self.ok(self.lp, NPM, calldata('createAndInitializePoolIfNecessary(address,address,uint24,uint160)', ['address', 'address', 'uint24', 'uint160'], [t0, t1, FEE, sqrt_price]))
        assert self.address(FACTORY, 'getPool(address,address,uint24)', ['address', 'address', 'uint24'], [self.token, WBNB, FEE]) == self.pool
        assert self.address(self.pool, 'factory()') == FACTORY
        assert self.address(self.pool, 'token0()') == t0 and self.address(self.pool, 'token1()') == t1
        assert self.uint(self.pool, 'fee()') == FEE
        pool_runtime = self.rpc.request('eth_getCode', [self.pool, 'latest'])
        (self.out / 'PancakeV3Pool.runtime.hex').write_text(pool_runtime + '\n')
        data = calldata(MINT_SIG, [MINT_TUPLE], [(t0, t1, FEE, -887250, 887250, a0, a1, 0, 0, self.lp, self.timestamp() + 86400)])
        minted = decode(['uint256', 'uint128', 'uint256', 'uint256'], bytes.fromhex(self.rpc.call(self.lp, NPM, data)[2:]))
        receipt = self.ok(self.lp, NPM, data)
        self.token0, self.token1 = t0, t1
        self.mint_timestamp = int(self.rpc.request('eth_getBlockByNumber', [receipt['blockNumber'], False])['timestamp'], 16)
        rdl_used = minted[2] if t0 == self.token else minted[3]
        assert self.balance(self.token, self.pool) == rdl_used
        assert self.balance(self.token, self.dist) == 0
        assert self.uint(self.pool, 'liquidity()') == minted[1] > 0
        assert self.address(NPM, 'ownerOf(uint256)', ['uint256'], [minted[0]]) == self.lp
        mint_trace = self.rpc.request('debug_traceTransaction', [receipt['transactionHash'], {'tracer': 'callTracer'}])
        save(self.out / 'npm-mint-trace.json', mint_trace)
        save(self.out / 'npm-mint-receipt.json', receipt)
        save(self.out / 'pool-create-receipt.json', create)
        raw_sha = hashlib.sha256(raw).hexdigest()
        pinned_sha = hashlib.sha256(bytes.fromhex((ROOT / 'artifacts/Riddle.bin').read_text().strip())).hexdigest()
        candidate_info = getattr(self, 'candidate_source', None)
        save(self.out / 'provenance.json', {'rdl_revision': '5e5719ee389318d06493870c42670a509692a2ea' if raw_sha == pinned_sha else 'candidate worktree bytecode; source hashes recorded separately',
             'rdl_creation_sha256': raw_sha, 'rdl_bytecode_input_path': str(self.riddle_bin),
             'candidate_source': candidate_info,
             'rdl_lib_source_sha256': candidate_info['source_sha256']['src/lib.fe'] if candidate_info else hashlib.sha256((ROOT / 'sources/rdl-current/src/lib.fe').read_bytes()).hexdigest(),
             'official_revision': '986847948755cba528324d41be19480731c36c2a', 'local_metadata': metadata,
             'pool_creation_keccak256': hx(keccak(pool_init)), 'pool_creation_hash_matches_token': True,
             'pool_runtime_sha256': hashlib.sha256(bytes.fromhex(pool_runtime[2:])).hexdigest(), 'pool_relocated': False,
             'pool_deployment': 'official NPM -> official factory -> official deployer CREATE2; no pool storage writes',
             'token': self.token, 'pool': self.pool, 'token0': t0, 'token1': t1, 'dependencies': self.dependencies,
             'fixture_roles': {'ieo': self.lp, 'mining': self.seller, 'distributor': self.dist},
             'limitations': ['dependency runtime/constructor-storage relocation declared', 'WETH9 creation from pinned upstream JSON fixture; not recompiled', 'NPM descriptor not exercised', 'direct-token constructor fixture, not IEO launch E2E'],
             'fork_used': False, 'external_rpc_used': False, 'token_storage_cheats': False})
        self.record('real_npm_mint_control', {'status': 'PASS', 'receipt_status': 1, 'token': self.token, 'pool': self.pool,
                    'token_id': minted[0], 'liquidity': minted[1], 'rdl_received_by_pool': rdl_used,
                    'distributor_tax': 0, 'pool_created_by_genuine_create2': True, 'tx': receipt['transactionHash']})
        return self

    def branch(self, fn):
        snapshot = self.rpc.request('evm_snapshot', [])
        try:
            return fn()
        finally:
            assert self.rpc.request('evm_revert', [snapshot])

    def swap_data(self, token_in, token_out, amount, limit=0, exact_output=False, recipient=None):
        args = (token_in, token_out, FEE, recipient or self.buyer, self.timestamp() + 86400, amount, limit, 0)
        return calldata(OUTPUT_SIG if exact_output else INPUT_SIG, [SWAP_TUPLE], [args])

    def standard_sell_iia(self, exact_output=False, bps=500, amount=None, name=None):
        name = name or ('standard_sell_exact_output_iia' if exact_output else 'standard_sell_exact_input_iia')
        amount = amount or (10**15 if exact_output else 1000 * E18)
        data = self.swap_data(self.token, WBNB, amount, MAX if exact_output else 0, exact_output)
        def state():
            return {'seller_rdl': self.balance(self.token, self.seller), 'pool_rdl': self.balance(self.token, self.pool),
                    'distributor_rdl': self.balance(self.token, self.dist), 'buyer_wbnb': self.balance(WBNB, self.buyer),
                    'pool_wbnb': self.balance(WBNB, self.pool), 'router_rdl': self.balance(self.token, ROUTER),
                    'slot0': self.rpc.call(self.lp, self.pool, calldata('slot0()'))}
        before = state()
        try:
            self.rpc.call(self.seller, ROUTER, data)
        except RpcError as exc:
            simulation_error = exc.error
            assert 'IIA' in str(exc), str(exc)
        else:
            raise AssertionError('Taxed standard-router sell unexpectedly succeeded')
        receipt = self.rpc.send(self.seller, ROUTER, data)
        assert int(receipt['status'], 16) == 0
        trace = self.rpc.request('debug_traceTransaction', [receipt['transactionHash'], {'tracer': 'callTracer'}])
        save(self.out / (name + '-receipt.json'), receipt)
        save(self.out / (name + '-trace.json'), trace)
        def walk(node):
            yield node
            for child in node.get('calls', []):
                yield from walk(child)
        nodes = list(walk(trace))
        reasons = []
        for node in nodes:
            reason = node.get('revertReason')
            output = node.get('output', '')
            if not reason and output.startswith('0x08c379a0'):
                reason = decode(['string'], bytes.fromhex(output[10:]))[0]
            reasons.append(reason)
        assert 'IIA' in reasons, reasons
        selector = calldata('transferFrom(address,address,uint256)')[:10]
        payments = [n for n in nodes if n.get('to', '').lower() == self.token.lower() and n.get('input', '').startswith(selector)]
        assert len(payments) == 1
        payment = payments[0]
        payer, recipient, debt = decode(['address', 'address', 'uint256'], bytes.fromhex(payment['input'][10:]))
        assert payer.lower() == self.seller.lower() and recipient.lower() == self.pool.lower()
        assert payment['from'].lower() == ROUTER.lower() and int(payment['output'], 16) == 1
        bal_selector = calldata('balanceOf(address)')[:10]
        balances = [int(n['output'], 16) for n in nodes if n.get('to', '').lower() == self.token.lower()
                    and n.get('input', '').startswith(bal_selector) and n['input'][-40:].lower() == self.pool[2:].lower()]
        assert len(balances) == 2
        shortfall = debt * bps // 10000
        net_credit = balances[1] - balances[0]
        assert shortfall > 0 and net_credit == debt - shortfall
        after = state()
        assert before == after, 'IIA must roll back pool, payer, distributor and output state'
        return self.record(name, {'status': 'EXPECTED_RED_CONFIRMED', 'receipt_status': 0, 'revert': 'IIA', 'bps': bps,
                                 'callback_debt_rdl': debt, 'net_credit_rdl': net_credit, 'tax_shortfall_rdl': shortfall,
                                 'pool_balance_before_callback': balances[0], 'pool_balance_after_callback_in_reverted_trace': balances[1],
                                 'payment_kind': 'transferFrom', 'payer': payer, 'router': ROUTER, 'pool': self.pool,
                                 'state_rolled_back': before == after, 'before': before, 'after': after,
                                 'simulation_error': simulation_error, 'tx': receipt['transactionHash']})

    def standard_buy(self, exact_output=False, amount=None, minimum_gross=True, name=None):
        name = name or ('standard_buy_exact_output_net_shortfall' if exact_output else 'standard_buy_exact_input_gross_min')
        amount = amount or (1000 * E18 if exact_output else E18)
        initial = self.swap_data(WBNB, self.token, amount, 20 * E18 if exact_output else 0, exact_output)
        quote = int(self.rpc.call(self.buyer, ROUTER, initial), 16)
        gross = amount if exact_output else quote
        limit = 20 * E18 if exact_output else (quote if minimum_gross else 0)
        data = self.swap_data(WBNB, self.token, amount, limit, exact_output)
        before = {'buyer_rdl': self.balance(self.token, self.buyer), 'distributor_rdl': self.balance(self.token, self.dist), 'buyer_wbnb': self.balance(WBNB, self.buyer)}
        receipt = self.ok(self.buyer, ROUTER, data)
        net = self.balance(self.token, self.buyer) - before['buyer_rdl']
        tax = self.balance(self.token, self.dist) - before['distributor_rdl']
        spent = before['buyer_wbnb'] - self.balance(WBNB, self.buyer)
        assert net + tax == gross and tax == gross * 500 // 10000
        assert spent == (quote if exact_output else amount)
        assert net < gross
        trace = self.rpc.request('debug_traceTransaction', [receipt['transactionHash'], {'tracer': 'callTracer'}])
        save(self.out / (name + '-receipt.json'), receipt)
        save(self.out / (name + '-trace.json'), trace)
        stamp = int(self.rpc.request('eth_getBlockByNumber', [receipt['blockNumber'], False])['timestamp'], 16)
        return self.record(name, {'status': 'NET_SHORTFALL_CONFIRMED', 'receipt_status': 1, 'router_return': quote,
                                 'gross_rdl_output': gross, 'buyer_rdl_delta': net, 'distributor_tax': tax, 'wbnb_spent': spent,
                                 'minimum_or_requested_rdl': gross, 'tx': receipt['transactionHash'], 'block_timestamp': stamp})

    def deploy_adapter(self, adapter_bin):
        """Parent adapter ABI: constructor(token, wbnb, actualPool)."""
        raw = bytes.fromhex(Path(adapter_bin).read_text().strip().removeprefix('0x'))
        receipt = self.ok(self.lp, data=hx(raw + encode(['address'] * 3, [self.token, WBNB, self.pool])))
        adapter = to_checksum_address(receipt['contractAddress'])
        self.approve(self.seller, self.token, adapter)
        self.approve(self.buyer, WBNB, adapter)
        return adapter, receipt

    def adapter_data(self, method, amount, limit=0, deadline=None, sqrt_price_limit_x96=0):
        """sell/buyExactInput/Output: amount, limit, deadline, sqrtPriceLimitX96."""
        assert method in {'sellExactInput', 'buyExactInput', 'sellExactOutput', 'buyExactOutput'}
        return calldata(method + '(uint256,uint256,uint256,uint160)', ['uint256'] * 3 + ['uint160'],
                        [amount, limit, self.timestamp() + 86400 if deadline is None else deadline, sqrt_price_limit_x96])


@contextmanager
def run_fixture(root=None, *, out=None, rpc_url=None, riddle_bin=None, fe_bin=None):
    """Yield a live owned local-real fixture; optional root builds parent token read-only.

    Usage: with run_fixture(parent_root, out=evidence_path) as f: ...
    The parent source is only read. Fe outputs and all receipts stay under out.
    Default (root=None) uses the included pinned baseline Riddle bytecode.
    """
    out = Path(out or ROOT / 'evidence/callable')
    out.mkdir(parents=True, exist_ok=True)
    manifest = verify_artifacts()
    candidate = None
    if root is not None and riddle_bin is None:
        root = Path(root).resolve()
        compiler = Path(fe_bin or os.environ.get('FE_BIN') or shutil.which('fe') or '/root/.hermes/cache/scratch/riddlemine-revision-evidence/fe-26.5.0')
        if not compiler.is_file() or hashlib.sha256(compiler.read_bytes()).hexdigest() != manifest['fe']['linux_amd64_sha256']:
            raise ValueError('FE_BIN must name the pinned Fe 26.5.0 Linux amd64 compiler')
        build_out = out / 'candidate-artifacts'
        command = [str(compiler), 'build', str(root), '--contract', 'Riddle', '--out-dir', str(build_out), '--emit', 'bytecode,runtime-bytecode,abi,metadata']
        build = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (out / 'candidate-build.log').write_text(build.stdout + build.stderr)
        candidate = {'source_root': str(root), 'build_command': command, 'build_exit_code': build.returncode,
                     'source_sha256': {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / 'src').rglob('*.fe'))}}
        save(out / 'candidate-source.json', candidate)
        if build.returncode:
            raise RuntimeError('Candidate Riddle build failed; see candidate-build.log')
        riddle_bin = build_out / 'Riddle.bin'
    if rpc_url is None:
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        rpc_url = 'http://127.0.0.1:' + str(port)
    transcript = out / 'rpc-transcript.jsonl'
    transcript.write_text('')
    for name in ['failure.json', 'summary.json', 'cleanup.json']:
        stale = out / name
        if stale.exists():
            stale.unlink()
    rpc = Rpc(rpc_url, transcript)
    parsed = urllib.parse.urlsplit(rpc_url)
    family = socket.AF_INET6 if ':' in parsed.hostname else socket.AF_INET
    with socket.socket(family) as reservation:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        reservation.bind((parsed.hostname, parsed.port))  # Refuse occupied RPC before any write.
    command = [shutil.which('anvil') or 'anvil', '--host', parsed.hostname, '--port', str(parsed.port),
               '--chain-id', '31337', '--accounts', '5', '--timestamp', '1800000000', '--steps-tracing', '--silent']
    save(out / 'commands.json', {'anvil': command, 'runner': sys.argv, 'network': 'loopback only; no fork URL'})
    process = None
    try:
        with (out / 'anvil.log').open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Owned Anvil exited during startup')
                try:
                    rpc.verify()
                    break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(0.1)
            else:
                raise RuntimeError('Local Anvil startup timeout')
            fixture = Fixture(rpc, out, riddle_bin)
            fixture.candidate_source = candidate
            fixture.artifact_manifest = manifest
            fixture.setup()
            yield fixture
    except Exception as exc:
        save(out / 'failure.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            save(out / 'cleanup.json', {'owned_anvil_stopped': process.poll() is not None, 'returncode': process.returncode, 'rpc': rpc_url})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc', default='http://127.0.0.1:18571')
    parser.add_argument('--out', type=Path, default=ROOT / 'evidence/run')
    parser.add_argument('--case', choices=['all', 'npm_mint', 'standard_sell'], default='all')
    parser.add_argument('--riddle-bin', type=Path)
    parser.add_argument('--source-root', type=Path, help='Read-only parent Riddle source build into evidence')
    parser.add_argument('--fe', help='Pinned Fe compiler for --source-root')
    args = parser.parse_args()
    with run_fixture(args.source_root, out=args.out, rpc_url=args.rpc, riddle_bin=args.riddle_bin, fe_bin=args.fe) as fixture:
        if args.case in {'all', 'standard_sell'}:
            fixture.branch(fixture.standard_sell_iia)
        expected = {'real_npm_mint_control'}
        if args.case != 'npm_mint':
            expected.add('standard_sell_exact_input_iia')
        if args.case == 'all':
            fixture.branch(lambda: fixture.standard_sell_iia(exact_output=True))
            fixture.branch(fixture.standard_buy)
            fixture.branch(lambda: fixture.standard_buy(exact_output=True))
            expected.update({'standard_sell_exact_output_iia', 'standard_buy_exact_input_gross_min', 'standard_buy_exact_output_net_shortfall'})
        actual = {r['case'] for r in fixture.results}
        assert actual == expected and len(actual) == len(fixture.results)
        summary = {'status': 'PASS', 'case_count': len(fixture.results), 'fork_used': False, 'external_rpc_used': False, 'results': fixture.results}
        save(args.out / 'summary.json', summary)
        print('PASS cases=' + str(summary['case_count']) + ' no-fork pool=' + fixture.pool)


if __name__ == '__main__':
    main()
