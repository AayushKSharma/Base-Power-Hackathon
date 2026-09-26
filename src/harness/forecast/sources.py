"""Fetch forecast vintages into the raw cache.

Routes, for each report:

1. `ApiForecastRoute`: the ERCOT Public API archive, filtered by postDatetime.
   Used only when ERCOT_API_USERNAME, ERCOT_API_PASSWORD and
   ERCOT_PUBLIC_API_SUBSCRIPTION_KEY are set. It never creates accounts.
2. `MisForecastRoute`: the live report's documents on ERCOT MIS. No key.
   MIS keeps about a week, which is what a build covers when no key is set.

A vintage already in the cache is not downloaded again.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from harness.forecast.catalog import ForecastInput
from harness.forecast.raw import ForecastRaw, vintage_id
from harness.market.catalog import CPT
from harness.market.sources import API_ENV_VARS, Doc, MisClient, read_live_csv

log = logging.getLogger(__name__)


class MisDocuments(Protocol):
    """The MIS calls the forecast fetcher needs. Tests pass a stand-in."""

    def list_docs(self, report_type_id: int) -> list[Doc]: ...

    def download(self, doc: Doc) -> bytes: ...


@dataclass
class FetchedVintage:
    vintage_id: str
    posted_time: pd.Timestamp
    rows: pd.DataFrame
    meta: dict[str, Any]


class Route(Protocol):
    name: str
    errors: list[str]

    def fetch(self, spec: ForecastInput, start: dt.date, end: dt.date) -> list[FetchedVintage]: ...


def _window(start: dt.date, end: dt.date) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(start).tz_localize(CPT), pd.Timestamp(end + dt.timedelta(days=1)).tz_localize(CPT)


def _posted(value: Any) -> pd.Timestamp:
    """Publication time as UTC. A postDatetime with no offset is Central time,
    which is how the public API and gridstatus record it."""
    posted = pd.Timestamp(value)
    if posted.tzinfo is None:
        posted = posted.tz_localize(CPT, ambiguous=True, nonexistent="shift_forward")
    return posted.tz_convert("UTC")


@dataclass
class MisForecastRoute:
    """Live MIS documents whose publish time falls on a requested posted date."""

    mis: MisDocuments
    raw: ForecastRaw
    refetch: bool = False
    workers: int = 8
    name: str = "mis-live"
    errors: list[str] = field(default_factory=list)

    def fetch(self, spec: ForecastInput, start: dt.date, end: dt.date) -> list[FetchedVintage]:
        try:
            docs = [doc for doc in self.mis.list_docs(spec.live_type_id) if doc.name.endswith("_csv")]
        except Exception as exc:  # a failed list must not stop the other reports
            self.errors.append(f"{spec.report_id} via {self.name}: {exc}")
            log.warning("%s via %s failed: %s", spec.report_id, self.name, exc)
            return []
        opening, closing = _window(start, end)
        wanted: list[Doc] = []
        for doc in docs:
            if not (opening <= doc.published < closing):
                continue
            posted = doc.published.tz_convert("UTC")
            ident = vintage_id(posted, doc.doc_id)
            if self.raw.has(spec.report_id, ident) and not self.refetch:
                continue
            wanted.append(doc)
        if not wanted:
            return []
        log.info("%s: downloading %d MIS document(s)", spec.report_id, len(wanted))
        with ThreadPoolExecutor(self.workers) as pool:
            frames = list(pool.map(self._download, wanted))
        out: list[FetchedVintage] = []
        for doc, frame in zip(wanted, frames):
            if frame is None:
                continue
            posted = doc.published.tz_convert("UTC")
            ident = vintage_id(posted, doc.doc_id)
            meta = {
                "route": self.name,
                "report": spec.report_id,
                "doc_id": doc.doc_id,
                "posted_time": posted.isoformat(),
            }
            out.append(FetchedVintage(ident, posted, frame, meta))
        return out

    def _download(self, doc: Doc) -> pd.DataFrame | None:
        try:
            return read_live_csv(self.mis.download(doc))
        except Exception as exc:
            self.errors.append(f"{doc.doc_id} via {self.name}: {exc}")
            log.warning("MIS document %s failed: %s", doc.doc_id, exc)
            return None


@dataclass
class ApiForecastRoute:
    """Public API archive. One returned postDatetime is one vintage, not merged with another."""

    raw: ForecastRaw
    refetch: bool = False
    name: str = "ercot-api"
    errors: list[str] = field(default_factory=list)
    _client: Any = field(default=None, repr=False)

    @staticmethod
    def available() -> bool:
        return all(os.environ.get(name) for name in API_ENV_VARS)

    def fetch(self, spec: ForecastInput, start: dt.date, end: dt.date) -> list[FetchedVintage]:
        if spec.api_endpoint is None or not self.available():
            return []
        yesterday = pd.Timestamp.now(tz=CPT).date() - dt.timedelta(days=1)
        complete_end = min(end, yesterday)
        fetch_start = start
        if (
            not self.refetch
            and complete_end >= start
            and self.raw.api_covers(spec.report_id, start, complete_end)
        ):
            # Complete days stay cached. Today is still being posted, so refetch it.
            fetch_start = complete_end + dt.timedelta(days=1)
        if fetch_start > end:
            return []
        try:
            frame = self._download(spec, fetch_start, end)
        except Exception as exc:
            self.errors.append(f"{spec.report_id} via {self.name}: {exc}")
            log.warning("%s via %s failed: %s", spec.report_id, self.name, exc)
            return []
        if complete_end >= fetch_start:
            self.raw.mark_api(spec.report_id, fetch_start, complete_end)
        if frame is None or frame.empty:
            return []
        return self._split(spec, frame, start, end)

    def _download(self, spec: ForecastInput, start: dt.date, end: dt.date) -> pd.DataFrame:
        if self._client is None:
            from gridstatus.ercot_api.ercot_api import ErcotAPI

            self._client = ErcotAPI()
        opening, closing = _window(start, end)
        # get_historical_data's end is exclusive and is the postDatetime filter.
        result = self._client.get_historical_data(
            endpoint=spec.api_endpoint,
            start_date=opening,
            end_date=closing,
            add_post_datetime=True,
            include_source_filename=True,
        )
        if not isinstance(result, pd.DataFrame):
            raise TypeError(f"{spec.report_id}: API returned {type(result).__name__}, not a table")
        return result

    def _split(self, spec: ForecastInput, frame: pd.DataFrame, start: dt.date, end: dt.date) -> list[FetchedVintage]:
        posted_col = _find(frame, "postdatetime", "posteddatetime")
        if posted_col is None:
            self.errors.append(f"{spec.report_id} via {self.name}: rows have no postDatetime")
            return []
        file_col = _find(frame, "sourcefilename")
        out: list[FetchedVintage] = []
        grouped = frame.groupby([posted_col] if file_col is None else [posted_col, file_col], dropna=True, sort=True)
        for key, group in grouped:
            if file_col is None:
                # A one-column groupby still yields a 1-tuple key.
                posted_value: Any = key[0] if isinstance(key, tuple) else key
                label = "api"
            else:
                posted_value, label = key[0], str(key[1])
            posted = _posted(posted_value)
            posted_day = posted.tz_convert(CPT).date()
            if posted_day < start or posted_day > end:
                continue
            ident = vintage_id(posted, str(label))
            if self.raw.has(spec.report_id, ident) and not self.refetch:
                continue
            rows = group.drop(columns=[col for col in (posted_col, file_col) if col])
            meta = {"route": self.name, "report": spec.report_id, "posted_time": posted.isoformat(), "doc_id": ident}
            out.append(FetchedVintage(ident, posted, rows, meta))
        return out


def _find(frame: pd.DataFrame, *canons: str) -> str | None:
    import re
    folded = {re.sub(r"[^a-z0-9]", "", column.lower()): column for column in frame.columns}
    for name in canons:
        if name in folded:
            return folded[name]
    return None


def default_routes(raw: ForecastRaw, *, refetch: bool = False) -> list[Route]:
    routes: list[Route] = []
    if ApiForecastRoute.available():
        routes.append(ApiForecastRoute(raw, refetch=refetch))
    else:
        log.info("ERCOT API credentials not set (%s); forecast history is limited to what MIS keeps",
                 ", ".join(API_ENV_VARS))
    routes.append(MisForecastRoute(MisClient(), raw, refetch=refetch))
    return routes
