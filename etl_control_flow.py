from dw import DW
import extract
import transform
import load
import os
import sys
import tempfile
import traceback

if __name__ == "__main__":
    dw = None
    conn = None
    failed = False

    try:
        print(">>> Creating and initializing the DW...")
        dw = DW(create=True)
        # Optimizations
        dw.conn_duckdb.execute(f"PRAGMA threads={os.cpu_count()};")
        dw.conn_duckdb.execute(f"PRAGMA temp_directory='{tempfile.gettempdir()}';")
        dw.conn_duckdb.execute("PRAGMA checkpoint_threshold='2GB';")

        # =====================================================
        # EXTRACT
        # =====================================================
        print(">>> Starting EXTRACT step...")
        conn = extract.get_db_connection()

        df_aircrafts = extract.extract_aircrafts(conn)
        df_dates = extract.extract_dates(conn)
        df_flights = extract.extract_flights(conn)
        df_maintenance = extract.extract_maintenance(conn)
        df_technicallogbook = extract.extract_technicallogbook(conn)
        df_personnel = extract.extract_personnel()
        df_postflightreports = extract.extract_postflightreports(conn)

        print("EXTRACT completed successfully")

        # =====================================================
        # TRANSFORM
        # =====================================================
        print(">>> Starting TRANSFORM step...")

        # Transform dimensions
        df_dim_aircraft = transform.transform_aircraft(df_aircrafts)
        df_dim_date = transform.transform_date(df_dates)

        # Transform facts
        df_fact_flights = transform.transform_flights(df_flights)
        df_fact_unavailability = transform.transform_unavailability(df_maintenance)
        df_fact_reports = transform.transform_reports(df_technicallogbook, df_postflightreports, df_dim_aircraft, df_personnel)

        print("TRANSFORM completed successfully")

        # =====================================================
        # LOAD
        # =====================================================
        print(">>> Starting LOAD step...")

        # Load dimensions
        load.load_aircraft(dw, df_dim_aircraft)
        load.load_dates(dw, df_dim_date)

        # Load facts
        load.load_flights(dw, df_fact_flights)
        load.load_unavailability(dw, df_fact_unavailability)
        load.load_reports(dw, df_fact_reports)

        print("LOAD completed successfully\n")

    except Exception as e:
        failed = True
        print(f"Error during ETL pipeline: {e}")
        traceback.print_exc()

    finally:
        print("\n>>> Cleaning up resources...")
        try:
            if conn:
                conn.close()
                print("PostgreSQL connection closed.")
        except Exception as e:
            print("Error closing PostgreSQL:", e)

        try:
            if dw:
                dw.close()
                print("DuckDB connection closed.")
        except Exception as e:
            print("Error closing DuckDB:", e)

        print("ETL pipeline finished")
        if failed:
            sys.exit(1)
