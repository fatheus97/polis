import unittest

from polis.constitution import Constitution
from polis.models import Diff, FileChange


class ConstitutionTest(unittest.TestCase):
    def setUp(self):
        self.c = Constitution.load()

    def test_detects_hardcoded_secret(self):
        diff = Diff(changes=[FileChange("config.py", 'API_KEY = "sk-deadbeefcafebabe1234"\n')])
        violations = self.c.check_diff(diff)
        ids = {v.rule_id for v in violations}
        self.assertIn("no-hardcoded-secrets", ids)
        self.assertTrue(any(v.severity == "block" for v in violations))

    def test_detects_password_assignment(self):
        diff = Diff(changes=[FileChange("a.py", 'password = "hunter2secret"\n')])
        self.assertTrue(self.c.check_diff(diff))

    def test_clean_diff_has_no_violations(self):
        diff = Diff(changes=[FileChange("feature.py", 'def feature():\n    return "ok"\n')])
        self.assertEqual(self.c.check_diff(diff), [])

    def test_blocking_ids(self):
        self.assertIn("no-hardcoded-secrets", self.c.blocking_ids)


class ProtectCoreTest(unittest.TestCase):
    def setUp(self):
        self.c = Constitution.load()

    def test_blocks_core_file_edit(self):
        vs = self.c.check_diff(Diff(changes=[FileChange("polis/orchestrator.py", "x = 1\n")]))
        self.assertIn("protect-core", {v.rule_id for v in vs})
        self.assertTrue(any(v.severity == "block" for v in vs))

    def test_blocks_constitution_edit(self):
        vs = self.c.check_diff(Diff(changes=[FileChange("config/constitution.json", "{}\n")]))
        self.assertIn("protect-core", {v.rule_id for v in vs})

    def test_allows_dashboard_tests_docs(self):
        for p in ("polis/dashboard/server.py", "polis/dashboard/static/app.js",
                  "tests/test_x.py", "docs/PRD.md", "README.md"):
            vs = self.c.check_diff(Diff(changes=[FileChange(p, "x = 1\n")]))
            self.assertNotIn("protect-core", {v.rule_id for v in vs}, p)

    def test_path_rule_ignores_content_mentions(self):
        # a non-core file that merely MENTIONS a core path must not trip the path rule
        diff = Diff(changes=[FileChange("notes.md", "see polis/orchestrator.py for details\n")])
        self.assertNotIn("protect-core", {v.rule_id for v in self.c.check_diff(diff)})

    def test_rule_defaults_to_content_target(self):
        from polis.constitution import Rule
        r = Rule(id="x", description="d", severity="warn", check="regex", pattern="y")
        self.assertEqual(r.target, "content")


if __name__ == "__main__":
    unittest.main()
