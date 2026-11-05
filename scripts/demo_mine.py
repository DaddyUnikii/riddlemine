"""Testnet demo: one IEO bidder buys RDL on PancakeSwap v3, stakes the gate,
redeems a tweet voucher signed by the bot, then solves and submits rounds.

Env: DEPLOYER_KEY_FILE, BIDDERS_FILE, BOT_KEY_FILE, ROUNDS (default 3)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from chain import Signer, call, rpc, receipt, keccak, selector, word
from solve import seed_of, solve
from eth_account import Account

ROUTER = "0x1b81D678ffb9C0263b24A97847620C99d213eB14"
d = json.load(open("deployments/bsc-testnet.json"))
me = Signer(open(os.environ["DEPLOYER_KEY_FILE"]).read().strip())
miner = Signer(json.load(open(os.environ["BIDDERS_FILE"]))[0])
bot = Account.from_key(open(os.environ["BOT_KEY_FILE"]).read().strip())
M, T = d["mining"], d["token"]
u = lambda x: int(x, 16)
log = {"miner": miner.address}

if u(rpc("eth_getBalance", [miner.address, "latest"])) < 2_000_000_000_000_000:
    me.send(miner.address, value=2_000_000_000_000_000, gas=21_000)

# Buy RDL from the launched pool (pays the 5% launch tax).
need = u(call(M, "requiredStake()"))
if u(call(T, "balanceOf(address)", miner.address)) < need:
    amount_in = 20_000_000_000_000  # 0.00002 tBNB
    data = bytes.fromhex("414bf389") + b"".join(word(a) for a in [
        d["wbnb"], T, 2500, miner.address, int(time.time()) + 600, amount_in, 0, 0])
    log["buy"] = miner.send(ROUTER, data, value=amount_in, gas=400_000)["transactionHash"]
log["rdl_bought"] = u(call(T, "balanceOf(address)", miner.address)) / 1e18

if u(call(M, "stakeOf(address)", miner.address)) < need:
    miner.tx(T, "approve(address,uint256)", M, need, gas=100_000)
    log["stake"] = miner.tx(M, "stake(uint256)", need, gas=300_000)["transactionHash"]

day = u(call(M, "currentDay()"))
if not u(call(M, "hasVoucher(address)", miner.address)):
    digest = keccak(b"".join(word(x) for x in [0x726464762D766F7563686572, 97, int(miner.address, 16), day]))
    sig = Account._sign_hash(digest, bot.key) if hasattr(Account, "_sign_hash") else bot.unsafe_sign_hash(digest)
    log["voucher"] = miner.tx(M, "redeemVoucher(uint256,uint8,uint256,uint256)", day, sig.v, sig.r, sig.s, gas=200_000)["transactionHash"]
log["boost_bps"] = u(call(M, "boostOf(address)", miner.address))

log["wins"] = []
done = set()
while len(log["wins"]) < int(os.environ.get("ROUNDS", 3)):
    rnd = u(call(M, "currentRound()"))
    if rnd in done:
        time.sleep(5)
        continue
    mask = solve(seed_of(97, rnd))[0]
    r = miner.tx(M, "submit(uint256)", mask, gas=400_000)
    done.add(rnd)
    log["wins"].append({"round": rnd, "mask": hex(mask), "tx": r["transactionHash"]})
    print("won round", rnd, flush=True)
log["vesting"] = u(call(M, "vesting(address)", miner.address)) / 1e18
log["claimable"] = u(call(M, "claimable(address)", miner.address)) / 1e18
log["roundReward"] = u(call(M, "roundReward()")) / 1e18
d["miningDemo"] = log
json.dump(d, open("deployments/bsc-testnet.json", "w"), indent=2)
print(json.dumps(log, indent=2))
