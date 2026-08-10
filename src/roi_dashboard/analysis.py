from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = {"CLIENT_ID", "MONTH", "OPEN_DATE", "ACTIVE"}


@dataclass(frozen=True)
class ModelInputs:
    acquisition_cost_rub: float
    subscription_fee_rub_month: float
    service_cost_rub_month: float
    fixed_cost_rub_month: float
    current_active_clients: int
    annual_balance_yield: float
    scenario_balance_min_rub: int
    scenario_balance_max_rub: int
    scenario_balance_step_rub: int
    minimum_eligible_accounts: int

    @classmethod
    def from_json(cls, path: Path) -> "ModelInputs":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def month_difference(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


def load_activity(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep=";")
    missing = REQUIRED_COLUMNS.difference(df.columns)
    if missing:
        raise ValueError(f"В источнике отсутствуют поля: {sorted(missing)}")

    df = df.loc[:, ["CLIENT_ID", "MONTH", "OPEN_DATE", "ACTIVE"]].copy()
    df["MONTH"] = pd.to_datetime(df["MONTH"], format="%d.%m.%Y", errors="raise")
    df["OPEN_DATE"] = pd.to_datetime(df["OPEN_DATE"], format="%d.%m.%Y", errors="raise")
    df["CLIENT_ID"] = pd.to_numeric(df["CLIENT_ID"], errors="raise").astype("int64")
    df["ACTIVE"] = pd.to_numeric(df["ACTIVE"], errors="raise").astype("int8")
    validate_source(df)
    return add_features(df)


def validate_source(df: pd.DataFrame) -> None:
    if df.isna().any().any():
        raise ValueError("В источнике есть пропуски")
    if df.duplicated().any():
        raise ValueError("В источнике есть полные дубли")
    if not set(df["ACTIVE"].unique()).issubset({0, 1}):
        raise ValueError("ACTIVE должен принимать только значения 0 и 1")
    if (df["MONTH"] < df["OPEN_DATE"].dt.to_period("M").dt.to_timestamp()).any():
        raise ValueError("Найден месяц наблюдения раньше месяца открытия")
    key_cols = ["CLIENT_ID", "OPEN_DATE", "MONTH"]
    if df.duplicated(key_cols).any():
        raise ValueError("Найдено более одной строки на счет и месяц")


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["OPEN_MONTH"] = out["OPEN_DATE"].dt.to_period("M").dt.to_timestamp()
    out["TENURE_MONTH"] = month_difference(out["MONTH"], out["OPEN_MONTH"]).astype("int16")
    out["ACCOUNT_KEY"] = (
        out["CLIENT_ID"].astype(str) + "_" + out["OPEN_DATE"].dt.strftime("%Y%m%d")
    )
    return out.sort_values(["CLIENT_ID", "OPEN_DATE", "MONTH"], kind="stable").reset_index(drop=True)


def build_account_dimension(df: pd.DataFrame) -> pd.DataFrame:
    cutoff_month = df["MONTH"].max()
    grouped = df.groupby(["ACCOUNT_KEY", "CLIENT_ID", "OPEN_DATE", "OPEN_MONTH"], as_index=False)
    accounts = grouped.agg(
        FIRST_OBSERVED_MONTH=("MONTH", "min"),
        LAST_OBSERVED_MONTH=("MONTH", "max"),
        OBSERVED_MONTHS=("MONTH", "size"),
        OBSERVED_ACTIVE_MONTHS=("ACTIVE", "sum"),
    )
    accounts["MAX_POTENTIAL_TENURE"] = month_difference(
        pd.Series(cutoff_month, index=accounts.index), accounts["OPEN_MONTH"]
    ).astype("int16")
    accounts["IS_OBSERVED_CLOSED"] = (accounts["LAST_OBSERVED_MONTH"] < cutoff_month).astype("int8")
    accounts["OBSERVED_ACTIVE_RATE"] = (
        accounts["OBSERVED_ACTIVE_MONTHS"] / accounts["OBSERVED_MONTHS"]
    )
    return accounts


def build_client_month(df: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    flags = accounts[["ACCOUNT_KEY", "LAST_OBSERVED_MONTH", "IS_OBSERVED_CLOSED"]]
    out = df.merge(flags, on="ACCOUNT_KEY", how="left", validate="many_to_one")
    out["IS_LAST_OBSERVED_MONTH"] = (out["MONTH"] == out["LAST_OBSERVED_MONTH"]).astype("int8")
    out["IS_CLOSURE_MONTH"] = (
        (out["IS_LAST_OBSERVED_MONTH"] == 1) & (out["IS_OBSERVED_CLOSED"] == 1)
    ).astype("int8")
    return out


def build_tenure_curve(
    df: pd.DataFrame, accounts: pd.DataFrame, inputs: ModelInputs
) -> pd.DataFrame:
    max_tenure = int(df["TENURE_MONTH"].max())
    observed = df.groupby("TENURE_MONTH", as_index=False).agg(
        OPEN_ACCOUNTS=("ACCOUNT_KEY", "nunique"),
        ACTIVE_ACCOUNTS=("ACTIVE", "sum"),
    )
    eligible = pd.DataFrame({"TENURE_MONTH": np.arange(max_tenure + 1, dtype=int)})
    eligible["ELIGIBLE_ACCOUNTS"] = eligible["TENURE_MONTH"].map(
        lambda tenure: int((accounts["MAX_POTENTIAL_TENURE"] >= tenure).sum())
    )
    curve = eligible.merge(observed, on="TENURE_MONTH", how="left").fillna(0)
    curve[["OPEN_ACCOUNTS", "ACTIVE_ACCOUNTS"]] = curve[
        ["OPEN_ACCOUNTS", "ACTIVE_ACCOUNTS"]
    ].astype(int)
    curve["SURVIVAL_RATE"] = curve["OPEN_ACCOUNTS"] / curve["ELIGIBLE_ACCOUNTS"]
    curve["ACTIVE_PROBABILITY"] = curve["ACTIVE_ACCOUNTS"] / curve["ELIGIBLE_ACCOUNTS"]
    curve["ACTIVE_RATE_AMONG_OPEN"] = curve["ACTIVE_ACCOUNTS"] / curve["OPEN_ACCOUNTS"]
    curve["IS_RELIABLE_SAMPLE"] = (
        curve["ELIGIBLE_ACCOUNTS"] >= inputs.minimum_eligible_accounts
    ).astype("int8")

    active_contribution = curve["ACTIVE_PROBABILITY"] * (
        inputs.subscription_fee_rub_month - inputs.service_cost_rub_month
    )
    all_open_contribution = (
        curve["ACTIVE_PROBABILITY"] * inputs.subscription_fee_rub_month
        - curve["SURVIVAL_RATE"] * inputs.service_cost_rub_month
    )
    curve["NET_CONTRIBUTION_ACTIVE_SERVICE_RUB"] = active_contribution
    curve["NET_CONTRIBUTION_ALL_OPEN_SERVICE_RUB"] = all_open_contribution
    curve["CUM_NET_CONTRIBUTION_ACTIVE_SERVICE_RUB"] = active_contribution.cumsum()
    curve["CUM_NET_CONTRIBUTION_ALL_OPEN_SERVICE_RUB"] = all_open_contribution.cumsum()
    curve["CUM_MARGIN_AFTER_CAC_ACTIVE_SERVICE_RUB"] = (
        curve["CUM_NET_CONTRIBUTION_ACTIVE_SERVICE_RUB"] - inputs.acquisition_cost_rub
    )
    curve["CUM_MARGIN_AFTER_CAC_ALL_OPEN_SERVICE_RUB"] = (
        curve["CUM_NET_CONTRIBUTION_ALL_OPEN_SERVICE_RUB"] - inputs.acquisition_cost_rub
    )
    return curve


def build_scenarios(curve: pd.DataFrame, inputs: ModelInputs) -> pd.DataFrame:
    active_months = float(curve["ACTIVE_PROBABILITY"].sum())
    open_months = float(curve["SURVIVAL_RATE"].sum())
    balances = np.arange(
        inputs.scenario_balance_min_rub,
        inputs.scenario_balance_max_rub + inputs.scenario_balance_step_rub,
        inputs.scenario_balance_step_rub,
        dtype=int,
    )
    rows: list[dict[str, float | int | str]] = []
    for cost_basis in ("Только активные месяцы", "Все открытые месяцы"):
        service_months = active_months if cost_basis == "Только активные месяцы" else open_months
        for balance in balances:
            fee_revenue = active_months * inputs.subscription_fee_rub_month
            balance_revenue = active_months * balance * inputs.annual_balance_yield / 12
            service_cost = service_months * inputs.service_cost_rub_month
            margin_before_cac = fee_revenue + balance_revenue - service_cost
            unit_margin = margin_before_cac - inputs.acquisition_cost_rub
            required = math.ceil(inputs.fixed_cost_rub_month / unit_margin) if unit_margin > 0 else np.nan
            rows.append(
                {
                    "COST_BASIS": cost_basis,
                    "AVERAGE_BALANCE_RUB": balance,
                    "EXPECTED_ACTIVE_MONTHS": active_months,
                    "EXPECTED_OPEN_MONTHS": open_months,
                    "FEE_REVENUE_LTV_RUB": fee_revenue,
                    "BALANCE_REVENUE_LTV_RUB": balance_revenue,
                    "SERVICE_COST_LTV_RUB": service_cost,
                    "MARGIN_BEFORE_CAC_RUB": margin_before_cac,
                    "UNIT_MARGIN_AFTER_CAC_RUB": unit_margin,
                    "REQUIRED_NEW_CLIENTS_MONTH": required,
                    "IS_UNIT_ECONOMICS_POSITIVE": int(unit_margin > 0),
                }
            )
    return pd.DataFrame(rows)


def build_cohort_summary(df: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    activity = df.groupby("OPEN_MONTH", as_index=False).agg(
        CLIENT_MONTHS=("ACCOUNT_KEY", "size"),
        ACTIVE_MONTHS=("ACTIVE", "sum"),
    )
    account_metrics = accounts.groupby("OPEN_MONTH", as_index=False).agg(
        ACCOUNTS=("ACCOUNT_KEY", "nunique"),
        UNIQUE_CLIENTS=("CLIENT_ID", "nunique"),
        CLOSED_ACCOUNTS=("IS_OBSERVED_CLOSED", "sum"),
        AVG_OBSERVED_MONTHS=("OBSERVED_MONTHS", "mean"),
        AVG_OBSERVED_ACTIVE_MONTHS=("OBSERVED_ACTIVE_MONTHS", "mean"),
    )
    out = account_metrics.merge(activity, on="OPEN_MONTH", how="left", validate="one_to_one")
    out["OBSERVED_ACTIVE_RATE"] = out["ACTIVE_MONTHS"] / out["CLIENT_MONTHS"]
    out["OBSERVED_CLOSED_SHARE"] = out["CLOSED_ACCOUNTS"] / out["ACCOUNTS"]
    return out


def build_cohort_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby(["OPEN_MONTH", "TENURE_MONTH"], as_index=False)
        .agg(
            ACCOUNTS=("ACCOUNT_KEY", "nunique"),
            ACTIVE_ACCOUNTS=("ACTIVE", "sum"),
            ACTIVE_RATE=("ACTIVE", "mean"),
        )
        .sort_values(["OPEN_MONTH", "TENURE_MONTH"])
    )


def build_monthly_activity(client_month: pd.DataFrame) -> pd.DataFrame:
    monthly = client_month.groupby("MONTH", as_index=False).agg(
        OPEN_ACCOUNTS=("ACCOUNT_KEY", "nunique"),
        ACTIVE_ACCOUNTS=("ACTIVE", "sum"),
        CLOSED_ACCOUNTS=("IS_CLOSURE_MONTH", "sum"),
    )
    new_accounts = (
        client_month.loc[client_month["TENURE_MONTH"] == 0]
        .groupby("MONTH")["ACCOUNT_KEY"]
        .nunique()
    )
    monthly["NEW_ACCOUNTS"] = monthly["MONTH"].map(new_accounts).fillna(0).astype(int)
    monthly["ACTIVE_RATE"] = monthly["ACTIVE_ACCOUNTS"] / monthly["OPEN_ACCOUNTS"]
    return monthly


def build_summary(
    df: pd.DataFrame,
    accounts: pd.DataFrame,
    curve: pd.DataFrame,
    scenarios: pd.DataFrame,
    inputs: ModelInputs,
) -> pd.DataFrame:
    base = scenarios[
        (scenarios["COST_BASIS"] == "Только активные месяцы")
        & (scenarios["AVERAGE_BALANCE_RUB"] == 0)
    ].iloc[0]
    current_revenue = inputs.current_active_clients * inputs.subscription_fee_rub_month
    current_service_cost = inputs.current_active_clients * inputs.service_cost_rub_month
    current_profit = current_revenue - current_service_cost - inputs.fixed_cost_rub_month
    account_counts = accounts.groupby("CLIENT_ID").size()
    repeat_clients = int((account_counts > 1).sum())
    repeat_accounts = int((account_counts - 1).clip(lower=0).sum())
    payback_rows = curve.loc[
        curve["CUM_MARGIN_AFTER_CAC_ACTIVE_SERVICE_RUB"] >= 0,
        "TENURE_MONTH",
    ]
    payback_months = int(payback_rows.iloc[0]) + 1 if not payback_rows.empty else np.nan
    summary = {
        "SOURCE_ROWS": len(df),
        "UNIQUE_CLIENTS": df["CLIENT_ID"].nunique(),
        "ACCOUNTS": accounts["ACCOUNT_KEY"].nunique(),
        "REPEAT_CLIENTS": repeat_clients,
        "REPEAT_ACCOUNTS": repeat_accounts,
        "REPEAT_CLIENT_SHARE": repeat_clients / df["CLIENT_ID"].nunique(),
        "OBSERVATION_START": df["MONTH"].min(),
        "OBSERVATION_END": df["MONTH"].max(),
        "OBSERVED_HORIZON_MONTHS": int(curve["TENURE_MONTH"].max()) + 1,
        "EXPECTED_ACTIVE_MONTHS_TRUNCATED": curve["ACTIVE_PROBABILITY"].sum(),
        "EXPECTED_OPEN_MONTHS_TRUNCATED": curve["SURVIVAL_RATE"].sum(),
        "CURRENT_ACTIVE_CLIENTS": inputs.current_active_clients,
        "CURRENT_MONTHLY_REVENUE_RUB": current_revenue,
        "CURRENT_MONTHLY_SERVICE_COST_RUB": current_service_cost,
        "FIXED_COST_RUB_MONTH": inputs.fixed_cost_rub_month,
        "CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB": current_profit,
        "BANK_IS_CURRENTLY_PROFITABLE": int(current_profit >= 0),
        "LTV_BEFORE_CAC_ZERO_BALANCE_RUB": base["MARGIN_BEFORE_CAC_RUB"],
        "PAYBACK_MONTHS_ZERO_BALANCE": payback_months,
        "UNIT_MARGIN_AFTER_CAC_ZERO_BALANCE_RUB": base["UNIT_MARGIN_AFTER_CAC_RUB"],
        "REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE": base["REQUIRED_NEW_CLIENTS_MONTH"],
    }
    return pd.DataFrame([summary])


def build_checks(
    df: pd.DataFrame,
    accounts: pd.DataFrame,
    curve: pd.DataFrame,
    summary: pd.DataFrame,
) -> pd.DataFrame:
    checks = [
        ("source_has_no_nulls", not df.isna().any().any(), 0, int(df.isna().sum().sum())),
        ("account_month_key_unique", not df.duplicated(["ACCOUNT_KEY", "MONTH"]).any(), 0, int(df.duplicated(["ACCOUNT_KEY", "MONTH"]).sum())),
        ("active_probability_le_survival", bool((curve["ACTIVE_PROBABILITY"] <= curve["SURVIVAL_RATE"] + 1e-12).all()), 0, int((curve["ACTIVE_PROBABILITY"] > curve["SURVIVAL_RATE"] + 1e-12).sum())),
        ("survival_within_bounds", bool(curve["SURVIVAL_RATE"].between(0, 1).all()), 0, int((~curve["SURVIVAL_RATE"].between(0, 1)).sum())),
        ("accounts_reconcile", len(accounts) == df[["CLIENT_ID", "OPEN_DATE"]].drop_duplicates().shape[0], int(len(accounts)), int(df[["CLIENT_ID", "OPEN_DATE"]].drop_duplicates().shape[0])),
        ("current_profit_reconciles", float(summary.iloc[0]["CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB"]) == 45_000_000, 45_000_000, float(summary.iloc[0]["CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB"])),
    ]
    return pd.DataFrame(checks, columns=["CHECK_NAME", "PASSED", "EXPECTED", "ACTUAL"])


def format_dates_for_csv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in out.select_dtypes(include=["datetime64[ns]"]).columns:
        out[column] = out[column].dt.strftime("%Y-%m-%d")
    return out


def build_combined_tableau_source(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    for record_type, frame in frames.items():
        part = format_dates_for_csv(frame)
        part.insert(0, "RECORD_TYPE", record_type)
        parts.append(part)
    return pd.concat(parts, ignore_index=True, sort=False)


def build_all_outputs(raw_path: Path, config_path: Path, output_dir: Path) -> dict[str, pd.DataFrame]:
    inputs = ModelInputs.from_json(config_path)
    df = load_activity(raw_path)
    accounts = build_account_dimension(df)
    client_month = build_client_month(df, accounts)
    curve = build_tenure_curve(df, accounts, inputs)
    scenarios = build_scenarios(curve, inputs)
    cohorts = build_cohort_summary(df, accounts)
    cohort_heatmap = build_cohort_heatmap(df)
    monthly = build_monthly_activity(client_month)
    summary = build_summary(df, accounts, curve, scenarios, inputs)
    checks = build_checks(df, accounts, curve, summary)

    if not checks["PASSED"].all():
        failed = checks.loc[~checks["PASSED"], "CHECK_NAME"].tolist()
        raise AssertionError(f"Не пройдены проверки: {failed}")

    outputs = {
        "tableau_client_month.csv": client_month.drop(columns=["LAST_OBSERVED_MONTH"]),
        "tableau_accounts.csv": accounts,
        "tableau_tenure_curve.csv": curve,
        "tableau_break_even_scenarios.csv": scenarios,
        "tableau_cohort_summary.csv": cohorts,
        "tableau_cohort_heatmap.csv": cohort_heatmap,
        "tableau_monthly_activity.csv": monthly,
        "tableau_model_summary.csv": summary,
        "model_checks.csv": checks,
    }
    combined_frames = {
        "SUMMARY": summary,
        "TENURE": curve,
        "SCENARIO": scenarios,
        "COHORT": cohort_heatmap,
        "MONTHLY": monthly,
    }
    outputs["tableau_dashboard_data.csv"] = build_combined_tableau_source(combined_frames)
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, frame in outputs.items():
        format_dates_for_csv(frame).to_csv(output_dir / filename, index=False)
    return outputs
