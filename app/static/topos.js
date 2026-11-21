/* Visor de croquis del proxy de 8a.nu (ver app/topos.py). Una foto más sus vías con trazo, vengan de donde vengan:
 * los premium de 8a.nu importados (líneas ya pintadas en la foto, o en blanco si hay versión sin líneas), los de
 * papel subidos por Adrián y los de theCrag (pedidos a thecrag.adrianlzt.com, con el mismo formato).
 *
 *   nuTopos.render(el, {key: "sportclimbing/spain/x", sector: "slug", thecrag: {country, area, crag, sector, cragSlug}})
 *
 * Verde las encadenadas según 8a.nu (ámbar con «?» si solo casa el nombre), naranja el resto; el grosor es la
 * popularidad (ascensiones en 8a.nu). Tocar una vía la resalta y enseña su ficha; la lista de abajo hace lo mismo.
 * Filtros All / Popular / Sent / Popular to do y por fuente. ⛶ pantalla completa con pellizco y arrastre. */
(function () {
  "use strict";
  var THECRAG = "https://thecrag.adrianlzt.com";
  var CSS = ".nut{font:14px/1.4 system-ui,sans-serif;color:#222}" +
    ".nut-topo{margin:0 0 14px;border:1px solid #ddd;border-radius:8px;overflow:hidden;background:#111}" +
    ".nut-head{display:flex;align-items:center;gap:8px;padding:6px 10px;background:#f4f4f4;color:#222;font-weight:600}" +
    ".nut-head .sp{flex:1}.nut-head button{border:1px solid #bbb;background:#fff;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:14px}" +
    ".nut-src{font-size:11px;font-weight:600;padding:1px 6px;border-radius:9px;color:#fff;background:#888}.nut-src.s8a{background:#8b1d8b}.nut-src.sthecrag{background:#2a6fdb}.nut-src.spaper{background:#b36b00}" +
    ".nut-wrap{position:relative;overflow:hidden;touch-action:pan-y}.nut-wrap .zoom{position:relative;width:100%}" +
    ".nut-wrap img{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:fill;user-select:none;-webkit-user-drag:none;background:#333}" +
    ".nut-wrap svg{position:absolute;left:0;top:0;width:100%;height:100%}" +
    ".nut-wrap .r{cursor:pointer}.nut-wrap .r .ln{fill:none;stroke-linejoin:round;stroke-linecap:round}" +
    ".nut-wrap .r .halo{fill:none;stroke:#fff;stroke-linejoin:round;stroke-linecap:round;opacity:.8}" +
    ".nut-wrap .r.sel .halo{stroke:#3af}.nut-wrap .r.dim{opacity:.35}" +
    ".nut-wrap .r.baked .hit{fill:none;stroke:#000;stroke-opacity:0;pointer-events:stroke}" +
    ".nut-wrap .r.baked .halo{display:none}.nut-wrap .r.baked .ln{opacity:0}.nut-wrap .r.baked.sent .ln{opacity:.5}" +
    ".nut-wrap .r.baked.sel .halo{display:block;opacity:.9}.nut-wrap .r.baked.sel .ln{opacity:1}.nut-wrap .r.baked.dim{opacity:1}" +
    ".nut-wrap .lbl rect{stroke:#000;stroke-width:1}.nut-wrap .lbl text{font:bold 9px Tahoma,Arial;text-anchor:middle;fill:#000}" +
    ".nut-wrap .grd{font:bold 8px Tahoma,Arial;text-anchor:middle;fill:#000;stroke:#fff;stroke-width:2px;paint-order:stroke}" +
    ".nut-info{padding:6px 10px;background:#fff;color:#222;min-height:20px;font-size:13px}" +
    ".nut-info b{margin-right:6px}.nut-badge{display:inline-block;padding:0 6px;border-radius:3px;color:#fff;font-weight:bold;margin-right:6px}" +
    ".nut-legend{font-size:12px;color:#555;margin:4px 0 8px}.nut-legend i{display:inline-block;width:14px;height:4px;border-radius:2px;vertical-align:middle;margin:0 4px 0 10px}" +
    ".nut-list{list-style:none;margin:0;padding:0;background:#fff;border-top:1px solid #eee}" +
    ".nut-list li{display:flex;align-items:center;gap:8px;padding:4px 10px;border-bottom:1px solid #f2f2f2;cursor:pointer;font-size:13px}" +
    ".nut-list li.sel{background:#eef7ff}.nut-list .n{min-width:22px;height:18px;line-height:18px;text-align:center;border-radius:3px;font-weight:bold;font-size:11px;color:#000}" +
    ".nut-list .g{min-width:34px;font-weight:bold}.nut-list .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".nut-bar{position:sticky;top:0;z-index:2;display:flex;gap:6px;flex-wrap:wrap;padding:6px 0;background:#fff}" +
    ".nut-bar button{border:1px solid #bbb;background:#fff;border-radius:14px;padding:3px 10px;font-size:13px;cursor:pointer}.nut-bar button.on{background:#1f9d3a;border-color:#1f9d3a;color:#fff}" +
    ".nut-bar button.src.on{background:#8b1d8b;border-color:#8b1d8b}.nut-bar .sep{width:1px;background:#ddd;margin:0 4px}" +
    ".nut-wrap .r.off{opacity:.18}.nut-wrap .r.baked.off .ln{opacity:0}.nut-list li.off{display:none}.nut-list .asc{min-width:44px;text-align:right;color:#666;font-size:12px}" +
    ".nut-log{display:inline-block;margin:6px 0 2px;padding:4px 10px;border-radius:14px;background:#7b1fa2;color:#fff!important;text-decoration:none;font-weight:600}" +
    ".nut-msg{padding:10px;background:#fff8e1;border:1px solid #f0d080;border-radius:6px;margin:0 0 10px}" +
    ".nut-msg input{width:100%;box-sizing:border-box;margin:6px 0;padding:6px;font-size:14px}" +
    ".nut-topo:fullscreen{display:block;overflow:auto;background:#000;touch-action:pan-y}.nut-topo:fullscreen .nut-head{background:#222;color:#ddd}" +
    ".nut-topo:fullscreen .nut-wrap .zoom{transform-origin:0 0}" +
    ".nut-head button svg{display:block;width:16px;height:16px}.nut-head button.nut-lines.hid{background:#ddd}.nut-head button.nut-lines .x{display:none}.nut-head button.nut-lines.hid .x{display:inline}" +
    ".nut-topo.nolines .nut-wrap svg{visibility:hidden}.nut-topo.hide{display:none}" +
    ".nut-access{background:#fff;border:1px solid #ddd;border-radius:8px;margin:0 0 12px;overflow:hidden}.nut-access summary{padding:8px 10px;font-weight:600;cursor:pointer;background:#f4f4f4}" +
    ".nut-access .body{padding:8px 10px;font-size:13px}.nut-access p{margin:6px 0;white-space:pre-line}.nut-access .kv{color:#555;font-size:12px;margin:4px 0}" +
    ".nut-access .pics{display:flex;gap:6px;overflow-x:auto;padding:4px 0}.nut-access .pics img{height:110px;border-radius:4px;cursor:pointer;background:#333}" +
    ".nut-access a.map{display:inline-block;margin:2px 10px 2px 0;color:#2a6fdb}";
  var EIGHT = location.origin;
  var COLORS = { sent: "#1fbf3f", maybe: "#e6a700", none: "#ff8c1a" };
  var FILTERS = [["all", "All"], ["pop", "Popular"], ["sent", "Sent"], ["todo", "Popular to do"]];
  var SRC_LABEL = { "8a": "8a.nu", thecrag: "theCrag", paper: "Paper" };
  function pref(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function setPref(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var filter = pref("nut-filter", "all");
  var srcFilter = pref("nut-src", "all");
  var redraws = [];

  function markPopular(routes) {   // el tercio más repetido de su croquis (y al menos 3)
    var ranked = routes.filter(function (r) { return r.ascents != null && r.ascents > 0; }).sort(function (a, b) { return b.ascents - a.ascents; });
    var n = Math.max(3, Math.ceil(ranked.length / 3));
    ranked.forEach(function (r, i) { r.popular = i < n; });
  }
  function passes(r) {
    if (filter === "pop") return !!r.popular;
    if (filter === "sent") return r.sent === "sent";
    if (filter === "todo") return !!r.popular && r.sent !== "sent";
    return true;
  }
  function lineWidth(r) { return r.pop == null ? 3 : 1.5 + 8.5 * Math.min(100, r.pop) / 100; }
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); });
    (children || []).forEach(function (c) { e.appendChild(c); });
    return e;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function esc(s) { return String(s == null ? "" : s); }
  function num(r) { return parseInt(String(r.num || "").replace(/\D/g, ""), 10) || 0; }

  function drawTopo(t) {
    var w = t.width || 650, h = t.height || 290, baked = !!t.baked;
    var img = el("img", { src: t.blank || t.image || t.thumb, alt: t.heading || "", loading: "lazy" });
    var svg = svgEl("svg", { viewBox: "0 0 " + w + " " + h, preserveAspectRatio: "none" });
    var info = el("div", { class: "nut-info" });
    var groups = [];
    markPopular(t.routes);
    var drawn = t.routes.filter(function (r) { return r.points && r.points.length; });
    var sorted = drawn.slice().sort(function (a, b) { return (a.sent === "sent") - (b.sent === "sent"); });   // las encadenadas, encima
    sorted.forEach(function (r) {
      var color = COLORS[r.sent || "none"] || COLORS.none, lw = baked ? 4 : lineWidth(r);
      var pts = r.points.map(function (p) { return p.x + "," + p.y; }).join(" ");
      var g = svgEl("g", { class: "r" + (baked ? " baked" : "") + (r.sent === "sent" ? " sent" : "") });
      if (baked) g.appendChild(svgEl("polyline", { class: "hit", points: pts, style: "stroke-width:" + Math.max(14, w / 40) }));
      g.appendChild(svgEl("polyline", { class: "halo", points: pts, style: "stroke-width:" + (lw + 2) }));
      var ln = svgEl("polyline", { class: "ln", points: pts, stroke: color, style: "stroke-width:" + lw });
      if (r.sent === "maybe") ln.setAttribute("stroke-dasharray", "6 3");
      g.appendChild(ln);
      var p0 = r.points[0], pl = r.points[r.points.length - 1], pp = r.points.length > 1 ? r.points[r.points.length - 2] : null;
      if (!baked) {
        r.points.forEach(function (p) {
          if (p.t === "belay" || p.t === "lower") g.appendChild(svgEl("circle", { cx: p.x, cy: p.y, r: 4, fill: "#fff", stroke: "#1a3ab8", "stroke-width": 2 }));
        });
        if (pp && (pp.x !== pl.x || pp.y !== pl.y)) {
          var ang = Math.atan2(pl.y - pp.y, pl.x - pp.x) * 180 / Math.PI;
          g.appendChild(svgEl("polygon", { points: "0,0 -9,-4 -9,4", fill: color, stroke: "#fff", "stroke-width": 1, transform: "translate(" + pl.x + "," + pl.y + ") rotate(" + ang + ")" }));
        }
        if (p0 && r.num) {
          var lw2 = 14, lh = 13, lx = Math.min(Math.max(p0.x, lw2 / 2 + 1), w - lw2 / 2 - 1), ly = Math.min(p0.y + 3, h - lh - 10);
          var fill = r.sent === "sent" ? "#9be89b" : r.sent === "maybe" ? "#ffe08a" : "#ffd24d";
          var lbl = svgEl("g", { class: "lbl" });
          lbl.appendChild(svgEl("rect", { x: lx - lw2 / 2, y: ly, width: lw2, height: lh, rx: 2, fill: fill }));
          var tx = svgEl("text", { x: lx, y: ly + 10 }); tx.textContent = r.num; lbl.appendChild(tx);
          if (r.grade) { var gr = svgEl("text", { class: "grd", x: lx, y: ly + lh + 8 }); gr.textContent = r.grade; lbl.appendChild(gr); }
          g.appendChild(lbl);
        }
      } else if (r.sent && pl) {   // foto con las líneas ya pintadas: solo un punto al final de las encadenadas
        g.appendChild(svgEl("circle", { cx: pl.x, cy: pl.y, r: Math.max(4, w / 160), fill: color, stroke: "#fff", "stroke-width": 1.5 }));
      }
      var title = svgEl("title"); title.textContent = "#" + r.num + " " + r.name + " " + r.grade; g.appendChild(title);
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(r, g); });
      svg.appendChild(g); groups.push(g); r._g = g;
    });
    var list = el("ul", { class: "nut-list" });
    function fillList() {
      list.textContent = "";
      var rows = t.routes.slice();
      if (filter === "all") rows.sort(function (a, b) { return num(a) - num(b); });
      else rows.sort(function (a, b) { return (b.ascents || 0) - (a.ascents || 0); });
      rows.forEach(function (r) { list.appendChild(r._li); });
    }
    t.routes.forEach(function (r) {
      var li = el("li", {}, [el("span", { class: "n", text: r.num || "·" }), el("span", { class: "g", text: r.grade || "" }), el("span", { class: "nm", text: r.name || "" }),
        el("span", { class: "asc", text: r.in8a === false ? "–" : r.ascents != null ? r.ascents + " asc." : "" })]);
      if (r.in8a === false) li.lastChild.title = "Not found on 8a.nu";
      li.firstChild.style.background = r.sent === "sent" ? "#9be89b" : r.sent === "maybe" ? "#ffe08a" : "#ffd24d";
      if (r.sent === "sent") li.appendChild(el("span", { class: "nut-badge", text: "✓", style: "background:" + COLORS.sent }));
      if (r.sent === "maybe") li.appendChild(el("span", { class: "nut-badge", text: "?", style: "background:" + COLORS.maybe }));
      li.addEventListener("click", function () { select(r, r._g); });
      r._li = li;
    });
    function applyFilter() {
      t.routes.forEach(function (r) { var ok = passes(r); if (r._g) r._g.classList.toggle("off", !ok); r._li.classList.toggle("off", !ok); });
      fillList();
    }
    applyFilter(); redraws.push(applyFilter);
    function select(r, g) {
      var same = g && g.classList.contains("sel");
      groups.forEach(function (x) { x.classList.remove("sel"); x.classList.toggle("dim", !same && !!g && x !== g); });
      t.routes.forEach(function (x) { if (x._li) x._li.classList.toggle("sel", !same && x === r); });
      if (same) { info.textContent = ""; return; }
      if (g) g.classList.add("sel");
      if (r._li && r._li.scrollIntoView) r._li.scrollIntoView({ block: "nearest" });
      info.textContent = "";
      var b = el("span", { class: "nut-badge", text: r.sent === "sent" ? "✓" : r.sent === "maybe" ? "?" : "·" });
      b.style.background = COLORS[r.sent || "none"];
      info.appendChild(b);
      info.appendChild(el("b", { text: "#" + (r.num || "·") + " " + r.name }));
      info.appendChild(document.createTextNode((r.grade || "") + (r.stars ? " " + r.stars : "") + (r.style ? " · " + r.style : "") +
        (r.in8a === false ? " · not on 8a.nu" : r.ascents != null ? " · " + r.ascents + " ascents" + (t.source === "thecrag" && r.in8a ? " on 8a.nu" : "") : "") +
        (r.ascents_tc != null ? " (theCrag: " + r.ascents_tc + ")" : "")));
      if (r.sent_info) info.appendChild(document.createTextNode(" · sent: " + esc(r.sent_info.crag) + " " + esc(r.sent_info.grade) + (r.sent_info.date ? ", " + r.sent_info.date : "")));
      if (r.bolts || r.height) info.appendChild(document.createTextNode(" · " + [r.height, r.bolts ? r.bolts + " bolts" : ""].filter(Boolean).join(", ")));
      if (!r.points || !r.points.length) info.appendChild(document.createTextNode(" · (line not available yet)"));
      if (r.url) { info.appendChild(document.createTextNode(" ")); info.appendChild(el("a", { href: THECRAG + r.url, target: "_blank", text: "theCrag ↗" })); }
      if (r.url8a) info.appendChild(el("a", { class: "nut-log", href: EIGHT + r.url8a, target: "_blank", text: "Log it on 8a.nu ↗" }));
    }
    svg.addEventListener("click", function () { groups.forEach(function (x) { x.classList.remove("sel", "dim"); }); t.routes.forEach(function (x) { if (x._li) x._li.classList.remove("sel"); }); info.textContent = ""; });
    var zoom = el("div", { class: "zoom" }, [img, svg]);
    zoom.style.aspectRatio = w + " / " + h;
    var wrap = el("div", { class: "nut-wrap" }, [zoom]);
    var fsBtn = el("button", { type: "button", title: "Full screen", text: "⛶" });
    var linesBtn = el("button", { type: "button", class: "nut-lines", title: "Hide lines", "aria-pressed": "false" });
    linesBtn.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><polyline points="3,14 6,9 5,6 9,2"/><polyline points="10,14 11,8 13,4"/><line class="x" x1="2" y1="2" x2="14" y2="14" stroke="#c00" stroke-width="2"/></svg>';
    var src = el("span", { class: "nut-src s" + (t.source || ""), text: SRC_LABEL[t.source] || t.source || "" });
    var head = el("div", { class: "nut-head" }, [src, el("span", { text: t.heading || "" }), el("span", { class: "sp" }),
      el("span", { text: t.routes.length ? t.routes.length + " routes" : "", style: "font-weight:normal;color:#666" })]);
    if (drawn.length && !baked) head.appendChild(linesBtn);
    head.appendChild(fsBtn);
    var card = el("div", { class: "nut-topo" }, [head, wrap, info, list]);
    card.dataset.source = t.source || "";
    linesBtn.addEventListener("click", function () {
      var hid = card.classList.toggle("nolines");
      linesBtn.classList.toggle("hid", hid);
      linesBtn.title = hid ? "Show lines" : "Hide lines";
      linesBtn.setAttribute("aria-pressed", String(hid));
    });
    fsBtn.addEventListener("click", function () {
      if (document.fullscreenElement === card) document.exitFullscreen();
      else if (card.requestFullscreen) card.requestFullscreen();
    });
    pinch(wrap, zoom, card);
    return card;
  }

  // Pellizco para ampliar y arrastre para mover, solo a pantalla completa.
  function pinch(wrap, zoom, card) {
    var s = 1, tx = 0, ty = 0, pinchD = null, pan = null, drag = null;
    function on() { return document.fullscreenElement === card; }
    function apply() { zoom.style.transform = "translate(" + tx + "px," + ty + "px) scale(" + s + ")"; wrap.style.touchAction = s > 1 ? "none" : "pan-y"; }
    function dist(a, b) { var dx = a.clientX - b.clientX, dy = a.clientY - b.clientY; return Math.sqrt(dx * dx + dy * dy) || 1; }
    function zoomAt(cx, cy, f) {
      var ns = Math.max(1, Math.min(8, s * f)); if (ns === s) return;
      var r = wrap.getBoundingClientRect(), lx = (cx - r.left - tx) / s, ly = (cy - r.top - ty) / s;
      s = ns; tx = s === 1 ? 0 : cx - r.left - s * lx; ty = s === 1 ? 0 : cy - r.top - s * ly; apply();
    }
    wrap.addEventListener("touchstart", function (e) {
      if (!on()) return;
      if (e.touches.length >= 2) { pinchD = dist(e.touches[0], e.touches[1]); pan = null; e.preventDefault(); }
      else if (s > 1) pan = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    }, { passive: false });
    wrap.addEventListener("touchmove", function (e) {
      if (!on()) return;
      if (e.touches.length >= 2 && pinchD) {
        var d = dist(e.touches[0], e.touches[1]);
        zoomAt((e.touches[0].clientX + e.touches[1].clientX) / 2, (e.touches[0].clientY + e.touches[1].clientY) / 2, d / pinchD);
        pinchD = d; e.preventDefault();
      } else if (pan && e.touches.length === 1) {
        tx += e.touches[0].clientX - pan.x; ty += e.touches[0].clientY - pan.y;
        pan = { x: e.touches[0].clientX, y: e.touches[0].clientY }; apply(); e.preventDefault();
      }
    }, { passive: false });
    wrap.addEventListener("touchend", function (e) { if (e.touches.length < 2) pinchD = null; if (!e.touches.length) pan = null; });
    wrap.addEventListener("wheel", function (e) {
      if (!on()) return;
      if (e.ctrlKey) zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.01)); else if (s > 1) { tx -= e.deltaX; ty -= e.deltaY; apply(); } else return;
      e.preventDefault();
    }, { passive: false });
    wrap.addEventListener("mousedown", function (e) { if (on() && s > 1 && e.button === 0) { drag = { x: e.clientX, y: e.clientY }; e.preventDefault(); } });
    document.addEventListener("mousemove", function (e) { if (!drag) return; tx += e.clientX - drag.x; ty += e.clientY - drag.y; drag = { x: e.clientX, y: e.clientY }; apply(); });
    document.addEventListener("mouseup", function () { drag = null; });
    document.addEventListener("fullscreenchange", function () { if (!on()) { s = 1; tx = ty = 0; apply(); } });
  }

  // Acceso y aproximación premium de 8a.nu, con sus fotos (se pliega; se recuerda abierto).
  function accessBlock(d) {
    var p = (d.crag && d.crag.premium) || {}, pics = d.access || [];
    var text = [["Approach", p.premium_approach], ["Access", p.premium_access], ["Notes", p.premium_notes], ["About", p.premium_description]].filter(function (x) { return x[1]; });
    if (!text.length && !pics.length) return null;
    var det = el("details", { class: "nut-access" });
    if (pref("nut-access", "0") === "1") det.open = true;
    det.addEventListener("toggle", function () { setPref("nut-access", det.open ? "1" : "0"); });
    det.appendChild(el("summary", { text: "Access & approach (8a.nu premium)" + (pics.length ? " · " + pics.length + " photos" : "") }));
    var body = el("div", { class: "body" });
    var kv = [];
    if (p.walkingTime) kv.push("walk " + p.walkingTime + " min");
    if (p.altitude) kv.push(p.altitude + " m");
    if (p.rock_type) kv.push(p.rock_type);
    if (p.heightRange && p.heightRange.length === 2) kv.push("routes " + p.heightRange[0] + "–" + p.heightRange[1] + " m");
    if (kv.length) body.appendChild(el("div", { class: "kv", text: kv.join(" · ") }));
    var maps = el("div", { class: "kv" });
    [["Crag", p.crag_coords_premium || p.crag_coords_community], ["Parking", p.parking_coords_premium || p.parking_coords_community]].forEach(function (x) {
      var c = x[1]; if (!c || c.latitude == null) return;
      maps.appendChild(el("a", { class: "map", target: "_blank", href: "https://www.google.com/maps/search/?api=1&query=" + c.latitude + "," + c.longitude, text: "📍 " + x[0] + " on the map" }));
    });
    if (maps.childElementCount) body.appendChild(maps);
    if (pics.length) {
      var row = el("div", { class: "pics" });
      pics.forEach(function (a) {
        var im = el("img", { src: a.image, loading: "lazy", alt: "access photo" });
        im.addEventListener("click", function () {
          var card = drawTopo({ source: "8a", heading: "Access", width: a.width, height: a.height, image: a.image, baked: true, routes: [] });
          card.style.display = "none"; det.appendChild(card);
          if (card.requestFullscreen) card.requestFullscreen().then(function () { card.style.display = ""; });
          card.addEventListener("fullscreenchange", function () { if (document.fullscreenElement !== card) card.remove(); });
          document.addEventListener("fullscreenchange", function h() { if (!document.fullscreenElement) { card.remove(); document.removeEventListener("fullscreenchange", h); } });
        });
        row.appendChild(im);
      });
      body.appendChild(row);
    }
    text.forEach(function (x) {
      var pEl = el("p");
      pEl.appendChild(el("b", { text: x[0] + ": " }));
      // «__Sector de Baix:__» → negrita, como en 8a.nu.
      String(x[1]).replace(/\r\n/g, "\n").split(/(__[^_]+__)/).forEach(function (part) {
        if (/^__[^_]+__$/.test(part)) pEl.appendChild(el("b", { text: part.slice(2, -2) })); else pEl.appendChild(document.createTextNode(part));
      });
      body.appendChild(pEl);
    });
    det.appendChild(body);
    return det;
  }

  function sourceBar(container, counts) {
    var bar = container.querySelector(".nut-bar");
    bar.querySelectorAll("button.src, .sep").forEach(function (b) { b.remove(); });
    var srcs = Object.keys(counts).filter(function (k) { return counts[k]; });
    if (srcs.length < 2) { applySrc(container); return; }
    bar.appendChild(el("span", { class: "sep" }));
    [["all", "All sources"]].concat(srcs.map(function (k) { return [k, (SRC_LABEL[k] || k) + " (" + counts[k] + ")"]; })).forEach(function (f) {
      var b = el("button", { type: "button", class: "src", text: f[1] });
      if (f[0] === srcFilter) b.classList.add("on");
      b.addEventListener("click", function () {
        srcFilter = f[0]; setPref("nut-src", srcFilter);
        bar.querySelectorAll("button.src").forEach(function (x) { x.classList.toggle("on", x === b); });
        applySrc(container);
      });
      bar.appendChild(b);
    });
    applySrc(container);
  }
  function applySrc(container) {
    var any = false;
    container.querySelectorAll(".nut-topo[data-source]").forEach(function (c) { var on = srcFilter === "all" || c.dataset.source === srcFilter; c.classList.toggle("hide", !on); any = any || on; });
    if (!any && srcFilter !== "all") { srcFilter = "all"; setPref("nut-src", "all"); applySrc(container); container.querySelectorAll(".nut-bar button.src").forEach(function (x) { x.classList.toggle("on", x.textContent === "All sources"); }); }
  }

  function render(container, opts) {
    opts = opts || {};
    if (!document.getElementById("nut-css")) { var st = el("style", { id: "nut-css" }); st.textContent = CSS; document.head.appendChild(st); }
    container.classList.add("nut");
    container.textContent = "";
    container.appendChild(el("div", { class: "nut-msg", text: "Loading topos…" }));
    var q = opts.sector ? "?sector=" + encodeURIComponent(opts.sector) : "";
    fetch("/_proxy/topos/" + opts.key + q, { credentials: "same-origin" }).then(function (r) {
      if (r.status === 401) throw new Error("the proxy asks for its password: reload the page and sign in");
      return r.json();
    }).then(function (d) {
      container.textContent = "";
      var counts = {};
      var legend = el("div", { class: "nut-legend" });
      ["sent", "none", "maybe"].forEach(function (k) {
        var i = el("i"); i.style.background = COLORS[k]; legend.appendChild(i);
        legend.appendChild(document.createTextNode(k === "sent" ? "sent (8a.nu)" : k === "maybe" ? "same route elsewhere?" : "not sent"));
      });
      legend.appendChild(document.createTextNode(" · line width: ascents on 8a.nu"));
      if (opts.standalone && d.crag) legend.insertBefore(el("a", { href: "/crags/" + d.crag.key, text: d.crag.name + " on 8a.nu ↗ ", style: "margin-right:8px" }), legend.firstChild);
      container.appendChild(legend);
      var acc = d.error ? null : accessBlock(d);
      if (acc) container.appendChild(acc);
      var bar = el("div", { class: "nut-bar" });
      FILTERS.forEach(function (f) {
        var b = el("button", { type: "button", text: f[1] });
        if (f[0] === filter) b.classList.add("on");
        b.addEventListener("click", function () {
          filter = f[0]; setPref("nut-filter", filter);
          bar.querySelectorAll("button:not(.src)").forEach(function (x) { x.classList.toggle("on", x === b); });
          redraws.forEach(function (fn) { fn(); });
        });
        bar.appendChild(b);
      });
      container.appendChild(bar);
      redraws = [];
      var host = el("div", { class: "nut-topos" });
      container.appendChild(host);
      (d.topos || []).forEach(function (t) { host.appendChild(drawTopo(t)); counts[t.source] = (counts[t.source] || 0) + 1; });
      var tc = opts.thecrag || (d.crag && { country: d.crag.country, area: d.crag.area, crag: d.crag.name, cragSlug: d.crag.slug });
      var tcHost = el("div");
      container.appendChild(tcHost);
      if (!(d.topos || []).length && !tc) tcHost.appendChild(el("div", { class: "nut-msg", text: "No topos for this crag yet." }));
      sourceBar(container, counts);
      if (opts.onLoad) opts.onLoad(d);
      if (tc && tc.crag) theCrag(container, tcHost, tc, opts, counts);
    }).catch(function (e) {
      container.textContent = ""; container.appendChild(el("div", { class: "nut-msg", text: "Couldn't load the topos (" + (e.message || e) + ")." }));
    });
  }

  // Los croquis de theCrag, del proxy de theCrag (mismo formato de croquis; su búsqueda puede tardar un minuto).
  function theCrag(container, host, tc, opts, counts) {
    host.textContent = "";
    host.appendChild(el("div", { class: "nut-msg", text: "Looking for this crag on theCrag… (the first time can take up to a minute)" }));
    var q = new URLSearchParams({ country: tc.country || "", area: tc.area || "", crag: tc.crag || "" });
    if (tc.sector) q.set("sector", tc.sector);
    if (tc.tc) q.set("tc", tc.tc);
    if (tc.cragSlug) q.set("crag_slug", tc.cragSlug);
    fetch(THECRAG + "/_proxy/topos?" + q.toString()).then(function (r) { return r.json(); }).then(function (d) {
      host.textContent = "";
      if (d.error) {
        if (d.error === "not_found" && (counts["8a"] || counts.paper)) return;   // ya hay croquis: no molestar
        var msg = el("div", { class: "nut-msg" });
        msg.appendChild(el("div", { text: d.error === "not_found" ? "Can't find «" + esc(tc.crag) + "» on theCrag." :
          d.error === "thecrag_blocked" ? "theCrag isn't answering right now (it cuts us off for asking too often); try again in a few minutes." : "theCrag: " + d.error }));
        var inp = el("input", { type: "url", placeholder: "Paste the crag's URL on theCrag" });
        var btn = el("button", { type: "button", text: "Use this page" });
        btn.addEventListener("click", function () {
          var m = /thecrag\.[a-z.]+(\/en\/climbing\/[a-z0-9\/_-]+)/i.exec(inp.value || "");
          if (!m) { inp.style.borderColor = "red"; return; }
          theCrag(container, host, Object.assign({}, tc, { tc: m[1].replace(/\/(maps|photos|topos|routes|ascents)\/?$/, "") }), opts, counts);
        });
        msg.appendChild(inp); msg.appendChild(btn);
        host.appendChild(msg);
        return;
      }
      if (!d.topos.length) { if (!counts["8a"] && !counts.paper) host.appendChild(el("div", { class: "nut-msg", text: "theCrag has no topos for this crag." })); return; }
      var link = el("div", { class: "nut-legend" });
      link.appendChild(el("a", { href: THECRAG + d.crag.path, target: "_blank", text: (d.crag.name || "Crag") + " on theCrag ↗" }));
      link.appendChild(document.createTextNode(d.partial ? " · theCrag cut us off: partial" : ""));
      host.appendChild(link);
      d.topos.forEach(function (t) { t.source = "thecrag"; host.appendChild(drawTopo(t)); });
      counts.thecrag = d.topos.length;
      sourceBar(container, counts);
    }).catch(function (e) {
      host.textContent = ""; host.appendChild(el("div", { class: "nut-msg", text: "theCrag topos didn't load (" + e + ")." }));
    });
  }

  window.nuTopos = { render: render, draw: drawTopo };
})();
