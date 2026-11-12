import unittest

from tools.validation.leakage import prefix_mismatches, purged_split


class LeakageTests(unittest.TestCase):
    def test_purge_excludes_unresolved_overlaps_and_embargo(self):
        events = [{"decision_ts": 1, "exit_ts": 4}, {"decision_ts": 2, "exit_ts": 9},
                  {"decision_ts": 3, "exit_ts": None}, {"decision_ts": 10, "exit_ts": 12},
                  {"decision_ts": 20, "exit_ts": 22}]
        result = purged_split(events, 10, 20, embargo=2)
        self.assertEqual(result["train_indices"], [0])
        self.assertEqual(result["test_indices"], [3])

    def test_exact_cutoff_is_excluded_and_invalid_times_fail(self):
        event = {"decision_ts": 1, "exit_ts": 8}
        self.assertEqual(purged_split([event], 10, 20, embargo=2)["train_indices"], [])
        with self.assertRaises(ValueError):
            purged_split([{"decision_ts": 2, "exit_ts": 1}], 10, 20)
        with self.assertRaises(ValueError):
            purged_split([], 20, 10)

    def test_causal_prefix_passes_and_future_dependent_transform_fails(self):
        rows = [1, 2, 3, 4]
        self.assertEqual(prefix_mismatches(lambda xs: [sum(xs[:i + 1]) for i in range(len(xs))], rows, [1, 2, 3]), [])
        self.assertTrue(prefix_mismatches(lambda xs: [sum(xs) for _ in xs], rows, [1, 2, 3]))

    def test_invalid_transform_or_checkpoint_fails(self):
        with self.assertRaises(ValueError):
            prefix_mismatches(lambda xs: [], [1, 2], [1])
        with self.assertRaises(ValueError):
            prefix_mismatches(lambda xs: xs, [1, 2], [3])


if __name__ == "__main__":
    unittest.main()
