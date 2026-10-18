"""Hand-written methodology and findings, placed at the top of ``RESULTS.md`` by ``report.py``.

The numbers quoted here are from ``results.json`` (seeds 1–3); re-check them after a re-run.
"""

FINDINGS = r"""
## What this is

An honest comparison of heating strategies on the `tools.sim` digital twin. Every strategy
controls the **same true house** (minute-by-minute physics, floor dead time, thermostat
hysteresis, sun, internal gains), with the **same schedule, comfort targets and information**:
noisy room sensors (none in the bathroom), a 48 h outdoor forecast whose error grows with lead
time, the household's effective price per hour (contract + Elvia grid, tomorrow published at
13:00), the whole-house meter, and the same *believed* room models (true parameters ±5–15 %).
Nobody gets presence: the two away days are a calendar setback for everyone. Metrics are taken
on the **true** room temperatures and the **true** house energy.

* **House:** 4 zones – `stue` (2 kW concrete floor, 2 h dead time), `kjokken` (1.2 kW, fast,
  panel-like), `sov` (0.8 kW thin floor), `bad` (0.8 kW bathroom floor on a percent regulator,
  no sensor). Base load from the twin (morning/evening use, random 2.2 kW cooking 17–19) – it,
  not heating, sets most capacity peaks.
* **Household:** work weekdays (out ~07:45–16:30 ± random), home at weekends, two away weekdays
  in week 2. Schedule: living room/kitchen 21 °C mornings and evenings (19/18 °C otherwise),
  bedroom 18–19 °C, bathroom 22 °C by day (measured target; the regulator is set per season to
  the lowest percent that holds it with the plain schedule on a tuning seed).
* **Scenarios:** 14 days from the 1st of the month (the capacity month starts with the run),
  Oslo NO1: **October** (8 → 4 °C), **January cold snap** (−4 → −15 °C, nights to −20, back to
  −5), **April** (4 → 8 °C, sunny middays). Seeds 1–3 (weather drift, clouds, prices, people,
  cooking) are reported; seeds 101–102 were used to tune the competitors and to develop our
  engine (held-out evaluation).
* **Contracts:** spot (no support), spot with *strømstøtte* (90 % above 0.77 NOK ex VAT),
  *Norgespris* (0.50 NOK incl VAT) – all with Elvia 2026 grid energy (0.364 day / 0.264 night
  and weekend) and Elvia 2026 capacity steps, pro-rated to 14 days.
* **Prices are synthetic** (the sandbox could not reach a price API): typical 2025–26 NO1 day
  shapes – night trough ~0.75×, morning peak ~1.3×, evening peak ~1.4×, flatter weekends – on
  seasonal levels (Oct ≈ 0.65, Jan 1.0 → 1.6 in the cold snap, Apr ≈ 0.45 NOK/kWh ex VAT), ±15 %
  day-to-day noise, varying day amplitude, spikes of 2.5–4× in a peak hour (Jan: 25 % of
  weekdays) and an April midday solar dip. See `world.spot_day`.

### Metrics

* **Total cost** = energy (contract) + grid energy + capacity step (Elvia, top-3 of the
  period's daily maxima, pro-rated 14/month) + a small correction for the heat stored in the
  rooms at the end (vs the schedule), for the whole house (base load included: differences
  between strategies are what matters).
* **Below target** (°C·h): while someone is home, how far each room is under its occupied
  target, summed over rooms. Also: below 18 °C, "too warm" (above the zone's highest target
  + 1 °C), longest run more than 1 °C under target, heating kWh, thermostat writes per day.
* **Value score** = total cost + **2 kr per °C·h more than 0.3 °C below the target**. The
  0.3 °C is the thermostats' own hysteresis: a room a thermostat holds "at 21 °C" averages
  ~20.85 °C, and counting that would mostly reward driving heaters as switches and running
  warm. The strict variant (from the target itself) is reported as *value (strict)*.

### Competitors (re-implemented from their docs and source – URLs and rules in `strategies.py`)

| key | what it does | defaults → tuned (grid search on the tuning seeds, `tuning.json`) |
|---|---|---|
| `fixed_schedule` | the schedule on the thermostats | – |
| `priceanalyzer` | PriceAnalyzer `_get_temperature_correction` (gaining / falling / day max / 5 most expensive), ±1 °C via its blueprint | multiply 1, 2 h boost/save → ×2, 3 h boost |
| `nordpool_diff` | triangle FIR of the coming prices, `max_min_sqrt_max`, offset = gain × sensor, ±2 °C | 10 h, gain 3 → 6 h, gain 1.5 |
| `cheapest_hours` | +1 °C in the day's 3 cheapest hours, −1 °C in the 4 dearest (aio_energy_management example) | → 6 cheapest +2 °C, 2 dearest −2 °C |
| `sparegris` | Piggy Bank price points (96 h average −40/−15/+25/+70 %, ≥ 4 low/high hours a day), ±1 °C, 5000 Wh/h power limiter | → limit 5 kWh (Oct/Apr) / 10 kWh (Jan), +2/−1/−2 °C |
| `emhass` | EMHASS thermal LP (heating_rate, cooling_constant, thermal_inertia), min/max comfort range, hourly MPC over 24 h; setpoint = published predicted temperature | → power mode: the optimised power switches the heater |
| `roommind` | model-based optimal start to the schedule, no prices | coasting variant tested; defaults kept |
| `heat_planner_*` | ours: rules / optimiser / optimiser + capacity limiting, integration defaults | not tuned |

## Plain-language findings

1. **Against the price add-ons (PriceAnalyzer, nordpool_diff, cheapest hours, Sparegris),
   Heat Planner wins every season and contract, also against their tuned settings** – by
   20–420 kr of value score per 14 days. Fixed ±1–2 °C offsets save little money (under
   *strømstøtte* and Norgespris the spread is small) and cost comfort, because a slow floor
   answers an offset hours later. Sparegris' power limiter wins capacity steps we do not (4).
2. **The real competition is model-based control.** Heat Planner has the lowest or equal-lowest
   comfort deficit (> 0.3 °C) in 8 of 9 cells. On the value score it is best in January with
   *strømstøtte* (1731 ± 31 vs RoomMind 1754, EMHASS 1780) and level with the best (±1–3 kr,
   well inside the seed spread) in October with *strømstøtte*, October Norgespris and January
   Norgespris. Tuned
   EMHASS (LP + power switching) is best on plain spot in October (910 vs our 934) and January
   (2483 vs 2538) and in April with spot prices (≈ 700 vs 724, spread ± 55–85); RoomMind is
   best under Norgespris in April (716 vs 733).
3. **Why EMHASS-power wins where it wins:** it switches the heaters from its LP, so there is no
   thermostat hysteresis (on the strict metric it is 10–67 °C·h below target against our 51–148,
   but it runs warm: 1.3–1.9× our "too warm" degree-hours) and – avoiding the dear evening hours – it more often keeps the house
   under 5 kW while cooking: one capacity step (110 kr/month ≈ 35 kr per 14 days) is the whole
   difference in April. It writes to the heaters 2–4× as often as we do (46–84 writes/day).
   In January on plain spot our cost is *lower* than EMHASS' (2377 vs 2392 kr); we lose on
   comfort, mostly in the bathroom, whose price-shifted regulator has no sensor.
4. **Where Heat Planner does not win, and why:**
   * **Capacity step:** `heat_planner_opt_cap` is identical to `heat_planner_opt` in every
     cell – the limiter never engages. It defends *last month's* step (here 5–10 kW) and plans
     cuts against a low (25th percentile) estimate of the rest of the house, so the random
     cooking peaks that set the month's top-3 are not seen. Aiming for the lower step (when last
     month was ≤ 1 kW above it) and pre-heating before cut hours were both tried: they did not
     get this house under 5 kW, raised the peak in the `tools.sim` "month_boundary" scenario and
     made the live guard flap (44 writes a day) – both reverted. This is the biggest open item.
   * **Percent zones without a sensor:** price shifting a regulator can be made energy-neutral
     (done, below) but not comfort-aware.
   * **Hysteresis:** we command setpoints and a thermostat restarts 0.3 °C under them; on the
     strict metric that costs us 30–80 °C·h per 14 days against EMHASS-power.
5. **Norgespris:** nothing worth shifting (0.10 NOK grid day/night spread), so Heat Planner keeps
   the schedule plus its warm-up – the same result as RoomMind. Tools that act on the grid spread
   anyway (nordpool_diff normalises the spread away; cheapest hours) lose comfort for ~nothing.

## Changes made to the engine (seeds 1–3, before → after)

Found on the development seeds, checked on the held-out reported seeds:

1. **Optimal start ("warm-up", `planner.warmup_targets`).** Before, the optimiser judged comfort
   against what the schedule itself achieves, so a slow floor was 1–2 °C under the schedule for
   hours after every morning/afternoon step, and RoomMind beat us on comfort in every cell. Now
   the room model finds how early each upward step must start to be reached on time; rules mode
   raises those hours, the optimiser gets the warm-up as its comfort reference (it may warm
   earlier, in cheaper hours) and never plans a warm-up hour below the coming target. It also
   works without prices, under Norgespris and without a room reading. Savings are compared with
   the schedule *including* its warm-up, so its energy is not counted as a cost of price control.
2. **Percent zones move heat instead of removing it (`planner._balance_percent`).** The rules'
   −15 points in dear hours were only partly given back (+10 before them), leaving the bathroom
   colder than with a plain schedule; now what is taken is put back in the same day's cheapest
   hours.
3. Benchmarked and **reverted**: capacity step-down targeting and pre-heating before capacity
   cuts (see 4 above).

| variant | season | contract | cost (kr) | below target (°C·h) | of which > 0.3 °C | value |
|---|---|---|---:|---:|---:|---:|
| heat_planner_rules | oct | spot+støtte | 881 → 883 | 178 → 68 | 100 → 15 | 1082 → 913 |
| heat_planner_rules | jan | spot+støtte | 1653 → 1667 | 348 → 127 | 236 → 53 | 2125 → 1772 |
| heat_planner_rules | apr | spot+støtte | 689 → 648 | 233 → 100 | 149 → 42 | 986 → 732 |
| heat_planner_opt | oct | spot | 924 → 929 | 127 → 51 | 56 → 3 | 1037 → 934 |
| heat_planner_opt | oct | spot+støtte | 877 → 882 | 131 → 53 | 59 → 3 | 995 → 888 |
| heat_planner_opt | oct | Norgespris | 660 → 663 | 176 → 56 | 100 → 4 | 859 → 670 |
| heat_planner_opt | jan | spot | 2374 → 2377 | 399 → 148 | 291 → 80 | 2957 → 2538 |
| heat_planner_opt | jan | spot+støtte | 1655 → 1670 | 294 → 95 | 190 → 31 | 2034 → 1731 |
| heat_planner_opt | jan | Norgespris | 1056 → 1064 | 303 → 111 | 198 → 41 | 1452 → 1146 |
| heat_planner_opt | apr | spot | 678 → 663 | 167 → 82 | 94 → 31 | 867 → 724 |
| heat_planner_opt | apr | spot+støtte | 677 → 662 | 168 → 82 | 94 → 30 | 865 → 723 |
| heat_planner_opt | apr | Norgespris | 661 → 663 | 219 → 97 | 133 → 35 | 926 → 733 |

("Before" = the engine at commit 0e90c6a run through the same benchmark.) Comfort deficits
fall by a factor 2–3 at a cost of 0–15 kr per 14 days (April got cheaper: the warm-up spreads
the morning heating and lowers peaks).

## Caveats

* One synthetic house and synthetic prices; three seeds per cell. Differences smaller than the
  ± spread (October and January Norgespris among the top three; April) are ties.
* Competitors are re-implementations of documented rules, not their code running in Home
  Assistant. EMHASS' LP is solved with our own interior-point solver (`lp.py`), its hard
  minimum is a stiff penalty (10 kr/°C·h) so it stays feasible, and its bathroom (no sensor)
  follows the schedule. RoomMind's EKF learning is not modelled – it gets the same believed
  model as everyone.
* All model-based strategies get the same ±5–15 % model error; with a worse (or better) model
  the ranking between them can change.
* The value score's 2 kr/°C·h and 0.3 °C band are choices; costs, both deficits and the
  strict value are all in the tables.
* Run time: about 14 minutes on 2 cores (≈ 1600 CPU s), so under 10 minutes from 4 cores.
"""
