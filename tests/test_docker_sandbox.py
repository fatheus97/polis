"""DockerSandbox test — opt-in (it pulls an image / needs the daemon).

Run with:  POLIS_DOCKER_TEST=1 py -m unittest tests.test_docker_sandbox
Skipped by default so the suite stays fast, hermetic, and offline.
"""

import os
import tempfile
import unittest
from pathlib import Path

from polis.sandbox import DockerSandbox

RUN = os.environ.get("POLIS_DOCKER_TEST") == "1" and DockerSandbox.available()


class _WS:
    def __init__(self, path):
        self.path = path


@unittest.skipUnless(RUN, "set POLIS_DOCKER_TEST=1 (and have docker) to run")
class DockerSandboxTest(unittest.TestCase):
    def _workspace(self, test_body: str) -> _WS:
        tmp = Path(tempfile.mkdtemp(prefix="polis-docker-"))
        (tmp / "test_smoke.py").write_text(
            "import unittest\n"
            "class T(unittest.TestCase):\n"
            f"    def test_it(self):\n        {test_body}\n",
            encoding="utf-8",
        )
        return _WS(tmp)

    def test_passing_tests_pass_in_container(self):
        res = DockerSandbox().run_tests(self._workspace("self.assertTrue(True)"))
        self.assertTrue(res.ran)
        self.assertTrue(res.passed, res.details)

    def test_failing_tests_fail_in_container(self):
        res = DockerSandbox().run_tests(self._workspace("self.assertTrue(False)"))
        self.assertTrue(res.ran)
        self.assertFalse(res.passed)


if __name__ == "__main__":
    unittest.main()
