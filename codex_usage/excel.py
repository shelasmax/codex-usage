from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from codex_usage.analytics import UsageBucket


HEADERS_FILL = PatternFill("solid", fgColor="1F4E78")
HEADERS_FONT = Font(color="FFFFFF", bold=True)

# Control characters forbidden by the XLSX specification.
_ILLEGAL_XLSX_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F]"
)


def clean_excel_text(
    value,
    max_length: int = 32000,
):
    """
    Make text safe for Excel/OpenXML.

    Excel cells support at most 32,767 characters.
    We keep a small safety margin because Excel for Mac
    may repair worksheet string properties at the limit.
    """
    if not isinstance(value, str):
        return value

    value = _ILLEGAL_XLSX_CHARS.sub(
        "",
        value,
    )

    return value[:max_length]


def style_sheet(ws, table_name: str) -> None:
    ws.freeze_panes = "A2"

    for cell in ws[1]:
        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(horizontal="center")

    if ws.max_row >= 2:
        table = Table(
            displayName=table_name,
            ref=ws.dimensions,
        )

        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )

        ws.add_table(table)

    for column in ws.columns:
        letter = get_column_letter(column[0].column)

        width = max(
            len(str(cell.value or ""))
            for cell in column
        )

        ws.column_dimensions[letter].width = min(
            max(width + 2, 12),
            32,
        )


def token_format(ws, columns: list[int]) -> None:
    for row in range(2, ws.max_row + 1):
        for column in columns:
            ws.cell(row, column).number_format = "#,##0"


def build_workbook(
    model_rows: list[dict],
    task_rows: list[dict],
    output_path: str | Path,
) -> Path:

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()

    # Remove default sheet.
    default = wb.active
    wb.remove(default)

    # =====================================================
    # DASHBOARD
    # =====================================================

    ws_dashboard = wb.create_sheet(
        "Dashboard",
        0,
    )

    ws_dashboard.sheet_view.showGridLines = False

    ws_dashboard["A1"] = "CODEX USAGE DASHBOARD"
    ws_dashboard["A1"].font = Font(
        size=20,
        bold=True,
    )

    ws_dashboard["A2"] = (
        "Local Codex usage · API-equivalent base rates"
    )

    ws_dashboard["A2"].font = Font(
        size=10,
        italic=True,
    )

    ws_dashboard["A4"] = "Metric"
    ws_dashboard["B4"] = "Today"
    ws_dashboard["C4"] = "This Week"
    ws_dashboard["D4"] = "This Month"
    ws_dashboard["E4"] = "All Time"

    dashboard_headers = [
        "Total Tokens",
        "Priced API Equivalent RUB",
        "Uncached Input",
        "Output Tokens",
        "Cache Hit %",
        "Unpriced Tokens",
        "Pricing Coverage %",
    ]

    for row_number, label in enumerate(
        dashboard_headers,
        start=5,
    ):
        ws_dashboard.cell(
            row=row_number,
            column=1,
            value=label,
        )

    for cell in ws_dashboard[4]:
        if cell.column <= 5:
            cell.fill = HEADERS_FILL
            cell.font = HEADERS_FONT
            cell.alignment = Alignment(
                horizontal="center"
            )

    ws_dashboard.column_dimensions["A"].width = 24
    ws_dashboard.column_dimensions["B"].width = 18
    ws_dashboard.column_dimensions["C"].width = 18
    ws_dashboard.column_dimensions["D"].width = 18
    ws_dashboard.column_dimensions["E"].width = 18

    # -----------------------
    # DASHBOARD KPI DATA
    # -----------------------

    if model_rows:
        latest_date = max(
            datetime.strptime(
                row["date"],
                "%Y-%m-%d",
            ).date()
            for row in model_rows
        )

        current_week_start = (
            latest_date
            - timedelta(
                days=latest_date.weekday()
            )
        )

        current_month_start = (
            latest_date.replace(day=1)
        )
    else:
        latest_date = None
        current_week_start = None
        current_month_start = None

    def dashboard_bucket():
        return {
            "total": 0,
            "input": 0,
            "cached": 0,
            "uncached": 0,
            "output": 0,
            "cost_rub": 0.0,
            "unpriced": 0,
        }

    dashboard_periods = {
        "today": dashboard_bucket(),
        "week": dashboard_bucket(),
        "month": dashboard_bucket(),
        "all": dashboard_bucket(),
    }

    def add_dashboard_row(
        target: dict,
        row: dict,
    ) -> None:
        target["total"] += row["total"]
        target["input"] += row["input"]
        target["cached"] += row["cached"]
        target["uncached"] += row["uncached"]
        target["output"] += row["output"]
        target["cost_rub"] += row["cost_rub"]

        if not row["priced"]:
            target["unpriced"] += row["total"]

    for row in model_rows:
        row_date = datetime.strptime(
            row["date"],
            "%Y-%m-%d",
        ).date()

        add_dashboard_row(
            dashboard_periods["all"],
            row,
        )

        if latest_date is not None:
            if row_date == latest_date:
                add_dashboard_row(
                    dashboard_periods["today"],
                    row,
                )

            if row_date >= current_week_start:
                add_dashboard_row(
                    dashboard_periods["week"],
                    row,
                )

            if row_date >= current_month_start:
                add_dashboard_row(
                    dashboard_periods["month"],
                    row,
                )

    dashboard_columns = {
        "today": 2,
        "week": 3,
        "month": 4,
        "all": 5,
    }

    for period, column in dashboard_columns.items():
        data = dashboard_periods[period]

        cache_hit = (
            data["cached"] / data["input"]
            if data["input"]
            else 0
        )
        pricing_coverage = (
            (data["total"] - data["unpriced"])
            / data["total"]
            if data["total"]
            else 0
        )

        ws_dashboard.cell(
            row=5,
            column=column,
            value=data["total"],
        )

        ws_dashboard.cell(
            row=6,
            column=column,
            value=data["cost_rub"],
        )

        ws_dashboard.cell(
            row=7,
            column=column,
            value=data["uncached"],
        )

        ws_dashboard.cell(
            row=8,
            column=column,
            value=data["output"],
        )

        ws_dashboard.cell(
            row=9,
            column=column,
            value=cache_hit,
        )

        ws_dashboard.cell(
            row=10,
            column=column,
            value=data["unpriced"],
        )
        ws_dashboard.cell(
            row=11,
            column=column,
            value=pricing_coverage,
        )

    for row_number in [5, 7, 8, 10]:
        for column in range(2, 6):
            ws_dashboard.cell(
                row=row_number,
                column=column,
            ).number_format = "#,##0"

    for column in range(2, 6):
        ws_dashboard.cell(
            row=6,
            column=column,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=9,
            column=column,
        ).number_format = "0.0%"
        
        ws_dashboard.cell(
            row=11,
            column=column,
        ).number_format = "0.0%"

    if latest_date is not None:
        ws_dashboard["A12"] = (
            f"Latest usage date: "
            f"{latest_date.isoformat()}"
        )

        ws_dashboard["A12"].font = Font(
            size=9,
            italic=True,
        )

    # -----------------------
    # DAILY TREND
    # -----------------------

    trend = defaultdict(
        lambda: {
            "tokens": 0,
            "cost_rub": 0.0,
            "uncached": 0,
            "output": 0,
            "unpriced": 0,
        }
    )

    for row in model_rows:
        day = row["date"]

        trend[day]["tokens"] += row["total"]
        trend[day]["cost_rub"] += row["cost_rub"]
        trend[day]["uncached"] += row["uncached"]
        trend[day]["output"] += row["output"]

        if not row["priced"]:
            trend[day]["unpriced"] += row["total"]

    ws_dashboard["A15"] = "DAILY TREND"
    ws_dashboard["A15"].font = Font(
        size=14,
        bold=True,
    )

    trend_headers = [
        "Date",
        "Total Tokens",
        "API Equivalent RUB",
        "Uncached Input",
        "Output Tokens",
        "Unpriced Tokens",
    ]

    for column, header in enumerate(
        trend_headers,
        start=1,
    ):
        cell = ws_dashboard.cell(
            row=16,
            column=column,
            value=header,
        )

        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(
            horizontal="center"
        )

    trend_start_row = 17

    for row_number, day in enumerate(
        sorted(trend),
        start=trend_start_row,
    ):
        data = trend[day]

        ws_dashboard.cell(
            row=row_number,
            column=1,
            value=datetime.strptime(
                day,
                "%Y-%m-%d",
            ).date(),
        )

        ws_dashboard.cell(
            row=row_number,
            column=2,
            value=data["tokens"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=3,
            value=data["cost_rub"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=4,
            value=data["uncached"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=5,
            value=data["output"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=6,
            value=data["unpriced"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=1,
        ).number_format = "yyyy-mm-dd"

        for column in [2, 4, 5, 6]:
            ws_dashboard.cell(
                row=row_number,
                column=column,
            ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=3,
        ).number_format = '#,##0.00 "₽"'

    # -----------------------
    # TOP PROJECTS
    # -----------------------

    dashboard_projects = defaultdict(
        lambda: {
            "tokens": 0,
            "cost_rub": 0.0,
            "unpriced": 0,
        }
    )

    for row in model_rows:
        project = row["project"]

        dashboard_projects[
            project
        ]["tokens"] += row["total"]

        dashboard_projects[
            project
        ]["cost_rub"] += row["cost_rub"]

        if not row["priced"]:
            dashboard_projects[
                project
            ]["unpriced"] += row["total"]

    total_project_cost = sum(
        item["cost_rub"]
        for item in dashboard_projects.values()
    )

    top_projects = sorted(
        dashboard_projects.items(),
        key=lambda item: item[1]["cost_rub"],
        reverse=True,
    )[:10]

    projects_title_row = (
        trend_start_row
        + len(trend)
        + 2
    )

    projects_header_row = (
        projects_title_row + 1
    )

    ws_dashboard.cell(
        row=projects_title_row,
        column=1,
        value="TOP PROJECTS BY API COST",
    ).font = Font(
        size=14,
        bold=True,
    )

    project_headers = [
        "Project",
        "Total Tokens",
        "API Equivalent RUB",
        "% of Cost",
        "Unpriced Tokens",
    ]

    for column, header in enumerate(
        project_headers,
        start=1,
    ):
        cell = ws_dashboard.cell(
            row=projects_header_row,
            column=column,
            value=header,
        )

        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(
            horizontal="center"
        )

    for index, (
        project,
        data,
    ) in enumerate(
        top_projects,
        start=1,
    ):
        row_number = (
            projects_header_row
            + index
        )

        share = (
            data["cost_rub"]
            / total_project_cost
            if total_project_cost
            else 0
        )

        ws_dashboard.cell(
            row=row_number,
            column=1,
            value=clean_excel_text(
                project,
                max_length=100,
            ),
        )

        ws_dashboard.cell(
            row=row_number,
            column=2,
            value=data["tokens"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=3,
            value=data["cost_rub"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=4,
            value=share,
        )

        ws_dashboard.cell(
            row=row_number,
            column=5,
            value=data["unpriced"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=2,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=3,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=4,
        ).number_format = "0.0%"

        ws_dashboard.cell(
            row=row_number,
            column=5,
        ).number_format = "#,##0"
        
    # -----------------------
    # TOP TASKS
    # -----------------------

    top_tasks = sorted(
        task_rows,
        key=lambda row: row.get(
            "full_cost_rub",
            0,
        ),
        reverse=True,
    )[:15]

    tasks_title_row = (
        projects_header_row
        + len(top_projects)
        + 3
    )

    tasks_header_row = (
        tasks_title_row + 1
    )

    ws_dashboard.cell(
        row=tasks_title_row,
        column=1,
        value="TOP TASKS BY API COST",
    ).font = Font(
        size=14,
        bold=True,
    )

    task_dashboard_headers = [
        "Project",
        "Task",
        "Model",
        "Effort",
        "Full Tokens",
        "API Equivalent RUB",
        "Subagent RUB",
        "Unpriced Tokens",
    ]

    for column, header in enumerate(
        task_dashboard_headers,
        start=1,
    ):
        cell = ws_dashboard.cell(
            row=tasks_header_row,
            column=column,
            value=header,
        )

        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(
            horizontal="center"
        )

    for index, task in enumerate(
        top_tasks,
        start=1,
    ):
        row_number = (
            tasks_header_row
            + index
        )

        ws_dashboard.cell(
            row=row_number,
            column=1,
            value=clean_excel_text(
                task["project"],
                max_length=80,
            ),
        )

        ws_dashboard.cell(
            row=row_number,
            column=2,
            value=clean_excel_text(
                task["title"],
                max_length=160,
            ),
        )

        ws_dashboard.cell(
            row=row_number,
            column=3,
            value=clean_excel_text(
                task["root_model"],
                max_length=50,
            ),
        )

        ws_dashboard.cell(
            row=row_number,
            column=4,
            value=clean_excel_text(
                task["root_effort"],
                max_length=30,
            ),
        )

        ws_dashboard.cell(
            row=row_number,
            column=5,
            value=task["full_tokens"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=6,
            value=task["full_cost_rub"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=7,
            value=task["subagent_cost_rub"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=8,
            value=task["unpriced_tokens"],
        )

        ws_dashboard.cell(
            row=row_number,
            column=5,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=6,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=7,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=8,
        ).number_format = "#,##0"
        
        
    ws_dashboard.column_dimensions["A"].width = 24
    ws_dashboard.column_dimensions["B"].width = 48
    ws_dashboard.column_dimensions["C"].width = 20
    ws_dashboard.column_dimensions["D"].width = 14
    ws_dashboard.column_dimensions["E"].width = 18
    ws_dashboard.column_dimensions["F"].width = 22
    ws_dashboard.column_dimensions["G"].width = 18
    ws_dashboard.column_dimensions["H"].width = 18        


    # -----------------------
    # MODEL / EFFORT ECONOMICS
    # -----------------------

    model_economics = defaultdict(
        lambda: {
            "calls": 0,
            "tokens": 0,
            "input": 0,
            "cached": 0,
            "cost_rub": 0.0,
            "unpriced": 0,
        }
    )

    for row in model_rows:
        key = (
            row["role"],
            row["model"],
            row["effort"],
        )

        data = model_economics[key]

        data["calls"] += row["calls"]
        data["tokens"] += row["total"]
        data["input"] += row["input"]
        data["cached"] += row["cached"]
        data["cost_rub"] += row["cost_rub"]

        if not row["priced"]:
            data["unpriced"] += row["total"]

    economics_rows = sorted(
        model_economics.items(),
        key=lambda item: item[1]["cost_rub"],
        reverse=True,
    )

    economics_title_row = (
        tasks_header_row
        + len(top_tasks)
        + 3
    )

    economics_header_row = (
        economics_title_row + 1
    )

    ws_dashboard.cell(
        row=economics_title_row,
        column=1,
        value="MODEL / EFFORT ECONOMICS",
    ).font = Font(
        size=14,
        bold=True,
    )

    economics_headers = [
        "Role",
        "Model",
        "Effort",
        "Calls",
        "Total Tokens",
        "API Equivalent RUB",
        "RUB / 1M Tokens",
        "Cache Hit %",
        "Unpriced Tokens",
    ]

    for column, header in enumerate(
        economics_headers,
        start=1,
    ):
        cell = ws_dashboard.cell(
            row=economics_header_row,
            column=column,
            value=header,
        )

        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(
            horizontal="center"
        )

    for index, (
        key,
        data,
    ) in enumerate(
        economics_rows,
        start=1,
    ):
        role, model, effort = key

        row_number = (
            economics_header_row
            + index
        )

        rub_per_million = (
            data["cost_rub"]
            / data["tokens"]
            * 1_000_000
            if data["tokens"]
            else 0
        )

        cache_hit = (
            data["cached"]
            / data["input"]
            if data["input"]
            else 0
        )

        values = [
            role,
            model,
            effort,
            data["calls"],
            data["tokens"],
            data["cost_rub"],
            rub_per_million,
            cache_hit,
            data["unpriced"],
        ]

        for column, value in enumerate(
            values,
            start=1,
        ):
            ws_dashboard.cell(
                row=row_number,
                column=column,
                value=value,
            )

        ws_dashboard.cell(
            row=row_number,
            column=4,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=5,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=6,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=7,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=8,
        ).number_format = "0.0%"

        ws_dashboard.cell(
            row=row_number,
            column=9,
        ).number_format = "#,##0"
        
    ws_dashboard.column_dimensions["I"].width = 18        

    # -----------------------
    # ROOT EFFORT ECONOMICS
    # -----------------------

    root_effort_economics = defaultdict(
        lambda: {
            "calls": 0,
            "tokens": 0,
            "input": 0,
            "cached": 0,
            "cost_rub": 0.0,
            "unpriced": 0,
        }
    )

    for row in model_rows:
        if row["role"] != "ROOT":
            continue

        key = (
            row["model"],
            row["effort"],
        )

        data = root_effort_economics[key]

        data["calls"] += row["calls"]
        data["tokens"] += row["total"]
        data["input"] += row["input"]
        data["cached"] += row["cached"]
        data["cost_rub"] += row["cost_rub"]

        if not row["priced"]:
            data["unpriced"] += row["total"]

    root_effort_rows = sorted(
        root_effort_economics.items(),
        key=lambda item: item[1]["cost_rub"],
        reverse=True,
    )

    root_effort_title_row = (
        economics_header_row
        + len(economics_rows)
        + 3
    )

    root_effort_header_row = (
        root_effort_title_row + 1
    )

    ws_dashboard.cell(
        row=root_effort_title_row,
        column=1,
        value="ROOT EFFORT ECONOMICS",
    ).font = Font(
        size=14,
        bold=True,
    )

    root_effort_headers = [
        "Model",
        "Effort",
        "Calls",
        "Total Tokens",
        "API Equivalent RUB",
        "RUB / 1M Tokens",
        "Cache Hit %",
        "Unpriced Tokens",
    ]

    for column, header in enumerate(
        root_effort_headers,
        start=1,
    ):
        cell = ws_dashboard.cell(
            row=root_effort_header_row,
            column=column,
            value=header,
        )

        cell.fill = HEADERS_FILL
        cell.font = HEADERS_FONT
        cell.alignment = Alignment(
            horizontal="center"
        )

    for index, (
        key,
        data,
    ) in enumerate(
        root_effort_rows,
        start=1,
    ):
        model, effort = key

        row_number = (
            root_effort_header_row
            + index
        )

        rub_per_million = (
            data["cost_rub"]
            / data["tokens"]
            * 1_000_000
            if data["tokens"]
            else 0
        )

        cache_hit = (
            data["cached"]
            / data["input"]
            if data["input"]
            else 0
        )

        values = [
            model,
            effort,
            data["calls"],
            data["tokens"],
            data["cost_rub"],
            rub_per_million,
            cache_hit,
            data["unpriced"],
        ]

        for column, value in enumerate(
            values,
            start=1,
        ):
            ws_dashboard.cell(
                row=row_number,
                column=column,
                value=value,
            )

        ws_dashboard.cell(
            row=row_number,
            column=3,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=4,
        ).number_format = "#,##0"

        ws_dashboard.cell(
            row=row_number,
            column=5,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=6,
        ).number_format = '#,##0.00 "₽"'

        ws_dashboard.cell(
            row=row_number,
            column=7,
        ).number_format = "0.0%"

        ws_dashboard.cell(
            row=row_number,
            column=8,
        ).number_format = "#,##0"

    # -----------------------
    # DASHBOARD CHARTS
    # -----------------------

    trend_end_row = (
        trend_start_row
        + len(trend)
        - 1
    )

    if trend_end_row >= trend_start_row:

        # Tokens by day
        tokens_chart = LineChart()

        tokens_chart.title = (
            "Token Usage by Day"
        )

        tokens_chart.y_axis.title = (
            "Tokens"
        )

        tokens_chart.x_axis.title = (
            "Date"
        )

        tokens_chart.height = 8
        tokens_chart.width = 15

        tokens_data = Reference(
            ws_dashboard,
            min_col=2,
            min_row=16,
            max_row=trend_end_row,
        )

        dates = Reference(
            ws_dashboard,
            min_col=1,
            min_row=17,
            max_row=trend_end_row,
        )

        tokens_chart.add_data(
            tokens_data,
            titles_from_data=True,
        )

        tokens_chart.set_categories(
            dates
        )

        tokens_chart.legend = None

        ws_dashboard.add_chart(
            tokens_chart,
            "H4",
        )

        # API cost by day
        cost_chart = LineChart()

        cost_chart.title = (
            "API Equivalent Cost by Day"
        )

        cost_chart.y_axis.title = (
            "RUB"
        )

        cost_chart.x_axis.title = (
            "Date"
        )

        cost_chart.height = 8
        cost_chart.width = 15

        cost_data = Reference(
            ws_dashboard,
            min_col=3,
            min_row=16,
            max_row=trend_end_row,
        )

        cost_chart.add_data(
            cost_data,
            titles_from_data=True,
        )

        cost_chart.set_categories(
            dates
        )

        cost_chart.legend = None

        ws_dashboard.add_chart(
            cost_chart,
            "H20",
        )

    # -----------------------
    # MODELS
    # -----------------------

    ws_models = wb.create_sheet("Models")

    model_headers = [
        "Date",
        "Project",
        "Role",
        "Model",
        "Effort",
        "Calls",
        "Total Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "Priced",
        "API Cost USD",
        "API Equivalent RUB",
    ]

    ws_models.append(model_headers)

    ws_models.append(model_headers)

    for row in model_rows:
        ws_models.append([
            clean_excel_text(row["date"]),
            clean_excel_text(row["project"]),
            clean_excel_text(row["role"]),
            clean_excel_text(row["model"]),
            clean_excel_text(row["effort"]),
            row["calls"],
            row["total"],
            row["input"],
            row["cached"],
            row["uncached"],
            row["output"],
            row["reasoning"],
            row["cache_hit"],
            "Yes" if row["priced"] else "No",
            row["cost_usd"],
            row["cost_rub"],
        ])

    token_format(
        ws_models,
        [6, 7, 8, 9, 10, 11, 12],
    )

    for row in range(2, ws_models.max_row + 1):
        ws_models.cell(row, 13).number_format = "0.0%"
        ws_models.cell(row, 15).number_format = '$#,##0.00'
        ws_models.cell(row, 16).number_format = '#,##0.00 "₽"'

    style_sheet(ws_models, "ModelsTable")

    # -----------------------
    # DAILY
    # -----------------------

    daily = defaultdict(UsageBucket)

    role_tokens = defaultdict(
        lambda: {
            "ROOT": 0,
            "SUBAGENT": 0,
            "REVIEW": 0,
        }
    )

    daily_cost = defaultdict(
        lambda: {
            "usd": 0.0,
            "rub": 0.0,
            "unpriced_tokens": 0,
        }
    )

    for row in model_rows:
        key = (row["date"], row["project"])
        bucket = daily[key]

        bucket.calls += row["calls"]
        bucket.total_tokens += row["total"]
        bucket.input_tokens += row["input"]
        bucket.cached_input_tokens += row["cached"]
        bucket.uncached_input_tokens += row["uncached"]
        bucket.output_tokens += row["output"]
        bucket.reasoning_tokens += row["reasoning"]

        role_tokens[key][row["role"]] += row["total"]
        daily_cost[key]["usd"] += row["cost_usd"]
        daily_cost[key]["rub"] += row["cost_rub"]

        if not row["priced"]:
            daily_cost[key]["unpriced_tokens"] += row["total"]

    ws_daily = wb.create_sheet("Daily")

    daily_headers = [
        "Date",
        "Project",
        "Calls",
        "Total Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "Root Tokens",
        "Subagent Tokens",
        "Review Tokens",
        "API Cost USD",
        "API Equivalent RUB",
        "Unpriced Tokens",
    ]

    ws_daily.append(daily_headers)

    for key in sorted(daily):
        day, project = key
        bucket = daily[key]
        cost = daily_cost[key]

        ws_daily.append([
            clean_excel_text(day),
            clean_excel_text(project),
            bucket.calls,
            bucket.total_tokens,
            bucket.input_tokens,
            bucket.cached_input_tokens,
            bucket.uncached_input_tokens,
            bucket.output_tokens,
            bucket.reasoning_tokens,
            bucket.cache_hit,
            role_tokens[key]["ROOT"],
            role_tokens[key]["SUBAGENT"],
            role_tokens[key]["REVIEW"],
            cost["usd"],
            cost["rub"],
            cost["unpriced_tokens"],
        ])

    token_format(
        ws_daily,
        [3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 16],
    )

    for row in range(2, ws_daily.max_row + 1):
        ws_daily.cell(row, 10).number_format = "0.0%"
        ws_daily.cell(row, 14).number_format = '$#,##0.00'
        ws_daily.cell(row, 15).number_format = '#,##0.00 "₽"'

    style_sheet(ws_daily, "DailyTable")

    # -----------------------
    # WEEKLY
    # -----------------------

    weekly = defaultdict(UsageBucket)

    weekly_role_tokens = defaultdict(
        lambda: {
            "ROOT": 0,
            "SUBAGENT": 0,
            "REVIEW": 0,
        }
    )
    weekly_cost = defaultdict(
        lambda: {
            "usd": 0.0,
            "rub": 0.0,
            "unpriced_tokens": 0,
        }
    )


    for row in model_rows:
        day = datetime.strptime(
            row["date"],
            "%Y-%m-%d",
        ).date()

        week_start = day - timedelta(
            days=day.weekday()
        )

        week_end = week_start + timedelta(days=6)

        week_label = (
            f"{week_start.isoformat()} — "
            f"{week_end.isoformat()}"
        )

        key = (
            week_start,
            week_label,
            row["project"],
        )

        bucket = weekly[key]

        bucket.calls += row["calls"]
        bucket.total_tokens += row["total"]
        bucket.input_tokens += row["input"]
        bucket.cached_input_tokens += row["cached"]
        bucket.uncached_input_tokens += row["uncached"]
        bucket.output_tokens += row["output"]
        bucket.reasoning_tokens += row["reasoning"]

        weekly_role_tokens[key][
            row["role"]
        ] += row["total"]
        weekly_cost[key]["usd"] += row["cost_usd"]
        weekly_cost[key]["rub"] += row["cost_rub"]

        if not row["priced"]:
            weekly_cost[key]["unpriced_tokens"] += row["total"]

    ws_weekly = wb.create_sheet("Weekly")

    weekly_headers = [
        "Week Start",
        "Week",
        "Project",
        "Calls",
        "Total Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "Root Tokens",
        "Subagent Tokens",
        "Review Tokens",
        "API Cost USD",
        "API Equivalent RUB",
        "Unpriced Tokens",
    ]

    ws_weekly.append(weekly_headers)

    for key in sorted(weekly):
        week_start, week_label, project = key
        bucket = weekly[key]
        cost = weekly_cost[key]

        ws_weekly.append([
            week_start,
            week_label,
            clean_excel_text(project),
            bucket.calls,
            bucket.total_tokens,
            bucket.input_tokens,
            bucket.cached_input_tokens,
            bucket.uncached_input_tokens,
            bucket.output_tokens,
            bucket.reasoning_tokens,
            bucket.cache_hit,
            weekly_role_tokens[key]["ROOT"],
            weekly_role_tokens[key]["SUBAGENT"],
            weekly_role_tokens[key]["REVIEW"],
            cost["usd"],
            cost["rub"],
            cost["unpriced_tokens"],
        ])

    for row_number in range(
        2,
        ws_weekly.max_row + 1,
    ):
        ws_weekly.cell(
            row_number,
            1,
        ).number_format = "yyyy-mm-dd"

        ws_weekly.cell(
            row_number,
            11,
        ).number_format = "0.0%"

    token_format(
        ws_weekly,
        [
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            12,
            13,
            14,
            17,
        ],
    )

    style_sheet(
        ws_weekly,
        "WeeklyTable",
    )

    # -----------------------
    # MONTHLY
    # -----------------------

    monthly = defaultdict(UsageBucket)

    monthly_role_tokens = defaultdict(
        lambda: {
            "ROOT": 0,
            "SUBAGENT": 0,
            "REVIEW": 0,
        }
    )
    monthly_cost = defaultdict(
        lambda: {
            "usd": 0.0,
            "rub": 0.0,
            "unpriced_tokens": 0,
        }
    )

    for row in model_rows:
        day = datetime.strptime(
            row["date"],
            "%Y-%m-%d",
        ).date()

        month_start = day.replace(day=1)
        month_label = day.strftime("%Y-%m")

        key = (
            month_start,
            month_label,
            row["project"],
        )

        bucket = monthly[key]

        bucket.calls += row["calls"]
        bucket.total_tokens += row["total"]
        bucket.input_tokens += row["input"]
        bucket.cached_input_tokens += row["cached"]
        bucket.uncached_input_tokens += row["uncached"]
        bucket.output_tokens += row["output"]
        bucket.reasoning_tokens += row["reasoning"]

        monthly_role_tokens[key][
            row["role"]
        ] += row["total"]
        monthly_cost[key]["usd"] += row["cost_usd"]
        monthly_cost[key]["rub"] += row["cost_rub"]

        if not row["priced"]:
            monthly_cost[key]["unpriced_tokens"] += row["total"]

    ws_monthly = wb.create_sheet("Monthly")

    monthly_headers = [
        "Month Start",
        "Month",
        "Project",
        "Calls",
        "Total Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "Root Tokens",
        "Subagent Tokens",
        "Review Tokens",
        "API Cost USD",
        "API Equivalent RUB",
        "Unpriced Tokens",
    ]

    ws_monthly.append(monthly_headers)

    for key in sorted(monthly):
        month_start, month_label, project = key
        bucket = monthly[key]
        cost = monthly_cost[key]
        
        ws_monthly.append([
            month_start,
            month_label,
            clean_excel_text(project),
            bucket.calls,
            bucket.total_tokens,
            bucket.input_tokens,
            bucket.cached_input_tokens,
            bucket.uncached_input_tokens,
            bucket.output_tokens,
            bucket.reasoning_tokens,
            bucket.cache_hit,
            monthly_role_tokens[key]["ROOT"],
            monthly_role_tokens[key]["SUBAGENT"],
            monthly_role_tokens[key]["REVIEW"],
            cost["usd"],
            cost["rub"],
            cost["unpriced_tokens"],
        ])

    for row_number in range(
        2,
        ws_monthly.max_row + 1,
    ):
        ws_monthly.cell(
            row_number,
            1,
        ).number_format = "yyyy-mm"

        ws_monthly.cell(
            row_number,
            11,
        ).number_format = "0.0%"
        
        ws_weekly.cell(
            row_number,
            15,
        ).number_format = '$#,##0.00'

        ws_weekly.cell(
            row_number,
            16,
        ).number_format = '#,##0.00 "₽"'

    token_format(
        ws_monthly,
        [
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            12,
            13,
            14,
            17,
        ],
    )

    style_sheet(
        ws_monthly,
        "MonthlyTable",
    )

    # -----------------------
    # PROJECTS
    # -----------------------

    projects = defaultdict(UsageBucket)

    project_cost = defaultdict(
        lambda: {
            "usd": 0.0,
            "rub": 0.0,
            "unpriced_tokens": 0,
        }
    )

    for row in model_rows:
        bucket = projects[row["project"]]

        bucket.calls += row["calls"]
        bucket.total_tokens += row["total"]
        bucket.input_tokens += row["input"]
        bucket.cached_input_tokens += row["cached"]
        bucket.uncached_input_tokens += row["uncached"]
        bucket.output_tokens += row["output"]
        bucket.reasoning_tokens += row["reasoning"]
        project = row["project"]

        project_cost[project]["usd"] += row["cost_usd"]
        project_cost[project]["rub"] += row["cost_rub"]

        if not row["priced"]:
            project_cost[project]["unpriced_tokens"] += row["total"]

    ws_projects = wb.create_sheet("Projects")

    ws_projects.append([
        "Project",
        "Calls",
        "Total Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "API Cost USD",
        "API Equivalent RUB",
        "Unpriced Tokens",
    ])

    for project, bucket in sorted(
        projects.items(),
        key=lambda item: item[1].total_tokens,
        reverse=True,
    ):
        cost = project_cost[project]
        
        ws_projects.append([
            clean_excel_text(project),
            bucket.calls,
            bucket.total_tokens,
            bucket.input_tokens,
            bucket.cached_input_tokens,
            bucket.uncached_input_tokens,
            bucket.output_tokens,
            bucket.reasoning_tokens,
            bucket.cache_hit,
            cost["usd"],
            cost["rub"],
            cost["unpriced_tokens"],
        ])

    token_format(
        ws_projects,
        [2, 3, 4, 5, 6, 7, 8, 12],
    )

    for row in range(2, ws_projects.max_row + 1):
        ws_projects.cell(row, 9).number_format = "0.0%"
        ws_projects.cell(row, 10).number_format = '$#,##0.00'
        ws_projects.cell(row, 11).number_format = '#,##0.00 "₽"'
        ws_monthly.cell(
            row_number,
            15,
        ).number_format = '$#,##0.00'

        ws_monthly.cell(
            row_number,
            16,
        ).number_format = '#,##0.00 "₽"'

    style_sheet(ws_projects, "ProjectsTable")

    # -----------------------
    # TASKS
    # -----------------------

    ws_tasks = wb.create_sheet("Tasks")

    task_headers = [
        "Project",
        "First Activity",
        "Last Activity",
        "Title",
        "Root Model",
        "Root Effort",
        "Children",
        "Active Days",
        "Duration Hours",
        "Direct Calls",
        "Subagent Calls",
        "Review Calls",
        "Full Calls",
        "Direct Tokens",
        "Subagent Tokens",
        "Review Tokens",
        "Full Tokens",
        "Input Tokens",
        "Cached Input",
        "Uncached Input",
        "Output Tokens",
        "Reasoning Tokens",
        "Cache Hit %",
        "Direct Cost RUB",
        "Subagent Cost RUB",
        "Review Cost RUB",
        "API Equivalent RUB",
        "API Cost USD",
        "Unpriced Tokens",
        "Root Thread ID",
    ]

    ws_tasks.append(task_headers)

    for row in task_rows:
        ws_tasks.append([
            clean_excel_text(row["project"]),
            row["first_activity"].replace(tzinfo=None),
            row["last_activity"].replace(tzinfo=None),
            clean_excel_text(
                row["title"],
                max_length=500,
            ),
            clean_excel_text(row["root_model"]),
            clean_excel_text(row["root_effort"]),
            row["children"],
            row["active_days"],
            row["duration_hours"],
            row["direct_calls"],
            row["subagent_calls"],
            row["review_calls"],
            row["full_calls"],
            row["direct_tokens"],
            row["subagent_tokens"],
            row["review_tokens"],
            row["full_tokens"],
            row["input_tokens"],
            row["cached_tokens"],
            row["uncached_tokens"],
            row["output_tokens"],
            row["reasoning_tokens"],
            row["cache_hit"],

            row["direct_cost_rub"],
            row["subagent_cost_rub"],
            row["review_cost_rub"],
            row["full_cost_rub"],
            row["full_cost_usd"],
            row["unpriced_tokens"],

            clean_excel_text(row["root_thread_id"]),
        ])

    # Date / time.
    for row_number in range(2, ws_tasks.max_row + 1):
        ws_tasks.cell(
            row_number,
            24,
        ).number_format = '#,##0.00 "₽"'

        ws_tasks.cell(
            row_number,
            25,
        ).number_format = '#,##0.00 "₽"'

        ws_tasks.cell(
            row_number,
            26,
        ).number_format = '#,##0.00 "₽"'

        ws_tasks.cell(
            row_number,
            27,
        ).number_format = '#,##0.00 "₽"'

        ws_tasks.cell(
            row_number,
            28,
        ).number_format = '$#,##0.00'

        ws_tasks.cell(
            row_number,
            29,
        ).number_format = "#,##0"

    # Integer/token columns.
    token_format(
        ws_tasks,
        [
            7,
            8,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21,
            22,
        ],
    )

    style_sheet(ws_tasks, "TasksTable")

    # Titles need more room.
    ws_tasks.column_dimensions["D"].width = 55
    ws_tasks.column_dimensions["AD"].width = 38

    wb.save(output)

    return output