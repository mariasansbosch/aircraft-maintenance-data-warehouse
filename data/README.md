# Data

The lookup files used by the pipeline are not included in this repository because they were part of the original (non-public) dataset. To run the ETL, place these two files here:

| File | Columns |
|---|---|
| `aircraft-manufacturerinfo-lookup.csv` | `aircraft_reg_code, manufacturer_serial_number, aircraft_model, aircraft_manufacturer` |
| `maintenance_personnel.csv` | `reporteurid, airport` |

The unit tests in `tests/` do not need these files.
