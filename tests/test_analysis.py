from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roi_dashboard import build_all_outputs  # noqa: E402


class AnalysisPipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.outputs = build_all_outputs(
            ROOT / "data" / "raw" / "clients_activity.csv",
            ROOT / "config" / "assumptions.json",
            Path(cls.temp_dir.name),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_source_reconciliation(self) -> None:
        summary = self.outputs["tableau_model_summary.csv"].iloc[0]
        self.assertEqual(int(summary["SOURCE_ROWS"]), 252001)
        self.assertEqual(int(summary["UNIQUE_CLIENTS"]), 20000)
        self.assertEqual(int(summary["ACCOUNTS"]), 20125)

    def test_probability_bounds(self) -> None:
        curve = self.outputs["tableau_tenure_curve.csv"]
        self.assertTrue(curve["SURVIVAL_RATE"].between(0, 1).all())
        self.assertTrue((curve["ACTIVE_PROBABILITY"] <= curve["SURVIVAL_RATE"]).all())

    def test_current_bank_profit(self) -> None:
        summary = self.outputs["tableau_model_summary.csv"].iloc[0]
        self.assertEqual(
            float(summary["CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB"]),
            45_000_000,
        )
        self.assertEqual(int(summary["BANK_IS_CURRENTLY_PROFITABLE"]), 1)

    def test_ltv_payback_and_repeat_clients(self) -> None:
        summary = self.outputs["tableau_model_summary.csv"].iloc[0]
        self.assertAlmostEqual(
            float(summary["LTV_BEFORE_CAC_ZERO_BALANCE_RUB"]),
            7367.102727,
            places=5,
        )
        self.assertEqual(int(summary["PAYBACK_MONTHS_ZERO_BALANCE"]), 18)
        self.assertEqual(int(summary["REPEAT_CLIENTS"]), 122)
        self.assertEqual(int(summary["REPEAT_ACCOUNTS"]), 125)
        self.assertAlmostEqual(float(summary["REPEAT_CLIENT_SHARE"]), 0.0061)

    def test_scenario_monotonicity(self) -> None:
        scenarios = self.outputs["tableau_break_even_scenarios.csv"]
        for _, frame in scenarios.groupby("COST_BASIS"):
            positive = frame.dropna(subset=["REQUIRED_NEW_CLIENTS_MONTH"])
            self.assertTrue(positive["REQUIRED_NEW_CLIENTS_MONTH"].is_monotonic_decreasing)

    def test_combined_tableau_source(self) -> None:
        combined = self.outputs["tableau_dashboard_data.csv"]
        self.assertEqual(
            set(combined["RECORD_TYPE"].unique()),
            {"SUMMARY", "TENURE", "SCENARIO", "COHORT", "MONTHLY"},
        )


if __name__ == "__main__":
    unittest.main()
