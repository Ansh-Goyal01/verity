# Data collection guide (for the person filling the CSVs)

Welcome! You don't need to code for this. You will copy facts **by hand** from public web pages into
Google Sheets, then upload them as CSV files. Each row you add becomes real evidence that the judges
can check, and that is what sets Verity apart: other teams show simulations, we show sourced numbers.

There are four files to fill in `data/corpus/`, plus one private file (vehicle specs,
`vehicles_india.csv`, are filled in separately). Each corpus file already has its exact header row.
**Never rename, reorder or retype a header**: copy it.

| File | What one row is | Why the judges care |
|---|---|---|
| `trips_india.csv` | one real trip, consumption figure or range test of an Indian EV | We test our range forecasts against real Indian driving, not just our own simulator |
| `charger_evidence.csv` | one dated public report that a specific charger worked or failed | Our charger-trust model learns which sources to believe from real reports |
| `app_status_snapshots.csv` | what an operator app showed for a charger at one moment | We can check how often the app said "available" when people said it was broken |
| `charger_match_overrides.csv` | your confirmation that an evidence row or snapshot is a specific charger in our directory | Rows the computer could not match safely are listed in `data/processed/charger_evidence_review.csv`; you settle each one by hand |
| `app_reviews.csv` | one app-store review, **paraphrased** (no review text) | We measure what drivers complain about, as published research did for the USA |
| `data/private/app_reviews_text.csv` | the full review text | Needed to test automatic classification; it is **never** uploaded to GitHub (see step 6) |

## The rules (please read twice)

1. **Copy by hand.** Never use scraping tools, browser extensions that bulk-download, or copy-paste
   bots. Google Maps, the Play Store, X and Team-BHP forbid automated collection.
2. **Always a link.** Every row needs the exact page link (`source_url`). No link means no row.
3. **No names of people.** No reviewer names, usernames or @handles anywhere, including in notes.
4. **Paraphrase in at most 15 words, in your own words.** Do not copy sentences from reviews or posts.
5. **Numbers exactly as published.** If the page gives a range ("65–70 km/h"), leave the number cell
   empty and write the range in `notes`. Never average, guess or round. If you don't know, leave it empty.
6. **`verified = yes` only after checking.** Open the link again, check every number in your row
   against the page, and fill `verified_by` with your initials. Until then keep `verified = no`.
   Rows that are not `verified = yes` are left out of every result.
7. **Dates as YYYY-MM-DD** (for example `2026-11-14`). Trip month alone is allowed as `YYYY-MM`.
   Times as 24-hour `HH:MM` (`18:45`). Snapshot moments as `YYYY-MM-DD HH:MM` in Indian time.
8. **Allowed values are lower case and exact**, for example `on`, not `On` or `yes`.
9. Use a new ID for every row and never reuse one: `T0007`, `T0008`... for trips, `E0001`... for charger
   evidence, `S0001`... for snapshots, `R0001`... for reviews.

## Where to find sources

- **Trips** (`trips_india.csv`): owner threads and trip reports on Team-BHP; road tests and range tests
  in Autocar India, CarDekho, CarWale, evo India, Rushlane, Cartoq; YouTube range-test videos (use the
  video link and note the timestamp in `notes`).
- **Charger evidence** (`charger_evidence.csv`): Google Maps reviews of a charging station (use the
  review's date), PlugShare check-ins, the four operator apps (Tata Power EZ Charge, Statiq, Jio-bp pulse,
  ChargeZone), posts to operators on X, forum threads and news. Focus on Delhi NCR and the Noida–Agra
  (Yamuna Expressway) corridor.
- **App snapshots** (`app_status_snapshots.csv`): open an operator app and write down what it shows for
  the same chargers, 2–3 times a day.
- **App reviews** (`app_reviews.csv`): Play Store / App Store reviews of the four operator apps.

## Targets for the week

- 40–60 trips.
- 40–60 chargers with evidence, in Delhi NCR and on the Noida–Agra corridor.
- App snapshots 2–3 times a day for 6 days, on the **same** chargers each time.
- 200–300 app reviews, of which 50 carry your own labels (`human_category`, `human_claim`).

Partial data is fine. Every pipeline re-runs on whatever is there and reports the real count.

## One worked example per file

**trips_india.csv.** This is seed row T0001, already in the file but still `verified = no`:

| column | value |
|---|---|
| record_id | T0001 |
| record_type | soc_trip (`soc_trip` = start and end battery %, `consumption` = Wh/km figure, `range_test` = drive until nearly empty) |
| source_url | https://www.team-bhp.com/news/bhubaneswar-kolkata-back-my-xev-9e-key-highlights-trip-stats |
| source_kind | owner_forum (`owner_forum`, `magazine_test`, `owner_story`, `video`) |
| make / model / battery_kwh_nominal | Mahindra / XEV 9E / 79 |
| origin / destination / one_way_or_round | Kolkata / Midnapore / unknown (`one_way`, `round_trip`, `unknown`) |
| distance_km / start_soc_pct / end_soc_pct | 302 / 83 / 22 |
| ac / regen_mode | on / L1 |
| speed_band | (empty: choose `city`, `mixed`, `highway_80_100` or `highway_100_plus` once you've read the page) |
| notes | Early summer; avg 65-70 km/h reported; check if 302 km is one way or includes local driving |
| entered_by / verified / verified_by | seed:claude-code / no / (empty) |

Your job for this row: open the link, confirm each number, answer the question in `notes`, add
`source_published_date`, then set `verified = yes` and your initials in `verified_by`. The other seed
rows (T0002–T0006) each have a note saying what to check or fill in.

Unit tip: if a page gives km/kWh, convert it to Wh/km as 1000 ÷ (km/kWh), and say so in notes
(7.9 km/kWh → 126.6 Wh/km).

**charger_evidence.csv.** FICTIONAL EXAMPLE, for format only; do not copy:

```
evidence_id,charger_name,operator,landmark_or_address,lat,lon,place_url,source_kind,evidence_date,evidence_time,status,connector,rated_kw,queue_count,source_url,paraphrase,collected_by,verified
E0001,Example Mall DC Hub,ExampleOp,Example Mall basement parking Sector 99,,,https://maps.example.org/place/123,google_review,2026-11-03,,broken,CCS2,60,,https://maps.example.org/review/456,DC charger showed error and would not start a session,AS,no
```

`status` is one of `working`, `broken`, `partially_working`, `queue`, `no_power`, `access_blocked`,
`payment_failed`, `unknown`. Give either the Google Maps place link (`place_url`) or both `lat` and
`lon`. Leave a cell empty when the source doesn't say it (time, kW, queue).

**app_status_snapshots.csv.** FICTIONAL EXAMPLE:

```
snapshot_id,app,charger_name,charger_ref_in_app,datetime_ist,status_shown,connectors_available,rated_kw,collected_by
S0001,statiq,Example Mall DC Hub,EX-123,2026-11-14 18:45,available,1,60,AS
```

`app` is `tata_ez_charge`, `statiq`, `jiobp_pulse`, `chargezone` or `other`; `status_shown` is
`available`, `busy`, `offline`, `faulty` or `unknown`. Use the charger name exactly as you wrote it in
charger_evidence.csv so the two can be matched.

**app_reviews.csv.** FICTIONAL EXAMPLE (no review text in this file!):

```
review_id,app,review_date,stars,language,paraphrase,human_category,human_claim,collected_by
R0001,chargezone,2026-10-20,1,hinglish,App showed charger free but it was not working,functionality,wrong_status,AS
```

`language`: `en`, `hi`, `hinglish`, `other`. Label 50 reviews with `human_category` (one of
`functionality`, `availability`, `cost`, `location`, `dealership`, `user_interaction`, `service_time`,
`range_anxiety`, `other`) and `human_claim` (`charger_not_working`, `wrong_status`, `payment`, `app_bug`,
`queue`, `other`). Leave both empty on unlabelled reviews. The category list comes from Asensio et al.,
Nature Sustainability 2020, who did this for 127,257 US charging-station reviews.

The full review text goes in a **separate** sheet with just two columns, `review_id,text`, saved as
`app_reviews_text.csv`. It never goes to GitHub (step 6).

**charger_match_overrides.csv.** After a pipeline run, open `data/processed/charger_evidence_review.csv`.
Each line is a row the computer could not match to a charger (with the reason and the nearest candidate id).
Open the row's link, find the charger in `data/processed/chargers_ncr.json` (search its name), and add one line
here only if you are sure. FICTIONAL EXAMPLE:

```
row_id,charger_id,confirmed_by,notes
E0007,CH-0042,your-initials,same map pin and same operator name
```

## Working in Google Sheets, step by step

1. Create one Google Sheet per file, named exactly like the file without `.csv` (for example `trips_india`).
2. Open the template in the repository on GitHub (`data/corpus/trips_india.csv`), copy its first line,
   paste it into cell A1, then use **Data > Split text to columns** (separator: comma). Freeze row 1.
3. Before typing dates, select the date columns and choose **Format > Number > Plain text**, so Sheets
   doesn't change `2026-11-14` into another format.
4. Add your rows. One row = one trip / report / snapshot / review.
5. Export: **File > Download > Comma-separated values (.csv)**. Sheets names the download like
   `trips_india - Sheet1.csv`; rename the downloaded file to exactly `trips_india.csv` before uploading.
6. Upload to GitHub: open the repository, go to the `data/corpus` folder, click **Add file > Upload
   files**, drop the CSV in, write a short message such as "add 8 trips", choose **Commit directly to the
   main branch**, and click **Commit changes**. The file on GitHub must keep its exact name so it replaces
   the old one. **Never upload `app_reviews_text.csv`** to GitHub; share that sheet privately with Ansh
   (Google Drive), who places it in `data/private/` on his machine, where git ignores it.
7. Ask Ansh to run the checker (or run it yourself if you have the repository):
   `uv run python scripts/validate_corpus.py`. It prints messages such as
   `ERROR trips_india.csv row 5 (T0004): start_soc_pct (20) must be larger than end_soc_pct (40)`.
   The row number is the row in your sheet. `ERROR` rows are left out of results until fixed; `WARN`
   lines are a to-do list for drafts.
