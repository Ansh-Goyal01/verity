# BMW i3 loader fixtures (SYNTHETIC)

These seven CSVs are **synthetic test data, not measurements**. Only the header lines are copied from the real dataset
(IEEE DataPort 10.21227/6jr9-5235), so the loader is tested against the real column layouts; every number in the rows is
made up (a 0.1 s time axis, a constant 36 km/h, a straight SoC ramp, ...). The raw dataset is never committed
(`data/raw/` is gitignored).

| file | layout copied from | what it exercises |
|---|---|---|
| `TripA01.csv` | `TripA03` (23 columns, no `Heating Power LIN`) | the summer layout; a clean trip |
| `TripB01.csv` | `TripB01` (48 columns) | the winter layout; a clean trip; heating kW -> W |
| `TripB02.csv` | `TripB38` (velocity header misspelled `Velocity [km/h]]]`) | misspelled header; 12 trailing empty SoC rows; a 9 kW heating peak |
| `TripB03.csv` | `TripB01` | a clean 40-row trip whose SoC channel `TripB07` copies |
| `TripB04.csv` | `TripB01` | positive battery current: a charging session |
| `TripB07.csv` | `TripB01` | the same SoC channel as `TripB03` (the real `TripB07` has this defect) |
| `TripB14.csv` | `TripB01` | SoC drops 34.6 -> 0.0 in the last row (the real `TripB14` has this defect) |

Files are Latin-1 with CRLF line endings and `;` separators, like the real ones; a missing value is an empty field.
Trip ids reuse real ones on purpose: the loader keeps a small table of known defects keyed by trip id.
