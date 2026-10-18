/* Heat Planner – visual help ("How it works").
 * <heat-planner-help> is used inside the main card; <heat-planner-help-card> is a dashboard card.
 * No build step, no dependencies. Reads live settings with heat_planner/get when available.
 */
(() => {
  if (customElements.get("heat-planner-help")) return;

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const MODE = {
    comfort: "var(--hp-comfort)", normal: "var(--hp-normal)", setback: "var(--hp-setback)", preheat: "var(--hp-preheat)",
    save: "var(--hp-save)", off: "var(--hp-off)", away: "var(--hp-setback)",
  };
  const LEVEL = { c: "var(--hp-cheap)", n: "var(--hp-pnormal)", e: "var(--hp-exp)", x: "var(--hp-ext)" };
  const M = { n: "normal", c: "comfort", s: "setback", p: "preheat", v: "save", o: "off", a: "away" };

  /* ---------- tiny chart renderer (same visual language as the plan card) ---------- */
  function chart(o) {
    const n = o.prices.length, W = o.w || 640, ml = 34, mr = 10, top = 30, ch = o.ch || 120;
    const pb = top + ch + 12, ph = o.ph || 30, axY = pb + ph + 15, H = axY + 6;
    const bw = (W - ml - mr) / n, X = (i) => ml + i * bw;
    const [y0, y1] = o.y, Y = (v) => top + ch - ((v - y0) / (y1 - y0)) * ch;
    let s = "";
    for (const b of o.bands || []) {
      s += `<rect x="${X(b.from) + 1}" y="${top - 22}" width="${(b.to - b.from) * bw - 2}" height="${ch + 22}" rx="7" class="band ${b.kind || ""}"/>`;
      if (b.label) s += `<text x="${X(b.from) + 7}" y="${top - 8}" class="bl">${esc(b.label)}</text>`;
    }
    for (const v of o.ticks || []) {
      s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(v)}" y2="${Y(v)}" class="gl"/><text x="${ml - 7}" y="${Y(v) + 3.5}" class="yl">${v}°</text>`;
    }
    if (o.hline) {
      s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(o.hline.v)}" y2="${Y(o.hline.v)}" class="hl"/>`;
      s += `<text x="${W - mr - 2}" y="${Y(o.hline.v) - 5}" class="hlt">${esc(o.hline.label)}</text>`;
    }
    o.tgt.forEach((v, i) => {
      if (v == null) return;
      const m = M[o.modes[i]] || o.modes[i], c = MODE[m] || MODE.normal;
      if (m === "off") {
        s += `<rect x="${X(i) + 1}" y="${top + ch - 3}" width="${bw - 2}" height="3" rx="1.5" fill="${c}"/>`;
        s += `<text x="${X(i) + bw / 2}" y="${top + ch - 9}" class="offt">${esc(o.offLabel || "off")}</text>`;
        return;
      }
      const y = Y(v);
      if (m !== "normal") s += `<rect x="${X(i) + 0.5}" y="${y}" width="${bw - 1}" height="${top + ch - y}" fill="${c}" opacity=".15"/>`;
      s += `<rect x="${X(i) + 0.5}" y="${y - 1.5}" width="${bw - 1}" height="3" rx="1.5" fill="${c}"/>`;
    });
    for (const l of o.lines || []) {
      let d = "";
      l.vals.forEach((v, i) => {
        if (v == null) return;
        const prev = l.vals[i - 1];
        d += prev == null ? `M${X(i)},${Y(v)}` : `L${X(i)},${Y(v)}`;
        d += `L${X(i + 1)},${Y(v)}`;
      });
      s += `<path d="${d}" class="${l.cls}"/>`;
    }
    if (o.pred) {
      const d = o.pred.map((v, i) => `${i ? "L" : "M"}${(X(i) + bw / 2).toFixed(1)},${Y(v).toFixed(1)}`).join("");
      s += `<path d="${d}" class="pred"/>`;
    }
    if (o.dots) for (const [i, v] of o.dots) s += `<circle cx="${X(i) + bw / 2}" cy="${Y(v)}" r="2.6" class="dot"/>`;
    const pm = Math.max(...o.prices);
    o.prices.forEach((p, i) => {
      if (p == null) return;
      const h = Math.max(3, (p / pm) * ph);
      s += `<rect x="${X(i) + bw * 0.17}" y="${pb + ph - h}" width="${bw * 0.66}" height="${h}" rx="2" fill="${LEVEL[o.levels[i]]}"/>`;
    });
    for (let i = 0; i < n; i++) {
      const hr = (o.start + i) % 24;
      if (hr % (o.every || 3) === 0) s += `<text x="${X(i) + bw / 2}" y="${axY}" class="xl">${String(hr).padStart(2, "0")}</text>`;
    }
    if (o.now != null) s += `<line x1="${X(o.now)}" x2="${X(o.now)}" y1="${top - 4}" y2="${pb + ph}" class="nowl"/>`;
    for (const m of o.marks || []) {
      const cx = X(m.h) + bw / 2;
      const base = m.y != null ? Y(m.y) : Y(o.tgt[m.h] ?? y1);
      const cy = Math.max(top - 12, base - 17);
      s += `<g class="mk"><circle cx="${cx}" cy="${cy}" r="9.5"/><text x="${cx}" y="${cy + 4}">${esc(m.k)}</text></g>`;
    }
    const svgEl = `<svg viewBox="0 0 ${W} ${H}" class="ill" role="img" aria-label="${esc(o.aria || "")}">${s}</svg>`;
    return W >= 600 ? `<div class="scroll">${svgEl}</div>` : svgEl;
  }

  // simple first-order room response for the illustrations
  const sim = (tgt, modes, start, k = 0.3) => {
    let T = start;
    return tgt.map((v, i) => (T = (M[modes[i]] || modes[i]) === "off" ? T - 0.35 : T + (v - T) * k));
  };
  const lv = (s) => s.split("");
  const md = (s) => s.split("");

  /* ---------- example scenarios ---------- */
  const DAY = {
    prices: [0.55, 0.5, 0.48, 0.47, 0.5, 0.62, 0.95, 1.35, 1.5, 1.2, 0.95, 0.85, 0.8, 0.78, 0.82, 1.05, 1.4, 1.9, 3.2, 1.8, 1.3, 1.0, 0.8, 0.65],
    levels: lv("cccccnnnnnnnncnnnexennnc"),
    tgt: [19, 19, 19, 19, 19, 22, 22, 22, 21, 21, 21, 21, 21, 21, 21, 23, 23, 20, 18, 20, 21, 21, 21, 19],
    modes: md("ssssscccnnnnnnnppvovnnns"),
  };
  DAY.pred = sim(DAY.tgt, DAY.modes, 19.6);

  const AWAY = {
    prices: [0.55, 0.5, 0.48, 0.47, 0.5, 0.62, 0.9, 1.1, 1.15, 1.0, 0.95, 0.9, 0.85, 0.8, 0.85, 0.95, 1.05, 1.15, 1.2, 1.1, 1.0, 0.9, 0.8, 0.65],
    levels: lv("cccccnnnnnnnnnnnnnnnnnnc"),
    tgt: [19, 19, 19, 19, 19, 22, 22, 22, 17, 17, 17, 17, 17, 21, 21, 21, 21, 21, 21, 21, 21, 21, 21, 19],
    modes: md("ssssscccaaaaannnnnnnnnns"),
  };
  AWAY.pred = sim(AWAY.tgt, AWAY.modes, 19.6, 0.28);

  const RULES = [
    { start: 16, prices: [1.0, 1.1, 1.7, 1.9, 1.8, 1.2, 1.0, 0.9], levels: lv("nneeennn"), tgt: [21, 21, 20, 20, 20, 21, 21, 21], modes: md("nnvvvnnn"), y: [18.5, 22.5] },
    { start: 4, prices: [0.5, 0.48, 0.52, 1.6, 1.8, 1.1, 1.0, 0.9], levels: lv("ccceennn"), tgt: [22, 22, 22, 20, 20, 21, 21, 21], modes: md("pppvvnnn"), y: [18.5, 23] },
    { start: 15, prices: [1.0, 1.1, 1.2, 3.4, 1.5, 1.1, 1.0, 0.9], levels: lv("nnnxennn"), tgt: [23, 23, 23, 18, 20, 21, 21, 21], modes: md("pppovnnn"), hline: 18, y: [17, 24] },
    { start: 11, prices: [0.9, 1.0, 1.7, 1.8, 1.6, 1.0, 0.9, 0.9], levels: lv("nneeennn"), tgt: [21, 21, 21, 21, 20, 21, 21, 21], modes: md("nnnnvnnn"), band: [2, 4], y: [18.5, 22.5] },
  ];
  for (const r of RULES) r.pred = sim(r.tgt, r.modes, 20.9, 0.35);

  const MANUAL = {
    prices: [0.9, 0.95, 1.0, 1.1, 1.2, 1.15, 1.0, 0.95, 0.85, 0.75, 0.7, 0.65],
    levels: lv("nnnnnnnnnccc"),
    tgt: [21, 21, 21, 21, 21, 21, 21, 21, 19, 19, 19, 19],
    modes: md("nnnnnnnnssss"),
    actual: [null, null, null, 23, 23, 23, 23, 23, null, null, null, null],
  };

  // a heating test: heater on for 6 h, then off – dots measured, dashed = model
  const LEARN = (() => {
    const model = [], dots = [];
    let T = 19;
    for (let i = 0; i < 16; i++) {
      T = i < 6 ? T + (24.5 - T) * 0.22 : T - (T - 16) / 26;
      model.push(T);
      const jitter = Math.sin(i * 12.9898) * 0.22;
      dots.push([i, T + jitter]);
    }
    return { model, dots, prices: Array(16).fill(null), levels: Array(16).fill("n") };
  })();

  /* ---------- texts ---------- */
  const T = {
    nb: {
      title: "Slik fungerer varmeplanen",
      lead: "Heat Planner lager en plan for hver time de neste 36 timene, for hvert rom – og stiller termostatene etter den. Her er hva som påvirker planen, og hvordan du tar kontrollen selv.",
      tocLabel: "Innhold",
      toc: { day: "En vanlig dag", colors: "Fargene", rules: "Prisreglene", optimizer: "Optimereren", tariff: "Strømavtalen", peak: "Effekttrinn", away: "Når dere er borte", arrival: "Hjemkomst", edit: "Endre planen", manual: "Når du tar over", feedback: "For kaldt / varmt", safety: "Sikkerhet", learn: "Hva den lærer", confidence: "Hvor sikker", money: "Kostnad", settings: "Innstillinger", faq: "Spørsmål" },

      optH: "Optimereren",
      optP: "Med «Optimer (alle rom)» på bruker Heat Planner ikke bare reglene over. Den simulerer hvert rom med sin egen modell og velger målet for hver time som gir lavest kostnad – energi × pris, pluss en straff for å være kaldere enn kurven din (og, med effekttrinn, for å gå over taket). Den forvarmer bare når det er billigere å lagre varme i gulvet enn å varme i de dyre timene, ekstra varmetap medregnet.",
      optBounds: [
        ["Senker maks", (s) => `${s.shiftMax}° under kurven din – «Senk i dyre timer» × strategien.`],
        ["Hever maks", (s) => `${s.raiseMax}° – inntil det dobbelte av «Hev før dyre timer».`],
        ["Av bare i ekstreme timer", (s) => `Med «Slå av ved ekstrempris» på, og maks ${s.maxoff} t på rad.`],
        ["Aldri under", (s) => `${s.safety}° når den senker – sikkerhetstemperaturen.`],
        ["Trege gulv", () => "Komforten vurderes når varmen kommer fram: et gulv som reagerer etter en time, forvarmes en time tidligere."],
        ["Faller tilbake til reglene", () => "Uten romtemperatur, uten priser, eller med strategien «Ignorer»."],
      ],
      optLive: (on) => (on ? "Optimereren er <b>på</b> hos dere." : "Optimereren er <b>av</b> hos dere – prisreglene over gjelder."),

      tariffH: "Strømavtalen",
      tariffP: "Hvor mye det lønner seg å flytte varme i tid, avhenger av hva dere betaler. Søylene viser samme døgn med tre ulike avtaler.",
      tariffs: [
        ["Spotpris", "Prisen følger børsen time for time. Store forskjeller mellom timene – her gir prisstyring mest."],
        ["Strømstøtte", "Staten dekker 90 % av prisen over en terskel. Toppene blir flate, så det er mindre å hente på å flytte forbruk."],
        ["Norgespris", "Samme pris hele døgnet. Å flytte forbruk sparer nesten ingenting – planen holder komforten og passer heller på effekttoppene."],
      ],
      thresholdLbl: "terskel", fixedLbl: "fast pris",
      tariffLive: (l) => `Dere har: <b>${l}</b>.`,

      peakH: "Effekttrinn",
      peakP: "En del av nettleien er et fast beløp hver måned. Trinnet bestemmes av snittet av de tre høyeste timene i måneden, fra tre ulike døgn. Heat Planner setter et tak for hver time, så varmen ikke dytter dere opp i neste trinn.",
      peakMarks: [
        ["1", "Tre høyeste døgn", "Snittet av disse bestemmer trinnet."],
        ["2", "Tak i dag", "Varmen fordeles så timeforbruket holder seg under."],
        ["3", "Neste trinn", "Koster mer hver måned resten av måneden."],
      ],
      capLbl: "tak i dag", stepLbl: "neste trinn",
      peakNotes: [
        "Krever en strømmåler for hele huset (Tibber Pulse, HAN-leser …) valgt under Konfigurer. Uten den er effektstyringen av.",
        "Taket gjelder hele huset – varmtvann, elbil og komfyr teller også.",
        "Når taket ville blitt brutt, senkes rom litt i de timene, lavest prioritet først. Beskyttede timer og rom nær sikkerhetstemperaturen kuttes aldri, og kutt som ikke senker døgnets topp, droppes.",
        "En vakt følger måleren: ser timen ut til å gå over taket, senkes rom til timen er over.",
      ],
      peakLive: (p) => `Nå: ${[p.step ? `effekttrinn <b>${p.step}</b>` : "", p.avg ? `snitt topp 3 <b>${p.avg} kW</b>` : "", p.cap ? `tak i dag <b>${p.cap} kW</b>` : ""].filter(Boolean).join(" · ")}.`,

      arrivalH: "Lærer når dere kommer hjem",
      arrivalP: "Står «Varmt igjen fra» på Auto, ser Heat Planner på når huset går fra tomt til at noen kommer hjem – ukedag for ukedag, de siste seks ukene. Den velger et tidlig anslag og begynner å varme så lenge før som rommet trenger.",
      arrivalMarks: [
        ["1", "Hjemkomster", "Hver prikk er en dag den første kom hjem."],
        ["2", "Tidlig anslag", "Et av de tidligste tidspunktene, så dere sjelden kommer til et kaldt hus."],
        ["3", "Varmer fra", "Anslaget minus tiden rommet trenger, mellom kl. 10 og 18."],
      ],
      arrivalNotes: ["Korte ærender under to timer og hjemkomster om natta teller ikke.", "Den trenger minst tre dager før den stoler på en ukedag. Til da brukes hverdager eller helg samlet – ellers den faste timen.", "Telefoner uten fungerende sporing teller ikke."],
      arrivalLive: (t) => `Hos dere: vanligvis hjemme ca. <b>${t}</b>.`,
      expLbl: "anslag", warmLbl: "varmer opp",

      fbH: "For kaldt eller for varmt?",
      fbP: "Trykk «For kaldt» eller «For varmt» i planen. Heat Planner justerer én ting litt – ut fra hva planen gjorde akkurat da – og forteller hva den endret.",
      fbDemo: ["Stue er 19,5°. Sparer med 20° i en dyr time.", "For kaldt", "For varmt", "Senker mindre i dyre timer (1° → 0,5°)."],
      fbRules: [
        ["For kaldt i en spare- eller av-time", "Senker 0,5° mindre i dyre timer. Var rommet nær «Aldri av under», heves den også 0,5°."],
        ["For kaldt mens ingen var hjemme", "Senker 0,5° mindre når dere er borte."],
        ["For kaldt i en senket time i din egen kurve", "Ingenting endres – endre kurven under «Endre døgn»."],
        ["For kaldt ellers", "Komforttemperaturen går opp 0,5°."],
        ["For varmt", "Forvarmer 0,5° mindre hvis den forvarmet – ellers går komforten ned 0,5°."],
      ],
      fbNote: "Rom i prosent bruker steg på 5 % og flytter hele døgnkurven i stedet for komforten. Fordi alle kan trykke, er effekten begrenset: samme knapp innen ti minutter teller én gang, maks 8 endringer per døgn, og komforten holdes innenfor ±2° av morgenens verdi – og over sikkerhetstemperaturen.",

      confH: "Hvor sikker er modellen?",
      confP: "Rommodellen sammenligner det den ville spådd en time fram med det termometeret faktisk viste. Planen bruker samme modell uansett – merkelappen sier hvor mye du kan stole på den stiplede linja.",
      conf: [["good", "God", "Under ±0,3°. Forventet temperatur er pålitelig."], ["fair", "Middels", "±0,3–0,6°. Rommet kan havne litt ved siden av."], ["uncertain", "Usikker", "±0,6° eller mer. En lært modell med så store avvik blandes halvt med anslaget fra beskrivelsen av huset, og Heat Planner gir et varsel."]],
      confNotes: ["Sol gjennom vinduene og at gulvet reagerer med forsinkelse tas med når modellen har lært det.", "Under «Innstillinger» i planen står hvor godt modellen treffer for hvert rom."],
      confLbl: "avvik",
      confZone: (z) => `<b>${esc(z.name)}</b>: ${z.q}` + (z.rmse ? ` · ±${z.rmse}°` : "") + (z.solar ? " · sol" : "") + (z.lag ? ` · reagerer etter ${z.lag} t` : ""),
      autoLbl: "Auto",
      awayAuto: " Står den på Auto, er timen lært av når dere pleier å komme hjem.",
      inputs: [
        ["mdi:chart-bell-curve-cumulative", "Døgnkurven", "Hvor varmt dere vil ha det"],
        ["mdi:lightning-bolt", "Strømprisen", "Time for time"],
        ["mdi:home-account", "Hvem er hjemme", "Fra telefonene"],
        ["mdi:home-thermometer", "Rommet", "Hvor fort det varmes og kjøles"],
      ],
      plan: ["mdi:calendar-clock", "Plan", "36 timer, hvert rom"],
      thermo: ["mdi:thermostat", "Termostatene", "Stilles automatisk"],
      principle: "Prisen bestemmer <b>når</b> det varmes. Dere bestemmer <b>hvor varmt</b>.",
      live: (z, src) => `Her styres <b>${z}</b>` + (src ? ` med priser fra <b>${src}</b>.` : "."),

      dayH: "En vanlig dag",
      dayP: "Et eksempel med komforttemperatur 21°. Den fargede stripen er målet, den stiplede linja er forventet romtemperatur, og søylene nederst er strømprisen.",
      dayMarks: [
        ["1", "Natt", "2° lavere mens dere sover."],
        ["2", "Morgen", "Ekstra varmt 05–08, før dere står opp."],
        ["3", "Beskyttet", "13–15 ignoreres prisen, så det er varmt når dere kommer hjem."],
        ["4", "Forvarmer", "Gulvet lagrer varme før pristoppen."],
        ["5", "Dyr time", "1° lavere – knapt merkbart."],
        ["6", "Ekstrempris", "Av en time. Gulvet holder på varmen."],
      ],
      protectedLbl: "beskyttet", offLbl: "av",

      colorsH: "Fargene",
      colorsP: "Stripen i planen skifter farge etter hva den gjør. Pek eller trykk på en time i planen for å se nøyaktig hvorfor.",
      modes: [
        ["normal", "Vanlig", "Følger døgnkurven"],
        ["comfort", "Komfort", "Ekstra varmt, f.eks. morgen"],
        ["setback", "Senket", "Natt eller ingen hjemme"],
        ["preheat", "Forvarmer", "Lagrer varme før dyre timer"],
        ["save", "Sparer", "Litt lavere i en dyr time"],
        ["off", "Av", "Ekstremt dyr time"],
      ],
      levelsH: "Strømprisen",
      levels: [["c", "Billig"], ["n", "Vanlig"], ["e", "Dyr"], ["x", "Ekstrem"]],
      predLbl: "Forventet romtemperatur", nowLbl: "Nå",

      rulesH: "Prisreglene",
      rulesP: (r) => `Hver dag sorteres timene i billige, vanlige, dyre og ekstreme. Ekstrem betyr minst ${r.ratio} × dagens snitt og minst ${r.min}, eller over ${r.abs} uansett. Et døgn der billigste og dyreste time skiller seg med mindre enn ${r.spread}, har ingenting å flytte: alle timene regnes som vanlige.`,
      rules: [
        ["De dyreste timene", (s) => `Døgnets ${s.n} dyreste timer senkes ${s.shift}° – aldri under ${s.safety}° (med mindre din egen kurve er lavere).`],
        ["Billig før dyrt", (s) => `Billige timer inntil ${s.look} t før dyre hever ${s.pre}°, så gulvet lagrer varme.`],
        ["Ekstrempris", (s) => `Varmen heves timene før og skrus av i selve timen – maks ${s.maxoff} t på rad, og ikke mens rommet er under ${s.safety}°.`],
        ["Beskyttet vindu", (s) => `${s.pf}–${s.pt} ignoreres prisen. Det er varmt når dere kommer hjem.`],
      ],
      strategyH: "Strategi per rom",
      strategies: [["Ignorer", "Prisen styrer ingenting"], ["Forsiktig", "Halv effekt"], ["Balansert", "Som beskrevet her"], ["Aggressiv", "Dobbel senking"]],

      awayH: "Når dere er borte",
      awayP: (s) => `Når alle er borte, senkes varmen til ${Math.abs(s.away)}° under komfort. Fra kl. ${s.until} varmes det opp igjen, så det er varmt når dere kommer hjem.`,
      awayMarks: [["1", "Alle har dratt", (s) => `${Math.abs(s.away)}° lavere.`], ["2", `Varmer opp igjen`, (s) => `Fra kl. ${s.until}, uansett om dere er hjemme ennå.`], ["3", "Hjemme", () => "Varmt når dere kommer."]],
      awayLbl: "borte",
      awayNotes: ["Drar dere etter den timen, senkes ingenting den dagen.", "Borte gjelder aldri inn i neste døgn.", "Borte og natt legges ikke sammen – det laveste gjelder.", "En person uten fungerende sporing telles ikke med."],

      editH: "Endre planen",
      edit: [
        ["Trykk «Endre døgn»", "og velg dagen du vil endre."],
        ["Dra over timene", "for å tegne en ny kurve. Temperatur og kostnad oppdateres mens du drar."],
        ["Velg hvor lenge", "Bare denne dagen, alle like ukedager, hverdager, helg eller alle dager."],
      ],
      chips: ["Bare i dag", "Alle mandager", "Hverdager", "Helg", "Alle dager"],
      editNote: "«Tilbake til fast plan» fjerner en endring som bare gjaldt én dag.",

      manualH: "Når du tar over",
      manualP: "Stiller du termostaten selv – i appen eller på veggen – trekker Heat Planner seg unna. Pausen varer til døgnkurven din endrer seg neste gang (eller tre timer, om den ikke endrer seg). Da tar planen over igjen av seg selv.",
      manualMarks: [["1", "Du skrur opp", "Kortet viser «Manuell»."], ["2", "Neste endring i kurven", "Planen tar over igjen."]],
      pauseLbl: "pause",
      manualTip: "Vil du ha planen tilbake med en gang: trykk «Gjenoppta plan». Vil du styre et rom selv over tid: slå av «Automatisk styring» i innstillingene.",

      safetyH: "Sikkerhetsnett",
      safety: [
        (s) => [`${s.safety}°`, "Aldri av under", "Er rommet kaldere, skrus varmen aldri av for å spare – og ingen pris- eller effektkutt setter den lavere (med mindre din egen kurve er lavere)."],
        (s) => [`${s.maxoff} t`, "Maks av på rad", "Lange perioder med ekstrempris deles opp."],
        () => ["mdi:shield-check", "Feiler noe?", "Feiler planen for et rom den hadde skrudd av, får rommet vanlig varme tilbake. Uten priser følger planen bare døgnkurven."],
        () => ["mdi:pencil-off", "Skriver lite", "Termostaten får bare beskjed når verdien faktisk må endres – og en utilgjengelig termostat røres ikke."],
      ],

      learnH: "Hva den lærer",
      learnP: (t) => `Har du tillatt læring fra historikken, leser Heat Planner de siste 30 dagene fra Home Assistant hver natt kl. ${t} og finner ut hvor fort hvert rom varmes og kjøles, hvor mye sol og et tregt gulv betyr, og hvor mye strøm varmen bruker. Ingenting sendes ut av huset.`,
      learnLegend: ["Målt", "Modellen"], heatOn: "varme på",
      learnZone: (z) => {
        if (z.percent) return `<b>${esc(z.name)}</b> styres i prosent (ingen gulvføler)` + (z.kw ? ` · ${z.kw} kW ${z.kwSrc === "measured" ? "målt" : "anslått"}` : "") + ".";
        return `<b>${esc(z.name)}</b>: varmes ${z.rate}°/t · holder på varmen i ${z.tau} t` + (z.kw ? ` · ${z.kw} kW ${z.kwSrc === "measured" ? "målt" : "anslått"}` : "") + (z.learned ? " · lært av historikken" : " · foreløpig anslag") + ".";
      },

      moneyH: "Kostnad og besparelse",
      money: [
        ["Kostnad", (x) => `Forventet strømforbruk × det dere faktisk betaler per time${x ? ` (${x})` : ""}, over timene som har publisert pris.`],
        ["Besparelse", () => "Sammenlignet med den samme døgnkurven uten prisstyring. Det er et anslag, ikke en måling."],
        ["Fastpris?", () => "Har dere fastpris eller Norgespris, er det lite å hente på å flytte forbruk. Sett strategien til «Ignorer» – resten av planen virker som før."],
      ],

      setH: "Innstillinger nå",
      setP: "Disse verdiene gjelder akkurat nå. Endre dem under «Innstillinger» i planen.",
      setRows: [
        ["comfort", "Komforttemperatur"], ["strategy", "Prisstrategi"], ["price_shift", "Senk i dyre timer"], ["preheat", "Hev før dyre timer"],
        ["extreme_off", "Av ved ekstrempris"], ["safety_min_temp", "Aldri av under"], ["away_offset", "Senk når ingen er hjemme"],
        ["away_until_hour", "Varmt igjen fra kl."], ["protected", "Beskyttet vindu"],
      ],
      yes: "Ja", no: "Nei",
      strategyNames: ["Ignorer", "Forsiktig", "Balansert", "Aggressiv"],

      faqH: "Spørsmål",
      faq: [
        ["Et rom følger ikke planen.", "Står det «Manuell» i planen, har noen stilt termostaten. Trykk «Gjenoppta plan», eller vent til neste planendring."],
        ["Planen viser bare priser for i dag.", "Morgendagens priser kommer rundt kl. 13 og hentes automatisk. Til da følger timene etter midnatt bare døgnkurven."],
        ["Hvorfor er badet i prosent?", "Termostaten har ingen gulvføler og står i regulatormodus. Prosenten er hvor stor del av tiden varmen er på."],
        ["Vi har fått nye termostater.", "Innstillinger → Enheter og tjenester → Heat Planner → Konfigurer → «Finn termostater»."],
        ["Kan jeg bruke dette i automasjoner eller Siri?", "Ja. Hvert rom har entiteter for mål, modus, forventet temperatur, kostnad, besparelse, automatisk styring, komforttemperatur og strategi. For hele huset finnes prisnivå, effektiv pris og faktisk besparelse – og, med strømmåler for hele huset, effekttrinn og månedens toppsnitt."],
        ["Sendes data ut av huset?", "Nei. Planlegging og læring skjer i Home Assistant. Bare hvis du selv bruker AI-oppsettet, sendes en beskrivelse av huset og listen over termostater."],
      ],
      lang: "Språk",
    },

    en: {
      title: "How the heating plan works",
      lead: "Heat Planner makes an hour-by-hour plan for the next 36 hours for every room – and sets the thermostats to follow it. Here is what shapes the plan, and how you take control yourself.",
      tocLabel: "Contents",
      toc: { day: "A normal day", colors: "Colours", rules: "Price rules", optimizer: "Optimiser", tariff: "Your contract", peak: "Capacity tariff", away: "When you're away", arrival: "Arrival", edit: "Changing the plan", manual: "Taking over", feedback: "Too cold / warm", safety: "Safety", learn: "What it learns", confidence: "Confidence", money: "Cost", settings: "Settings", faq: "Questions" },

      optH: "The optimiser",
      optP: "With “Optimise (all rooms)” on, Heat Planner doesn't just apply the rules above. It simulates each room with its own model and picks every hour's target for the lowest cost – energy × price, plus a penalty for being colder than your curve (and, with a capacity tariff, for going over the cap). It only pre-heats when storing heat in the floor is cheaper than heating in the expensive hours, extra losses included.",
      optBounds: [
        ["Lowers at most", (s) => `${s.shiftMax}° below your curve – “Lower in expensive hours” × the strategy.`],
        ["Raises at most", (s) => `${s.raiseMax}° – up to twice “Raise before expensive hours”.`],
        ["Off only in extreme hours", (s) => `With “Turn off at extreme prices” on, and at most ${s.maxoff} h in a row.`],
        ["Never below", (s) => `${s.safety}° when it lowers – the safety temperature.`],
        ["Slow floors", () => "Comfort is judged when the heat arrives: a floor that responds after an hour is pre-heated an hour earlier."],
        ["Falls back to the rules", () => "Without a room temperature, without prices, or with the strategy set to “Ignore”."],
      ],
      optLive: (on) => (on ? "The optimiser is <b>on</b> in your home." : "The optimiser is <b>off</b> in your home – the price rules above apply."),

      tariffH: "Your electricity contract",
      tariffP: "How much it pays to move heat in time depends on what you actually pay. The bars show the same day on three different contracts.",
      tariffs: [
        ["Spot price", "The price follows the market hour by hour. Big differences between hours – this is where price control helps most."],
        ["Electricity support", "The state covers 90 % of the price above a threshold. Peaks are flattened, so there is less to gain from moving consumption."],
        ["Norgespris", "One price all day. Moving consumption saves almost nothing – the plan keeps you comfortable and watches the power peaks instead."],
      ],
      thresholdLbl: "threshold", fixedLbl: "fixed price",
      tariffLive: (l) => `You have: <b>${l}</b>.`,

      peakH: "Capacity tariff",
      peakP: "Part of the grid fee is a fixed monthly amount. The step is set by the average of the month's three highest hours, from three different days. Heat Planner sets a cap for every hour so heating doesn't push you into the next step.",
      peakMarks: [
        ["1", "Three highest days", "Their average sets the step."],
        ["2", "Today's cap", "Heating is spread so each hour stays below it."],
        ["3", "Next step", "Costs more every month for the rest of the month."],
      ],
      capLbl: "today's cap", stepLbl: "next step",
      peakNotes: [
        "Needs a whole-house power meter (Tibber Pulse, HAN reader …) chosen under Configure. Without one, capacity limiting stays off.",
        "The cap covers the whole home – water heater, EV and stove count too.",
        "When the cap would be exceeded, rooms are lowered a little in those hours, lowest priority first. Protected hours and rooms near their safety temperature are never cut, and cuts that wouldn't lower that day's peak are dropped.",
        "A live guard watches the meter: if the current hour is heading over the cap, rooms are set back until the hour ends.",
      ],
      peakLive: (p) => `Now: ${[p.step ? `step <b>${p.step}</b>` : "", p.avg ? `average top 3 <b>${p.avg} kW</b>` : "", p.cap ? `cap today <b>${p.cap} kW</b>` : ""].filter(Boolean).join(" · ")}.`,

      arrivalH: "Learning when you get home",
      arrivalP: "With “Warm again from” set to Auto, Heat Planner watches when the house goes from empty to someone coming home – weekday by weekday, over the last six weeks. It picks an early estimate and starts warming as long before as the room needs.",
      arrivalMarks: [
        ["1", "Arrivals", "Each dot is a day the first person got home."],
        ["2", "Early estimate", "One of the earliest times, so you rarely come home to a cold house."],
        ["3", "Warms from", "The estimate minus the time the room needs, between 10:00 and 18:00."],
      ],
      arrivalNotes: ["Short errands under two hours and arrivals at night don't count.", "It needs at least three days before trusting a weekday. Until then weekdays or weekends are pooled – otherwise the fixed hour is used.", "Phones without working tracking are ignored."],
      arrivalLive: (t) => `In your home: usually home around <b>${t}</b>.`,
      expLbl: "estimate", warmLbl: "warming up",

      fbH: "Too cold or too warm?",
      fbP: "Tap “Too cold” or “Too warm” in the plan. Heat Planner nudges one thing a little – based on what the plan was doing at that moment – and tells you what it changed.",
      fbDemo: ["Living room is 19.5°. Saving at 20° through an expensive hour.", "Too cold", "Too warm", "Lowering less in expensive hours (1° → 0.5°)."],
      fbRules: [
        ["Too cold in a saving or off hour", "Lowers 0.5° less in expensive hours. If the room was near “Never off below”, that is raised 0.5° too."],
        ["Too cold while nobody was home", "Lowers 0.5° less when you're away."],
        ["Too cold in a lowered hour of your own curve", "Nothing changes – change the curve with “Edit day”."],
        ["Too cold otherwise", "Comfort temperature goes up 0.5°."],
        ["Too warm", "Pre-heats 0.5° less if it was pre-heating – otherwise comfort goes down 0.5°."],
      ],
      fbNote: "Percent rooms use 5 % steps and move their whole day curve instead of the comfort temperature. Because anyone can press, the effect is bounded: the same button within ten minutes counts once, at most 8 changes a day, and comfort stays within ±2° of where it was that morning – and above the safety temperature.",

      confH: "How sure is the model?",
      confP: "The room model compares what it would have predicted one hour ahead with what the thermometer actually showed. The plan uses the same model either way – the label tells you how far to trust the dashed line.",
      conf: [["good", "Good", "Under ±0.3°. The expected temperature is reliable."], ["fair", "Fair", "±0.3–0.6°. Expect the room to land a little off."], ["uncertain", "Uncertain", "±0.6° or more. A learned model this far off is blended halfway with the estimate from your home description, and Heat Planner raises an alert."]],
      confNotes: ["Sun through the windows and a floor that responds with a delay are included once the model has learned them.", "“Settings” in the plan shows how well the model fits each room."],
      confLbl: "error",
      confZone: (z) => `<b>${esc(z.name)}</b>: ${z.q}` + (z.rmse ? ` · ±${z.rmse}°` : "") + (z.solar ? " · sun" : "") + (z.lag ? ` · responds after ${z.lag} h` : ""),
      autoLbl: "Auto",
      awayAuto: " Set to Auto, the hour is learned from when you usually get home.",
      inputs: [
        ["mdi:chart-bell-curve-cumulative", "Day curve", "How warm you want it"],
        ["mdi:lightning-bolt", "Electricity price", "Hour by hour"],
        ["mdi:home-account", "Who's home", "From your phones"],
        ["mdi:home-thermometer", "The room", "How fast it heats and cools"],
      ],
      plan: ["mdi:calendar-clock", "Plan", "36 hours, every room"],
      thermo: ["mdi:thermostat", "Thermostats", "Set automatically"],
      principle: "The price decides <b>when</b> to heat. You decide <b>how warm</b>.",
      live: (z, src) => `Controlling <b>${z}</b>` + (src ? ` with prices from <b>${src}</b>.` : "."),

      dayH: "A normal day",
      dayP: "An example with a comfort temperature of 21°. The coloured ribbon is the target, the dashed line is the expected room temperature, and the bars at the bottom are the electricity price.",
      dayMarks: [
        ["1", "Night", "2° lower while you sleep."],
        ["2", "Morning", "Extra warm 05–08, before you get up."],
        ["3", "Protected", "13–15 ignores the price, so it's warm when you get home."],
        ["4", "Pre-heat", "The floor stores heat before the price peak."],
        ["5", "Expensive hour", "1° lower – barely noticeable."],
        ["6", "Extreme price", "Off for an hour. The floor keeps the warmth."],
      ],
      protectedLbl: "protected", offLbl: "off",

      colorsH: "Colours",
      colorsP: "The ribbon changes colour with what the plan is doing. Point at or tap an hour in the plan to see exactly why.",
      modes: [
        ["normal", "Normal", "Follows the day curve"],
        ["comfort", "Comfort", "Extra warm, e.g. mornings"],
        ["setback", "Lowered", "Night or nobody home"],
        ["preheat", "Pre-heat", "Stores heat before expensive hours"],
        ["save", "Saving", "A bit lower in an expensive hour"],
        ["off", "Off", "Extremely expensive hour"],
      ],
      levelsH: "Electricity price",
      levels: [["c", "Cheap"], ["n", "Normal"], ["e", "Expensive"], ["x", "Extreme"]],
      predLbl: "Expected room temperature", nowLbl: "Now",

      rulesH: "Price rules",
      rulesP: (r) => `Every day the hours are sorted into cheap, normal, expensive and extreme. Extreme means at least ${r.ratio} × the day's average and at least ${r.min}, or above ${r.abs} regardless. A day whose cheapest and dearest hours differ by less than ${r.spread} has nothing worth moving: every hour counts as normal.`,
      rules: [
        ["Most expensive hours", (s) => `The day's ${s.n} most expensive hours are lowered by ${s.shift}° – never below ${s.safety}° (unless your own curve is lower).`],
        ["Cheap before expensive", (s) => `Cheap hours up to ${s.look} h before expensive ones are raised by ${s.pre}°, so the floor stores heat.`],
        ["Extreme price", (s) => `Heat is raised in the hours before and turned off in the hour itself – at most ${s.maxoff} h in a row, and not while the room is below ${s.safety}°.`],
        ["Protected window", (s) => `${s.pf}–${s.pt} ignores the price. It's warm when you get home.`],
      ],
      strategyH: "Strategy per room",
      strategies: [["Ignore", "Price steers nothing"], ["Gentle", "Half effect"], ["Balanced", "As described here"], ["Aggressive", "Double lowering"]],

      awayH: "When you're away",
      awayP: (s) => `When everyone is away, heating drops to ${Math.abs(s.away)}° below comfort. From ${s.until} it warms up again, so it's warm when you get home.`,
      awayMarks: [["1", "Everyone left", (s) => `${Math.abs(s.away)}° lower.`], ["2", "Warming up again", (s) => `From ${s.until}, whether you're home yet or not.`], ["3", "Home", () => "Warm when you arrive."]],
      awayLbl: "away",
      awayNotes: ["Leaving after that hour doesn't lower anything that day.", "Away never carries over into the next day.", "Away and night are not added together – the lowest applies.", "A person whose tracking isn't working is ignored."],

      editH: "Changing the plan",
      edit: [
        ["Tap “Edit day”", "and pick the day you want to change."],
        ["Drag across the hours", "to draw a new curve. Temperature and cost update while you drag."],
        ["Choose how long", "Only this day, every same weekday, weekdays, weekend or all days."],
      ],
      chips: ["Only today", "Every Monday", "Weekdays", "Weekend", "All days"],
      editNote: "“Back to schedule” removes a change that applied to a single day.",

      manualH: "Taking over",
      manualP: "If you set the thermostat yourself – in its app or on the wall – Heat Planner steps back. The pause lasts until your day curve next changes (or three hours, if it doesn't change). Then the plan takes over again on its own.",
      manualMarks: [["1", "You turn it up", "The plan shows “Manual”."], ["2", "Next change in the curve", "The plan takes over again."]],
      pauseLbl: "pause",
      manualTip: "Want the plan back right away? Tap “Resume plan”. To run a room by hand for a while, turn off “Automatic control” in its settings.",

      safetyH: "Safety net",
      safety: [
        (s) => [`${s.safety}°`, "Never off below", "If the room is colder, heating is never turned off to save money – and no price or peak cut sets it lower (unless your own curve is)."],
        (s) => [`${s.maxoff} h`, "Max off in a row", "Long runs of extreme prices are broken up."],
        () => ["mdi:shield-check", "Something fails?", "If planning fails for a room it had turned off, the room gets its scheduled heat back. Without prices the plan simply follows your day curve."],
        () => ["mdi:pencil-off", "Writes little", "The thermostat is only told when the value actually needs to change – and an unavailable one is left alone."],
      ],

      learnH: "What it learns",
      learnP: (t) => `If you allowed learning from history, Heat Planner reads the last 30 days from Home Assistant every night at ${t} to learn how fast each room heats and cools, how much the sun and a slow floor matter, and how much power the heating uses. Nothing leaves your home.`,
      learnLegend: ["Measured", "Model"], heatOn: "heating on",
      learnZone: (z) => {
        if (z.percent) return `<b>${esc(z.name)}</b> is controlled in percent (no floor sensor)` + (z.kw ? ` · ${z.kw} kW ${z.kwSrc === "measured" ? "measured" : "estimated"}` : "") + ".";
        return `<b>${esc(z.name)}</b>: heats ${z.rate}°/h · keeps its warmth for ${z.tau} h` + (z.kw ? ` · ${z.kw} kW ${z.kwSrc === "measured" ? "measured" : "estimated"}` : "") + (z.learned ? " · learned from history" : " · estimate so far") + ".";
      },

      moneyH: "Cost and savings",
      money: [
        ["Cost", (x) => `Expected energy use × what you actually pay each hour${x ? ` (${x})` : ""}, over the hours that have a published price.`],
        ["Savings", () => "Compared with the same day curve without price control. It is an estimate, not a measurement."],
        ["Fixed price?", () => "On a fixed-price contract there is little to gain from moving consumption. Set the strategy to “Ignore” – the rest of the plan works as before."],
      ],

      setH: "Settings right now",
      setP: "These values apply right now. Change them under “Settings” in the plan.",
      setRows: [
        ["comfort", "Comfort temperature"], ["strategy", "Price strategy"], ["price_shift", "Lower in expensive hours"], ["preheat", "Raise before expensive hours"],
        ["extreme_off", "Off at extreme prices"], ["safety_min_temp", "Never off below"], ["away_offset", "Lower when nobody is home"],
        ["away_until_hour", "Warm again from"], ["protected", "Protected window"],
      ],
      yes: "Yes", no: "No",
      strategyNames: ["Ignore", "Gentle", "Balanced", "Aggressive"],

      faqH: "Questions",
      faq: [
        ["A room isn't following the plan.", "If the plan says “Manual”, someone set the thermostat. Tap “Resume plan”, or wait for the next plan change."],
        ["The plan only shows today's prices.", "Tomorrow's prices arrive around 13:00 and are fetched automatically. Until then the hours after midnight just follow the day curve."],
        ["Why is a room in percent?", "Its thermostat has no floor sensor and runs in regulator mode. The percent is how much of the time the heat is on."],
        ["We have new thermostats.", "Settings → Devices & services → Heat Planner → Configure → “Find thermostats”."],
        ["Can I use this in automations or voice assistants?", "Yes. Each room has entities for target, mode, expected temperature, cost, savings, automatic control, comfort temperature and strategy. For the whole home there are price level, effective price and realised savings – and, with a whole-house meter, the capacity step and the month's peak average."],
        ["Does any data leave my home?", "No. Planning and learning happen in Home Assistant. Only if you use the AI setup is a description of the house and the list of thermostats sent."],
      ],
      lang: "Language",
    },
  };

  const CSS = `
:host { display:block;
  --hp-comfort:#E9A23B; --hp-normal:color-mix(in srgb, var(--primary-text-color) 45%, transparent); --hp-setback:#5B6CD9; --hp-preheat:#F0644B; --hp-save:#1FA896; --hp-off:#8C96A8;
  --hp-cheap:#3FA971; --hp-pnormal:color-mix(in srgb, var(--secondary-text-color) 35%, transparent); --hp-exp:color-mix(in srgb, var(--primary-text-color) 62%, transparent); --hp-ext:#E0464E;
  --hp-ink: var(--primary-text-color); --hp-soft: var(--secondary-text-color); --hp-line: var(--divider-color, rgba(127,127,127,.25));
  --hp-tint: color-mix(in srgb, var(--hp-ink) 4.5%, transparent);
  --hp-ext-ink: color-mix(in srgb, var(--hp-ext) 70%, var(--hp-ink));
  color:var(--hp-ink); }
* { box-sizing:border-box; }
.wrap { max-width:980px; margin:0 auto; }
.top { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }
h1 { font-size:clamp(24px, 3.4vw, 34px); line-height:1.12; letter-spacing:-0.02em; font-weight:600; margin:2px 0 10px; }
.lead { font-size:clamp(15px, 1.8vw, 17px); line-height:1.5; color:var(--hp-soft); max-width:62ch; margin:0; }
.seg { display:flex; border:1px solid var(--hp-line); border-radius:999px; overflow:hidden; flex:none; }
.seg button { all:unset; cursor:pointer; font-size:12.5px; padding:5px 11px; color:var(--hp-soft); }
.seg button[aria-pressed=true] { background:var(--hp-ink); color:var(--card-background-color, #fff); }
.toc { display:flex; flex-wrap:wrap; gap:6px; margin:22px 0 4px; }
.toc button { all:unset; box-sizing:border-box; cursor:pointer; font-size:12.5px; padding:5px 11px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); display:inline-flex; align-items:center; }
.toc button:hover { color:var(--hp-ink); border-color:var(--hp-ink); }
button:focus-visible, summary:focus-visible, h2:focus-visible { outline:2px solid var(--primary-color, #03a9f4); outline-offset:2px; border-radius:6px; }
h2:focus:not(:focus-visible) { outline:none; }
@media (pointer: coarse) { .toc button, .seg button { min-height:44px; } summary { min-height:44px; align-items:center; } }

.flow { display:grid; grid-template-columns: 1fr auto 1fr auto 1fr; align-items:center; gap:14px; margin:28px 0 10px; }
.inputs { display:grid; gap:8px; }
.node { display:flex; gap:10px; align-items:center; padding:10px 12px; border:1px solid var(--hp-line); border-radius:14px; background:var(--card-background-color, transparent); }
.node ha-icon { --mdc-icon-size:22px; color:var(--hp-ink); flex:none; }
.node b { display:block; font-size:14px; font-weight:600; }
.node span { display:block; font-size:12.5px; color:var(--hp-soft); }
.node.big { padding:18px 16px; border-radius:18px; background:var(--hp-tint); border-color:transparent; }
.node.big ha-icon { --mdc-icon-size:30px; }
.node.big b { font-size:16px; }
.arrow { width:34px; height:2px; background:var(--hp-line); position:relative; }
.arrow::after { content:""; position:absolute; right:-1px; top:-4px; border:5px solid transparent; border-left:7px solid var(--hp-line); border-right:0; }
.principle { margin:18px 0 0; font-size:clamp(16px, 2vw, 19px); }
.live { margin:6px 0 0; color:var(--hp-soft); font-size:13.5px; }

section { padding:38px 0 6px; border-top:1px solid var(--hp-line); margin-top:34px; scroll-margin-top:70px; }
.eyebrow { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--hp-soft); margin:0 0 6px; font-variant-numeric:tabular-nums; }
h2 { font-size:clamp(20px, 2.6vw, 26px); letter-spacing:-0.015em; font-weight:600; margin:0 0 8px; }
p.d { color:var(--hp-soft); max-width:64ch; line-height:1.55; margin:0 0 18px; font-size:14.5px; }
.ill { width:100%; height:auto; display:block; overflow:visible; }
.scroll { overflow-x:auto; overflow-y:hidden; margin:0 -4px; padding:0 4px; -webkit-overflow-scrolling:touch; }
.scroll .ill { min-width:600px; }
.ill .gl { stroke:var(--hp-line); }
.ill .yl, .ill .xl { fill:var(--hp-soft); font-size:10.5px; font-variant-numeric:tabular-nums; }
.ill .yl { text-anchor:end; } .ill .xl { text-anchor:middle; }
.ill .pred { fill:none; stroke:var(--hp-ink); stroke-width:1.5; stroke-dasharray:4 4; opacity:.65; }
.ill .act { fill:none; stroke:var(--hp-comfort); stroke-width:3; stroke-linejoin:round; }
.ill .dot { fill:var(--hp-ink); opacity:.55; }
.ill .band { fill:var(--hp-tint); }
.ill .band.away { fill:color-mix(in srgb, var(--hp-setback) 9%, transparent); }
.ill .band.pause { fill:color-mix(in srgb, var(--hp-comfort) 11%, transparent); }
.ill .bl { fill:var(--hp-soft); font-size:10.5px; letter-spacing:.04em; text-transform:uppercase; }
.ill .hl { stroke:var(--hp-ext); stroke-dasharray:3 3; opacity:.8; }
.ill .hlt { fill:var(--hp-ext-ink); font-size:10.5px; text-anchor:end; }
.ill .offt { fill:var(--hp-soft); font-size:10px; text-anchor:middle; }
.ill .nowl { stroke:var(--hp-ink); stroke-dasharray:2 3; opacity:.55; }
.ill .mk circle { fill:var(--hp-ink); }
.ill .mk text { fill:var(--card-background-color, #fff); font-size:11px; font-weight:600; text-anchor:middle; }

.marks { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:14px 22px; margin:20px 0 0; padding:0; list-style:none; }
.marks li { display:grid; grid-template-columns:22px 1fr; gap:10px; align-items:start; font-size:13.5px; line-height:1.45; color:var(--hp-soft); }
.marks li b { color:var(--hp-ink); display:block; font-weight:600; }
.num { width:22px; height:22px; border-radius:50%; background:var(--hp-ink); color:var(--card-background-color, #fff); font-size:11.5px; font-weight:600; display:grid; place-items:center; }

.swatches { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:10px; }
.sw { display:grid; grid-template-columns:38px 1fr; gap:12px; align-items:center; padding:10px 12px; border-radius:12px; background:var(--hp-tint); }
.sw .chip { height:30px; border-radius:6px; position:relative; overflow:hidden; }
.sw .chip::before { content:""; position:absolute; inset:0; background:var(--c); opacity:.17; }
.sw .chip::after { content:""; position:absolute; left:0; right:0; top:7px; height:4px; border-radius:2px; background:var(--c); }
.sw .chip.off::before { opacity:1; top:auto; height:12px; background:repeating-linear-gradient(135deg, var(--c) 0 1.6px, transparent 1.6px 3.6px); }
.sw .chip.off::after { top:auto; bottom:12px; height:3px; }
.sw .chip.normal::before { opacity:0; }
.sw b { display:block; font-size:13.5px; font-weight:600; } .sw span { font-size:12.5px; color:var(--hp-soft); }
.sub { font-size:13px; font-weight:600; margin:22px 0 10px; }
.prices { display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; font-size:13px; color:var(--hp-soft); }
.prices i { display:inline-block; width:10px; height:16px; border-radius:2px; vertical-align:-3px; margin-right:7px; }
.prices .dash { width:20px; height:0; border-top:1.5px dashed var(--hp-ink); opacity:.65; vertical-align:4px; border-radius:0; }
.prices .nowi { width:0; height:16px; border-left:1.5px dashed var(--hp-ink); opacity:.6; border-radius:0; margin-right:9px; }

.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 340px), 1fr)); gap:14px; }
.card { border:1px solid var(--hp-line); border-radius:16px; padding:14px 14px 12px; display:flex; flex-direction:column; gap:6px; }
.card h3 { margin:0; font-size:15px; font-weight:600; }
.card p { margin:0; color:var(--hp-soft); font-size:13.5px; line-height:1.45; }
.card .ill { margin-top:4px; }
.strat { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:0; border:1px solid var(--hp-line); border-radius:14px; overflow:hidden; margin-top:6px; }
.strat div { padding:12px 14px; border-right:1px solid var(--hp-line); }
.strat div:last-child { border-right:0; }
.strat b { display:block; font-size:13.5px; } .strat span { font-size:12.5px; color:var(--hp-soft); }
.meter { display:flex; gap:3px; margin:8px 0 2px; } .meter i { width:16px; height:5px; border-radius:3px; background:var(--hp-line); } .meter i.on { background:var(--hp-save); }

.notes { margin:16px 0 0; padding:0; list-style:none; display:grid; gap:8px; }
.notes li { font-size:13.5px; color:var(--hp-soft); padding-left:18px; position:relative; line-height:1.45; }
.notes li::before { content:""; position:absolute; left:2px; top:8px; width:6px; height:6px; border-radius:50%; background:var(--hp-setback); }

.steps { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:18px; margin-top:18px; }
.step { display:grid; grid-template-columns:22px 1fr; gap:10px; font-size:13.5px; line-height:1.45; color:var(--hp-soft); }
.step b { color:var(--hp-ink); display:block; font-weight:600; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.chips span { font-size:12px; padding:4px 10px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); }
.chips span.on { color:var(--hp-ink); border-color:var(--hp-ink); }
.tip { margin:16px 0 0; padding:12px 14px; border-radius:12px; background:var(--hp-tint); font-size:13.5px; line-height:1.5; }

.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; }
.tile { padding:16px; border-radius:16px; background:var(--hp-tint); }
.tile .big { font-size:34px; font-weight:600; letter-spacing:-0.02em; line-height:1; font-variant-numeric:tabular-nums; }
.tile ha-icon { --mdc-icon-size:34px; display:block; height:34px; }
.tile b { display:block; margin:10px 0 4px; font-size:14px; }
.tile span { font-size:13px; color:var(--hp-soft); line-height:1.45; }

.learn { display:grid; grid-template-columns:minmax(0, 1.2fr) minmax(0, 1fr); gap:22px; align-items:center; }
.zl { margin:0; padding:0; list-style:none; display:grid; gap:10px; }
.zl li { font-size:13.5px; color:var(--hp-soft); line-height:1.45; padding:10px 12px; border-radius:12px; background:var(--hp-tint); }
.zl li b { color:var(--hp-ink); }

.money { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; }
.money div { padding:14px; border-left:3px solid var(--hp-cheap); background:var(--hp-tint); border-radius:4px 12px 12px 4px; }
.money div:last-child { border-left-color:var(--hp-comfort); }
.money.bounds div, .money.bounds div:last-child { border-left-color:var(--hp-save); }
.money b { display:block; font-size:14px; margin-bottom:4px; } .money span { font-size:13.5px; color:var(--hp-soft); line-height:1.45; }

.tbl { width:100%; border-collapse:collapse; font-size:13.5px; }
.tblwrap { overflow-x:auto; }
.tbl th, .tbl td { padding:9px 10px; text-align:left; border-bottom:1px solid var(--hp-line); white-space:nowrap; }
.tbl th { font-weight:600; } .tbl td:first-child { color:var(--hp-soft); }
.tbl td.v { font-variant-numeric:tabular-nums; }

details { border-bottom:1px solid var(--hp-line); padding:12px 2px; }
summary { cursor:pointer; font-size:14.5px; font-weight:500; list-style:none; display:flex; justify-content:space-between; gap:12px; }
summary::-webkit-details-marker { display:none; }
summary::after { content:"+"; color:var(--hp-soft); font-weight:400; }
details[open] summary::after { content:"–"; }
details p { margin:8px 0 2px; color:var(--hp-soft); font-size:13.5px; line-height:1.5; max-width:70ch; }

.ill .capl { stroke:var(--hp-ext); stroke-dasharray:4 3; stroke-width:1.2; }
.ill .capt { fill:var(--hp-ext-ink); font-size:10.5px; text-anchor:end; }
.ill .stepl { stroke:var(--hp-ink); opacity:.35; }
.ill .stept { fill:var(--hp-soft); font-size:10.5px; text-anchor:end; }
.ill .ghost { fill:none; stroke:var(--hp-line); stroke-width:1; }
.ill .warm { fill:color-mix(in srgb, var(--hp-comfort) 14%, transparent); }
.ill .estl { stroke:var(--hp-ink); stroke-width:1.5; }
.ill .rowl { fill:var(--hp-soft); font-size:10.5px; text-anchor:end; }
.ill .adot { fill:var(--hp-setback); opacity:.75; }
.ill .cband { fill:color-mix(in srgb, var(--hp-save) 14%, transparent); }
.live2 { margin:12px 0 0; color:var(--hp-soft); font-size:13.5px; }
.fbdemo { border:1px solid var(--hp-line); border-radius:16px; padding:14px 16px; max-width:520px; }
.fbdemo .s { font-size:16px; line-height:1.35; margin:0 0 8px; }
.fbdemo .c { display:flex; gap:6px; }
.fbdemo .c span { font-size:12px; padding:4px 10px; border-radius:999px; border:1px solid var(--hp-line); color:var(--hp-soft); }
.fbdemo .c span.on { color:var(--hp-ink); border-color:var(--hp-ink); }
.fbdemo .c i { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:6px; vertical-align:1px; }
.fbdemo .m { margin:10px 0 0; font-size:13px; color:var(--hp-ink); }
.fbgrid { display:grid; grid-template-columns:minmax(0, 1fr) minmax(0, 1fr); gap:22px; align-items:start; }
.conf { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px; margin-top:14px; }
.conf > div { padding:12px 14px; border-radius:12px; background:var(--hp-tint); }
.conf b { display:block; font-size:13.5px; } .conf span { font-size:12.5px; color:var(--hp-soft); line-height:1.45; }
.conf .meter { margin:0 0 6px; }

@media (max-width: 720px) {
  .fbgrid { grid-template-columns:1fr; }
  .flow { grid-template-columns:1fr; } .arrow { width:2px; height:20px; margin:0 auto; }
  .arrow::after { right:-4px; top:auto; bottom:-1px; border:5px solid transparent; border-top:7px solid var(--hp-line); border-bottom:0; }
  .learn { grid-template-columns:1fr; }
  .top { flex-direction:column-reverse; }
}
@media (prefers-reduced-motion: no-preference) { section { transition:opacity .2s; } }
`;

  const fmtN = (v, loc, d = 1) => (v == null ? "–" : Number(v).toLocaleString(loc, { maximumFractionDigits: d }));

  class HeatPlannerHelp extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._data = null;
      this._lang = null;
      try { this._lang = localStorage.getItem("heat-planner-help-lang"); } catch (e) { /* storage unavailable */ }
    }
    set hass(h) {
      const first = !this._hass;
      this._hass = h;
      if (first) { this._fetch(); this._render(); }
    }
    set language(l) { if (l && l !== "auto") { this._forced = l; this._render(); } }
    get loc() { return this._hass?.locale?.language || this._hass?.language || "en"; }
    get h12() {
      const tf = this._hass?.locale?.time_format;
      if (tf === "12") return true;
      if (tf === "24") return false;
      try {
        const hc = new Intl.DateTimeFormat(tf === "system" ? undefined : this.loc, { hour: "numeric" }).resolvedOptions().hourCycle;
        return hc === "h11" || hc === "h12";
      } catch (e) { return false; }
    }
    /** An hour (and minute) of the day in the user's 12/24-hour format. */
    clk(h, m = 0) {
      if (!this.h12) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
      return new Intl.DateTimeFormat(this.langKey === "nb" ? "nb-NO" : "en-US", { hour: "numeric", minute: "2-digit", hour12: true, timeZone: "UTC" }).format(Date.UTC(2000, 0, 1, h, m));
    }
    clkText(hhmm) { const m = /^(\d{1,2}):(\d{2})/.exec(String(hhmm || "")); return m ? this.clk(+m[1], +m[2]) : hhmm; }
    get langKey() {
      if (this._forced) return this._forced;
      if (this._lang) return this._lang;
      return /^(nb|no|nn)/i.test(this.loc) ? "nb" : "en";
    }
    async _fetch() {
      try {
        const d = await this._hass.connection.sendMessagePromise({ type: "heat_planner/get" });
        this._data = d.entries?.[0] || null;
        this._render();
      } catch (e) { /* integration not loaded – keep defaults */ }
    }
    _ctx() {
      const d = this._data, z0 = d?.zones?.find((z) => z.mode === "setpoint") || d?.zones?.[0];
      const s = z0?.settings || {};
      const r = d?.rules || {};
      const agg = Math.max(0, Math.min(2, s.price_aggressiveness ?? 1));
      const cur = d?.currency === "NOK" ? (this.langKey === "nb" ? "kr" : "NOK") : (d?.currency || "");
      const loc = this.langKey === "nb" ? "nb-NO" : "en-GB";
      const money = (v) => `${fmtN(v, loc, 2)} ${cur}/kWh`.trim();
      return {
        loc, cur,
        zones: d?.zones || [],
        src: d?.price_source,
        extra: d?.tariff?.label || (d?.price_extra ? money(d.price_extra) : null),
        s: {
          n: r.expensive_hours ?? 3, shift: fmtN(s.price_shift ?? 1, loc), pre: fmtN(s.preheat ?? 1, loc),
          safety: fmtN(s.safety_min_temp ?? 18, loc), pf: this.clk(s.protected_from_hour ?? 13),
          pt: this.clk((s.protected_to_hour ?? 15) % 24), away: s.away_offset ?? -4,
          until: this.clk(s.away_until_hour === -1 ? (d?.arrival?.warm_from ?? 13) : (s.away_until_hour ?? 13)),
          auto: s.away_until_hour === -1, maxoff: s.max_off_hours ?? 2, look: r.lookahead_hours ?? 3,
          shiftMax: fmtN((s.price_shift ?? 1) * agg, loc), raiseMax: fmtN(2 * (s.preheat ?? 1) * Math.min(agg, 1), loc),
        },
        tariff: d?.tariff?.label || null,
        peak: d?.peak ? {
          step: d.peak.step_label ? esc(d.peak.step_label) : null,
          avg: typeof d.peak.month_avg_kw === "number" ? fmtN(d.peak.month_avg_kw, loc) : null,
          cap: typeof d.peak.cap_kw === "number" ? fmtN(d.peak.cap_kw, loc) : null,
        } : null,
        arrival: d?.arrival?.expected ? this.clkText(d.arrival.expected) : null,
        optimize: d && "optimize" in d ? !!d.optimize : null,
        r: { ratio: fmtN(r.extreme_ratio ?? 1.5, loc), min: money(r.extreme_min_price ?? 2), abs: money(r.extreme_abs_price ?? 3), spread: money(r.min_spread ?? 0.15) },
      };
    }
    _setLang(l) {
      this._lang = l;
      try { localStorage.setItem("heat-planner-help-lang", l); } catch (e) { /* ignore */ }
      this._render();
    }
    _render() {
      const t = T[this.langKey] || T.en, c = this._ctx(), s = c.s;
      // sections are numbered in display order when assembled (see bottom)
      const sec = (id, _i, h, body) => ({ id, h, body });
      const node = ([icon, b, sp], big) => `<div class="node${big ? " big" : ""}"><ha-icon icon="${icon}"></ha-icon><div><b>${b}</b><span>${sp}</span></div></div>`;
      const marks = (list) => `<ul class="marks">${list.map(([k, b, p]) => `<li><span class="num">${k}</span><div><b>${b}</b>${p}</div></li>`).join("")}</ul>`;
      const zoneNames = c.zones.map((z) => esc(z.name));
      const zList = zoneNames.length > 1 ? zoneNames.slice(0, -1).join(", ") + (this.langKey === "nb" ? " og " : " and ") + zoneNames.at(-1) : zoneNames[0];

      const hero = `
        <div class="top">
          <div><h1>${t.title}</h1><p class="lead">${t.lead}</p></div>
          <div class="seg" role="group" aria-label="${t.lang}">
            <button data-lang="nb" aria-pressed="${this.langKey === "nb"}">Norsk</button>
            <button data-lang="en" aria-pressed="${this.langKey === "en"}">English</button>
          </div>
        </div>
        <div class="flow">
          <div class="inputs">${t.inputs.map((x) => node(x)).join("")}</div>
          <div class="arrow"></div>${node(t.plan, true)}<div class="arrow"></div>${node(t.thermo, true)}
        </div>
        <p class="principle">${t.principle}</p>
        ${zList ? `<p class="live">${t.live(zList, c.src ? esc(c.src) : null)}</p>` : ""}`;

      const day = sec("day", 1, t.dayH, `<p class="d">${t.dayP}</p>` + chart({
        ...DAY, start: 0, y: [17, 24.5], ticks: [18, 20, 22, 24], every: 3, now: null, offLabel: t.offLbl,
        bands: [{ from: 13, to: 15, label: t.protectedLbl }],
        marks: [{ k: "1", h: 2 }, { k: "2", h: 6 }, { k: "3", h: 13.5 - 0.5, y: 21 }, { k: "4", h: 15 }, { k: "5", h: 17 }, { k: "6", h: 18, y: 19.2 }],
        aria: t.dayH,
      }) + marks(t.dayMarks));

      const colors = sec("colors", 2, t.colorsH, `<p class="d">${t.colorsP}</p>
        <div class="swatches">${t.modes.map(([m, b, sp]) => `<div class="sw"><div class="chip ${m}" style="--c:${MODE[m]}"></div><div><b>${b}</b><span>${sp}</span></div></div>`).join("")}</div>
        <p class="sub">${t.levelsH}</p>
        <div class="prices">${t.levels.map(([k, l]) => `<span><i style="background:${LEVEL[k]}"></i>${l}</span>`).join("")}
          <span><i class="dash"></i>${t.predLbl}</span><span><i class="nowi"></i>${t.nowLbl}</span></div>`);

      const rules = sec("rules", 3, t.rulesH, `<p class="d">${t.rulesP(c.r)}</p><div class="cards">${t.rules.map(([h, f], i) => {
        const r = RULES[i];
        return `<div class="card"><h3>${h}</h3><p>${f(s)}</p>${chart({
          ...r, w: 320, ch: 92, ph: 22, y: r.y, ticks: [], every: 1, offLabel: t.offLbl,
          hline: r.hline ? { v: r.hline, label: `${s.safety}°` } : null,
          bands: r.band ? [{ from: r.band[0], to: r.band[1], label: t.protectedLbl }] : [], aria: h,
        })}</div>`;
      }).join("")}</div>
        <p class="sub">${t.strategyH}</p>
        <div class="strat">${t.strategies.map(([b, sp], i) => `<div><b>${b}</b><span>${sp}</span><div class="meter">${[0, 1, 2, 3].map((k) => `<i class="${k < [0, 1, 2, 4][i] ? "on" : ""}"></i>`).join("")}</div></div>`).join("")}</div>`);

      const away = sec("away", 4, t.awayH, `<p class="d">${t.awayP(s)}${s.auto ? t.awayAuto : ""}</p>` + chart({
        ...AWAY, start: 0, y: [15.5, 24], ticks: [16, 18, 20, 22], every: 3,
        bands: [{ from: 8, to: 16, label: t.awayLbl, kind: "away" }],
        marks: [{ k: "1", h: 8 }, { k: "2", h: 13 }, { k: "3", h: 16, y: 21.3 }], aria: t.awayH,
      }) + marks(t.awayMarks.map(([k, b, f]) => [k, b, f(s)])) + `<ul class="notes">${t.awayNotes.map((x) => `<li>${x}</li>`).join("")}</ul>`);

      const edit = sec("edit", 5, t.editH, this._editIll(t) + `<div class="steps">${t.edit.map(([b, p], i) => `<div class="step"><span class="num">${i + 1}</span><div><b>${b}</b>${p}${i === 2 ? `<div class="chips">${t.chips.map((x, k) => `<span class="${k === 2 ? "on" : ""}">${x}</span>`).join("")}</div>` : ""}</div></div>`).join("")}</div><p class="tip">${t.editNote}</p>`);

      const manual = sec("manual", 6, t.manualH, `<p class="d">${t.manualP}</p>` + chart({
        prices: MANUAL.prices, levels: MANUAL.levels, tgt: MANUAL.tgt, modes: MANUAL.modes, start: 14, y: [17, 24.5], ticks: [18, 20, 22, 24], every: 2,
        lines: [{ vals: MANUAL.actual, cls: "act" }], bands: [{ from: 3, to: 8, label: t.pauseLbl, kind: "pause" }],
        marks: [{ k: "1", h: 3, y: 23 }, { k: "2", h: 8, y: 19 }], aria: t.manualH,
      }) + marks(t.manualMarks) + `<p class="tip">${t.manualTip}</p>`);

      const safety = sec("safety", 7, t.safetyH, `<div class="tiles">${t.safety.map((f) => {
        const [big, b, sp] = f(s);
        return `<div class="tile">${big.startsWith("mdi:") ? `<ha-icon icon="${big}"></ha-icon>` : `<div class="big">${big}</div>`}<b>${b}</b><span>${sp}</span></div>`;
      }).join("")}</div>`);

      const zl = c.zones.map((z) => {
        const th = z.thermal || {};
        return `<li>${t.learnZone({
          name: z.name, percent: z.mode === "percent", rate: fmtN(th.gross_rate, c.loc), tau: fmtN(th.tau_hours, c.loc),
          learned: th.learned, kw: z.heater_kw ? fmtN(z.heater_kw, c.loc, 2) : null, kwSrc: z.heater_kw_source,
        })}</li>`;
      }).join("");
      const learn = sec("learn", 8, t.learnH, `<p class="d">${t.learnP(this.clk(3, 17))}</p><div class="learn">
        <div>${chart({ w: 420, ch: 140, prices: LEARN.prices, levels: LEARN.levels, tgt: Array(16).fill(null), modes: Array(16).fill("n"), start: 6, y: [18, 24], ticks: [18, 20, 22, 24], every: 2, ph: 0, dots: LEARN.dots, pred: LEARN.model, bands: [{ from: 0, to: 6, label: t.heatOn }], aria: t.learnH })}
          <div class="prices" style="margin-top:6px"><span><i style="width:8px;height:8px;border-radius:50%;background:var(--hp-ink);opacity:.55;vertical-align:0"></i>${t.learnLegend[0]}</span><span><i class="dash"></i>${t.learnLegend[1]}</span></div></div>
        ${zl ? `<ul class="zl">${zl}</ul>` : ""}</div>`);

      const money = sec("money", 9, t.moneyH, `<div class="money">${t.money.map(([b, f]) => `<div><b>${b}</b><span>${f(c.extra)}</span></div>`).join("")}</div>`);

      let settings = "";
      if (c.zones.length) {
        const val = (z, key) => {
          const st = z.settings || {}, pct = z.mode === "percent";
          const u = (v) => (pct ? `${fmtN(v, c.loc)} %` : `${fmtN(v, c.loc)}°`);
          switch (key) {
            case "comfort": return pct ? "–" : `${fmtN(st.comfort, c.loc)}°`;
            case "strategy": { const a = st.price_aggressiveness ?? 1; return t.strategyNames[a <= 0 ? 0 : a < 0.75 ? 1 : a <= 1.25 ? 2 : 3]; }
            case "price_shift": return u(st.price_shift);
            case "preheat": return u(st.preheat);
            case "extreme_off": return st.extreme_off ? t.yes : t.no;
            case "safety_min_temp": return `${fmtN(st.safety_min_temp, c.loc)}°`;
            case "away_offset": return u(st.away_offset);
            case "away_until_hour": return st.away_until_hour === -1 ? t.autoLbl : this.clk(st.away_until_hour);
            case "protected": return `${this.clk(st.protected_from_hour)}–${this.clk(st.protected_to_hour % 24)}`;
            default: return "–";
          }
        };
        settings = sec("settings", 10, t.setH, `<p class="d">${t.setP}</p><div class="tblwrap"><table class="tbl"><thead><tr><th></th>${c.zones.map((z) => `<th>${esc(z.name)}</th>`).join("")}</tr></thead><tbody>${t.setRows.map(([k, l]) => `<tr><td>${l}</td>${c.zones.map((z) => `<td class="v">${val(z, k)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      }

      const faq = sec("faq", c.zones.length ? 11 : 10, t.faqH, t.faq.map(([q, a]) => `<details><summary>${q}</summary><p>${a}</p></details>`).join(""));

      const tariff = sec("tariff", 0, t.tariffH, `<p class="d">${t.tariffP}</p><div class="cards" style="grid-template-columns:repeat(auto-fit, minmax(min(100%, 230px), 1fr))">${t.tariffs.map(([h, p], i) =>
        `<div class="card"><h3>${h}</h3><p>${p}</p>${this._tariffIll(i, t)}</div>`).join("")}</div>` +
        (c.tariff ? `<p class="live2">${t.tariffLive(esc(c.tariff))}</p>` : ""));

      const peak = sec("peak", 0, t.peakH, `<p class="d">${t.peakP}</p>` + this._peakIll(t) + marks(t.peakMarks) +
        `<ul class="notes">${t.peakNotes.map((x) => `<li>${x}</li>`).join("")}</ul>` +
        (c.peak && (c.peak.step || c.peak.avg || c.peak.cap) ? `<p class="live2">${t.peakLive(c.peak)}</p>` : ""));

      const arrival = sec("arrival", 0, t.arrivalH, `<p class="d">${t.arrivalP}</p>` + this._arrivalIll(t) + marks(t.arrivalMarks) +
        `<ul class="notes">${t.arrivalNotes.map((x) => `<li>${x}</li>`).join("")}</ul>` +
        (c.arrival ? `<p class="live2">${t.arrivalLive(esc(c.arrival))}</p>` : ""));

      const [d0, d1, d2, d3] = t.fbDemo;
      const feedback = sec("feedback", 0, t.fbH, `<p class="d">${t.fbP}</p><div class="fbgrid">
        <div class="fbdemo" aria-hidden="true"><p class="s">${d0}</p>
          <div class="c"><span class="on"><i style="background:var(--hp-setback)"></i>${d1}</span><span><i style="background:var(--hp-preheat)"></i>${d2}</span></div>
          <p class="m">${d3}</p></div>
        <ul class="marks" style="margin:0;grid-template-columns:1fr">${t.fbRules.map(([b, p], i) => `<li><span class="num">${i + 1}</span><div><b>${b}</b>${p}</div></li>`).join("")}</ul>
      </div><p class="tip">${t.fbNote}</p>`);

      const confZones = c.zones.filter((z) => z.mode !== "percent" && z.thermal && (z.thermal.quality || typeof z.thermal.rmse === "number")).map((z) => {
        const th = z.thermal, q = (t.conf.find(([k]) => k === th.quality) || [])[1] || esc(th.quality || "–");
        const lag = th.lag_hours ?? th.lag;
        return `<li>${t.confZone({ name: z.name, q, rmse: typeof th.rmse === "number" ? fmtN(th.rmse, c.loc) : null, solar: (th.solar_gain ?? th.solar) > 0, lag: typeof lag === "number" && lag >= 0.25 ? fmtN(lag, c.loc) : null })}</li>`;
      }).join("");
      const confidence = sec("confidence", 0, t.confH, `<p class="d">${t.confP}</p><div class="learn"><div>${this._confIll(t)}</div>
        ${confZones ? `<ul class="zl">${confZones}</ul>` : `<ul class="notes" style="margin:0">${t.confNotes.map((x) => `<li>${x}</li>`).join("")}</ul>`}</div>
        <div class="conf">${t.conf.map(([, b, p], i) => `<div><div class="meter">${[0, 1, 2].map((k) => `<i class="${k < 3 - i ? "on" : ""}"></i>`).join("")}</div><b>${b}</b><span>${p}</span></div>`).join("")}</div>
        ${confZones ? `<ul class="notes">${t.confNotes.map((x) => `<li>${x}</li>`).join("")}</ul>` : ""}`);

      const optimizer = sec("optimizer", 0, t.optH, `<p class="d">${t.optP}</p><div class="money bounds">${t.optBounds.map(([b, f]) => `<div><b>${b}</b><span>${f(s)}</span></div>`).join("")}</div>` +
        (c.optimize !== null ? `<p class="live2">${t.optLive(c.optimize)}</p>` : ""));

      // The table of contents is built from the sections actually shown, so the two never drift apart.
      const order = [day, colors, rules, optimizer, tariff, peak, away, arrival, edit, manual, feedback, safety, learn, confidence, money, settings, faq].filter(Boolean);
      const toc = `<nav class="toc" aria-label="${esc(t.tocLabel)}">${order.map((x) => `<button data-go="${x.id}">${t.toc[x.id] || x.h}</button>`).join("")}</nav>`;
      const body = order.map((x, i) => `<section id="${x.id}" aria-labelledby="h-${x.id}"><p class="eyebrow" aria-hidden="true">${String(i + 1).padStart(2, "0")}</p><h2 id="h-${x.id}" tabindex="-1">${x.h}</h2>${x.body}</section>`).join("");
      this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="wrap" lang="${this.langKey}">${hero}${toc}${body}</div>`;
      this.shadowRoot.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => this._setLang(b.dataset.lang)));
      const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.shadowRoot.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
        const sec = this.shadowRoot.getElementById(b.dataset.go);
        if (!sec) return;
        sec.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
        sec.querySelector("h2")?.focus({ preventScroll: true }); // keyboard and screen-reader users land in the section
      }));
    }

    _editIll(t) {
      // a day curve being repainted: hours 17–21 dragged up
      const W = 640, H = 170, ml = 34, mr = 10, top = 22, ch = 110, n = 24, bw = (W - ml - mr) / n;
      const X = (i) => ml + i * bw, Y = (v) => top + ch - ((v + 3) / 6) * ch;
      const base = [-2, -2, -2, -2, -2, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -2];
      const painted = new Set([17, 18, 19, 20, 21]);
      let s = `<line x1="${ml}" x2="${W - mr}" y1="${Y(0)}" y2="${Y(0)}" class="gl"/><text x="${ml - 7}" y="${Y(0) + 3.5}" class="yl">±0</text>`;
      s += `<text x="${ml - 7}" y="${Y(2) + 3.5}" class="yl">+2</text><text x="${ml - 7}" y="${Y(-2) + 3.5}" class="yl">−2</text>`;
      base.forEach((v, i) => {
        const nv = painted.has(i) ? 1.5 : v, y = Y(nv);
        const col = painted.has(i) ? MODE.comfort : nv > 0 ? MODE.comfort : nv < 0 ? MODE.setback : MODE.normal;
        if (nv !== 0) s += `<rect x="${X(i) + 0.5}" y="${Math.min(y, Y(0))}" width="${bw - 1}" height="${Math.abs(Y(0) - y)}" fill="${col}" opacity=".15"/>`;
        s += `<rect x="${X(i) + 0.5}" y="${y - 1.5}" width="${bw - 1}" height="3" rx="1.5" fill="${col}"/>`;
        if (painted.has(i)) {
          s += `<line x1="${X(i) + bw / 2}" x2="${X(i) + bw / 2}" y1="${Y(0)}" y2="${y + 6}" stroke="var(--hp-line)" stroke-dasharray="2 2"/>`;
          s += `<circle cx="${X(i) + bw / 2}" cy="${y}" r="4.5" fill="var(--hp-ink)"/>`;
        }
        if (i % 3 === 0) s += `<text x="${X(i) + bw / 2}" y="${top + ch + 18}" class="xl">${String(i).padStart(2, "0")}</text>`;
      });
      const y1 = Y(1.5) - 22;
      s += `<path d="M${X(17) + bw / 2},${y1} C${X(19)},${y1 - 10} ${X(20)},${y1 - 10} ${X(21) + bw / 2},${y1}" fill="none" stroke="var(--hp-ink)" stroke-width="1.5" marker-end="url(#ah)"/>`;
      s += `<defs><marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="var(--hp-ink)"/></marker></defs>`;
      return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" class="ill" role="img" aria-label="${esc(t.editH)}">${s}</svg></div>`;
    }

    _tariffIll(kind, t) {
      // The same day's prices as paid on spot, with electricity support, and on Norgespris.
      const W = 320, H = 112, ml = 8, mr = 8, top = 16, ch = 74, n = 24, bw = (W - ml - mr) / n;
      const X = (i) => ml + i * bw, pmax = 3.3, Y = (v) => top + ch - (v / pmax) * ch;
      const thr = 0.94, fixed = 0.5;
      let s = "";
      DAY.prices.forEach((p, i) => {
        const eff = kind === 0 ? p : kind === 1 ? (p > thr ? thr + (p - thr) * 0.1 : p) : fixed;
        if (kind > 0) s += `<rect x="${X(i) + bw * 0.17}" y="${Y(p)}" width="${bw * 0.66}" height="${top + ch - Y(p)}" rx="2" class="ghost"/>`;
        const col = kind === 0 ? LEVEL[DAY.levels[i]] : kind === 1 && p > thr ? LEVEL.e : LEVEL.n;
        s += `<rect x="${X(i) + bw * 0.17}" y="${Y(eff)}" width="${bw * 0.66}" height="${Math.max(2, top + ch - Y(eff))}" rx="2" fill="${col}"/>`;
        if (i % 6 === 0) s += `<text x="${X(i) + bw / 2}" y="${H - 6}" class="xl">${String(i).padStart(2, "0")}</text>`;
      });
      if (kind > 0) {
        const v = kind === 1 ? thr : fixed;
        s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(v)}" y2="${Y(v)}" class="hl"/>`;
        s += `<text x="${W - mr - 2}" y="${Y(v) - 5}" class="hlt">${esc(kind === 1 ? t.thresholdLbl : t.fixedLbl)}</text>`;
      }
      return `<svg viewBox="0 0 ${W} ${H}" class="ill" role="img" aria-label="${esc(t.tariffs[kind][0])}">${s}</svg>`;
    }

    _peakIll(t) {
      // Daily peak hour (kW) for half a month: the three highest set the step; a dashed cap keeps today below the next step.
      const days = [5.1, 6.3, 4.8, 7.9, 5.5, 6.0, 8.4, 5.2, 4.9, 7.2, 6.1, 5.8, 5.0, 6.6, 5.4, 4.6];
      const today = 6.9, top3 = [...days].sort((a, b) => b - a).slice(0, 3);
      const W = 640, H = 190, ml = 34, mr = 10, top = 26, ch = 130, n = days.length + 1, bw = (W - ml - mr) / n;
      const X = (i) => ml + i * bw, Y = (v) => top + ch - (v / 11) * ch;
      const cap = 9.0, step = 10;
      let s = "";
      for (const v of [0, 5, 10]) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(v)}" y2="${Y(v)}" class="gl"/><text x="${ml - 7}" y="${Y(v) + 3.5}" class="yl">${v}</text>`;
      s += `<text x="${ml - 7}" y="${top - 12}" class="yl">kW</text>`;
      const used = new Set();
      days.forEach((v, i) => {
        const hi = top3.includes(v) && !used.has(v) && used.add(v);
        s += `<rect x="${X(i) + bw * 0.2}" y="${Y(v)}" width="${bw * 0.6}" height="${top + ch - Y(v)}" rx="3" fill="${hi ? "var(--hp-ink)" : LEVEL.n}" opacity="${hi ? 0.8 : 1}"/>`;
        s += `<text x="${X(i) + bw / 2}" y="${top + ch + 16}" class="xl">${i + 1}</text>`;
      });
      const ti = days.length;
      s += `<rect x="${X(ti) + bw * 0.2}" y="${Y(today)}" width="${bw * 0.6}" height="${top + ch - Y(today)}" rx="3" fill="var(--hp-save)"/>`;
      s += `<text x="${X(ti) + bw / 2}" y="${top + ch + 16}" class="xl">${esc(t.nowLbl)}</text>`;
      s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(step)}" y2="${Y(step)}" class="stepl"/><text x="${ml + 28}" y="${Y(step) - 6}" class="stept" style="text-anchor:start">${esc(t.stepLbl)}</text>`;
      s += `<line x1="${X(ti - 2)}" x2="${W - mr}" y1="${Y(cap)}" y2="${Y(cap)}" class="capl"/><text x="${X(ti - 2) - 4}" y="${Y(cap) + 3.5}" class="capt">${esc(t.capLbl)}</text>`;
      const mk = (k, x, y) => `<g class="mk"><circle cx="${x}" cy="${y}" r="9.5"/><text x="${x}" y="${y + 4}">${k}</text></g>`;
      const i1 = days.indexOf(top3[0]);
      s += mk("1", X(i1) + bw / 2, Y(top3[0]) - 16) + mk("2", X(ti) + bw / 2, Y(today) - 16) + mk("3", ml + 14, Y(step) - 10);
      return `<div class="scroll"><svg viewBox="0 0 ${W} ${H}" class="ill" role="img" aria-label="${esc(t.peakH)}">${s}</svg></div>`;
    }

    _arrivalIll(t) {
      // Arrival times per weekday over six weeks, the early estimate, and the warm-up lead before it.
      const W = 640, H = 196, ml = 44, mr = 12, top = 30, rowH = 26, h0 = 12, h1 = 19;
      const X = (h) => ml + ((h - h0) / (h1 - h0)) * (W - ml - mr);
      const rows = this.langKey === "nb" ? ["man", "tir", "ons", "tor", "fre"] : ["Mon", "Tue", "Wed", "Thu", "Fri"];
      const base = [15.3, 15.45, 15.25, 15.6, 14.9];
      const est = 15 + 10 / 60, warm = 13;
      let s = `<rect x="${X(warm)}" y="${top - 18}" width="${X(est) - X(warm)}" height="${rows.length * rowH + 18}" rx="7" class="warm"/>`;
      s += `<text x="${X(warm) + 7}" y="${top - 6}" class="bl">${esc(t.warmLbl)}</text>`;
      rows.forEach((r, k) => {
        const y = top + k * rowH + rowH / 2;
        s += `<line x1="${ml}" x2="${W - mr}" y1="${y}" y2="${y}" class="gl"/><text x="${ml - 8}" y="${y + 3.5}" class="rowl">${r}</text>`;
        const offs = [-0.2, 0.05, 0.32, -0.08, 0.5, 0.18];
        for (let w = 0; w < 6; w++) {
          const jitter = offs[(w + k * 2) % 6] + (w === 2 && k === 3 ? 1.5 : 0);
          s += `<circle cx="${X(base[k] + jitter)}" cy="${y}" r="4" class="adot"/>`;
        }
      });
      const yb = top + rows.length * rowH;
      s += `<line x1="${X(est)}" x2="${X(est)}" y1="${top - 18}" y2="${yb}" class="estl"/>`;
      s += `<text x="${X(est) + 5}" y="${top - 6}" class="bl">${esc(t.expLbl)} 15:10</text>`;
      for (let h = h0; h <= h1; h++) s += `<text x="${X(h)}" y="${yb + 18}" class="xl">${String(h).padStart(2, "0")}</text>`;
      const mk = (k, x, y) => `<g class="mk"><circle cx="${x}" cy="${y}" r="9.5"/><text x="${x}" y="${y + 4}">${k}</text></g>`;
      s += mk("1", X(16.35), top + rowH / 2 - 14) + mk("2", X(est), yb + 34) + mk("3", X(warm), yb + 34);
      return `<div class="scroll"><svg viewBox="0 0 ${W} ${H + 12}" class="ill" role="img" aria-label="${esc(t.arrivalH)}">${s}</svg></div>`;
    }

    _confIll(t) {
      // Model prediction with its typical error as a band; measured temperatures mostly fall inside.
      const W = 420, H = 170, ml = 34, mr = 10, top = 14, ch = 128, n = 24, bw = (W - ml - mr) / n;
      const X = (i) => ml + i * bw, Y = (v) => top + ch - ((v - 18) / 6) * ch;
      const model = [], meas = [];
      let T = 19.2;
      for (let i = 0; i < n; i++) {
        T = i >= 4 && i < 10 ? T + (23.5 - T) * 0.25 : T - (T - 17) / 22;
        model.push(T);
        meas.push(T + Math.sin(i * 2.7) * 0.25);
      }
      const r = 0.35;
      let band = model.map((v, i) => `${i ? "L" : "M"}${(X(i) + bw / 2).toFixed(1)},${Y(v + r).toFixed(1)}`).join("");
      band += model.map((v, i) => [i, v]).reverse().map(([i, v]) => `L${(X(i) + bw / 2).toFixed(1)},${Y(v - r).toFixed(1)}`).join("") + "Z";
      let s = "";
      for (const v of [18, 20, 22, 24]) s += `<line x1="${ml}" x2="${W - mr}" y1="${Y(v)}" y2="${Y(v)}" class="gl"/><text x="${ml - 7}" y="${Y(v) + 3.5}" class="yl">${v}°</text>`;
      s += `<path d="${band}" class="cband"/>`;
      s += `<path d="${model.map((v, i) => `${i ? "L" : "M"}${(X(i) + bw / 2).toFixed(1)},${Y(v).toFixed(1)}`).join("")}" class="pred"/>`;
      meas.forEach((v, i) => { s += `<circle cx="${X(i) + bw / 2}" cy="${Y(v)}" r="2.6" class="dot"/>`; });
      s += `<text x="${X(10) + bw / 2}" y="${Y(model[10] + r) - 8}" class="bl">±${t.confLbl}</text>`;
      return `<svg viewBox="0 0 ${W} ${H}" class="ill" role="img" aria-label="${esc(t.confH)}">${s}</svg>
        <div class="prices" style="margin-top:6px"><span><i style="width:8px;height:8px;border-radius:50%;background:var(--hp-ink);opacity:.55;vertical-align:0"></i>${t.learnLegend[0]}</span><span><i class="dash"></i>${t.learnLegend[1]}</span></div>`;
    }
  }
  customElements.define("heat-planner-help", HeatPlannerHelp);

  class HeatPlannerHelpCard extends HTMLElement {
    setConfig(config) {
      this._config = config || {};
      if (!this._card) {
        this._card = document.createElement("ha-card");
        this._card.style.padding = "clamp(16px, 3vw, 32px)";
        this._help = document.createElement("heat-planner-help");
        this._card.append(this._help);
        this.append(this._card);
      }
      if (this._config.language) this._help.language = this._config.language;
    }
    set hass(h) { if (this._help) this._help.hass = h; }
    getCardSize() { return 30; }
    getGridOptions() { return { columns: "full" }; }
    static getStubConfig() { return {}; }
  }
  customElements.define("heat-planner-help-card", HeatPlannerHelpCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "heat-planner-help-card", name: "Heat Planner – how it works", description: "A visual guide to the heating plan, in Norwegian or English." });
})();
