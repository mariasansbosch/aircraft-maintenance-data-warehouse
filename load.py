import pandas as pd
from dw import DW


# =====================================================
# Dimension loaders
# =====================================================
def load_aircraft(dw: DW, df_aircraft: pd.DataFrame):
    print(f"Loading Aircraft dimension ({len(df_aircraft)} rows)...")
    df_aircraft = df_aircraft[["aircraft_id", "model", "manufacturer"]]
    dw.conn_duckdb.register("tmp_aircraft", df_aircraft)
    dw.conn_duckdb.execute(
        """
        INSERT INTO Aircraft
        SELECT * FROM tmp_aircraft
    """
    )
    dw.conn_duckdb.unregister("tmp_aircraft")
    dw.conn_pygrametl.commit()


def load_dates(dw: DW, df_date: pd.DataFrame):
    print(f"Loading Date and Month dimensions ({len(df_date)} rows)...")
    dw.conn_duckdb.register("tmp_date", df_date)

    dw.conn_duckdb.execute(
        """
        INSERT INTO Date (date_id, day, month, year)
        SELECT date_id, day, month, year
        FROM tmp_date
    """
    )

    dw.conn_duckdb.execute(
        """
        INSERT INTO Month (month_id, month, year)
        SELECT DISTINCT
            year * 100 + month AS month_id,
            month,
            year
        FROM tmp_date
    """
    )

    dw.conn_duckdb.unregister("tmp_date")
    dw.conn_pygrametl.commit()

    print("Date and Month dimensions loaded successfully.")


# =====================================================
# Fact loaders
# =====================================================
def load_flights(dw: DW, df_flights: pd.DataFrame):
    print(f"Loading Flights fact ({len(df_flights)} rows)...")
    columns = [
        "date_id",
        "aircraft_id",
        "flight_hours",
        "takeoff",
        "delays",
        "delay_minutes",
        "cancellations",
    ]
    df_flights = df_flights[columns]
    try:
        dw.conn_duckdb.register("tmp_flights", df_flights)
        dw.conn_duckdb.execute(
            """
            INSERT INTO Flights(
                date_id, aircraft_id, 
                flight_hours, takeoff, delays, delay_minutes, cancellations
            )
            SELECT * FROM tmp_flights
        """
        )
    finally:
        dw.conn_duckdb.unregister("tmp_flights")
    print("Flights fact loaded successfully.")
    dw.conn_pygrametl.commit()


def load_unavailability(dw: DW, df_unavailability: pd.DataFrame):
    print(f"Loading Unavailability fact ({len(df_unavailability)} rows)...")
    columns = [
        "aircraft_id",
        "month_id",
        "type",
        "days",
    ]
    df_unavailability = df_unavailability[columns]
    try:
        dw.conn_duckdb.register("tmp_unavailability", df_unavailability)
        dw.conn_duckdb.execute(
            """
            INSERT INTO Unavailability(
                aircraft_id, month_id, 
                type, days
            )
            SELECT * FROM tmp_unavailability
        """
        )
    finally:
        dw.conn_duckdb.unregister("tmp_unavailability")
    print("Unavailability fact loaded successfully.")
    dw.conn_pygrametl.commit()


def load_reports(dw: DW, df_reports: pd.DataFrame):
    print(f"Loading Reports fact ({len(df_reports)} rows)...")

    columns = [
        "report_id",
        "aircraft_id",
        "date_id",
        "reporter_class",
        "airport",
        "source",
    ]
    df_reports = df_reports[columns]

    try:
        dw.conn_duckdb.register("tmp_reports", df_reports)
        dw.conn_duckdb.execute(
            """
            INSERT INTO Reports (
                report_id, aircraft_id, date_id, 
                reporter_class, airport, source
            )
            SELECT * FROM tmp_reports
        """
        )
    finally:
        dw.conn_duckdb.unregister("tmp_reports")
    dw.conn_pygrametl.commit()
