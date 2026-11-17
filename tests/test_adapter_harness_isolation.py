"""Transport isolation controls for the disposable adapter unit harness."""
import importlib.util
from pathlib import Path
import socket
import unittest

MODULE_PATH = Path(__file__).resolve().parent / "test_swap_adapter_unit_evm.py"


class AdapterHarnessIsolationTests(unittest.TestCase):
    def test_reservation_uses_an_available_ephemeral_loopback_port(self):
        spec = importlib.util.spec_from_file_location("adapter_unit_transport", MODULE_PATH)
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        reserve = getattr(harness, "reserve_loopback_port", None)
        self.assertTrue(callable(reserve), "unit harness needs an ephemeral loopback port, not fixed18561")
        port = reserve()
        self.assertGreater(port, 0)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen(1)
            other_port = reserve()
            self.assertNotEqual(port, other_port, "reservation selected an occupied endpoint")
            with socket.socket() as independent:
                independent.bind(("127.0.0.1", other_port))


if __name__ == "__main__":
    unittest.main()
