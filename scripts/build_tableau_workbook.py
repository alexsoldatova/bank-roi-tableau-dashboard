from __future__ import annotations

from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "processed" / "tableau_dashboard_data.csv"
BUILD_DIR = ROOT / "work" / "tableau" / "build"
OUTPUT_DIR = ROOT / "outputs"
TWB_NAME = "bank_roi_tableau_public.twb"
TWBX_NAME = "bank_roi_tableau_public.twbx"
HYPER_NAME = "tableau_dashboard_data.hyper"
CSV_NAME = "tableau_dashboard_data.csv"
DS = "ROIData"
CAPTIONS = {"DASHBOARD_DATE": "Период отчёта"}
DEFAULT_FORMATS = {
    "ACTIVE_PROBABILITY": "p0.0%",
    "ACTIVE_PROBABILITY_LABEL": "n0.0&quot;%&quot;",
    "ACTIVE_RATE": "p0.00%",
    "REPEAT_CLIENT_SHARE": "p0.00%",
    "LTV_BEFORE_CAC_ZERO_BALANCE_RUB": "c&quot;₽&quot;#,##0.00",
    "UNIT_MARGIN_AFTER_CAC_RUB": "c&quot;₽&quot;#,##0.00",
    "AVERAGE_BALANCE_RUB": "c&quot;₽&quot;#,##0.00",
    "REQUIRED_NEW_CLIENTS_MONTH": "n#,##0.00",
}
CALCULATED_FIELDS = {
    "LTV_BEFORE_CAC_ZERO_BALANCE_RUB",
    "PAYBACK_MONTHS_ZERO_BALANCE",
    "REPEAT_CLIENTS",
    "REPEAT_CLIENT_SHARE",
    "DASHBOARD_DATE",
    "ACTIVE_PROBABILITY_LABEL",
}


def calculated_formulas() -> dict[str, str]:
    summary = pd.read_csv(ROOT / "data" / "processed" / "tableau_model_summary.csv").iloc[0]
    return {
        "LTV_BEFORE_CAC_ZERO_BALANCE_RUB": format(
            float(summary["LTV_BEFORE_CAC_ZERO_BALANCE_RUB"]), ".2f"
        ),
        "PAYBACK_MONTHS_ZERO_BALANCE": str(
            int(summary["PAYBACK_MONTHS_ZERO_BALANCE"])
        ),
        "REPEAT_CLIENTS": str(int(summary["REPEAT_CLIENTS"])),
        "REPEAT_CLIENT_SHARE": format(float(summary["REPEAT_CLIENT_SHARE"]), ".15g"),
        "DASHBOARD_DATE": "IFNULL([OPEN_MONTH], [MONTH])",
        "ACTIVE_PROBABILITY_LABEL": "ROUND([ACTIVE_PROBABILITY] * 100, 1)",
    }


def column(
    name: str,
    datatype: str,
    role: str,
    column_type: str,
    aggregation: str | None = None,
    formula: str | None = None,
) -> str:
    agg = f" aggregation='{aggregation}'" if aggregation else ""
    caption = f" caption='{CAPTIONS[name]}'" if name in CAPTIONS else ""
    default_format = (
        f" default-format='{DEFAULT_FORMATS[name]}'" if name in DEFAULT_FORMATS else ""
    )
    calculation = f"<calculation class='tableau' formula='{formula}' />" if formula else ""
    return (
        f"<column{agg}{caption} datatype='{datatype}'{default_format} name='[{name}]' "
        f"role='{role}' type='{column_type}'>{calculation}</column>"
    )


COLUMNS = {
    "RECORD_TYPE": ("string", "dimension", "nominal", None),
    "CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB": ("real", "measure", "quantitative", "Sum"),
    "REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE": ("real", "measure", "quantitative", "Sum"),
    "LTV_BEFORE_CAC_ZERO_BALANCE_RUB": ("real", "measure", "quantitative", "Sum"),
    "PAYBACK_MONTHS_ZERO_BALANCE": ("integer", "measure", "quantitative", "Sum"),
    "REPEAT_CLIENTS": ("integer", "measure", "quantitative", "Sum"),
    "REPEAT_CLIENT_SHARE": ("real", "measure", "quantitative", "Avg"),
    "OBSERVED_HORIZON_MONTHS": ("integer", "measure", "quantitative", "Sum"),
    "TENURE_MONTH": ("integer", "dimension", "ordinal", None),
    "ACTIVE_PROBABILITY": ("real", "measure", "quantitative", "Avg"),
    "ACTIVE_PROBABILITY_LABEL": ("real", "measure", "quantitative", "Avg"),
    "SURVIVAL_RATE": ("real", "measure", "quantitative", "Avg"),
    "ELIGIBLE_ACCOUNTS": ("integer", "measure", "quantitative", "Sum"),
    "AVERAGE_BALANCE_RUB": ("real", "dimension", "quantitative", None),
    "REQUIRED_NEW_CLIENTS_MONTH": ("real", "measure", "quantitative", "Avg"),
    "UNIT_MARGIN_AFTER_CAC_RUB": ("real", "measure", "quantitative", "Avg"),
    "COST_BASIS": ("string", "dimension", "nominal", None),
    "OPEN_MONTH": ("date", "dimension", "ordinal", None),
    "ACTIVE_RATE": ("real", "measure", "quantitative", "Avg"),
    "ACCOUNTS": ("integer", "measure", "quantitative", "Sum"),
    "MONTH": ("date", "dimension", "ordinal", None),
    "ACTIVE_ACCOUNTS": ("integer", "measure", "quantitative", "Sum"),
    "OPEN_ACCOUNTS": ("integer", "measure", "quantitative", "Sum"),
    "DASHBOARD_DATE": ("date", "dimension", "ordinal", None),
}


def dependency_xml(fields: list[str]) -> str:
    base = []
    instances = []
    formulas = calculated_formulas()
    for field in fields:
        datatype, role, field_type, aggregation = COLUMNS[field]
        base.append(
            column(
                field,
                datatype,
                role,
                field_type,
                aggregation,
                formulas.get(field),
            )
        )
        derivation = "Avg" if aggregation == "Average" else (aggregation or "None")
        suffix = "qk" if field_type == "quantitative" else ("ok" if field_type == "ordinal" else "nk")
        instance_name = f"[{derivation.lower()}:{field}:{suffix}]"
        instances.append(
            f"<column-instance column='[{field}]' derivation='{derivation}' "
            f"name='{instance_name}' pivot='key' type='{field_type}' />"
        )
    return "\n".join(base + instances)


def filter_xml(record_type: str, extra: str = "") -> str:
    filters = f"""
    <filter class='categorical' column='[{DS}].[RECORD_TYPE]'>
      <groupfilter function='member' level='[RECORD_TYPE]' member='&quot;{record_type}&quot;' />
    </filter>"""
    if extra:
        filters += "\n" + extra
    return filters


def worksheet(
    name: str,
    fields: list[str],
    rows: str,
    cols: str,
    mark: str,
    encodings: str,
    record_type: str,
    extra_filter: str = "",
    extra_style: str = "",
    show_labels: bool = False,
    mark_color: str = "#5B5FEF",
) -> str:
    labels_value = "true" if show_labels else "false"
    return f"""
    <worksheet name='{name}'>
      <table>
        <view>
          <datasources><datasource caption='ROI Dashboard Data' name='{DS}' /></datasources>
          <datasource-dependencies datasource='{DS}'>
            {dependency_xml(fields)}
          </datasource-dependencies>
          {filter_xml(record_type, extra_filter)}
          <slices><column>[{DS}].[RECORD_TYPE]</column></slices>
          <aggregation value='true' />
        </view>
        <style>
          <style-rule element='worksheet'>
            <format attr='color' value='#172033' />
            <format attr='font-family' value='Arial' />
          </style-rule>
          <style-rule element='axis'>
            <format attr='font-family' value='Arial' />
            <format attr='font-size' value='9' />
          </style-rule>
          <style-rule element='label'><format attr='font-family' value='Arial' /></style-rule>
          {extra_style}
        </style>
        <panes>
          <pane>
            <view><breakdown value='auto' /></view>
            <mark class='{mark}' />
            <encodings>{encodings}</encodings>
            <style>
              <style-rule element='mark'>
                <format attr='mark-labels-show' value='{labels_value}' />
                <format attr='mark-labels-cull' value='true' />
                <format attr='color' value='{mark_color}' />
              </style-rule>
            </style>
          </pane>
        </panes>
        <rows>{rows}</rows>
        <cols>{cols}</cols>
      </table>
    </worksheet>"""


def datasource_connection_xml(use_hyper: bool) -> str:
    if use_hyper:
        return f"""
      <connection class='federated'>
        <named-connections>
          <named-connection caption='ROI Dashboard Data' name='hyper.roi'>
            <connection authentication='auth-none' author-locale='ru_RU' class='hyper' dbname='Data/Extracts/{HYPER_NAME}' default-settings='yes' port='' sslmode='' username='tableau_internal_user' />
          </named-connection>
        </named-connections>
        <relation connection='hyper.roi' name='Extract' table='[Extract].[Extract]' type='table' />
        <refresh increment-key='' incremental-updates='false' />
      </connection>"""
    csv_columns = "\n".join(
        f"<column datatype='{spec[0]}' name='{name}' ordinal='{ordinal}' />"
        for ordinal, (name, spec) in enumerate(
            (item for item in COLUMNS.items() if item[0] not in CALCULATED_FIELDS)
        )
    )
    return f"""
      <connection class='textscan' directory='' filename='{CSV_NAME}' password='' server=''>
        <relation name='tableau_dashboard_data#csv' table='[tableau_dashboard_data#csv]' type='table'>
          <columns character-set='UTF-8' header='yes' locale='ru_RU' separator=','>
            {csv_columns}
          </columns>
        </relation>
      </connection>"""


def build_twb(use_hyper: bool = True) -> str:
    formulas = calculated_formulas()
    root_columns = "\n".join(
        column(name, *spec, formulas.get(name)) for name, spec in COLUMNS.items()
    )
    datasource_connection = datasource_connection_xml(use_hyper)
    active_window = """
    <window class='dashboard' maximized='true' name='Окупаемость банка'>
      <viewpoints>
        <viewpoint name='Текущая прибыль'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Новых клиентов для безубыточности'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='LTV клиента, ₽'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Срок окупаемости, мес.'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Повторно открыли счёт, чел.'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Активность по возрасту счета'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Остаток → клиенты для окупаемости'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Когортная heatmap активности'><zoom type='entire-view' /></viewpoint>
        <viewpoint name='Динамика активной базы'><zoom type='entire-view' /></viewpoint>
      </viewpoints>
      <active id='-1' />
    </window>"""
    if not use_hyper:
        active_window = """
    <window class='worksheet' maximized='true' name='Текущая прибыль'>
      <cards>
        <edge name='left'><strip size='160'><card type='pages' /><card type='filters' /><card type='marks' /></strip></edge>
        <edge name='top'><strip size='2147483647'><card type='columns' /></strip><strip size='2147483647'><card type='rows' /></strip></edge>
      </cards>
    </window>"""
    kpi_profit = worksheet(
        "Текущая прибыль",
        ["RECORD_TYPE", "CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB"],
        "",
        "",
        "Text",
        f"<text column='[{DS}].[sum:CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB:qk]' />",
        "SUMMARY",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[sum:CURRENT_MONTHLY_PROFIT_BEFORE_ACQUISITION_RUB:qk]' value='₽#,##0,,&quot; млн&quot;' />"
            "<format attr='font-size' value='24' /><format attr='font-weight' value='bold' />"
            "<format attr='color' value='#15A36D' /></style-rule>"
        ),
        show_labels=True,
        mark_color="#15A36D",
    )
    kpi_clients = worksheet(
        "Новых клиентов для безубыточности",
        ["RECORD_TYPE", "REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE"],
        "",
        "",
        "Text",
        f"<text column='[{DS}].[sum:REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE:qk]' />",
        "SUMMARY",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[sum:REQUIRED_NEW_CLIENTS_MONTH_ZERO_BALANCE:qk]' value='#,##0&quot; / мес.&quot;' />"
            "<format attr='font-size' value='24' /><format attr='font-weight' value='bold' />"
            "<format attr='color' value='#5B5FEF' /></style-rule>"
        ),
        show_labels=True,
    )
    kpi_ltv = worksheet(
        "LTV клиента, ₽",
        ["RECORD_TYPE", "LTV_BEFORE_CAC_ZERO_BALANCE_RUB"],
        "",
        "",
        "Text",
        f"<text column='[{DS}].[sum:LTV_BEFORE_CAC_ZERO_BALANCE_RUB:qk]' />",
        "SUMMARY",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[sum:LTV_BEFORE_CAC_ZERO_BALANCE_RUB:qk]' value='₽#,##0.00' />"
            "<format attr='font-size' value='24' /><format attr='font-weight' value='bold' />"
            "<format attr='color' value='#15A36D' /></style-rule>"
        ),
        show_labels=True,
        mark_color="#15A36D",
    )
    kpi_payback = worksheet(
        "Срок окупаемости, мес.",
        ["RECORD_TYPE", "PAYBACK_MONTHS_ZERO_BALANCE"],
        "",
        "",
        "Text",
        f"<text column='[{DS}].[sum:PAYBACK_MONTHS_ZERO_BALANCE:qk]' />",
        "SUMMARY",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[sum:PAYBACK_MONTHS_ZERO_BALANCE:qk]' value='#,##0\" мес.\"' />"
            "<format attr='font-size' value='24' /><format attr='font-weight' value='bold' />"
            "<format attr='color' value='#F59E0B' /></style-rule>"
        ),
        show_labels=True,
        mark_color="#F59E0B",
    )
    kpi_repeat = worksheet(
        "Повторно открыли счёт, чел.",
        ["RECORD_TYPE", "REPEAT_CLIENTS", "REPEAT_CLIENT_SHARE"],
        "",
        "",
        "Text",
        (
            f"<text column='[{DS}].[sum:REPEAT_CLIENTS:qk]' />"
            f"<tooltip column='[{DS}].[avg:REPEAT_CLIENT_SHARE:qk]' />"
        ),
        "SUMMARY",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[sum:REPEAT_CLIENTS:qk]' value='#,##0\" чел.\"' />"
            "<format attr='text-format' field='[ROIData].[avg:REPEAT_CLIENT_SHARE:qk]' value='0.00%' />"
            "<format attr='font-size' value='24' /><format attr='font-weight' value='bold' />"
            "<format attr='color' value='#5B5FEF' /></style-rule>"
        ),
        show_labels=True,
    )
    tenure = worksheet(
        "Активность по возрасту счета",
        [
            "RECORD_TYPE",
            "TENURE_MONTH",
            "ACTIVE_PROBABILITY",
            "ACTIVE_PROBABILITY_LABEL",
            "ELIGIBLE_ACCOUNTS",
        ],
        f"[{DS}].[avg:ACTIVE_PROBABILITY:qk]",
        f"[{DS}].[none:TENURE_MONTH:ok]",
        "Line",
        (
            f"<text column='[{DS}].[avg:ACTIVE_PROBABILITY_LABEL:qk]' />"
            f"<tooltip column='[{DS}].[sum:ELIGIBLE_ACCOUNTS:qk]' />"
        ),
        "TENURE",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[avg:ACTIVE_PROBABILITY:qk]' value='0.0%' />"
            "</style-rule>"
        ),
        show_labels=True,
    )
    scenario_filter = f"""
    <filter class='categorical' column='[{DS}].[COST_BASIS]'>
      <groupfilter function='member' level='[COST_BASIS]' member='&quot;Только активные месяцы&quot;' />
    </filter>"""
    scenario = worksheet(
        "Остаток → клиенты для окупаемости",
        ["RECORD_TYPE", "AVERAGE_BALANCE_RUB", "REQUIRED_NEW_CLIENTS_MONTH", "UNIT_MARGIN_AFTER_CAC_RUB", "COST_BASIS"],
        f"[{DS}].[avg:REQUIRED_NEW_CLIENTS_MONTH:qk]",
        f"[{DS}].[none:AVERAGE_BALANCE_RUB:qk]",
        "Line",
        (
            f"<tooltip column='[{DS}].[avg:UNIT_MARGIN_AFTER_CAC_RUB:qk]' />"
        ),
        "SCENARIO",
        scenario_filter,
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[avg:UNIT_MARGIN_AFTER_CAC_RUB:qk]' value='₽#,##0.00' />"
            "</style-rule>"
        ),
        show_labels=True,
    )
    cohort = worksheet(
        "Когортная heatmap активности",
        ["RECORD_TYPE", "OPEN_MONTH", "TENURE_MONTH", "ACTIVE_RATE", "ACCOUNTS", "DASHBOARD_DATE"],
        f"[{DS}].[none:OPEN_MONTH:ok]",
        f"[{DS}].[none:TENURE_MONTH:ok]",
        "Square",
        (
            f"<color column='[{DS}].[avg:ACTIVE_RATE:qk]' />"
            f"<tooltip column='[{DS}].[sum:ACCOUNTS:qk]' />"
        ),
        "COHORT",
        f"""
    <filter class='categorical' column='[{DS}].[none:DASHBOARD_DATE:ok]' filter-group='1'>
      <groupfilter function='level-members' level='[none:DASHBOARD_DATE:ok]' />
    </filter>""",
        extra_style=(
            "<style-rule element='cell'>"
            "<format attr='text-format' field='[ROIData].[avg:ACTIVE_RATE:qk]' value='0.00%' />"
            "</style-rule>"
        ),
    )
    monthly = worksheet(
        "Динамика активной базы",
        ["RECORD_TYPE", "MONTH", "ACTIVE_ACCOUNTS", "OPEN_ACCOUNTS", "DASHBOARD_DATE"],
        f"[{DS}].[sum:ACTIVE_ACCOUNTS:qk]",
        f"[{DS}].[none:MONTH:ok]",
        "Line",
        (
            f"<tooltip column='[{DS}].[sum:OPEN_ACCOUNTS:qk]' />"
        ),
        "MONTHLY",
        f"""
    <filter class='categorical' column='[{DS}].[none:DASHBOARD_DATE:ok]' filter-group='1'>
      <groupfilter function='level-members' level='[none:DASHBOARD_DATE:ok]' />
    </filter>""",
        show_labels=True,
    )

    return f"""<?xml version='1.0' encoding='utf-8'?>
<workbook locale='ru_RU' source-build='2021.3.0 (20213.21.0902.1838)' source-platform='mac' version='18.1' xmlns:user='http://www.tableausoftware.com/xml/user'>
  <preferences>
    <preference name='ui.encoding.shelf.height' value='24' />
    <preference name='ui.shelf.height' value='26' />
  </preferences>
  <style-theme name='smooth' />
  <datasources>
    <datasource caption='ROI Dashboard Data' inline='true' name='{DS}' version='18.1'>
      {datasource_connection}
      <aliases enabled='yes' />
      {root_columns}
      <layout dim-ordering='alphabetic' dim-percentage='0.5' measure-ordering='alphabetic' measure-percentage='0.4' show-structure='true' />
      <semantic-values>
        <semantic-value key='[Country].[Name]' value='&quot;Россия&quot;' />
      </semantic-values>
    </datasource>
  </datasources>
  <worksheets>
    {kpi_profit}
    {kpi_clients}
    {kpi_ltv}
    {kpi_payback}
    {kpi_repeat}
    {tenure}
    {scenario}
    {cohort}
    {monthly}
  </worksheets>
  <dashboards>
    <dashboard name='Окупаемость банка'>
      <layout-options>
        <title><formatted-text><run bold='true' fontcolor='#172033' fontname='Arial' fontsize='24'>Окупаемость банка</run></formatted-text></title>
      </layout-options>
      <style>
        <style-rule element='dashboard'><format attr='background-color' value='#F7F8FA' /></style-rule>
      </style>
      <size maxheight='900' maxwidth='1440' minheight='900' minwidth='1440' />
      <datasources><datasource caption='ROI Dashboard Data' name='{DS}' /></datasources>
      <zones>
        <zone h='100000' id='1' type='layout-basic' w='100000' x='0' y='0'>
          <zone h='6000' id='2' type='title' w='100000' x='0' y='0' />
          <zone h='8000' id='3' type='text' w='75000' x='0' y='6000'>
            <formatted-text>
              <run bold='true' fontcolor='#15A36D' fontsize='14'>Банк операционно прибылен: +45,0 млн ₽/мес.</run>
              <run fontcolor='#667085' fontsize='11'>&#10;До расходов на новое привлечение. Ниже отдельно показана экономика устойчивого потока новых когорт.</run>
            </formatted-text>
          </zone>
          <zone h='8000' id='13' mode='checkdropdown' name='Когортная heatmap активности' param='[ROIData].[none:DASHBOARD_DATE:ok]' show-all='true' type-v2='filter' values='relevant' w='25000' x='75000' y='6000' />
          <zone h='13000' id='4' name='Текущая прибыль' show-title='true' w='20000' x='0' y='14000' />
          <zone h='13000' id='5' name='LTV клиента, ₽' show-title='true' w='20000' x='20000' y='14000' />
          <zone h='13000' id='6' name='Срок окупаемости, мес.' show-title='true' w='20000' x='40000' y='14000' />
          <zone h='13000' id='7' name='Повторно открыли счёт, чел.' show-title='true' w='20000' x='60000' y='14000' />
          <zone h='13000' id='8' name='Новых клиентов для безубыточности' show-title='true' w='20000' x='80000' y='14000' />
          <zone h='30000' id='9' name='Активность по возрасту счета' show-title='true' w='50000' x='0' y='27000' />
          <zone h='30000' id='10' name='Остаток → клиенты для окупаемости' show-title='true' w='50000' x='50000' y='27000' />
          <zone h='43000' id='11' name='Когортная heatmap активности' show-title='true' w='60000' x='0' y='57000' />
          <zone h='43000' id='12' name='Динамика активной базы' show-title='true' w='40000' x='60000' y='57000' />
        </zone>
      </zones>
    </dashboard>
  </dashboards>
  <windows source-height='32'>
    {active_window}
  </windows>
</workbook>
"""


def build_hyper(hyper_path: Path) -> None:
    try:
        from tableauhyperapi import (
            Connection,
            CreateMode,
            HyperProcess,
            Inserter,
            Nullability,
            SqlType,
            TableDefinition,
            TableName,
            Telemetry,
        )
    except ImportError:
        sys.path.insert(0, str(ROOT / "work" / "tableau" / "pydeps"))
        from tableauhyperapi import (
            Connection,
            CreateMode,
            HyperProcess,
            Inserter,
            Nullability,
            SqlType,
            TableDefinition,
            TableName,
            Telemetry,
        )

    physical_columns = [name for name in COLUMNS if name not in CALCULATED_FIELDS]
    data = pd.read_csv(DATA_FILE, low_memory=False).loc[:, physical_columns]
    type_map = {
        "string": SqlType.text(),
        "date": SqlType.date(),
        "integer": SqlType.big_int(),
        "real": SqlType.double(),
    }
    table_name = TableName("Extract", "Extract")
    table_definition = TableDefinition(
        table_name,
        [
            TableDefinition.Column(name, type_map[spec[0]], Nullability.NULLABLE)
            for name, spec in COLUMNS.items()
            if name not in CALCULATED_FIELDS
        ],
    )

    rows = []
    for record in data.itertuples(index=False, name=None):
        converted = []
        for value, name in zip(record, physical_columns):
            datatype = COLUMNS[name][0]
            if pd.isna(value):
                converted.append(None)
            elif datatype == "date":
                converted.append(pd.Timestamp(value).date())
            elif datatype == "integer":
                converted.append(int(value))
            elif datatype == "real":
                converted.append(float(value))
            else:
                converted.append(str(value))
        rows.append(converted)

    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
        with Connection(hyper.endpoint, hyper_path, CreateMode.CREATE_AND_REPLACE) as connection:
            connection.catalog.create_schema("Extract")
            connection.catalog.create_table(table_definition)
            with Inserter(connection, table_definition) as inserter:
                inserter.add_rows(rows)
                inserter.execute()


def main() -> None:
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Сначала запустите scripts/build_tableau_data.py: {DATA_FILE}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / TWBX_NAME
    reusable_hyper: bytes | None = None
    if output_path.exists():
        with zipfile.ZipFile(output_path) as existing:
            hyper_members = [name for name in existing.namelist() if name.endswith(".hyper")]
            if hyper_members:
                reusable_hyper = existing.read(hyper_members[0])

    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    (BUILD_DIR / "Data" / "Extracts").mkdir(parents=True)
    twb_path = BUILD_DIR / TWB_NAME
    twb_path.write_text(build_twb(use_hyper=True), encoding="utf-8")
    ET.parse(twb_path)
    hyper_path = BUILD_DIR / "Data" / "Extracts" / HYPER_NAME
    try:
        build_hyper(hyper_path)
    except Exception as exc:
        if reusable_hyper is None:
            raise RuntimeError("Не удалось создать Hyper extract и нет extract для повторного использования") from exc
        hyper_path.write_bytes(reusable_hyper)
        print(f"Hyper extract недоступен, использую проверенный extract из предыдущей сборки: {exc}")
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(twb_path, TWB_NAME)
        archive.write(hyper_path, f"Data/Extracts/{HYPER_NAME}")
    print(f"Создан Tableau workbook: {output_path}")


if __name__ == "__main__":
    sys.exit(main())
