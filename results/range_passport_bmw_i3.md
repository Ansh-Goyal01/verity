# Range Passport - BMW i3 60 Ah (TU Munich test car)

**REAL trips (BMW i3, Germany); range on Indian routes is the engine's SIMULATION** - status: **OK** (schema `verity.range_passport.v1`)

Evidence: 40 logged trips, 728.2 km, 512.0 SoC points used (observed on those trips: 142.2 km per 100 SoC points; data, not model).

## Usable battery

- Usable capacity: **19.39 kWh** (80 % credible interval 17.97-20.53; 95 % 17.19-20.99)
- That is 90% of the 21.6 kWh nominal pack.

## Real-world range in Indian conditions

| Reference route | Range 100->0 % (p10 / p50 / p90) | Wh/km (p50) | Conditions |
|---|---|---|---|
| Noida -> Agra highway, afternoon (190.8 km) | 147 / **177** / 211 km | 108 | AC on, 2 people |
| Noida -> IGI Airport T3, evening (41.5 km) | 244 / **280** / 324 km | 69 | AC on, 2 people |

## Method

Bayesian calibration of the physics energy model on the logged trips' SoC drops (Laplace posterior), then the Monte Carlo on fixed reference routes. Usable capacity = kWh per 100 SoC points as the vehicle's own gauge counts them.

## Validation

**REAL: BMW i3 ground truth from integrated measured current**

Passport estimate 19.39 kWh (80 % CI 17.97-20.53) vs measured 22.7 kWh per 100 SoC points (-14.6 %); truth inside the 95 % interval: False. The model caps usable capacity at 1.05 x nominal = 22.68 kWh. On its own logged trips this car drove 142.2 km per 100 SoC points (measured, German conditions incl. winter heating); the Indian-route ranges above are model outputs NOT validated against measured Indian driving, and the capacity bias suggests the model under-predicts this car's consumption.

Intervals come from the model; the reference routes and their sources are listed in the JSON. Not a warranty.
