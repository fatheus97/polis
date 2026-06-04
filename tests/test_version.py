"""Guard: polis.__version__ must match pyproject.toml. The two drifted silently
(polis/__init__.py stayed at 0.2.0 through v0.3.0 and v0.4.0); this catches it."""

import re
import unittest
from pathlib import Path

import polis

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


class VersionConsistencyTest(unittest.TestCase):
    def test_dunder_version_matches_pyproject(self):
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "version not found in pyproject.toml")
        self.assertEqual(polis.__version__, m.group(1))


if __name__ == "__main__":
    unittest.main()
