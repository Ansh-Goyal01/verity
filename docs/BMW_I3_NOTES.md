> Availability on the takeover machine: raw BMW files are absent; real E1 is NOT DONE (dataset not on this machine). The profiling below is historical work completed before the fixed split; no test trips were opened in this takeover.

# BMW i3 dataset: what is in the files (read this before using the loader)

Source: "Battery and Heating Data in Real Driving Cycles", Steinstraeter, Buberger and Trifonov, TU Munich, Institute of
Automotive Technology, IEEE DataPort, DOI 10.21227/6jr9-5235. Page:
https://ieee-dataport.org/open-access/battery-and-heating-data-real-driving-cycles. The page describes a 60 Ah BMW i3, with
environment, vehicle, battery and heating-circuit channels. This is **German** driving, with a **heater** (winter) or **A/C**
(summer) load: it is not Indian driving and the A/C term is only half of what we need. Say so wherever a result is shown.

Everything below was read off the files, nothing was fitted. The profiling used all 70 trips and was done **before** the
train/test split existed (the split is made from trip ids only, so it is blind to it). After the split was written no code or
test opens a test trip. The raw files are in `data/raw/bmw_i3/` (gitignored, never committed): see section 8.

## 1. What the download contains

| file | what it is |
|---|---|
| `TripA01.csv` ... `TripA32.csv` (32) | summer 2019, 25 Jun - 22 Jul, Munich area, 13.0 h, 467,701 rows |
| `TripB01.csv` ... `TripB38.csv` (38) | winter 2019-20, 7 Dec - 7 Feb, Munich area, 17.4 h, 627,092 rows |
| `Overview.xlsx` | one row per trip: date, route, weather, battery temperature and SoC at start/end, ambient temperature at start, target cabin temperature, distance, duration, fan, free-text note |
| `readin.m` | 10 lines of MATLAB (`readtable`); documents nothing |

- **There is no `readme.txt`** in this download (the task brief expected one). The only documentation is `Overview.xlsx` and the
  paper the authors cite (MDPI World Electric Vehicle Journal 11(2):41, not read here: it is the place to look for the SoC
  definition and the vehicle parameters).
- **70 trips**, not the 72 the DataPort summary mentions. 70 trips + `Overview.xlsx` + `readin.m` = 72 files, which may be the origin
  of the 72 (not verified).
- 191 MB in total; 1,094,793 rows.

## 2. File format

- Text, `;`-separated, `.` decimal, **Latin-1** (the degree sign in headers is the single byte `0xB0`), CRLF line endings, one header
  line. A missing value is an **empty field**.
- The first column is `Time [s]`: **exactly 0.1 s steps (10 Hz) in all 70 trips**, starting at 0, strictly increasing, no gaps and no
  duplicate timestamps. There is **no wall-clock time inside the CSVs**; dates exist only in `Overview.xlsx`.
- **Six header layouts**: A trips have 28 (12 files), 23 (18 files), 24 (TripA11, with an extra `Unnamed: 23` column) or 22 (TripA21)
  columns; B trips have 48 (all 38; TripB38 differs only by the typo below). The eight signals we need are in every layout. Layouts
  differ in the rest: the A layouts with 23, 24 and 22 columns lack `Heating Power LIN [W]`, `Heater Voltage/Current` and some coolant
  temperatures; B has 20 extra temperature/flow columns (defrost, vents, footwell, coolant flow).
- **Header typos** (the loader copes with them, and matches headers on the raw line so a duplicated name cannot hide):
  `TripB38` writes `Velocity [km/h]]]`; every file has `max. SoC [%)`; one B header (`Temperature Vent right`) appears twice.
  A loader that matches names exactly reads TripB38's speed as all-NaN.

## 3. Columns we use, and what else is there

| common name (loader) | source column | unit in file | notes |
|---|---|---|---|
| `time_s` | `Time [s]` | s | 0.1 s steps |
| `speed_mps` | `Velocity [km/h]` | km/h -> /3.6 | max 152.3 km/h |
| `elevation_m` | `Elevation [m]` | m | absolute, 437-665 m; source undocumented |
| `ambient_c` | `Ambient Temperature [degC]` | degC | the vehicle's own sensor, see 5 |
| `voltage_v` | `Battery Voltage [V]` | V | 301.8-394.8 V |
| `current_a` | `Battery Current [A]` | A | **discharge negative, charge and regen positive** (checked against the energy balance and the two charging trips) |
| `soc_pct` | `SoC [%]` | % points | see 4 |
| `heating_w` | `Heating Power CAN [kW]` | kW -> x1000 | present in all 70 trips (the `LIN [W]` twin is in only 50 and agrees within ~40 W) |

Not mapped, but in the files and likely useful later: `Longitudinal Acceleration [m/s^2]` (a logged channel, so the inertia term
can be checked without differentiating speed), `Motor Torque [Nm]`, `Throttle [%]`, `Regenerative Braking Signal`,
`AirCon Power [kW]`, `Heater Voltage/Current`, `Battery Temperature`, `Requested Heating Power [W]`, `Cabin Temperature Sensor`,
`displayed SoC [%]`.

## 4. How SoC is defined and recorded (what is known and what is not)

- **Not documented anywhere in the download.** What the files show:
  - `SoC [%]` is the BMS value: the Overview start SoC equals it on 69 of 70 trips and the end SoC on 66 (exceptions: TripA01, TripA16 and
    TripA31, where Overview and CSV disagree, and TripB14, whose CSV has the last-sample glitch of 6.3).
  - `displayed SoC [%]` is the dashboard value and is **not** a simple linear map of the BMS value over its window: `(soc - min)/(max - min)`
    misses it by a mean of -4.3 points (checked on two trips). `min. SoC` and `max. SoC` are constants within a trip (8 and 89-90 in the
    two trips checked).
  - **It is a staircase:** 96 % of sample-to-sample differences are exactly 0 (92-98 % per trip). It moves in steps of 0.01-0.05 points
    (0.05 is the most common, then 0.04 and 0.01) and can stay constant for up to 3,032 samples (5 min). Whether the BMS reports at that
    rate or the logger holds/interpolates is not knowable from the files.
  - A **unit consistency check** (not a fit): the net energy out of the battery, the integral of U x I, divided by the SoC drop gives a median
    **22.8 kWh per 100 points** (IQR 22.3-23.2) over 63 trips with a usable SoC and a drop above 3 points. That says voltage, current and
    SoC are in consistent units. What 0 % and 100 % refer to (gross or usable capacity) is **not established here**; take it from the paper.
- Start SoC ranges 31.5-88.5 points and end SoC 15.4-82.9 (TripB14's glitch sample excluded); drops per trip go from 1.9 to 50.9 points
  (excluding the charging trip B04).

## 5. Which signals can drive our physics model

| model term (CLAUDE.md) | signal | verdict |
|---|---|---|
| speed `v`, acceleration `a` | `speed_mps`, `Longitudinal Acceleration` | good: 10 Hz, no gaps (TripB38 needs the header fix) |
| grade `theta` | `elevation_m` + integrated speed | usable: smooth (median step 0.01 m per sample). TripB02 has a 27.5 m jump at t = 199.1 s and a 1.6 m one at 39.2 s |
| air density `rho_air(T)`, `P_aux(T)` | `ambient_c` | usable with care: it is the car's sensor and **drifts during a trip** (TripA01 25.5 -> 33.0 degC, TripA07 33.5 -> 21.5), probably heat-soak (not verified). B trips also have a second `Ambient Temperature Sensor` column (TripB01 start 9.6 vs 8.5) |
| `P_aux` heating (winter) | `heating_w` (CAN) | B trips: usable, peak ~7 kW. **A trips: unreliable**, see 6.6 |
| `P_aux` cooling (summer) | `AirCon Power [kW]` | A trips only: mean 0.38 kW, max 3.3 kW, non-zero 66 % of the time; B trips ~0. The nearest thing to our A/C term |
| measured battery power | `voltage_v * current_a` | good: includes traction, aux and base load together |
| ground truth for E1 | `soc_pct`, or the integral of U x I | SoC is a coarse staircase and has defects (6.1-6.3); the energy integral is the fallback |
| wind, road surface, payload | **none** | not in any column. Payload exists only as free text in `Overview.xlsx` notes: `+70 kg` (B09, B10, B14, B25), `+80 kg` (B22), `+60 kg` (B23, B24), `+160 kg` (B33, B34) |

## 6. Data-quality problems (all confirmed in the files)

1. **TripB07's SoC is a copy of TripB03's.** The collapsed SoC staircase (673 values) is identical, and the first 15,794 samples are
   identical to the last digit; B07 then holds 50.4 and goes NaN, while its voltage and current differ. The Overview also gives both the
   same 67.4 -> 50.4. **B07's SoC is unusable.** The loader flags it (`soc_copy_of:TripB03`). A search of every 100-sample velocity window
   found no speed data shared between any two trips.
2. **SoC missing for long stretches** (flag `soc_missing_tail`, more than 10 trailing rows): TripB07 4,175 rows, TripB09 20,541 (37 %, whether
   this is the charging period is not verified), TripB28 1,625, TripB34 4,407 (60 %), TripB13 13. In 21 further B trips the last 1-2 rows are
   empty, and TripB11 is empty in its first and last row. The other channels continue, so the energy integral covers the whole trip while
   SoC does not.
3. **TripB14's last sample has SoC 0.0** (34.6 the sample before, displayed SoC empty): a logging glitch, flag `soc_jump`. Taking the
   last row as the end SoC would give a 34.6-point error. No other trip has a jump larger than 0.5 points.
4. **Two charging sessions, not driving trips:** TripB04 (SoC +24 points, net +5.8 kWh into the battery) and TripB09 (net +15.9 kWh;
   the Overview note says fast charging). Flag `charging_session`.
5. **TripB02 heating spike:** `Heating Power CAN` reaches 40.0 kW for 158 s (t = 41-199 s), while every other trip peaks at 7.04 kW or
   less; it ends at the instant of the 27.5 m elevation jump. This trip's energy per SoC point is 29.8 kWh (median 22.8). Flag
   `heating_spike` (above 8 kW).
6. **A-trip heating power is not a physical power in several trips.** `Heating Power CAN` peaks at exactly 5.52 kW in 26 of 32 A trips,
   and TripA03, A05, A06 and A13 sit above 1 kW for the whole trip at 22-30 degC. TripA06's mean CAN heating is 5.3 kW while its mean
   battery power, traction included, is 5.7 kW. In summer, use `AirCon Power` and treat this column as a status/limit signal until the
   paper says otherwise. (No flag: it cannot be told from the data alone.)
7. **`Overview.xlsx` has errors; the CSVs agree with the energy balance where checked, so trust the CSVs:**
   TripA31 end SoC 82.5 (CSV 71.9) and TripA16 51.5 (CSV 46.8); TripA01 start/end 86.3/80.3 (CSV 86.9/81.5; which is right is not known);
   TripA30 distance 7.75 km (integrated speed 10.73 km, which is also TripA04's integrated distance although the speed traces differ);
   TripA31 and TripA32 start ambient swapped; TripA30 and TripA31 carry one timestamp (their CSVs are different and continuous: A30's last
   sample equals A31's first); TripA24 and TripA25 are out of time order; TripB03-B06 timestamps use `_` instead of `-`; TripB05's distance
   is stored as text; the SoC-difference column has no header (and TripA31's value is wrong). Everything else in the Overview agrees:
   duration on 70 of 70 trips, distance on 69.
8. **Duplicated trips:** none, apart from the SoC copy in 6.1. Consecutive trips chain: for 12 pairs the end SoC of one equals the start
   SoC of the next (for example A03 -> A04, B15 -> B16 -> B17).

## 7. The loader and the split

- `engine/verity/datasets/bmw_i3.py`: `read_trip` maps a file to `BmwTrip(trip_id, season, data, flags)` with the eight columns above in SI
  units (m/s, W). It **cleans nothing**: no row is dropped or repaired and the source sign is kept; problems are `flags`. A missing
  or ambiguous signal, a non-numeric value or a non-increasing time axis raises `ValueError` naming the trip. `season` is "A" (summer,
  A/C) or "B" (winter, heater).
- Flags: `soc_missing_tail`, `soc_jump`, `charging_session`, `heating_spike`, `soc_copy_of:<id>`. Thresholds are constants at the top of the
  module, read off these files ("approximate - verify"). Decide centrally, and equally for train and test, which flags make a trip unusable.
- **Split:** `data/splits/bmw_i3_split.json`, **fixed once** (seed 31, `verity.splits.make_trip_id_split`), ids only, **49 train / 21 test**,
  stratified by season (A: 22 train / 10 test, B: 27 / 11). **Never re-split.** `load_split_part("test")` raises `PermissionError` unless
  `final_evaluation=True`, which only the final E1 run may pass. A test fails if the committed file stops matching its own seed.

## 8. Getting the data (it is not in git)

1. Open https://doi.org/10.21227/6jr9-5235 (redirects to the DataPort page; a free IEEE DataPort login may be asked for).
2. Download the **Measurement Data** files: the 70 `TripXnn.csv`, `Overview.xlsx` and `readin.m`.
3. Put them directly in `data/raw/bmw_i3/` (so `data/raw/bmw_i3/TripA01.csv` exists; not in a sub-folder; a zip may unpack into a
   folder called `Measurement Data`, which must be renamed).
4. Check: `uv run python -m verity.datasets.bmw_i3` prints `{'status': 'loader_ready', 'n_trips': 70, ...}`.
