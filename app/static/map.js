/* Mapa de condiciones (8anu-proxy): escuelas de 8a.nu agrupadas (cada grupo enseña la mejor y la peor condición de
 * sus escuelas), filtrables por estilo, vías en un rango de grados, valoración y condiciones, coloreadas por el día
 * elegido. Las condiciones se piden para todo lo visible (si no son demasiadas) por tandas de BATCH. */
(function () {
  "use strict";
  const BATCH = 400, MAX_VIEW = 2000;
  const FKEY = "nuc-map-filters", VKEY = "nuc-map-view";
  const RANK = { great: 3, good: 2, ok: 1, bad: 0 };
  const LABEL = { great: "Great", good: "Good", ok: "OK", bad: "Bad" };
  const COLOR = { great: "#1e8a4c", good: "#8fd8ac", ok: "#ffd166", bad: "#e0475e", none: "#a3a39d" };
  // Índices de grado de 8a.nu → francés (deportiva) / Font (bloque).
  const GRADES = [[3, "3a"], [6, "4a"], [9, "4c"], [11, "5a"], [12, "5b"], [14, "5c"], [16, "6a"], [17, "6a+"], [18, "6b"],
    [19, "6b+"], [21, "6c"], [22, "6c+"], [23, "7a"], [24, "7a+"], [25, "7b"], [26, "7b+"], [27, "7c"], [28, "7c+"], [29, "8a"],
    [30, "8a+"], [31, "8b"], [32, "8b+"], [33, "8c"], [34, "8c+"], [35, "9a"], [36, "9a+"], [37, "9b"], [38, "9b+"], [39, "9c"]];
  const gname = (i, cat) => { const g = GRADES.filter(([k]) => k <= i).pop(); const s = g ? g[1] : "?"; return cat === 1 ? s.toUpperCase() : s; };
  const DAYN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  const prefs = (() => { try { return Object.assign({ tmin: 12, tmax: 22 }, JSON.parse(localStorage.getItem("nuc-prefs") || "{}")); } catch (e) { return { tmin: 12, tmax: 22 }; } })();
  const st = { crags: [], cond: {}, retries: {}, day: 0, markers: new Map(), loading: false };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- Filtros ---------------------------------------------------------------------------------------------------------
  const form = $("#m-filters");
  for (const name of ["gfrom", "gto"]) form.elements[name].innerHTML = GRADES.map(([k, g]) => `<option value="${k}">${g}</option>`).join("");
  const saved = (() => { try { return JSON.parse(localStorage.getItem(FKEY) || "{}"); } catch (e) { return {}; } })();
  const f0 = Object.assign({ cat: "0", gfrom: "16", gto: "29", inrange: "5", rating: "0", cond: "", q: "" }, saved);
  for (const [k, v] of Object.entries(f0)) if (form.elements[k]) form.elements[k].value = v;
  function filters() {
    const e = form.elements;
    return { cat: e.cat.value, gfrom: +e.gfrom.value, gto: Math.max(+e.gfrom.value, +e.gto.value), inrange: +e.inrange.value || 0,
             rating: +e.rating.value || 0, cond: e.cond.value, q: e.q.value.trim().toLowerCase() };
  }
  // Vías de la escuela con grado en el rango (el índice de grado empieza en 1).
  const inRange = (c, f) => { let n = 0; for (let i = f.gfrom; i <= f.gto; i++) n += c.g[i - 1] || 0; return n; };
  function pass(c, f, withCond = true) {
    if (f.cat !== "" && String(c.cat) !== f.cat) return false;
    if (f.inrange > 0 ? inRange(c, f) < f.inrange : inRange(c, f) === 0) return false;
    if (c.rating < f.rating) return false;
    if (f.q && !`${c.name} ${c.area || ""} ${c.country || ""}`.toLowerCase().includes(f.q)) return false;
    if (withCond && f.cond) { const d = dayOf(c); if (d && RANK[d.cat] < RANK[f.cond]) return false; }
    return true;
  }
  $("#m-filters-btn").addEventListener("click", () => { form.hidden = !form.hidden; setTimeout(() => map.invalidateSize(), 50); });
  form.addEventListener("input", () => {
    const f = filters();
    localStorage.setItem(FKEY, JSON.stringify(Object.assign({}, f, { gfrom: String(f.gfrom), gto: String(f.gto), inrange: String(f.inrange) })));
    rebuild(); schedule();
  });

  // --- Días ------------------------------------------------------------------------------------------------------------
  const today = new Date();
  $("#m-days").innerHTML = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today.getTime() + i * 86400000);
    return `<button type="button" data-d="${i}" class="${i === 0 ? "on" : ""}">${i === 0 ? "Today" : i === 1 ? "Tomorrow" : `${DAYN[d.getDay()]} ${d.getDate()}`}</button>`;
  }).join("");
  $("#m-days").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    st.day = +b.dataset.d;
    document.querySelectorAll("#m-days button").forEach((x) => x.classList.toggle("on", x === b));
    if (filters().cond) rebuild(); else recolor();
  });
  const dayOf = (c) => { const x = st.cond[c.key]; return x && x.days[st.day]; };

  // --- Mapa y grupos ---------------------------------------------------------------------------------------------------
  const hash = location.hash.slice(1).split(",").map(Number);
  const view = hash.length >= 2 && hash.every((x) => !isNaN(x)) ? { c: [hash[0], hash[1]], z: hash[2] || 10 }
    : (() => { try { return JSON.parse(localStorage.getItem(VKEY)); } catch (e) { return null; } })() || { c: [40.4, -3.7], z: 6 };
  const map = L.map("m-map", { preferCanvas: true }).setView(view.c, view.z);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);

  // Al abrir (salvo que venga un #lat,lon de una escuela) se centra en el usuario mostrando ~100 km alrededor; el botón
  // ◎ lo vuelve a hacer. Mientras el navegador da la posición se ve la última vista.
  const RADIUS_M = 100000;
  let me = null;
  function locate(manual) {
    if (!navigator.geolocation) { if (manual) alert("Location not available in this browser"); return; }
    navigator.geolocation.getCurrentPosition((pos) => {
      const ll = L.latLng(pos.coords.latitude, pos.coords.longitude);
      if (me) me.setLatLng(ll); else me = L.circleMarker(ll, { radius: 7, color: "#fff", weight: 3, fillColor: "#1a73e8", fillOpacity: 1 }).addTo(map).bindTooltip("You");
      // El zoom en el que el lado corto del mapa abarca ~2×RADIUS (con zoom entero, fitBounds se queda uno más lejos).
      const px = Math.min(map.getSize().x, map.getSize().y);
      const mPerPx = (2 * RADIUS_M) / px;
      const z = Math.round(Math.log2((156543 * Math.cos((ll.lat * Math.PI) / 180)) / mPerPx));
      map.setView(ll, Math.max(5, Math.min(12, z)));
    }, (err) => {
      if (manual) alert(`Couldn't get your location: ${err.message}`);
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 10 * 60 * 1000 });
  }
  const LocateCtl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const b = L.DomUtil.create("a", "leaflet-bar m-locate");
      b.href = "#"; b.title = "Show crags around me"; b.textContent = "◎";
      L.DomEvent.on(b, "click", (e) => { L.DomEvent.stop(e); locate(true); });
      return b;
    },
  });
  map.addControl(new LocateCtl());
  if (!(hash.length >= 2 && hash.every((x) => !isNaN(x)))) locate(false);

  // Cada grupo: un círculo partido, mitad con la mejor condición de sus escuelas y mitad con la peor, y el número.
  const clusters = L.markerClusterGroup({
    maxClusterRadius: 50, showCoverageOnHover: false, chunkedLoading: true,
    iconCreateFunction(cluster) {
      let best = null, worst = null, known = 0;
      for (const m of cluster.getAllChildMarkers()) {
        const cat = m.options.cat;
        if (!cat) continue;
        known++;
        if (best === null || RANK[cat] > RANK[best]) best = cat;
        if (worst === null || RANK[cat] < RANK[worst]) worst = cat;
      }
      const n = cluster.getChildCount(), size = n < 10 ? 34 : n < 100 ? 40 : 46;
      const bg = known ? `conic-gradient(${COLOR[worst]} 0 50%, ${COLOR[best]} 50% 100%)` : COLOR.none;
      const title = known ? `${n} crags · best ${LABEL[best]}, worst ${LABEL[worst]}${known < n ? ` (${known} with conditions)` : ""}` : `${n} crags`;
      return L.divIcon({ html: `<div class="m-cl" style="background:${bg};width:${size}px;height:${size}px" title="${title}"><span>${n}</span></div>`,
                         className: "m-cl-wrap", iconSize: [size, size] });
    },
  }).addTo(map);

  map.on("moveend", () => {
    const c = map.getCenter();
    localStorage.setItem(VKEY, JSON.stringify({ c: [c.lat, c.lng], z: map.getZoom() }));
    list(); schedule();
  });

  function popup(c) {
    const d = dayOf(c), x = st.cond[c.key], f = filters();
    const src = x ? { user: "set by you", thecrag: "from theCrag", osm: "from OpenStreetMap cliffs", dem: "from the elevation model", terrain: "estimated from terrain", default: "not known yet" }[x.aspect_source] : "";
    const cond = d ? `<p><span class="m-badge c-${d.cat}">${LABEL[d.cat]}</span> ${d.window ? `best ${d.window.from}–${d.window.to}` : "no good window"} · ${d.tmax}°/${d.tmin}° · ${d.rain} mm · ${esc(d.rock)}</p>
      <p class="m-muted">Wall faces ${esc(x.aspect)} (${src})</p>` : "<p class=\"m-muted\">Conditions not loaded yet</p>";
    return `<div class="m-pop"><b>${esc(c.name)}</b><br><small>${esc(c.area || c.country)} · ${c.cat === 1 ? "boulder" : "sport"} · ${c.routes} routes · <b>${inRange(c, f)}</b> in ${gname(f.gfrom, c.cat)}–${gname(f.gto, c.cat)} · ★${c.rating}</small>
      ${cond}<a href="/crags/${c.key}/conditions">Full conditions →</a> · <a href="/crags/${c.key}/routes">Routes</a></div>`;
  }

  function marker(c) {
    let m = st.markers.get(c.key);
    if (!m) {
      m = L.circleMarker([c.lat, c.lon], { radius: 7, weight: 1, color: "#333", fillOpacity: 0.95, fillColor: COLOR.none });
      m.bindPopup(() => popup(c));
      st.markers.set(c.key, m);
    }
    const d = dayOf(c);
    m.options.cat = d ? d.cat : null;
    m.setStyle({ fillColor: d ? COLOR[d.cat] : COLOR.none });
    return m;
  }

  // Filtros cambiados: se rehace el grupo con las escuelas que pasan.
  function rebuild() {
    const f = filters();
    clusters.clearLayers();
    clusters.addLayers(st.crags.filter((c) => pass(c, f)).map(marker));
    list();
  }
  // Día cambiado o condiciones nuevas: solo colores.
  function recolor() {
    const f = filters();
    for (const c of st.crags) if (st.markers.has(c.key) && pass(c, f, false)) marker(c);
    clusters.refreshClusters();
    list();
  }

  function visible() {
    const f = filters(), b = map.getBounds();
    return st.crags.filter((c) => b.contains([c.lat, c.lon]) && pass(c, f, false));
  }
  function list() {
    const f = filters(), inView = visible();
    const withCond = inView.filter((c) => dayOf(c) && pass(c, f)).sort((a, z) => dayOf(z).score - dayOf(a).score || z.ascents - a.ascents);
    $("#m-list").innerHTML = withCond.slice(0, 60).map((c) => {
      const d = dayOf(c);
      return `<li><span class="m-badge c-${d.cat}">${LABEL[d.cat]}</span><a href="/crags/${c.key}/conditions"><b>${esc(c.name)}</b>
        <small>${d.window ? `${d.window.from}–${d.window.to}` : "no good window"} · ${d.tmax}°/${d.tmin}°${d.rain ? ` · ${d.rain} mm` : ""} · ${esc(c.area || c.country)} · ${inRange(c, f)} routes ${gname(f.gfrom, c.cat)}–${gname(f.gto, c.cat)}</small></a></li>`;
    }).join("");
    const missing = inView.filter((c) => !st.cond[c.key]).length;
    $("#m-status").textContent = `${inView.length} crags in view` +
      (inView.length > MAX_VIEW ? ` · zoom in to load conditions (up to ${MAX_VIEW})` : missing ? ` · loading conditions for ${missing}…` : "") +
      ` · ideal ${prefs.tmin}–${prefs.tmax} °C`;
  }

  // --- Condiciones -------------------------------------------------------------------------------------------------------
  let timer = null;
  function schedule(ms = 400) { clearTimeout(timer); timer = setTimeout(loadConditions, ms); }
  async function loadConditions() {
    if (st.loading) { schedule(); return; }
    const inView = visible();
    if (inView.length > MAX_VIEW) { list(); return; }
    const want = inView.filter((c) => !st.cond[c.key]).sort((a, z) => z.ascents - a.ascents);
    if (!want.length) return;
    st.loading = true;
    for (let i = 0; i < want.length; i += BATCH) {
      const chunk = want.slice(i, i + BATCH);
      try {
        const r = await fetch(`/_proxy/map/conditions?tmin=${prefs.tmin}&tmax=${prefs.tmax}&keys=${chunk.map((c) => c.key).join(",")}`);
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || r.status);
        Object.assign(st.cond, d);
      } catch (e) {
        $("#m-status").textContent = `Couldn't load conditions: ${e.message || e}`;
        break;
      }
      if (filters().cond) rebuild(); else recolor();
    }
    st.loading = false;
    retryAspects();
  }
  // Las escuelas sin orientación todavía (el servidor la está buscando en OpenStreetMap o en el relieve) se vuelven a
  // pedir un poco después, unas pocas veces.
  let retryTimer = null;
  function retryAspects() {
    clearTimeout(retryTimer);
    const pending = Object.keys(st.cond).filter((k) => st.cond[k].aspect_source === "default" && (st.retries[k] || 0) < 6);
    if (!pending.length) return;
    retryTimer = setTimeout(() => {
      for (const k of pending) { st.retries[k] = (st.retries[k] || 0) + 1; delete st.cond[k]; }
      schedule(0);
    }, 20000);
  }

  (async () => {
    $("#m-status").textContent = "Loading crags… (the first time of the day it takes a while)";
    const r = await fetch("/_proxy/map/crags");
    st.crags = await r.json();
    rebuild(); schedule(0);
  })();
})();
