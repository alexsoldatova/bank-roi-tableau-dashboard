from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from roi_dashboard import build_all_outputs  # noqa: E402


if __name__ == "__main__":
    outputs = build_all_outputs(
        raw_path=ROOT / "data" / "raw" / "clients_activity.csv",
        config_path=ROOT / "config" / "assumptions.json",
        output_dir=ROOT / "data" / "processed",
    )
    summary = outputs["tableau_model_summary.csv"].iloc[0]
    print("Tableau-выгрузки построены")
    print(f"Счетов: {int(summary['ACCOUNTS']):,}")
    print(
        "Текущая прибыль до привлечения: "
        f"{summary['CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB']:,.0f} руб./мес."
    )
    print(
        "Новых клиентов для покрытия fixed costs: "
        f"{summary['REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE']:,.0f} чел./мес."
    )
