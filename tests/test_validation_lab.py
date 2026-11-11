"""Standard-library tests for the isolated synthetic validation laboratory."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.validation import lab


class SyntheticLabTests(unittest.TestCase):
    def test_all_scenarios_are_reproducible_and_distinctly_named(self):
        self.assertEqual(len(set(lab.SCENARIOS)), 15)
        for scenario in lab.SCENARIOS:
            self.assertEqual(lab.make_market(scenario, 9, 60), lab.make_market(scenario, 9, 60))
            self.assertNotEqual(lab.make_market(scenario, 9, 60), lab.make_market(scenario, 10, 60))

    def test_unknown_scenario_and_empty_input_are_rejected(self):
        with self.assertRaises(ValueError):
            lab.make_market("unknown", 1)
        with self.assertRaises(ValueError):
            lab.fit_logistic([])

    def test_known_signal_generalizes_to_independent_suffix(self):
        rows = lab.make_market("trend", 7, 3000)
        model = lab.fit_logistic(rows[:1500])
        test = rows[1500:]
        p = [lab.sigmoid(model[0] + model[1] * x) for x, *_ in test]
        metrics = lab.score(test, p, "model")
        self.assertGreater(metrics["auc"], 0.7)
        self.assertLess(metrics["brier"], 0.25)

    def test_noise_has_no_planted_edge_on_predefined_sample(self):
        rows = lab.make_market("noise", 7, 3000)
        model = lab.fit_logistic(rows[:1500])
        test = rows[1500:]
        p = [lab.sigmoid(model[0] + model[1] * x) for x, *_ in test]
        self.assertLess(abs(lab.auc_score(p, [int(y > 0) for _, y, _, _ in test]) - 0.5), 0.08)

    def test_auc_ties_and_one_class(self):
        self.assertEqual(lab.auc_score([0.5, 0.5], [0, 1]), 0.5)
        self.assertEqual(lab.auc_score([0.1, 0.9], [0, 1]), 1.0)
        self.assertIsNone(lab.auc_score([0.2, 0.9], [1, 1]))

    def test_two_sided_cost_and_cash_accounting(self):
        rows = [(1.0, 0.1, 0.01, True)]
        m = lab.score(rows, [0.8], "model", allocation=1.0)
        expected = 30 * 0.99 * 1.1 * 0.99
        self.assertAlmostEqual(m["final_equity_eur"], expected)
        self.assertAlmostEqual(m["mean_net_trade"], expected / 30 - 1)
        self.assertEqual(m["n_trades"], 1)

    def test_costs_can_turn_a_gross_gain_into_a_net_loss(self):
        m = lab.score([(1, 0.001, 0.004, True)], [0.9], "model")
        self.assertLess(m["mean_net_trade"], 0)
        self.assertLess(m["final_equity_eur"], 30)

    def test_flat_baseline_preserves_capital(self):
        rows = lab.make_market("downtrend", 11, 100)
        m = lab.score(rows, [0.5] * len(rows), "flat")
        self.assertEqual(m["final_equity_eur"], 30)
        self.assertEqual(m["n_trades"], 0)
        self.assertEqual(m["refused"], len(rows))

    def test_holdout_is_not_used_to_fit(self):
        rows = lab.make_market("trend", 12, 700)
        first = lab.fit_logistic(rows[:500])
        modified = rows[:500] + [(x, -y, c, a) for x, y, c, a in rows[500:]]
        self.assertEqual(first, lab.fit_logistic(modified[:500]))
        self.assertNotEqual(rows[500:], modified[500:])

    def test_paired_block_bootstrap_is_reproducible(self):
        series = [0.0, 0.01, -0.02, 0.01] * 20
        a = lab.block_bootstrap_mean_ci(series, 123, block=4, reps=100)
        self.assertEqual(a, lab.block_bootstrap_mean_ci(series, 123, block=4, reps=100))
        self.assertLessEqual(a[0], a[1])
        with self.assertRaises(ValueError):
            lab.block_bootstrap_mean_ci([], 123)

    def test_identical_models_have_zero_paired_difference_and_no_promotion(self):
        rows = lab.make_market("trend", 13, 240)
        result = lab.compare_models(rows, (0.0, 0.4), (0.0, 0.4), 7)
        self.assertEqual(result["daily_return_delta_ci_95pct"], [0.0, 0.0])
        self.assertFalse(result["automatic_promotion"])
        self.assertEqual(result["verdict"], "TESTS INCOMPLETS")

    def test_calibration_bins_cover_all_observations(self):
        rows = lab.make_market("trend", 3, 100)
        m = lab.score(rows, [0.5] * 100, "model")
        self.assertEqual(sum(b["n"] for b in m["calibration_bins"]), 100)

    def test_report_files_match_and_never_authorize_real_trading(self):
        r = lab.run_experiment(seeds=(2,), scenarios=("noise",), sizes=(100,))
        with tempfile.TemporaryDirectory() as directory:
            lab.write_reports(r, directory)
            saved = json.loads((Path(directory) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["results"][0]["verdict"], "AVANTAGE NET NON DÉMONTRÉ")
            self.assertIn("noise", (Path(directory) / "learning_curve.csv").read_text(encoding="utf-8"))
            self.assertIn("not TRADER AI", (Path(directory) / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
