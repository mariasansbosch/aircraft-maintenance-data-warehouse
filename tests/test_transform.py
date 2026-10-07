"""Unit tests for the business rules in transform.py (no database needed)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transform  # noqa: E402


def _flight(aircraft, dep, arr, sched_arr=None, cancelled=False):
    return {
        "aircraft_id": aircraft,
        "date_id": pd.Timestamp(dep).date(),
        "actualdeparture": pd.Timestamp(dep),
        "actualarrival": pd.Timestamp(arr),
        "scheduledarrival": pd.Timestamp(sched_arr or arr),
        "cancelled": cancelled,
    }


def test_br23_swaps_times_and_flight_hours_are_positive():
    df = pd.DataFrame([_flight("XY-AAA", "2024-01-01 12:00", "2024-01-01 10:00")])
    out = transform.transform_flights(df)
    assert out.loc[0, "flight_hours"] == 2.0


def test_br21_drops_the_earlier_overlapping_flight():
    df = pd.DataFrame([
        _flight("XY-AAA", "2024-01-01 08:00", "2024-01-01 11:00"),  # earlier -> dropped
        _flight("XY-AAA", "2024-01-01 10:00", "2024-01-01 12:00"),
        _flight("XY-BBB", "2024-01-01 10:00", "2024-01-01 12:00"),  # other aircraft
    ])
    out = transform.transform_flights(df).set_index("aircraft_id")
    assert out.loc["XY-AAA", "takeoff"] == 1
    assert out.loc["XY-AAA", "flight_hours"] == 2.0
    assert out.loc["XY-BBB", "takeoff"] == 1


def test_br21_ignores_cancelled_flights():
    df = pd.DataFrame([
        _flight("XY-AAA", "2024-01-01 08:00", "2024-01-01 11:00"),
        _flight("XY-AAA", "2024-01-01 10:00", "2024-01-01 12:00", cancelled=True),
    ])
    out = transform.transform_flights(df)
    assert out.loc[0, "takeoff"] == 1
    assert out.loc[0, "cancellations"] == 1


def test_delay_measures():
    df = pd.DataFrame([
        _flight("XY-AAA", "2024-01-01 08:00", "2024-01-01 10:30", sched_arr="2024-01-01 10:00"),
        _flight("XY-AAA", "2024-01-01 12:00", "2024-01-01 14:10", sched_arr="2024-01-01 14:00"),
    ])
    out = transform.transform_flights(df)
    assert out.loc[0, "delays"] == 1          # only the 30-min delay counts (> 15 min)
    assert out.loc[0, "delay_minutes"] == 30.0


def test_reports_drop_unknown_aircraft_and_map_airport_for_both_sources():
    aircraft = pd.DataFrame({"aircraft_id": ["XY-AAA"], "model": ["A320"], "manufacturer": ["Airbus"]})
    personnel = pd.DataFrame({"reporter_id": [1], "airport": ["BCN"]})
    common = {"date_id": pd.Timestamp("2024-01-01").date(), "airport": "NULL"}
    pfr = pd.DataFrame([
        {"reporter_id": 1, "aircraft_id": "XY-AAA", "reporter_class": "MAREP", "source": "postflightreport", **common},
        {"reporter_id": 2, "aircraft_id": "XY-ZZZ", "reporter_class": "PIREP", "source": "postflightreport", **common},
    ])
    tlb = pd.DataFrame([
        {"reporter_id": 1, "aircraft_id": "XY-AAA", "reporter_class": "MAREP", "source": "technicallogbook", **common},
    ])
    out = transform.transform_reports(tlb, pfr, aircraft, personnel)
    assert set(out["aircraft_id"]) == {"XY-AAA"}
    assert list(out["airport"]) == ["BCN", "BCN"]
    assert out["report_id"].is_unique
