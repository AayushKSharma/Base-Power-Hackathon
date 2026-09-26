"""The ERCOT Public API route, against a recorded NP4-212-CD archive slice.

The rows are from the archive document posted 2025-12-05 04:56 CT (doc 1167941552):
ECRS, hour ending 01:00, the first three curve points. Point 3 is filed on a
later post so the test can see which publication was kept.
"""

import datetime as dt
from pathlib import Path

import pandas as pd

from harness.market.catalog import ASDC
from harness.market.sources import ApiRoute

FIXTURE = Path(__file__).parent / "fixtures" / "api" / "np4-212-cd.csv"
RTC_B = dt.date(2025, 12, 5)


class RecordedArchive:
    """get_historical_data stand-in. Returns the recorded frame for any window."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def get_historical_data(self, endpoint, start_date, end_date, add_post_datetime=False):
        assert endpoint == "/np4-212-cd/"
        assert add_post_datetime
        return self.frame.copy()


class EmptyWindowThenRecorded(RecordedArchive):
    """The archive listing has no `archives` key when a time slice is empty."""

    def get_historical_data(self, endpoint, start_date, end_date, add_post_datetime=False):
        if pd.Timestamp(start_date).day == 4:
            raise KeyError("archives")
        return super().get_historical_data(endpoint, start_date, end_date, add_post_datetime)


class OneTransientFailure(RecordedArchive):
    """The first request for the early-morning slice fails once; later slices are empty."""

    def __init__(self, frame: pd.DataFrame):
        super().__init__(frame)
        self.failed = False

    def get_historical_data(self, endpoint, start_date, end_date, add_post_datetime=False):
        start = pd.Timestamp(start_date)
        if start.day == 4 and start.hour == 0:
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary")
            return super().get_historical_data(endpoint, start_date, end_date, add_post_datetime)
        raise KeyError("archives")


def test_api_route_retries_a_failed_archive_slice_for_the_recorded_day():
    route = ApiRoute(_client=OneTransientFailure(pd.read_csv(FIXTURE)))

    out = route.fetch(ASDC, [RTC_B])

    assert out[RTC_B][0]["Price"].tolist() == ["5050.0", "5050.0"]


def test_api_route_skips_an_empty_archive_window_and_keeps_the_recorded_day():
    route = ApiRoute(_client=EmptyWindowThenRecorded(pd.read_csv(FIXTURE)))

    out = route.fetch(ASDC, [RTC_B])

    rows, _ = out[RTC_B]
    assert rows["Price"].tolist() == ["5050.0", "5050.0"]


def test_api_route_keeps_the_earliest_recorded_asdc_publication():
    route = ApiRoute(_client=RecordedArchive(pd.read_csv(FIXTURE)))

    out = route.fetch(ASDC, [RTC_B])

    rows, meta = out[RTC_B]
    assert meta["route"] == "ercot-api"
    assert meta["report"] == "NP4-212-CD"
    assert list(rows.columns) == list(ASDC.columns)
    # Points 1 and 2 are the 04:56 publication. Point 3 exists only on the later post.
    assert rows[["DemandCurvePoint", "Quantity", "Price"]].values.tolist() == [
        ["1", "0", "5050.0"],
        ["2", "40", "5050.0"],
    ]
