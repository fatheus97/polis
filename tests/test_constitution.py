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


if __name__ == "__main__":
    unittest.main()
