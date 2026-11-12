# Range Passport - Demo vehicle V07 (compact e-SUV, aged battery)

**SIMULATED: demo fleet trips (verity.sim)** - status: **OK** (schema `verity.range_passport.v1`)

Evidence: 6 logged trips, 421.1 km, 253.0 SoC points used (observed on those trips: 166.4 km per 100 SoC points; data, not model).

## Usable battery

- Usable capacity: **37.08 kWh** (80 % credible interval 33.12-40.62; 95 % 31.13-41.97)
- That is 82% of the 45.0 kWh nominal pack.

## Real-world range in Indian conditions

| Reference route | Range 100->0 % (p10 / p50 / p90) | Wh/km (p50) | Conditions |
|---|---|---|---|
| Noida -> Agra highway, afternoon (190.8 km) | 153 / **185** / 216 km | 201 | AC on, 2 people |
| Noida -> IGI Airport T3, evening (41.5 km) | 232 / **264** / 296 km | 140 | AC on, 2 people |

## Method

Bayesian calibration of the physics energy model on the logged trips' SoC drops (Laplace posterior), then the Monte Carlo on fixed reference routes. Usable capacity = kWh per 100 SoC points as the vehicle's own gauge counts them.

## Validation

**SIMULATED: checked against the simulator's hidden usable capacity**

Estimate 37.08 kWh vs simulator truth 32.87 kWh (+12.8 %); truth inside the 80 % interval: False.

Intervals come from the model; the reference routes and their sources are listed in the JSON. Not a warranty.
