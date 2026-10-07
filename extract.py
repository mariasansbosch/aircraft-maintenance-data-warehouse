import os
from pathlib import Path
import warnings
import psycopg
import pandas as pd

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message="pandas only supports SQLAlchemy connectable.*",
)


# =====================================================
# Paths & Database Connection
# =====================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
AIRCRAFT_LOOKUP_CSV = DATA_DIR / "aircraft-manufacturerinfo-lookup.csv"
PERSONNEL_CSV = DATA_DIR / "maintenance_personnel.csv"
DB_CONF_FILE = BASE_DIR / "db_conf.txt"

# Environment variables take precedence over db_conf.txt
ENV_KEYS = {
    "dbname": "BDA_DB_NAME",
    "user": "BDA_DB_USER",
    "password": "BDA_DB_PASSWORD",
    "ip": "BDA_DB_HOST",
    "port": "BDA_DB_PORT",
}


def _read_db_parameters():
    parameters = {}
    if DB_CONF_FILE.is_file():
        with open(DB_CONF_FILE, "r") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    parameters[key.strip()] = value.strip()
    for key, env_var in ENV_KEYS.items():
        if os.getenv(env_var):
            parameters[key] = os.getenv(env_var)

    missing = [k for k in ENV_KEYS if k not in parameters]
    if missing:
        raise FileNotFoundError(
            f"Missing database parameters {missing}. Create '{DB_CONF_FILE.name}' "
            f"(see db_conf.example.txt) or set the environment variables "
            f"{', '.join(ENV_KEYS.values())}."
        )
    return parameters


def get_db_connection():
    parameters = _read_db_parameters()
    try:
        conn = psycopg.connect(
            dbname=parameters["dbname"],
            user=parameters["user"],
            password=parameters["password"],
            host=parameters["ip"],
            port=parameters["port"],
        )
        print("Connection to the source database created successfully")
        return conn
    except psycopg.Error as e:
        print("Error connecting to PostgreSQL:", e)
        raise


# =====================================================
# Extract Aircraft
# =====================================================
def extract_aircrafts(conn=None, csv_path=AIRCRAFT_LOOKUP_CSV):
    print("Extracting aircrafts...")

    df_aircraft_lookup = pd.read_csv(csv_path)
    df_aircraft_lookup = df_aircraft_lookup.rename(
        columns={
            "aircraft_reg_code": "aircraft_id",
            "aircraft_model": "model",
            "aircraft_manufacturer": "manufacturer",
        }
    )[["aircraft_id", "model", "manufacturer"]]
    df_aircraft_lookup["aircraft_id"] = df_aircraft_lookup["aircraft_id"].astype(str).str.upper().str.strip()

    if conn:
        sql_queries = [
            'SELECT DISTINCT aircraftregistration AS aircraft_id FROM "AIMS".flights',
            'SELECT DISTINCT aircraftregistration AS aircraft_id FROM "AIMS".maintenance',
            'SELECT DISTINCT aircraftregistration AS aircraft_id FROM "AMOS".workorders',
            'SELECT DISTINCT aircraftregistration AS aircraft_id FROM "AMOS".postflightreports',
            'SELECT DISTINCT aircraftregistration AS aircraft_id FROM "AMOS".maintenanceevents',
        ]

        dfs = []
        with conn.cursor() as cur:
            for q in sql_queries:
                cur.execute(q)
                temp_df = pd.DataFrame(cur.fetchall(), columns=["aircraft_id"])
                temp_df["aircraft_id"] = temp_df["aircraft_id"].astype(str).str.upper().str.strip()
                dfs.append(temp_df)

        df_all_aircraft = pd.concat([df_aircraft_lookup[["aircraft_id"]]] + dfs).drop_duplicates()
        df_aircraft = df_all_aircraft.merge(df_aircraft_lookup, on="aircraft_id", how="left")
    else:
        df_aircraft = df_aircraft_lookup

    print(f"Extracted {len(df_aircraft)} aircraft")
    return df_aircraft



# =====================================================
# Extract Date
# =====================================================
def extract_dates(conn):
    print("Extracting dates...")
    df_date = pd.read_sql(
        """
            WITH all_dates AS (
                SELECT actualDeparture::date AS date_id FROM "AIMS".flights
                UNION
                SELECT actualArrival::date AS date_id FROM "AIMS".flights
                UNION
                SELECT scheduledDeparture::date AS date_id FROM "AIMS".flights
                UNION
                SELECT scheduledArrival::date AS date_id FROM "AIMS".flights
                UNION
                SELECT scheduledDeparture::date AS date_id FROM "AIMS".maintenance
                UNION
                SELECT scheduledArrival::date AS date_id FROM "AIMS".maintenance
                UNION
                SELECT reportingDate::date AS date_id FROM "AMOS".postflightreports
                UNION
                SELECT reportingdate::date AS date_id FROM "AMOS".technicallogbookorders
            )
            SELECT DISTINCT
                date_id,
                EXTRACT(DAY FROM date_id) AS day,
                EXTRACT(MONTH FROM date_id) AS month,
                EXTRACT(YEAR FROM date_id) AS year
            FROM all_dates
            ORDER BY date_id;
        """,
        conn,
    )
    print(f"Extracted dates: {len(df_date)}")
    return df_date


# =====================================================
# Extract Flights
# =====================================================
def extract_flights(conn):
    """Extract raw flight records from AIMS.

    Per-flight measures (flight hours, delays, ...) are computed in the
    transform step, *after* BR-23 has fixed swapped timestamps, so that the
    corrected timestamps are the ones used.
    """
    print("Extracting Flights...")

    df_flights = pd.read_sql(
        """
            SELECT
                aircraftregistration AS aircraft_id,
                scheduleddeparture::date AS date_id,
                actualdeparture,
                actualarrival,
                scheduledarrival,
                cancelled
            FROM "AIMS".flights
        """,
        conn,
    )
    df_flights["cancelled"] = df_flights["cancelled"].astype(bool)
    print(f"Extracted {len(df_flights)} flights")
    return df_flights


def extract_maintenance(conn):
    print("Extracting Maintenance...")

    query = """
        SELECT 
            aircraftregistration AS aircraft_id,
            scheduleddeparture::date AS start_date,
            scheduledarrival::date AS end_date,
            programmed
        FROM "AIMS".maintenance
    """
    df_maint = pd.read_sql(query, conn)

    df_maint["start_date"] = pd.to_datetime(df_maint["start_date"], errors="coerce")
    df_maint["end_date"] = pd.to_datetime(df_maint["end_date"], errors="coerce")
    df_maint["days"] = (
        df_maint["end_date"] - df_maint["start_date"]
    ).dt.total_seconds() / 86400.0

    df_maint["type"] = df_maint["programmed"].apply(
        lambda x: "SCHEDULED" if x else "UNSCHEDULED"
    )
    df_maint["month_id"] = (
        df_maint["start_date"].dt.year * 100 + df_maint["start_date"].dt.month
    )
    df_unavailability = df_maint[
        ["aircraft_id", "start_date", "month_id", "type", "days"]
    ]
    print(f"Extracted {len(df_unavailability)} maintenance records")
    return df_unavailability


# =====================================================
# Extract Reports
# =====================================================
def extract_personnel():
    print("Extracting Maintenance Personnel...")
    try:
        df_personnel = pd.read_csv(PERSONNEL_CSV)
        df_personnel = df_personnel.rename(columns={
            'reporteurid': 'reporter_id'
        })
        print(f"Extracted {len(df_personnel)} maintenance personnel records")
        return df_personnel
    except FileNotFoundError:
        return pd.DataFrame(columns=['airport', 'reporter_id'])


def extract_technicallogbook(conn):
    print("Extracting Technical Logbook...")

    query = """
            SELECT 
                reporteurid AS reporter_id,
                aircraftregistration AS aircraft_id,
                reportingdate::date AS date_id,
                reporteurclass AS reporter_class,
                'NULL' AS airport,
                'technicallogbook' AS source
            FROM "AMOS".technicallogbookorders
        """
    df = pd.read_sql(query, conn)
    df["date_id"] = pd.to_datetime(df["date_id"]).dt.date
    print(f"Extracted {len(df)} technical logbook reports")
    return df 


def extract_postflightreports(conn):
    print("Extracting Post Flight Reports...")
    query = """
            SELECT 
                reporteurid AS reporter_id,
                aircraftregistration AS aircraft_id,
                reportingdate::date AS date_id,
                reporteurclass AS reporter_class,
                'NULL' AS airport,
                'postflightreport' AS source
            FROM "AMOS".postflightreports
        """
    df = pd.read_sql(query, conn)
    df["date_id"] = pd.to_datetime(df["date_id"]).dt.date
    print(f"Extracted {len(df)} post flight reports")
    return df 


    

# =====================================================
# Baseline queries
# =====================================================
def get_aircrafts_per_manufacturer(conn=None) -> dict[str, list[str]]:
    """Generate a dictionary mapping each manufacturer to a list of aircraft registration codes."""
    df = pd.read_csv(AIRCRAFT_LOOKUP_CSV)

    result = {}
    for _, row in df.iterrows():
        manufacturer = row["aircraft_manufacturer"]
        aircraft_id = row["aircraft_reg_code"]
        if manufacturer not in result:
            result[manufacturer] = []
        result[manufacturer].append(aircraft_id)
    return result


def query_utilization_baseline():
    conn = get_db_connection()
    aircrafts = get_aircrafts_per_manufacturer(conn)
    cur = conn.cursor()
    cur.execute(
        f"""
        WITH atomic_data AS (
            SELECT f.aircraftregistration,
                CASE 
                    WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                    WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                    ELSE f.aircraftregistration
                    END AS manufacturer, 
                DATE_PART('year', f.scheduleddeparture)::text AS year,
                CASE WHEN f.cancelled 
                    THEN 0
                    ELSE EXTRACT(EPOCH FROM f.actualarrival-f.actualdeparture) / 3600
                    END AS flightHours,
                CASE WHEN f.cancelled 
                    THEN 0
                    ELSE 1
                    END AS flightCycles,
                CASE WHEN f.cancelled
                    THEN 1
                    ELSE 0
                    END AS cancellations,
                CASE WHEN f.cancelled
                    THEN 0
                    ELSE CASE WHEN EXTRACT(EPOCH FROM f.actualarrival - f.scheduledarrival) / 60 > 15
                        THEN 1
                        ELSE 0
                        END
                    END AS delays,
                CASE WHEN f.cancelled
                    THEN 0
                    ELSE CASE WHEN EXTRACT(EPOCH FROM f.actualarrival - f.scheduledarrival) / 60 > 15
                        THEN EXTRACT(EPOCH FROM f.actualarrival - f.scheduledarrival) / 60
                        ELSE 0
                        END
                    END AS delayedMinutes,
                0 AS scheduledOutOfService,
                0 AS unScheduledOutOfService
            FROM "AIMS".flights f
            UNION ALL
            SELECT m.aircraftregistration,           
                CASE 
                    WHEN m.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                    WHEN m.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                    ELSE m.aircraftregistration
                    END AS manufacturer, 
                DATE_PART('year', m.scheduleddeparture)::text AS year,
                0 AS flightHours,
                0 AS flightCycles,
                0 AS cancellations,
                0 AS delays,
                0 AS delayedMinutes,
                CASE WHEN m.programmed
                    THEN EXTRACT(EPOCH FROM m.scheduledarrival-m.scheduleddeparture)/(24*3600)
                    ELSE 0
                    END AS scheduledOutOfService,
                CASE WHEN m.programmed
                    THEN 0
                    ELSE EXTRACT(EPOCH FROM m.scheduledarrival-m.scheduleddeparture)/(24*3600)
                    END AS unScheduledOutOfService
            FROM "AIMS".maintenance m
            )
        SELECT a.manufacturer, a.year, 
            ROUND(SUM(a.flightHours)/COUNT(DISTINCT a.aircraftregistration), 2) AS FH,
            ROUND(SUM(a.flightCycles)/COUNT(DISTINCT a.aircraftregistration), 2) AS TakeOff,
            ROUND(SUM(a.scheduledOutOfService)/COUNT(DISTINCT a.aircraftregistration), 2) AS ADOSS,
            ROUND(SUM(a.unscheduledOutOfService)/COUNT(DISTINCT a.aircraftregistration), 2) AS ADOSU,
            ROUND((SUM(a.scheduledOutOfService)+SUM(a.unscheduledOutOfService))/COUNT(DISTINCT a.aircraftregistration), 2) AS ADOS,
            365-ROUND((SUM(a.scheduledOutOfService)+SUM(a.unscheduledOutOfService))/COUNT(DISTINCT a.aircraftregistration), 2) AS ADIS, -- This assumes a period of one year (as in the group by)
            ROUND(ROUND(SUM(a.flightHours)/COUNT(DISTINCT a.aircraftregistration), 2)/((365-ROUND((SUM(a.scheduledOutOfService)+SUM(a.unscheduledOutOfService))/COUNT(DISTINCT a.aircraftregistration), 2))*24), 2) AS DU,
            ROUND(ROUND(SUM(a.flightCycles)/COUNT(DISTINCT a.aircraftregistration), 2)/(365-ROUND((SUM(a.scheduledOutOfService)+SUM(a.unscheduledOutOfService))/COUNT(DISTINCT a.aircraftregistration), 2)), 2) AS DC,
            100*ROUND(SUM(delays)/ROUND(SUM(a.flightCycles), 2), 4) AS DYR,
            100*ROUND(SUM(a.cancellations)/ROUND(SUM(a.flightCycles), 2), 4) AS CNR,
            100-ROUND(100*(SUM(delays)+SUM(cancellations))/SUM(a.flightCycles), 2) AS TDR,
            100*ROUND(SUM(delayedMinutes)/SUM(delays),2) AS ADD
        FROM atomic_data a
        GROUP BY a.manufacturer, a.year
        ORDER BY a.manufacturer, a.year;
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def query_reporting_baseline():
    conn = get_db_connection()
    aircrafts = get_aircrafts_per_manufacturer(conn)
    cur = conn.cursor()
    cur.execute(
        f"""
        WITH 
            atomic_data_utilization AS (
                SELECT
                    CASE 
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                        ELSE f.aircraftregistration
                        END AS manufacturer, 
                    DATE_PART('year', f.scheduleddeparture)::text AS year,
                    CAST(SUM(CASE WHEN f.cancelled 
                        THEN 0
                        ELSE EXTRACT(EPOCH FROM f.actualarrival-f.actualdeparture) / 3600
                        END) AS numeric) AS flightHours,
                    CAST(SUM(CASE WHEN f.cancelled 
                        THEN 0
                        ELSE 1
                        END) AS numeric) AS flightCycles
                FROM "AIMS".flights f
                GROUP BY manufacturer, YEAR
                ),
            atomic_data_reporting AS (
                SELECT
                    CASE 
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                        ELSE f.aircraftregistration
                        END AS manufacturer, 
                    DATE_PART('year', f.reportingdate)::text AS year,
                    COUNT(*) AS counter
                FROM "AMOS".postflightreports f
                GROUP BY manufacturer, YEAR
                )
        SELECT f1.manufacturer, f1.year,
            1000*ROUND(f1.counter/f2.flightHours, 3) AS RRh,
            100*ROUND(f1.counter/f2.flightCycles, 2) AS RRc               
        FROM atomic_data_reporting f1
            JOIN atomic_data_utilization f2 ON f2.manufacturer = f1.manufacturer AND f1.year = f2.year
        ORDER BY f1.manufacturer, f1.YEAR;
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def query_reporting_per_role_baseline():
    conn = get_db_connection()
    aircrafts = get_aircrafts_per_manufacturer(conn)
    cur = conn.cursor()
    cur.execute(
        f"""
        WITH 
            atomic_data_utilization AS (
                SELECT
                    CASE 
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                        ELSE f.aircraftregistration
                        END AS manufacturer, 
                    DATE_PART('year', f.scheduleddeparture)::text AS year,
                    CAST(SUM(CASE WHEN f.cancelled 
                        THEN 0
                        ELSE EXTRACT(EPOCH FROM f.actualarrival-f.actualdeparture) / 3600
                        END) AS numeric) AS flightHours,
                    CAST(SUM(CASE WHEN f.cancelled 
                        THEN 0
                        ELSE 1
                        END) AS numeric) AS flightCycles
                FROM "AIMS".flights f
                GROUP BY manufacturer, YEAR
                ),
            atomic_data_reporting AS (
                SELECT
                    CASE 
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Airbus", []))}') THEN 'Airbus'
                        WHEN f.aircraftregistration in ('{"','".join(aircrafts.get("Boeing", []))}') THEN 'Boeing'
                        ELSE f.aircraftregistration
                        END AS manufacturer, 
                    DATE_PART('year', f.reportingdate)::text AS year,
                    f.reporteurclass AS role,
                    COUNT(*) AS counter
                FROM "AMOS".postflightreports f
                GROUP BY manufacturer, year, role
                )
        SELECT f1.manufacturer, f1.year, f1.role,
            1000*ROUND(f1.counter/f2.flightHours, 3) AS RRh,
            100*ROUND(f1.counter/f2.flightCycles, 2) AS RRc              
        FROM atomic_data_reporting f1
            JOIN atomic_data_utilization f2 ON f2.manufacturer = f1.manufacturer AND f1.year = f2.year
        ORDER BY f1.manufacturer, f1.year, f1.role;
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result
