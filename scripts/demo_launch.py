"""Testnet demo: after the sale ends, finalize, settle everyone and launch."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from chain import Signer, call, rpc

d = json.load(open("deployments/bsc-testnet.json"))
me = Signer(open(os.environ["DEPLOYER_KEY_FILE"]).read().strip())
end = int(call(d["ieo"], "endTime()"), 16)
while int(rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16) < end + 5:
    time.sleep(10)
out = {}
out["finalize"] = me.tx(d["ieo"], "finalize()", gas=200_000)["transactionHash"]
print("state", int(call(d["ieo"], "state()"), 16))
while int(call(d["ieo"], "settledCount()"), 16) < int(call(d["ieo"], "participants()"), 16):
    me.tx(d["ieo"], "settle(uint256)", 25, gas=6_000_000)
print("state", int(call(d["ieo"], "state()"), 16), "raised", int(call(d["ieo"], "raised()"), 16))
out["launch"] = me.tx(d["ieo"], "launch()", gas=7_000_000)["transactionHash"]
FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
d["pool"] = "0x" + call(FACTORY, "getPool(address,address,uint24)", d["token"], d["wbnb"], 2500)[-40:]
d["lpTokenId"] = int(call(d["locker"], "tokenId()"), 16)
d["txs"] = out
json.dump(d, open("deployments/bsc-testnet.json", "w"), indent=2)
print(json.dumps(out), d.get("pool"))
