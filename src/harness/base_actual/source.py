"""Fetch the Aggregate Load Resource rows of ERCOT's 60-Day SCED Disclosure (NP3-965-ER).

ERCOT posts one zip per report date on MIS, 60 days after the operating day
(report date = operating day + 60 days). It holds, among other tables,
`60d_Load_Resource_Data_in_SCED-<DD-MON-YY>.csv` (about 150k rows: every load
resource at every SCED run). We keep only Aggregate Load Resource (ALR) rows,
all columns as published, in the raw cache.

ERCOT has reissued some days. For each operating day the fetcher takes, in order:
1. the latest load-resource SUPPLEMENTAL zip whose report-date range covers it
   (for example 60d_Load_Resource_Data_in_SCED_02032026_thru_03062026_SUPPLEMENTAL);
2. a 60_Day_SCED_Disclosure_CORRECTION zip posted on the report date;
3. the regular 60_Day_SCED_Disclosure zip posted on the report date.
MIS keeps these back to 2024, so every post-RTC+B day is reachable without a key.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pandas as pd

from harness.market.catalog import Report
from harness.market.sources import DayRows, Doc, MisClient, row_days

log = logging.getLogger(__name__)

PUBLICATION_LAG = dt.timedelta(days=60)
BASE_QSE = "QBASTX"  # Base Texas QSE
# ERCOT names Aggregate Load Resources <SITE>_ALD<n>; the file has no resource-type column.
ALR_NAME = re.compile(r"_ALD\d+$")

RAW_COLUMNS: tuple[str, ...] = (
    "SCED Time Stamp", "Repeated Hour Flag", "QSE", "DME", "Resource Name",
    "Telemetered Resource Status", "Max Power Consumption", "Low Power Consumption",
    "Real Power Consumption",
    "AS Awards NSPIN", "AS Awards RRSFFR", "AS Awards RRSPFR", "AS Awards RRSUFR",
    "AS Awards ECRS", "AS Awards REGUP", "AS Awards REGDN",
    *(f"SCED Bid to Buy Curve-{kind}{i}" for i in range(1, 11) for kind in ("MW", "Price")),
    "Self Provided RRSFFR", "Self Provided RRSUFR", "Self Provided ECRS",
    "Ramp Rate Up", "Ramp Rate Down",
    "AS Capability NSPIN", "AS Capability ECRS", "AS Capability REGUP", "AS Capability REGDN",
    "AS Capability RRSPFR", "AS Capability RRSFFR", "AS Capability RRSUFR",
    "HDL", "LDL", "Base Point",
)

LOAD_RESOURCES = Report(
    id="NP3-965-ER",
    title="60-Day SCED Disclosure: Load Resource Data in SCED (ALR rows only)",
    columns=RAW_COLUMNS,
    keys=("SCED Time Stamp", "Repeated Hour Flag", "Resource Name"),
    day_column="SCED Time Stamp",
    live_type_id=13052,
    daily_documents=True,
)

REGULAR = "60_Day_SCED_Disclosure"
CORRECTION = "60_Day_SCED_Disclosure_CORRECTION"
SUPPLEMENTAL = re.compile(r"^60d_Load_Resource_Data_in_SCED_(\d{8})_thru_(\d{8})_SUPPLEMENTAL$")


def report_date(day: dt.date) -> dt.date:
    return day + PUBLICATION_LAG


def latest_published_day(today: dt.date) -> dt.date:
    """The newest operating day whose disclosure is due by `today`."""
    return today - PUBLICATION_LAG


def is_alr(rows: pd.DataFrame) -> pd.Series:
    return rows["Resource Name"].str.contains(ALR_NAME) | (rows["QSE"] == BASE_QSE)


def _member_name(day: dt.date) -> str:
    return "60d_Load_Resource_Data_in_SCED-" + report_date(day).strftime("%d-%b-%y").upper() + ".csv"


def _covers(doc: Doc) -> tuple[int, dt.date, dt.date] | None:
    """(priority, first report date, last report date) a document can supply."""
    if doc.name == REGULAR:
        return 0, doc.published.date(), doc.published.date()
    if doc.name == CORRECTION:
        return 1, doc.published.date(), doc.published.date()
    m = SUPPLEMENTAL.match(doc.name)
    if m:
        first, last = (dt.datetime.strptime(g, "%m%d%Y").date() for g in m.groups())
        return 2, first, last
    return None


def choose_documents(docs: list[Doc], days: list[dt.date]) -> dict[dt.date, Doc]:
    """The best document for each operating day that has one."""
    best: dict[dt.date, tuple[int, pd.Timestamp, Doc]] = {}
    for doc in docs:
        cover = _covers(doc)
        if cover is None:
            continue
        priority, first, last = cover
        for day in days:
            if first <= report_date(day) <= last:
                current = best.get(day)
                if current is None or (priority, doc.published) > current[:2]:
                    best[day] = (priority, doc.published, doc)
    return {day: doc for day, (_, _, doc) in best.items()}


@dataclass
class DisclosureRoute:
    """NP3-965-ER load-resource tables from MIS, one zip download per document."""

    mis: MisClient
    workers: int = 3
    name: str = "mis-60day"

    def fetch(self, report: Report, days: list[dt.date]) -> DayRows:
        if not days:
            return {}
        chosen = choose_documents(self.mis.list_docs(report.live_type_id), days)
        by_doc: dict[str, tuple[Doc, list[dt.date]]] = {}
        for day, doc in chosen.items():
            by_doc.setdefault(doc.doc_id, (doc, []))[1].append(day)
        log.info("%s: downloading %d document(s) for %d day(s)", report.id, len(by_doc), len(chosen))
        out: DayRows = {}
        with ThreadPoolExecutor(self.workers) as pool:
            for rows in pool.map(lambda item: self._extract(report, *item), by_doc.values()):
                out.update(rows)
        return out

    def _extract(self, report: Report, doc: Doc, days: list[dt.date]) -> DayRows:
        try:
            data = self.mis.download(doc)
        except Exception as exc:  # one failed download must not lose the other days
            log.warning("%s: downloading %s failed (%s); its day(s) stay unavailable", report.id, doc.name, exc)
            return {}
        out: DayRows = {}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = set(z.namelist())
            for day in sorted(days):
                member = _member_name(day)
                if member not in members:
                    log.warning("%s: %s has no %s", report.id, doc.name, member)
                    continue
                with z.open(member) as f:
                    rows = pd.read_csv(f, dtype=str, keep_default_na=False, na_values=[""])
                rows = rows[is_alr(rows)]
                rows = rows[row_days(report, rows) == day].reindex(columns=list(report.columns))
                if rows.empty:
                    log.warning("%s: %s holds no ALR rows for %s", report.id, member, day)
                    continue
                out[day] = (rows.reset_index(drop=True), {
                    "route": self.name, "report": report.id, "document": doc.name,
                    "member": member, "published": doc.published.isoformat(), "rows": len(rows),
                })
        log.info("%s: %s -> %d day(s)", report.id, doc.name, len(out))
        return out
