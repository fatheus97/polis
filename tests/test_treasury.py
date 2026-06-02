import unittest

from polis.treasury import InsufficientFunds, Treasury


class TreasuryTest(unittest.TestCase):
    def test_appropriate_and_debit(self):
        t = Treasury(":memory:")
        t.appropriate(100)
        self.assertEqual(t.balance(), 100)
        t.debit("dev", 30, "implement", run_id="r1")
        self.assertEqual(t.balance(), 70)
        self.assertEqual(t.total_spent(), 30)
        self.assertEqual(t.total_appropriated(), 100)
        self.assertEqual(t.spent_on("r1"), 30)
        self.assertEqual(t.spent_on("other"), 0)

    def test_can_afford(self):
        t = Treasury(":memory:")
        t.appropriate(50)
        self.assertTrue(t.can_afford(50))
        self.assertFalse(t.can_afford(51))

    def test_overdraft_raises(self):
        t = Treasury(":memory:")
        t.appropriate(10)
        with self.assertRaises(InsufficientFunds):
            t.debit("dev", 20, "implement")
        self.assertEqual(t.balance(), 10)  # unchanged

    def test_appropriation_must_be_positive(self):
        t = Treasury(":memory:")
        with self.assertRaises(ValueError):
            t.appropriate(0)


if __name__ == "__main__":
    unittest.main()
