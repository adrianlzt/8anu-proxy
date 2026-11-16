/* Mapa de condiciones (8anu-proxy): escuelas de 8a.nu filtrables y coloreadas por las condiciones del día elegido.
 * Las condiciones se piden solo para las escuelas visibles (las MAX con más ascensiones) y se guardan por día. */
(function () {
  "use strict";
  const MAX = 60;
  const FKEY = "nuc-map-filters", VKEY = "nuc-map-view";
  const RANK = { great: 3, good: 2, ok: 1, bad: 0 };
  const LABEL = { great: "Great", good: "Good", ok: "OK", bad: "Bad" };
  const COLOR = { great: "#1e8a4c", good: "#8fd8ac", ok: "#ffd166", bad: "#e0475e", none: "#9a9a94" };
  // Índices de grado de 8a.nu → francés (deportiva) / Font (bloque).
  const GRADES = [[3, "3a"], [6, "4a"], [9, "4c"], [11, "5a"], [12, "5b"], [14, "5c"], [16, "6a"], [17, "6a+"], [18, "6b"],
    [19, "6b+"], [21, "6c"], [22, "6c+"], [23, "7a"], [24, "7a+"], [25, "7b"], [26, "7b+"], [27, "7c"], [28, "7c+"], [29, "8a"],
    [30, "8a+"], [31, "8b"], [32, "8b+"], [33, "8c"], [34, "8c+"], [35, "9a"], [36, "9a+"], [37, "9b"], [38, "9b+"], [39, "9c"]];
  const gname = (i, cat) => { const g = GRADES.filter(([k]) => k <= i).pop(); const s = g ? g[1] : "?"; return cat === 1 ? s.toUpperCase() : s; };
  const DAYN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  const prefs = (() => { try { return Object.assign({ tmin: 12, tmax: 22 }, JSON.parse(localStorage.getItem("nuc-prefs") || "{}")); } catch (e) { return { tmin: 12, tmax: 22 }; } })();
  const st = { crags: [], cond: {}, day: 0, markers: new Map(), pending: false };
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- Filtros ---------------------------------------------------------------------------------------------------------
  const form = $("#m-filters");
  for (const name of ["gfrom", "gto"]) {
    form.elements[name].innerHTML = GRADES.map(([k, g]) => `<option value="${k}">${g}</option>`).join("");
  }
  const saved = (() => { try { return JSON.parse(localStorage.getItem(FKEY) || "{}"); } catch (e) { return {}; } })();
  const defaults = { cat: "0", gfrom: "16", gto: "29", routes: "20", rating: "0", cond: "", q: "" };
  const f0 = Object.assign({}, defaults, saved);
  for (const [k, v] of Object.entries(f0)) {
    const el = form.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
  function filters() {
    const e = form.elements;
    return { cat: e.cat.value, gfrom: +e.gfrom.value, gto: +e.gto.value, routes: +e.routes.value || 0, rating: +e.rating.value || 0,
             cond: e.cond.value, q: e.q.value.trim().toLowerCase() };
  }
  function saveFilters() {
    const f = filters();
    localStorage.setItem(FKEY, JSON.stringify(Object.assign({}, f, { gfrom: String(f.gfrom), gto: String(f.gto) })));
  }
  function pass(c, f) {
    if (f.cat !== "" && String(c.cat) !== f.cat) return false;
    // Hay vías en el rango si el rango de la escuela lo solapa.
    if (c.gmin != null && c.gmax != null && (c.gmax < f.gfrom || c.gmin > f.gto)) return false;
    if (c.routes < f.routes || c.rating < f.rating) return false;
    if (f.q && !`${c.name} ${c.area || ""} ${c.country || ""}`.toLowerCase().includes(f.q)) return false;
    if (f.cond) {
      const d = dayOf(c);
      if (d && RANK[d.cat] < RANK[f.cond]) return false;
    }
    return true;
  }
  $("#m-filters-btn").addEventListener("click", () => { form.hidden = !form.hidden; setTimeout(() => map.invalidateSize(), 50); });
  form.addEventListener("input", () => { saveFilters(); redraw(); schedule(); });

  // --- Días ------------------------------------------------------------------------------------------------------------
  const today = new Date();
  $("#m-days").innerHTML = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today.getTime() + i * 86400000);
    const l = i === 0 ? "Today" : i === 1 ? "Tomorrow" : `${DAYN[d.getDay()]} ${d.getDate()}`;
    return `<button type="button" data-d="${i}" class="${i === 0 ? "on" : ""}">${l}</button>`;
  }).join("");
  $("#m-days").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    st.day = +b.dataset.d;
    document.querySelectorAll("#m-days button").forEach((x) => x.classList.toggle("on", x === b));
    redraw();
  });
  const dayOf = (c) => { const x = st.cond[c.key]; return x && x.days[st.day]; };

  // --- Mapa ------------------------------------------------------------------------------------------------------------
  // #lat,lon,zoom (enlace desde la página de condiciones de una escuela) o la última vista.
  const hash = location.hash.slice(1).split(",").map(Number);
  const view = hash.length >= 2 && hash.every((x) => !isNaN(x)) ? { c: [hash[0], hash[1]], z: hash[2] || 10 }
    : (() => { try { return JSON.parse(localStorage.getItem(VKEY)); } catch (e) { return null; } })() || { c: [40.4, -3.7], z: 6 };
  const map = L.map("m-map", { preferCanvas: true }).setView(view.c, view.z);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
  map.on("moveend", () => {
    const c = map.getCenter();
    localStorage.setItem(VKEY, JSON.stringify({ c: [c.lat, c.lng], z: map.getZoom() }));
    redraw(); schedule();
  });

  function popup(c) {
    const d = dayOf(c), x = st.cond[c.key];
    const grades = c.gmin != null ? `${gname(c.gmin, c.cat)}–${gname(c.gmax, c.cat)}` : "?";
    const cond = d ? `<p><span class="m-badge c-${d.cat}">${LABEL[d.cat]}</span> ${d.window ? `best ${d.window.from}–${d.window.to}` : "no good window"} · ${d.tmax}°/${d.tmin}° · ${d.rain} mm · ${esc(d.rock)}</p>
      <p class="m-muted">Wall ${esc(x.aspect)}${x.aspect_known ? "" : " (not known: assumed south)"}</p>` : "<p>Conditions not loaded: zoom in</p>";
    return `<div class="m-pop"><b>${esc(c.name)}</b><br><small>${esc(c.area || c.country)} · ${c.cat === 1 ? "boulder" : "sport"} · ${c.routes} routes · ${grades} · ★${c.rating}${c.walk ? ` · ${c.walk} min` : ""}${c.rain ? " · rain-safe" : ""}</small>
      ${cond}<a href="/crags/${c.key}/conditions">Full conditions →</a> · <a href="/crags/${c.key}/routes">Routes</a></div>`;
  }

  function redraw() {
    const f = filters(), b = map.getBounds();
    const seen = new Set();
    for (const c of st.crags) {
      if (!pass(c, f)) continue;
      seen.add(c.key);
      const d = dayOf(c);
      const color = d ? COLOR[d.cat] : COLOR.none;
      let m = st.markers.get(c.key);
      if (!m) {
        m = L.circleMarker([c.lat, c.lon], { radius: 6, weight: 1, color: "#333", fillOpacity: 0.9 });
        m.bindPopup(() => popup(c));
        st.markers.set(c.key, m);
      }
      m.setStyle({ fillColor: color, radius: d ? 8 : 5 });
      if (!map.hasLayer(m)) m.addTo(map);
    }
    for (const [k, m] of st.markers) if (!seen.has(k) && map.hasLayer(m)) map.removeLayer(m);
    // Lista: las visibles con condiciones, de mejor a peor.
    const inView = st.crags.filter((c) => seen.has(c.key) && b.contains([c.lat, c.lon]));
    const withCond = inView.filter((c) => dayOf(c)).sort((a, z) => dayOf(z).score - dayOf(a).score || z.ascents - a.ascents);
    $("#m-list").innerHTML = withCond.slice(0, 50).map((c) => {
      const d = dayOf(c);
      const grades = c.gmin != null ? `${gname(c.gmin, c.cat)}–${gname(c.gmax, c.cat)}` : "";
      return `<li><span class="m-badge c-${d.cat}">${LABEL[d.cat]}</span><a href="/crags/${c.key}/conditions"><b>${esc(c.name)}</b>
        <small>${d.window ? `${d.window.from}–${d.window.to}` : "no good window"} · ${d.tmax}°/${d.tmin}°${d.rain ? ` · ${d.rain} mm` : ""} · ${esc(c.area || c.country)} · ${c.routes} routes ${grades}</small></a></li>`;
    }).join("");
    const missing = inView.length - withCond.length;
    $("#m-status").textContent = `${inView.length} crags in view · conditions for ${withCond.length}` +
      (missing > 0 ? ` (zoom in to load the rest; up to ${MAX} at a time, most climbed first)` : "") +
      ` · ideal ${prefs.tmin}–${prefs.tmax} °C`;
  }

  let timer = null;
  function schedule() { clearTimeout(timer); timer = setTimeout(loadConditions, 400); }
  async function loadConditions() {
    if (st.pending) { schedule(); return; }
    const f = filters(), b = map.getBounds();
    const noCond = Object.assign({}, f, { cond: "" });
    const want = st.crags.filter((c) => !st.cond[c.key] && pass(c, noCond) && b.contains([c.lat, c.lon]))
      .sort((a, z) => z.ascents - a.ascents).slice(0, MAX);
    if (!want.length) return;
    st.pending = true;
    $("#m-status").textContent = `Loading conditions for ${want.length} crags…`;
    try {
      const r = await fetch(`/_proxy/map/conditions?tmin=${prefs.tmin}&tmax=${prefs.tmax}&keys=${want.map((c) => c.key).join(",")}`);
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      Object.assign(st.cond, d);
    } catch (e) {
      $("#m-status").textContent = `Couldn't load conditions: ${e.message || e}`;
    }
    st.pending = false;
    redraw();
  }

  (async () => {
    $("#m-status").textContent = "Loading crags…";
    const r = await fetch("/_proxy/map/crags");
    st.crags = await r.json();
    redraw(); schedule();
  })();
})();
