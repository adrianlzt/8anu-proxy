/* Meteorología detallada (8anu-proxy): tarjeta plegada al final de la página de condiciones. Al abrirla pide
 * /_proxy/conditions/…/meteo (app/meteo.py) y pinta avisos, meteograma de 72 h (temperatura con banda del ensemble,
 * rocío, lluvia y su probabilidad, nubes por capas, viento y rachas, poder de secado), comparación de modelos y radar
 * de lluvia en directo (RainViewer). La usa conditions.js con window.NucMeteo.card(key, crag) y .bind(root, key, crag). */
(function () {
  "use strict";
  const OPEN_KEY = "nuc-meteo-open";
  const st = { key: null, data: null, loading: false, error: null, sel: null, radar: false };
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const DAYN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const isOpen = () => localStorage.getItem(OPEN_KEY) === "1";
  const ICON = { storm: "⚡", freeze: "❄", fog: "🌫", wind: "💨", uv: "☀", heat: "🔥" };
  const AGREE = { high: ["Models agree", "great"], medium: ["Some disagreement", "ok"], low: ["Models disagree", "bad"] };

  async function load(key, rerender) {
    if (st.loading || (st.key === key && st.data)) return;
    st.key = key; st.loading = true; st.error = null; st.data = null;
    rerender();
    try {
      const r = await fetch(`/_proxy/conditions/${key}/meteo`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      st.data = d;
    } catch (e) { st.error = String(e.message || e); }
    st.loading = false;
    rerender();
  }

  const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  const compass = (deg) => (deg == null ? "" : COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]);
  function dayName(date) { const d = new Date(date + "T12:00"); return `${DAYN[d.getDay()]} ${d.getDate()}`; }

  // --- Meteograma -------------------------------------------------------------------------------------------------
  function meteogram(d) {
    const h = d.hours, e = d.ensemble, n = h.time.length;
    const W = 480, L = 30, R = 30, X = (i) => L + (i + 0.5) * ((W - L - R) / n), bw = (W - L - R) / n;
    const panels = { t: [8, 130], p: [150, 230], c: [250, 292], w: [312, 382], v: [402, 440] };
    const H = 462;
    const vals = (k) => h[k].filter((v) => v != null);
    const tAll = [...vals("temperature_2m"), ...vals("dew_point_2m"), ...e.t10.filter((v) => v != null), ...e.t90.filter((v) => v != null)];
    const tMin = Math.floor(Math.min(...tAll) - 1), tMax = Math.ceil(Math.max(...tAll) + 1);
    const yT = (v) => panels.t[1] - ((v - tMin) / (tMax - tMin)) * (panels.t[1] - panels.t[0]);
    const pMax = Math.max(2, ...vals("precipitation"), ...e.p90.filter((v) => v != null));
    const yP = (v) => panels.p[1] - (v / pMax) * (panels.p[1] - panels.p[0]);
    const yPP = (v) => panels.p[1] - (v / 100) * (panels.p[1] - panels.p[0]);
    const wMax = Math.max(20, ...vals("wind_gusts_10m"));
    const yW = (v) => panels.w[1] - (v / wMax) * (panels.w[1] - panels.w[0]);
    const vMax = Math.max(1, ...vals("vapour_pressure_deficit"));
    const yV = (v) => panels.v[1] - (v / vMax) * (panels.v[1] - panels.v[0]);
    const line = (arr, y) => arr.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${y(v).toFixed(1)}`)).filter(Boolean).join(" ");
    let g = "";
    // Noche y separadores de día.
    h.time.forEach((t, i) => {
      if (!h.is_day[i]) g += `<rect x="${X(i) - bw / 2}" y="0" width="${bw + 0.3}" height="${H - 16}" class="mg-night"/>`;
      if (t.slice(11, 13) === "00" && i) g += `<line x1="${X(i) - bw / 2}" x2="${X(i) - bw / 2}" y1="0" y2="${H - 16}" class="mg-day"/>`;
      if (t.slice(11, 13) === "12") g += `<text x="${X(i)}" y="${H - 3}" class="mg-lbl" text-anchor="middle">${dayName(t.slice(0, 10))}</text>`;
    });
    // Panel temperatura: banda ensemble p10–p90, temperatura y rocío.
    const band = e.t10.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${yT(e.t90[i]).toFixed(1)}`)).filter(Boolean).join(" ") + " " +
      e.t10.map((v, i) => (v == null ? null : `${X(i).toFixed(1)},${yT(v).toFixed(1)}`)).filter(Boolean).reverse().join(" ");
    g += `<polygon points="${band}" class="mg-band"/>`;
    g += `<polyline points="${line(h.dew_point_2m, yT)}" class="mg-dew"/><polyline points="${line(h.temperature_2m, yT)}" class="mg-temp"/>`;
    for (let v = Math.ceil(tMin / 5) * 5; v <= tMax; v += 5) g += `<text x="${L - 4}" y="${yT(v) + 4}" class="mg-ax" text-anchor="end">${v}°</text><line x1="${L}" x2="${W - R}" y1="${yT(v)}" y2="${yT(v)}" class="mg-grid"/>`;
    if (tMin < 0 && tMax > 0) g += `<line x1="${L}" x2="${W - R}" y1="${yT(0)}" y2="${yT(0)}" class="mg-zero"/>`;
    // Panel lluvia: barras (modelo), p90 del ensemble y probabilidad.
    h.precipitation.forEach((v, i) => {
      if (e.p90[i]) g += `<rect x="${X(i) - bw * 0.4}" y="${yP(e.p90[i])}" width="${bw * 0.8}" height="${panels.p[1] - yP(e.p90[i])}" class="mg-p90"/>`;
      if (v) g += `<rect x="${X(i) - bw * 0.4}" y="${yP(v)}" width="${bw * 0.8}" height="${panels.p[1] - yP(v)}" class="mg-rain"/>`;
    });
    g += `<polyline points="${line(e.pp, yPP)}" class="mg-pp"/>`;
    g += `<text x="${L - 4}" y="${panels.p[0] + 8}" class="mg-ax" text-anchor="end">${pMax.toFixed(1)}</text><text x="${L - 4}" y="${panels.p[1]}" class="mg-ax" text-anchor="end">mm</text>`;
    g += `<text x="${W - R + 4}" y="${panels.p[0] + 8}" class="mg-ax mg-ppax">100%</text><text x="${W - R + 4}" y="${panels.p[1]}" class="mg-ax mg-ppax">0%</text>`;
    // Panel nubes: bajas, medias, altas.
    [["cloud_cover_high", 0], ["cloud_cover_mid", 1], ["cloud_cover_low", 2]].forEach(([k, row]) => {
      const y0 = panels.c[0] + row * 14;
      h[k].forEach((v, i) => { if (v) g += `<rect x="${X(i) - bw / 2}" y="${y0}" width="${bw + 0.3}" height="12" fill="#5b6b7a" fill-opacity="${(v / 100) * 0.85}"/>`; });
      g += `<text x="${L - 4}" y="${y0 + 10}" class="mg-ax" text-anchor="end">${["High", "Mid", "Low"][row]}</text>`;
    });
    // Panel viento: velocidad, rachas y dirección cada 3 h.
    g += `<polyline points="${line(h.wind_speed_10m, yW)}" class="mg-wind"/><polyline points="${line(h.wind_gusts_10m, yW)}" class="mg-gust"/>`;
    h.wind_direction_10m.forEach((dir, i) => {
      if (i % 3 !== 1 || dir == null) return;
      const cx = X(i), cy = panels.w[0] + 8;
      g += `<g transform="translate(${cx},${cy}) rotate(${dir + 180})"><path d="M0,-6 L3,3 L0,1 L-3,3 Z" class="mg-arrow"/></g>`;
    });
    g += `<text x="${L - 4}" y="${panels.w[0] + 20}" class="mg-ax" text-anchor="end">${wMax.toFixed(0)}</text><text x="${L - 4}" y="${panels.w[1]}" class="mg-ax" text-anchor="end">km/h</text>`;
    // Panel poder de secado (déficit de presión de vapor).
    h.vapour_pressure_deficit.forEach((v, i) => { if (v) g += `<rect x="${X(i) - bw * 0.4}" y="${yV(v)}" width="${bw * 0.8}" height="${panels.v[1] - yV(v)}" class="mg-vpd"/>`; });
    g += `<text x="${L - 4}" y="${panels.v[1]}" class="mg-ax" text-anchor="end">kPa</text>`;
    // Títulos de panel.
    const title = (y, t) => `<text x="${L + 4}" y="${y}" class="mg-title">${t}</text>`;
    g += title(panels.t[0] + 10, "Temp · dew point · ensemble 10–90 %") + title(panels.p[0] - 4, "Rain mm/h · ensemble chance") +
      title(panels.c[0] - 4, "Cloud layers") + title(panels.w[0] - 4, "Wind · gusts") + title(panels.v[0] - 4, "Drying power (VPD)");
    // Hora elegida.
    if (st.sel != null) g += `<line x1="${X(st.sel)}" x2="${X(st.sel)}" y1="0" y2="${H - 16}" class="mg-sel"/>`;
    return `<svg viewBox="0 0 ${W} ${H}" class="mg" data-mg="1" data-l="${L}" data-r="${R}" data-n="${n}">${g}</svg>`;
  }

  function hourInfo(d) {
    if (st.sel == null) return `<p class="nuc-muted">Tap the chart to see the values of an hour.</p>`;
    const h = d.hours, e = d.ensemble, i = st.sel, f = (v, u = "", dec = 0) => (v == null ? "–" : `${(+v).toFixed(dec)}${u}`);
    const t = h.time[i];
    return `<div class="mg-info"><b>${dayName(t.slice(0, 10))} ${t.slice(11, 16)}</b>
      <span>🌡 ${f(h.temperature_2m[i], "°", 1)} <small>(${f(e.t10[i], "", 1)}–${f(e.t90[i], "°", 1)})</small></span>
      <span>💧 dew ${f(h.dew_point_2m[i], "°", 1)} · RH ${f(h.relative_humidity_2m[i], "%")}</span>
      <span>🌧 ${f(h.precipitation[i], " mm", 1)} · ${f(e.pp[i], "%")} chance</span>
      <span>☁ low ${f(h.cloud_cover_low[i], "%")} · mid ${f(h.cloud_cover_mid[i], "%")} · high ${f(h.cloud_cover_high[i], "%")}</span>
      <span>💨 ${f(h.wind_speed_10m[i], " km/h")} · gusts ${f(h.wind_gusts_10m[i], " km/h")}</span>
      <span>🧊 freezing level ${f(h.freezing_level_height[i], " m")} · 👁 ${h.visibility[i] == null ? "–" : (h.visibility[i] / 1000).toFixed(1) + " km"}</span>
      <span>⚡ CAPE ${f(h.cape[i], " J/kg")} · LI ${f(h.lifted_index[i], "", 1)} · ☀ UV ${f(h.uv_index[i], "", 1)}</span>
      <span>🔽 ${f(h.pressure_msl[i], " hPa")} · VPD ${f(h.vapour_pressure_deficit[i], " kPa", 2)}</span></div>`;
  }

  function modelsTable(d) {
    const m = d.models, names = Object.keys(m.models);
    const rows = m.dates.map((date, i) => {
      const a = m.agreement[i];
      const cells = names.map((nm) => {
        const v = m.models[nm][i];
        if (!v) return "<td>–</td>";
        const wind = v.wind == null ? "" : `<br><span class="mg-wind-cell" title="Wind 8–20 h: mean, max gust, from ${compass(v.wind_dir)}"><i style="transform:rotate(${v.wind_dir + 180}deg)">↑</i>${v.wind}${v.gust != null ? `<b>${v.gust}</b>` : ""} <em>${compass(v.wind_dir)}</em></span>`;
        return `<td>${v.tmax}°/${v.tmin}°<br><small>${v.rain == null ? "–" : v.rain + " mm"}</small>${wind}</td>`;
      }).join("");
      const badge = a ? `<span class="nuc-badge nuc-c-${AGREE[a.level][1]}" title="Max temp spread ${a.tspread}°, ${a.wet_models}/${a.models} models with ≥1 mm">${AGREE[a.level][0]}</span>` : "";
      return `<tr class="mg-dayrow" data-hday="${date}"><th>${dayName(date)}<span class="mg-hourly">Hourly ›</span></th>${cells}<td>${badge}</td></tr>`;
    }).join("");
    return `<p class="mg-taphint">👆 Tap a day to compare the models hour by hour.</p><div class="mg-scroll"><table class="mg-models"><thead><tr><th></th>${names.map((x) => `<th>${esc(x)}</th>`).join("")}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  // --- Hora a hora de un día (ventana a pantalla completa al tocar un día de «What each model says») -------------
  // Filas: horas; columnas: cada modelo y el ensemble de ECMWF. Si un modelo solo da pasos de 3 h ese día, sus horas
  // van en una celda de 3 filas (Open-Meteo reparte la lluvia del bloque a partes iguales: no hay detalle horario).
  // Arriba, el resumen para la ventana de escalada del día (la de la página de condiciones) o, si no hay, las horas de luz.
  const sheet = { date: null, mode: "rain", night: false };
  const hourOf = (t) => +t.slice(11, 13);
  const block = (t, off) => Math.floor((Date.parse(t + ":00Z") / 3.6e6 - off / 3600 - 1) / 3);
  const WET = 0.1;

  function dayHours(mh, date) { return mh.time.map((t, i) => [t, i]).filter(([t]) => t.startsWith(date)).map(([, i]) => i); }

  function climbInfo(date) {
    const days = (window.NucMeteo.days || []);
    const x = days.find((y) => y.date === date);
    let from = 7, to = 19, label = "daylight";
    if (x && x.sun && x.sun.daylight && x.sun.daylight.length) {
      from = Math.floor(x.sun.daylight[0][0] / 60); to = Math.ceil(x.sun.daylight[x.sun.daylight.length - 1][1] / 60);
    }
    const dl = [from, to];
    if (x && x.window) { from = +x.window.from.slice(0, 2); to = +x.window.to.slice(0, 2) || 24; label = "your window"; }
    return { from, to, label, dl, cat: x && x.cat };
  }

  function sheetSummary(mh, idx, ci) {
    const names = Object.keys(mh.models);
    const win = idx.filter((i) => hourOf(mh.time[i]) >= ci.from && hourOf(mh.time[i]) < ci.to);
    const tot = names.map((n) => [n, win.reduce((a, i) => a + (mh.models[n].p[i] || 0), 0), win.some((i) => mh.models[n].p[i] != null)]).filter(([, , ok]) => ok);
    const wet = tot.filter(([, mm]) => mm >= WET);
    const pp = Math.max(0, ...win.map((i) => mh.ens.pp[i] || 0));
    const range = `${String(ci.from).padStart(2, "0")}–${String(ci.to).padStart(2, "0")} h`;
    let verdict, cls;
    if (!tot.length) { verdict = "No model data for these hours"; cls = "ok"; }
    else if (!wet.length) { verdict = `Dry in all ${tot.length} models`; cls = pp >= 30 ? "ok" : "great"; }
    else if (wet.length === tot.length) { verdict = `Rain in all ${tot.length} models`; cls = "bad"; }
    else { verdict = `Rain in ${wet.length} of ${tot.length} models`; cls = wet.length * 2 >= tot.length ? "bad" : "ok"; }
    const who = wet.sort((a, b) => b[1] - a[1]).map(([n, mm]) => `${esc(n)} ${mm.toFixed(1)} mm`).join(", ");
    return `<div class="mg-sum nuc-c-${cls}"><div><small>${ci.label === "your window" ? "Your climbing window" : "Daylight"} · ${range}</small></div>
      <b>${verdict}</b>${who ? `<div>${who}</div>` : ""}<div>ECMWF ensemble: ${pp ? `up to ${pp} % chance of rain in an hour` : "no member rains"}</div></div>`;
  }

  function sheetTable(mh, idx, ci) {
    const names = Object.keys(mh.models), di = mh.dates.indexOf(sheet.date), off = mh.utc_offset || 0;
    const rows = sheet.night ? idx : idx.filter((i) => hourOf(mh.time[i]) >= ci.dl[0] - 1 && hourOf(mh.time[i]) <= ci.dl[1]);
    const nowT = new Date(Date.now() + off * 1000).toISOString().slice(0, 13);
    const cols = names.map((n) => ({ n, s: mh.models[n], step: mh.models[n].step[di] }));
    if (sheet.mode === "rain") cols.push({ n: "ECMWF ens.", ens: true, s: mh.ens, step: mh.ens.step[di] });
    const span = (col, k) => {  // cuántas filas visibles ocupa la celda que empieza en la fila k (0 si va dentro de otra)
      if (col.step !== 3 || sheet.mode !== "rain") return 1;  // temperatura y viento: Open-Meteo los interpola cada hora
      const b = block(mh.time[rows[k]], off);
      if (k > 0 && block(mh.time[rows[k - 1]], off) === b) return 0;
      let n = 1;
      while (k + n < rows.length && block(mh.time[rows[k + n]], off) === b) n++;
      return n;
    };
    const rainBg = (mm) => (mm >= WET ? `background:rgba(61,127,184,${Math.min(0.85, 0.18 + mm / 3)});${mm >= 1.5 ? "color:#fff;" : ""}` : "");
    const cell = (col, k, n) => {
      const ii = rows.slice(k, k + n), i = ii[0], rs = n > 1 ? ` rowspan="${n}"` : "", blk = n > 1 ? " mg-blk" : "";
      if (col.ens) {
        const pp = Math.max(...ii.map((j) => col.s.pp[j] ?? -1));
        return pp < 0 ? `<td${rs} class="mg-na${blk}">–</td>` : `<td${rs} class="mg-ens${blk}" style="${pp ? `background:rgba(139,29,139,${0.08 + pp / 130});${pp >= 60 ? "color:#fff;" : ""}` : ""}">${pp ? pp + "%" : "·"}</td>`;
      }
      const s = col.s;
      if (sheet.mode === "rain") {
        const v = ii.map((j) => s.p[j]).filter((x) => x != null);
        if (!v.length) return `<td${rs} class="mg-na${blk}">–</td>`;
        const mm = v.reduce((a, b) => a + b, 0);
        return `<td${rs} class="mg-rc${blk}" style="${rainBg(mm / v.length)}">${mm >= WET ? mm.toFixed(1) : "·"}${n > 1 ? `<i>${n} h</i>` : ""}</td>`;
      }
      if (sheet.mode === "temp") return s.t[i] == null ? `<td class="mg-na">–</td>` : `<td>${Math.round(s.t[i])}°</td>`;
      if (s.w[i] == null) return `<td class="mg-na">–</td>`;
      return `<td class="mg-wc"><i style="transform:rotate(${(s.d[i] || 0) + 180}deg)">↑</i>${s.w[i]}${s.g[i] != null ? ` <b>${s.g[i]}</b>` : ""}</td>`;
    };
    const agree = (i) => {
      const ms = names.map((n) => mh.models[n]);
      if (sheet.mode === "rain") {
        const v = ms.map((m) => m.p[i]).filter((x) => x != null);
        if (!v.length) return `<td class="mg-na">–</td>`;
        const w = v.filter((x) => x >= WET).length;
        const [t, c] = w === 0 ? ["Dry", "great"] : w === v.length ? ["All wet", "bad"] : [`${w}/${v.length} wet`, w * 2 >= v.length ? "bad" : "ok"];
        return `<td><span class="nuc-badge nuc-c-${c}">${t}</span></td>`;
      }
      if (sheet.mode === "temp") {
        const v = ms.map((m) => m.t[i]).filter((x) => x != null);
        if (!v.length) return `<td class="mg-na">–</td>`;
        const sp = Math.max(...v) - Math.min(...v);
        return `<td><span class="nuc-badge nuc-c-${sp <= 2 ? "great" : sp <= 4 ? "ok" : "bad"}">±${(sp / 2).toFixed(1)}°</span></td>`;
      }
      const g = ms.map((m) => m.g[i]).filter((x) => x != null);
      return g.length ? `<td><span class="nuc-badge nuc-c-${Math.max(...g) >= 50 ? "bad" : Math.max(...g) >= 30 ? "ok" : "great"}">${Math.max(...g)}</span></td>` : `<td class="mg-na">–</td>`;
    };
    const body = rows.map((i, k) => {
      const t = mh.time[i], h = hourOf(t);
      const cls = [h >= ci.from && h < ci.to ? "mg-win" : "", t.slice(0, 13) === nowT ? "mg-nowrow" : "", t.slice(0, 13) < nowT ? "mg-past" : ""].join(" ");
      const cells = cols.map((c) => { const n = span(c, k); return n ? cell(c, k, n) : ""; }).join("");
      return `<tr class="${cls}"><th>${t.slice(11, 16)}${t.slice(0, 13) === nowT ? "<em>now</em>" : ""}</th>${agree(i)}${cells}</tr>`;
    }).join("");
    const head = { rain: "Rain", temp: "Spread", wind: "Max gust" }[sheet.mode];
    return `<div class="mg-scroll mg-hscroll"><table class="mg-hours"><thead><tr><th></th><th>${head}</th>${cols.map((c) => `<th>${c.ens ? "ECMWF<br>ens." : c.n === "Météo-France" ? "Météo<br>France" : esc(c.n)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  function renderSheet() {
    const d = st.data, mh = d && d.model_hours;
    let el = document.getElementById("mg-sheet");
    if (!sheet.date || !mh) { if (el) el.remove(); document.documentElement.classList.remove("mg-noscroll"); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "mg-sheet";
      el.className = "nuc mg-sheet";
      document.body.appendChild(el);
      el.addEventListener("click", sheetClick);
    }
    document.documentElement.classList.add("mg-noscroll");
    const di = mh.dates.indexOf(sheet.date), idx = dayHours(mh, sheet.date), ci = climbInfo(sheet.date);
    const prev = mh.dates[di - 1], next = mh.dates[di + 1];
    const seg = (v, l) => `<button type="button" data-hmode="${v}" class="${sheet.mode === v ? "on" : ""}">${l}</button>`;
    const legend = {
      rain: "mm of rain in each hour (blue = more). <b>Wet</b> = 0.1 mm or more. The ensemble column is the share of ECMWF's 51 runs that rain in that hour: how sure that model is.",
      temp: "Air temperature at the crag's altitude. <b>Spread</b>: how far apart the models are (± half the gap between warmest and coldest).",
      wind: "Mean wind km/h and <b>gust</b>; the arrow points where it blows. Max gust: the highest of all models.",
    }[sheet.mode];
    el.innerHTML = `<div class="mg-shead">
        <button type="button" class="mg-x" data-hclose="1" aria-label="Close">✕</button>
        <div class="mg-stitle"><b>${dayName(sheet.date)}</b><small>hour by hour, each model</small></div>
        <span class="mg-nav"><button type="button" data-hgo="${prev || ""}" ${prev ? "" : "disabled"}>‹</button><button type="button" data-hgo="${next || ""}" ${next ? "" : "disabled"}>›</button></span>
      </div>
      <div class="mg-sbody">
        ${sheetSummary(mh, idx, ci)}
        <div class="nuc-row"><div class="nuc-seg">${seg("rain", "🌧 Rain")}${seg("temp", "🌡 Temp")}${seg("wind", "💨 Wind")}</div>
          <label class="mg-nightt"><input type="checkbox" data-hnight="1" ${sheet.night ? "checked" : ""}> night</label></div>
        ${sheetTable(mh, idx, ci)}
        <p class="nuc-muted">${legend} Rows with a purple edge are ${ci.label === "your window" ? "your climbing window" : "the daylight hours"}.
        A cell that covers several hours (“3 h”) is a model that only gives 3-hour steps for this day: its rain is spread evenly, so it can't tell which of those hours it falls in.</p>
      </div>`;
  }

  function sheetClick(ev) {
    const b = ev.target.closest("button, input");
    if (!b) return;
    if (b.dataset.hclose) { history.state && history.state.mgSheet ? history.back() : closeSheet(); return; }
    if (b.dataset.hgo) { sheet.date = b.dataset.hgo; renderSheet(); return; }
    if (b.dataset.hmode) { sheet.mode = b.dataset.hmode; renderSheet(); return; }
    if (b.dataset.hnight) { sheet.night = b.checked; renderSheet(); }
  }
  function openSheet(date) {
    sheet.date = date;
    history.pushState({ ...(history.state || {}), mgSheet: 1 }, "");
    renderSheet();
  }
  function closeSheet() { sheet.date = null; renderSheet(); }
  window.addEventListener("popstate", () => { if (sheet.date) closeSheet(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet.date) history.back(); });

  function risksBlock(d) {
    if (!d.risks.length) return `<p class="mg-ok">✓ No storms, ice, fog, strong gusts or heat expected in the coming days.</p>`;
    return `<ul class="mg-risks">${d.risks.map((r) => `<li class="mg-r-${r.level}">${ICON[r.kind] || "!"} <b>${dayName(r.date)}</b> ${esc(r.text)}</li>`).join("")}</ul>`;
  }

  function card(key, crag) {
    const open = isOpen();
    let body = "";
    if (open) {
      if (st.key !== key || (!st.data && !st.loading && !st.error)) setTimeout(() => load(key, window.NucMeteo._rerender), 0);
      if (st.loading || !st.data) body = st.error ? `<div class="nuc-pad">Couldn't load: ${esc(st.error)}</div>` : `<div class="nuc-pad">Loading models and ensemble…</div>`;
      else {
        const d = st.data;
        const trend = d.summary.pressure_trend;
        body = `<div class="nuc-pad"><h3>Warnings</h3>${risksBlock(d)}
          <p class="nuc-muted">Pressure ${trend <= -3 ? "falling fast" : trend < -1 ? "falling" : trend >= 3 ? "rising fast" : trend > 1 ? "rising" : "steady"} (${trend > 0 ? "+" : ""}${trend} hPa in 24 h)${d.summary.vpd_24h != null ? ` · drying power next 24 h: ${d.summary.vpd_24h < 0.3 ? "poor" : d.summary.vpd_24h < 0.8 ? "moderate" : "good"} (VPD ${d.summary.vpd_24h} kPa)` : ""} · grid elevation ${Math.round(d.elevation)} m</p></div>
          <div class="nuc-pad"><h3>Meteogram · next 72 h</h3>${meteogram(d)}${hourInfo(d)}
          <div class="nuc-legend"><span><i style="background:#e0475e"></i>Temperature</span><span><i style="background:#4f97a3"></i>Dew point</span><span><i style="background:#f6c9cf"></i>Ensemble 10–90 %</span><span><i style="background:#3d7fb8"></i>Rain</span><span><i style="background:#b9d4ea"></i>Rain (wet members 90 %)</span><span><i style="background:#8b1d8b"></i>Chance</span><span><i style="background:#6b6b66"></i>Wind / gusts</span><span><i style="background:#e3a33a"></i>Drying power</span></div></div>
          <div class="nuc-pad"><h3>What each model says</h3>${modelsTable(d)}
          <p class="nuc-muted">Max/min temperature and rain per day; wind from 8 to 20 h: mean km/h, <b>max gust</b> and where it comes from (the arrow points where it blows). Météo-France uses AROME (1.3 km) where it reaches, which includes northern Spain, and ARPEGE elsewhere.</p></div>
          <div class="nuc-pad"><h3>Rain radar</h3>${st.radar ? `<div id="mg-radar" class="mg-radar"></div><p class="nuc-muted" id="mg-radar-t"></p>` : `<button type="button" class="nuc-outline" data-radar="1">Show live rain radar</button>`}</div>
          <p class="nuc-pad nuc-muted">Open-Meteo (ECMWF IFS, ICON, GFS, Météo-France AROME/ARPEGE, UKMO; ECMWF ensemble with ${d.ensemble.members} members), temperatures corrected to the crag's altitude · radar RainViewer.</p>`;
      }
    }
    return `<section class="nuc-card" id="nuc-meteo"><button type="button" class="nuc-pad nuc-row mg-toggle" data-meteo="1"><h2>Detailed weather</h2><span>${open ? "▴" : "▾ models, ensemble, meteogram, radar"}</span></button>${open ? body : ""}</section>`;
  }

  async function radar(crag) {
    if (!window.L) {
      await new Promise((ok, ko) => {
        const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"; document.head.appendChild(css);
        const s = document.createElement("script"); s.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"; s.onload = ok; s.onerror = ko; document.head.appendChild(s);
      });
    }
    const el = document.getElementById("mg-radar");
    if (!el || el._map) return;
    const map = L.map(el, { zoomControl: true, attributionControl: true }).setView([crag.lat, crag.lon], 8);
    el._map = map;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12, attribution: "© OpenStreetMap" }).addTo(map);
    L.circleMarker([crag.lat, crag.lon], { radius: 6, color: "#fff", weight: 2, fillColor: "#8b1d8b", fillOpacity: 1 }).addTo(map);
    const meta = await (await fetch("https://api.rainviewer.com/public/weather-maps.json")).json();
    const frames = [...meta.radar.past, ...(meta.radar.nowcast || [])];
    const layers = frames.map((f) => L.tileLayer(`${meta.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`, { opacity: 0, maxNativeZoom: 7, maxZoom: 12, attribution: "RainViewer" }).addTo(map));
    let i = 0;
    const label = document.getElementById("mg-radar-t");
    const show = () => {
      layers.forEach((l, k) => l.setOpacity(k === i ? 0.7 : 0));
      if (label) label.textContent = `Radar ${new Date(frames[i].time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}${i >= meta.radar.past.length ? " (forecast)" : ""} · last 2 h, looping`;
      i = (i + 1) % frames.length;
    };
    show();
    el._timer = setInterval(() => { if (!document.body.contains(el)) clearInterval(el._timer); else show(); }, 700);
  }

  function bind(root, key, crag, rerender) {
    window.NucMeteo._rerender = rerender;
    root.querySelectorAll("[data-meteo]").forEach((b) => b.addEventListener("click", () => {
      localStorage.setItem(OPEN_KEY, isOpen() ? "0" : "1");
      rerender();
    }));
    root.querySelectorAll("[data-hday]").forEach((r) => r.addEventListener("click", () => openSheet(r.dataset.hday)));
    root.querySelectorAll("[data-radar]").forEach((b) => b.addEventListener("click", () => { st.radar = true; rerender(); }));
    if (st.radar && st.data) radar(crag).catch((e) => console.warn("radar", e));
    const svg = root.querySelector("[data-mg]");
    if (svg) svg.addEventListener("click", (ev) => {
      const r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      const x = ((ev.clientX - r.left) / r.width) * vb.width;
      const L0 = +svg.dataset.l, R0 = +svg.dataset.r, n = +svg.dataset.n;
      st.sel = Math.max(0, Math.min(n - 1, Math.floor(((x - L0) / (vb.width - L0 - R0)) * n)));
      rerender();
    });
  }

  window.NucMeteo = { card, bind, _rerender: () => {} };
})();
