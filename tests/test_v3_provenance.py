"""Packaging controls for the pinned official V3 creation-code fixture."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

from eth_utils import keccak

FIXTURE = Path(__file__).resolve().parent / "fixtures/pancake_v3"


class V3ProvenanceTests(unittest.TestCase):
    def test_compiler_inputs_include_all_corresponding_sources(self):
        manifest = json.loads((FIXTURE / "artifacts/manifest.json").read_text())
        for name in ("PancakeV3PoolDeployer", "PancakeV3Factory", "SwapRouter", "NonfungiblePositionManager"):
            path = FIXTURE / "artifacts" / (name + ".input.json")
            compiler_input = json.loads(path.read_text())
            self.assertEqual(compiler_input["language"], "Solidity")
            self.assertGreater(len(compiler_input["sources"]), 1)
            self.assertEqual(compiler_input["settings"]["metadata"]["bytecodeHash"], "none")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest["files"][str(path.relative_to(FIXTURE))]["sha256"])
            for key, value in compiler_input["sources"].items():
                self.assertIn("content", value, key)
                self.assertNotIn("urls", value, "rebuild would depend on unavailable external source")

    def test_rejects_nonloopback_and_unverified_write_endpoints(self):
        spec = importlib.util.spec_from_file_location("fixture_safety_check", FIXTURE / "run_local.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        for url in ("https://127.0.0.1:8545", "http://203.0.113.1:8545", "http://localhost:8545", "http://user:pass@127.0.0.1:8545", "http://127.0.0.1:8545?redirect=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fixture.Rpc(url)
        rpc = fixture.Rpc("http://127.0.0.1:8545")
        with self.assertRaisesRegex(ValueError, "verification required"):
            rpc.request("eth_sendTransaction", [])
        checks = 0
        for bps in (100, 300, 500):
            for net in list(range(1001)) + [10**18, 1000 * 10**18, 10**30]:
                gross = fixture.minimum_gross_for_net(net, bps)
                self.assertEqual(gross - gross * bps // 10000, net)
                if gross:
                    self.assertLess((gross - 1) - (gross - 1) * bps // 10000, net)
                checks += 1
        self.assertEqual(checks, 3012)

    def test_portable_fixture_has_verified_official_creation_code(self):
        runner = FIXTURE / "run_local.py"
        self.assertTrue(runner.is_file(), "portable V3 fixture runner is missing")
        spec = importlib.util.spec_from_file_location("fixture_provenance_check", runner)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        manifest = fixture.verify_artifacts()
        self.assertEqual(manifest["solidity_revision"], "986847948755cba528324d41be19480731c36c2a")
        self.assertEqual(manifest["pool_init_hash"], "0x6ce8eb472fa82df5469c6ab6d485f17c3ad13c8cd7af59b3d4a8026c5ce0f7e2")
        raw = bytes.fromhex((FIXTURE / "artifacts/PancakeV3Pool.bin").read_text().strip())
        self.assertEqual("0x" + keccak(raw).hex(), manifest["pool_init_hash"])
        self.assertGreater(len(raw), 20000)


if __name__ == "__main__":
    unittest.main()
