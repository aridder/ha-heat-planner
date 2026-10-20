/* Heat Planner card – price & presence aware heating schedules.
 * No build step, no dependencies. Talks to the heat_planner websocket API.
 */
const VERSION = "0.6.0";
const SETUP_URL = "https://my.home-assistant.io/redirect/config_flow_start/?domain=heat_planner";
const INTEGRATION_URL = "https://my.home-assistant.io/redirect/integration/?domain=heat_planner";
const STALE_MS = 20 * 60e3; // the integration refreshes every 5 minutes
const LOAD_TIMEOUT_MS = 15e3;
const DEFAULT_HOURS = 36;

const I18N = {
  en: {
    loading: "Loading heat plan…",
    errTitle: "Couldn't load the heat plan",
    errTimeout: "Heat Planner didn't answer. Check that the integration is running.",
    retry: "Try again",
    emptyTitle: "Heat Planner isn't set up yet",
    emptyBody: "Add the integration to plan your heating around electricity prices and who is home.",
    emptyAction: "Add Heat Planner",
    noZonesTitle: "No rooms yet",
    noZonesBody: "Add a room under Settings → Devices & services → Heat Planner → Configure.",
    noZonesAction: "Open Heat Planner",
    noMatch: "None of the rooms picked for this card exist any more. Edit the card to choose rooms.",
    stale: (t) => `The plan was last updated ${t}. Heat Planner may have stopped – check the integration.`,
    unavailable: (n) => `${n}: the thermostat is unavailable, so the plan can't be applied right now.`,
    unavailableShort: "thermostat unavailable",
    actionFail: (m) => `Couldn't save the change: ${m}`,
    dismiss: "Dismiss",
    is: (n, t) => [`${n} is `, t, "."],
    noTemp: (n) => [`${n}.`],
    now: {
      comfort: (t) => ["Holding ", t, " now"], normal: (t) => ["Holding ", t, " now"], setback: (t) => ["Lowered to ", t, " now"],
      preheat: (t) => ["Pre-heating to ", t, " now"], save: (t) => ["Saving at ", t, " through an expensive hour"],
      off: () => ["Off now – electricity is extremely expensive"], away: (t) => ["Nobody home – ", t],
      manual: (h) => [`Changed by hand – back on plan at ${h}`], disabled: () => ["Automatic control is off"],
    },
    next: (t, h) => `, ${t} from ${h}.`,
    nextOff: (h) => `, off from ${h}.`,
    dot: ".",
    peak: (a, b, p) => ` Next price peak ${a}–${b} (up to ${p}).`,
    cost: (c, s) => `Next 24 h about ${c}` + (s ? `, ${s} less than without price control.` : "."),
    target: "Target", predicted: "Expected room temperature", price: "Price",
    modes: { comfort: "Comfort", normal: "Normal", setback: "Lowered", preheat: "Pre-heat", save: "Saving", off: "Off", away: "Away" },
    levels: { cheap: "cheap", normal: "normal", expensive: "expensive", extreme: "extreme" },
    reasons: {
      schedule: "Schedule", override: "Changed for this day", away: "Nobody home", expensive: "One of the most expensive hours",
      preheat: "Cheap hour before expensive hours", preheat_extreme: "Storing heat before an extreme price",
      extreme_off: "Extreme price – heating off", protected: "Warm-up window, price ignored",
      optimized: "Chosen by the optimiser for the lowest cost at the same comfort", peak_limit: "Keeping the monthly peak down (capacity tariff)",
      safety: "Kept above the safety temperature", warmup: "Warming up to reach the schedule on time",
    },
    nowLabel: "now", offShort: "off", edit: "Edit day", done: "Done", today: "Today", tomorrow: "Tomorrow",
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    daysLong: ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"],
    dragHint: "Drag across the hours to paint a new curve – or use the arrow keys.",
    applyTo: "Save for", onlyDay: (d) => `Only ${d}`, every: (d) => `Every ${d.replace(/s$/, "")}`, weekdays: "Weekdays", weekend: "Weekend", all: "All days",
    discard: "Discard", save: "Save", saved: "Saved", resetDay: "Back to schedule", overrideNote: "This day has its own curve.",
    settings: "Settings", auto: "Automatic control", comfort: "Comfort temperature",
    strategy: "Price strategy", strategies: ["Ignore", "Gentle", "Balanced", "Aggressive"],
    shift: "Lower in expensive hours", preheatS: "Raise before expensive hours", extremeOff: "Turn off at extreme prices",
    extremeAbs: "Extreme price from", safety: "Never cut off below", away: "Lower when nobody is home", warmFrom: "Warm again from",
    protectFrom: "No price saving from", protectTo: "No price saving until",
    model: (g, tau, l) => `Room model: heats ${g}°/h, loses half its warmth in ${tau} h${l ? " (learned from history)" : " (from your home description)"}.`,
    power: (kw, src) => `Heater ${kw} kW${src === "measured" ? " (measured)" : src === "estimate" ? " (estimate)" : ""}.`,
    resume: "Resume plan", manualBadge: "Manual", help: "How it works", optimizeLbl: "Optimise (all rooms)", closeHelp: "Close help",
    fbCold: "Too cold", fbWarm: "Too warm", fbLabel: "How does it feel?", fbFail: "Could not send feedback.",
    cost24: (c, k) => `Next 24 h about ${c} · ${k} kWh.`,
    saves: (s, p) => ` Saves ${s}${p ? ` (${p})` : ""}`,
    kwhMore: (k) => `, uses ${k} kWh more.`, kwhLess: (k) => `, uses ${k} kWh less.`,
    littleSaving: " Price control saves little today.",
    deficit: (d) => ` About ${d} degree-hours below target.`,
    step: (l) => `Capacity step ${l}`, top3: (v) => `avg top 3: ${v} kW`, capToday: (v) => `cap today ${v} kW`,
    nextStep: (k, c) => `next step at ${k} kW` + (c ? ` (+${c}/month)` : ""), homeLoad: "Home load",
    quality: { good: "good", fair: "fair", uncertain: "uncertain" },
    fit: (r, q) => `Hits within ±${r}°` + (q ? ` (${q})` : "") + ".", fitQ: (q) => `Model confidence: ${q}.`,
    solar: "Accounts for sunshine.", lag: (h) => `Floor responds after about ${h} h.`,
    autoLbl: "Auto", autoAt: (h) => `Auto (${h})`,
    arrival: (t) => `You usually get home around ${t}.`, arrivalLearning: "Still learning when you get home.",
    rooms: "Rooms",
    chartLabel: (z, n) => `Heating plan for ${z}, next ${n} hours`,
    chartKeys: "Left and right arrow keys move between hours. Enter edits the day.",
    editKeys: "Left and right arrow keys move between hours, up and down change the target. Escape stops editing.",
    dec: (l) => `Lower: ${l}`, inc: (l) => `Raise: ${l}`, onW: "on", offW: "off",
    editing: (d) => `Editing ${d}.`,
    // header, chart panels and tiles (0.6)
    targetNow: "Target now", levelNow: "Heating level now", valFrom: (v, h) => `${v} from ${h}`, offFrom: (h) => `Off from ${h}`,
    noChange: "No change ahead",
    st: {
      preheatPeak: (a) => `Pre-heating before the price peak ${a}`, preheat: "Pre-heating in a cheap hour",
      save: "Saving in an expensive hour", setback: "Lowered by the schedule", away: "Nobody home – lowered",
      peak: "Keeping the power peak down", comfort: "Comfort by the schedule", normal: "Following the schedule",
      warmup: "Warming up for the schedule", safety: "Kept above the safety temperature", off: "Off – extreme price",
      manual: (h) => `Changed by hand – back on plan at ${h}`, disabled: "Automatic control is off",
    },
    peakSoon: (a, p) => `Price peak ${a}, up to ${p}`,
    priceLbl: (u) => `Electricity price, ${u}`, houseLbl: "Whole house, kW", capLbl: (v) => `cap ${v} kW`,
    flatFixed: (p) => `Fixed price ${p} – no price control needed`,
    flatEven: (p) => `Even price ${p} – little to gain from price control`,
    expected: "Expected", measured: "Measured", house: "House", planned: "planned", keyTarget: "Target", keyPlan: "Plan", edCurve: "Your curve",
    tooCold: "too cold", tooWarm: "too warm",
    tNext: "Next 24 h", tSave: "Price control saves", tSaveSub: (p, k) => [p, k].filter(Boolean).join(" · "),
    tLittle: "almost nothing today", kwhMoreS: (k) => `uses ${k} kWh more`, kwhLessS: (k) => `uses ${k} kWh less`,
    tStep: "Capacity step", tTop3: (v) => `avg top 3: ${v} kW`, tCap: "Cap today", tNextStep: (k) => `next step at ${k} kW`,
    deficitS: (d) => `About ${d} degree-hours below target in the next 24 h.`,
    pAvg: "Average", pAvgSub: (tg) => `target ${tg}`, pBelow: "Hours below target", pBelowSub: (d) => `${d}° or more under`,
    pAvgPct: "Average level", pEnergy: "Energy", pTop: "Highest house hour", pCap: (c) => `cap ${c} kW`, pOver: (c) => `over the cap of ${c} kW`,
    hoursN: (n) => (n === 1 ? "1 hour" : `${n} hours`),
    // card editor
    edEntry: "Heat Planner setup", edZones: "Rooms", edZonesHelp: "Leave all unticked to show every room.",
    edHours: "Hours to show", edHelp: "Show “How it works”", edLoading: "Loading rooms…", edNoData: "Heat Planner isn't set up yet – the card will show every room once it is.",
    hUnit: "h",
    // look-back view
    rangeLbl: "Time range", rPlan: "Plan", r24: "Last 24 h", r48: "Last 48 h",
    histLabel: (z, n) => `Heating history for ${z}, last ${n} hours`,
    histKeys: "Left and right arrow keys move between hours.",
    actual: "Measured room temperature", actualShort: "measured", demand: "Heating demand", runtime: "Heater on",
    histLoading: "Loading history…", histFail: "Couldn't load the history.", retryShort: "Retry",
    histEmpty: "No history yet – it builds up hour by hour.",
    flags: { manual: "Changed by hand", paused: "Paused after a manual change", shed: "Set back to stay under the capacity cap",
      off: "Heating off", disabled: "Automatic control off", safety: "Held above the safety temperature" },
    mk: { manual: "Manual change", shed: "Capacity cap", off: "Off" },
    atHour: (c) => c,
    sumAvg: (a, tg) => `Average ${a} (target ${tg})`, sumAvgPct: (a) => `Average demand ${a}`,
    sumBelow: (n) => (n === 1 ? "1 h below target" : `${n} h below target`),
    sumEnergy: (k, c) => `${k} kWh` + (c ? ` ≈ ${c}` : ""),
    sumTop: (k, cap) => `top house hour ${k} kWh` + (cap ? ` (cap ${cap} kW)` : ""), sumOver: " – over the cap",
    sumNone: "No measurements in this period yet.",
  },
  nb: {
    loading: "Henter varmeplan …",
    errTitle: "Kunne ikke hente varmeplanen",
    errTimeout: "Heat Planner svarte ikke. Sjekk at integrasjonen kjører.",
    retry: "Prøv igjen",
    emptyTitle: "Heat Planner er ikke satt opp ennå",
    emptyBody: "Legg til integrasjonen for å planlegge varmen etter strømprisen og hvem som er hjemme.",
    emptyAction: "Legg til Heat Planner",
    noZonesTitle: "Ingen rom ennå",
    noZonesBody: "Legg til et rom under Innstillinger → Enheter og tjenester → Heat Planner → Konfigurer.",
    noZonesAction: "Åpne Heat Planner",
    noMatch: "Ingen av rommene som er valgt for dette kortet finnes lenger. Rediger kortet for å velge rom.",
    stale: (t) => `Planen ble sist oppdatert ${t}. Heat Planner kan ha stoppet – sjekk integrasjonen.`,
    unavailable: (n) => `${n}: termostaten er utilgjengelig, så planen kan ikke settes nå.`,
    unavailableShort: "termostaten er utilgjengelig",
    actionFail: (m) => `Kunne ikke lagre endringen: ${m}`,
    dismiss: "Lukk",
    is: (n, t) => [`${n} er `, t, "."],
    noTemp: (n) => [`${n}.`],
    now: {
      comfort: (t) => ["Holder ", t, " nå"], normal: (t) => ["Holder ", t, " nå"], setback: (t) => ["Senket til ", t, " nå"],
      preheat: (t) => ["Forvarmer til ", t, " nå"], save: (t) => ["Sparer med ", t, " i en dyr time"],
      off: () => ["Av nå – strømmen er ekstremt dyr"], away: (t) => ["Ingen hjemme – ", t],
      manual: (h) => [`Endret for hånd – tilbake på planen kl. ${h}`], disabled: () => ["Automatisk styring er av"],
    },
    next: (t, h) => `, ${t} fra kl. ${h}.`,
    nextOff: (h) => `, av fra kl. ${h}.`,
    dot: ".",
    peak: (a, b, p) => ` Neste pristopp ${a}–${b} (opptil ${p}).`,
    cost: (c, s) => `Neste 24 timer koster omtrent ${c}` + (s ? `, ${s} mindre enn uten prisstyring.` : "."),
    target: "Mål", predicted: "Forventet romtemperatur", price: "Pris",
    modes: { comfort: "Komfort", normal: "Normal", setback: "Senket", preheat: "Forvarmer", save: "Sparer", off: "Av", away: "Borte" },
    levels: { cheap: "billig", normal: "normal", expensive: "dyr", extreme: "ekstrem" },
    reasons: {
      schedule: "Tidsplan", override: "Endret for denne dagen", away: "Ingen hjemme", expensive: "En av de dyreste timene",
      preheat: "Billig time før dyre timer", preheat_extreme: "Lagrer varme før ekstrempris",
      extreme_off: "Ekstrempris – varmen er av", protected: "Oppvarmingsvindu, prisen ignoreres",
      optimized: "Valgt av optimereren – lavest kostnad med samme komfort", peak_limit: "Holder månedens effekttopp nede (effekttrinn)",
      safety: "Holdt over sikkerhetstemperaturen", warmup: "Varmer opp for å nå tidsplanen i tide",
    },
    nowLabel: "nå", offShort: "av", edit: "Endre døgn", done: "Ferdig", today: "I dag", tomorrow: "I morgen",
    days: ["man", "tir", "ons", "tor", "fre", "lør", "søn"],
    daysLong: ["mandager", "tirsdager", "onsdager", "torsdager", "fredager", "lørdager", "søndager"],
    dragHint: "Dra over timene for å tegne en ny kurve – eller bruk piltastene.",
    applyTo: "Lagre for", onlyDay: (d) => `Bare ${d}`, every: (d) => `Alle ${d}`, weekdays: "Hverdager", weekend: "Helg", all: "Alle dager",
    discard: "Forkast", save: "Lagre", saved: "Lagret", resetDay: "Tilbake til fast plan", overrideNote: "Denne dagen har egen kurve.",
    settings: "Innstillinger", auto: "Automatisk styring", comfort: "Komforttemperatur",
    strategy: "Prisstrategi", strategies: ["Ignorer", "Forsiktig", "Balansert", "Aggressiv"],
    shift: "Senk i dyre timer", preheatS: "Hev før dyre timer", extremeOff: "Slå av ved ekstrempris",
    extremeAbs: "Ekstrempris fra", safety: "Aldri av under", away: "Senk når ingen er hjemme", warmFrom: "Varmt igjen fra kl.",
    protectFrom: "Ingen prissparing fra kl.", protectTo: "Ingen prissparing til kl.",
    model: (g, tau, l) => `Rommodell: varmes ${g}°/t, mister halve varmen på ${tau} t${l ? " (lært av historikken)" : " (fra beskrivelsen av huset)"}.`,
    power: (kw, src) => `Effekt ${kw} kW${src === "measured" ? " (målt)" : src === "estimate" ? " (anslått)" : ""}.`,
    resume: "Gjenoppta plan", manualBadge: "Manuell", help: "Slik fungerer det", optimizeLbl: "Optimer (alle rom)", closeHelp: "Lukk hjelpen",
    fbCold: "For kaldt", fbWarm: "For varmt", fbLabel: "Hvordan kjennes det?", fbFail: "Kunne ikke sende tilbakemeldingen.",
    cost24: (c, k) => `Neste 24 t ca. ${c} · ${k} kWh.`,
    saves: (s, p) => ` Sparer ${s}${p ? ` (${p})` : ""}`,
    kwhMore: (k) => `, bruker ${k} kWh mer.`, kwhLess: (k) => `, bruker ${k} kWh mindre.`,
    littleSaving: " Prisstyring sparer lite i dag.",
    deficit: (d) => ` Ca. ${d} gradtimer under målet.`,
    step: (l) => `Effekttrinn ${l}`, top3: (v) => `snitt topp 3: ${v} kW`, capToday: (v) => `tak i dag ${v} kW`,
    nextStep: (k, c) => `neste trinn ved ${k} kW` + (c ? ` (+${c}/mnd)` : ""), homeLoad: "Husets forbruk",
    quality: { good: "god", fair: "middels", uncertain: "usikker" },
    fit: (r, q) => `Treffer innenfor ±${r}°` + (q ? ` (${q})` : "") + ".", fitQ: (q) => `Modellens sikkerhet: ${q}.`,
    solar: "Tar hensyn til sol.", lag: (h) => `Gulvet reagerer etter ca. ${h} t.`,
    autoLbl: "Auto", autoAt: (h) => `Auto (${h})`,
    arrival: (t) => `Dere kommer vanligvis hjem ca. ${t}.`, arrivalLearning: "Lærer fortsatt når dere kommer hjem.",
    rooms: "Rom",
    chartLabel: (z, n) => `Varmeplan for ${z}, neste ${n} timer`,
    chartKeys: "Piltastene venstre og høyre flytter mellom timene. Enter endrer døgnet.",
    editKeys: "Venstre og høyre flytter mellom timene, opp og ned endrer målet. Escape avslutter.",
    dec: (l) => `Senk: ${l}`, inc: (l) => `Hev: ${l}`, onW: "på", offW: "av",
    editing: (d) => `Endrer ${d}.`,
    targetNow: "Mål nå", levelNow: "Varmenivå nå", valFrom: (v, h) => `${v} fra ${h}`, offFrom: (h) => `Av fra ${h}`,
    noChange: "Ingen endring fremover",
    st: {
      preheatPeak: (a) => `Forvarmer før pristoppen ${a}`, preheat: "Forvarmer i en billig time",
      save: "Sparer i en dyr time", setback: "Senket etter timeplanen", away: "Ingen hjemme – senket",
      peak: "Holder effekttoppen nede", comfort: "Komfort etter timeplanen", normal: "Følger timeplanen",
      warmup: "Varmer opp til timeplanen", safety: "Holdt over sikkerhetstemperaturen", off: "Av – ekstrem strømpris",
      manual: (h) => `Endret for hånd – tilbake på planen kl. ${h}`, disabled: "Automatisk styring er av",
    },
    peakSoon: (a, p) => `Pristopp ${a}, opptil ${p}`,
    priceLbl: (u) => `Strømpris, ${u}`, houseLbl: "Hele huset, kW", capLbl: (v) => `tak ${v} kW`,
    flatFixed: (p) => `Fast pris ${p} – ingen prisstyring nødvendig`,
    flatEven: (p) => `Jevn pris ${p} – lite å hente på prisstyring`,
    expected: "Forventet", measured: "Målt", house: "Huset", planned: "planlagt", keyTarget: "Mål", keyPlan: "Plan", edCurve: "Din kurve",
    tooCold: "for kaldt", tooWarm: "for varmt",
    tNext: "Neste 24 t", tSave: "Prisstyring sparer", tSaveSub: (p, k) => [p, k].filter(Boolean).join(" · "),
    tLittle: "nesten ingenting i dag", kwhMoreS: (k) => `bruker ${k} kWh mer`, kwhLessS: (k) => `bruker ${k} kWh mindre`,
    tStep: "Effekttrinn", tTop3: (v) => `snitt topp 3: ${v} kW`, tCap: "Tak i dag", tNextStep: (k) => `neste trinn ved ${k} kW`,
    deficitS: (d) => `Ca. ${d} gradtimer under målet neste døgn.`,
    pAvg: "Snitt", pAvgSub: (tg) => `mål ${tg}`, pBelow: "Timer under mål", pBelowSub: (d) => `${d}° eller mer under`,
    pAvgPct: "Snitt varmenivå", pEnergy: "Energi", pTop: "Høyeste time i huset", pCap: (c) => `tak ${c} kW`, pOver: (c) => `over taket på ${c} kW`,
    hoursN: (n) => (n === 1 ? "1 time" : `${n} timer`),
    edEntry: "Heat Planner-oppsett", edZones: "Rom", edZonesHelp: "La alle stå uten hake for å vise alle rom.",
    edHours: "Timer som vises", edHelp: "Vis «Slik fungerer det»", edLoading: "Henter rom …", edNoData: "Heat Planner er ikke satt opp ennå – kortet viser alle rom når det er gjort.",
    hUnit: "t",
    rangeLbl: "Tidsrom", rPlan: "Plan", r24: "Siste 24 t", r48: "Siste 48 t",
    histLabel: (z, n) => `Varmehistorikk for ${z}, siste ${n} timer`,
    histKeys: "Piltastene venstre og høyre flytter mellom timene.",
    actual: "Målt romtemperatur", actualShort: "faktisk", demand: "Varmebehov", runtime: "Varme på",
    histLoading: "Henter historikk …", histFail: "Kunne ikke hente historikken.", retryShort: "Prøv igjen",
    histEmpty: "Ingen historikk ennå – den bygges opp time for time.",
    flags: { manual: "Endret for hånd", paused: "Pause etter manuell endring", shed: "Satt ned for å holde effekttaket",
      off: "Varmen av", disabled: "Automatisk styring av", safety: "Holdt over sikkerhetstemperaturen" },
    mk: { manual: "Manuell endring", shed: "Effekttak", off: "Av" },
    atHour: (c) => `Kl. ${c}`,
    sumAvg: (a, tg) => `Snitt ${a} (mål ${tg})`, sumAvgPct: (a) => `Snitt behov ${a}`,
    sumBelow: (n) => `${n} t under målet`,
    sumEnergy: (k, c) => `${k} kWh` + (c ? ` ≈ ${c}` : ""),
    sumTop: (k, cap) => `høyeste time i huset ${k} kWh` + (cap ? ` (tak ${cap} kW)` : ""), sumOver: " – over taket",
    sumNone: "Ingen målinger i denne perioden ennå.",
  },
};

const langOf = (hass) => {
  const l = (hass?.locale?.language || hass?.language || "en").toLowerCase();
  return ["nb", "no", "nn"].some((p) => l.startsWith(p)) ? "nb" : "en";
};

const MODE_COLOR = {
  comfort: "var(--hp-comfort)", normal: "var(--hp-normal)", setback: "var(--hp-setback)", preheat: "var(--hp-preheat)",
  save: "var(--hp-save)", off: "var(--hp-off)", away: "var(--hp-setback)",
};
const LEVEL_COLOR = { cheap: "var(--hp-cheap)", normal: "var(--hp-pnormal)", expensive: "var(--hp-exp)", extreme: "var(--hp-ext)" };
const STRAT = [0, 0.5, 1, 2];
let INSTANCE = 0;

const css = `
:host { display:block;
  --hp-comfort:#E9A23B; --hp-normal:color-mix(in srgb, var(--primary-text-color) 42%, transparent); --hp-setback:#5B6CD9; --hp-preheat:#F0644B; --hp-save:#1FA896; --hp-off:#8C96A8;
  --hp-cheap:#3FA971; --hp-pnormal:color-mix(in srgb, var(--secondary-text-color) 32%, transparent); --hp-exp:color-mix(in srgb, var(--primary-text-color) 68%, transparent); --hp-ext:#E0464E;
  --hp-ink: var(--primary-text-color); --hp-soft: var(--secondary-text-color); --hp-line: var(--divider-color, rgba(127,127,127,.25));
  --hp-bg: var(--ha-card-background, var(--card-background-color, #fff));
  --hp-track: color-mix(in srgb, var(--secondary-text-color) 55%, transparent);
  --hp-warn: var(--warning-color, #E8A33B); --hp-err: var(--error-color, #DB4437);
  --hp-grid: color-mix(in srgb, var(--hp-ink) 9%, transparent);
  --hp-tint: color-mix(in srgb, var(--hp-ink) 5%, transparent);
}
ha-card { padding:18px 20px 14px; overflow:hidden; container-type:inline-size; }
.sr-only { position:absolute !important; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
.zones { display:flex; gap:4px; margin:0 0 14px; overflow-x:auto; scrollbar-width:none; scroll-snap-type:x proximity; }
.zones::-webkit-scrollbar { display:none; }
.zones button { all:unset; cursor:pointer; color:var(--hp-soft); font-size:14px; padding:6px 12px; border-radius:999px; white-space:nowrap; scroll-snap-align:start; flex:none; box-sizing:border-box; display:inline-flex; align-items:center; }
.zones button:hover { color:var(--hp-ink); }
.zones button[aria-selected=true] { color:var(--hp-ink); background:color-mix(in srgb, var(--hp-ink) 8%, transparent); font-weight:500; }
.zones button .warn { display:inline-block; margin-left:6px; width:16px; height:16px; line-height:16px; text-align:center; border-radius:50%; font-size:11px; font-weight:700; background:var(--hp-warn); color:#000; }
:focus-visible { outline:2px solid var(--primary-color, #03a9f4); outline-offset:2px; }
button:focus-visible, a:focus-visible, summary:focus-visible { border-radius:6px; }
.zones button:focus-visible { border-radius:999px; }
.chartwrap:focus-visible { outline-offset:4px; border-radius:6px; }

/* header: hero number, target and next change, one status chip */
.zname { font-size:14px; color:var(--hp-soft); margin:0 0 2px; }
.hero { display:flex; align-items:flex-start; justify-content:space-between; gap:8px 16px; flex-wrap:wrap; }
.heromain { display:flex; align-items:center; gap:4px 18px; flex-wrap:wrap; min-width:0; }
.big { font-size:clamp(42px, 12cqi, 54px); line-height:1; font-weight:300; letter-spacing:-0.035em; font-variant-numeric:tabular-nums; color:var(--hp-ink); margin:2px 0; white-space:nowrap; }
.side { display:grid; gap:3px; font-size:14px; line-height:1.3; }
.side .tgt { color:var(--hp-ink); }
.side .tgt b { font-weight:600; font-variant-numeric:tabular-nums; }
.side .nxt { color:var(--hp-soft); font-variant-numeric:tabular-nums; }
.statusrow { display:flex; align-items:center; flex-wrap:wrap; gap:6px 12px; margin:10px 0 2px; }
.status { --c: var(--hp-normal); display:inline-flex; align-items:center; gap:7px; font-size:13px; font-weight:500; line-height:1.3; padding:4px 11px 4px 9px; border-radius:999px;
  color:color-mix(in srgb, var(--c) 55%, var(--hp-ink)); background:color-mix(in srgb, var(--c) 13%, transparent); }
.status i { width:7px; height:7px; border-radius:50%; background:var(--c); flex:none; }
.peaknote { font-size:12.5px; color:var(--hp-soft); font-variant-numeric:tabular-nums; }
.fb { display:flex; gap:2px; align-items:center; flex-wrap:wrap; }
.fb .chip { font-size:12.5px; padding:5px 10px; border-color:transparent; color:var(--hp-soft); }
.fb .chip:hover:not([disabled]) { color:var(--hp-ink); background:color-mix(in srgb, var(--hp-ink) 6%, transparent); }
.fb .chip[disabled] { opacity:.5; cursor:default; }
.fb .chip i { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; }

/* chart */
.chartwrap { position:relative; user-select:none; -webkit-user-select:none; touch-action:pan-y; margin-top:4px; }
.chartwrap.editing { touch-action:none; }
svg { display:block; width:100%; overflow:visible; }
.axis text { fill:var(--hp-soft); font-size:10.5px; font-variant-numeric:tabular-nums; }
.axis text.day { fill:var(--hp-ink); font-weight:600; }
.grid line { stroke:var(--hp-grid); stroke-width:1; }
.dayline { stroke:var(--hp-line); stroke-width:1; }
.plbl { fill:var(--hp-soft); font-size:10.5px; }
.plbl .flatv { fill:var(--hp-ink); font-weight:600; font-variant-numeric:tabular-nums; }
.axis text, .plbl, .vlbl { paint-order:stroke; stroke:var(--hp-bg); stroke-width:3px; stroke-linejoin:round; }
.vlbl { fill:var(--hp-soft); font-size:10px; font-variant-numeric:tabular-nums; }
.now line { stroke:var(--hp-ink); stroke-width:1; opacity:.45; }
.now rect { fill:var(--hp-bg); stroke:color-mix(in srgb, var(--hp-ink) 35%, transparent); stroke-width:1; }
.now text { fill:var(--hp-ink); font-size:10.5px; font-weight:600; }
.exp { fill:none; stroke:var(--hp-ink); stroke-width:1.75; stroke-linecap:round; stroke-linejoin:round; opacity:.8; }
.pred { fill:none; stroke:var(--hp-ink); stroke-width:1.25; stroke-dasharray:3 4; opacity:.45; }
.tgtl { fill:none; stroke-width:2.5; stroke-linejoin:round; stroke-linecap:round; }
.tgtl.ghost { stroke-width:1.75; opacity:.55; }
.offl { stroke:var(--hp-off); stroke-width:2.5; stroke-dasharray:1 4; stroke-linecap:round; }
.sched { fill:none; stroke:var(--hp-ink); stroke-width:2; stroke-linejoin:round; opacity:.85; }
.handle { fill:var(--hp-bg); stroke:var(--hp-ink); stroke-width:1.5; }
.handle.dirty { fill:var(--hp-ink); }
.handle.focus { stroke-width:2.5; stroke:var(--primary-color, #03a9f4); }
.dl { font-size:11px; font-weight:600; font-variant-numeric:tabular-nums; paint-order:stroke; stroke:var(--hp-bg); stroke-width:3px; stroke-linejoin:round; }
.dot { stroke:var(--hp-bg); stroke-width:1.5; }
.act { fill:none; stroke:var(--hp-ink); stroke-width:2.25; stroke-linejoin:round; stroke-linecap:round; }
.actdot { fill:var(--hp-ink); }
.cold { fill:color-mix(in srgb, var(--hp-setback) 22%, transparent); }
.warm { fill:color-mix(in srgb, var(--hp-preheat) 20%, transparent); }
.pbar { shape-rendering:geometricPrecision; }
.kw { fill:color-mix(in srgb, var(--hp-ink) 38%, transparent); }
.kw.plan { fill:color-mix(in srgb, var(--hp-ink) 10%, transparent); stroke:color-mix(in srgb, var(--hp-ink) 32%, transparent); stroke-width:1; }
.kw.over { fill:var(--hp-ext); }
.kw.plan.over { fill:color-mix(in srgb, var(--hp-ext) 25%, transparent); stroke:var(--hp-ext); }
.capl { fill:none; stroke:var(--hp-ext); stroke-width:1.25; }
.capt { fill:color-mix(in srgb, var(--hp-ext) 60%, var(--hp-ink)); font-size:10px; font-weight:600; font-variant-numeric:tabular-nums; }
.mkr .ring { fill:var(--hp-bg); stroke:var(--hp-ink); stroke-width:1.5; }
.mkr .tri { fill:var(--hp-ext); }
.mkr .sq { fill:var(--hp-off); }
.xh line { stroke:var(--hp-ink); stroke-width:1; opacity:.4; }
.xh rect { fill:var(--hp-ink); opacity:.05; }
.xh circle { stroke:var(--hp-bg); stroke-width:2; }
.tip { position:absolute; pointer-events:none; background:var(--hp-bg); color:var(--hp-ink); border:1px solid var(--hp-line);
  border-radius:12px; padding:9px 11px; font-size:12.5px; line-height:1.5; width:max-content; min-width:150px; max-width:min(230px, 70%); box-sizing:border-box; box-shadow:0 8px 28px rgba(0,0,0,.16); z-index:2; }
.tip .when { font-weight:600; margin-bottom:2px; }
.tip .tr { display:flex; justify-content:space-between; gap:14px; }
.tip .tk { color:var(--hp-soft); }
.tip b { font-weight:600; font-variant-numeric:tabular-nums; text-align:right; }
.tip .why { color:var(--hp-soft); margin-top:4px; padding-top:4px; border-top:1px solid var(--hp-grid); }
.tip .why i { display:inline-block; width:10px; height:3px; border-radius:2px; margin-right:6px; vertical-align:3px; }
.key { display:flex; flex-wrap:wrap; align-items:center; gap:4px 12px; font-size:11.5px; color:var(--hp-soft); margin:8px 0 0; }
.key span { display:inline-flex; align-items:center; white-space:nowrap; }
.key i { display:inline-block; width:12px; height:3px; border-radius:2px; margin-right:5px; }
.key i.exp { height:0; border-top:1.75px solid var(--hp-ink); border-radius:0; opacity:.8; }
.key i.sw { height:9px; width:10px; border-radius:2px; }
.key svg { width:10px; height:10px; margin-right:5px; overflow:visible; }

/* tiles */
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(128px, 1fr)); gap:8px; margin:14px 0 0; }
.tile { padding:9px 12px 10px; border-radius:12px; background:color-mix(in srgb, var(--hp-ink) 4.5%, transparent); min-width:0; }
.tile .tl { font-size:12px; color:var(--hp-soft); line-height:1.3; }
.tile .tv { font-size:18px; font-weight:500; color:var(--hp-ink); font-variant-numeric:tabular-nums; line-height:1.35; margin-top:2px; letter-spacing:-0.01em; }
.tile .ts { font-size:12px; color:var(--hp-soft); line-height:1.35; margin-top:1px; font-variant-numeric:tabular-nums; }
.tile.bad .tv { color:color-mix(in srgb, var(--hp-ext) 70%, var(--hp-ink)); }
.summary .sumh { font-size:12px; font-weight:600; color:var(--hp-ink); margin:14px 0 -6px; }
.caption { color:var(--hp-soft); font-size:12px; margin:8px 0 0; line-height:1.45; }

.row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.bar { display:flex; justify-content:flex-end; align-items:center; gap:10px; flex-wrap:wrap; margin-top:12px; }
.cost { color:var(--hp-soft); font-size:13px; }
.cost.flash { color:var(--hp-ink); display:block; margin:12px 0 0; padding:8px 12px; border-radius:10px; background:color-mix(in srgb, var(--hp-ink) 6%, transparent); }
.btn { all:unset; box-sizing:border-box; cursor:pointer; font-size:13px; padding:7px 14px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-ink); display:inline-flex; align-items:center; justify-content:center; text-align:center; }
.btn:hover { background:color-mix(in srgb, var(--hp-ink) 5%, transparent); }
.btn.ghost { border-color:transparent; color:var(--hp-soft); }
.btn.ghost:hover { color:var(--hp-ink); }
.btn.primary { background:var(--hp-ink); color:var(--hp-bg); border-color:var(--hp-ink); }
.btn:disabled, .btn[aria-disabled=true] { opacity:.4; cursor:default; }
.helpwrap { margin-top:18px; padding-top:6px; border-top:1px solid var(--hp-line); }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { all:unset; box-sizing:border-box; cursor:pointer; font-size:12.5px; padding:5px 11px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); display:inline-flex; align-items:center; }
.chip[aria-pressed=true] { color:var(--hp-bg); background:var(--hp-ink); border-color:var(--hp-ink); }
.editor { margin-top:14px; padding-top:12px; border-top:1px solid var(--hp-line); }
.hint { color:var(--hp-soft); font-size:12.5px; margin:8px 0 10px; }
details.settings { margin-top:12px; border-top:1px solid var(--hp-line); padding-top:10px; }
summary { cursor:pointer; color:var(--hp-soft); font-size:13px; list-style:none; display:inline-flex; align-items:center; }
summary:hover { color:var(--hp-ink); }
summary::-webkit-details-marker { display:none; }
summary::before { content:"+"; display:inline-block; width:1.1em; } details[open] summary::before { content:"–"; }
.set { display:grid; gap:10px; margin-top:12px; font-size:13.5px; }
.srow { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:6px 16px; align-items:center; }
.srow .lbl { color:var(--hp-ink); }
.val { font-variant-numeric:tabular-nums; color:var(--hp-ink); min-width:56px; text-align:right; }
.step { display:flex; align-items:center; gap:6px; flex-wrap:wrap; }
.step .pm { all:unset; box-sizing:border-box; cursor:pointer; width:28px; height:28px; display:inline-flex; align-items:center; justify-content:center; border-radius:50%; border:1px solid var(--hp-line); color:var(--hp-ink); font-size:15px; }
.step .pm[aria-disabled=true] { opacity:.35; cursor:default; }
.seg { display:flex; border:1px solid var(--hp-line); border-radius:999px; overflow:hidden; flex-wrap:wrap; }
.seg button { all:unset; box-sizing:border-box; cursor:pointer; font-size:12.5px; padding:5px 10px; color:var(--hp-soft); display:inline-flex; align-items:center; justify-content:center; }
.seg button[aria-pressed=true] { background:var(--hp-ink); color:var(--hp-bg); }
.tog { all:unset; cursor:pointer; position:relative; width:42px; height:26px; flex:none; box-sizing:border-box; }
.tog::before { content:""; position:absolute; left:50%; top:50%; width:38px; height:22px; margin:-11px 0 0 -19px; border-radius:999px; background:var(--hp-track); transition:background .15s; }
.tog::after { content:""; position:absolute; left:50%; top:50%; width:16px; height:16px; margin:-8px 0 0 -16px; border-radius:50%; background:var(--hp-bg); transition:transform .15s; box-shadow:0 1px 2px rgba(0,0,0,.3); }
.tog[aria-checked=true]::before { background:var(--hp-ink); } .tog[aria-checked=true]::after { transform:translateX(16px); }
.model { color:var(--hp-soft); font-size:12px; margin-top:4px; line-height:1.45; }
.rangebar { display:flex; justify-content:flex-end; margin:12px 0 0; }
.seg.range { flex-wrap:nowrap; border:0; background:color-mix(in srgb, var(--hp-ink) 6%, transparent); padding:2px; }
.seg.range button { padding:4px 12px; white-space:nowrap; border-radius:999px; font-size:12.5px; }
.seg.range button[aria-pressed=true] { background:var(--hp-bg); color:var(--hp-ink); font-weight:500; box-shadow:0 1px 3px rgba(0,0,0,.14); }
.sk.hist { height:200px; margin:6px 0 12px; }
.step .chip { font-size:12px; padding:3px 9px; margin-right:4px; }
.notice { display:flex; gap:10px; align-items:flex-start; justify-content:space-between; font-size:13px; line-height:1.45; padding:8px 10px 8px 12px; border-radius:10px; margin:0 0 10px;
  color:var(--hp-ink); background:color-mix(in srgb, var(--hp-warn) 12%, transparent); border-left:3px solid var(--hp-warn); }
.notice.err { background:color-mix(in srgb, var(--hp-err) 10%, transparent); border-left-color:var(--hp-err); }
.notice .btn { padding:2px 10px; font-size:12px; flex:none; }
.state { padding:6px 2px 4px; }
.state h2 { font-size:17px; font-weight:600; margin:0 0 6px; color:var(--hp-ink); }
.state p { color:var(--hp-soft); font-size:14px; line-height:1.45; margin:0 0 14px; max-width:60ch; }
.sk { background:color-mix(in srgb, var(--hp-ink) 8%, transparent); border-radius:8px; }
.sk.line { height:14px; margin:0 0 10px; } .sk.story { height:46px; width:40% !important; margin:0 0 12px; } .sk.chart { height:200px; margin:16px 0 12px; }
@media (prefers-reduced-motion: no-preference) {
  .sk { animation:hp-pulse 1.6s ease-in-out infinite; }
  .xh { transition:opacity .12s; }
  .zones button, .btn, .chip, .seg button { transition:background-color .15s, color .15s; }
}
@keyframes hp-pulse { 50% { opacity:.45; } }
@media (prefers-reduced-motion: reduce) { .tog::before, .tog::after { transition:none; } }
@media (pointer: coarse) {
  .btn, .chip, .seg button, .zones button { min-height:44px; }
  .fb .chip, .step .chip { min-height:44px; }
  .step .pm { width:44px; height:44px; }
  .tog { width:52px; height:44px; }
  summary { min-height:44px; }
}
@container (max-width: 480px) {
  ha-card { padding:14px 12px 12px; }
  .btn, .chip, .seg button, .zones button, .fb .chip, .step .chip { min-height:44px; }
  .zones button { min-width:44px; justify-content:center; }
  .fb { margin:0 -6px; }
  .step .pm { width:44px; height:44px; }
  .tog { width:52px; height:44px; }
  summary { min-height:44px; }
  .srow:not(.inline) { grid-template-columns:minmax(0,1fr); }
  .srow .val { text-align:left; min-width:64px; }
  .seg { display:grid; grid-template-columns:1fr 1fr; border-radius:14px; }
  .seg.range { display:flex; width:100%; border-radius:999px; }
  .seg.range button { flex:1 1 0; padding:4px 6px; }
  .rangebar { justify-content:stretch; }
  .zones { margin:0 -12px 10px; padding:0 12px; }
  .tiles { grid-template-columns:1fr 1fr; }
  .tile .tv { font-size:17px; }
  .tile:last-child:nth-child(odd) { grid-column:1 / -1; }
  .tip { max-width:min(190px, 62%); min-width:0; font-size:12px; }
}
`;

const SVG_TAGS = new Set(["svg", "g", "path", "line", "rect", "text", "circle", "pattern", "defs", "clipPath", "title", "linearGradient", "stop", "tspan"]);
function el(tag, attrs = {}, ...kids) {
  const e = SVG_TAGS.has(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(3)) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}

/** Smooth path through points without overshoot (monotone cubic, Fritsch–Carlson). */
function smoothPath(pts) {
  const n = pts.length;
  if (!n) return "";
  const f = (v) => v.toFixed(1);
  if (n < 3) return pts.map((p, i) => `${i ? "L" : "M"}${f(p[0])},${f(p[1])}`).join("");
  const dx = [], m = [];
  for (let i = 0; i < n - 1; i++) { dx[i] = pts[i + 1][0] - pts[i][0] || 1e-6; m[i] = (pts[i + 1][1] - pts[i][1]) / dx[i]; }
  const tg = [m[0]];
  for (let i = 1; i < n - 1; i++) {
    tg[i] = m[i - 1] * m[i] <= 0 ? 0 : (3 * (dx[i - 1] + dx[i])) / ((2 * dx[i] + dx[i - 1]) / m[i - 1] + (dx[i] + 2 * dx[i - 1]) / m[i]);
  }
  tg[n - 1] = m[n - 2];
  let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], h = dx[i] / 3;
    d += `C${f(x0 + h)},${f(y0 + tg[i] * h)} ${f(x1 - h)},${f(y1 - tg[i + 1] * h)} ${f(x1)},${f(y1)}`;
  }
  return d;
}
/** Split [[x,y]|null] into runs of consecutive points. */
const runsOf = (pts) => pts.reduce((a, p) => { if (!p) a.push([]); else a[a.length - 1].push(p); return a; }, [[]]).filter((r) => r.length);
/** Smallest "nice" number ≥ v (price scale tops). */
const niceCeil = (v) => {
  const e = 10 ** Math.floor(Math.log10(Math.max(v, 1e-6)));
  return [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].map((k) => k * e).find((x) => x >= v - 1e-9) || 10 * e;
};
const COLD = 0.3; // measured this far under target counts as "too cold" (look-back)
const WARM = 1; // …and this far over as "too warm"
/** A reason colour made dark (light theme) or light (dark theme) enough for text. */
const inkOf = (c) => `color-mix(in srgb, ${c} 55%, var(--hp-ink))`;

/* Locale helpers shared by the card and its editor: number format and 12/24 h from hass.locale. */
function numberLocale(hass) {
  const loc = hass?.locale, lang = loc?.language || hass?.language || "en";
  switch (loc?.number_format) {
    case "comma_decimal": return ["en-US", "en"];
    case "decimal_comma": return ["de", "es", "it"];
    case "space_comma": return ["fr", "sv", "cs"];
    case "system": return undefined;
    default: return lang;
  }
}
function uses12h(hass) {
  const tf = hass?.locale?.time_format;
  if (tf === "12") return true;
  if (tf === "24") return false;
  const lang = tf === "system" ? undefined : (hass?.locale?.language || hass?.language || "en");
  try {
    const hc = new Intl.DateTimeFormat(lang, { hour: "numeric" }).resolvedOptions().hourCycle;
    return hc === "h11" || hc === "h12";
  } catch { return false; }
}

class HeatPlannerCard extends HTMLElement {
  static getStubConfig() { return { hours: DEFAULT_HOURS }; }
  static getConfigElement() { return document.createElement("heat-planner-card-editor"); }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._uid = `hp${++INSTANCE}`;
    this._data = null;
    this._zone = null;
    this._edit = null; // {date, values:[24], dirty:Set, preview:plan|null, scope}
    this._tip = null;
    this._width = 600;
    this._focusIdx = null;
    this._config = { hours: DEFAULT_HOURS };
    this._range = "plan"; // "plan" | "24" | "48" – the look-back view
    this._hist = null; // {key, data, at, loading, error}
    this._onVisible = () => { if (document.visibilityState === "visible" && this._pending) this._update(true); };
    // a tap outside the chart hides the hour shown by an earlier tap
    this._onDocDown = (ev) => { if (this._tip && this._wrap && !ev.composedPath().includes(this._wrap)) this._hideHL(); };
  }

  setConfig(config) {
    if (config && typeof config !== "object") throw new Error("Invalid configuration");
    const hours = Math.round(Number(config?.hours ?? DEFAULT_HOURS));
    this._config = { ...(config || {}), hours: Number.isFinite(hours) ? Math.max(12, Math.min(48, hours)) : DEFAULT_HOURS };
    this._update(true);
  }

  getCardSize() {
    if (!this._data) return 4;
    const W = this._chartW();
    const chart = Math.round(Math.min(220, Math.max(140, W * 0.3))) + 30 + 28 + 84 + (this.entry?.peak?.metered ? 78 : 0);
    const px = (this.zones.length > 1 ? 44 : 0) + 64 + 34 + 40 + chart + 30 + 80 + 50 + 40 + (this._open ? 360 : 0) + (this._edit ? 120 : 0);
    return Math.ceil(px / 50);
  }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._subscribe(); return; }
    // hass changes on every state change in Home Assistant: only re-render when something we show changed.
    const sig = this._hassSig();
    if (sig !== this._lastHassSig) { this._lastHassSig = sig; this._update(); }
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    this._ro = new ResizeObserver((e) => {
      this._pendingW = Math.round(e[0].contentRect.width);
      if (this._roRaf) return;
      this._roRaf = requestAnimationFrame(() => {
        this._roRaf = null;
        const w = this._pendingW;
        if (w > 0 && Math.abs(w - this._width) > 4) { this._width = w; this._update(true); }
      });
    });
    this._ro.observe(this);
    document.addEventListener("visibilitychange", this._onVisible);
    document.addEventListener("pointerdown", this._onDocDown, true);
    clearInterval(this._tick);
    // moves the "now" marker and notices stale data; renders only if something visible changed
    this._tick = setInterval(() => this._update(), 60e3);
  }

  disconnectedCallback() {
    this._ro?.disconnect(); this._ro = null;
    cancelAnimationFrame(this._roRaf); this._roRaf = null;
    document.removeEventListener("visibilitychange", this._onVisible);
    document.removeEventListener("pointerdown", this._onDocDown, true);
    clearInterval(this._tick);
    clearTimeout(this._loadT); clearTimeout(this._pt); clearTimeout(this._flashT);
    this._unsubscribe();
  }

  get lang() { return langOf(this._hass); }
  get t() { return I18N[this.lang]; }
  get locale() { return this._hass?.locale?.language || this._hass?.language || "en"; }

  _subscribe() {
    if (!this._hass?.connection || this._unsub) return;
    clearTimeout(this._loadT);
    this._loadT = setTimeout(() => {
      if (!this._data) { this._error = { kind: "timeout" }; this._update(true); }
    }, LOAD_TIMEOUT_MS);
    const p = this._hass.connection.subscribeMessage((msg) => {
      clearTimeout(this._loadT);
      if (this._error) this._error = null;
      this._data = msg;
      this._update();
    }, { type: "heat_planner/subscribe" });
    this._unsub = p;
    p.catch((err) => {
      if (this._unsub !== p) return;
      this._unsub = null;
      clearTimeout(this._loadT);
      this._error = { kind: err?.code === "unknown_command" ? "missing" : "error", message: err?.message || String(err) };
      this._update(true);
    });
  }
  _unsubscribe() {
    const p = this._unsub;
    this._unsub = null;
    if (p) p.then((u) => u?.()).catch(() => {});
  }
  _retry() {
    this._unsubscribe();
    this._error = null;
    this._data = null;
    this._update(true);
    this._subscribe();
  }

  // ------------------------------------------------------------- data helpers
  get entry() {
    const entries = this._data?.entries || [];
    const id = this._config?.entry_id;
    return (id && entries.find((e) => e.entry_id === id)) || entries[0] || null;
  }
  get zones() {
    const zs = this.entry?.zones || [];
    const f = this._config?.zones;
    return Array.isArray(f) && f.length ? zs.filter((z) => f.includes(z.zone_id)) : zs;
  }
  get zone() {
    const zs = this.zones;
    return zs.find((z) => z.zone_id === this._zone) || zs[0] || null;
  }
  nf(opts) {
    const extra = this._hass?.locale?.number_format === "none" ? { useGrouping: false } : {};
    try { return new Intl.NumberFormat(numberLocale(this._hass), { ...opts, ...extra }); }
    catch { return new Intl.NumberFormat("en", opts); }
  }
  fmtNum(v, d = 1) { return this.nf({ minimumFractionDigits: d, maximumFractionDigits: d }).format(v); }
  fmtT(v, z = this.zone) {
    if (v === null || v === undefined) return "–";
    if (z?.mode === "percent") return `${this.nf({ maximumFractionDigits: 0 }).format(v)} %`;
    return `${this.nf({ maximumFractionDigits: 1 }).format(v)}°`;
  }
  fmtMoney(v) {
    const cur = this.entry?.currency || "NOK";
    try { return this.nf({ style: "currency", currency: cur, maximumFractionDigits: 2 }).format(v); }
    catch { return `${this.fmtNum(v, 2)} ${cur}`; }
  }
  fmtPrice(v) {
    if (v === null || v === undefined) return "–";
    return `${this.fmtNum(v, 2)} ${this.curUnit}/kWh`.trim();
  }
  get h12() { return uses12h(this._hass); }
  _clockFmt(withMinutes) {
    const key = `${this.locale}|${this.h12}|${withMinutes}`;
    if (this._cf?.key !== key) {
      const lang = this._hass?.locale?.time_format === "system" ? undefined : this.locale;
      let f;
      try { f = new Intl.DateTimeFormat(lang, { hour: "numeric", ...(withMinutes ? { minute: "2-digit" } : {}), hour12: this.h12, timeZone: "UTC" }); }
      catch { f = new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit", hour12: false, timeZone: "UTC" }); }
      this._cf = { key, f };
    }
    return this._cf.f;
  }
  /** "13:00" or "1:00 PM" for an hour of the day. */
  fmtClock(h) {
    if (!this.h12) return `${String(h).padStart(2, "0")}:00`;
    return this._clockFmt(true).format(Date.UTC(2000, 0, 1, h));
  }
  /** Axis label: "13" or "1 PM". */
  fmtAxis(h) {
    if (!this.h12) return String(h).padStart(2, "0");
    return this._clockFmt(false).format(Date.UTC(2000, 0, 1, h));
  }
  /** A point in time (HA time zone) as a clock with minutes. */
  fmtTime(date) {
    try {
      const lang = this._hass?.locale?.time_format === "system" ? undefined : this.locale;
      return new Intl.DateTimeFormat(lang, { hour: "numeric", minute: "2-digit", hour12: this.h12, timeZone: this.tz,
        ...(Date.now() - date.getTime() > 20 * 3600e3 ? { weekday: "short" } : {}) }).format(date);
    } catch { return date.toISOString().slice(11, 16); }
  }
  get tz() { return this._hass?.config?.time_zone || undefined; }
  // Server timestamps are ISO strings in HA's own time zone: read date/hour from the text.
  hh(d) {
    if (typeof d === "string" && d.length >= 13 && d[10] === "T") return d.slice(11, 13);
    const h = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: this.tz }).format(new Date(d));
    return h.padStart(2, "0");
  }
  dateKey(d) {
    if (typeof d === "string" && /^\d{4}-\d{2}-\d{2}/.test(d)) return d.slice(0, 10);
    return new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: this.tz }).format(new Date(d));
  }
  nowKey() { const n = new Date(); return { date: this.dateKey(n), hour: +this.hh(n), frac: n.getMinutes() / 60 }; }
  weekdayShort(dateKey) {
    const [y, m, d] = dateKey.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d, 12)).toLocaleDateString(this.locale, { weekday: "short", timeZone: "UTC" });
  }
  weekday(dateKey) { const [y, m, d] = dateKey.split("-").map(Number); return (new Date(y, m - 1, d).getDay() + 6) % 7; }
  dayValues(z, dateKey) {
    if (z.overrides?.[dateKey]) return [...z.overrides[dateKey]];
    return [...(z.schedule?.[String(this.weekday(dateKey))] || Array(24).fill(0))];
  }
  abs(z, v) { return z.mode === "percent" ? v : z.settings.comfort + v; }
  rel(z, v) { return z.mode === "percent" ? v : v - z.settings.comfort; }

  // ------------------------------------------------------------- state checks
  _isStale() {
    const u = Date.parse(this.entry?.updated || "");
    return Number.isFinite(u) && Date.now() - u > STALE_MS;
  }
  _unavailable(z) {
    if (typeof z.available === "boolean") return !z.available;
    const states = this._hass?.states;
    if (!states) return false;
    const id = z.climate_entity || z.number_entity;
    if (!id) return false;
    const st = states[id];
    return !st || st.state === "unavailable";
  }
  _hassSig() {
    const l = this._hass?.locale || {};
    const un = (this.entry?.zones || []).filter((z) => this._unavailable(z)).map((z) => z.zone_id).join(",");
    return [this.lang, l.number_format, l.time_format, this._hass?.config?.time_zone, un].join("|");
  }
  _dataSig() {
    // What is visible: everything about the zones on this card, the entry fields shown, the
    // stale flag and the "now" marker (5-minute steps). The export's own timestamp is not.
    const e = this.entry;
    if (!e) return JSON.stringify([this._data ? "empty" : "loading", this._error]);
    const { updated, prices, ...rest } = e;
    return JSON.stringify([rest.zones && this.zones, { ...rest, zones: undefined }, this._edit ? prices : null,
      this._isStale(), Math.floor(Date.now() / 300e3), this._error, this._hassSig(), this._range,
      this._hist ? [this._hist.key, this._hist.at, !!this._hist.loading, !!this._hist.error] : null]);
  }

  /** Render if anything visible changed (force: always). Deferred while the browser tab is hidden. */
  _update(force = false) {
    if (!this.shadowRoot) return;
    if (document.visibilityState === "hidden" && this._shell) { this._pending = true; return; }
    if (!force && this._edit?.dirty?.size) return; // don't disturb an unsaved edit
    if (!force && this._drag) return;
    const sig = this._dataSig();
    if (!force && sig === this._lastSig && this._shell) return;
    this._lastSig = sig;
    this._lastHassSig = this._hassSig();
    this._pending = false;
    this._render();
  }

  // ------------------------------------------------------------- announcements
  _announce(text) {
    const live = this._live;
    if (!live || !text) return;
    live.textContent = "";
    clearTimeout(this._liveT);
    this._liveT = setTimeout(() => { live.textContent = text; }, 60);
  }

  // ------------------------------------------------------------- look-back view
  get hasHistory() { const f = this.entry?.features; return Array.isArray(f) && f.includes("history_log"); }
  get past() { return this._range !== "plan" && this.hasHistory && !this._edit; }
  get pastHours() { return this._range === "48" ? 48 : 24; }
  _histKey() { return `${this.entry?.entry_id}|${this.pastHours}`; }
  _histData() { return this._hist?.key === this._histKey() ? this._hist.data : null; }
  _histZone(z) { return this._histData()?.zones?.find((x) => x.zone_id === z.zone_id) || null; }
  _setRange(r) {
    if (r === this._range) return;
    this._range = r; this._focusIdx = null; this._kbActive = false;
    this._hideTip();
    this._update(true);
  }
  /** Fetch heat_planner/history for the chosen range (cached for 5 minutes; force: now). */
  _loadHistory(force = false) {
    if (!this.past || !this._hass?.callWS || !this.entry) return;
    const key = this._histKey(), h = this._hist;
    // after a failure, wait a minute before trying again on its own (the Retry button forces it)
    if (h?.key === key && (h.loading || (!force && Date.now() - h.at < (h.error ? 60e3 : 300e3)))) return;
    const keep = h?.key === key ? h.data : null;
    this._hist = { key, data: keep, at: h?.key === key ? h.at : 0, loading: true };
    this._hass.callWS({ type: "heat_planner/history", entry_id: this.entry.entry_id, hours: this.pastHours })
      .then((data) => { if (this._hist?.key === key) { this._hist = { key, data, at: Date.now() }; this._update(true); } })
      .catch((err) => {
        if (this._hist?.key !== key) return;
        this._hist = { key, data: keep, at: Date.now(), error: err?.message || String(err) };
        this._update(true);
      });
  }
  _rangeBar() {
    const t = this.t;
    return el("div", { class: "rangebar" }, el("div", { class: "seg range", role: "group", "aria-label": t.rangeLbl },
      [["plan", t.rPlan], ["24", t.r24], ["48", t.r48]].map(([k, label]) => el("button", {
        "aria-pressed": String(this._range === k), "data-fk": `range:${k}`, text: label, onclick: () => this._setRange(k),
      }))));
  }
  _histPlaceholder() {
    const t = this.t, h = this._hist;
    if (h?.error && !h.loading) {
      return el("div", { class: "notice err" }, el("span", { text: t.histFail }),
        el("button", { class: "btn", text: t.retryShort, "data-fk": "histretry", onclick: () => this._loadHistory(true) }));
    }
    return el("div", { class: "sk hist", role: "status", "aria-busy": "true", "aria-label": t.histLoading },
      el("span", { class: "sr-only", text: t.histLoading }));
  }
  /** "Kl. 07" / "07:00" (with the weekday when it isn't today). */
  _pastWhen(s) {
    const today = this.dateKey(new Date());
    const day = s.key !== today ? this.weekdayShort(s.key) : "";
    return this.lang === "nb" ? (day ? `${day} kl. ${this.fmtAxis(s.hour)}` : this.t.atHour(this.fmtAxis(s.hour)))
      : `${day ? day + " " : ""}${this.fmtClock(s.hour)}`;
  }
  _pastWhy(s) {
    const t = this.t, f = s.flags || [];
    const shown = ["manual", "shed", "off", "disabled", "safety", "paused"].filter((k) => f.includes(k) && !(k === "paused" && f.includes("manual")));
    const reasons = f.includes("paused") || f.includes("disabled") ? [] : (s.reasons || []).filter((r) => r !== "schedule");
    return [...shown.map((k) => t.flags[k]), ...reasons.map((r) => t.reasons[r] || r)].join(". ");
  }
  _pastText(s, z) {
    const t = this.t, pct = z.mode === "percent";
    const parts = [];
    if (s.empty) parts.push("–");
    else parts.push(s.target == null ? t.modes.off.toLowerCase() : `${t.target.toLowerCase()} ${this.fmtT(s.target, z)}`);
    if (s.temp != null) parts.push(`${t.actualShort} ${this.fmtT(s.temp, { mode: "setpoint" })}`);
    if (pct && s.heating != null) parts.push(`${t.runtime.toLowerCase()} ${this.fmtT(s.heating * 100, z)}`);
    if (s.price != null) parts.push(this.fmtPrice(s.price));
    const why = this._pastWhy(s);
    return `${this._pastWhen(s)}: ${parts.join(", ")}${why ? ` – ${why}` : ""}`;
  }
  /** Stat tiles: [{label, value, sub, tone}] – wrap 2×2 on phones. */
  _tiles(items) {
    return el("div", { class: "tiles" }, items.filter(Boolean).map((it) => el("div", { class: `tile${it.tone ? " " + it.tone : ""}` },
      el("div", { class: "tl", text: it.label }),
      el("div", { class: "tv", text: it.value }),
      it.sub ? el("div", { class: "ts", text: it.sub }) : null)));
  }
  /** Look-back hours where the room missed the target (pre-heat aims above comfort, saving hours below it on purpose). */
  _cold(s) { return s.temp != null && s.target != null && s.temp < s.target - COLD && s.rawMode !== "preheat"; }
  _warm(s, i, series = this._series_ || []) {
    if (s.temp == null || s.target == null || s.temp <= s.target + WARM || !["normal", "comfort", "manual"].includes(s.rawMode)) return false;
    if ((s.flags || []).includes("shed")) return false;
    return !series.slice(Math.max(0, i - 3), i).some((x) => x.rawMode === "preheat");
  }

  _summary(z) {
    const t = this.t, pct = z.mode === "percent";
    const rows = (this._series_ || []).filter((s) => !s.partial && !s.empty);
    const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    const tiles = [];
    if (!pct) {
      const withT = rows.filter((s) => s.temp != null), both = withT.filter((s) => s.target != null);
      if (withT.length) {
        tiles.push({ label: t.pAvg, value: this.fmtT(mean(withT.map((s) => s.temp)), z),
          sub: both.length ? t.pAvgSub(this.fmtT(mean(both.map((s) => s.target)), z)) : null });
        tiles.push({ label: t.pBelow, value: t.hoursN(both.filter((s) => this._cold(s)).length), sub: t.pBelowSub(this.fmtNum(COLD, 1)) });
      }
    } else {
      const d = rows.filter((s) => s.target != null);
      if (d.length) tiles.push({ label: t.pAvgPct, value: this.fmtT(mean(d.map((s) => s.target)), z) });
      const r = rows.filter((s) => s.heating != null);
      if (r.length) tiles.push({ label: t.runtime, value: this.fmtT(mean(r.map((s) => s.heating * 100)), z) });
    }
    const kw = rows.filter((s) => s.kwh != null);
    if (kw.length) {
      const cost = rows.filter((s) => s.cost != null);
      tiles.push({ label: t.pEnergy, value: `${this.fmtNum(kw.reduce((a, s) => a + s.kwh, 0), 1)} kWh`,
        sub: cost.length ? `≈ ${this.fmtMoney(cost.reduce((a, s) => a + s.cost, 0))}` : null });
    }
    const done = new Set(rows.map((s) => s.start));
    const home = (this._histData()?.home || []).filter((h) => done.has(h.start) && typeof h.house_kwh === "number");
    if (home.length) {
      const top = home.reduce((a, h) => (h.house_kwh > a.house_kwh ? h : a));
      const caps = home.map((h) => h.cap_kw).filter((c) => typeof c === "number");
      const cap = typeof top.cap_kw === "number" ? top.cap_kw : caps.length ? Math.max(...caps) : null;
      const over = cap != null && top.house_kwh > cap;
      tiles.push({ label: t.pTop, value: `${this.fmtNum(top.house_kwh, 1)} kW`, tone: over ? "bad" : "",
        sub: cap == null ? null : over ? t.pOver(this.fmtNum(cap, 1)) : t.pCap(this.fmtNum(cap, 1)) });
    }
    const label = this._range === "48" ? t.r48 : t.r24;
    const wrap = el("div", { class: "summary" }, el("div", { class: "sumh", text: label }));
    if (!tiles.length) wrap.append(el("p", { class: "caption", text: t.sumNone }));
    else wrap.append(this._tiles(tiles));
    return wrap;
  }

  /** Whole-house kW per shown hour (only with a house meter): {kw, cap, kind: measured|live|planned}. */
  _houseRows(series) {
    if (this._edit || !series.length) return null;
    let rows;
    if (this.past) {
      const data = this._histData();
      if (!data?.home || data.metered === false) return null;
      const byStart = new Map(data.home.map((h) => [h.start, h]));
      rows = series.map((s) => {
        const r = byStart.get(s.start);
        return r && typeof r.house_kwh === "number"
          ? { kw: r.house_kwh, cap: typeof r.cap_kw === "number" ? r.cap_kw : null, kind: r.live || s.partial ? "live" : "measured" } : null;
      });
    } else {
      const pk = this.entry?.peak;
      if (!pk?.metered || typeof pk.cap_kw !== "number" || !Array.isArray(pk.load)) return null;
      const key = (d) => this.dateKey(d) + "T" + this.hh(d);
      const byKey = new Map(pk.load.map((l) => [key(l.start), l]));
      rows = series.map((s) => {
        const r = s.start ? byKey.get(key(s.start)) : null;
        return r && typeof r.kw === "number" ? { kw: r.kw, cap: pk.cap_kw, kind: r.measured ? "measured" : r.live ? "live" : "planned" } : null;
      });
    }
    return rows.some(Boolean) ? rows : null;
  }

  // ------------------------------------------------------------- prices
  /** "kr" for the Nordic crowns, else the currency code. */
  get curUnit() { const c = this.entry?.currency || ""; return ["NOK", "SEK", "DKK", "kr"].includes(c) ? "kr" : c; }
  /** Prices as shown: hour price + the configured extra. */
  _priceOf(s) { return s.price == null ? null : s.price + (s.past ? 0 : this.entry?.price_extra || 0); }
  /** A flat day (fixed contract such as Norgespris, or hardly any spread): no bars, one line. */
  _flatPrice(series) {
    const ps = series.map((s) => this._priceOf(s)).filter((v) => v != null);
    if (!ps.length) return null;
    const mn = Math.min(...ps), mx = Math.max(...ps);
    const fixed = this.entry?.tariff?.mode === "fixed";
    return fixed || mx - mn < Math.max(0.05, 0.08 * Math.abs(mx)) ? { fixed, mn, mx } : null;
  }
  /** "76 øre/kWh" / "76–86 øre/kWh" for the crowns, else "0.76 EUR/kWh". */
  _priceRange(mn, mx) {
    const c = this.entry?.currency || "";
    const sub = { NOK: "øre", DKK: "øre", SEK: "öre", kr: "øre" }[c];
    if (sub) {
      const a = Math.round(mn * 100), b = Math.round(mx * 100);
      return `${a === b ? a : `${a}–${b}`} ${sub}/kWh`;
    }
    return Math.abs(mx - mn) < 0.005 ? this.fmtPrice(mn) : `${this.fmtNum(mn, 2)}–${this.fmtPrice(mx)}`;
  }
  /** [before, price, after] for the one-line flat price row. */
  _flatText(flat) {
    const t = this.t;
    const [a, b] = (flat.fixed ? t.flatFixed("\u0000") : t.flatEven("\u0000")).split("\u0000");
    return [a, this._priceRange(flat.mn, flat.mx), b];
  }
  /** The first run of expensive/extreme hours ahead: {a, b, max} (a, b as clock labels). */
  _nextPeak(hours) {
    const i = hours.findIndex((h) => h.level === "expensive" || h.level === "extreme");
    if (i < 0) return null;
    let j = i;
    while (j + 1 < hours.length && ["expensive", "extreme"].includes(hours[j + 1].level)) j++;
    const max = Math.max(...hours.slice(i, j + 1).map((h) => h.price ?? 0)) + (this.entry?.price_extra || 0);
    return { span: `${this.fmtAxis(+this.hh(hours[i].start))}–${this.fmtAxis((+this.hh(hours[j].start) + 1) % 24)}`, max, start: i };
  }

  // ------------------------------------------------------------- header
  _hero(z) {
    const t = this.t, pct = z.mode === "percent";
    const hours = z.plan?.hours || [];
    const h0 = hours[0];
    const hasT = z.room_temperature != null;
    const tgt = (h) => (h.target == null ? t.modes.off : this.fmtT(h.target, z));
    const big = hasT ? this.fmtT(z.room_temperature, { mode: "setpoint" }) : h0 ? tgt(h0) : "–";
    const side = el("div", { class: "side" });
    if (hasT && h0) side.append(el("div", { class: "tgt" }, `${t.targetNow} `, el("b", { text: tgt(h0) })));
    else if (!hasT) side.append(el("div", { class: "tgt", text: pct ? t.levelNow : t.targetNow }));
    if (h0 && z.auto && !z.manual_until) {
      const nxt = hours.slice(1).find((h) => h.target !== h0.target);
      const clock = nxt ? this.fmtClock(+this.hh(nxt.start)) : "";
      side.append(el("div", { class: "nxt", text: !nxt ? t.noChange : nxt.target === null ? t.offFrom(clock) : t.valFrom(this.fmtT(nxt.target, z), clock) }));
    }
    const main = el("div", { class: "heromain" }, el("div", { class: "big", text: big }), side);
    return el("div", { class: "hero" }, main, this.hasFeedback(z) ? this._feedbackRow(z) : null);
  }

  /** One chip with why the room is at its target right now, in the reason's colour. */
  _status(z) {
    const t = this.t, hours = z.plan?.hours || [], h0 = hours[0];
    let mode = "normal", text;
    const peak = this._nextPeak(hours);
    if (!z.auto) { mode = "off"; text = t.st.disabled; }
    else if (z.manual_until) { mode = "comfort"; text = t.st.manual(this.fmtClock(+this.hh(z.manual_until))); }
    else if (!h0) return null;
    else {
      const r = h0.reasons || [];
      mode = MODE_COLOR[h0.mode] ? h0.mode : "normal";
      if (h0.target == null || mode === "off") { mode = "off"; text = t.st.off; }
      else if (mode === "preheat") text = peak ? t.st.preheatPeak(peak.span) : t.st.preheat;
      else if (mode === "save") text = t.st.save;
      else if (mode === "away" || r.includes("away")) text = t.st.away;
      else if (r.includes("peak_limit")) text = t.st.peak;
      else if (r.includes("safety")) text = t.st.safety;
      else if (mode === "setback") text = t.st.setback;
      else if (mode === "comfort") text = t.st.comfort;
      else if (r.includes("warmup")) text = t.st.warmup;
      else text = t.st.normal;
    }
    const row = el("div", { class: "statusrow" },
      el("span", { class: "status", style: `--c:${MODE_COLOR[mode]}` }, el("i", { "aria-hidden": "true" }), text));
    if (peak && !(mode === "preheat" && z.auto && !z.manual_until) && !this._flatPrice(this._series(z)))
      row.append(el("span", { class: "peaknote", text: t.peakSoon(peak.span, this.fmtPrice(peak.max)) }));
    return row;
  }

  costLine(z) {
    const p = z.plan;
    const hours = (p?.hours || []).slice(0, 24);
    if (!hours.length || hours.some((h) => h.cost === null)) {
      return typeof p?.explanation === "string" ? p.explanation.trim() : "";
    }
    if (typeof p.explanation === "string" && p.explanation.trim()) return p.explanation.trim();
    const extra = this.entry?.price_extra || 0;
    const cost = hours.reduce((a, h) => a + h.cost + h.kwh * extra, 0);
    const save = (p.savings || 0) + extra * ((p.baseline_kwh || 0) - (p.kwh || 0));
    if (p.extra_kwh === undefined && p.savings_pct === undefined && p.comfort_deficit === undefined)
      return this.t.cost(this.fmtMoney(cost), save > 0.005 ? this.fmtMoney(save) : 0);
    const t = this.t;
    const kwh = hours.reduce((a, h) => a + (h.kwh || 0), 0);
    let txt = t.cost24(this.fmtMoney(cost), this.fmtNum(kwh, 1));
    const pct = typeof p.savings_pct === "number" ? p.savings_pct : null;
    if (save < 0.2 || (pct !== null && pct < 2)) txt += t.littleSaving;
    else {
      txt += t.saves(this.fmtMoney(save), pct !== null ? `${this.fmtNum(pct, 0)} %` : "");
      const ek = typeof p.extra_kwh === "number" ? p.extra_kwh : null;
      if (ek !== null && ek >= 0.05) txt += t.kwhMore(this.fmtNum(ek, 1));
      else if (ek !== null && ek <= -0.05) txt += t.kwhLess(this.fmtNum(-ek, 1));
      else txt += ".";
    }
    if (typeof p.comfort_deficit === "number" && p.comfort_deficit >= 0.5) txt += t.deficit(this.fmtNum(p.comfort_deficit, 1));
    return txt;
  }

  /** Footer for the plan: cost, saving and the capacity tariff as tiles, the contract as a caption. */
  _planFooter(z) {
    const t = this.t, p = z.plan, pk = this.entry?.peak, tr = this.entry?.tariff;
    const hours = (p?.hours || []).slice(0, 24);
    const tiles = [];
    const notes = [];
    if (hours.length && !hours.some((h) => h.cost === null)) {
      const extra = this.entry?.price_extra || 0;
      const cost = hours.reduce((a, h) => a + h.cost + h.kwh * extra, 0);
      const kwh = hours.reduce((a, h) => a + (h.kwh || 0), 0);
      const save = (p.savings || 0) + extra * ((p.baseline_kwh || 0) - (p.kwh || 0));
      const pct = typeof p.savings_pct === "number" ? p.savings_pct : null;
      tiles.push({ label: t.tNext, value: this.fmtMoney(cost), sub: `${this.fmtNum(kwh, 1)} kWh` });
      if (this.entry?.tariff?.mode === "fixed" && save < 0.2) { /* the flat price row already says it */ }
      else if (save < 0.2 || (pct !== null && pct < 2)) tiles.push({ label: t.tSave, value: this.fmtMoney(Math.max(0, save)), sub: t.tLittle });
      else {
        const ek = typeof p.extra_kwh === "number" ? p.extra_kwh : null;
        const k = ek !== null && ek >= 0.05 ? t.kwhMoreS(this.fmtNum(ek, 1)) : ek !== null && ek <= -0.05 ? t.kwhLessS(this.fmtNum(-ek, 1)) : "";
        tiles.push({ label: t.tSave, value: this.fmtMoney(save), sub: t.tSaveSub(pct !== null ? `${this.fmtNum(pct, 0)} %` : "", k) });
      }
      if (typeof p.comfort_deficit === "number" && p.comfort_deficit >= 0.5) notes.push(t.deficitS(this.fmtNum(p.comfort_deficit, 1)));
    } else {
      const c = this.costLine(z);
      if (c) notes.push(c);
    }
    if (pk) {
      if (pk.step_label) tiles.push({ label: t.tStep, value: pk.step_label,
        sub: typeof pk.month_avg_kw === "number" ? t.tTop3(this.fmtNum(pk.month_avg_kw, 1)) : null });
      if (typeof pk.cap_kw === "number") tiles.push({ label: t.tCap, value: `${this.fmtNum(pk.cap_kw, 1)} kW`,
        sub: typeof pk.next_step_kw === "number" ? t.tNextStep(this.fmtNum(pk.next_step_kw, 1)) : null });
    }
    const out = [];
    if (tiles.length) out.push(this._tiles(tiles));
    const cap = [tr?.label, ...notes].filter(Boolean);
    if (cap.length) {
      const line = el("p", { class: "caption", text: cap.join(" · ") });
      if (pk && typeof pk.next_step_kw === "number") {
        const c = typeof pk.step_cost === "number" ? this.fmtMoney(pk.step_cost) : "";
        let title = t.nextStep(this.fmtNum(pk.next_step_kw, 1), c);
        if (Array.isArray(pk.top3_kw) && pk.top3_kw.length) title += ` · ${pk.top3_kw.map((v) => this.fmtNum(v, 1)).join(" / ")} kW`;
        line.title = title;
      }
      out.push(line);
    }
    return out;
  }

  // ------------------------------------------------------------- feedback
  hasFeedback(z) {
    const f = this.entry?.features;
    return !this._edit && ("feedback" in z || (Array.isArray(f) && f.includes("feedback")));
  }
  _flashFor(z) { return !!this._flash && this._flash.zone === z.zone_id && this._flash.until > Date.now(); }
  _feedbackRow(z) {
    const t = this.t;
    const busy = this._fbBusy === z.zone_id;
    const b = (kind, label, color) => el("button", {
      class: "chip", disabled: busy || undefined, "aria-label": `${label} – ${z.name}`, "data-fk": `fb:${kind}`,
      onclick: () => this._sendFeedback(z, kind),
    }, el("i", { style: `background:${color}`, "aria-hidden": "true" }), label);
    return el("div", { class: "fb", role: "group", "aria-label": t.fbLabel },
      b("too_cold", t.fbCold, "var(--hp-setback)"), b("too_warm", t.fbWarm, "var(--hp-preheat)"));
  }
  async _sendFeedback(z, kind) {
    this._fbBusy = z.zone_id; this._update(true);
    let text;
    try {
      const res = await this._hass.connection.sendMessagePromise({ type: "heat_planner/feedback", entry_id: this.entry.entry_id, zone: z.zone_id, kind });
      text = res?.message || "";
    } catch (e) { text = this.t.fbFail; }
    this._fbBusy = null;
    if (text) {
      this._flash = { zone: z.zone_id, text, until: Date.now() + 7000 };
      clearTimeout(this._flashT);
      this._flashT = setTimeout(() => { this._flash = null; this._update(true); }, 7050);
      this._announce(text);
    }
    this._update(true);
  }

  // ------------------------------------------------------------- render
  _ensureShell() {
    if (this._shell) return;
    const root = this.shadowRoot;
    root.innerHTML = "";
    root.append(el("style", { text: css }));
    this._card = el("ha-card");
    this._live = el("div", { class: "sr-only", "aria-live": "polite", "aria-atomic": "true" });
    this._body = el("div");
    this._card.append(this._live, this._body);
    root.append(this._card);
    this._shell = true;
  }

  _render() {
    this._ensureShell();
    const t = this.t;
    const active = this.shadowRoot.activeElement;
    const focusKey = active?.dataset?.fk || null;
    this._hideTip();
    const body = el("div");
    this._card.setAttribute("lang", this.lang === "nb" ? "nb" : "en");
    this._build(body, t);
    this._body.replaceWith(body);
    this._body = body;
    if (focusKey) {
      const target = [...this.shadowRoot.querySelectorAll("[data-fk]")].find((x) => x.dataset.fk === focusKey);
      target?.focus({ preventScroll: true });
    }
  }

  _build(card, t) {
    if (this._error && this._error.kind !== "missing") {
      card.append(el("div", { class: "state" },
        el("h2", { text: t.errTitle }),
        el("p", { text: this._error.kind === "timeout" ? t.errTimeout : this._error.message || "" }),
        el("button", { class: "btn primary", text: t.retry, "data-fk": "retry", onclick: () => this._retry() })));
      return;
    }
    if (!this._data && !this._error) {
      card.append(el("div", { role: "status", "aria-busy": "true", "aria-label": t.loading },
        el("div", { class: "sk story", style: "width:78%" }), el("div", { class: "sk line", style: "width:46%" }),
        el("div", { class: "sk chart" }), el("div", { class: "sk line", style: "width:62%" }),
        el("span", { class: "sr-only", text: t.loading })));
      return;
    }
    const entry = this.entry;
    if (!entry) {
      card.append(el("div", { class: "state" }, el("h2", { text: t.emptyTitle }), el("p", { text: t.emptyBody }),
        el("a", { class: "btn primary", href: SETUP_URL, target: "_blank", rel: "noopener noreferrer", "data-fk": "setup", text: t.emptyAction })));
      return;
    }
    const z = this.zone;
    if (!z) {
      const filtered = (entry.zones || []).length > 0;
      card.append(el("div", { class: "state" }, el("h2", { text: t.noZonesTitle }), el("p", { text: filtered ? t.noMatch : t.noZonesBody }),
        filtered ? null : el("a", { class: "btn", href: INTEGRATION_URL, target: "_blank", rel: "noopener noreferrer", "data-fk": "integration", text: t.noZonesAction })));
      return;
    }
    if (this._zone !== z.zone_id) { this._zone = z.zone_id; this._focusIdx = null; }

    if (this._isStale()) card.append(el("div", { class: "notice" }, el("span", { text: t.stale(this.fmtTime(new Date(entry.updated))) })));

    let panel = card;
    if (this.zones.length > 1) {
      const tabs = el("div", { class: "zones", role: "tablist", "aria-label": t.rooms });
      const zs = this.zones;
      zs.forEach((x, i) => {
        const sel = x.zone_id === z.zone_id;
        const un = this._unavailable(x);
        tabs.append(el("button", {
          role: "tab", id: `${this._uid}-tab-${i}`, "aria-selected": String(sel), "aria-controls": `${this._uid}-panel`,
          tabindex: sel ? "0" : "-1", "data-fk": `tab:${x.zone_id}`,
          onclick: () => this._selectZone(x.zone_id),
          onkeydown: (ev) => {
            const k = ev.key;
            let j = null;
            if (k === "ArrowRight") j = (i + 1) % zs.length;
            else if (k === "ArrowLeft") j = (i - 1 + zs.length) % zs.length;
            else if (k === "Home") j = 0;
            else if (k === "End") j = zs.length - 1;
            if (j === null) return;
            ev.preventDefault();
            this._selectZone(zs[j].zone_id, true);
          },
        }, x.name, un ? el("span", { class: "warn", "aria-hidden": "true", text: "!" }) : null,
        un ? el("span", { class: "sr-only", text: ` (${t.unavailableShort})` }) : null));
      });
      card.append(tabs);
      const idx = zs.findIndex((x) => x.zone_id === z.zone_id);
      panel = el("div", { role: "tabpanel", id: `${this._uid}-panel`, "aria-labelledby": `${this._uid}-tab-${idx}` });
      card.append(panel);
      requestAnimationFrame(() => {
        const b = tabs.children[idx];
        if (b && (b.offsetLeft < tabs.scrollLeft || b.offsetLeft + b.offsetWidth > tabs.scrollLeft + tabs.clientWidth))
          tabs.scrollLeft = Math.max(0, b.offsetLeft - 12);
      });
    }

    if (this._unavailable(z)) panel.append(el("div", { class: "notice" }, el("span", { text: t.unavailable(z.name) })));
    if (this._actionError) {
      panel.append(el("div", { class: "notice err" }, el("span", { text: t.actionFail(this._actionError) }),
        el("button", { class: "btn", text: t.dismiss, "data-fk": "dismiss", onclick: () => { this._actionError = null; this._update(true); } })));
    }

    if (this.zones.length === 1) panel.append(el("div", { class: "zname", text: z.name }));
    const hero = this._hero(z);
    const status = this._status(z);
    panel.append(hero);
    if (status) panel.append(status);
    // what the header says, for screen readers when it changes
    const storyText = [z.name, hero.querySelector(".heromain")?.textContent, status?.textContent].filter(Boolean).join(". ");
    if (this._lastStory && this._lastStory.zone === z.zone_id && this._lastStory.text !== storyText) this._announce(storyText);
    this._lastStory = { zone: z.zone_id, text: storyText };

    const hintId = `${this._uid}-keys`;
    const past = this.past;
    if (this.hasHistory && !this._edit) panel.append(this._rangeBar());
    if (past) this._loadHistory();
    if (past && !this._histZone(z)) {
      panel.append(this._histPlaceholder());
      this._series_ = []; this._geo = null;
    } else {
      const chart = el("div", {
        class: `chartwrap${this._edit ? " editing" : ""}`, tabindex: "0", role: "group", "data-fk": "chart",
        "aria-roledescription": this.lang === "nb" ? "diagram" : "chart",
        "aria-label": past ? t.histLabel(z.name, this.pastHours)
          : t.chartLabel(z.name, this._edit ? 24 : Math.min(this._config.hours, (z.plan?.hours || []).length)),
        "aria-describedby": hintId,
      });
      panel.append(chart, el("p", { class: "sr-only", id: hintId, text: this._edit ? t.editKeys : past ? t.histKeys : t.chartKeys }));
      this._drawChart(chart, z);
      this._bindChart(chart, z);
      const key = this._key(z);
      if (key) panel.append(key);
      if (past && this._hist?.error && !this._hist.loading) panel.append(this._histPlaceholder());
    }

    if (this._edit) panel.append(this._editor(z));
    else {
      if (this._flashFor(z)) panel.append(el("p", { class: "cost flash", text: this._flash.text }));
      if (past) panel.append(this._summary(z));
      else panel.append(...this._planFooter(z));
      const showHelp = this._config.show_help !== false && customElements.get("heat-planner-help");
      panel.append(el("div", { class: "bar" },
        el("div", { class: "row" },
          showHelp ? el("button", { class: "btn ghost", text: this._showHelp ? t.closeHelp : t.help, "aria-expanded": String(!!this._showHelp),
            "data-fk": "help", onclick: () => { this._showHelp = !this._showHelp; this._update(true); } }) : null,
          z.manual_until ? el("button", { class: "btn", text: t.resume, "data-fk": "resume", onclick: () => this._settings(z, {}, true) }) : null,
          el("button", { class: "btn", text: t.edit, "data-fk": "edit", onclick: () => this._startEdit(z, this.dateKey(new Date())) }))));
    }
    panel.append(this._settingsPanel(z));
    if (this._showHelp && this._config.show_help !== false && customElements.get("heat-planner-help")) {
      if (!this._helpEl) this._helpEl = document.createElement("heat-planner-help");
      this._helpEl.hass = this._hass;
      panel.append(el("div", { class: "helpwrap" }, this._helpEl));
    }
  }

  _selectZone(id, focus = false) {
    this._zone = id; this._edit = null; this._focusIdx = null;
    this._update(true);
    if (focus) [...this.shadowRoot.querySelectorAll("[role=tab]")].find((b) => b.dataset.fk === `tab:${id}`)?.focus();
  }

  // ------------------------------------------------------------- chart
  _series(z) {
    if (this.past) {
      return (this._histZone(z)?.hours || []).map((h) => ({
        ...h, past: true, key: this.dateKey(h.start), hour: +this.hh(h.start), scheduled: h.target,
        mode: MODE_COLOR[h.mode] ? h.mode : "normal", rawMode: h.mode, level: h.level || "normal",
        empty: h.mode == null && h.target == null && h.temp == null && h.heating == null,
      }));
    }
    if (!this._edit) {
      const hrs = (z.plan?.hours || []).slice(0, this._config.hours);
      return hrs.map((h) => ({ ...h, key: this.dateKey(h.start), hour: +this.hh(h.start) }));
    }
    const { date, values } = this._edit;
    const plan = this._edit.preview || z.plan;
    const byKey = new Map((plan?.hours || []).map((h) => [this.dateKey(h.start) + "T" + this.hh(h.start), h]));
    const prices = new Map((this.entry?.prices || []).map((p) => [this.dateKey(p.start) + "T" + this.hh(p.start), p]));
    return values.map((v, i) => {
      const key = date + "T" + String(i).padStart(2, "0");
      const h = byKey.get(key);
      const p = prices.get(key);
      return {
        key: date, hour: i, editIdx: i, scheduled: this.abs(z, v),
        target: h ? h.target : this.abs(z, v), predicted: h?.predicted ?? null,
        price: h?.price ?? p?.price ?? null, level: h?.level ?? p?.level ?? "normal",
        mode: h?.mode ?? "normal", reasons: h?.reasons ?? ["schedule"], cost: h?.cost ?? null,
      };
    });
  }

  _chartW() { return Math.max(260, this._width - (this._width <= 480 ? 24 : 36)); }

  /** Weekday + date for the day boundary on the hour axis: "tir 17." / "Tue 17". */
  _dayLabel(key) {
    const d = +key.slice(8, 10), wd = this.t.days[this.weekday(key)];
    return this.lang === "nb" ? `${wd} ${d}.` : `${wd} ${d}`;
  }

  _drawChart(wrap, z) {
    const series = this._series(z);
    this._series_ = series;
    if (!series.length) { wrap.replaceChildren(); this._geo = null; return; }
    const t = this.t, past = this.past, edit = !!this._edit, percent = z.mode === "percent";
    const W = this._chartW(), narrow = W < 520;
    const m = { l: percent ? 40 : 34, r: 8, t: past ? 42 : 30, b: 0 };
    const ch = Math.round(Math.min(220, Math.max(140, W * 0.3)));
    const n = series.length, bw = (W - m.l - m.r) / n;
    const X = (i) => m.l + i * bw;

    // y range: tight around the data (±0.5°) so changes are visible; 3–4 gridlines
    let lo, hi, step;
    if (percent) { lo = 0; hi = 100; step = 50; }
    else {
      const vals = series.flatMap((s) => [edit ? s.scheduled : null, s.target, s.predicted, s.temp]).filter((v) => v != null);
      if (z.room_temperature != null && !past && !edit) vals.push(z.room_temperature);
      if (!vals.length) vals.push(z.settings?.comfort ?? 21);
      lo = Math.floor((Math.min(...vals) - 0.5) * 2) / 2; hi = Math.ceil((Math.max(...vals) + 0.5) * 2) / 2;
      if (edit && hi - lo < 6) { const c = (hi + lo) / 2; lo = Math.floor(c - 3); hi = Math.ceil(c + 3); }
      else if (hi - lo < 2) { lo = Math.floor(((hi + lo) / 2 - 1) * 2) / 2; hi = lo + 2; }
      step = [0.5, 1, 2, 5].find((k) => (hi - lo) / k <= 3.4) || 5;
    }
    if ((this._drag || this._kbEdit) && this._frozen) ({ lo, hi, step } = this._frozen);
    const Y = (v) => m.t + ch - ((v - lo) / (hi - lo)) * ch;
    const bot = m.t + ch;

    // panels below the hour axis: price (unless flat), whole house (only with a meter)
    const axisY = bot + 16;
    let y = bot + 28;
    const flat = this._flatPrice(series);
    const pv = series.map((s) => this._priceOf(s));
    let pp = null, fp = null;
    if (!flat && pv.some((v) => v != null)) { pp = { top: y, bt: y + 26, h: narrow ? 44 : 54 }; y = pp.bt + pp.h + 4; }
    else if (flat) { fp = { top: y }; y += 22; }
    const house = this._houseRows(series);
    this._house_ = house;
    let hp = null;
    if (house) { hp = { top: y + 12, bt: y + 38, h: narrow ? 30 : 36 }; y = hp.bt + hp.h + 4; }
    const H = Math.round(y);
    this._geo = { X, Y, bw, lo, hi, step, m, ch, n, W, H, percent };

    const uid = this._uid;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, "aria-hidden": "true", focusable: "false" });
    svg.append(el("defs", {},
      el("linearGradient", { id: `${uid}-fill`, x1: 0, y1: 0, x2: 0, y2: 1 },
        el("stop", { offset: "0", style: "stop-color:var(--hp-ink);stop-opacity:.12" }),
        el("stop", { offset: "1", style: "stop-color:var(--hp-ink);stop-opacity:0" }))));

    // direct labels: placed in priority order, dropped when they would collide
    const boxes = [];
    const label = (g, cands, text, o = {}) => {
      const fs = o.fs || 11, w = text.length * fs * 0.58 + 2;
      for (const [anchor, x, yb] of cands) {
        const x0 = anchor === "start" ? x : anchor === "end" ? x - w : x - w / 2;
        const b = { x0, x1: x0 + w, y0: yb - fs + 1, y1: yb + 3 };
        const [bl, br] = o.bounds || [0, W];
        if (b.x0 < bl || b.x1 > br) continue;
        if (boxes.some((q) => b.x0 < q.x1 + 3 && b.x1 > q.x0 - 3 && b.y0 < q.y1 && b.y1 > q.y0)) continue;
        boxes.push(b);
        g.append(el("text", { x, y: yb, "text-anchor": anchor, class: o.cls || "dl", style: o.color ? `fill:${o.color}` : null, text }));
        return true;
      }
      return false;
    };

    // runs of hours with the same reason (off = no target)
    const runs = [];
    series.forEach((s, i) => {
      if (s.empty) return;
      const mode = s.target == null ? "off" : MODE_COLOR[s.mode] ? s.mode : "normal";
      const last = runs[runs.length - 1];
      if (last && last.mode === mode && last.b === i) last.b = i + 1; else runs.push({ mode, a: i, b: i + 1 });
    });

    // grid + y labels
    const grid = el("g", { class: "grid" }), axis = el("g", { class: "axis" });
    const yfmt = this.nf({ maximumFractionDigits: 1 });
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) {
      grid.append(el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
      axis.append(el("text", { x: m.l - 7, y: Y(v) + 3.5, "text-anchor": "end", text: percent ? `${v} %` : `${yfmt.format(v)}°` }));
    }
    grid.append(el("line", { x1: m.l, x2: W - m.r, y1: bot, y2: bot, style: "stroke:var(--hp-line)" }));
    svg.append(grid);

    // day boundaries (through every panel) and the hour axis; the day label sits in the axis row
    const minPx = this.h12 ? 36 : 24;
    const every = [1, 2, 3, 4, 6, 12].find((k) => k * bw >= minPx && k >= (narrow ? 3 : 2)) || 12;
    const dayBoxes = [];
    const days = el("g");
    series.forEach((s, i) => {
      if (s.hour !== 0 || i === 0) return;
      days.append(el("line", { class: "dayline", x1: X(i), x2: X(i), y1: m.t - 4, y2: H }));
      const txt = this._dayLabel(s.key), w = txt.length * 6.4;
      const x = Math.max(w / 2, Math.min(W - w / 2, X(i)));
      axis.append(el("text", { class: "day", x, y: axisY, "text-anchor": "middle", text: txt }));
      dayBoxes.push([x - w / 2 - 6, x + w / 2 + 6]);
    });
    series.forEach((s, i) => {
      if (s.hour % every !== 0 || (s.hour === 0 && i > 0)) return;
      const txt = this.fmtAxis(s.hour), x = X(i), w = txt.length * 6.2;
      if (x - w / 2 < 0 || x + w / 2 > W) return;
      if (dayBoxes.some(([a, b]) => x + w / 2 > a && x - w / 2 < b)) return;
      axis.append(el("text", { x, y: axisY, "text-anchor": "middle", text: txt }));
    });
    svg.append(days, axis);

    // "now": the line goes behind the data, its pill on top
    const nk = this.nowKey();
    const idx = past ? series.findIndex((s) => s.partial) : series.findIndex((s) => s.key === nk.date && s.hour === nk.hour);
    this._nowIdx = idx;
    const nowX = idx >= 0 && !edit ? X(idx) + bw * nk.frac : null;
    if (nowX !== null) svg.append(el("g", { class: "now" }, el("line", { x1: nowX, x2: nowX, y1: 19, y2: bot })));

    // percent rooms: the planned level as a soft stepped area
    if (percent && !past) {
      let d = "";
      series.forEach((s, i) => { const yv = s.target == null ? bot : Y(s.target); d += `${i ? "L" : "M"}${X(i)},${yv}L${X(i + 1)},${yv}`; });
      svg.append(el("path", { d: `${d}L${X(n)},${bot}L${X(0)},${bot}Z`, fill: `url(#${uid}-fill)` }));
    }

    // expected room temperature: smooth line with a fading fill (plan); faint dashed what was predicted (look-back)
    const nf = !past && !edit ? this._nowFrac(series) : null;
    if (!percent) {
      const pts = series.map((s, i) => (s.predicted == null ? null : [X(i) + bw, Y(s.predicted)]));
      if (z.room_temperature != null && nf !== null) pts.unshift([X(0) + bw * nf, Y(z.room_temperature)]);
      for (const r of runsOf(pts)) {
        const d = smoothPath(r);
        if (!past) svg.append(el("path", { d: `${d}L${r[r.length - 1][0].toFixed(1)},${bot}L${r[0][0].toFixed(1)},${bot}Z`, fill: `url(#${uid}-fill)` }));
        svg.append(el("path", { class: past ? "pred" : "exp", d }));
      }
    }

    // look-back: where comfort was missed, shaded between the measured line and the target
    const nkf = Math.max(0.1, nk.frac);
    const mx = (s, i) => X(i) + (s.partial ? nkf / 2 : 0.5) * bw;
    if (past && !percent) {
      const g = el("g");
      series.forEach((s, i) => {
        const cold = this._cold(s), warm = !cold && this._warm(s, i, series);
        if (!cold && !warm) return;
        const lim = (v) => (cold ? Math.min(v, s.target) : Math.max(v, s.target));
        const pv0 = series[i - 1]?.temp, nv = series[i + 1]?.temp;
        const l = lim(pv0 != null ? (pv0 + s.temp) / 2 : s.temp), r = lim(nv != null && !s.partial ? (nv + s.temp) / 2 : s.temp);
        const xr = s.partial ? mx(s, i) : X(i + 1), yt = Y(s.target);
        g.append(el("path", { class: cold ? "cold" : "warm", d: `M${X(i)},${yt}L${X(i)},${Y(l)}L${mx(s, i)},${Y(s.temp)}L${xr},${Y(r)}L${xr},${yt}Z` }));
      });
      svg.append(g);
    }

    // the plan target: one continuous step line, coloured by reason
    const tg = el("g");
    let prevY = null, prevEnd = -1;
    for (const r of runs) {
      if (r.mode === "off") {
        tg.append(el("line", { class: "offl", x1: X(r.a) + 3, x2: X(r.b) - 3, y1: bot - 2, y2: bot - 2 }));
        prevY = null; prevEnd = r.b; continue;
      }
      let d = "";
      for (let i = r.a; i < r.b; i++) {
        const yv = Y(series[i].target);
        if (i === r.a) d += prevY !== null && prevEnd === r.a ? `M${X(i)},${prevY}L${X(i)},${yv}` : `M${X(i)},${yv}`;
        else d += `L${X(i)},${yv}`;
        d += `L${X(i + 1)},${yv}`;
        prevY = yv;
      }
      prevEnd = r.b;
      tg.append(el("path", { class: `tgtl${edit ? " ghost" : ""}`, d, style: `stroke:${MODE_COLOR[r.mode]}` }));
    }
    svg.append(tg);

    // editing: the curve being painted, with handles
    if (edit) {
      let d = "";
      series.forEach((s, i) => { const yv = Y(s.scheduled); d += `${i ? "L" : "M"}${X(i)},${yv}L${X(i + 1)},${yv}`; });
      svg.append(el("path", { class: "sched", d }));
      const hg = el("g");
      series.forEach((s, i) => hg.append(el("circle", {
        class: `handle${this._edit.dirty.has(i) ? " dirty" : ""}${this._focusIdx === i && this._kbActive ? " focus" : ""}`,
        cx: X(i) + bw / 2, cy: Y(s.scheduled), r: Math.max(2.5, Math.min(5, bw / 3)),
      })));
      svg.append(hg);
    }

    // look-back: what was measured (room temperature; for percent rooms the heater's on-time)
    if (past) {
      const pts = series.map((s, i) => {
        const v = percent ? (s.heating != null ? s.heating * 100 : null) : s.temp;
        return v == null ? null : [mx(s, i), Y(v)];
      });
      let d = "";
      pts.forEach((p, i) => {
        if (!p) return;
        d += `${pts[i - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
        if (!pts[i - 1] && !pts[i + 1]) svg.append(el("circle", { class: "actdot", cx: p[0], cy: p[1], r: 2.4 }));
      });
      if (d) svg.append(el("path", { class: "act", d, style: percent ? "stroke-width:1.5" : null }));
    }

    // price panel: bars by level, two labelled gridlines, the highest hour of each day and the lowest labelled
    if (pp) {
      const g = el("g");
      const vals = pv.filter((v) => v != null);
      const pmax = Math.max(...vals), pmin = Math.min(0, ...vals);
      const top = niceCeil(Math.max(pmax, 0.01));
      const Yp = (v) => pp.bt + pp.h - ((v - pmin) / (top - pmin)) * pp.h;
      const plbl = t.priceLbl(`${this.curUnit}/kWh`);
      g.append(el("text", { class: "plbl", x: 0, y: pp.top + 10, text: plbl }));
      boxes.push({ x0: 0, x1: plbl.length * 6.2, y0: pp.top, y1: pp.top + 13 });
      const pg = el("g", { class: "grid" }), pa = el("g", { class: "axis" });
      const pf = this.nf({ maximumFractionDigits: 2 });
      for (const v of [top / 2, top]) {
        pg.append(el("line", { x1: m.l, x2: W - m.r, y1: Yp(v), y2: Yp(v) }));
        pa.append(el("text", { x: m.l - 7, y: Yp(v) + 3.5, "text-anchor": "end", text: pf.format(v) }));
      }
      pg.append(el("line", { x1: m.l, x2: W - m.r, y1: Yp(0), y2: Yp(0), style: "stroke:var(--hp-line)" }));
      g.append(pg, pa);
      series.forEach((s, i) => {
        const v = pv[i];
        if (v == null) return;
        const y0 = Yp(Math.max(0, v)), y1 = Yp(Math.min(0, v));
        g.append(el("rect", { class: "pbar", x: X(i) + bw * 0.15, y: y0, width: bw * 0.7, height: Math.max(1.5, y1 - y0),
          rx: Math.min(2, bw * 0.18), style: `fill:${LEVEL_COLOR[s.level] || LEVEL_COLOR.normal}` }));
      });
      const best = {};
      pv.forEach((v, i) => { if (v != null && (best[series[i].key] == null || v > pv[best[series[i].key]])) best[series[i].key] = i; });
      const imin = pv.reduce((b, v, i) => (v != null && (b < 0 || v < pv[b]) ? i : b), -1);
      for (const i of [...Object.values(best).sort((a, b) => pv[b] - pv[a]), imin]) {
        label(g, [["middle", X(i) + bw / 2, Yp(Math.max(0, pv[i])) - 4]], this.fmtNum(pv[i], 2), { cls: "vlbl", fs: 10 });
      }
      svg.append(g);
    }

    if (fp) {
      const parts = this._flatText(flat);
      svg.append(el("text", { class: "plbl", x: 0, y: fp.top + 11 }, parts[0], el("tspan", { class: "flatv", text: parts[1] }), parts[2]));
      boxes.push({ x0: 0, x1: parts.join("").length * 6.2, y0: fp.top, y1: fp.top + 14 });
    }

    // whole house: measured solid, planned outlined, the cap as a labelled line; over the cap in red
    if (hp) {
      const g = el("g");
      const caps = house.map((r) => r?.cap ?? null);
      const maxLoad = Math.max(0.5, ...house.map((r) => r?.kw ?? 0)), maxCap = Math.max(0, ...caps.map((c) => c ?? 0));
      // a cap far above the load would squash the bars: scale to the load and name the cap instead
      const capIn = maxCap > 0 && maxCap <= maxLoad * 1.6;
      const maxv = capIn ? Math.max(maxLoad, maxCap * 1.12) : niceCeil(maxLoad * 1.05);
      const Yh = (v) => hp.bt + hp.h - (v / maxv) * hp.h;
      g.append(el("text", { class: "plbl", x: 0, y: hp.top + 10, text: t.houseLbl }));
      boxes.push({ x0: 0, x1: t.houseLbl.length * 6.2, y0: hp.top, y1: hp.top + 13 });
      g.append(el("line", { x1: m.l, x2: W - m.r, y1: Yh(0), y2: Yh(0), style: "stroke:var(--hp-line)" }));
      house.forEach((r, i) => {
        if (!r) return;
        const over = r.cap != null && r.kw > r.cap + 1e-9, yv = Yh(r.kw);
        g.append(el("rect", { class: `kw${r.kind === "planned" ? " plan" : ""}${over ? " over" : ""}`,
          x: X(i) + bw * 0.15 + 0.5, y: yv, width: Math.max(1, bw * 0.7 - 1), height: Math.max(1, Yh(0) - yv), rx: Math.min(2, bw * 0.18) }));
      });
      let d = "", last = -1;
      caps.forEach((c, i) => { if (c == null) return; d += `${i && caps[i - 1] != null ? "L" : "M"}${X(i)},${Yh(c)}L${X(i + 1)},${Yh(c)}`; last = i; });
      if (d && capIn) {
        g.append(el("path", { class: "capl", d }));
        const c = caps[last], cy = Yh(c);
        label(g, [["end", W - m.r, cy - 4], ["end", W - m.r, cy + 12]], t.capLbl(this.fmtNum(c, 1)), { cls: "capt", fs: 10 });
      } else {
        g.append(el("line", { x1: m.l, x2: W - m.r, y1: Yh(maxv), y2: Yh(maxv), style: "stroke:var(--hp-grid)" }));
        g.append(el("text", { class: "vlbl", x: m.l - 7, y: Yh(maxv) + 3.5, "text-anchor": "end", text: this.fmtNum(maxv, maxv < 3 ? 1 : 0) }));
        if (last >= 0) label(g, [["end", W - m.r, hp.top + 10]], t.capLbl(this.fmtNum(caps[last], 1)), { cls: "capt", fs: 10 });
      }
      svg.append(g);
    }

    // look-back events on a slim row above the chart: ring = changed by hand,
    // triangle = set back for the capacity cap, square = heating off (shape, not colour alone)
    if (past) {
      const g = el("g", { class: "mkr" });
      const my = m.t - 12;
      series.forEach((s, i) => {
        const kinds = ["manual", "shed", "off"].filter((k) => (s.flags || []).includes(k));
        const st = Math.min(9, bw / Math.max(1, kinds.length));
        kinds.forEach((k, j) => {
          const cx = X(i) + bw / 2 + (j - (kinds.length - 1) / 2) * st;
          if (k === "manual") g.append(el("circle", { class: "ring", cx, cy: my, r: 3.4 }));
          else if (k === "shed") g.append(el("path", { class: "tri", d: `M${cx - 3.8},${my - 3}L${cx + 3.8},${my - 3}L${cx},${my + 3.6}Z` }));
          else g.append(el("rect", { class: "sq", x: cx - 3, y: my - 3, width: 6, height: 6, rx: 1 }));
        });
      });
      svg.append(g);
    }

    // the "now" pill
    if (nowX !== null) {
      const txt = t.nowLabel, w = txt.length * 6.4 + 16, px = Math.max(w / 2, Math.min(W - w / 2, nowX));
      boxes.push({ x0: px - w / 2, x1: px + w / 2, y0: 2, y1: 20 });
      svg.append(el("g", { class: "now" }, el("rect", { x: px - w / 2, y: 3, width: w, height: 17, rx: 8.5 }),
        el("text", { x: px, y: 15, "text-anchor": "middle", text: txt })));
    }

    // direct labels on the temperature panel
    const dl = el("g");
    const inner = [m.l + 2, W - m.r];
    for (const r of runs.filter((x) => x.mode === "off")) {
      label(dl, [["middle", (X(r.a) + X(r.b)) / 2, bot - 9]], t.offShort, { color: inkOf("var(--hp-off)"), bounds: [0, W] });
    }
    if (!past && !edit) {
      if (!percent && z.room_temperature != null && nf !== null) {
        const x = X(0) + bw * nf, yv = Y(z.room_temperature);
        dl.append(el("circle", { class: "dot", cx: x, cy: yv, r: 4, style: "fill:var(--hp-ink)" }));
        boxes.push({ x0: x - 5, x1: x + 5, y0: yv - 5, y1: yv + 5 });
      }
      // the coldest expected point (only when it dips below where the room is now)
      if (!percent) {
        let im = -1;
        series.forEach((s, i) => { if (s.predicted != null && (im < 0 || s.predicted < series[im].predicted)) im = i; });
        if (im >= 0 && series[im].predicted < (z.room_temperature ?? Infinity) - 0.2) {
          const x = X(im) + bw, yv = Y(series[im].predicted);
          if (label(dl, [["middle", x, yv + 17], ["start", x + 7, yv + 4]], this.fmtT(series[im].predicted, { mode: "setpoint" }), { color: "var(--hp-ink)", bounds: inner })) {
            dl.append(el("circle", { class: "dot", cx: x, cy: yv, r: 3.2, style: "fill:var(--hp-ink)" }));
            boxes.push({ x0: x - 5, x1: x + 5, y0: yv - 5, y1: yv + 5 });
          }
        }
      }
      // the first raised / lowered stretch, named where it starts: above a raised line, under a lowered one
      // (the expected temperature runs below a raise and above a cut, so the label stays clear of it)
      let k = 0;
      const firsts = ["preheat", "save", "setback", "away", "comfort"].map((md) => runs.find((r) => r.mode === md)).filter(Boolean).sort((a, b) => a.a - b.a);
      for (const r of firsts) {
        if (k >= 2) break;
        const ys = series.slice(r.a, r.b).map((s) => Y(s.target));
        const up = Math.min(...ys) - 7, down = Math.max(...ys) + 15, name = t.modes[r.mode];
        const raised = r.mode === "preheat" || r.mode === "comfort";
        const c = raised
          ? [["start", X(r.a) + 3, up], ["end", X(r.b) - 3, up], ["start", X(r.a) + 3, down]]
          : [["start", X(r.a) + 3, down], ["end", X(r.b) - 3, down], ["start", X(r.a) + 3, up]];
        if (label(dl, c.filter(([, , yb]) => yb < bot - 2 && yb > m.t + 8), name, { color: inkOf(MODE_COLOR[r.mode]), bounds: inner })) k++;
      }
    }
    svg.append(dl);

    // hover / tap / keyboard crosshair
    const xh = el("g", { class: "xh", opacity: 0 },
      el("rect", { x: 0, y: m.t, width: bw, height: H - m.t }),
      el("line", { x1: 0, x2: 0, y1: m.t - 4, y2: H }),
      el("circle", { r: 4.5, cx: -20, cy: -20 }),
      el("circle", { r: 4, cx: -20, cy: -20, style: "fill:var(--hp-ink)" }));
    svg.append(xh);
    this._xh = xh;
    const old = wrap.querySelector("svg");
    if (old) old.replaceWith(svg); else wrap.prepend(svg);
  }

  _bindChart(wrap, z) {
    this._wrap = wrap;
    const geo = () => this._geo;
    const idxAt = (ev) => {
      const { W, m, bw, n } = geo();
      const r = wrap.getBoundingClientRect();
      const x = ((ev.clientX - r.left) / r.width) * W;
      return Math.max(0, Math.min(n - 1, Math.floor((x - m.l) / bw)));
    };
    const valAt = (ev) => {
      const { m, ch, lo, hi, percent } = geo();
      const svg = wrap.querySelector("svg");
      const r = svg.getBoundingClientRect();
      const H = svg.viewBox.baseVal.height;
      const y = ((ev.clientY - r.top) / r.height) * H;
      let v = lo + ((m.t + ch - y) / ch) * (hi - lo);
      v = percent ? Math.round(v / 5) * 5 : Math.round(v * 2) / 2;
      return this._clampVal(z, v);
    };
    wrap.addEventListener("pointermove", (ev) => {
      if (!this._geo) return;
      const i = idxAt(ev);
      if (this._drag) { this._paint(wrap, z, i, valAt(ev)); return; }
      if (ev.pointerType !== "mouse" && !(ev.buttons & 1)) return; // touch: scrub while the finger is down
      this._kbActive = false;
      this._highlight(wrap, z, i);
    });
    wrap.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse" && !this._drag && !this._kbActive) this._hideHL(); });
    if (this._edit) {
      wrap.addEventListener("pointerdown", (ev) => {
        if (ev.button > 0 || !this._geo) return;
        ev.preventDefault();
        wrap.setPointerCapture?.(ev.pointerId);
        this._drag = true; this._hideTip();
        this._frozen = { lo: this._geo.lo, hi: this._geo.hi, step: this._geo.step };
        this._paint(wrap, z, idxAt(ev), valAt(ev));
      });
      const end = () => { if (this._drag) { this._drag = false; this._frozen = null; this._update(true); this._preview(z); } };
      wrap.addEventListener("pointerup", end);
      wrap.addEventListener("pointercancel", end);
    } else {
      // touch: a tap shows the hour (a tap outside the chart hides it again)
      wrap.addEventListener("pointerdown", (ev) => {
        if (ev.pointerType === "mouse" || !this._geo) return;
        this._kbActive = false;
        this._highlight(wrap, z, idxAt(ev));
      });
    }
    wrap.addEventListener("keydown", (ev) => this._chartKey(ev, wrap, z));
    wrap.addEventListener("focus", () => {
      if (!wrap.matches(":focus-visible")) return; // mouse clicks: no keyboard cursor
      this._kbActive = true;
      if (this._focusIdx == null || this._focusIdx >= (this._series_ || []).length) this._focusIdx = this._nowIdx >= 0 && !this._edit ? this._nowIdx : this._edit ? new Date().getHours() : 0;
      this._highlight(wrap, z, this._focusIdx);
      if (this._edit) this._markHandle(wrap);
    });
    wrap.addEventListener("blur", () => { this._kbActive = false; this._hideHL(); this._markHandle(wrap); });
    // re-show the keyboard cursor after a re-render that kept focus here
    if (this._kbActive && this._focusIdx != null) requestAnimationFrame(() => {
      if (this.shadowRoot.activeElement === wrap) { this._highlight(wrap, z, this._focusIdx); this._markHandle(wrap); }
    });
  }

  _markHandle(wrap) {
    if (!this._edit) return;
    wrap.querySelectorAll("circle.handle").forEach((c, i) => c.classList.toggle("focus", this._kbActive && i === this._focusIdx));
  }

  _highlight(wrap, z, i) {
    const s = this._series_?.[i], g = this._geo;
    if (!s || !g) return;
    const xh = this._xh;
    if (xh) {
      const [band, line, c1, c2] = xh.children;
      const cx = g.X(i) + g.bw / 2;
      band.setAttribute("x", g.X(i)); band.setAttribute("width", g.bw);
      line.setAttribute("x1", cx); line.setAttribute("x2", cx);
      const tv = this._edit ? s.scheduled : s.target;
      if (tv != null && !s.empty) {
        c1.setAttribute("cx", cx); c1.setAttribute("cy", g.Y(tv));
        c1.style.fill = this._edit ? "var(--hp-ink)" : MODE_COLOR[s.mode] || MODE_COLOR.normal; c1.style.display = "";
      } else c1.style.display = "none";
      const mv = s.past ? (g.percent ? (s.heating != null ? s.heating * 100 : null) : s.temp) : null;
      if (mv != null) {
        c2.setAttribute("cx", g.X(i) + (s.partial ? Math.max(0.1, this.nowKey().frac) / 2 : 0.5) * g.bw); c2.setAttribute("cy", g.Y(mv)); c2.style.display = "";
      } else c2.style.display = "none";
      xh.setAttribute("opacity", 1);
    }
    this._showTip(wrap, s, z, i);
  }
  _hideHL() { this._xh?.setAttribute("opacity", 0); this._hideTip(); }

  /** A one-line key that only lists what this chart shows. */
  _key(z) {
    const t = this.t, past = this.past, pct = z.mode === "percent";
    const series = (this._series_ || []).filter((s) => !s.empty);
    if (!series.length) return null;
    const items = [];
    const sw = (style, label, cls = "") => el("span", {}, el("i", { class: cls, style, "aria-hidden": "true" }), label);
    const modes = new Set(series.map((s) => (s.target == null ? "off" : MODE_COLOR[s.mode] ? s.mode : "normal")));
    const ms = ["normal", "comfort", "preheat", "save", "setback", "away", "off"].filter((md) => modes.has(md) && t.modes[md]);
    if (this._edit) items.push(sw("background:var(--hp-ink)", t.edCurve), el("span", { text: `${t.keyPlan}:` }));
    else if (ms.length) items.push(el("span", { text: `${t.keyTarget}:` }));
    for (const md of ms) items.push(sw(`background:${MODE_COLOR[md]}`, t.modes[md]));
    if (!pct && !past && series.some((s) => s.predicted != null)) items.push(sw("", t.expected, "exp"));
    if (past && !pct && series.some((s) => s.temp != null)) items.push(sw("border-top-width:2.25px;opacity:1", t.measured, "exp"));
    if (past && pct && series.some((s) => s.heating != null)) items.push(sw("", t.runtime, "exp"));
    if (past && !pct) {
      if (series.some((s) => this._cold(s))) items.push(sw("background:color-mix(in srgb, var(--hp-setback) 30%, transparent)", t.tooCold, "sw"));
      if ((this._series_ || []).some((s, i) => this._warm(s, i))) items.push(sw("background:color-mix(in srgb, var(--hp-preheat) 28%, transparent)", t.tooWarm, "sw"));
    }
    if (past) {
      const glyph = { manual: el("circle", { class: "ring", cx: 5, cy: 5, r: 3.6 }), shed: el("path", { class: "tri", d: "M1,1.5L9,1.5L5,9Z" }), off: el("rect", { class: "sq", x: 1.5, y: 1.5, width: 7, height: 7, rx: 1 }) };
      for (const k of ["manual", "shed", "off"]) if (series.some((s) => (s.flags || []).includes(k)) && !(k === "off" && ms.includes("off")))
        items.push(el("span", {}, el("svg", { viewBox: "0 0 10 10", class: "mkr", "aria-hidden": "true" }, glyph[k]), t.mk[k]));
    }
    return el("div", { class: "key" }, items);
  }

  _clampVal(z, v) {
    const pct = z.mode === "percent";
    return Math.max(pct ? 0 : z.settings.min_value, Math.min(pct ? 100 : z.settings.max_value, v));
  }

  _chartKey(ev, wrap, z) {
    const series = this._series_ || [];
    if (!series.length) return;
    const n = series.length;
    let i = this._focusIdx ?? 0;
    const move = (j) => {
      ev.preventDefault();
      this._kbActive = true;
      this._focusIdx = Math.max(0, Math.min(n - 1, j));
      this._highlight(wrap, z, this._focusIdx);
      this._markHandle(wrap);
      const s = series[this._focusIdx];
      this._announce(this._edit ? `${this._when(s)}: ${this.fmtT(s.scheduled, z)}` : this._hourText(s, z));
    };
    switch (ev.key) {
      case "ArrowRight": return move(i + 1);
      case "ArrowLeft": return move(i - 1);
      case "Home": return move(0);
      case "End": return move(n - 1);
      case "PageUp": return move(i - 6);
      case "PageDown": return move(i + 6);
      case "Enter": case " ": {
        ev.preventDefault();
        if (this._edit || this.past) return;
        const s = series[i];
        this._focusIdx = s.hour;
        this._kbActive = true;
        this._startEdit(z, s.key);
        this._announce(`${this.t.editing(this._dayName(s.key))} ${this.t.editKeys}`);
        return;
      }
      case "ArrowUp": case "ArrowDown": {
        if (!this._edit) return;
        ev.preventDefault();
        const pct = z.mode === "percent";
        const s = series[i];
        const nv = this._clampVal(z, s.scheduled + (ev.key === "ArrowUp" ? 1 : -1) * (pct ? 5 : 0.5));
        if (!this._frozen) this._frozen = { lo: this._geo.lo, hi: this._geo.hi, step: this._geo.step };
        this._kbEdit = true;
        this._edit.values[i] = this.rel(z, nv);
        this._edit.dirty.add(i);
        this._update(true);
        this._kbEdit = false;
        this._announce(`${this._when(s)}: ${this.fmtT(nv, z)}`);
        this._preview(z);
        return;
      }
      case "Escape": {
        if (this._edit && !this._edit.dirty.size) { ev.preventDefault(); this._edit = null; this._frozen = null; this._update(true); return; }
        this._hideHL();
        return;
      }
      default:
    }
  }

  _nowFrac(series) {
    const nk = this.nowKey();
    return series.some((s) => s.key === nk.date && s.hour === nk.hour) ? nk.frac : null;
  }

  _when(s) {
    const today = this.dateKey(new Date());
    return `${s.key !== today ? this.weekdayShort(s.key) + " " : ""}${this.fmtClock(s.hour)}`;
  }
  _reasonText(s) { const t = this.t; return (s.reasons || []).map((r) => t.reasons[r] || r).join(". "); }
  _hourText(s, z) {
    if (s.past) return this._pastText(s, z);
    const t = this.t, extra = this.entry?.price_extra || 0;
    const parts = [this._when(s), s.target === null ? t.modes.off : `${t.target} ${this.fmtT(s.target, z)}`];
    if (s.predicted != null) parts.push(`${t.predicted} ${this.fmtT(s.predicted, { mode: "setpoint" })}`);
    if (s.price != null) parts.push(`${t.price} ${this.fmtPrice(s.price + extra)}, ${t.levels[s.level] || s.level}`);
    const r = this._reasonText(s);
    if (r) parts.push(r);
    return parts.join(". ") + ".";
  }

  _tipRow(k, v) { return el("div", { class: "tr" }, el("span", { class: "tk", text: k }), el("b", { text: v })); }
  _tipHouse(i) {
    const r = this._house_?.[i];
    if (!r) return null;
    return this._tipRow(`${this.t.house}${r.kind === "planned" ? ` (${this.t.planned})` : ""}`, `${this.fmtNum(r.kw, 1)} kW`);
  }
  _tipWhy(mode, text) {
    if (!text) return null;
    return el("div", { class: "why" }, el("i", { style: `background:${mode === "off" ? "var(--hp-off)" : MODE_COLOR[mode] || MODE_COLOR.normal}` }), text);
  }

  _pastTip(s, z, i) {
    const t = this.t, pct = z.mode === "percent";
    const why = this._pastWhy(s);
    return el("div", { class: "tip", "aria-hidden": "true" },
      el("div", { class: "when", text: this._pastWhen(s) }),
      s.empty ? el("div", { class: "tk", text: "–" }) : this._tipRow(t.target, s.target == null ? t.modes.off : this.fmtT(s.target, z)),
      s.temp != null ? this._tipRow(t.measured, this.fmtT(s.temp, { mode: "setpoint" })) : null,
      s.predicted != null ? this._tipRow(t.expected, this.fmtT(s.predicted, { mode: "setpoint" })) : null,
      pct && s.heating != null ? this._tipRow(t.runtime, this.fmtT(s.heating * 100, z)) : null,
      s.price != null ? this._tipRow(`${t.price} · ${t.levels[s.level] || s.level}`, this.fmtPrice(s.price)) : null,
      s.kwh != null ? this._tipRow(t.pEnergy, `${this.fmtNum(s.kwh, 2)} kWh${s.cost != null ? ` · ${this.fmtMoney(s.cost)}` : ""}`) : null,
      this._tipHouse(i),
      this._tipWhy(s.target == null && !s.empty ? "off" : s.mode, why));
  }

  _showTip(wrap, s, z, i) {
    const t = this.t;
    this._hideTip();
    if (s.past) return this._placeTip(wrap, this._pastTip(s, z, i), i);
    const tv = this._edit ? s.scheduled : s.target; // while editing: the value being painted
    const mode = s.target == null ? "off" : MODE_COLOR[s.mode] ? s.mode : "normal";
    const rs = (s.reasons || []).filter((r) => r !== "schedule" || (s.reasons || []).length === 1).slice(0, 2);
    const reason = rs.map((r) => t.reasons[r] || r).join(". ");
    const why = mode === "normal" ? reason : `${t.modes[mode]}${reason ? ` – ${reason}` : ""}`;
    const v = this._priceOf(s);
    const tip = el("div", { class: "tip", "aria-hidden": "true" },
      el("div", { class: "when", text: this._when(s) }),
      this._tipRow(t.target, tv === null ? t.modes.off : this.fmtT(tv, z)),
      s.predicted != null && z.mode !== "percent" ? this._tipRow(t.expected, this.fmtT(s.predicted, { mode: "setpoint" })) : null,
      v != null ? this._tipRow(`${t.price} · ${t.levels[s.level] || s.level}`, this.fmtPrice(v)) : null,
      this._tipHouse(i),
      this._tipWhy(mode, why));
    this._placeTip(wrap, tip, i);
  }
  /** Beside the crosshair (right of it, or left when there's no room), at the top of the chart. */
  _placeTip(wrap, tip, i) {
    const g = this._geo;
    wrap.append(tip);
    const w = wrap.clientWidth, sc = w / g.W, tw = tip.offsetWidth;
    const cx = (g.X(i) + g.bw / 2) * sc;
    let left = cx + 14;
    if (left + tw > w) left = cx - 14 - tw;
    if (left < 0) left = Math.max(0, Math.min(w - tw, cx - tw / 2));
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round((g.m.t + 2) * sc)}px`;
    this._tip = tip;
  }
  _hideTip() { this._tip?.remove(); this._tip = null; }

  // ------------------------------------------------------------- editing
  _dayName(date) {
    const t = this.t;
    const today = this.dateKey(new Date());
    const tomorrow = this.dateKey(new Date(Date.now() + 864e5));
    return date === today ? t.today.toLowerCase() : date === tomorrow ? t.tomorrow.toLowerCase() : t.days[this.weekday(date)];
  }

  _startEdit(z, date) {
    this._range = "plan";
    this._edit = { date, values: this.dayValues(z, date), dirty: new Set(), preview: null, scope: "date" };
    this._frozen = null;
    this._update(true);
    this._preview(z);
  }

  _paint(wrap, z, i, absVal) {
    const v = this.rel(z, absVal);
    if (this._edit.values[i] === v && this._edit.dirty.has(i)) return;
    this._edit.values[i] = v; this._edit.dirty.add(i);
    cancelAnimationFrame(this._raf);
    this._raf = requestAnimationFrame(() => this._drawChart(wrap, z));
  }

  _preview(z) {
    if (!this._edit) return;
    clearTimeout(this._pt);
    this._pt = setTimeout(async () => {
      const ed = this._edit;
      if (!ed) return;
      try {
        const res = await this._hass.callWS({
          type: "heat_planner/preview", entry_id: this.entry.entry_id, zone_id: z.zone_id,
          date: ed.date, values: [...ed.values],
        });
        if (this._edit === ed && !this._drag) { ed.preview = res; this._update(true); }
      } catch (e) { /* preview is best effort */ }
    }, 160);
  }

  _editor(z) {
    const t = this.t;
    const e = this._edit;
    const today = this.dateKey(new Date());
    const tomorrow = this.dateKey(new Date(Date.now() + 864e5));
    const wd = this.weekday(e.date);
    const dayName = this._dayName(e.date);
    const pick = (date) => el("button", {
      class: "chip", "aria-pressed": String(e.date === date), "data-fk": `day:${date}`,
      text: date === today ? t.today : date === tomorrow ? t.tomorrow : t.days[this.weekday(date)],
      onclick: () => this._startEdit(z, date),
    });
    const dates = [0, 1, 2, 3, 4, 5, 6].map((k) => this.dateKey(new Date(Date.now() + k * 864e5)));
    const scopes = [
      ["date", t.onlyDay(dayName)], ["days", t.every(t.daysLong[wd])], ["weekdays", t.weekdays], ["weekend", t.weekend], ["all", t.all],
    ];
    const isOverride = !!z.overrides?.[e.date];
    const lblId = `${this._uid}-scope`;
    return el("div", { class: "editor" },
      el("div", { class: "chips", role: "group", "aria-label": t.edit }, dates.map(pick)),
      el("p", { class: "hint", text: t.dragHint + (isOverride ? " " + t.overrideNote : "") }),
      el("div", { class: "row" }, el("span", { class: "cost", id: lblId, text: t.applyTo }),
        el("div", { class: "chips", role: "group", "aria-labelledby": lblId }, scopes.map(([k, label]) => el("button", {
          class: "chip", "aria-pressed": String(e.scope === k), text: label, "data-fk": `scope:${k}`, onclick: () => { e.scope = k; this._update(true); },
        })))),
      el("div", { class: "bar" },
        el("span", { class: "cost", text: e.preview ? this.costLine({ ...z, plan: e.preview }) : "" }),
        el("div", { class: "row" },
          isOverride ? el("button", { class: "btn", text: t.resetDay, "data-fk": "reset", onclick: () => this._save(z, "date", null) }) : null,
          el("button", { class: "btn", text: e.dirty.size ? t.discard : t.done, "data-fk": "done", onclick: () => { this._edit = null; this._frozen = null; this._update(true); } }),
          el("button", { class: "btn primary", text: t.save, "data-fk": "save", disabled: !e.dirty.size && e.scope === "date",
            onclick: () => this._save(z, e.scope, e.values) }))));
  }

  _merge(res) {
    if (res?.entries) this._data = res;
    else if (res?.entry_id && this._data?.entries) {
      this._data = { ...this._data, entries: this._data.entries.map((x) => (x.entry_id === res.entry_id ? res : x)) };
    }
  }
  _fail(err) {
    this._actionError = err?.message || String(err);
    this._announce(this.t.actionFail(this._actionError));
    this._update(true);
  }

  async _save(z, scope, values) {
    const e = this._edit;
    const msg = { type: "heat_planner/save", entry_id: this.entry.entry_id, zone_id: z.zone_id, scope, values };
    if (scope === "date") msg.date = e.date;
    if (scope === "days") msg.days = [this.weekday(e.date)];
    try {
      const res = await this._hass.callWS(msg);
      this._edit = null; this._frozen = null; this._actionError = null;
      this._merge(res);
      this._announce(this.t.saved);
      this._update(true);
    } catch (err) { this._fail(err); }
  }

  async _settings(z, settings, resume = false, say = null) {
    try {
      const res = await this._hass.callWS({ type: "heat_planner/settings", entry_id: this.entry.entry_id, zone_id: z ? z.zone_id : null, settings, resume });
      this._actionError = null;
      this._merge(res);
      this._update(true);
      if (say) this._announce(say);
    } catch (err) { this._fail(err); }
  }

  // ------------------------------------------------------------- settings
  _settingsPanel(z) {
    const t = this.t;
    const s = z.settings;
    const pct = z.mode === "percent";
    const unit = pct ? " %" : "°";
    const num = (v) => this.nf({ maximumFractionDigits: 2 }).format(v);
    const d = el("details", { class: "settings", open: this._open || undefined, ontoggle: (ev) => { this._open = ev.target.open; } },
      el("summary", { text: t.settings, "data-fk": "settings" }));
    const grid = el("div", { class: "set" });
    let n = 0;
    const row = (label, control, key = `r${n++}`, cls = "") => {
      const id = `${this._uid}-l-${key}`;
      grid.append(el("div", { class: `srow${cls}` }, el("span", { class: "lbl", id, text: label }), control(id)));
    };
    const stepper = (label, key, stepv, min, max, fmt = (v) => `${num(v)}${unit}`, zone = z) => {
      const v = s[key];
      const set = (nv) => this._settings(zone, { [key]: nv }, false, `${label}: ${fmt(nv)}`);
      row(label, (id) => el("div", { class: "step", role: "group", "aria-labelledby": id },
        el("button", { class: "pm", "aria-label": t.dec(label), text: "−", "data-fk": `set:${key}:-`, "aria-disabled": v <= min ? "true" : undefined,
          onclick: () => { if (v > min) set(Math.max(min, +(v - stepv).toFixed(2))); } }),
        el("span", { class: "val", text: fmt(v) }),
        el("button", { class: "pm", "aria-label": t.inc(label), text: "+", "data-fk": `set:${key}:+`, "aria-disabled": v >= max ? "true" : undefined,
          onclick: () => { if (v < max) set(Math.min(max, +(v + stepv).toFixed(2))); } })), key);
    };
    const toggle = (label, key, on, zone, settings) => row(label, (id) => el("button", {
      class: "tog", role: "switch", "aria-checked": String(!!on), "aria-labelledby": id, "data-fk": `tog:${key}`,
      onclick: () => this._settings(zone, settings, false, `${label}: ${on ? t.offW : t.onW}`) }), key, " inline");

    toggle(t.auto, "auto", z.auto, z, { auto: !z.auto });
    if (this.entry && "optimize" in this.entry) toggle(t.optimizeLbl, "optimize", this.entry.optimize, null, { optimize: !this.entry.optimize });
    if (!pct) stepper(t.comfort, "comfort", 0.5, Math.max(10, s.min_value ?? 10), Math.min(28, s.max_value ?? 28), (v) => this.fmtT(v, z));
    const ai = STRAT.reduce((b, v, i) => (Math.abs(v - s.price_aggressiveness) < Math.abs(STRAT[b] - s.price_aggressiveness) ? i : b), 0);
    row(t.strategy, (id) => el("div", { class: "seg", role: "group", "aria-labelledby": id },
      t.strategies.map((name, i) => el("button", { "aria-pressed": String(i === ai), text: name, "data-fk": `strat:${i}`,
        onclick: () => this._settings(z, { price_aggressiveness: STRAT[i] }, false, `${t.strategy}: ${name}`) }))), "strategy");
    stepper(t.shift, "price_shift", pct ? 5 : 0.5, 0, pct ? 60 : 5, (v) => `−${num(v)}${unit}`);
    stepper(t.preheatS, "preheat", pct ? 5 : 0.5, 0, pct ? 40 : 4, (v) => `+${num(v)}${unit}`);
    toggle(t.extremeOff, "extreme_off", s.extreme_off, z, { extreme_off: !s.extreme_off });
    if (s.extreme_off && this.entry?.rules) {
      const r = this.entry.rules;
      const setX = (nv) => this._settings(null, { extreme_abs_price: nv }, false, `${t.extremeAbs}: ${this.fmtPrice(nv)}`);
      row(t.extremeAbs, (id) => el("div", { class: "step", role: "group", "aria-labelledby": id },
        el("button", { class: "pm", text: "−", "aria-label": t.dec(t.extremeAbs), "data-fk": "set:xabs:-", "aria-disabled": r.extreme_abs_price <= 0.5 ? "true" : undefined,
          onclick: () => { if (r.extreme_abs_price > 0.5) setX(Math.max(0.5, +(r.extreme_abs_price - 0.25).toFixed(2))); } }),
        el("span", { class: "val", text: this.fmtPrice(r.extreme_abs_price) }),
        el("button", { class: "pm", text: "+", "aria-label": t.inc(t.extremeAbs), "data-fk": "set:xabs:+",
          onclick: () => setX(+(r.extreme_abs_price + 0.25).toFixed(2)) })), "xabs");
      if (!pct) stepper(t.safety, "safety_min_temp", 0.5, 5, 25, (v) => this.fmtT(v, z));
    }
    stepper(t.away, "away_offset", pct ? 5 : 0.5, pct ? -80 : -10, 0, (v) => `${num(v)}${unit}`);
    this._warmFrom(z, row, grid);
    stepper(t.protectFrom, "protected_from_hour", 1, 0, 23, (v) => this.fmtClock(v));
    stepper(t.protectTo, "protected_to_hour", 1, 1, 24, (v) => this.fmtClock(v % 24));
    const notes = [];
    const th = z.thermal || {};
    if (!pct && z.thermal) notes.push(t.model(this.fmtNum(th.gross_rate, 1), this.nf({ maximumFractionDigits: 0 }).format(Math.round(th.tau_hours * Math.LN2)), th.learned));
    const q = t.quality[th.quality] || th.quality || null;
    if (!pct && typeof th.rmse === "number") notes.push(t.fit(this.fmtNum(th.rmse, 1), q));
    else if (q) notes.push(t.fitQ(q));
    if (!pct && (th.solar_gain ?? th.solar) > 0) notes.push(t.solar);
    const lag = th.lag_hours ?? th.lag;
    if (!pct && typeof lag === "number" && lag >= 0.25) notes.push(t.lag(this.fmtNum(lag, lag < 2 ? 1 : 0)));
    if (z.heater_kw) notes.push(t.power(this.nf({ maximumFractionDigits: 2 }).format(z.heater_kw), z.heater_kw_source));
    if (notes.length) grid.append(el("p", { class: "model", text: notes.join(" ") }));
    d.append(grid);
    return d;
  }

  _warmFrom(z, row, grid) {
    // "Warm again from": a fixed hour, or Auto (-1) = learned from when you usually get home.
    const t = this.t;
    const v = z.settings.away_until_hour;
    const hasAuto = !!this.entry && "arrival" in this.entry;
    const arr = this.entry?.arrival || null;
    const auto = v === -1 || v === "auto";
    const learned = typeof arr?.warm_from === "number" ? arr.warm_from : null;
    const base = auto ? (learned ?? 13) : v;
    const label = auto ? (learned !== null ? t.autoAt(this.fmtClock(learned)) : t.autoLbl) : this.fmtClock(v);
    const set = (nv) => this._settings(z, { away_until_hour: nv }, false, `${t.warmFrom} ${nv === -1 ? t.autoLbl : this.fmtClock(nv)}`);
    row(t.warmFrom, (id) => el("div", { class: "step", role: "group", "aria-labelledby": id },
      hasAuto || auto ? el("button", { class: "chip", "aria-pressed": String(auto), text: t.autoLbl, "data-fk": "set:warm:auto",
        onclick: () => set(auto ? base : -1) }) : null,
      el("button", { class: "pm", "aria-label": t.dec(t.warmFrom), text: "−", "data-fk": "set:warm:-", "aria-disabled": base <= 0 ? "true" : undefined, onclick: () => { if (base > 0) set(base - 1); } }),
      el("span", { class: "val", text: label }),
      el("button", { class: "pm", "aria-label": t.inc(t.warmFrom), text: "+", "data-fk": "set:warm:+", "aria-disabled": base >= 23 ? "true" : undefined, onclick: () => { if (base < 23) set(base + 1); } })), "warm");
    if (arr?.expected) grid.append(el("p", { class: "model", text: t.arrival(this._clockText(arr.expected)) }));
    else if (auto) grid.append(el("p", { class: "model", text: t.arrivalLearning }));
  }
  /** "15:20" from the server → the user's clock format. */
  _clockText(hhmm) {
    const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm));
    if (!m || !this.h12) return hhmm;
    return this._clockFmt(true).format(Date.UTC(2000, 0, 1, +m[1], +m[2]));
  }
}

customElements.define("heat-planner-card", HeatPlannerCard);

/* ------------------------------------------------------------------ visual editor */
class HeatPlannerCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._entries = null;
  }
  setConfig(config) { this._config = { ...(config || {}) }; this._render(); }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) { this._load(); this._ensureForm(); }
    if (this._form) this._form.hass = hass;
  }
  get t() { return I18N[langOf(this._hass)]; }

  async _load() {
    try {
      const r = await this._hass.connection.sendMessagePromise({ type: "heat_planner/get" });
      this._entries = r?.entries || [];
    } catch { this._entries = []; }
    this._render();
  }
  async _ensureForm() {
    if (customElements.get("ha-form")) return;
    // ha-form is lazy-loaded by Home Assistant; asking a built-in card for its editor loads it.
    try {
      const helpers = await window.loadCardHelpers?.();
      const c = helpers ? await helpers.createCardElement({ type: "entities", entities: [] }) : null;
      await c?.constructor?.getConfigElement?.();
    } catch { /* fall back below */ }
    const ok = await Promise.race([customElements.whenDefined("ha-form").then(() => true), new Promise((r) => setTimeout(() => r(false), 3000))]);
    if (!ok) this._noForm = true;
    this._render();
  }
  _schema() {
    const t = this.t;
    const entries = this._entries || [];
    const entry = entries.find((e) => e.entry_id === this._config.entry_id) || entries[0];
    const schema = [];
    if (entries.length > 1) schema.push({ name: "entry_id", selector: { select: { mode: "dropdown", options: entries.map((e) => ({ value: e.entry_id, label: e.title || e.entry_id })) } } });
    if (entry?.zones?.length) schema.push({ name: "zones", selector: { select: { multiple: true, mode: "list", options: entry.zones.map((z) => ({ value: z.zone_id, label: z.name })) } } });
    schema.push({ name: "hours", selector: { number: { min: 12, max: 48, step: 1, mode: "slider", unit_of_measurement: t.hUnit } } });
    schema.push({ name: "show_help", selector: { boolean: {} } });
    return schema;
  }
  _data() {
    return { hours: DEFAULT_HOURS, show_help: true, zones: [], ...this._config, ...(this._entries?.length > 1 && !this._config.entry_id ? { entry_id: this._entries[0].entry_id } : {}) };
  }
  _changed(value) {
    const prevEntry = this._config.entry_id || this._entries?.[0]?.entry_id;
    const cfg = { ...this._config, ...value };
    if (cfg.entry_id && cfg.entry_id !== prevEntry) delete cfg.zones; // rooms belong to one setup
    if (!cfg.zones?.length) delete cfg.zones;
    if (cfg.show_help === true) delete cfg.show_help;
    if (cfg.entry_id && (this._entries || []).length <= 1) delete cfg.entry_id;
    const h = Math.round(Number(cfg.hours));
    cfg.hours = Number.isFinite(h) ? Math.max(12, Math.min(48, h)) : DEFAULT_HOURS;
    this._config = cfg;
    this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: cfg }, bubbles: true, composed: true }));
  }
  _render() {
    if (!this._hass) return;
    const t = this.t;
    const root = this.shadowRoot;
    const labels = { entry_id: t.edEntry, zones: t.edZones, hours: t.edHours, show_help: t.edHelp };
    const helpers = { zones: t.edZonesHelp };
    const note = this._entries === null ? t.edLoading : this._entries.length === 0 ? t.edNoData : null;
    if (!root.querySelector("style")) root.append(el("style", { text: ".note{color:var(--secondary-text-color);font-size:14px;margin:0 0 12px} .fb{display:grid;gap:14px} .fb label{display:flex;gap:10px;align-items:center;min-height:44px} .fb fieldset{border:0;padding:0;margin:0} .fb legend{font-weight:500;margin-bottom:4px}" }));
    root.querySelector(".note")?.remove();
    if (note) root.prepend(...[root.querySelector("style"), el("p", { class: "note", text: note })].filter(Boolean));
    if (customElements.get("ha-form")) {
      if (!this._form) {
        this._form = document.createElement("ha-form");
        this._form.addEventListener("value-changed", (ev) => { ev.stopPropagation(); this._changed(ev.detail.value); });
        root.querySelector(".fb")?.remove();
        root.append(this._form);
      }
      this._form.hass = this._hass;
      this._form.schema = this._schema();
      this._form.data = this._data();
      this._form.computeLabel = (s) => labels[s.name] || s.name;
      this._form.computeHelper = (s) => helpers[s.name];
      return;
    }
    if (!this._noForm) return;
    // Fallback without ha-form: plain, accessible controls.
    root.querySelector(".fb")?.remove();
    const d = this._data();
    const f = el("div", { class: "fb" });
    for (const s of this._schema()) {
      if (s.name === "zones" || s.name === "entry_id") {
        const opts = s.selector.select.options;
        const fs = el("fieldset", {}, el("legend", { text: labels[s.name] }));
        if (s.name === "zones") {
          fs.append(...opts.map((o) => el("label", {}, el("input", { type: "checkbox", checked: d.zones.includes(o.value) || undefined,
            onchange: (ev) => this._changed({ zones: ev.target.checked ? [...(this._config.zones || []), o.value] : (this._config.zones || []).filter((x) => x !== o.value) }) }), o.label)));
          fs.append(el("p", { class: "note", text: helpers.zones }));
        } else {
          const sel = el("select", { "aria-label": labels.entry_id, onchange: (ev) => this._changed({ entry_id: ev.target.value }) },
            opts.map((o) => el("option", { value: o.value, selected: o.value === d.entry_id || undefined, text: o.label })));
          fs.append(sel);
        }
        f.append(fs);
      } else if (s.name === "hours") {
        f.append(el("label", {}, labels.hours, el("input", { type: "number", min: 12, max: 48, step: 1, value: d.hours, onchange: (ev) => this._changed({ hours: +ev.target.value }) })));
      } else if (s.name === "show_help") {
        f.append(el("label", {}, el("input", { type: "checkbox", checked: d.show_help || undefined, onchange: (ev) => this._changed({ show_help: ev.target.checked }) }), labels.show_help));
      }
    }
    root.append(f);
  }
}
customElements.define("heat-planner-card-editor", HeatPlannerCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "heat-planner-card",
  name: "Heat Planner",
  description: "Heating plan with electricity prices, expected room temperature and a drag-to-edit day curve.",
  preview: false,
  documentationURL: "https://github.com/aridder/ha-heat-planner",
});
console.info(`%c HEAT-PLANNER-CARD %c ${VERSION} `, "background:#E9A23B;color:#000", "");
