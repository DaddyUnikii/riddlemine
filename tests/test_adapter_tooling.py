"""Adapter tooling contract: no fixed RPC port, deterministic post-deploy binding."""
import importlib.util
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "run_swap_adapter_official.py"
FIXTURE = ROOT / "tests/fixtures/pancake_v3/run_local.py"


class AdapterToolingTests(unittest.TestCase):
    def test_official_runner_does_not_pin_a_fixed_rpc_port(self):
        source = RUNNER.read_text()
        self.assertNotIn("18561", source, "the official runner must not pin a fixed RPC port")
        self.assertNotIn("127.0.0.1:", source, "let the fixture reserve its own ephemeral loopback port")
        self.assertIsNone(re.search(r"rpc_url\s*=", source), "the runner should not pass an explicit rpc_url")

    def test_fixture_reserves_an_ephemeral_port_when_none_is_supplied(self):
        source = FIXTURE.read_text()
        self.assertIn("reservation.bind(('127.0.0.1', 0))", source)
        self.assertIn("if rpc_url is None:", source)
        # An occupied explicit endpoint must be refused before any write.
        self.assertIn("Refuse occupied RPC before any write", source)

    def test_fixture_rejects_an_occupied_endpoint(self):
        spec = importlib.util.spec_from_file_location("pancake_local_fixture_occupied", FIXTURE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="rdl-occupied-rpc-") as temporary, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            busy = listener.getsockname()[1]
            with patch.object(module.subprocess, "Popen") as spawn, patch.object(module.Rpc, "request") as request:
                with self.assertRaises((OSError, ValueError)):
                    with module.run_fixture(out=Path(temporary), rpc_url="http://127.0.0.1:%d" % busy):
                        self.fail("occupied loopback endpoint was accepted")
                spawn.assert_not_called()
                request.assert_not_called()

    def test_deploy_script_verifies_binding_at_the_receipt_block(self):
        source = (ROOT / "scripts" / "deploy_swap_adapter.py").read_text()
        self.assertNotIn('"latest"', source, "post-deploy verification must not read latest")
        self.assertIn("confirmed_block", source)
        self.assertRegex(source, r"read_address\(adapter, key \+ \"\(\)\", confirmed_block\)")


if __name__ == "__main__":
    unittest.main()
