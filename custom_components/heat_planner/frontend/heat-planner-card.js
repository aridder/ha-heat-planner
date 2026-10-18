/* Heat Planner card – price & presence aware heating schedules.
 * No build step, no dependencies. Talks to the heat_planner websocket API.
 */
const VERSION = "0.1.0";

const I18N = {
  en: {
    loading: "Loading heat plan…",
    none: "No Heat Planner zones yet. Add one under Settings → Devices & services → Heat Planner → Configure.",
    is: (n, t) => `${n} is ${t}.`,
    noTemp: (n) => `${n}.`,
    now: {
      comfort: (t) => `Holding ${t} now`, normal: (t) => `Holding ${t} now`, setback: (t) => `Lowered to ${t} now`,
      preheat: (t) => `Pre-heating to ${t} now`, save: (t) => `Saving at ${t} through an expensive hour`,
      off: () => `Off now – electricity is extremely expensive`, away: (t) => `Nobody home – ${t}`,
      manual: (h) => `Changed by hand – back on plan at ${h}`, disabled: () => `Automatic control is off`,
    },
    next: (t, h) => `, ${t} from ${h}.`,
    nextOff: (h) => `, off from ${h}.`,
    dot: ".",
    peak: (a, b, p) => ` Next price peak ${a}–${b} (up to ${p}).`,
    cost: (c, s) => `Next 24 h about ${c}` + (s > 0.005 ? `, ${s} less than without price control.` : "."),
    target: "Target", predicted: "Expected room temperature", price: "Price",
    modes: { comfort: "Comfort", normal: "Normal", setback: "Lowered", preheat: "Pre-heat", save: "Saving", off: "Off", away: "Away" },
    levels: { cheap: "cheap", normal: "normal", expensive: "expensive", extreme: "extreme" },
    reasons: {
      schedule: "Schedule", override: "Changed for this day", away: "Nobody home", expensive: "One of the most expensive hours",
      preheat: "Cheap hour before expensive hours", preheat_extreme: "Storing heat before an extreme price",
      extreme_off: "Extreme price – heating off", protected: "Warm-up window, price ignored",
    },
    nowLabel: "now", edit: "Edit day", done: "Done", today: "Today", tomorrow: "Tomorrow",
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    daysLong: ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"],
    dragHint: "Drag across the hours to paint a new curve.",
    applyTo: "Save for", onlyDay: (d) => `Only ${d}`, every: (d) => `Every ${d}`, weekdays: "Weekdays", weekend: "Weekend", all: "All days",
    discard: "Discard", save: "Save", saved: "Saved", resetDay: "Back to schedule", overrideNote: "This day has its own curve.",
    settings: "Settings", auto: "Automatic control", comfort: "Comfort temperature",
    strategy: "Price strategy", strategies: ["Ignore", "Gentle", "Balanced", "Aggressive"],
    shift: "Lower in expensive hours", preheatS: "Raise before expensive hours", extremeOff: "Turn off at extreme prices",
    extremeAbs: "Extreme price from", safety: "Never cut off below", away: "Lower when nobody is home", warmFrom: "Warm again from",
    protectFrom: "No price saving from", protectTo: "No price saving until", model: (g, tau, l) => `Room model: heats ${g}°/h, loses half its warmth in ${tau} h${l ? " (learned)" : " (default)"}.`,
    resume: "Resume plan", manualBadge: "Manual",
  },
  nb: {
    loading: "Henter varmeplan …",
    none: "Ingen soner ennå. Legg til en under Innstillinger → Enheter og tjenester → Heat Planner → Konfigurer.",
    is: (n, t) => `${n} er ${t}.`,
    noTemp: (n) => `${n}.`,
    now: {
      comfort: (t) => `Holder ${t} nå`, normal: (t) => `Holder ${t} nå`, setback: (t) => `Senket til ${t} nå`,
      preheat: (t) => `Forvarmer til ${t} nå`, save: (t) => `Sparer med ${t} i en dyr time`,
      off: () => `Av nå – strømmen er ekstremt dyr`, away: (t) => `Ingen hjemme – ${t}`,
      manual: (h) => `Endret for hånd – tilbake på planen kl. ${h}`, disabled: () => `Automatisk styring er av`,
    },
    next: (t, h) => `, ${t} fra kl. ${h}.`,
    nextOff: (h) => `, av fra kl. ${h}.`,
    dot: ".",
    peak: (a, b, p) => ` Neste pristopp ${a}–${b} (opptil ${p}).`,
    cost: (c, s) => `Neste 24 timer koster omtrent ${c}` + (s > 0.005 ? `, ${s} mindre enn uten prisstyring.` : "."),
    target: "Mål", predicted: "Forventet romtemperatur", price: "Pris",
    modes: { comfort: "Komfort", normal: "Normal", setback: "Senket", preheat: "Forvarmer", save: "Sparer", off: "Av", away: "Borte" },
    levels: { cheap: "billig", normal: "normal", expensive: "dyr", extreme: "ekstrem" },
    reasons: {
      schedule: "Tidsplan", override: "Endret for denne dagen", away: "Ingen hjemme", expensive: "En av de dyreste timene",
      preheat: "Billig time før dyre timer", preheat_extreme: "Lagrer varme før ekstrempris",
      extreme_off: "Ekstrempris – varmen er av", protected: "Oppvarmingsvindu, prisen ignoreres",
    },
    nowLabel: "nå", edit: "Endre døgn", done: "Ferdig", today: "I dag", tomorrow: "I morgen",
    days: ["man", "tir", "ons", "tor", "fre", "lør", "søn"],
    daysLong: ["mandager", "tirsdager", "onsdager", "torsdager", "fredager", "lørdager", "søndager"],
    dragHint: "Dra over timene for å tegne en ny kurve.",
    applyTo: "Lagre for", onlyDay: (d) => `Bare ${d}`, every: (d) => `Alle ${d}`, weekdays: "Hverdager", weekend: "Helg", all: "Alle dager",
    discard: "Forkast", save: "Lagre", saved: "Lagret", resetDay: "Tilbake til fast plan", overrideNote: "Denne dagen har egen kurve.",
    settings: "Innstillinger", auto: "Automatisk styring", comfort: "Komforttemperatur",
    strategy: "Prisstrategi", strategies: ["Ignorer", "Forsiktig", "Balansert", "Aggressiv"],
    shift: "Senk i dyre timer", preheatS: "Hev før dyre timer", extremeOff: "Slå av ved ekstrempris",
    extremeAbs: "Ekstrempris fra", safety: "Aldri av under", away: "Senk når ingen er hjemme", warmFrom: "Varmt igjen fra kl.",
    protectFrom: "Ingen prissparing fra kl.", protectTo: "Ingen prissparing til kl.", model: (g, tau, l) => `Rommodell: varmes ${g}°/t, mister halve varmen på ${tau} t${l ? " (lært)" : " (standard)"}.`,
    resume: "Gjenoppta plan", manualBadge: "Manuell",
  },
};

const MODE_COLOR = {
  comfort: "var(--hp-comfort)", normal: "var(--hp-normal)", setback: "var(--hp-setback)", preheat: "var(--hp-preheat)",
  save: "var(--hp-save)", off: "var(--hp-off)", away: "var(--hp-setback)",
};
const LEVEL_COLOR = { cheap: "var(--hp-cheap)", normal: "var(--hp-pnormal)", expensive: "var(--hp-exp)", extreme: "var(--hp-ext)" };
const STRAT = [0, 0.5, 1, 2];

const css = `
:host { display:block;
  --hp-comfort:#E9A23B; --hp-normal:color-mix(in srgb, var(--primary-text-color) 45%, transparent); --hp-setback:#5B6CD9; --hp-preheat:#F0644B; --hp-save:#1FA896; --hp-off:#8C96A8;
  --hp-cheap:#3FA971; --hp-pnormal:color-mix(in srgb, var(--secondary-text-color) 35%, transparent); --hp-exp:color-mix(in srgb, var(--primary-text-color) 62%, transparent); --hp-ext:#E0464E;
  --hp-ink: var(--primary-text-color); --hp-soft: var(--secondary-text-color); --hp-line: var(--divider-color, rgba(127,127,127,.25));
}
ha-card { padding: 18px 18px 14px; overflow:hidden; }
.zones { display:flex; gap:18px; margin:0 0 12px; overflow-x:auto; scrollbar-width:none; }
.zones button { all:unset; cursor:pointer; color:var(--hp-soft); font-size:14px; padding:2px 0 6px; border-bottom:2px solid transparent; white-space:nowrap; }
.zones button[aria-selected=true] { color:var(--hp-ink); border-color:var(--hp-ink); font-weight:500; }
.zones button:focus-visible, button:focus-visible, input:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; border-radius:4px; }
.story { font-size:clamp(17px, 2.3vw, 21px); line-height:1.35; color:var(--hp-ink); margin:0 0 4px; max-width:62ch; font-weight:400; letter-spacing:-0.005em; }
.story .temp { font-variant-numeric: tabular-nums; font-weight:600; }
.sub { color:var(--hp-soft); font-size:13px; margin:0 0 14px; max-width:70ch; }
.badge { display:inline-block; font-size:12px; padding:1px 8px; border-radius:999px; background:color-mix(in srgb, var(--hp-ink) 9%, transparent); color:var(--hp-ink); margin-left:6px; vertical-align:2px; }
.chartwrap { position:relative; touch-action:none; user-select:none; -webkit-user-select:none; }
svg { display:block; width:100%; overflow:visible; }
.axis text { fill:var(--hp-soft); font-size:10.5px; font-variant-numeric: tabular-nums; }
.grid line { stroke:var(--hp-line); stroke-width:1; }
.now line { stroke:var(--hp-ink); stroke-width:1; stroke-dasharray:2 3; opacity:.55; }
.now text { fill:var(--hp-ink); font-size:10.5px; opacity:.7; }
.pred { fill:none; stroke:var(--hp-ink); stroke-width:1.5; stroke-dasharray:4 4; opacity:.65; }
.sched { fill:none; stroke:var(--hp-ink); stroke-width:1; opacity:.35; }
.handle { fill:var(--card-background-color, #fff); stroke:var(--hp-ink); stroke-width:1.5; }
.handle.dirty { fill:var(--hp-ink); }
.tip { position:absolute; pointer-events:none; background:var(--card-background-color, #fff); color:var(--hp-ink); border:1px solid var(--hp-line);
  border-radius:10px; padding:8px 10px; font-size:12.5px; line-height:1.45; min-width:150px; box-shadow:0 6px 24px rgba(0,0,0,.14); z-index:2; }
.tip b { font-weight:600; font-variant-numeric:tabular-nums; }
.tip .r { color:var(--hp-soft); }
.legend { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--hp-soft); margin:8px 0 2px; }
.legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
.legend .dash { width:16px; height:0; border-top:1.5px dashed var(--hp-ink); border-radius:0; opacity:.65; vertical-align:3px; }
.row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.bar { display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin-top:12px; }
.cost { color:var(--hp-soft); font-size:13px; }
button.btn { all:unset; cursor:pointer; font-size:13px; padding:7px 14px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-ink); }
button.btn.primary { background:var(--hp-ink); color:var(--card-background-color, #fff); border-color:var(--hp-ink); }
button.btn:disabled { opacity:.4; cursor:default; }
.chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { all:unset; cursor:pointer; font-size:12.5px; padding:5px 11px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); }
.chip[aria-pressed=true] { color:var(--hp-ink); border-color:var(--hp-ink); }
.editor { margin-top:12px; padding-top:12px; border-top:1px solid var(--hp-line); }
.hint { color:var(--hp-soft); font-size:12.5px; margin:6px 0 10px; }
details { margin-top:14px; border-top:1px solid var(--hp-line); padding-top:10px; }
summary { cursor:pointer; color:var(--hp-soft); font-size:13px; list-style:none; }
summary::-webkit-details-marker { display:none; }
summary::before { content:"+ "; } details[open] summary::before { content:"– "; }
.set { display:grid; grid-template-columns: minmax(0,1fr) auto; gap:10px 16px; align-items:center; margin-top:12px; font-size:13.5px; }
.set label { color:var(--hp-ink); }
.set .val { font-variant-numeric:tabular-nums; color:var(--hp-ink); min-width:56px; text-align:right; }
.step { display:flex; align-items:center; gap:6px; }
.step button { all:unset; cursor:pointer; width:26px; height:26px; text-align:center; line-height:26px; border-radius:50%; border:1px solid var(--hp-line); color:var(--hp-ink); }
.seg { display:flex; border:1px solid var(--hp-line); border-radius:999px; overflow:hidden; }
.seg button { all:unset; cursor:pointer; font-size:12.5px; padding:5px 10px; color:var(--hp-soft); }
.seg button[aria-pressed=true] { background:var(--hp-ink); color:var(--card-background-color, #fff); }
.tog { all:unset; cursor:pointer; width:38px; height:22px; border-radius:999px; background:var(--hp-line); position:relative; transition:background .15s; }
.tog::after { content:""; position:absolute; top:3px; left:3px; width:16px; height:16px; border-radius:50%; background:var(--card-background-color, #fff); transition:transform .15s; box-shadow:0 1px 2px rgba(0,0,0,.3); }
.tog[aria-checked=true] { background:var(--hp-ink); } .tog[aria-checked=true]::after { transform:translateX(16px); }
.model { grid-column:1 / -1; color:var(--hp-soft); font-size:12px; margin-top:4px; }
.empty { color:var(--hp-soft); font-size:14px; padding:8px 0; }
@media (prefers-reduced-motion: reduce) { .tog, .tog::after { transition:none; } }
`;

function el(tag, attrs = {}, ...kids) {
  const ns = ["svg", "g", "path", "line", "rect", "text", "circle", "pattern", "defs", "clipPath"].includes(tag)
    ? "http://www.w3.org/2000/svg" : null;
  const e = ns ? document.createElementNS(ns, tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}

class HeatPlannerCard extends HTMLElement {
  static getStubConfig() { return {}; }
  static getConfigElement() { return document.createElement("heat-planner-card-editor"); }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._zone = null;
    this._edit = null; // {date, values:[24], dirty:Set, preview:plan|null}
    this._tip = null;
    this._width = 600;
  }

  setConfig(config) {
    this._config = { hours: 36, ...(config || {}) };
    this._render();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: 12, min_columns: 6, rows: "auto" }; }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._subscribe();
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
    this._ro = new ResizeObserver((e) => {
      const w = Math.round(e[0].contentRect.width);
      if (Math.abs(w - this._width) > 4) { this._width = w; this._render(); }
    });
    this._ro.observe(this);
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; }
  }

  get t() {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").toLowerCase();
    return ["nb", "no", "nn"].some((l) => lang.startsWith(l)) ? I18N.nb : I18N.en;
  }
  get locale() { return this._hass?.locale?.language || this._hass?.language || "en"; }

  _subscribe() {
    if (!this._hass?.connection) return;
    this._unsub = this._hass.connection.subscribeMessage((msg) => {
      this._data = msg;
      if (!this._edit?.dirty?.size) this._render();
    }, { type: "heat_planner/subscribe" });
    this._unsub.catch((err) => { this._error = err?.message || String(err); this._render(); });
  }

  // ------------------------------------------------------------- data helpers
  get entry() {
    const entries = this._data?.entries || [];
    return entries.find((e) => !this._config?.entry_id || e.entry_id === this._config.entry_id) || null;
  }
  get zones() {
    const zs = this.entry?.zones || [];
    const f = this._config?.zones;
    return f?.length ? zs.filter((z) => f.includes(z.zone_id)) : zs;
  }
  get zone() {
    const zs = this.zones;
    return zs.find((z) => z.zone_id === this._zone) || zs[0] || null;
  }
  fmtT(v, z = this.zone) {
    if (v === null || v === undefined) return "–";
    if (z?.mode === "percent") return `${Math.round(v)} %`;
    return `${new Intl.NumberFormat(this.locale, { maximumFractionDigits: 1 }).format(v)}°`;
  }
  fmtMoney(v) {
    const cur = this.entry?.currency || "NOK";
    try { return new Intl.NumberFormat(this.locale, { style: "currency", currency: cur, maximumFractionDigits: 2 }).format(v); }
    catch { return `${v.toFixed(2)} ${cur}`; }
  }
  fmtPrice(v) {
    if (v === null || v === undefined) return "–";
    const cur = this.entry?.currency || "";
    return `${new Intl.NumberFormat(this.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v)} ${cur}/kWh`.trim();
  }
  hh(d) { return String(new Date(d).getHours()).padStart(2, "0"); }
  dateKey(d) { const x = new Date(d); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`; }
  weekday(dateKey) { const [y, m, d] = dateKey.split("-").map(Number); return (new Date(y, m - 1, d).getDay() + 6) % 7; }
  dayValues(z, dateKey) {
    if (z.overrides?.[dateKey]) return [...z.overrides[dateKey]];
    return [...(z.schedule?.[String(this.weekday(dateKey))] || Array(24).fill(0))];
  }
  abs(z, v) { return z.mode === "percent" ? v : z.settings.comfort + v; }
  rel(z, v) { return z.mode === "percent" ? v : v - z.settings.comfort; }

  // ------------------------------------------------------------- story
  story(z) {
    const t = this.t;
    const hours = z.plan?.hours || [];
    const h0 = hours[0];
    const parts = [];
    parts.push(z.room_temperature != null ? t.is(z.name, `<span class="temp">${this.fmtT(z.room_temperature, { mode: "setpoint" })}</span>`) : t.noTemp(z.name));
    let nowTxt;
    if (!z.auto) nowTxt = t.now.disabled() + t.dot;
    else if (z.manual_until) nowTxt = t.now.manual(this.hh(z.manual_until)) + t.dot;
    else if (h0) {
      const mode = h0.mode in t.now ? h0.mode : "normal";
      nowTxt = t.now[mode](`<span class="temp">${this.fmtT(h0.target, z)}</span>`);
      const nxt = hours.slice(1).find((h) => h.target !== h0.target);
      if (nxt) nowTxt += nxt.target === null ? t.nextOff(this.hh(nxt.start)) : t.next(this.fmtT(nxt.target, z), this.hh(nxt.start));
      else nowTxt += t.dot;
    }
    if (nowTxt) parts.push(" " + nowTxt);
    // next price peak
    const peakIdx = hours.findIndex((h) => h.level === "expensive" || h.level === "extreme");
    if (peakIdx >= 0) {
      let end = peakIdx;
      while (end + 1 < hours.length && ["expensive", "extreme"].includes(hours[end + 1].level)) end++;
      const max = Math.max(...hours.slice(peakIdx, end + 1).map((h) => h.price ?? 0));
      const endH = new Date(new Date(hours[end].start).getTime() + 3600e3);
      parts.push(t.peak(this.hh(hours[peakIdx].start), this.hh(endH), this.fmtPrice(max + (this.entry?.price_extra || 0))));
    }
    return parts.join("");
  }

  costLine(z) {
    const hours = (z.plan?.hours || []).slice(0, 24);
    if (!hours.length || hours.some((h) => h.cost === null)) return "";
    const extra = this.entry?.price_extra || 0;
    const cost = hours.reduce((a, h) => a + h.cost + h.kwh * extra, 0);
    const save = (z.plan.savings || 0) + extra * ((z.plan.baseline_kwh || 0) - (z.plan.kwh || 0));
    return this.t.cost(this.fmtMoney(cost), save > 0.005 ? this.fmtMoney(save) : 0);
  }

  // ------------------------------------------------------------- render
  _render() {
    const root = this.shadowRoot;
    if (!root) return;
    const t = this.t;
    root.innerHTML = "";
    root.append(el("style", { text: css }));
    const card = el("ha-card");
    root.append(card);
    if (this._error) { card.append(el("div", { class: "empty", text: this._error })); return; }
    if (!this._data) { card.append(el("div", { class: "empty", text: t.loading })); return; }
    const z = this.zone;
    if (!z) { card.append(el("div", { class: "empty", text: t.none })); return; }

    if (this.zones.length > 1) {
      card.append(el("div", { class: "zones", role: "tablist" },
        this.zones.map((x) => el("button", {
          role: "tab", "aria-selected": String(x.zone_id === z.zone_id), text: x.name,
          onclick: () => { this._zone = x.zone_id; this._edit = null; this._render(); },
        }))));
    }
    const story = el("p", { class: "story", html: this.story(z) });
    if (z.manual_until) story.append(el("span", { class: "badge", text: t.manualBadge }));
    card.append(story);

    const chart = el("div", { class: "chartwrap" });
    card.append(chart);
    this._drawChart(chart, z);
    this._bindChart(chart, z);

    card.append(this._legend(z));

    if (this._edit) card.append(this._editor(z));
    else {
      const bar = el("div", { class: "bar" },
        el("span", { class: "cost", text: this.costLine(z) }),
        el("div", { class: "row" },
          z.manual_until ? el("button", { class: "btn", text: t.resume, onclick: () => this._settings(z, {}, true) }) : null,
          el("button", { class: "btn", text: t.edit, onclick: () => this._startEdit(z, this.dateKey(new Date())) })));
      card.append(bar);
    }
    card.append(this._settingsPanel(z));
  }

  _legend(z) {
    const t = this.t;
    const modes = new Set((this._edit?.preview || z.plan)?.hours?.map((h) => h.mode) || []);
    const items = [...modes].filter((m) => t.modes[m]).map((m) => el("span", {}, el("i", { style: `background:${MODE_COLOR[m]}` }), t.modes[m]));
    if (z.room_temperature != null || z.plan?.hours?.some((h) => h.predicted != null))
      items.push(el("span", {}, el("i", { class: "dash" }), t.predicted));
    const lv = new Set(this._series(z).map((s) => s.price != null && s.level).filter(Boolean));
    const pl = ["cheap", "expensive", "extreme"].filter((l) => lv.has(l));
    if (pl.length) items.push(el("span", {}, `${t.price}: `, pl.map((l) => el("span", { style: "margin-right:8px" }, el("i", { style: `background:${LEVEL_COLOR[l]}` }), t.levels[l]))));
    return el("div", { class: "legend" }, items);
  }

  // ------------------------------------------------------------- chart
  _series(z) {
    // Returns [{start: Date, target, scheduled, predicted, price, level, mode, reasons, editIdx}]
    if (!this._edit) {
      const hrs = (z.plan?.hours || []).slice(0, this._config.hours);
      return hrs.map((h) => ({ ...h, start: new Date(h.start) }));
    }
    const { date, values } = this._edit;
    const plan = this._edit.preview || z.plan;
    const byKey = new Map((plan?.hours || []).map((h) => [this.dateKey(h.start) + "T" + this.hh(h.start), h]));
    const prices = new Map((this.entry?.prices || []).map((p) => [this.dateKey(p.start) + "T" + this.hh(p.start), p]));
    const [y, m, d] = date.split("-").map(Number);
    return values.map((v, i) => {
      const start = new Date(y, m - 1, d, i);
      const key = date + "T" + String(i).padStart(2, "0");
      const h = byKey.get(key);
      const p = prices.get(key);
      return {
        start, editIdx: i, scheduled: this.abs(z, v),
        target: h ? h.target : this.abs(z, v), predicted: h?.predicted ?? null,
        price: h?.price ?? p?.price ?? null, level: h?.level ?? p?.level ?? "normal",
        mode: h?.mode ?? "normal", reasons: h?.reasons ?? ["schedule"], cost: h?.cost ?? null,
      };
    });
  }

  _drawChart(wrap, z) {
    const series = this._series(z);
    if (!series.length) return;
    const W = Math.max(280, this._width - 36);
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
      const vals = series.flatMap((s) => [s.target, s.scheduled, s.predicted]).filter((v) => v !== null && v !== undefined);
      if (z.room_temperature != null) vals.push(z.room_temperature);
      lo = Math.floor(Math.min(...vals) - 1); hi = Math.ceil(Math.max(...vals) + 1);
      if (hi - lo < 6) { const c = (hi + lo) / 2; lo = Math.floor(c - 3); hi = Math.ceil(c + 3); }
    }
    const Y = (v) => m.t + ch - ((v - lo) / (hi - lo)) * ch;
    this._geo = { X, Y, bw, lo, hi, m, ch, n, W, percent };

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": this.t.target });
    // grid + y axis
    const grid = el("g", { class: "grid" }); const axis = el("g", { class: "axis" });
    const step = percent ? 25 : (hi - lo > 10 ? 4 : 2);
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      grid.append(el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
      axis.append(el("text", { x: m.l - 6, y: Y(v) + 3.5, "text-anchor": "end", text: percent ? `${v}%` : `${v}°` }));
    }
    svg.append(grid);

    // ribbon: area under target, colored by mode
    const ribbon = el("g");
    series.forEach((s, i) => {
      const color = MODE_COLOR[s.mode] || MODE_COLOR.normal;
      if (s.target === null || s.target === undefined) {
        ribbon.append(el("rect", { x: X(i) + 1, y: Y(lo + (hi - lo) * 0.04) - 3, width: bw - 2, height: 3, rx: 1.5, fill: MODE_COLOR.off }));
        return;
      }
      const yv = Y(s.target);
      if (s.mode !== "normal") ribbon.append(el("rect", { x: X(i) + 0.5, y: yv, width: Math.max(0, bw - 1), height: m.t + ch - yv, fill: color, opacity: 0.14 }));
      ribbon.append(el("rect", { x: X(i) + 0.5, y: yv - 1.5, width: Math.max(0, bw - 1), height: 3, rx: 1.5, fill: color }));
    });
    svg.append(ribbon);

    // scheduled line (only differs from target when price logic acts)
    if (this._edit || series.some((s) => s.target !== null && Math.abs((s.target ?? 0) - s.scheduled) > 0.01)) {
      let d = "";
      series.forEach((s, i) => { const y = Y(s.scheduled); d += `${i ? "L" : "M"}${X(i)},${y}L${X(i + 1)},${y}`; });
      svg.append(el("path", { class: "sched", d }));
    }

    // predicted temperature
    if (!percent || series.some((s) => s.predicted != null)) {
      const pts = series.map((s, i) => (s.predicted == null ? null : [X(i) + bw, Y(s.predicted)]));
      if (z.room_temperature != null && !this._edit) pts.unshift([X(0) + bw * this._nowFrac(series), Y(z.room_temperature)]);
      let d = ""; let pen = false;
      pts.forEach((p) => { if (!p) { pen = false; return; } d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; pen = true; });
      if (d) svg.append(el("path", { class: "pred", d }));
    }

    // price strip
    const priced = series.filter((s) => s.price != null);
    if (priced.length) {
      const pmax = Math.max(...priced.map((s) => s.price)); const pmin = Math.min(0, ...priced.map((s) => s.price));
      const py0 = H - m.b; const ph = P - 6;
      const g = el("g");
      series.forEach((s, i) => {
        if (s.price == null) return;
        const hgt = Math.max(2, ((s.price - pmin) / (pmax - pmin || 1)) * ph);
        g.append(el("rect", { x: X(i) + bw * 0.18, y: py0 - hgt, width: bw * 0.64, height: hgt, rx: Math.min(2, bw * 0.2), fill: LEVEL_COLOR[s.level] || LEVEL_COLOR.normal }));
      });
      svg.append(g);
    }

    // x axis labels + day separators
    const every = n > 30 ? 3 : n > 16 ? 2 : 1;
    series.forEach((s, i) => {
      const hr = s.start.getHours();
      if (hr === 0 && i > 0) {
        svg.append(el("line", { x1: X(i), x2: X(i), y1: m.t, y2: H - m.b, stroke: "var(--hp-line)" }));
        axis.append(el("text", { x: X(i) + 3, y: m.t + 10, text: s.start.toLocaleDateString(this.locale, { weekday: "short" }) }));
      }
      if (hr % every === 0) axis.append(el("text", { x: X(i) + bw / 2, y: H - 5, "text-anchor": "middle", text: String(hr).padStart(2, "0") }));
    });
    svg.append(axis);

    // now marker
    const nowFrac = this._nowFrac(series);
    if (nowFrac !== null) {
      const idx = series.findIndex((s) => s.start.getTime() <= Date.now() && Date.now() < s.start.getTime() + 3600e3);
      const x = X(idx) + bw * nowFrac;
      svg.append(el("g", { class: "now" }, el("line", { x1: x, x2: x, y1: m.t, y2: H - m.b }),
        el("text", { x: x + 4, y: m.t + 9, text: this.t.nowLabel })));
    }

    // edit handles
    if (this._edit) {
      const hg = el("g");
      series.forEach((s, i) => hg.append(el("circle", {
        class: `handle${this._edit.dirty.has(i) ? " dirty" : ""}`, cx: X(i) + bw / 2, cy: Y(s.scheduled), r: Math.min(5, bw / 3),
      })));
      svg.append(hg);
    }

    // hover highlight
    const hl = el("rect", { class: "hl", x: 0, y: m.t, width: bw, height: H - m.t - m.b, fill: "color-mix(in srgb, var(--hp-ink) 7%, transparent)", opacity: 0 });
    svg.append(hl);
    this._hl = hl;
    this._series_ = series;
    wrap.replaceChildren(svg);
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
      return Math.max(percent ? 0 : z.settings.min_value, Math.min(percent ? 100 : z.settings.max_value, v));
    };
    wrap.addEventListener("pointermove", (ev) => {
      if (!this._geo) return;
      const i = idxAt(ev);
      if (this._drag) { this._paint(wrap, z, i, valAt(ev)); return; }
      const { X, bw, W } = geo();
      this._hl?.setAttribute("x", X(i)); this._hl?.setAttribute("opacity", 1);
      this._showTip(wrap, this._series_[i], z, (X(i) + bw / 2) / W);
    });
    wrap.addEventListener("pointerleave", () => { if (!this._drag) { this._hl?.setAttribute("opacity", 0); this._hideTip(); } });
    if (this._edit) {
      wrap.addEventListener("pointerdown", (ev) => {
        if (ev.button > 0) return;
        ev.preventDefault();
        wrap.setPointerCapture(ev.pointerId);
        this._drag = true; this._hideTip();
        this._paint(wrap, z, idxAt(ev), valAt(ev));
      });
      const end = () => { if (this._drag) { this._drag = false; this._render(); this._preview(z); } };
      wrap.addEventListener("pointerup", end);
      wrap.addEventListener("pointercancel", end);
    }
  }

  _nowFrac(series) {
    const now = Date.now();
    const s = series.find((x) => x.start.getTime() <= now && now < x.start.getTime() + 3600e3);
    return s ? (now - s.start.getTime()) / 3600e3 : null;
  }

  _showTip(wrap, s, z, frac) {
    const t = this.t;
    this._hideTip();
    const extra = this.entry?.price_extra || 0;
    const tip = el("div", { class: "tip" },
      el("div", {}, el("b", { text: `${String(s.start.getHours()).padStart(2, "0")}:00` }), "  ",
        s.target === null ? t.modes.off : `${t.target} `, s.target === null ? null : el("b", { text: this.fmtT(s.target, z) })),
      s.predicted != null ? el("div", {}, `${t.predicted}: `, el("b", { text: this.fmtT(s.predicted, { mode: "setpoint" }) })) : null,
      s.price != null ? el("div", {}, `${t.price}: `, el("b", { text: this.fmtPrice(s.price + extra) }), ` (${t.levels[s.level] || s.level})`) : null,
      el("div", { class: "r", text: (s.reasons || []).map((r) => t.reasons[r] || r).join(". ") }));
    wrap.append(tip);
    const w = wrap.clientWidth; const tw = tip.offsetWidth;
    tip.style.left = `${Math.max(0, Math.min(w - tw, frac * w - tw / 2))}px`;
    tip.style.top = `-8px`;
    tip.style.transform = "translateY(-100%)";
    this._tip = tip;
  }
  _hideTip() { this._tip?.remove(); this._tip = null; }

  // ------------------------------------------------------------- editing
  _startEdit(z, date) {
    this._edit = { date, values: this.dayValues(z, date), dirty: new Set(), preview: null, scope: "date" };
    this._render();
    this._preview(z);
  }

  _paint(wrap, z, i, absVal) {
    const v = this.rel(z, absVal);
    if (this._edit.values[i] === v && this._edit.dirty.has(i)) return;
    this._edit.values[i] = v; this._edit.dirty.add(i);
    cancelAnimationFrame(this._raf);
    this._raf = requestAnimationFrame(() => this._drawChart(wrap, z));
  }

  async _preview(z) {
    if (!this._edit) return;
    clearTimeout(this._pt);
    this._pt = setTimeout(async () => {
      try {
        const res = await this._hass.callWS({
          type: "heat_planner/preview", entry_id: this.entry.entry_id, zone_id: z.zone_id,
          date: this._edit.date, values: this._edit.values,
        });
        if (this._edit) { this._edit.preview = res; this._render(); }
      } catch (e) { /* preview is best effort */ }
    }, 120);
  }

  _editor(z) {
    const t = this.t;
    const e = this._edit;
    const today = this.dateKey(new Date());
    const tomorrow = this.dateKey(new Date(Date.now() + 864e5));
    const wd = this.weekday(e.date);
    const dayName = e.date === today ? t.today.toLowerCase() : e.date === tomorrow ? t.tomorrow.toLowerCase() : t.days[wd];
    const pick = (date) => el("button", {
      class: "chip", "aria-pressed": String(e.date === date),
      text: date === today ? t.today : date === tomorrow ? t.tomorrow : t.days[this.weekday(date)],
      onclick: () => this._startEdit(z, date),
    });
    const dates = [0, 1, 2, 3, 4, 5, 6].map((k) => this.dateKey(new Date(Date.now() + k * 864e5)));
    const scopes = [
      ["date", t.onlyDay(dayName)], ["days", t.every(t.daysLong[wd])], ["weekdays", t.weekdays], ["weekend", t.weekend], ["all", t.all],
    ];
    const isOverride = !!z.overrides?.[e.date];
    return el("div", { class: "editor" },
      el("div", { class: "chips" }, dates.map(pick)),
      el("p", { class: "hint", text: t.dragHint + (isOverride ? " " + t.overrideNote : "") }),
      el("div", { class: "row" }, el("span", { class: "cost", text: t.applyTo }),
        el("div", { class: "chips" }, scopes.map(([k, label]) => el("button", {
          class: "chip", "aria-pressed": String(e.scope === k), text: label, onclick: () => { e.scope = k; this._render(); },
        })))),
      el("div", { class: "bar" },
        el("span", { class: "cost", text: e.preview ? this.costLine({ ...z, plan: e.preview }) : "" }),
        el("div", { class: "row" },
          isOverride ? el("button", { class: "btn", text: t.resetDay, onclick: () => this._save(z, "date", null) }) : null,
          el("button", { class: "btn", text: e.dirty.size ? t.discard : t.done, onclick: () => { this._edit = null; this._render(); } }),
          el("button", { class: "btn primary", text: t.save, disabled: !e.dirty.size && e.scope === "date",
            onclick: () => this._save(z, e.scope, e.values) }))));
  }

  async _save(z, scope, values) {
    const e = this._edit;
    const msg = { type: "heat_planner/save", entry_id: this.entry.entry_id, zone_id: z.zone_id, scope, values };
    if (scope === "date") msg.date = e.date;
    if (scope === "days") msg.days = [this.weekday(e.date)];
    try {
      await this._hass.callWS(msg);
      this._edit = null;
      this._render();
    } catch (err) { this._error = err?.message || String(err); this._render(); }
  }

  async _settings(z, settings, resume = false) {
    try {
      await this._hass.callWS({ type: "heat_planner/settings", entry_id: this.entry.entry_id, zone_id: z ? z.zone_id : null, settings, resume });
    } catch (err) { this._error = err?.message || String(err); this._render(); }
  }

  // ------------------------------------------------------------- settings
  _settingsPanel(z) {
    const t = this.t;
    const s = z.settings;
    const pct = z.mode === "percent";
    const unit = pct ? " %" : "°";
    const d = el("details", { open: this._open || undefined, ontoggle: (ev) => { this._open = ev.target.open; } }, el("summary", { text: t.settings }));
    const grid = el("div", { class: "set" });
    const stepper = (label, key, stepv, min, max, fmt = (v) => `${v}${unit}`) => {
      const v = s[key];
      grid.append(el("label", { text: label }),
        el("div", { class: "step" },
          el("button", { "aria-label": `${label} −`, text: "−", onclick: () => this._settings(z, { [key]: Math.max(min, +(v - stepv).toFixed(2)) }) }),
          el("span", { class: "val", text: fmt(v) }),
          el("button", { "aria-label": `${label} +`, text: "+", onclick: () => this._settings(z, { [key]: Math.min(max, +(v + stepv).toFixed(2)) }) })));
    };
    const toggle = (label, on, fn) => grid.append(el("label", { text: label }),
      el("button", { class: "tog", role: "switch", "aria-checked": String(!!on), "aria-label": label, onclick: fn }));

    toggle(t.auto, z.auto, () => this._settings(z, { auto: !z.auto }));
    if (!pct) stepper(t.comfort, "comfort", 0.5, 10, 28, (v) => this.fmtT(v, z));
    const ai = STRAT.reduce((b, v, i) => (Math.abs(v - s.price_aggressiveness) < Math.abs(STRAT[b] - s.price_aggressiveness) ? i : b), 0);
    grid.append(el("label", { text: t.strategy }), el("div", { class: "seg", role: "group" },
      t.strategies.map((name, i) => el("button", { "aria-pressed": String(i === ai), text: name, onclick: () => this._settings(z, { price_aggressiveness: STRAT[i] }) }))));
    stepper(t.shift, "price_shift", pct ? 5 : 0.5, 0, pct ? 60 : 5, (v) => `−${v}${unit}`);
    stepper(t.preheatS, "preheat", pct ? 5 : 0.5, 0, pct ? 40 : 4, (v) => `+${v}${unit}`);
    toggle(t.extremeOff, s.extreme_off, () => this._settings(z, { extreme_off: !s.extreme_off }));
    if (s.extreme_off && this.entry?.rules) {
      const r = this.entry.rules;
      grid.append(el("label", { text: t.extremeAbs }), el("div", { class: "step" },
        el("button", { text: "−", "aria-label": `${t.extremeAbs} −`, onclick: () => this._settings(null, { extreme_abs_price: Math.max(0.5, +(r.extreme_abs_price - 0.25).toFixed(2)) }) }),
        el("span", { class: "val", text: this.fmtPrice(r.extreme_abs_price) }),
        el("button", { text: "+", "aria-label": `${t.extremeAbs} +`, onclick: () => this._settings(null, { extreme_abs_price: +(r.extreme_abs_price + 0.25).toFixed(2) }) })));
      if (!pct) stepper(t.safety, "safety_min_temp", 0.5, 5, 25, (v) => this.fmtT(v, z));
    }
    stepper(t.away, "away_offset", pct ? 5 : 0.5, pct ? -80 : -10, 0, (v) => `${v}${unit}`);
    stepper(t.warmFrom, "away_until_hour", 1, 0, 23, (v) => `${String(v).padStart(2, "0")}:00`);
    stepper(t.protectFrom, "protected_from_hour", 1, 0, 23, (v) => `${String(v).padStart(2, "0")}:00`);
    stepper(t.protectTo, "protected_to_hour", 1, 1, 24, (v) => `${String(v).padStart(2, "0")}:00`);
    if (!pct && z.thermal) {
      const half = Math.round(z.thermal.tau_hours * Math.LN2);
      grid.append(el("div", { class: "model", text: t.model(z.thermal.gross_rate, half, z.thermal.learned) }));
    }
    d.append(grid);
    return d;
  }
}

customElements.define("heat-planner-card", HeatPlannerCard);

class HeatPlannerCardEditor extends HTMLElement {
  setConfig(c) { this._c = c || {}; this._r(); }
  set hass(h) { this._h = h; }
  _r() {
    this.innerHTML = `<p style="margin:8px 0;color:var(--secondary-text-color)">Heat Planner shows every zone. Optional YAML: <code>zones: [stue, bad]</code>, <code>hours: 36</code>.</p>`;
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
