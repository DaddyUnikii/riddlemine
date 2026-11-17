"""Packaging and CI safety rails: hash-pinned deps, no-skip gate, provenance job."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PackagingPinsTests(unittest.TestCase):
    def test_ci_installs_hash_pinned_requirements(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("requirements.lock", workflow, "CI must install the hash-pinned lock")
        self.assertIn("--require-hashes", workflow)
        self.assertNotIn("pip install -r requirements.txt", workflow)

    def test_lock_pins_every_requirement_with_hashes(self):
        lock = ROOT / "requirements.lock"
        self.assertTrue(lock.is_file(), "missing hash-pinned requirements.lock")
        # Pip uses trailing backslashes to continue multi-artifact hash lists.
        lines = [line.rstrip().removesuffix(" \\") for line in lock.read_text().splitlines() if line.strip() and not line.startswith("#")]
        requirements = [line for line in lines if not line.startswith((" ", "--", "\t"))]
        self.assertTrue(requirements, "lock file lists no requirements")
        for requirement in requirements:
            self.assertRegex(requirement, r"^[A-Za-z0-9_.\-]+==[0-9][^;]*$", "unpinned requirement: " + requirement)
        hashes = [line.strip() for line in lines if line.strip().startswith("--hash=sha256:")]
        self.assertGreaterEqual(len(hashes), len(requirements), "every requirement needs at least one hash")
        for value in hashes:
            self.assertRegex(value, r"^--hash=sha256:[0-9a-f]{64}$")
        self.assertEqual(len(set(hashes)), len(hashes), "duplicate hash lines")

    def test_lock_covers_every_direct_requirement(self):
        direct = []
        for line in (ROOT / "requirements.txt").read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                direct.append(line)
        lock = (ROOT / "requirements.lock").read_text().lower()
        for requirement in direct:
            name = re.split(r"[=<>!~\[]", requirement, maxsplit=1)[0].strip().lower()
            self.assertIn(name + "==", lock, "lock is missing " + name)

    def test_ci_fails_on_skipped_tests(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        # The gate must be a shell guard, not just an env var: a skipped class
        # has to make the job exit nonzero.
        self.assertIn("skipped", workflow)
        self.assertRegex(workflow, r"grep -Eq", "CI must grep the captured log for skips")
        self.assertRegex(workflow, r"skipped[^\n]*\n[^\n]*echo[^\n]*error", "a detected skip must fail the job")
        self.assertIn("exit 1", workflow)

    def test_ci_verifies_official_bytecode_provenance(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        self.assertIn("rebuild.cjs", workflow)
        self.assertIn("npm ci", workflow)


class LockFixtureIntegrityTests(unittest.TestCase):
    def test_lock_is_plain_ascii_pip_requirements(self):
        raw = (ROOT / "requirements.lock").read_bytes()
        raw.decode("ascii")
        self.assertTrue(raw.endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
