/* Service worker del visor de croquis (ámbito /_proxy/topos/): lo que Adrián guarda con «Save offline» (página,
 * JS, JSON e imágenes de una escuela) se sirve de la caché «nut-v1» sin red. Las imágenes van primero de caché (son
 * inmutables); lo demás primero de red y, si falla, de caché. No toca nada fuera de /_proxy/topos/. */
var CACHE = "nut-v1";
self.addEventListener("install", function (e) { e.waitUntil(caches.open(CACHE).then(function (c) { return c.add("/_proxy/topos/").catch(function () {}); }).then(function () { return self.skipWaiting(); })); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.indexOf("/_proxy/topos/") !== 0) return;
  if (url.pathname.indexOf("/_proxy/topos/img/") === 0) {
    e.respondWith(caches.match(e.request).then(function (r) { return r || fetch(e.request); }));
    return;
  }
  e.respondWith(fetch(e.request).then(function (r) {
    if (r.ok) { var copy = r.clone(); caches.open(CACHE).then(function (c) { c.match(e.request).then(function (had) { if (had) c.put(e.request, copy); }); }); }
    return r;
  }).catch(function () { return caches.match(e.request).then(function (r) { return r || new Response("Offline and not saved", { status: 503 }); }); }));
});
