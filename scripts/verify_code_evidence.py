"""Re-check the recorded BSC testnet deployment against public RPC.

Reads deployments/bsc-testnet-v2-code-evidence.json and, for every contract,
re-fetches the on-chain code and confirms:

  * SHA256(eth_getCode) == the recorded on-chain hash
  * the recorded on-chain hash equals the recorded build runtime hash
  * keccak(rlp(deployer, nonce))[12:] == the recorded address

Read-only: no key is read, nothing is signed, nothing is broadcast.

    python scripts/verify_code_evidence.py
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

RPC = "https://bsc-testnet-rpc.publicnode.com"
EVIDENCE = Path(__file__).resolve().parent.parent / "deployments" / "bsc-testnet-v2-code-evidence.json"


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        RPC, body, {"content-type": "application/json", "user-agent": "curl/8"})
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(f"{method} failed: {payload['error']}")
    return payload["result"]


def main():
    from eth_utils import keccak, to_checksum_address
    import rlp

    evidence = json.loads(EVIDENCE.read_text())
    deployer = evidence["deployer"]
    block = rpc("eth_blockNumber", [])
    failures = []
    print(f"evidence block {evidence['observedBlock']}, current block {int(block, 16)}")
    for name, record in sorted(evidence["contracts"].items()):
        code = bytes.fromhex(rpc("eth_getCode", [record["address"], block]).removeprefix("0x"))
        onchain = hashlib.sha256(code).hexdigest()
        predicted = to_checksum_address(keccak(rlp.encode(
            [bytes.fromhex(deployer.removeprefix("0x")), record["nonce"]]))[12:])

        checks = {
            "code_hash": onchain == record["onchainCodeSha256"],
            "code_equals_build": onchain == record["runtimeSha256"],
            "code_bytes": len(code) == record["onchainCodeBytes"],
            "address_linkage": predicted.lower() == record["address"].lower(),
        }
        failed = [label for label, ok in checks.items() if not ok]
        failures.extend(f"{name}: {label}" for label in failed)
        status = "ok" if not failed else "FAIL"
        print(f"{status:4s} {name:12s} {record['address']} "
              f"{len(code)}B {' '.join(sorted(failed))}")

    if failures:
        print("\nFAILED:", len(failures))
        for item in failures:
            print("  -", item)
        return 1
    print(f"\nall {len(evidence['contracts'])} contracts re-verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
