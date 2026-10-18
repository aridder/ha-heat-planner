# Heat Planner

Plan your heating around electricity prices, presence and a model of each room – and see exactly why it does what it does.

![Heat Planner card](docs/card.png)

Heat Planner is a Home Assistant integration with a built-in dashboard card. For every heated zone it builds an hourly plan for the next 36 hours from:

- **Your day curve** – one per weekday, plus one-off curves for a single date. Drag across the hours in the card to paint a new curve.
- **Electricity prices** – from the core **Nord Pool** integration or any price sensor (Nordpool from HACS, Tibber, ENTSO-e, Energi Data Service …). Quarter-hour prices are supported.
- **Presence** – lowers the temperature while nobody is home, but is warm again by the time you usually get back.
- **A room model** – learned every night from your thermostat's history, so the card can show the expected room temperature hour by hour.

It then sets your thermostats. Change a thermostat by hand and Heat Planner steps back until the next change in the plan.

## What the price logic does

| Situation | Effect |
| --- | --- |
| One of the day's 3 most expensive hours | Lower by 1 °C (configurable) |
| Cheap hour with expensive hours within 3 h | Raise by 1 °C to store heat in the floor |
| Extreme price ahead (≥ 1.5 × day average and ≥ 2/kWh, or ≥ 3/kWh) | Raise by 2 °C beforehand |
| Extreme price now | Heating off – never below a safety temperature |
| Warm-up window (default 13–15) | Prices are ignored so it is warm when you get home |

Prices only shift *when* heat is used; comfort stays where you put it. Choose a strategy per zone: ignore prices, gentle, balanced or aggressive.

## Zones

- **Temperature** – a `climate` entity. The schedule stores offsets from a comfort temperature, so moving the comfort temperature moves the whole curve.
- **Percent** – for thermostats running without a sensor (regulator mode), e.g. a bathroom floor. The schedule stores heating demand in percent and Heat Planner writes it to a `number` entity.

## Installation

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/aridder/ha-heat-planner` as *Integration*.
2. Install **Heat Planner** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Heat Planner.** Pick a price source, who counts as "home" and a weather entity.
4. **Configure → Add zone** for each heated room.
5. Add the card to a dashboard:

```yaml
type: custom:heat-planner-card
# optional
zones: [stue, kjokken]
hours: 36
```

The card is served by the integration itself; there is no separate frontend resource to add.

## Editing in the card

![Editing a day](docs/edit.png)

**Edit day** → pick a day → drag across the hours. The expected room temperature and cost update while you drag. Save for *only that day*, *every Monday*, *weekdays*, *weekend* or *all days*. **Back to schedule** removes a one-day curve.

Under **Settings**: comfort temperature, price strategy, how much to lower and raise, extreme-price cut-off and threshold, the away setback, when it should be warm again, and the warm-up window.

## Entities

Per zone: target, mode, expected temperature, estimated cost and savings for the next 24 h, automatic control (switch), comfort temperature (number), price strategy (select). Per home: the current price level.

## Services

- `heat_planner.set_schedule` – set 24 hourly values for all days, weekdays, weekend, selected days or one date.
- `heat_planner.clear_override` – go back to the standing schedule for a date.
- `heat_planner.resume` – end a manual pause.

## Development

```bash
pip install pytest-homeassistant-custom-component
pytest
```

---

## Norsk

Heat Planner planlegger oppvarmingen etter strømpris, hvem som er hjemme og hvordan hvert rom oppfører seg, og kortet forklarer hvorfor. Dra i døgnkurven for å endre den, og lagre for bare én dag, en ukedag, hverdager, helg eller alle dager. Kortet viser strømpris, mål og forventet romtemperatur time for time, og hva det koster.

Installer via HACS som beskrevet over. Integrasjon og kort er oversatt til norsk.
