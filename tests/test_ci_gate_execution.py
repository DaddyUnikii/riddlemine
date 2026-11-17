"""Execute the CI shell gate with real unittest results, not grep fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def gate_script():
    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: Python and local EVM regressions\n", 1)[1]
    body = step.split("          set +e\n", 1)[1].split("\n\n", 1)[0]
    return "set +e\n" + textwrap.dedent(body)


class CiGateExecutionTests(unittest.TestCase):
    def execute_gate(self, source):
        with tempfile.TemporaryDirectory(prefix="rdl-ci-gate-") as temporary:
            directory = Path(temporary)
            tests = directory / "tests"
            tests.mkdir()
            if source is not None:
                (tests / "test_fixture.py").write_text(textwrap.dedent(source))
            invocation = f"{sys.executable} -m unittest discover -s tests -v"
            script = gate_script().replace("python -m unittest discover -s tests -v", invocation)
            self.assertIn(invocation, script, "test must execute the actual workflow test invocation")
            environment = dict(os.environ, RUNNER_TEMP=str(directory))
            return subprocess.run(
                ["bash", "-e", "-o", "pipefail", "-c", script], cwd=directory,
                env=environment, capture_output=True, text=True, timeout=30,
            )

    def test_passing_test_with_skipped_in_its_name_is_not_rejected(self):
        result = self.execute_gate('''
            import unittest
            class Control(unittest.TestCase):
                def test_skipped_is_only_a_word(self):
                    self.assertTrue(True)
        ''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"Ran 1 test in")

    def test_actual_skipped_test_is_rejected(self):
        result = self.execute_gate('''
            import unittest
            class Control(unittest.TestCase):
                @unittest.skip("required local coverage is absent")
                def test_missing_coverage(self):
                    pass
        ''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::error::skipped tests", result.stdout)

    def test_actual_test_failure_keeps_nonzero_exit(self):
        result = self.execute_gate('''
            import unittest
            class Control(unittest.TestCase):
                def test_failure(self):
                    self.assertEqual(1, 2)
        ''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAILED (failures=1)", result.stdout)

    def test_zero_selected_tests_are_rejected(self):
        result = self.execute_gate(None)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("::error::no tests executed", result.stdout)

    def test_setup_error_keeps_nonzero_exit(self):
        result = self.execute_gate('''
            import unittest
            class Control(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise RuntimeError("compiler setup failed")
                def test_not_executed(self):
                    pass
        ''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FAILED (errors=1)", result.stdout)


if __name__ == "__main__":
    unittest.main()
