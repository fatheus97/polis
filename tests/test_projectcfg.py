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
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

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
        self.assertNotIn("workspace", projectcfg.read_config(self.base))  # key cleared

    def test_corrupt_config_ignored(self):
        (self.base / "config.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(projectcfg.read_config(self.base), {})

    def test_model_tier_overrides_and_default(self):
        from polis.registry import ModelTier
        self.assertEqual(ModelTier().dev, "sonnet")  # the dev is bumped off Haiku
        self.assertEqual(projectcfg.resolve_model_tier_overrides(self.base), {})
        projectcfg.write_config(self.base, {"dev_model": "opus", "architect_model": "opus"})
        ov = projectcfg.resolve_model_tier_overrides(self.base)
        self.assertEqual(ov, {"architect": "opus", "dev": "opus"})
        self.assertEqual(ModelTier(**ov).reviewer, "sonnet")  # an unset role keeps its default

    def test_dev_timeout_config(self):
        self.assertEqual(projectcfg.resolve_dev_timeout(self.base), 900)  # default
        projectcfg.write_config(self.base, {"dev_timeout": 1800})
        self.assertEqual(projectcfg.resolve_dev_timeout(self.base), 1800)
        projectcfg.write_config(self.base, {"dev_timeout": "bad"})
        self.assertEqual(projectcfg.resolve_dev_timeout(self.base), 900)  # bad -> default

    def test_build_government_uses_config_models_and_timeout(self):
        from polis.app import build_government
        from polis.llm import FakeLLM
        projectcfg.write_config(self.base, {"dev_model": "opus", "dev_timeout": 1500})

        class FakeWS:
            path = "/not-the-polis-repo"
        gov = build_government(self.base, agents="real", backend=FakeLLM([]), workspace=FakeWS())
        try:
            dev = gov.registry.hire_dev()
            self.assertEqual(dev.model, "opus")             # model from config
            self.assertEqual(dev.timeout, 1500)             # dev_timeout from config
            self.assertEqual(gov.registry.spawn("architect").model, "sonnet")  # default
        finally:
            gov.close()

    def test_configured_non_git_dir_is_refused(self):
        from polis.app import build_government
        target = Path(tempfile.mkdtemp(prefix="polis-nongit-"))
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        (target / "somefile.txt").write_text("x", encoding="utf-8")  # non-empty, not a repo
        projectcfg.write_config(self.base, {"workspace": str(target)})
        with self.assertRaises(ValueError):
            build_government(self.base)  # refuses to git-init an existing non-git dir

    def test_protect_core_only_when_developing_polis_repo(self):
        # The protect-core path-rule must be ACTIVE only when the target repo is Polis's own
        # repo; for any other app it's dropped so a stray polis/ dir isn't falsely blocked.
        # Inject a fake workspace so we never construct git on the real repo.
        import polis as polis_pkg
        from polis.app import build_government
        polis_root = Path(polis_pkg.__file__).resolve().parent.parent

        class FakeWS:
            def __init__(self, p):
                self.path = p

        gov = build_government(self.base, workspace=FakeWS(Path("/some/other/app")))
        try:
            self.assertNotIn("protect-core", {r.id for r in gov.constitution.rules})
        finally:
            gov.close()
        gov2 = build_government(self.base, workspace=FakeWS(polis_root))
        try:
            self.assertIn("protect-core", {r.id for r in gov2.constitution.rules})
        finally:
            gov2.close()

    def test_restart_on_merge_default_off_and_config(self):
        self.assertFalse(projectcfg.resolve_restart_on_merge(self.base))   # default off
        projectcfg.write_config(self.base, {"restart_on_merge": True})
        self.assertTrue(projectcfg.resolve_restart_on_merge(self.base))

    def test_merge_via_pr_default_off_and_injection(self):
        from polis.app import build_government
        from polis.merger import LocalMerger, PullRequestMerger

        class FakeWS:
            def __init__(self, p):
                self.path = p

        self.assertFalse(projectcfg.resolve_merge_via_pr(self.base))  # default off
        gov = build_government(self.base, workspace=FakeWS("/some/app"))
        try:
            self.assertIsInstance(gov.orchestrator.merger, LocalMerger)  # default = local
        finally:
            gov.close()

        projectcfg.write_config(self.base, {"merge_via_pr": True})
        gov2 = build_government(self.base, workspace=FakeWS("/some/app"))
        try:
            self.assertIsInstance(gov2.orchestrator.merger, PullRequestMerger)
        finally:
            gov2.close()


@unittest.skipUnless(HAVE_GIT, "git required")
class ConfiguredRepoTest(unittest.TestCase):
    def test_build_government_uses_configured_repo(self):
        from polis.app import build_government
        base = Path(tempfile.mkdtemp(prefix="polis-cfg-"))
        target = Path(tempfile.mkdtemp(prefix="polis-app-")) / "myapp"
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.addCleanup(shutil.rmtree, target.parent, ignore_errors=True)
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
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.addCleanup(shutil.rmtree, target.parent, ignore_errors=True)
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
