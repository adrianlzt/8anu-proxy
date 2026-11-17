/* Condiciones para escalar (8anu-proxy). Se pinta en la pestaña «Conditions» de cada escuela de 8a.nu (en lugar del
 * «Sign in for today's conditions») o en la página suelta /_proxy/conditions/<cat>/<país>/<escuela>. Los datos salen de
 * /_proxy/conditions/…/data (ver app/conditions.py). */
(function () {
  "use strict";
  const ROUTE = /^\/crags\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)\/conditions\/?$/;
  const CAT_LABEL = { great: "Great", good: "Good", ok: "OK", bad: "Bad", night: "Night" };
  const PREFS_KEY = "nuc-prefs";
  const DAYNAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const state = { key: null, data: null, mode: "next", sel: null, chart: "day", hourly: false, editPrefs: false,
                  editCrag: false, root: null, loading: false, error: null };

  function prefs() {
    try {
      return Object.assign({ tmin: 12, tmax: 22, units: "metric", style: "sport" }, JSON.parse(localStorage.getItem(PREFS_KEY) || "{}"));
    } catch (e) { return { tmin: 12, tmax: 22, units: "metric", style: "sport" }; }
  }
  function savePrefs(p) { localStorage.setItem(PREFS_KEY, JSON.stringify(p)); }

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const imperial = () => prefs().units === "imperial";
  const T = (c) => (c == null ? "–" : imperial() ? Math.round(c * 9 / 5 + 32) : Math.round(c));
  const TU = () => (imperial() ? "°F" : "°C");
  const W = (k) => (imperial() ? Math.round(k / 1.609) + " mph" : Math.round(k) + " km/h");
  const fmtMin = (m) => (m >= 1440 ? "24:00" : String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"));

  function dayLabel(d, i) {
    const t = state.data.today;
    if (i === t) return "Today";
    if (i === t + 1) return "Tomorrow";
    if (i === t - 1) return "Yesterday";
    return DAYNAMES[new Date(d.date + "T12:00").getDay()];
  }
  function shortDate(s) {
    const d = new Date(s + "T12:00");
    return DAYNAMES[d.getDay()].slice(0, 3) + " " + d.getDate() + " " + MONTHS[d.getMonth()];
  }
  function icon(code, cloud) {
    if (code >= 95) return "⛈️";
    if (code >= 71 && code <= 77) return "🌨️";
    if (code >= 80) return "🌦️";
    if (code >= 51) return "🌧️";
    if (code >= 45) return "🌫️";
    if (cloud < 20) return "☀️";
    if (cloud < 50) return "🌤️";
    if (cloud < 85) return "⛅";
    return "☁️";
  }
  function compass(deg) {
    const c = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
    return c[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
  }

  // --- Montaje -------------------------------------------------------------------------------------------------------

  function findAnchor() {
    // El bloque «Sign in for today's conditions» del contenido principal (el último: el primero va en la ficha lateral).
    const all = Array.from(document.querySelectorAll("body *")).filter(
      (el) => el.children.length === 0 && /Sign in for today/i.test(el.textContent || "") && !el.closest("#nuc-root")
    );
    if (!all.length) return null;
    let el = all[all.length - 1];
    while (el.parentElement && el.parentElement !== document.body) {
      const txt = el.parentElement.textContent || "";
      if (txt.length > 1500 || /For Climbers|ROUTES IN DB/.test(txt)) break;
      el = el.parentElement;
    }
    return el;
  }

  function mount() {
    const standalone = document.querySelector("body.nuc-standalone #nuc-root");
    if (standalone) {
      if (!state.root) { state.root = standalone; load(standalone.dataset.crag); }
      return;
    }
    const m = location.pathname.match(ROUTE);
    if (!m) {
      if (state.root && state.root.isConnected) state.root.remove();
      state.root = null;
      return;
    }
    const key = m.slice(1).join("/");
    if (state.root && state.root.isConnected && state.key === key) {
      const col = state.root.parentElement;
      hideOfficial();
      return;
    }
    const root = document.createElement("div");
    root.id = "nuc-root";
    root.className = "nuc";
    // Columna central de la escuela: debajo de su cabecera (nombre + chip de condiciones). Sin sesión, 8a.nu pone ahí un
    // muro de «Log in to explore this crag», que se esconde: las condiciones no necesitan cuenta.
    const col = Array.from(document.querySelectorAll(".crags-column-center")).find((c) => c.offsetWidth > 0);
    if (col) {
      const hdr = Array.from(col.children).find((c) => /crag-mobile-header|crag-header/.test(c.className) && c.offsetWidth > 0);
      col.insertBefore(root, hdr ? hdr.nextSibling : col.firstChild);
    } else {
      const anchor = findAnchor();
      if (anchor) {
        anchor.style.display = "none";
        anchor.parentElement.insertBefore(root, anchor);
      } else {
        document.body.insertBefore(root, document.body.firstChild);
      }
    }
    state.root = root;
    hideOfficial();
    if (state.key !== key || !state.data) load(key); else render();
  }

  // Lo que 8a.nu pone en la columna detrás de nuestro bloque (su página de condiciones, con o sin sesión, o el muro
  // de «Log in») se esconde; «Show 8a.nu's version» lo vuelve a enseñar.
  function hideOfficial() {
    const root = state.root;
    if (!root || !root.parentElement || root.parentElement === document.body) return;
    for (let el = root.nextElementSibling; el; el = el.nextElementSibling) {
      if (el.tagName === "FOOTER" || el.id === "nuc-official") continue;
      el.style.display = state.showOfficial ? "" : "none";
    }
  }

  async function load(key) {
    if (state.key !== key) { state.data = null; state.sel = null; }
    state.key = key;
    state.loading = true;
    state.error = null;
    render();
    const p = prefs();
    try {
      const r = await fetch(`/_proxy/conditions/${key}/data?tmin=${p.tmin}&tmax=${p.tmax}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      state.data = d;
      if (state.sel == null || state.sel >= d.days.length) state.sel = d.today;
    } catch (e) {
      state.error = String(e.message || e);
    }
    state.loading = false;
    render();
  }

  // --- Render ----------------------------------------------------------------------------------------------------------

  function render() {
    const root = state.root;
    if (!root) return;
    root.classList.add("nuc");
    if (state.loading && !state.data) { root.innerHTML = '<div class="nuc-card nuc-pad">Loading conditions…</div>'; return; }
    if (state.error) { root.innerHTML = `<div class="nuc-card nuc-pad">Couldn't load conditions: ${esc(state.error)}</div>`; return; }
    const d = state.data;
    root.innerHTML = [header(d), daysCard(d), bestTime(d), scoreCard(d), sunCard(d), whyCard(d),
      window.NucMeteo ? window.NucMeteo.card(state.key, d.crag) : "", prefsCard(), webcamsCard(d), knowCard(d),
      `<a href="#" class="nuc-link nuc-official" data-official="1">${state.showOfficial ? "Hide" : "Show"} 8a.nu's version</a>`].join("");
    bind(root);
  }

  function header(d) {
    const n = d.now;
    const sym = n.state === "Dark" ? "☾" : n.state === "Sun" ? "☀" : "◐";
    return `<div class="nuc-head"><span class="nuc-crag">${esc(d.crag.name)}</span>
      <span class="nuc-pill nuc-c-${n.cat}">${sym} ${T(n.temp)}° · ${esc(n.state)}</span>
      <span class="nuc-now">Rock ${esc(n.rock)} · ${n.rh} % · ${W(n.wind)}</span>
      <a class="nuc-link nuc-maplink" href="/_proxy/map#${d.crag.lat},${d.crag.lon},10">🗺 Conditions map</a></div>`;
  }

  function seg(name, opts, cur) {
    return `<div class="nuc-seg">${opts.map(([v, l]) => `<button type="button" data-${name}="${v}" class="${v === cur ? "on" : ""}">${l}</button>`).join("")}</div>`;
  }

  function rainLine(day) {
    const r = day.rain;
    if (r.mm >= 0.1 && r.prob >= 20) {
      const when = r.from ? (r.to && r.to !== "24:00" ? `${r.from}–${r.to}` : `from ${r.from}`) : "";
      return `<div class="nuc-rain">${r.prob} % showers ${when} · ${r.mm} mm</div>`;
    }
    return `<div>${r.mm} mm rain</div>`;
  }

  function daysCard(d) {
    const idx = d.days.map((_, i) => i).filter((i) => (state.mode === "past" ? i < d.today : i >= d.today && i < d.today + 7));
    const cards = idx.map((i) => {
      const x = d.days[i];
      const wind = x.wind_from && x.wind_to && x.wind_from !== x.wind_to ? `${x.wind_from}→${x.wind_to}` : x.wind_from;
      const rockCls = /^Rock dry/.test(x.rock) ? "dry" : /after/i.test(x.rock) ? "after" : "wet";
      return `<button type="button" class="nuc-day ${i === state.sel ? "sel" : ""}" data-day="${i}">
        <div class="nuc-dname">${dayLabel(x, i)}</div><div class="nuc-ddate">${shortDate(x.date)}</div>
        <div class="nuc-icon">${icon(x.code, x.cloud)}</div>
        <div class="nuc-temps"><b>${T(x.tmax)}°</b> ${T(x.tmin)}°</div>
        <div class="nuc-dmeta">${rainLine(x)}<div>${W(x.wind)}</div><div>from ${esc(wind)}</div><div>${x.rh} % humidity</div></div>
        <div class="nuc-dfoot"><span class="nuc-badge nuc-c-${x.cat}">${CAT_LABEL[x.cat]}</span>
          <div class="nuc-win">${x.window ? `${x.window.from}–${x.window.to}` : "No good window"}</div>
          <div class="nuc-rock ${rockCls}">${esc(x.rock)}</div></div>
        <div class="nuc-dbar nuc-bg-${x.cat}"></div></button>`;
    }).join("");
    return `<section class="nuc-card"><div class="nuc-pad"><h2>${state.mode === "past" ? "Past 5 days" : "Next 7 days"}</h2>
      ${seg("mode", [["past", "Past 5 days"], ["next", "Next 7 days"]], state.mode)}</div>
      <div class="nuc-sub">Weather for ${esc(d.crag.area || d.crag.name)}, forecast from ${esc(d.fetched || "")} · tap a day for its conditions</div>
      <div class="nuc-days">${cards}</div>
      <button type="button" class="nuc-hourly-toggle" data-hourly="1"><span>HOURLY WEATHER</span><span>Next 48 hours</span><span>${state.hourly ? "▴" : "▾"}</span></button>
      ${state.hourly ? hourlyTable(d) : ""}</section>`;
  }

  function hourlyTable(d) {
    const rows = d.hours.slice(d.now.index, d.now.index + 48).map((h) => `<tr class="${h.day ? "" : "night"}">
      <td>${h.time.slice(8, 10)}/${h.time.slice(5, 7)} ${h.time.slice(11, 16)}</td><td>${icon(h.code, h.cloud)}</td><td>${T(h.temp)}°</td>
      <td>${h.precip ? h.precip + " mm" : ""}${h.prob ? ` <small>${h.prob}%</small>` : ""}</td><td>${W(h.wind)}</td><td>${h.rh} %</td>
      <td><span class="nuc-dot nuc-bg-${h.cat}"></span>${h.sun ? " ☀" : ""}</td></tr>`).join("");
    return `<div class="nuc-hourly"><table><thead><tr><th>Time</th><th></th><th>Temp</th><th>Rain</th><th>Wind</th><th>RH</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  const dayHours = (d, i) => d.hours.filter((h) => h.time.startsWith(d.days[i].date));

  function dayNav(d) {
    const i = state.sel;
    return `<div class="nuc-nav"><button type="button" data-nav="-1" ${i <= 0 ? "disabled" : ""}>‹</button>
      <span>${dayLabel(d.days[i], i)} · ${shortDate(d.days[i].date)}</span>
      <button type="button" data-nav="1" ${i >= d.days.length - 1 ? "disabled" : ""}>›</button></div>`;
  }

  function trend(hs) {
    const lit = hs.filter((h) => h.day);
    if (lit.length < 4) return "Short day.";
    const half = Math.floor(lit.length / 2);
    const avg = (a) => a.reduce((s, h) => s + h.score, 0) / a.length;
    const am = avg(lit.slice(0, half)), pm = avg(lit.slice(half));
    if (lit[0].rock !== "dry" && lit[lit.length - 1].rock === "dry") return "Drying out during the day.";
    if (lit[0].rock === "dry" && lit[lit.length - 1].rock !== "dry") return "Getting wet later in the day.";
    if (pm - am > 15) return "Better in the afternoon.";
    if (am - pm > 15) return "Better in the morning.";
    return "Steady conditions all day.";
  }

  function tempWord(t, p) {
    if (t >= p.tmin && t <= p.tmax) return "in your ideal range";
    if (t < p.tmin) return t >= p.tmin - 2 ? "close to your ideal" : "colder than you like";
    return t <= p.tmax + 2 ? "close to your ideal" : "hotter than you like";
  }
  const rhWord = (rh) => (rh < 50 ? "dry air" : rh < 70 ? "a bit humid" : rh < 85 ? "quite humid" : "very humid");

  function bestTime(d) {
    const x = d.days[state.sel], hs = dayHours(d, state.sel);
    const lit = hs.filter((h) => h.day);
    const first = Math.max(0, (lit.length ? +lit[0].time.slice(11, 13) : 7) - 1);
    const last = Math.min(23, (lit.length ? +lit[lit.length - 1].time.slice(11, 13) : 18) + 2);
    const span = hs.slice(first, last + 1);
    const wf = x.window ? +x.window.from.slice(0, 2) : -1, wt = x.window ? +x.window.to.slice(0, 2) : -1;
    const inWin = (h) => { const hr = +h.time.slice(11, 13); return hr >= wf && hr < wt; };
    const blocks = span.map((h) => `<span class="nuc-blk nuc-bg-${h.cat} ${inWin(h) ? "win" : ""}" title="${h.time.slice(11, 16)} · ${h.score}"></span>`).join("");
    const ticks = span.filter((_, k) => k % 3 === 0).map((h) => `<span>${h.time.slice(11, 16)}</span>`).join("");
    const wh = x.window ? hs.filter(inWin) : lit;
    const ref = wh.length ? wh : hs;
    const avg = (k) => ref.reduce((s, h) => s + h[k], 0) / ref.length;
    const p = prefs();
    const rt = Math.round(avg("rock_t")), rh = Math.round(avg("rh")), wind = avg("wind");
    const rock = ref.every((h) => h.rock === "dry") ? "Dry" : ref.some((h) => h.rock === "wet") ? "Wet" : "Damp";
    const dir = ref[Math.floor(ref.length / 2)].wind_dir;
    return `<section class="nuc-card"><div class="nuc-pad"><h2>Best time to climb</h2>${dayNav(d)}</div><div class="nuc-pad nuc-bt">
      <div class="nuc-big">${x.window ? `${x.window.from} – ${x.window.to}` : "No good window"}</div>
      <p>${trend(hs)}</p>
      <div class="nuc-blocks">${blocks}</div><div class="nuc-ticks">${ticks}</div>
      <div class="nuc-legend">${["great", "good", "ok", "bad", "night"].map((c) => `<span><i class="nuc-bg-${c}"></i>${CAT_LABEL[c]}</span>`).join("")}<span><i class="nuc-winkey"></i>Best 3-hour window of the day</span></div>
      <div class="nuc-tiles">
        <div class="nuc-tile"><small>Rock temperature</small><b>${T(rt)} ${TU()}</b><span>${tempWord(rt, p)}</span></div>
        <div class="nuc-tile"><small>Humidity</small><b>${rh} %</b><span>${rhWord(rh)}</span></div>
        <div class="nuc-tile"><small>Rock</small><b class="${rock === "Dry" ? "nuc-green" : "nuc-red"}">${rock}</b><span>${rock === "Dry" ? "good to go" : "wait for it to dry"}</span></div>
        <div class="nuc-tile"><small>Wind</small><b>${W(wind)}</b><span>from ${compass(dir)}</span></div>
      </div></div></section>`;
  }

  // Gráfica de puntuación (SVG): bandas Great/Good/OK/Bad, noche, mejor ventana, sol en la pared arriba, lluvia abajo.
  function chartSvg(hours, opts) {
    const Wd = 600, H = 260, top = 14, bot = 22;
    const n = hours.length, x = (i) => (i / n) * Wd, y = (s) => top + (1 - Math.min(100, Math.max(0, s)) / 100) * (H - top - bot);
    const bands = [[100, 80, "great"], [80, 65, "good"], [65, 45, "ok"], [45, 0, "bad"]].map(([a, b, c]) =>
      `<rect x="0" y="${y(a)}" width="${Wd}" height="${y(b) - y(a)}" class="nuc-band-${c}"/><text x="6" y="${y(a) + 14}" class="nuc-band-t">${CAT_LABEL[c]}</text>`).join("");
    let shade = "";
    hours.forEach((h, i) => { if (!h.day) shade += `<rect x="${x(i)}" y="${top}" width="${Wd / n + 0.5}" height="${H - top - bot}" class="nuc-nightshade"/>`; });
    const win = (opts.windows || []).map(([a, b]) => `<rect x="${x(a)}" y="${top}" width="${x(b) - x(a)}" height="${H - top - bot}" class="nuc-winshade"/>`).join("");
    const sun = (opts.sun || []).map(([a, b, cls]) => `<rect x="${x(a)}" y="2" width="${Math.max(1, x(b) - x(a))}" height="8" rx="2" class="${cls}"/>`).join("");
    const maxP = Math.max(0.5, ...hours.map((h) => h.precip || 0));
    let rain = "";
    hours.forEach((h, i) => {
      if (h.precip > 0) { const hh = Math.max(3, (h.precip / maxP) * 40); rain += `<rect x="${x(i) + 1}" y="${H - bot - hh}" width="${Math.max(2, Wd / n - 2)}" height="${hh}" class="nuc-rainbar"/>`; }
    });
    const pts = hours.map((h, i) => `${(x(i) + Wd / n / 2).toFixed(1)},${y(h.score).toFixed(1)}`).join(" ");
    const now = opts.now == null ? "" : `<line x1="${x(opts.now)}" x2="${x(opts.now)}" y1="${top}" y2="${H - bot}" class="nuc-nowline"/><text x="${x(opts.now) + 4}" y="${H - bot - 6}" class="nuc-nowt">Now</text>`;
    const ticks = (opts.ticks || []).map(([i, l]) => `<text x="${x(i)}" y="${H - 6}" class="nuc-tick" text-anchor="${i === 0 ? "start" : i >= n ? "end" : "middle"}">${l}</text>`).join("");
    return `<svg viewBox="0 0 ${Wd} ${H}" class="nuc-chart">${bands}${shade}${win}${rain}
      <polyline points="${pts}" class="nuc-line-o"/><polyline points="${pts}" class="nuc-line"/>${sun}${now}${ticks}
      <text x="${Wd - 6}" y="${top + 14}" text-anchor="end" class="nuc-band-t">Precip. max ${maxP.toFixed(1)} mm</text></svg>`;
  }

  function scoreCard(d) {
    let svg, best;
    if (state.chart === "day") {
      const hs = dayHours(d, state.sel), x = d.days[state.sel];
      const windows = x.window ? [[+x.window.from.slice(0, 2), +x.window.to.slice(0, 2)]] : [];
      const sun = x.sun.possible.map(([a, b]) => [a / 60, b / 60, "nuc-sunmaybe"]).concat(x.sun.sun.map(([a, b]) => [a / 60, b / 60, "nuc-sunon"]));
      const nowIdx = state.sel === d.today ? +d.now.time.slice(11, 13) : null;
      svg = chartSvg(hs, { windows, sun, now: nowIdx, ticks: [[0, "00:00"], [6, "06:00"], [12, "12:00"], [18, "18:00"], [24, "24:00"]] });
      best = x.window ? `Best time · ${x.window.from}–${x.window.to}` : "No good window";
    } else {
      const days = d.days.slice(d.today, d.today + 7);
      const hs = d.hours.filter((h) => h.time >= days[0].date && h.time.slice(0, 10) <= days[days.length - 1].date);
      const windows = [], sun = [], ticks = [];
      days.forEach((x, k) => {
        if (x.window) windows.push([k * 24 + +x.window.from.slice(0, 2), k * 24 + +x.window.to.slice(0, 2)]);
        x.sun.sun.forEach(([a, b]) => sun.push([k * 24 + a / 60, k * 24 + b / 60, "nuc-sunon"]));
        ticks.push([k * 24 + 12, dayLabel(x, d.today + k).slice(0, 3)]);
      });
      svg = chartSvg(hs, { windows, sun, now: +d.now.time.slice(11, 13), ticks });
      best = "Next 7 days";
    }
    return `<section class="nuc-card"><div class="nuc-pad"><h2>Condition score</h2>${state.chart === "day" ? dayNav(d) : ""}
      ${seg("chart", [["day", "Day"], ["week", "Next 7 days"]], state.chart)}</div>
      <div class="nuc-pad"><div class="nuc-besttitle">${best}</div>${svg}
      <div class="nuc-legend"><span><i class="nuc-k-line"></i>Condition score</span><span><i class="nuc-k-win"></i>Best time</span>
      <span><i class="nuc-k-sun"></i>Sun on the wall</span><span><i class="nuc-k-maybe"></i>Maybe sun (±)</span>
      <span><i class="nuc-k-rain"></i>Rain</span><span><i class="nuc-k-night"></i>Night</span></div></div></section>`;
  }

  function sunBar(s) {
    const a = 5 * 60, b = 20 * 60, pct = (m) => ((Math.min(b, Math.max(a, m)) - a) / (b - a)) * 100;
    const seg = (x, y, cls) => `<span class="${cls}" style="left:${pct(x)}%;width:${Math.max(0.6, pct(y) - pct(x))}%"></span>`;
    return `<div class="nuc-sunbar">${s.daylight.map(([x, y]) => seg(x, y, "dl")).join("")}${s.possible.map(([x, y]) => seg(x, y, "maybe")).join("")}${s.sun.map(([x, y]) => seg(x, y, "on")).join("")}</div>
      <div class="nuc-ticks nuc-ticks-sun"><span>05:00</span><span>08:45</span><span>12:30</span><span>16:15</span><span>20:00</span></div>`;
  }
  function sunText(s) {
    if (!s.sun.length) return "No direct sun";
    return s.sun.map(([a, b]) => `${fmtMin(a)}–${fmtMin(b)}`).join(", ") + (s.margin ? ` ±${s.margin} min` : "");
  }

  function sunCard(d) {
    const x = d.days[state.sel], s = x.sun, a = d.aspect;
    const mapped = d.crag.sectors.filter((z) => z.aspect != null);
    const sect = mapped.map((z) => `<div class="nuc-sector"><div class="nuc-row"><b>${esc(z.name)}</b><small>faces ${esc(z.compass)} (${Math.round(z.aspect)}°)</small></div>
      ${sunBar(z.sun[x.date])}<div>${sunText(z.sun[x.date])}</div></div>`).join("");
    const lead = s.sun.length ? `${esc(d.crag.name)} gets sun from ${fmtMin(s.sun[0][0])} until ${fmtMin(s.sun[s.sun.length - 1][1])}.` : `${esc(d.crag.name)} gets no direct sun.`;
    return `<section class="nuc-card"><div class="nuc-pad nuc-row"><h2>Sun on the wall</h2><small>${dayLabel(x, state.sel)}, ${shortDate(x.date)}</small></div>
      <div class="nuc-pad"><p>${lead}</p>
      <div class="nuc-row"><b>${esc(d.crag.name)}</b><small>${a.source === "user" ? "Wall set by you" : "Estimated from terrain"} · faces ${esc(a.compass)} (${Math.round(a.deg)}°)</small></div>
      ${sunBar(s)}<div class="nuc-suntext">${sunText(s)}</div><hr>
      <div class="nuc-muted">${d.crag.sectors.length} sectors</div>
      ${mapped.length ? sect : `<div class="nuc-muted">Same as the crag: no wall data of their own yet</div>`}
      <a href="#" class="nuc-link" data-know="1">Tell us when they get sun</a>
      <div class="nuc-legend"><span><i class="nuc-k-sun"></i>Sun on the wall</span><span><i class="nuc-k-maybe"></i>Uncertainty (±)</span><span><i class="nuc-k-day"></i>Daylight</span></div>
      </div></section>`;
  }

  function whyCard(d) {
    const w = d.why[state.sel], p = prefs(), a = d.aspect;
    const tcls = w.temperature.state === "In your range" ? "nuc-green" : /Close/.test(w.temperature.state) ? "nuc-amber" : "nuc-red";
    const rcls = w.rock.state === "Dry" ? "nuc-green" : "nuc-red";
    const mapped = d.crag.sectors.filter((z) => z.aspect != null).length;
    const src = a.source === "user" ? "set by you" : `estimated from the terrain (slope ${a.slope}°), may be wrong`;
    const fcls = (v) => (v >= 0.95 ? "great" : v >= 0.8 ? "good" : v >= 0.6 ? "ok" : "bad");
    const bars = (w.factors || []).map((f) => `<div class="nuc-factor"><div class="nuc-row"><b>${esc(f.label)}</b><small>${esc(f.text)}</small></div>
      <div class="nuc-fbar"><span class="nuc-bg-${fcls(f.value)}" style="width:${Math.max(4, f.value * 100)}%"></span></div></div>`).join("");
    const x = d.days[state.sel];
    return `<section class="nuc-card"><div class="nuc-pad"><h2>Why these conditions?</h2>
      <p class="nuc-muted">${x.window ? `During the best window (${x.window.from}–${x.window.to}), worst factors first. The day scores its weakest hour in that window: ${x.score}.` : "Over the daylight hours, worst factors first."}</p></div>
      <div class="nuc-pad nuc-factors">${bars}</div><div class="nuc-pad nuc-why">
      <div><h3>Temperature</h3><b class="${tcls}">${esc(w.temperature.state)}</b>
        <p>${w.temperature.window != null ? `${T(w.temperature.window)} ${TU()} on the rock during the best window, ` : ""}${T(w.temperature.high)} ${TU()} at the day's high.</p>
        <p class="nuc-muted">Scores are tuned to ${T(p.tmin)} – ${T(p.tmax)} ${TU()}.</p></div>
      <div><h3>Rock</h3><b class="${rcls}">${esc(w.rock.state)}</b><p>${w.rock.rh_now} % humidity now, ${w.rock.rh_6h} % over 6 h.</p></div>
      <div><h3>Sun</h3><b>${esc(w.sun.state)}</b><p>${w.sun.clear_hours} clear daylight hours.</p></div>
      <div><h3>Since the last rain</h3><b>${esc(w.rain.state)}</b><p>${w.rain.mm_3d} mm over the last 3 days.</p></div>
      </div><div class="nuc-pad"><p class="nuc-warn">⚠ Wall faces ${esc(a.compass)} (${Math.round(a.deg)}°), ${src}; ${mapped} of ${d.crag.sectors.length} sectors mapped${d.crag.rain_safe ? "; marked as climbable in the rain on 8a.nu" : ""}. Unmapped sectors may be shadier, wetter or windier.</p>
      <p class="nuc-muted">Model v2: rock temperature with thermal inertia, dew point and condensation, rain and drying for ${esc(d.crag.rock)}${d.crag.rock_known ? "" : " (assumed)"}${d.crag.rain_safe ? " in a sheltered crag" : ""}, wind at the wall from its direction, rain chance; not fitted to ascents yet · forecast Open-Meteo, fetched ${esc(d.fetched || "")}</p></div></section>`;
  }

  function prefsCard() {
    const p = prefs();
    if (state.editPrefs) {
      return `<section class="nuc-card"><div class="nuc-pad"><h2>Your preferences</h2></div><form class="nuc-pad nuc-form" data-form="prefs">
        <label>Style <select name="style"><option value="sport" ${p.style !== "boulder" ? "selected" : ""}>Sport climbing</option><option value="boulder" ${p.style === "boulder" ? "selected" : ""}>Bouldering</option></select></label>
        <label>Ideal climbing temperature (°C) <span><input name="tmin" type="number" value="${p.tmin}" step="1"> – <input name="tmax" type="number" value="${p.tmax}" step="1"></span></label>
        <label>Measurement units <select name="units"><option value="metric" ${p.units === "metric" ? "selected" : ""}>°C and km/h</option><option value="imperial" ${p.units === "imperial" ? "selected" : ""}>°F and mph</option></select></label>
        <div class="nuc-actions"><button type="button" data-cancel="prefs">Cancel</button><button type="submit" class="primary">Save</button></div></form></section>`;
    }
    return `<section class="nuc-card"><div class="nuc-pad nuc-row"><h2>Your preferences</h2><a href="#" class="nuc-link" data-edit="prefs">✎ Edit</a></div>
      <div class="nuc-pad"><div class="nuc-kv"><span>Style</span><b>${p.style === "boulder" ? "Bouldering" : "Sport climbing"}</b></div>
      <div class="nuc-kv"><span>Ideal climbing temperature</span><b>${T(p.tmin)} – ${T(p.tmax)} ${TU()}</b></div>
      <div class="nuc-kv"><span>Measurement units</span><b>${p.units === "imperial" ? "°F and mph" : "°C and km/h"}</b></div></div></section>`;
  }

  function webcamsCard(d) {
    const list = d.webcams.map((w) => `<li><a href="${esc(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.title || w.url)}</a></li>`).join("");
    return `<section class="nuc-card"><div class="nuc-pad nuc-row"><h2>Webcams</h2><a href="#" class="nuc-link" data-webcam="1">＋ Add a link</a></div>
      ${list ? `<ul class="nuc-pad nuc-cams">${list}</ul>` : ""}</section>`;
  }

  function knowCard(d) {
    const a = d.aspect, mapped = d.crag.sectors.filter((z) => z.aspect != null).length;
    if (!state.editCrag) {
      return `<section class="nuc-card" id="nuc-know"><div class="nuc-pad"><h2>Know this crag?</h2></div><div class="nuc-pad">
        <p>Conditions are calculated for the whole crag – <b>${mapped} of ${d.crag.sectors.length} sectors mapped</b> so far. Tell us which way the wall and the sectors face and the times get more precise.</p>
        <button type="button" class="nuc-outline" data-know="1">Answer a few questions</button></div></section>`;
    }
    const dirs = Object.entries({ N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 });
    const sel = (name, cur) => `<select name="${name}"><option value="">${name === "crag" ? "—" : "same as the crag"}</option>${dirs.map(([k, v]) =>
      `<option value="${v}" ${cur != null && Math.round(cur) === v ? "selected" : ""}>${k} (${v}°)</option>`).join("")}</select>`;
    const sectors = d.crag.sectors.map((z) => `<label>${esc(z.name)} ${sel("s:" + z.slug, z.aspect)}</label>`).join("");
    return `<section class="nuc-card" id="nuc-know"><div class="nuc-pad"><h2>Know this crag?</h2></div><form class="nuc-pad nuc-form" data-form="crag">
      <p>Which way does the main wall face? Now: ${esc(a.compass)} (${Math.round(a.deg)}°), ${a.source === "user" ? "set by you" : "estimated from terrain"}.</p>
      <label>Main wall ${sel("crag", a.source === "user" ? a.deg : null)} <span>or degrees <input name="deg" type="number" min="0" max="359" placeholder="${Math.round(a.deg)}"></span></label>
      <p class="nuc-muted">Sectors (only if they differ from the main wall):</p>${sectors}
      <div class="nuc-actions"><button type="button" data-cancel="crag">Cancel</button><button type="submit" class="primary">Save</button></div></form></section>`;
  }

  // --- Eventos ---------------------------------------------------------------------------------------------------------

  function bind(root) {
    if (window.NucMeteo && state.data) window.NucMeteo.bind(root, state.key, state.data.crag, render);
    const on = (sel, fn) => root.querySelectorAll(sel).forEach((b) => b.addEventListener("click", (e) => fn(b, e)));
    on("[data-mode]", (b) => {
      state.mode = b.dataset.mode;
      state.sel = state.mode === "past" ? Math.max(0, state.data.today - 1) : state.data.today;
      render();
    });
    on("[data-day]", (b) => { state.sel = +b.dataset.day; render(); });
    on("[data-nav]", (b) => { state.sel = Math.min(state.data.days.length - 1, Math.max(0, state.sel + +b.dataset.nav)); render(); });
    on("[data-chart]", (b) => { state.chart = b.dataset.chart; render(); });
    on("[data-official]", (b, e) => { e.preventDefault(); state.showOfficial = !state.showOfficial; hideOfficial(); render(); });
    on("[data-hourly]", () => { state.hourly = !state.hourly; render(); });
    on("[data-edit]", (b, e) => { e.preventDefault(); state.editPrefs = true; render(); });
    on("[data-cancel]", (b) => { if (b.dataset.cancel === "prefs") state.editPrefs = false; else state.editCrag = false; render(); });
    on("[data-know]", (b, e) => {
      e.preventDefault(); state.editCrag = true; render();
      const k = document.getElementById("nuc-know"); if (k) k.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    on("[data-webcam]", async (b, e) => {
      e.preventDefault();
      const url = prompt("Webcam URL (https://…)");
      if (!url) return;
      const title = prompt("Name (optional)") || "";
      const r = await fetch(`/_proxy/conditions/${state.key}/webcams`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ url, title }) });
      if (!r.ok) alert("Not a valid URL"); else load(state.key);
    });
    const pf = root.querySelector('[data-form="prefs"]');
    // Al cambiar de estilo se propone su rango: el bloque se escala con más frío (mejor fricción).
    if (pf) pf.querySelector('[name="style"]').addEventListener("change", (e) => {
      const [a, b] = e.target.value === "boulder" ? [4, 14] : [12, 22];
      pf.querySelector('[name="tmin"]').value = a;
      pf.querySelector('[name="tmax"]').value = b;
    });
    if (pf) pf.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = new FormData(pf), tmin = +f.get("tmin"), tmax = +f.get("tmax");
      if (!(tmax > tmin)) { alert("The maximum must be above the minimum"); return; }
      savePrefs({ tmin, tmax, units: f.get("units"), style: f.get("style") });
      state.editPrefs = false;
      load(state.key);
    });
    const cf = root.querySelector('[data-form="crag"]');
    if (cf) cf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(cf), posts = [];
      const deg = f.get("deg") !== "" ? f.get("deg") : f.get("crag");
      if (deg !== "" && deg != null) posts.push({ deg: +deg });
      for (const [k, v] of f.entries()) if (k.startsWith("s:") && v !== "") posts.push({ deg: +v, sector: k.slice(2) });
      for (const body of posts) {
        await fetch(`/_proxy/conditions/${state.key}/aspect`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      }
      state.editCrag = false;
      load(state.key);
    });
  }

  // 8a.nu es una SPA: se vuelve a mirar cada segundo (cambios de ruta, o Vue repinta y nos quita).
  // No se toca el DOM de 8a.nu hasta que Vue ha hidratado la página: si el div entra antes, Vue lo toma por uno suyo
  // (desajuste de hidratación) y lo rellena con su contenido.
  let readySince = 0;
  function hydrated() {
    if (document.body.classList.contains("nuc-standalone")) return true;
    const app = document.getElementById("__nuxt");
    if (!app || !app.__vue_app__) return false;
    if (!readySince) readySince = Date.now();
    return Date.now() - readySince > 800;
  }
  // Acceso al mapa de condiciones: botón flotante solo en la portada (en el resto pisaba contenido) y, siempre, una
  // entrada «Conditions map» en el menú lateral de 8a.nu, debajo de «Premium» (copia de su .menu-item).
  function mapButton() {
    const btn = document.getElementById("nuc-mapbtn");
    if (document.body.classList.contains("nuc-standalone") || location.pathname !== "/") { if (btn) btn.remove(); return; }
    if (btn) return;
    const a = document.createElement("a");
    a.id = "nuc-mapbtn";
    a.href = "/_proxy/map";
    a.textContent = "🗺 Conditions map";
    document.body.appendChild(a);
  }
  function menuEntry() {
    for (const item of document.querySelectorAll(".menu-item")) {
      if ((item.textContent || "").trim() !== "Premium" || !item.offsetWidth) continue;
      const next = item.nextElementSibling;
      if (next && next.classList.contains("nuc-menu-map")) continue;
      const copy = item.cloneNode(true);
      copy.classList.add("nuc-menu-map");
      copy.removeAttribute("id");
      const leaf = Array.from(copy.querySelectorAll("*")).reverse().find((el) => (el.textContent || "").trim() === "Premium") || copy;
      leaf.textContent = "Conditions map";
      copy.querySelectorAll("a").forEach((a) => { a.href = "/_proxy/map"; a.removeAttribute("target"); });
      copy.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); location.href = "/_proxy/map"; }, true);
      item.after(copy);
    }
  }
  // En las escuelas que 8a.nu no tiene marcadas con condiciones no sale su chip «24° · OK» en la cabecera ni la
  // pestaña. Las nuestras valen para cualquier escuela con coordenadas: se pone un chip propio que lleva a ellas.
  const CRAG_PATH = /^\/crags\/(sportclimbing|bouldering)\/([a-z0-9-]+)\/([a-z0-9-]+)(\/|$)/;
  const chipInfo = {};
  function loadChip(key) {
    if (chipInfo[key]) return;
    chipInfo[key] = "☁ Conditions";
    const p = prefs();
    fetch(`/_proxy/conditions/${key}/data?tmin=${p.tmin}&tmax=${p.tmax}`).then((r) => r.json()).then((d) => {
      if (!d.now) return;
      chipInfo[key] = `${d.now.state === "Dark" ? "☾" : d.now.state === "Sun" ? "☀" : "☁"} ${T(d.now.temp)}° · ${CAT_LABEL[d.days[d.today].cat]}`;
      document.querySelectorAll(".nuc-chip").forEach((c) => (c.textContent = chipInfo[key]));
    }).catch(() => {});
  }
  function cragChip() {
    const m = location.pathname.match(CRAG_PATH);
    if (!m || ROUTE.test(location.pathname)) return;
    const hdr = Array.from(document.querySelectorAll(".crag-mobile-header, .crag-header")).find((h) => h.offsetWidth > 0);
    if (!hdr) return;
    const key = m.slice(1, 4).join("/");
    loadChip(key);
    const theirs = hdr.querySelector(".conditions-chip, .conditions-mobile-chip");
    if (theirs) {
      // El chip de 8a.nu a veces sale solo con «Conditions» (sin temperatura): se rellena con nuestro resumen.
      const info = chipInfo[key];
      if (!/\d/.test(theirs.textContent || "") && info && /\d/.test(info)) {
        const leaf = Array.from(theirs.querySelectorAll("*")).reverse().find((el) => /Conditions/.test(el.textContent || "") && !el.children.length) || theirs;
        leaf.textContent = info;
      }
      return;
    }
    if (hdr.querySelector(".nuc-chip")) return;
    const a = document.createElement("a");
    a.className = "nuc-chip";
    a.href = `/crags/${key}/conditions`;
    a.textContent = chipInfo[key];
    const trigger = hdr.querySelector(".crag-mobile-header__trigger");
    (trigger && trigger.parentElement === hdr ? trigger : hdr.lastElementChild || hdr).after(a);
  }
  function tick() { try { if (hydrated()) { mapButton(); menuEntry(); cragChip(); mount(); } } catch (e) { console.warn("nuc", e); } }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tick); else tick();
  setInterval(tick, 500);
})();
