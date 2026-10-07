import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
from tqdm import tqdm

LOG_FILE = Path(__file__).resolve().parent / "logs.log"

# =====================================================
# Logging configuration
# =====================================================
logging.basicConfig(
    filename=LOG_FILE, level=logging.INFO, format="%(message)s", filemode="w"
)


# =====================================================
# Business Rules Helpers
# =====================================================
def enforce_br23_swap_times(df_flights):
    """
    In a Flight, actualArrival is posterior to actualDeparture, related to BR-23
    (Fix: Swap their values)
    """
    print("Applying BR-23")
    mask = pd.to_datetime(df_flights["actualarrival"]) < pd.to_datetime(
        df_flights["actualdeparture"]
    )
    df_br23 = df_flights[mask]

    if len(df_br23) > 0:
        logging.info(
            f"BR-23: Found {len(df_br23)} flights with arrival before departure on {datetime.now().isoformat()}"
        )
        for _, row in tqdm(
            df_br23.iterrows(),
            total=len(df_br23),
            desc="BR-23: Processing flights with swapped times",
        ):
            logging.info(
                f"BR-23: Swapping times for flight - "
                f"Aircraft: {row['aircraft_id']}, "
                f"Original Departure: {row['actualdeparture']}, "
                f"Original Arrival: {row['actualarrival']}"
            )
        print(f"BR-23: {len(df_br23)} flights swapped")
        df_flights.loc[mask, ["actualdeparture", "actualarrival"]] = df_flights.loc[
            mask, ["actualarrival", "actualdeparture"]
        ].values

    return df_flights


def enforce_br21_remove_overlaps(df_flights):
    """
    Two non-cancelled Flights of the same aircraft cannot overlap, related to BR-21
    (Fix: Ignore the first flight, but record the row in a log file)
    """
    print("Applying BR-21")
    df = df_flights.copy()
    df["actualdeparture"] = pd.to_datetime(df["actualdeparture"])
    df["actualarrival"] = pd.to_datetime(df["actualarrival"])
    df = df.sort_values(
        ["aircraft_id", "actualdeparture", "actualarrival"]
    ).reset_index(drop=True)
    non_cancelled = df[df["cancellations"] == 0]
    to_drop_idx = []

    groups = non_cancelled.groupby("aircraft_id")
    for _, group in tqdm(
        groups, desc="BR-21: Processing aircraft for overlaps", total=groups.ngroups
    ):
        prev_idx, prev_arrival = None, None
        for idx, dep, arr in zip(
            group.index, group["actualdeparture"], group["actualarrival"]
        ):
            if pd.isna(dep) or pd.isna(arr):
                continue
            if prev_arrival is not None and dep < prev_arrival:
                # Overlap detected: ignore the earlier flight
                to_drop_idx.append(prev_idx)
            prev_idx, prev_arrival = idx, arr

    if to_drop_idx:
        logging.info(
            f"BR-21: Found {len(to_drop_idx)} overlapping flights on {datetime.now().isoformat()}"
        )
        for _, row in df.loc[to_drop_idx].iterrows():
            logging.info(
                f"BR-21: Removing overlapping flight - "
                f"Aircraft: {row['aircraft_id']}, "
                f"Departure: {row['actualdeparture']}, "
                f"Arrival: {row['actualarrival']}"
            )
        print(f"BR-21: {len(to_drop_idx)} overlapping flights removed.")

    df = df.drop(index=to_drop_idx).reset_index(drop=True)
    return df


def enforce_br_unmatched_aircraft(df, df_aircraft):
    """
    Reports must reference existing aircraft, related to data quality
    (Fix: Remove reports with non-existent aircraft and log them)
    """
    print("Applying BR-unmatched-aircraft")
    df = df.copy()
    df["aircraft_id"] = df["aircraft_id"].astype(str).str.upper().str.strip()
    valid_aircraft = set(df_aircraft["aircraft_id"].astype(str).str.upper().str.strip())
    invalid_mask = ~df["aircraft_id"].isin(valid_aircraft)
    invalid_rows = df[invalid_mask]
    if not invalid_rows.empty:
        print(f"Dropping {len(invalid_rows)} rows from reports with aircraft_id not in aircraft dimension:")
        for _, row in invalid_rows.iterrows():
            logging.info(
                f"BR-UA: Dropped - aircraft_id={row['aircraft_id']}, report_id={row.get('report_id', 'N/A')}, date_id={row.get('date_id', 'N/A')}, reporter_class={row.get('reporter_class', 'N/A')}, airport={row.get('airport', 'N/A')}, source={row.get('source', 'N/A')}"
            )
    df = df[~invalid_mask].reset_index(drop=True)
    return df


# =====================================================
# Transform Functions
# =====================================================
def transform_aircraft(df_aircraft):
    print("TRANSFORM Aircraft dimension...")
    # Ensure required columns
    df = df_aircraft.copy()
    for col in ["aircraft_id", "model", "manufacturer"]:
        if col not in df.columns:
            raise ValueError(
                "df_aircraft must contain 'aircraft_id', 'model', and 'manufacturer'"
            )

    # Clean from post flight reports nulls
    nan_rows = df[df[["aircraft_id", "model", "manufacturer"]].isna().any(axis=1)]
    num_nans = len(nan_rows)
    if num_nans > 0:
        print(f"Dropped {num_nans} inexistent aircrafts from post flight reports.")
    df = df.dropna(subset=["aircraft_id", "model", "manufacturer"])

    # Format
    df["aircraft_id"] = df["aircraft_id"].astype(str).str.upper().str.strip()
    df["model"] = df["model"].astype(str).str.strip()
    df["manufacturer"] = df["manufacturer"].astype(str).str.strip()
    df = (
        df[["aircraft_id", "model", "manufacturer"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )

    print("TRANSFORM Aircraft completed")
    return df


def transform_date(df_date):
    print("TRANSFORM Date dimension...")

    # Ensure required columns
    df = df_date.copy()
    if "date_id" not in df.columns:
        raise ValueError("df_date must contain 'date_id'")

    # Clean and format
    df["date_id"] = pd.to_datetime(df["date_id"], errors="coerce").dt.date
    invalid_dates = df[df["date_id"].isna()]
    if len(invalid_dates) > 0:
        print(f"Dropped {len(invalid_dates)} invalid date_id rows.")
    df = df.dropna(subset=["date_id"])
    df["day"] = pd.to_datetime(df["date_id"]).dt.day
    df["month"] = pd.to_datetime(df["date_id"]).dt.month
    df["year"] = pd.to_datetime(df["date_id"]).dt.year
    df = (
        df[["date_id", "day", "month", "year"]].drop_duplicates().reset_index(drop=True)
    )

    print("TRANSFORM Date completed")
    return df


# =====================================================
# Flights Fact Transformation
# =====================================================
def compute_flight_measures(df_flights):
    """Per-flight measures. Must run after BR-23 so swapped timestamps are fixed."""
    df = df_flights.copy()
    dep = pd.to_datetime(df["actualdeparture"])
    arr = pd.to_datetime(df["actualarrival"])
    sched_arr = pd.to_datetime(df["scheduledarrival"])
    cancelled = df["cancelled"].astype(bool)

    df["flight_hours"] = np.where(
        cancelled | dep.isna() | arr.isna(),
        0.0,
        (arr - dep).dt.total_seconds() / 3600,
    )
    df["takeoff"] = (~cancelled).astype(int)

    delay_min = (arr - sched_arr).dt.total_seconds() / 60
    is_delayed = ~cancelled & delay_min.notna() & (delay_min > 15)
    df["delays"] = is_delayed.astype(int)
    df["delay_minutes"] = np.where(is_delayed, delay_min, 0.0)
    df["cancellations"] = cancelled.astype(int)
    return df


def transform_flights(df_flights):
    print("TRANSFORM Flights fact...")

    # Apply BRs (measures are computed between BR-23 and BR-21)
    df = df_flights.copy()
    df = enforce_br23_swap_times(df)
    df = compute_flight_measures(df)
    df = enforce_br21_remove_overlaps(df)
    df = df.drop(
        columns=["actualarrival", "actualdeparture", "scheduledarrival", "cancelled"],
        errors="ignore",
    )

    # Aggregate by aircraft and date
    df_agg = df.groupby(["aircraft_id", "date_id"], as_index=False).agg(
        {
            "flight_hours": "sum",
            "takeoff": "sum",
            "delays": "sum",
            "delay_minutes": "sum",
            "cancellations": "sum",
        }
    )

    print("TRANSFORM Flights completed")
    return df_agg


# =====================================================
# Unavailability Fact Transformation
# =====================================================
def transform_unavailability(df_maintenance):
    print("TRANSFORM Unavailability...")

    df = df_maintenance.copy()

    # Ensure required columns
    required_cols = ["aircraft_id", "start_date", "month_id", "type", "days"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns: {missing_cols}")

    # Clean and format
    null_rows = df[df[required_cols].isna().any(axis=1)]
    if not null_rows.empty:
        print(f"Dropped: {len(null_rows)} rows with null values.")
    df = df.dropna()
    df["aircraft_id"] = df["aircraft_id"].astype(str)
    df["type"] = df["type"].str.upper()
    df["days"] = pd.to_numeric(df["days"], errors="coerce").fillna(0.0)

    # Aggregate by aircraft, month, and type
    df_monthly = df.groupby(["aircraft_id", "month_id", "type"], as_index=False)[
        "days"
    ].sum()

    print("TRANSFORM Unavailability completed")
    return df_monthly


# =====================================================
# Transform Reports Fact
# =====================================================
def transform_reports(
    df_technicallogbook, df_postflightreports, df_aircrafts, df_personnel
):
    print("TRANSFORM Reports fact...")

    # Concatenate both sources
    df = pd.concat([df_postflightreports, df_technicallogbook], ignore_index=True)

    # Apply BR-unmatched-aircraft (both sources must reference existing aircraft)
    df = enforce_br_unmatched_aircraft(df, df_aircrafts)

    # Add airport to maintenance reports (pilots are not in the personnel file)
    airport_map = df_personnel.set_index("reporter_id")["airport"]
    df["airport"] = df["reporter_id"].map(airport_map)

    # Ensure proper types
    df["report_id"] = df.index
    df["aircraft_id"] = df["aircraft_id"].astype(str)
    df["date_id"] = pd.to_datetime(df["date_id"]).dt.date
    df["reporter_class"] = df["reporter_class"].str.upper()
    df["source"] = df["source"].str.upper()

    # Project 
    df = df[
        ["report_id", "aircraft_id", "date_id", "reporter_class", "airport", "source"]
    ]

    print(f"TRANSFORM Reports completed: {len(df)} rows")
    return df
