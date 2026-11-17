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
      const cells = names.map((nm) => { const v = m.models[nm][i]; return `<td>${v ? `${v.tmax}°/${v.tmin}°<br><small>${v.rain == null ? "–" : v.rain + " mm"}</small>` : "–"}</td>`; }).join("");
      const badge = a ? `<span class="nuc-badge nuc-c-${AGREE[a.level][1]}" title="Max temp spread ${a.tspread}°, ${a.wet_models}/${a.models} models with ≥1 mm">${AGREE[a.level][0]}</span>` : "";
      return `<tr><th>${dayName(date)}</th>${cells}<td>${badge}</td></tr>`;
    }).join("");
    return `<div class="mg-scroll"><table class="mg-models"><thead><tr><th></th>${names.map((x) => `<th>${esc(x)}</th>`).join("")}<th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

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
          <p class="nuc-muted">Max/min temperature and rain per day. Météo-France uses AROME (1.3 km) where it reaches, which includes northern Spain, and ARPEGE elsewhere.</p></div>
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
