"""Fetch ERCOT reports into the raw cache, one (report, operating day) at a time.

Routes, tried in order for each report until every requested day is covered:

1. `ArchiveRoute`: the historical (ER) yearly files on ERCOT MIS. No key needed.
   ERCOT updates them weekly, so they stop about a week before today.
2. `LiveRoute`: the live (CD) report's documents on ERCOT MIS. No key needed.
   MIS keeps about 7 days of real-time documents and about 30 days of DAM/ASDC ones.
3. `ApiRoute`: the ERCOT Public API archive. Used only when the ERCOT_API_*
   credentials are set in the environment.

Every route turns its source into rows with the live report's CSV columns, so
the normalizer never needs to know which route fetched a day.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from harness.market.catalog import (
    AS_CAPABILITY,
    CPT,
    DAM_MCPC,
    MCPC_15MIN,
    PRODUCTS,
    REPORTS,
    SCED_MCPC,
    SPP,
    Report,
)
from harness.market.raw import RawCache

log = logging.getLogger(__name__)

MIS_LIST_URL = "https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId={}"
MIS_DOWNLOAD_URL = "https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={}"

API_ENV_VARS = ("ERCOT_API_USERNAME", "ERCOT_API_PASSWORD", "ERCOT_PUBLIC_API_SUBSCRIPTION_KEY")

DayRows = dict[dt.date, tuple[pd.DataFrame, dict[str, Any]]]


# --------------------------------------------------------------------------- MIS


@dataclass(frozen=True)
class Doc:
    doc_id: str
    name: str
    published: pd.Timestamp


class MisClient:
    """ERCOT MIS document lists and downloads (public, no key)."""

    def __init__(self, timeout: float = 180.0):
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(total=5, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504))
        self.session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))

    def list_docs(self, report_type_id: int) -> list[Doc]:
        resp = self.session.get(MIS_LIST_URL.format(report_type_id), timeout=self.timeout)
        resp.raise_for_status()
        entries = resp.json()["ListDocsByRptTypeRes"]["DocumentList"]
        docs = [
            Doc(
                doc_id=str(e["Document"]["DocID"]),
                name=e["Document"]["FriendlyName"],
                published=pd.Timestamp(e["Document"]["PublishDate"]).tz_convert(CPT),
            )
            for e in entries
        ]
        return sorted(docs, key=lambda d: d.published)

    def download(self, doc: Doc) -> bytes:
        resp = self.session.get(MIS_DOWNLOAD_URL.format(doc.doc_id), timeout=self.timeout)
        resp.raise_for_status()
        return resp.content


# ------------------------------------------------------------------ raw parsing


def _unzip_one(data: bytes) -> tuple[str, io.BytesIO]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = z.namelist()[0]
        return name, io.BytesIO(z.read(name))


def read_live_csv(data: bytes) -> pd.DataFrame:
    _, buf = _unzip_one(data)
    return pd.read_csv(buf, dtype=str, keep_default_na=False, na_values=[""])


def _text_timestamps(values: pd.Series) -> pd.Series:
    ts = pd.to_datetime(values, format="mixed")
    return ts.dt.round("s").dt.strftime("%m/%d/%Y %H:%M:%S")


def _text_dates(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, format="mixed").dt.strftime("%m/%d/%Y")


def _text_ints(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values).astype("Int64").astype(str)


def _read_workbook_rows(data: bytes, header_first_cells: set[str]) -> pd.DataFrame:
    """Concatenate the data rows of every sheet that has a header row.

    ERCOT's historical workbooks have one sheet per month, a title block above the
    header row, and sometimes a footer line below the data. The "Report Info"
    sheet describes the columns and is skipped.
    """
    name, buf = _unzip_one(data)
    if name.lower().endswith(".csv"):
        df = pd.read_csv(buf, dtype=str, keep_default_na=False, na_values=[""])
        return df.rename(columns=str.strip)  # e.g. "REGUP " in NP4-181-ER
    sheets = pd.read_excel(buf, sheet_name=None, header=None)
    frames = []
    for sheet_name, sheet in sheets.items():
        if str(sheet_name).strip().lower() == "report info":
            continue
        first = sheet.iloc[:, 0].astype(str).str.strip()
        hits = np.flatnonzero(first.isin(header_first_cells))
        if len(hits) == 0:
            continue
        pos = int(hits[0])
        body = sheet.iloc[pos + 1 :].copy()
        body.columns = [str(c).strip() for c in sheet.iloc[pos]]
        frames.append(body)
    if not frames:
        raise ValueError("no sheet with a recognised header row")
    return pd.concat(frames, ignore_index=True)


def _archive_sced_mcpc(data: bytes) -> pd.DataFrame:
    df = _read_workbook_rows(data, {"SCED Timestamp"}).rename(columns={
        "SCED Timestamp": "SCEDTimestamp",
        "AS Type": "ASType",
        "Repeated Hour Flag": "RepeatedHourFlag",
        "CAPPED_MCPC": "CappedMCPC",
        "UNCAPPED_MCPC": "UncappedMCPC",
        # The 2025 file predates the capped/uncapped split.
        "MCPC": "CappedMCPC",
    })
    if "UncappedMCPC" not in df:
        df["UncappedMCPC"] = pd.NA
    df = df.dropna(subset=["SCEDTimestamp", "ASType", "RepeatedHourFlag"])
    df["SCEDTimestamp"] = _text_timestamps(df["SCEDTimestamp"])
    return df


def _archive_15min_mcpc(data: bytes) -> pd.DataFrame:
    df = _read_workbook_rows(data, {"Delivery Date"}).rename(columns={
        "Delivery Date": "DeliveryDate",
        "Delivery Hour": "DeliveryHour",
        "Delivery Interval": "DeliveryInterval",
        "AS Type": "ASType",
        "Repeated Hour Flag": "RepeatedHourFlag",
    })
    df = df.dropna(subset=["DeliveryDate", "DeliveryHour", "DeliveryInterval", "ASType"])
    df["DeliveryDate"] = _text_dates(df["DeliveryDate"])
    df["DeliveryHour"] = _text_ints(df["DeliveryHour"])
    df["DeliveryInterval"] = _text_ints(df["DeliveryInterval"])
    return df


def _archive_capability(data: bytes) -> pd.DataFrame:
    df = _read_workbook_rows(data, {"SCEDTimestamp"})
    df = df.dropna(subset=["SCEDTimestamp", "RepeatedHourFlag"])
    df["SCEDTimestamp"] = _text_timestamps(df["SCEDTimestamp"])
    return df


def _archive_spp(data: bytes) -> pd.DataFrame:
    df = _read_workbook_rows(data, {"Delivery Date"}).rename(columns={
        "Delivery Date": "DeliveryDate",
        "Delivery Hour": "DeliveryHour",
        "Delivery Interval": "DeliveryInterval",
        "Repeated Hour Flag": "DSTFlag",
        "Settlement Point Name": "SettlementPointName",
        "Settlement Point Type": "SettlementPointType",
        "Settlement Point Price": "SettlementPointPrice",
    })
    df = df.dropna(subset=["DeliveryDate", "DeliveryHour", "DeliveryInterval", "SettlementPointName"])
    df["DeliveryDate"] = _text_dates(df["DeliveryDate"])
    df["DeliveryHour"] = _text_ints(df["DeliveryHour"])
    df["DeliveryInterval"] = _text_ints(df["DeliveryInterval"])
    return df


def _archive_dam_mcpc(data: bytes) -> pd.DataFrame:
    wide = _read_workbook_rows(data, {"Delivery Date"}).rename(columns={
        "Delivery Date": "DeliveryDate",
        "Hour Ending": "HourEnding",
        "Repeated Hour Flag": "DSTFlag",
    })
    products = [c for c in wide.columns if c in PRODUCTS]
    df = wide.melt(
        id_vars=["DeliveryDate", "HourEnding", "DSTFlag"],
        value_vars=products,
        var_name="AncillaryType",
        value_name="MCPC",
    )
    df = df.dropna(subset=["DeliveryDate", "HourEnding"])
    df["DeliveryDate"] = _text_dates(df["DeliveryDate"])
    return df


ARCHIVE_PARSERS = {
    SCED_MCPC.id: _archive_sced_mcpc,
    MCPC_15MIN.id: _archive_15min_mcpc,
    AS_CAPABILITY.id: _archive_capability,
    SPP.id: _archive_spp,
    DAM_MCPC.id: _archive_dam_mcpc,
}


def _to_raw_schema(report: Report, df: pd.DataFrame) -> pd.DataFrame:
    """Bring a parsed source frame to the live report's columns."""
    df = df.copy()
    if report is SCED_MCPC and "CappedMCPC" not in df and "MCPC" in df:
        df = df.rename(columns={"MCPC": "CappedMCPC"})
    if report is SPP:
        # The historical report carries load zones and hubs only; match it.
        df = df[df["SettlementPointName"].str.startswith(("LZ_", "HB_"))]
    for col in report.columns:
        if col not in df:
            df[col] = pd.NA
    return df.loc[:, list(report.columns)]


def row_days(report: Report, df: pd.DataFrame) -> pd.Series:
    """Operating day of each raw row."""
    text = df[report.day_column].astype(str).str.slice(0, 10)
    return pd.to_datetime(text, format="%m/%d/%Y").dt.date


# ----------------------------------------------------------------------- routes


class Route(Protocol):
    name: str

    def fetch(self, report: Report, days: list[dt.date]) -> DayRows: ...


def _split_days(report: Report, df: pd.DataFrame, days: list[dt.date], meta: dict[str, Any]) -> DayRows:
    df = _to_raw_schema(report, df)
    df = df.drop_duplicates(subset=list(report.keys), keep="last")
    by_day = row_days(report, df)
    out: DayRows = {}
    for day in days:
        rows = df[by_day == day]
        if len(rows):
            out[day] = (rows.reset_index(drop=True), {**meta, "rows": len(rows)})
    return out


@dataclass
class ArchiveRoute:
    """Historical (ER) yearly files on MIS."""

    mis: MisClient
    downloads: Path
    name: str = "mis-archive"

    def fetch(self, report: Report, days: list[dt.date]) -> DayRows:
        if report.archive_type_id is None or not days:
            return {}
        by_year: dict[int, Doc] = {}
        for listed in self.mis.list_docs(report.archive_type_id):
            m = re.search(r"_(\d{4})$", listed.name)
            if m:
                by_year[int(m.group(1))] = listed  # docs are sorted; keep the latest
        out: DayRows = {}
        for year in sorted({d.year for d in days}):
            doc = by_year.get(year)
            if doc is None:
                continue
            # Updated weekly for the previous week: nothing on or after the post date.
            wanted = [d for d in days if d.year == year and d < doc.published.date()]
            if not wanted:
                continue
            log.info("%s: reading %s (%s) for %d day(s)", report.id, doc.name, report.archive_id, len(wanted))
            rows = ARCHIVE_PARSERS[report.id](self._download(report, year, doc))
            meta = {"route": self.name, "report": report.archive_id, "document": doc.name,
                    "published": doc.published.isoformat()}
            out.update(_split_days(report, rows, wanted, meta))
        return out

    def _download(self, report: Report, year: int, doc: Doc) -> bytes:
        self.downloads.mkdir(parents=True, exist_ok=True)
        path = self.downloads / f"{report.archive_id}_{year}_{doc.doc_id}.zip"
        if path.exists():
            return path.read_bytes()
        data = self.mis.download(doc)
        for stale in self.downloads.glob(f"{report.archive_id}_{year}_*.zip"):
            stale.unlink()
        path.write_bytes(data)
        return data


def _publication_window(report: Report, day: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(day).tz_localize(CPT)
    end = pd.Timestamp(day + dt.timedelta(days=1)).tz_localize(CPT)
    if report.daily_documents:
        # DAM prices and ASDCs for a day are posted the day before.
        return start - pd.Timedelta(days=1), end
    # A settlement interval's document posts a few minutes after it ends.
    return start, end + pd.Timedelta(minutes=30)


def _earliest_publication_rows(
    report: Report, docs: list[tuple[pd.Timestamp, pd.DataFrame]], day: dt.date
) -> pd.DataFrame | None:
    """Rows for `day` from the first document that carries it.

    Using the first publication keeps DAM prices and ASDCs to what was known
    before the operating day started.
    """
    for _, df in sorted(docs, key=lambda x: x[0]):
        rows = df[row_days(report, df) == day]
        if len(rows):
            return rows
    return None


@dataclass
class LiveRoute:
    """Live (CD) report documents on MIS, one per SCED run or interval."""

    mis: MisClient
    workers: int = 8
    name: str = "mis-live"

    def fetch(self, report: Report, days: list[dt.date]) -> DayRows:
        if not days:
            return {}
        docs = [d for d in self.mis.list_docs(report.live_type_id) if d.name.endswith("_csv")]
        if not docs:
            return {}
        oldest = docs[0].published
        now = pd.Timestamp.now(tz=CPT)
        wanted: dict[dt.date, list[Doc]] = {}
        for day in days:
            start, end = _publication_window(report, day)
            if not report.daily_documents and (start < oldest or end > now):
                continue  # the day's first documents rolled off MIS, or its last aren't posted yet
            in_window = [d for d in docs if start <= d.published < end]
            if in_window:
                wanted[day] = in_window
        if not wanted:
            return {}
        unique = {d.doc_id: d for ds in wanted.values() for d in ds}
        log.info("%s: downloading %d live document(s) for %d day(s)", report.id, len(unique), len(wanted))
        with ThreadPoolExecutor(self.workers) as pool:
            frames = dict(zip(unique, pool.map(lambda d: read_live_csv(self.mis.download(d)), unique.values())))
        out: DayRows = {}
        for day, ds in wanted.items():
            meta = {"route": self.name, "report": report.id, "documents": len(ds),
                    "first_published": ds[0].published.isoformat(),
                    "last_published": ds[-1].published.isoformat()}
            parsed = [(d.published, _to_raw_schema(report, frames[d.doc_id])) for d in ds]
            if report.daily_documents:
                rows = _earliest_publication_rows(report, parsed, day)
                if rows is not None:
                    out.update(_split_days(report, rows, [day], meta))
            else:
                out.update(_split_days(report, pd.concat([df for _, df in parsed]), [day], meta))
        return out


@dataclass
class ApiRoute:
    """ERCOT Public API archive. Needs ERCOT_API_USERNAME, ERCOT_API_PASSWORD and
    ERCOT_PUBLIC_API_SUBSCRIPTION_KEY in the environment; never creates accounts.
    """

    name: str = "ercot-api"
    _client: Any = field(default=None, repr=False)

    @staticmethod
    def available() -> bool:
        return all(os.environ.get(v) for v in API_ENV_VARS)

    def fetch(self, report: Report, days: list[dt.date]) -> DayRows:
        if not days:
            return {}
        if self._client is None:
            from gridstatus.ercot_api.ercot_api import ErcotAPI

            self._client = ErcotAPI()  # reads the ERCOT_API_* variables
        start, _ = _publication_window(report, min(days))
        _, end = _publication_window(report, max(days))
        df = self._client.get_historical_data(
            endpoint=f"/{report.id.lower()}/",
            start_date=start,
            end_date=end,
            add_post_datetime=True,
        )
        if df is None or df.empty:
            return {}
        df = df.astype({c: str for c in df.columns if c != "postDatetime"})
        meta = {"route": self.name, "report": report.id}
        if not report.daily_documents:
            return _split_days(report, df, days, meta)
        docs = [(pd.Timestamp(p), g) for p, g in df.groupby("postDatetime")]
        out: DayRows = {}
        for day in days:
            rows = _earliest_publication_rows(report, docs, day)
            if rows is not None:
                out.update(_split_days(report, rows, [day], meta))
        return out


# ---------------------------------------------------------------------- fetcher


@dataclass
class FetchResult:
    fetched: dict[str, list[dt.date]]
    uncovered: dict[str, list[dt.date]]
    errors: list[str]


def default_routes(raw: RawCache) -> list[Route]:
    mis = MisClient()
    routes: list[Route] = [ArchiveRoute(mis, raw.root / "_downloads"), LiveRoute(mis)]
    if ApiRoute.available():
        routes.append(ApiRoute())
    else:
        log.info("ERCOT API credentials not set (%s); using key-free MIS routes only",
                 ", ".join(API_ENV_VARS))
    return routes


def fetch_missing(
    raw: RawCache,
    days: list[dt.date],
    routes: list[Route],
    reports: tuple[Report, ...] = REPORTS,
    refetch: bool = False,
) -> FetchResult:
    """Fill the raw cache for `days`, fetching only (report, day) pairs it lacks."""
    fetched: dict[str, list[dt.date]] = {}
    uncovered: dict[str, list[dt.date]] = {}
    errors: list[str] = []
    for report in reports:
        missing = [d for d in days if refetch or not raw.has(report, d)]
        got: list[dt.date] = []
        for route in routes:
            if not missing:
                break
            try:
                rows = route.fetch(report, missing)
            except Exception as exc:  # a failed route must not stop the others
                errors.append(f"{report.id} via {route.name}: {exc}")
                log.warning("%s via %s failed: %s", report.id, route.name, exc)
                continue
            for day, (df, meta) in sorted(rows.items()):
                raw.write(report, day, df, meta)
                got.append(day)
            missing = [d for d in missing if d not in rows]
        fetched[report.id] = sorted(got)
        uncovered[report.id] = missing
    return FetchResult(fetched, uncovered, errors)
