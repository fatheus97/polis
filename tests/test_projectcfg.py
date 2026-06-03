"""Project config + configurable target-repo tests."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from polis import projectcfg

HAVE_GIT = shutil.which("git") is not None


class ProjectCfgTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="polis-cfg-"))

    def test_default_is_managed_workspace(self):
        self.assertEqual(projectcfg.resolve_workspace(self.base),
                         (self.base / "workspace").resolve())
        self.assertTrue(projectcfg.is_managed_default(self.base))
        self.assertEqual(projectcfg.resolve_main_branch(self.base), "main")

    def test_override_beats_config_and_default(self):
        projectcfg.write_config(self.base, {"workspace": str(Path("/cfg/app").resolve())})
        self.assertEqual(projectcfg.resolve_workspace(self.base, "/override/app"),
                         Path("/override/app").resolve())

    def test_write_persists_and_resolves(self):
        projectcfg.write_config(self.base, {"workspace": str(Path("/my/app").resolve()),
                                            "main_branch": "master"})
        self.assertEqual(projectcfg.resolve_workspace(self.base), Path("/my/app").resolve())
        self.assertEqual(projectcfg.resolve_main_branch(self.base), "master")
        self.assertFalse(projectcfg.is_managed_default(self.base))
        self.assertEqual(projectcfg.read_config(self.base)["main_branch"], "master")

    def test_empty_workspace_resets_to_default(self):
        projectcfg.write_config(self.base, {"workspace": str(Path("/my/app").resolve())})
        projectcfg.write_config(self.base, {"workspace": ""})
        self.assertTrue(projectcfg.is_managed_default(self.base))
        self.assertEqual(projectcfg.resolve_workspace(self.base),
                         (self.base / "workspace").resolve())

    def test_corrupt_config_ignored(self):
        (self.base / "config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(projectcfg.read_config(self.base), {})


@unittest.skipUnless(HAVE_GIT, "git required")
class ConfiguredRepoTest(unittest.TestCase):
    def test_build_government_uses_configured_repo(self):
        from polis.app import build_government
        base = Path(tempfile.mkdtemp(prefix="polis-cfg-"))
        target = Path(tempfile.mkdtemp(prefix="polis-app-")) / "myapp"
        projectcfg.write_config(base, {"workspace": str(target)})
        gov = build_government(base)
        try:
            self.assertEqual(Path(gov.workspace.path), target.resolve())
        finally:
            gov.close()

    def test_parallel_develops_the_configured_repo(self):
        from polis.app import build_government
        base = Path(tempfile.mkdtemp(prefix="polis-cfg-"))
        target = Path(tempfile.mkdtemp(prefix="polis-app-")) / "app"
        projectcfg.write_config(base, {"workspace": str(target)})
        gov = build_government(base)
        try:
            gov.treasury.appropriate(1000)
            gov.inbox.submit("feature", directives={"module": "cfgx"})
            results = gov.run_parallel(gov.inbox.pending(), max_workers=1)
            self.assertTrue(results[0].merged, results[0].reason)
            files = subprocess.run(["git", "-C", str(target), "ls-files"],
                                   capture_output=True, text=True).stdout
            self.assertIn("cfgx.py", files)  # landed in the configured repo
        finally:
            gov.close()


if __name__ == "__main__":
    unittest.main()
