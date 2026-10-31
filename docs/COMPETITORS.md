# Competitor scan (October 2026)

Why this exists: the landing page (`web/home.html`, section 05) compares Verity with the tools a driver or
fleet uses today. This file records what that comparison is based on, so every claim can be checked.

Method: web search in October 2026. The competitor sites themselves could not be fetched from the build
sandbox (network policy), so the notes below come from search-indexed product pages, help centres, store
listings and guides. Treat vendor numbers as the vendor's own claims. Products change; re-check before quoting.

## Route planners: "predict a number"

| Product | What it does (as described publicly) | Source |
|---|---|---|
| A Better Routeplanner (ABRP) | Plans EV trips with charging stops and predicts state of charge on arrival at each stop. Accounts for vehicle, speed, temperature, elevation. Supports 1000+ models. Live car data improves estimates; route planning with live data is in Premium. v6.0 mentions "improved charger reliability insights". | [abetterrouteplanner.com/home](https://abetterrouteplanner.com/home), [ABRP 6.0 notes](https://abetterrouteplanner.com/resources/article/2025-05-09_abrp-6-0) |
| ABRP in practice | Guides advise keeping a 10–15% arrival buffer. Users report accuracy ranging from "within 2–3%" to arriving well below the prediction (anecdotes, not benchmarks). | [evcourse ABRP guide](https://www.evcourse.com/how-to-use-abrp-road-trip), [Tesla Motors Club thread](https://teslamotorsclub.com/tmc/threads/about-abetterrouteplanner.220863/page-2) |
| Chargetrip | B2B EV routing API (GraphQL) embedded by OEMs, networks and fleets (e.g. Plugsurfing's route planner). Claims 95% range-model accuracy against telemetry and 2,000+ EV models (vendor claim). Its FAQ says charger status shows "Unknown" when it is not known. | [chargetrip.com](https://www.chargetrip.com/), [routing product](https://www.chargetrip.com/products/routing), [API FAQ](https://help.chargetrip.com/faq/api/) |
| Statiq | Indian charging network app with a trip planner: suitable charging stops, saved routes, amenities; lists 6000+ stations (vendor). | [Statiq trip planner](https://blog.statiq.in/2024/08/20/how-to-use-trip-planner-in-the-statiq-app), [Statiq blog](https://statiq.in/blog/2026/05/15/best-ev-charging-app-in-india-features/) |
| EVJoints | Indian EV trip planner; store listings cite ~4,000 integrated stations with live status across many networks (counts differ between listings). | [Google Play](https://play.google.com/store/apps/details?id=com.evjoints), [App Store](https://apps.apple.com/in/app/evjoints-ev-trip-planner/id1670634514) |
| HERE charge-aware routing (India) | Shows remaining range along the route from battery consumption. | [HERE press release](https://www.here.com/about/press-releases/HERE-announces-HERE-charge-aware-routing-services-in-India) |
| Tata Power EZ Charge | Network app: live map, availability, RFID tap-and-charge, ratings; route planning less prominent in public material. | [Tata Power blog](https://www.tatapower.com/blogs/the-top-ev-charging-app-in-india-just-got-smarter-new-features) |

## Charger maps: "crowdsource a score"

| Product | What it does | Source |
|---|---|---|
| PlugShare | Global charger map with community check-ins, reviews and photos; trip planner. The 1–10 PlugShare Score is built from recent check-ins; guides say newer and verified check-ins weigh more. Critics note one number hides partial outages and depends on drivers checking in. | [PlugShare trip planner](https://www.plugshare.com/trip-planner.html), [score explained](https://www.evcourse.com/plugshare-station-score-explained), [CleanTechnica analysis](https://cleantechnica.com/2023/01/19/what-a-plugshare-score-really-means-how-it-could-be-better/) |

## Fleet tools

Chargetrip (fleet routing via Arval), Make My Day (charging optimisation API), NextBillion.ai (dispatch-ready
routes), Geotab marketplace EV routing. None of the public material found shows a tested confidence level,
a refusal mode, or adversarial-report handling. Sources: [eu-startups on Chargetrip](https://www.eu-startups.com/2023/03/amsterdam-based-chargetrip-fuels-up-with-e10-million-to-scale-ev-routing-in-the-us/),
[Geotab EV routing](https://marketplace.geotab.com/solutions/ev-route-planning-and-dispatching/),
[NextBillion.ai](https://nextbillion.ai/feeds/service/ev-fleet-management-platforms).

## Market context used on the landing page

- "~1 in 6 public stations not working": MeraEV blog, 27,737 stations of which ~22,753 working
  ([source](https://www.meraev.com/blog/india-ev-charging-network-2026-status-reliability)). Third-party
  estimate, not an audit; the page labels it that way.

## Where Verity is different (what the landing page claims)

1. The answer is a decision with a confidence level (GO / NO-GO), or an explicit refusal (INSUFFICIENT_EVIDENCE).
2. Uncertainty is a p10–p90 band whose calibration is tested (E2), not a single number plus a habitual buffer.
3. Unverified chargers stay "unknown" and the plan carries a reachable backup for each stop.
4. Reports are weighted by learned source reliability with ring detection, tested against attacks (E5),
   including one attack that is not caught.
5. Results are published with sample sizes and intervals. All Verity results are from simulated worlds; no
   real-road validation exists yet. Say this whenever the comparison is presented.

## Design references

Motion patterns follow [motion.dev](https://motion.dev) (springs, scroll-linked progress, in-view reveals; the
vanilla build is vendored as `web/vendor/motion-12.43.0.js`, MIT) and common [21st.dev](https://21st.dev)
landing patterns (spotlight bento cards, border beam, animated beams, number tickers, marquee), re-implemented
in plain CSS/JS.
