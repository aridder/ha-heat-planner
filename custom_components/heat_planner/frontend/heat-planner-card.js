/* Heat Planner card – price & presence aware heating schedules.
 * No build step, no dependencies. Talks to the heat_planner websocket API.
 */
const VERSION = "0.4.1";
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
  --hp-comfort:#E9A23B; --hp-normal:color-mix(in srgb, var(--primary-text-color) 45%, transparent); --hp-setback:#5B6CD9; --hp-preheat:#F0644B; --hp-save:#1FA896; --hp-off:#8C96A8;
  --hp-cheap:#3FA971; --hp-pnormal:color-mix(in srgb, var(--secondary-text-color) 35%, transparent); --hp-exp:color-mix(in srgb, var(--primary-text-color) 62%, transparent); --hp-ext:#E0464E;
  --hp-ink: var(--primary-text-color); --hp-soft: var(--secondary-text-color); --hp-line: var(--divider-color, rgba(127,127,127,.25));
  --hp-bg: var(--ha-card-background, var(--card-background-color, #fff));
  --hp-track: color-mix(in srgb, var(--secondary-text-color) 55%, transparent);
  --hp-warn: var(--warning-color, #E8A33B); --hp-err: var(--error-color, #DB4437);
}
ha-card { padding:18px 18px 14px; overflow:hidden; container-type:inline-size; }
.sr-only { position:absolute !important; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0; }
.zones { display:flex; gap:18px; margin:0 0 12px; overflow-x:auto; scrollbar-width:none; scroll-snap-type:x proximity; }
.zones::-webkit-scrollbar { display:none; }
.zones button { all:unset; cursor:pointer; color:var(--hp-soft); font-size:14px; padding:2px 0 6px; border-bottom:2px solid transparent; white-space:nowrap; scroll-snap-align:start; flex:none; box-sizing:border-box; }
.zones button[aria-selected=true] { color:var(--hp-ink); border-color:var(--hp-ink); font-weight:500; }
.zones button .warn { display:inline-block; margin-left:6px; width:16px; height:16px; line-height:16px; text-align:center; border-radius:50%; font-size:11px; font-weight:700; background:var(--hp-warn); color:#000; }
:focus-visible { outline:2px solid var(--primary-color, #03a9f4); outline-offset:2px; }
button:focus-visible, a:focus-visible, summary:focus-visible { border-radius:6px; }
.chartwrap:focus-visible { outline-offset:4px; border-radius:6px; }
.story { font-size:clamp(17px, 4.6cqi, 21px); line-height:1.35; color:var(--hp-ink); margin:0 0 4px; max-width:62ch; font-weight:400; letter-spacing:-0.005em; }
.story .temp { font-variant-numeric: tabular-nums; font-weight:600; }
.badge { display:inline-block; font-size:12px; padding:1px 8px; border-radius:999px; background:color-mix(in srgb, var(--hp-ink) 9%, transparent); color:var(--hp-ink); margin-left:6px; vertical-align:2px; }
.chartwrap { position:relative; user-select:none; -webkit-user-select:none; touch-action:pan-y; }
.chartwrap.editing { touch-action:none; }
svg { display:block; width:100%; overflow:visible; }
.axis text { fill:var(--hp-soft); font-size:10.5px; font-variant-numeric: tabular-nums; }
.grid line { stroke:var(--hp-line); stroke-width:1; }
.now line { stroke:var(--hp-ink); stroke-width:1; stroke-dasharray:2 3; opacity:.55; }
.now text { fill:var(--hp-soft); font-size:10.5px; }
.offt { fill:var(--hp-soft); font-size:10px; }
.pred { fill:none; stroke:var(--hp-ink); stroke-width:1.5; stroke-dasharray:4 4; opacity:.65; }
.sched { fill:none; stroke:var(--hp-ink); stroke-width:1; opacity:.35; }
.handle { fill:var(--hp-bg); stroke:var(--hp-ink); stroke-width:1.5; }
.handle.dirty { fill:var(--hp-ink); }
.handle.focus { stroke-width:2.5; stroke:var(--primary-color, #03a9f4); }
.tip { position:absolute; pointer-events:none; background:var(--hp-bg); color:var(--hp-ink); border:1px solid var(--hp-line);
  border-radius:10px; padding:8px 10px; font-size:12.5px; line-height:1.45; min-width:150px; max-width:min(280px, 100%); box-sizing:border-box; box-shadow:0 6px 24px rgba(0,0,0,.14); z-index:2; }
.tip b { font-weight:600; font-variant-numeric:tabular-nums; }
.tip .r { color:var(--hp-soft); }
.legend { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--hp-soft); margin:8px 0 2px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
.legend .dash { width:16px; height:0; border-top:1.5px dashed var(--hp-ink); border-radius:0; opacity:.65; vertical-align:3px; }
.legend i.hatch-off { background:repeating-linear-gradient(135deg, var(--hp-off) 0 1.6px, transparent 1.6px 3.6px); box-shadow:inset 0 2px 0 var(--hp-off); }
.legend i.hatch-ext { background:repeating-linear-gradient(135deg, var(--hp-ext) 0 2px, color-mix(in srgb, var(--hp-ext) 40%, var(--hp-bg)) 2px 4px); }
.row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.bar { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin-top:12px; }
.cost { color:var(--hp-soft); font-size:13px; }
.btn { all:unset; box-sizing:border-box; cursor:pointer; font-size:13px; padding:7px 14px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-ink); display:inline-flex; align-items:center; justify-content:center; text-align:center; }
.btn.ghost { border-color:transparent; color:var(--hp-soft); }
.btn.ghost:hover { color:var(--hp-ink); }
.btn.primary { background:var(--hp-ink); color:var(--hp-bg); border-color:var(--hp-ink); }
.btn:disabled, .btn[aria-disabled=true] { opacity:.4; cursor:default; }
.helpwrap { margin-top:18px; padding-top:6px; border-top:1px solid var(--hp-line); }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { all:unset; box-sizing:border-box; cursor:pointer; font-size:12.5px; padding:5px 11px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); display:inline-flex; align-items:center; }
.chip[aria-pressed=true] { color:var(--hp-ink); border-color:var(--hp-ink); }
.editor { margin-top:12px; padding-top:12px; border-top:1px solid var(--hp-line); }
.hint { color:var(--hp-soft); font-size:12.5px; margin:6px 0 10px; }
details.settings { margin-top:14px; border-top:1px solid var(--hp-line); padding-top:10px; }
summary { cursor:pointer; color:var(--hp-soft); font-size:13px; list-style:none; display:inline-flex; align-items:center; }
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
.fb { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin:6px 0 12px; }
.fb .chip { font-size:12px; padding:4px 10px; }
.fb .chip:hover:not([disabled]) { color:var(--hp-ink); border-color:var(--hp-ink); }
.fb .chip[disabled] { opacity:.5; cursor:default; }
.fb .chip i { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; }
.cost.flash { color:var(--hp-ink); }
.tariff { color:var(--hp-soft); font-size:12px; margin:6px 0 0; font-variant-numeric:tabular-nums; }
.tariff b { color:var(--hp-ink); font-weight:500; }
.loadrow { margin-top:2px; }
.loadrow .kwbar { fill:var(--hp-normal); opacity:.55; }
.loadrow .kwbar.over { fill:var(--hp-ext); opacity:.85; }
.loadrow .kwbar.live { opacity:.8; }
.loadrow .cap { stroke:var(--hp-ext); stroke-width:1; stroke-dasharray:3 3; opacity:.8; }
.loadrow text { fill:var(--hp-soft); font-size:10px; }
.rangebar { display:flex; justify-content:flex-end; margin:2px 0 6px; }
.seg.range { flex-wrap:nowrap; }
.seg.range button { padding:4px 11px; white-space:nowrap; }
.act { fill:none; stroke:var(--hp-ink); stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
.actdot { fill:var(--hp-ink); }
.past .pbar { opacity:.45; }
.mkr .ring { fill:var(--hp-bg); stroke:var(--hp-ink); stroke-width:1.4; }
.mkr .tri { fill:var(--hp-ext); }
.mkr .sq { fill:var(--hp-off); }
.legend i.solid { width:16px; height:0; border-top:2px solid var(--hp-ink); border-radius:0; vertical-align:3px; }
.legend svg { display:inline-block; width:10px; height:10px; vertical-align:-1px; margin-right:5px; overflow:visible; }
.summary { color:var(--hp-soft); font-size:13px; line-height:1.5; font-variant-numeric:tabular-nums; }
.summary b { color:var(--hp-ink); font-weight:500; }
.loadrow .capstep { fill:none; stroke:var(--hp-ext); stroke-width:1; stroke-dasharray:3 3; opacity:.8; }
.sk.hist { height:170px; margin:0 0 12px; }
.step .chip { font-size:12px; padding:3px 9px; margin-right:4px; }
.notice { display:flex; gap:10px; align-items:flex-start; justify-content:space-between; font-size:13px; line-height:1.45; padding:8px 10px 8px 12px; border-radius:10px; margin:0 0 10px;
  color:var(--hp-ink); background:color-mix(in srgb, var(--hp-warn) 12%, transparent); border-left:3px solid var(--hp-warn); }
.notice.err { background:color-mix(in srgb, var(--hp-err) 10%, transparent); border-left-color:var(--hp-err); }
.notice .btn { padding:2px 10px; font-size:12px; flex:none; }
.state { padding:6px 2px 4px; }
.state h2 { font-size:17px; font-weight:600; margin:0 0 6px; color:var(--hp-ink); }
.state p { color:var(--hp-soft); font-size:14px; line-height:1.45; margin:0 0 14px; max-width:60ch; }
.sk { background:color-mix(in srgb, var(--hp-ink) 8%, transparent); border-radius:8px; }
.sk.line { height:14px; margin:0 0 10px; } .sk.story { height:24px; margin:0 0 10px; } .sk.chart { height:170px; margin:16px 0 12px; }
@media (prefers-reduced-motion: no-preference) { .sk { animation:hp-pulse 1.6s ease-in-out infinite; } }
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
  .step .pm { width:44px; height:44px; }
  .tog { width:52px; height:44px; }
  summary { min-height:44px; }
  .srow:not(.inline) { grid-template-columns:minmax(0,1fr); }
  .srow .val { text-align:left; min-width:64px; }
  .seg { display:grid; grid-template-columns:1fr 1fr; border-radius:14px; }
  .seg.range { display:flex; width:100%; border-radius:999px; }
  .seg.range button { flex:1 1 0; padding:4px 6px; }
  .rangebar { justify-content:stretch; }
  .zones { gap:14px; margin:0 -12px 10px; padding:0 12px; }
}
`;

const SVG_TAGS = new Set(["svg", "g", "path", "line", "rect", "text", "circle", "pattern", "defs", "clipPath", "title"]);
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
    const chart = Math.round(Math.min(260, Math.max(170, W * 0.36)));
    const px = (this.zones.length > 1 ? 40 : 0) + 64 + 40 + chart + 40 + 50 + 40 + (this._open ? 360 : 0) + (this._edit ? 120 : 0);
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
    clearInterval(this._tick);
    // moves the "now" marker and notices stale data; renders only if something visible changed
    this._tick = setInterval(() => this._update(), 60e3);
  }

  disconnectedCallback() {
    this._ro?.disconnect(); this._ro = null;
    cancelAnimationFrame(this._roRaf); this._roRaf = null;
    document.removeEventListener("visibilitychange", this._onVisible);
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
    const cur = this.entry?.currency || "";
    return `${this.fmtNum(v, 2)} ${cur}/kWh`.trim();
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
  _summary(z) {
    const t = this.t, pct = z.mode === "percent";
    const rows = (this._series_ || []).filter((s) => !s.partial && !s.empty);
    const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
    const parts = [];
    if (!pct) {
      const withT = rows.filter((s) => s.temp != null), both = withT.filter((s) => s.target != null);
      if (withT.length) {
        const avg = this.fmtT(mean(withT.map((s) => s.temp)), z);
        parts.push(both.length ? t.sumAvg(avg, this.fmtT(mean(both.map((s) => s.target)), z)) : `${t.actual} ${avg}`);
        // below target: the hour's mean half a degree or more under (pre-heat hours aim above comfort, so they don't count)
        parts.push(t.sumBelow(both.filter((s) => s.temp < s.target - 0.5 && s.rawMode !== "preheat").length));
      }
    } else {
      const d = rows.filter((s) => s.target != null);
      if (d.length) parts.push(t.sumAvgPct(this.fmtT(mean(d.map((s) => s.target)), z)));
    }
    const kw = rows.filter((s) => s.kwh != null);
    if (kw.length) {
      const cost = rows.filter((s) => s.cost != null);
      parts.push(t.sumEnergy(this.fmtNum(kw.reduce((a, s) => a + s.kwh, 0), 1), cost.length ? this.fmtMoney(cost.reduce((a, s) => a + s.cost, 0)) : null));
    }
    const done = new Set(rows.map((s) => s.start));
    const home = (this._histData()?.home || []).filter((h) => done.has(h.start) && typeof h.house_kwh === "number");
    if (home.length) {
      const top = home.reduce((a, h) => (h.house_kwh > a.house_kwh ? h : a));
      const caps = home.map((h) => h.cap_kw).filter((c) => typeof c === "number");
      const cap = typeof top.cap_kw === "number" ? top.cap_kw : caps.length ? Math.max(...caps) : null;
      parts.push(t.sumTop(this.fmtNum(top.house_kwh, 1), cap != null ? this.fmtNum(cap, 1) : null) + (cap != null && top.house_kwh > cap ? t.sumOver : ""));
    }
    const label = this._range === "48" ? t.r48 : t.r24;
    if (!parts.length) return el("p", { class: "summary", text: `${label}: ${t.sumNone}` });
    const txt = parts.join(" · ");
    return el("p", { class: "summary" }, el("b", { text: `${label}: ` }), txt.charAt(0).toUpperCase() + txt.slice(1) + ".");
  }
  _pastLoadRow() {
    const data = this._histData(), series = this._series_ || [];
    if (!data?.home || !this._geo || !series.length) return null;
    const byStart = new Map(data.home.map((h) => [h.start, h]));
    const rows = series.map((s) => byStart.get(s.start) || null);
    const kwh = (r) => (r && typeof r.house_kwh === "number" ? r.house_kwh : null);
    if (!rows.some((r) => kwh(r) !== null)) return null;
    const caps = rows.map((r) => (r && typeof r.cap_kw === "number" ? r.cap_kw : null));
    const { X, bw, W, m } = this._geo;
    const H = 30, top = 4, base = H - 4;
    const max = Math.max(0.5, ...rows.map((r) => kwh(r) ?? 0), ...caps.map((c) => (c ?? 0) * 1.15));
    const Yk = (v) => base - (v / max) * (base - top);
    const capVals = caps.filter((c) => c !== null);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, class: "loadrow", role: "img",
      "aria-label": `${this.t.homeLoad}${capVals.length ? ` · ${this.t.capToday(this.fmtNum(Math.max(...capVals), 1))}` : ""}` });
    rows.forEach((r, i) => {
      const v = kwh(r);
      if (v === null) return;
      const y = Yk(v);
      svg.append(el("rect", { class: `kwbar${caps[i] !== null && v > caps[i] ? " over" : ""}${r.live ? " live" : ""}`, x: X(i) + bw * 0.22, y, width: bw * 0.56, height: Math.max(1, base - y), rx: Math.min(1.5, bw * 0.2) }));
    });
    let d = "";
    caps.forEach((c, i) => { if (c === null) return; d += `${i && caps[i - 1] !== null ? "L" : "M"}${X(i)},${Yk(c)}L${X(i + 1)},${Yk(c)}`; });
    if (d) svg.append(el("path", { class: "capstep", d }));
    svg.append(el("text", { x: m.l - 6, y: Yk(capVals.length ? capVals[capVals.length - 1] : max) + 3.5, "text-anchor": "end", text: "kWh" }));
    return svg;
  }

  // ------------------------------------------------------------- story
  story(z) {
    const t = this.t;
    const hours = z.plan?.hours || [];
    const h0 = hours[0];
    const temp = (txt) => el("span", { class: "temp", text: txt });
    const parts = [];
    parts.push(z.room_temperature != null ? t.is(z.name, temp(this.fmtT(z.room_temperature, { mode: "setpoint" }))) : t.noTemp(z.name));
    let now = null;
    if (!z.auto) now = [...t.now.disabled(), t.dot];
    else if (z.manual_until) now = [...t.now.manual(this.fmtClock(+this.hh(z.manual_until))), t.dot];
    else if (h0) {
      const mode = h0.mode in t.now ? h0.mode : "normal";
      now = [...t.now[mode](temp(this.fmtT(h0.target, z)))];
      const nxt = hours.slice(1).find((h) => h.target !== h0.target);
      if (nxt) now.push(nxt.target === null ? t.nextOff(this.fmtClock(+this.hh(nxt.start))) : t.next(this.fmtT(nxt.target, z), this.fmtClock(+this.hh(nxt.start))));
      else now.push(t.dot);
    }
    if (now) parts.push(" ", now);
    const peakIdx = hours.findIndex((h) => h.level === "expensive" || h.level === "extreme");
    if (peakIdx >= 0) {
      let end = peakIdx;
      while (end + 1 < hours.length && ["expensive", "extreme"].includes(hours[end + 1].level)) end++;
      const max = Math.max(...hours.slice(peakIdx, end + 1).map((h) => h.price ?? 0));
      const endH = (+this.hh(hours[end].start) + 1) % 24;
      parts.push(t.peak(this.fmtClock(+this.hh(hours[peakIdx].start)), this.fmtClock(endH), this.fmtPrice(max + (this.entry?.price_extra || 0))));
    }
    return parts.flat(3);
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

  // ------------------------------------------------------------- tariff & peak
  _tariffLine() {
    const tr = this.entry?.tariff, pk = this.entry?.peak, t = this.t;
    const parts = [];
    if (tr?.label) parts.push(el("b", { text: tr.label }));
    if (pk) {
      if (pk.step_label) parts.push(t.step(pk.step_label));
      if (typeof pk.month_avg_kw === "number") parts.push(t.top3(this.fmtNum(pk.month_avg_kw, 1)));
      if (typeof pk.cap_kw === "number") parts.push(t.capToday(this.fmtNum(pk.cap_kw, 1)));
    }
    if (!parts.length) return null;
    const line = el("p", { class: "tariff" });
    parts.forEach((p, i) => { if (i) line.append(" · "); line.append(p); });
    if (pk && typeof pk.next_step_kw === "number") {
      const cost = typeof pk.step_cost === "number" ? this.fmtMoney(pk.step_cost) : "";
      let title = t.nextStep(this.fmtNum(pk.next_step_kw, 1), cost);
      if (Array.isArray(pk.top3_kw) && pk.top3_kw.length) title += ` · ${pk.top3_kw.map((v) => this.fmtNum(v, 1)).join(" / ")} kW`;
      line.title = title;
      line.append(el("span", { class: "sr-only", text: ` · ${title}` }));
    }
    return line;
  }

  _loadRow() {
    // Thin home-load row aligned with the chart (whole house: rest of the home + planned
    // heating, the current hour live from the meter), with the day's cap as a dashed line.
    if (this.past) return this._pastLoadRow();
    const pk = this.entry?.peak, cap = pk?.cap_kw;
    const series = this._series_ || [];
    if (this._edit || !pk || typeof cap !== "number" || !this._geo || !Array.isArray(pk.load)) return null;
    const key = (d) => this.dateKey(d) + "T" + this.hh(d);
    const byKey = new Map(pk.load.map((l) => [key(l.start), l]));
    const rows = series.map((s) => (s.start ? byKey.get(key(s.start)) : null) || null);
    if (!rows.some((r) => r && typeof r.kw === "number")) return null;
    const { X, bw, W, m } = this._geo;
    const H = 30, top = 4, base = H - 4;
    const max = Math.max(cap * 1.15, ...rows.map((r) => (r && typeof r.kw === "number" ? r.kw : 0)));
    const Yk = (v) => base - (v / max) * (base - top);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, class: "loadrow", role: "img",
      "aria-label": `${this.t.homeLoad} · ${this.t.capToday(this.fmtNum(cap, 1))}` });
    rows.forEach((r, i) => {
      if (!r || typeof r.kw !== "number") return;
      const y = Yk(r.kw);
      svg.append(el("rect", { class: `kwbar${r.kw > cap ? " over" : ""}${r.live ? " live" : ""}`, x: X(i) + bw * 0.22, y, width: bw * 0.56, height: Math.max(1, base - y), rx: Math.min(1.5, bw * 0.2) }));
    });
    svg.append(el("line", { class: "cap", x1: m.l, x2: W - m.r, y1: Yk(cap), y2: Yk(cap) }));
    svg.append(el("text", { x: m.l - 6, y: Yk(cap) + 3.5, "text-anchor": "end", text: "kW" }));
    return svg;
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

    const storyNodes = this.story(z);
    const story = el("p", { class: "story" }, storyNodes);
    if (z.manual_until) story.append(el("span", { class: "badge", text: t.manualBadge }));
    panel.append(story);
    const storyText = story.textContent;
    if (this._lastStory && this._lastStory.zone === z.zone_id && this._lastStory.text !== storyText) this._announce(storyText);
    this._lastStory = { zone: z.zone_id, text: storyText };
    if (this.hasFeedback(z)) panel.append(this._feedbackRow(z));

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
      const load = this._loadRow();
      if (load) panel.append(load);
      if (past && this._hist?.error && !this._hist.loading) panel.append(this._histPlaceholder());
    }
    const tariff = this._tariffLine();
    if (tariff) panel.append(tariff);
    panel.append(this._legend(z));

    if (this._edit) panel.append(this._editor(z));
    else {
      const showHelp = this._config.show_help !== false && customElements.get("heat-planner-help");
      panel.append(el("div", { class: "bar" },
        past && !this._flashFor(z) ? this._summary(z)
          : el("span", { class: `cost${this._flashFor(z) ? " flash" : ""}`, text: this._flashFor(z) ? this._flash.text : this.costLine(z) }),
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

  _legend(z) {
    const t = this.t;
    if (this.past) return this._pastLegend(z);
    const modes = new Set((this._edit?.preview || z.plan)?.hours?.map((h) => h.mode) || []);
    const items = [...modes].filter((m) => t.modes[m]).map((m) => el("span", {},
      el("i", m === "off" ? { class: "hatch-off", "aria-hidden": "true" } : { style: `background:${MODE_COLOR[m]}`, "aria-hidden": "true" }), t.modes[m]));
    if (z.room_temperature != null || z.plan?.hours?.some((h) => h.predicted != null))
      items.push(el("span", {}, el("i", { class: "dash", "aria-hidden": "true" }), t.predicted));
    const lv = new Set(this._series(z).map((s) => s.price != null && s.level).filter(Boolean));
    const pl = ["cheap", "expensive", "extreme"].filter((l) => lv.has(l));
    if (pl.length) items.push(el("span", {}, `${t.price}: `, pl.map((l) => el("span", { style: "margin-right:8px" },
      el("i", l === "extreme" ? { class: "hatch-ext", "aria-hidden": "true" } : { style: `background:${LEVEL_COLOR[l]}`, "aria-hidden": "true" }), t.levels[l]))));
    return el("div", { class: "legend" }, items);
  }

  _pastLegend(z) {
    const t = this.t, series = this._series_ || [], pct = z.mode === "percent";
    const items = [];
    const modes = new Set(series.filter((s) => !s.empty && s.target != null).map((s) => s.mode));
    if (series.some((s) => !s.empty && s.target == null)) modes.add("off");
    for (const m of modes) if (t.modes[m]) items.push(el("span", {},
      el("i", m === "off" ? { class: "hatch-off", "aria-hidden": "true" } : { style: `background:${MODE_COLOR[m]}`, "aria-hidden": "true" }), t.modes[m]));
    if (!pct && series.some((s) => s.temp != null)) items.push(el("span", {}, el("i", { class: "solid", "aria-hidden": "true" }), t.actual));
    if (pct && series.some((s) => s.heating != null)) items.push(el("span", {}, el("i", { class: "solid", "aria-hidden": "true" }), t.runtime));
    if (!pct && series.some((s) => s.predicted != null)) items.push(el("span", {}, el("i", { class: "dash", "aria-hidden": "true" }), t.predicted));
    const glyph = { manual: el("circle", { class: "ring", cx: 5, cy: 5, r: 3.6 }), shed: el("path", { class: "tri", d: "M1,1.5L9,1.5L5,9Z" }), off: el("rect", { class: "sq", x: 1.5, y: 1.5, width: 7, height: 7, rx: 1 }) };
    for (const k of ["manual", "shed", "off"]) if (series.some((s) => (s.flags || []).includes(k)))
      items.push(el("span", {}, el("svg", { viewBox: "0 0 10 10", class: "mkr", "aria-hidden": "true" }, glyph[k]), t.mk[k]));
    const lv = new Set(series.map((s) => s.price != null && s.level).filter(Boolean));
    const pl = ["cheap", "expensive", "extreme"].filter((l) => lv.has(l));
    if (pl.length) items.push(el("span", {}, `${t.price}: `, pl.map((l) => el("span", { style: "margin-right:8px" },
      el("i", l === "extreme" ? { class: "hatch-ext", "aria-hidden": "true" } : { style: `background:${LEVEL_COLOR[l]}`, "aria-hidden": "true" }), t.levels[l]))));
    return el("div", { class: "legend" }, items);
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

  _drawChart(wrap, z) {
    const series = this._series(z);
    this._series_ = series;
    if (!series.length) { wrap.replaceChildren(); this._geo = null; return; }
    const W = this._chartW();
    const H = Math.round(Math.min(260, Math.max(170, W * 0.36)));
    const P = 46; // price strip height
    const m = { l: 30, r: 6, t: 10, b: 20 };
    const ch = H - m.t - m.b - P - 10;
    const n = series.length;
    const bw = (W - m.l - m.r) / n;
    const X = (i) => m.l + i * bw;
    const percent = z.mode === "percent";
    let lo, hi;
    if (percent) { lo = 0; hi = 100; }
    else {
      const vals = series.flatMap((s) => [s.target, s.scheduled, s.predicted, s.temp]).filter((v) => v !== null && v !== undefined);
      if (z.room_temperature != null && !this.past) vals.push(z.room_temperature);
      if (!vals.length) vals.push(z.settings?.comfort ?? 21);
      lo = Math.floor(Math.min(...vals) - 1); hi = Math.ceil(Math.max(...vals) + 1);
      if (hi - lo < 6) { const c = (hi + lo) / 2; lo = Math.floor(c - 3); hi = Math.ceil(c + 3); }
    }
    if ((this._drag || this._kbEdit) && this._frozen) ({ lo, hi } = this._frozen);
    const Y = (v) => m.t + ch - ((v - lo) / (hi - lo)) * ch;
    this._geo = { X, Y, bw, lo, hi, m, ch, n, W, H, percent };
    const hatch = `${this._uid}-hatch`, offp = `${this._uid}-off`;

    const past = this.past;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, "aria-hidden": "true", focusable: "false", class: past ? "past" : null });
    svg.append(el("defs", {},
      el("pattern", { id: hatch, width: 4, height: 4, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
        el("rect", { width: 2, height: 4, fill: "var(--hp-bg)", opacity: 0.6 })),
      el("pattern", { id: offp, width: 3.6, height: 3.6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
        el("rect", { width: 1.6, height: 3.6, fill: "var(--hp-off)" }))));
    const grid = el("g", { class: "grid" }); const axis = el("g", { class: "axis" });
    const step = percent ? 25 : (hi - lo > 10 ? 4 : 2);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      grid.append(el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
      axis.append(el("text", { x: m.l - 6, y: Y(v) + 3.5, "text-anchor": "end", text: percent ? `${v}%` : `${v}°` }));
    }
    svg.append(grid);

    // ribbon: area under target, coloured by mode; "off" hours hatched and labelled (not colour alone)
    const ribbon = el("g");
    const floorY = m.t + ch;
    series.forEach((s, i) => {
      if (s.empty) return; // no record for this hour (look-back view)
      const color = MODE_COLOR[s.mode] || MODE_COLOR.normal;
      if (s.target === null || s.target === undefined) {
        ribbon.append(el("rect", { x: X(i) + 0.5, y: floorY - 12, width: Math.max(0, bw - 1), height: 12, fill: `url(#${offp})` }));
        ribbon.append(el("rect", { x: X(i) + 0.5, y: floorY - 13.5, width: Math.max(0, bw - 1), height: 3, rx: 1.5, fill: MODE_COLOR.off }));
        const first = i === 0 || series[i - 1].target != null;
        if (first) {
          let run = 1; while (i + run < n && series[i + run].target == null && !series[i + run].empty) run++;
          ribbon.append(el("text", { class: "offt", x: X(i) + (run * bw) / 2, y: floorY - 18, "text-anchor": "middle", text: this.t.offShort }));
        }
        return;
      }
      const yv = Y(s.target);
      if (s.mode !== "normal") ribbon.append(el("rect", { x: X(i) + 0.5, y: yv, width: Math.max(0, bw - 1), height: floorY - yv, fill: color, opacity: 0.14 }));
      ribbon.append(el("rect", { x: X(i) + 0.5, y: yv - 1.5, width: Math.max(0, bw - 1), height: 3, rx: 1.5, fill: color }));
    });
    svg.append(ribbon);

    if (this._edit || series.some((s) => s.target !== null && Math.abs((s.target ?? 0) - s.scheduled) > 0.01)) {
      let d = "";
      series.forEach((s, i) => { const y = Y(s.scheduled); d += `${i ? "L" : "M"}${X(i)},${y}L${X(i + 1)},${y}`; });
      svg.append(el("path", { class: "sched", d }));
    }

    if (!percent || series.some((s) => s.predicted != null)) {
      const pts = series.map((s, i) => (s.predicted == null ? null : [X(i) + bw, Y(s.predicted)]));
      const nf = this._nowFrac(series);
      if (z.room_temperature != null && !this._edit && !past && nf !== null) pts.unshift([X(0) + bw * nf, Y(z.room_temperature)]);
      let d = ""; let pen = false;
      pts.forEach((p) => { if (!p) { pen = false; return; } d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; pen = true; });
      if (d) svg.append(el("path", { class: "pred", d }));
    }

    // look-back: what was measured (room temperature; for percent rooms the heater's on-time)
    if (past) {
      const nkf = this.nowKey().frac;
      const pts = series.map((s, i) => {
        const v = percent ? (s.heating != null ? s.heating * 100 : null) : s.temp;
        return v == null ? null : [X(i) + (s.partial ? Math.max(0.1, nkf) / 2 : 0.5) * bw, Y(v)];
      });
      let d = "";
      pts.forEach((p, i) => {
        if (!p) return;
        d += `${pts[i - 1] ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
        if (!pts[i - 1] && !pts[i + 1]) svg.append(el("circle", { class: "actdot", cx: p[0], cy: p[1], r: 2.2 }));
      });
      if (d) svg.append(el("path", { class: "act", d }));
    }

    // price strip; extreme hours get a hatch on top of the colour
    const priced = series.filter((s) => s.price != null);
    if (priced.length) {
      const pmax = Math.max(...priced.map((s) => s.price)); const pmin = Math.min(0, ...priced.map((s) => s.price));
      const py0 = H - m.b; const ph = P - 6;
      const g = el("g");
      series.forEach((s, i) => {
        if (s.price == null) return;
        const hgt = Math.max(2, ((s.price - pmin) / (pmax - pmin || 1)) * ph);
        const r = { x: X(i) + bw * 0.18, y: py0 - hgt, width: bw * 0.64, height: hgt, rx: Math.min(2, bw * 0.2), class: past ? "pbar" : null };
        g.append(el("rect", { ...r, fill: LEVEL_COLOR[s.level] || LEVEL_COLOR.normal }));
        if (s.level === "extreme") g.append(el("rect", { ...r, fill: `url(#${hatch})` }));
      });
      svg.append(g);
    }

    // look-back markers between the chart and the price strip: ring = changed by hand,
    // triangle = set back for the capacity cap, square = heating off (shape, not colour alone)
    if (past) {
      const g = el("g", { class: "mkr" });
      const my = m.t + ch + 8;
      series.forEach((s, i) => {
        const kinds = ["manual", "shed", "off"].filter((k) => (s.flags || []).includes(k));
        const step = Math.min(9, bw / Math.max(1, kinds.length));
        kinds.forEach((k, j) => {
          const cx = X(i) + bw / 2 + (j - (kinds.length - 1) / 2) * step;
          if (k === "manual") g.append(el("circle", { class: "ring", cx, cy: my, r: 3.2 }));
          else if (k === "shed") g.append(el("path", { class: "tri", d: `M${cx - 3.6},${my - 3}L${cx + 3.6},${my - 3}L${cx},${my + 3.4}Z` }));
          else g.append(el("rect", { class: "sq", x: cx - 3, y: my - 3, width: 6, height: 6, rx: 1 }));
        });
      });
      svg.append(g);
    }

    // x axis labels + day separators: spacing from the label width (12 h labels are wider)
    const minPx = this.h12 ? 34 : 20;
    const every = [1, 2, 3, 4, 6, 12].find((k) => k * bw >= minPx) || 12;
    series.forEach((s, i) => {
      const hr = s.hour;
      if (hr === 0 && i > 0) {
        svg.append(el("line", { x1: X(i), x2: X(i), y1: m.t, y2: H - m.b, stroke: "var(--hp-line)" }));
        axis.append(el("text", { x: X(i) + 3, y: m.t + 10, text: this.weekdayShort(s.key) }));
      }
      if (hr % every === 0) axis.append(el("text", { x: X(i) + bw / 2, y: H - 5, "text-anchor": "middle", text: this.fmtAxis(hr) }));
    });
    svg.append(axis);

    const nk = this.nowKey();
    const idx = past ? series.findIndex((s) => s.partial) : series.findIndex((s) => s.key === nk.date && s.hour === nk.hour);
    if (idx >= 0 && !this._edit) {
      const x = X(idx) + bw * nk.frac;
      svg.append(el("g", { class: "now" }, el("line", { x1: x, x2: x, y1: m.t, y2: H - m.b }),
        el("text", { x: x + 4, y: m.t + 9, text: this.t.nowLabel })));
    }
    this._nowIdx = idx;

    if (this._edit) {
      const hg = el("g");
      series.forEach((s, i) => hg.append(el("circle", {
        class: `handle${this._edit.dirty.has(i) ? " dirty" : ""}${this._focusIdx === i && this._kbActive ? " focus" : ""}`,
        cx: X(i) + bw / 2, cy: Y(s.scheduled), r: Math.max(2.5, Math.min(5, bw / 3)),
      })));
      svg.append(hg);
    }

    const hl = el("rect", { class: "hl", x: 0, y: m.t, width: bw, height: H - m.t - m.b, fill: "color-mix(in srgb, var(--hp-ink) 7%, transparent)", opacity: 0 });
    svg.append(hl);
    this._hl = hl;
    const old = wrap.querySelector("svg");
    if (old) old.replaceWith(svg); else wrap.prepend(svg);
  }

  _bindChart(wrap, z) {
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
      this._kbActive = false;
      this._highlight(wrap, z, i);
    });
    wrap.addEventListener("pointerleave", () => { if (!this._drag && !this._kbActive) { this._hl?.setAttribute("opacity", 0); this._hideTip(); } });
    if (this._edit) {
      wrap.addEventListener("pointerdown", (ev) => {
        if (ev.button > 0 || !this._geo) return;
        ev.preventDefault();
        wrap.setPointerCapture?.(ev.pointerId);
        this._drag = true; this._hideTip();
        this._frozen = { lo: this._geo.lo, hi: this._geo.hi };
        this._paint(wrap, z, idxAt(ev), valAt(ev));
      });
      const end = () => { if (this._drag) { this._drag = false; this._frozen = null; this._update(true); this._preview(z); } };
      wrap.addEventListener("pointerup", end);
      wrap.addEventListener("pointercancel", end);
    }
    wrap.addEventListener("keydown", (ev) => this._chartKey(ev, wrap, z));
    wrap.addEventListener("focus", () => {
      if (!wrap.matches(":focus-visible")) return; // mouse clicks: no keyboard cursor
      this._kbActive = true;
      if (this._focusIdx == null || this._focusIdx >= (this._series_ || []).length) this._focusIdx = this._nowIdx >= 0 && !this._edit ? this._nowIdx : this._edit ? new Date().getHours() : 0;
      this._highlight(wrap, z, this._focusIdx);
      if (this._edit) this._markHandle(wrap);
    });
    wrap.addEventListener("blur", () => { this._kbActive = false; this._hl?.setAttribute("opacity", 0); this._hideTip(); this._markHandle(wrap); });
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
    const s = this._series_?.[i];
    if (!s || !this._geo) return;
    const { X, bw, W } = this._geo;
    this._hl?.setAttribute("x", X(i)); this._hl?.setAttribute("opacity", 1);
    this._showTip(wrap, s, z, (X(i) + bw / 2) / W);
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
        if (!this._frozen) this._frozen = { lo: this._geo.lo, hi: this._geo.hi };
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
        this._hl?.setAttribute("opacity", 0); this._hideTip();
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

  _pastTip(s, z) {
    const t = this.t, pct = z.mode === "percent";
    const row = (label, val, rest) => el("div", {}, `${label}: `, el("b", { text: val }), rest || null);
    const why = this._pastWhy(s);
    return el("div", { class: "tip", "aria-hidden": "true" },
      el("div", {}, el("b", { text: this._pastWhen(s) }), "  ",
        s.empty ? "–" : s.target == null ? t.modes.off : `${t.target} `, s.empty || s.target == null ? null : el("b", { text: this.fmtT(s.target, z) })),
      s.temp != null ? row(t.actual, this.fmtT(s.temp, { mode: "setpoint" })) : null,
      s.predicted != null ? row(t.predicted, this.fmtT(s.predicted, { mode: "setpoint" })) : null,
      pct && s.heating != null ? row(t.runtime, this.fmtT(s.heating * 100, z)) : null,
      s.price != null ? row(t.price, this.fmtPrice(s.price), ` (${t.levels[s.level] || s.level})`) : null,
      s.kwh != null ? el("div", {}, el("b", { text: `${this.fmtNum(s.kwh, 2)} kWh` }), s.cost != null ? ` ≈ ${this.fmtMoney(s.cost)}` : null) : null,
      why ? el("div", { class: "r", text: why }) : null);
  }

  _showTip(wrap, s, z, frac) {
    const t = this.t;
    this._hideTip();
    if (s.past) return this._placeTip(wrap, this._pastTip(s, z), frac);
    const extra = this.entry?.price_extra || 0;
    const tv = this._edit ? s.scheduled : s.target; // while editing: the value being painted
    const tip = el("div", { class: "tip", "aria-hidden": "true" },
      el("div", {}, el("b", { text: this._when(s) }), "  ",
        tv === null ? t.modes.off : `${t.target} `, tv === null ? null : el("b", { text: this.fmtT(tv, z) })),
      s.predicted != null ? el("div", {}, `${t.predicted}: `, el("b", { text: this.fmtT(s.predicted, { mode: "setpoint" }) })) : null,
      s.price != null ? el("div", {}, `${t.price}: `, el("b", { text: this.fmtPrice(s.price + extra) }), ` (${t.levels[s.level] || s.level})`) : null,
      el("div", { class: "r", text: this._reasonText(s) }));
    this._placeTip(wrap, tip, frac);
  }
  _placeTip(wrap, tip, frac) {
    wrap.append(tip);
    const w = wrap.clientWidth; const tw = tip.offsetWidth;
    tip.style.left = `${Math.max(0, Math.min(w - tw, frac * w - tw / 2))}px`;
    tip.style.top = `-8px`;
    tip.style.transform = "translateY(-100%)";
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
