"""Voucher CLI regressions use a generated throwaway signing key only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from coincurve import PrivateKey, PublicKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import voucher


class VoucherCliTests(unittest.TestCase):
    def test_cli_reads_bot_key_from_file_not_process_arguments(self):
        key = PrivateKey()
        with tempfile.TemporaryDirectory(prefix="riddlemine-voucher-") as directory:
            path = Path(directory) / "bot.key"
            path.write_text(key.secret.hex())
            path.chmod(0o600)
            env = dict(os.environ, BOT_KEY_FILE=str(path))
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "voucher.py"), "97", "0x" + "12" * 20, "5"],
                capture_output=True, text=True, env=env, timeout=10,
            )
        self.assertEqual(result.returncode, 0, "BOT_KEY_FILE-based CLI did not succeed")
        self.assertNotIn(key.secret.hex(), result.stdout + result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[0], "signer " + voucher.address_of(key.secret.hex()))
        day, v, r, s = lines[1].split()
        self.assertEqual(int(day), 5)
        signature = bytes.fromhex(r[2:] + s[2:]) + bytes([int(v) - 27])
        recovered = PublicKey.from_signature_and_message(
            signature, voucher.voucher_digest(97, int("12" * 20, 16), 5), hasher=None,
        )
        self.assertEqual(recovered.format(), key.public_key.format())

    def test_legacy_key_in_arguments_is_rejected_without_echoing_it(self):
        key = PrivateKey().secret.hex()
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "voucher.py"), key, "97", "0x" + "12" * 20, "5"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0, "private key argv is still accepted")
        self.assertNotIn(key, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
