"""Deploy Riddlemine to BSC testnet in README order and bind everything.

Usage: DEPLOYER_KEY_FILE=... BOT=0x... python3 scripts/deploy_testnet.py
Writes deployments/bsc-testnet.json.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from chain import Signer

WBNB = "0xae13d989daC2f0dEbFf460aC112a837C89BAa7cd"
NPM = "0x427bF5b37357632377eCbEC9de3626C71A5396c1"
SCALE = 10_000
DAY = 86_400
IEO_SECS = int(os.environ.get("IEO_SECS", 1_800))

me = Signer(open(os.environ["DEPLOYER_KEY_FILE"]).read().strip())
bot = os.environ["BOT"]
out = {"chainId": 97, "scale": SCALE, "deployer": me.address, "bot": bot, "wbnb": WBNB, "npm": NPM}

# The Ieo needs Mining's address and Mining needs the Ieo's: predict Mining
# from the deployer nonce (Ieo is nonce n, Mining n + 1).
from eth_utils import keccak, to_checksum_address
import rlp
def at(n):
    return to_checksum_address(keccak(rlp.encode([bytes.fromhex(me.address[2:]), n]))[12:])

mining_at = at(me.nonce + 1)
out["ieo"] = me.deploy("out/Ieo.bin", 0, IEO_SECS, me.address, mining_at, DAY, SCALE)
out["mining"] = me.deploy("out/Mining.bin", out["ieo"], 60, 7 * DAY, 30 * DAY, bot)
assert out["mining"] == mining_at, "mining address mismatch"
out["distributor"] = me.deploy("out/Distributor.bin", out["mining"], me.address, WBNB, 3 * DAY)
out["token"] = me.deploy("out/Riddle.bin", out["ieo"], out["mining"], out["distributor"], WBNB, NPM)
out["locker"] = me.deploy("out/Locker.bin", out["ieo"], out["token"], WBNB, NPM, out["distributor"])
me.tx(out["ieo"], "bind(address,address)", out["token"], out["locker"])
me.tx(out["mining"], "bind(address)", out["token"])
me.tx(out["distributor"], "bind(address,address)", out["token"], out["locker"])
json.dump(out, open("deployments/bsc-testnet.json", "w"), indent=2)
print(json.dumps(out, indent=2))
