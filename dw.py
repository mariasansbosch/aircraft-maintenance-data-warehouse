import os
import sys
from pathlib import Path
import duckdb
import pygrametl
from pygrametl.tables import CachedDimension, FactTable

duckdb_filename = str(Path(__file__).resolve().parent / "dw.duckdb")


class DW:
    def __init__(self, create=False):
        if create and os.path.exists(duckdb_filename):
            os.remove(duckdb_filename)
        try:
            self.conn_duckdb = duckdb.connect(duckdb_filename)
            print("Connection to the DW created successfully")
        except duckdb.Error as e:
            print(f"Unable to connect to DuckDB database '{duckdb_filename}':", e)
            sys.exit(1)

        # ===================================================================
        # DATABASE SCHEMA CREATION
        # ===================================================================
        if create:
            try:
                # ------------------- DIMENSIONS -------------------
                self.conn_duckdb.execute(
                    """
                    -- Day granularity
                    CREATE TABLE IF NOT EXISTS Date (
                        date_id DATE PRIMARY KEY,
                        day INTEGER,
                        month INTEGER,
                        year INTEGER
                    );
                    -- Month granularity
                    CREATE TABLE IF NOT EXISTS Month(
                        month_id INTEGER PRIMARY KEY,
                        month INTEGER NOT NULL,
                        year INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS Aircraft (
                        aircraft_id CHAR(6) PRIMARY KEY,   -- Registration code
                        model VARCHAR,
                        manufacturer VARCHAR
                    );
                    """
                )
                print("DW dimension tables created successfully")

                # ------------------- FACT TABLES -------------------
                self.conn_duckdb.execute(
                    """
                    CREATE TABLE IF NOT EXISTS Flights(
                        --- (Aircraft X Date) granularity
                        aircraft_id CHAR(6) NOT NULL REFERENCES Aircraft(aircraft_id),
                        date_id DATE NOT NULL REFERENCES Date(date_id),
                        flight_hours DOUBLE,
                        takeoff INTEGER,
                        delays INTEGER,
                        delay_minutes DOUBLE,
                        cancellations INTEGER,
                        PRIMARY KEY (aircraft_id, date_id)
                    );

                    CREATE TABLE IF NOT EXISTS Unavailability(
                        -- (Aircraft X Month X Year X Type) granularity
                        aircraft_id CHAR(6) NOT NULL REFERENCES Aircraft(aircraft_id),
                        month_id INTEGER NOT NULL REFERENCES Month(month_id),
                        type TEXT NOT NULL, -- 'scheduled' or 'unscheduled'
                        days DOUBLE,
                        PRIMARY KEY (aircraft_id, month_id, type)
                    );

                    CREATE TABLE IF NOT EXISTS Reports(
                        -- Report granularity
                        report_id INTEGER PRIMARY KEY,
                        aircraft_id CHAR(6) NOT NULL REFERENCES Aircraft(aircraft_id),
                        date_id DATE NOT NULL REFERENCES Date(date_id),
                        reporter_class TEXT NOT NULL,     -- 'PIREP' or 'MAREP'
                        airport CHAR(3),
                        source TEXT                       -- technicallogbook or postflightreport
                    );
                    """
                )
                print("DW fact tables created successfully")
            except duckdb.Error as e:
                print("Error creating the DW tables:", e)
                sys.exit(2)

        # Link DuckDB and PyGramETL
        self.conn_pygrametl = pygrametl.ConnectionWrapper(self.conn_duckdb)

        # ===================================================================
        # DIMENSION DECLARATIONS
        # ===================================================================
        self.dim_date = CachedDimension(
            name="Date",
            key="date_id",
            attributes=["day", "month", "year"],
            lookupatts=["date_id"],
        )
        self.dim_month = CachedDimension(
            name="Month",
            key="month_id",
            attributes=["month", "year"],
            lookupatts=["month_id"],
        )

        self.dim_aircraft = CachedDimension(
            name="Aircraft",
            key="aircraft_id",
            attributes=["model", "manufacturer"],
            lookupatts=["aircraft_id"],
        )

        print("DW dimension tables declared successfully")

        # ===================================================================
        # FACT TABLE DECLARATIONS
        # ===================================================================
        self.fact_flights = FactTable(
            name="Flights",
            keyrefs=["aircraft_id", "date_id"],
            measures=["flight_hours", "takeoff", "delays", "delay_minutes", "cancellations"],
        )

        self.fact_unavailability = FactTable(
            name="Unavailability",
            keyrefs=["aircraft_id", "month_id", "type"],
            measures=["days"],
        )

        self.fact_reports = FactTable(
            name="Reports",
            keyrefs=["report_id", "aircraft_id", "date_id"],
            measures=["airport", "reporter_class", "source"],
        )

        print("DW fact tables declared successfully")

        print("DW initialized successfully")

    # ===================================================================
    # QUERIES
    # ===================================================================
    def query_utilization(self):
        query = """
           SELECT
                ac.manufacturer,
                c.year,
                ROUND(AVG(c.flight_hours), 2)                    AS FH,       
                ROUND(AVG(c.takeoff), 2)                         AS TO, 
                ROUND(AVG(c.scheduled_days), 2)                  AS ADOSS,
                ROUND(AVG(c.unscheduled_days), 2)                AS ADOSU,
                ROUND(AVG(c.total_days), 2)                      AS ADOS,
                ROUND(365 - AVG(c.total_days), 2)                AS ADIS,
                ROUND( AVG(c.flight_hours) / ((365 - AVG(c.total_days)) * 24), 2) AS DU,
                ROUND( AVG(c.takeoff) / (365 - AVG(c.total_days)), 2)            AS DC,
                100 * ROUND(SUM(c.delays) / NULLIF(SUM(c.takeoff),0), 4)         AS DYR,
                100 * ROUND(SUM(c.cancellations) / NULLIF(SUM(c.takeoff),0), 4)  AS CNR,
                100 - ROUND(100 * (SUM(c.delays) + SUM(c.cancellations)) / NULLIF(SUM(c.takeoff),0), 2) AS TDR,
                ROUND(100 * SUM(c.delay_minutes) / NULLIF(SUM(c.delays),0), 2)   AS ADD
                FROM (
                -- (aircraft X year) Facts
                SELECT
                    f.aircraft_id,
                    f.year,
                    f.flight_hours,
                    f.takeoff,
                    f.delays,
                    f.delay_minutes,
                    f.cancellations,
                    COALESCE(u.scheduled_days, 0)   AS scheduled_days,
                    COALESCE(u.unscheduled_days, 0) AS unscheduled_days,
                    COALESCE(u.total_days, 0)       AS total_days
                FROM (
                    -- (aircraft X year) Flights 
                    SELECT
                    aircraft_id,
                    EXTRACT(year FROM date_id) AS year,
                    SUM(flight_hours)   AS flight_hours,
                    SUM(takeoff)        AS takeoff,
                    SUM(delays)         AS delays,
                    SUM(delay_minutes)  AS delay_minutes,
                    SUM(cancellations)  AS cancellations
                    FROM Flights
                    GROUP BY aircraft_id, year
                ) f
                LEFT JOIN (
                    -- (aircraft X year) Unavailability
                    SELECT
                    aircraft_id,
                    CAST(month_id / 100 AS INTEGER) AS year,
                    SUM(CASE WHEN type = 'SCHEDULED'   THEN days ELSE 0 END) AS scheduled_days,
                    SUM(CASE WHEN type = 'UNSCHEDULED' THEN days ELSE 0 END) AS unscheduled_days,
                    SUM(days) AS total_days
                    FROM Unavailability
                    GROUP BY aircraft_id, year
                ) u
                ON f.aircraft_id = u.aircraft_id AND f.year = u.year
                ) c
                JOIN Aircraft ac ON c.aircraft_id = ac.aircraft_id
                GROUP BY ac.manufacturer, c.year
                ORDER BY ac.manufacturer, c.year;
        """
        return self.conn_duckdb.execute(query).fetchall()

    def query_reporting(self):
        query = """
        WITH flight_summary AS (
    SELECT
        a.manufacturer,
        EXTRACT(year FROM f.date_id) AS year,
        SUM(f.flight_hours) AS flight_hours,
        SUM(f.takeoff) AS flight_cycles
    FROM Flights f
    JOIN Aircraft a ON f.aircraft_id = a.aircraft_id
    GROUP BY a.manufacturer, year
),
report_summary AS (
    SELECT
        a.manufacturer,
        EXTRACT(year FROM r.date_id) AS year,
        COUNT(*) AS report_count
    FROM Reports r
    JOIN Aircraft a ON r.aircraft_id = a.aircraft_id
    WHERE r.source = 'POSTFLIGHTREPORT'
    GROUP BY a.manufacturer, year
)
SELECT
    r.manufacturer,
    r.year,
    1000 * ROUND(r.report_count / NULLIF(f.flight_hours,0), 3) AS RRh,
    100 * ROUND(r.report_count / NULLIF(f.flight_cycles,0), 2) AS RRc
FROM report_summary r
JOIN flight_summary f
    ON r.manufacturer = f.manufacturer AND r.year = f.year
ORDER BY r.manufacturer, r.year;

       
        """

        result = self.conn_duckdb.execute(query).fetchall()
        return result

    def query_reporting_per_role(self):
        query = """
        WITH flight_summary AS (
    SELECT
        a.manufacturer,
        EXTRACT(year FROM f.date_id) AS year,
        SUM(f.flight_hours) AS flight_hours,
        SUM(f.takeoff) AS flight_cycles
    FROM Flights f
    JOIN Aircraft a ON f.aircraft_id = a.aircraft_id
    GROUP BY a.manufacturer, year
),
report_summary AS (
    SELECT
        a.manufacturer,
        EXTRACT(year FROM r.date_id) AS year,
        r.reporter_class AS role,
        COUNT(*) AS report_count
    FROM Reports r
    JOIN Aircraft a ON r.aircraft_id = a.aircraft_id
    WHERE r.source = 'POSTFLIGHTREPORT'
    GROUP BY a.manufacturer, year, role
)
SELECT
    r.manufacturer,
    r.year,
    r.role,
    1000 * ROUND(r.report_count / NULLIF(f.flight_hours,0), 3) AS RRh,
    100 * ROUND(r.report_count / NULLIF(f.flight_cycles,0), 2) AS RRc
FROM report_summary r
JOIN flight_summary f
    ON r.manufacturer = f.manufacturer AND r.year = f.year
ORDER BY r.manufacturer, r.year, r.role;


      
        """
        return self.conn_duckdb.execute(query).fetchall()

    def close(self):
        self.conn_pygrametl.commit()
        self.conn_pygrametl.close()


if __name__ == "__main__":
    dw = DW(create=True)
