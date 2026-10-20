# Changelog

## 0.4.1

- **Look back:** the card now has *Plan / Last 24 h / Last 48 h*. The past view shows:
  - the measured room temperature against the target as it was applied, with the price
  - markers for manual changes, capacity set-backs and heating off
  - the house's hourly energy against the cap
  - a summary: average vs target, hours below target, kWh and estimated cost
- **History log:** kept for 7 days and filled in from Home Assistant's own history the first time, so the last 48 hours show up immediately. Available over the websocket command `heat_planner/history`.
- **Help:** new "Look back" section.

## Unreleased

### Look back
- **History in the card:** "Plan / Last 24 h / Last 48 h" above the chart. Looking back shows what actually happened hour by hour: the target and mode that were applied (and why), the measured room temperature (solid) against the prediction (dashed), faded prices, markers for manual changes, capacity set-backs and off hours, and the home's hourly energy against the cap. A summary gives the average against target, hours below target, kWh with an estimated cost and the highest house hour. Percent rooms without a sensor show their heating demand (and on-time, when reported).
- **Rolling log:** 7 days per room and hour, stored apart from the settings and kept across restarts; after a first start or a gap the last 48 hours are rebuilt from the recorder. Thermostats reporting 0 °C (no floor sensor) are ignored as room temperatures. Both 02:00 hours of the autumn DST change are kept.
- **API:** websocket command `heat_planner/history` (`entry_id`, `hours` ≤ 168) – the regular export stays small.

## 0.4.0

The first published release. It contains everything since 0.1.0.

### Plan around what you actually pay
- **Electricity contract:** spot, spot with Norwegian *strømstøtte*, or a fixed price such as *Norgespris*. Set it in the setup step "Electricity contract and capacity tariff".
- **Grid tariff:** the energy part of the grid tariff is included hour by hour, with day/night and weekend rates. Elvia 2026 is a preset, or you can enter your own. Prices that already include VAT, and VAT-exempt areas (NO4), are handled.
- **Flat price days:** on days with almost no price spread, no heat is moved.

### Capacity tariff (*effekttrinn*)
- **Planning:** with a whole-house power meter (Tibber Pulse, HAN reader …), heating is spread between rooms so the average of the month's three highest hours stays in the current step.
  - Lower-priority rooms give way first.
  - The warm-up window is never touched.
  - Cuts that would not lower a day's peak are dropped again.
- **Live guard:** a guard watches the current hour and sets low-priority rooms back until the hour ends if the house is heading over the cap.
- **Sensors:**
  - capacity step
  - monthly peak average
  - headroom this hour

### Comfort on time (from the benchmark)
- **Optimal start (warm-up):** heating now starts early enough, from the room model, that a room *reaches* the next schedule step on time instead of starting to warm when it begins (slow floors were 1–2 °C under the schedule for hours after every step). Works in rules and optimiser mode and under Norgespris; the optimiser may warm up earlier, in cheaper hours. Savings are compared with the schedule *including* its warm-up, so the warm-up's energy is not counted as a cost of price control. New reason in the card: "Warming up to reach the schedule on time".
- **Percent zones (regulators):** price shifting now moves heat to the day's cheaper hours instead of only removing it in the expensive ones (the bathroom ended up colder than with a plain schedule).
- **Benchmark:** `python -m tools.bench` compares Heat Planner with re-implementations of PriceAnalyzer, nordpool_diff, cheapest-hours automations, Homey Sparegris, an EMHASS-style LP and a RoomMind-style MPC on the same simulated house (see `tools/bench/RESULTS.md`).

### Optimiser and honest savings
- **Optimiser:** a dynamic-programming optimiser chooses each hour's target to minimise cost at the same comfort. It respects:
  - the safety temperature
  - the warm-up window
  - the maximum number of hours off
  - your lower/raise limits
  - the delay of slow floors

  You can switch it off in the card.
- **Savings line:** the card shows cost, kWh, extra energy spent pre-heating, savings in kroner and % and any loss of comfort, and says plainly when price control saves little.
- **Realised savings:** sensors show savings for today and this month.

### A better model of each room
- **Learning:** solar gain (from the weather forecast) and floor delay are learned per room, and the card shows how good the model is.
- **Arrival times:** "Auto" warm-up hour, learned from when the household actually gets home.

### Everyday use
- **"Too cold / Too warm" buttons:** small, bounded nudges, at most 8 a day and ±2 °C, never at or below the safety temperature.
- **Built-in illustrated guide:** in English and Norwegian, as a "How it works" button in the card or as its own card.
- **Card polish:**
  - visual editor
  - keyboard and screen-reader support
  - mobile layout
  - loading and error states
  - number and time formats from your Home Assistant settings
- **Alerts:** the `heat_planner_alert` event, with optional phone notifications, for:
  - peak risk
  - an extreme price tomorrow
  - a poor room model

### Quality
- **Quality scale:** diagnostics, repair issues (no prices, meter unavailable, recorder missing), reconfigure flow and removal of stale room devices.
- **Code checks:**
  - 220+ tests
  - ≥95 % coverage
  - ruff
  - mypy
  - hassfest and HACS validation in CI
- **Logo and icon:** ship with the integration.

### Safety fixes
- **Safety temperature:** no stacked lowering (away + price + peak + guard) can push a room below it.
- **Max hours off:** carries across replans.
- **Presence:** people without working tracking no longer count as "home".

## 0.2.0
- Illustrated, bilingual "How it works" guide.

## 0.1.0
- First version: day curves you paint in the card, price rules, presence, a learned room model, a 36-hour plan with expected temperature and cost, and guided setup with optional AI.
