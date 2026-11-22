# 8anu-proxy

Proxy inverso de [8a.nu](https://www.8a.nu). Cambiando `www.8a.nu` por `8a.adrianlzt.com` en cualquier URL se ve
la misma página, servida desde el proxy (copia el enfoque del [de theCrag](https://github.com/adrianlzt/thecrag-proxy)),
con un añadido: **condiciones para escalar** en la pestaña «Conditions» de cada escuela.

## Condiciones

En `/crags/<cat>/<país>/<escuela>/conditions` (la pestaña «Conditions», que en 8a.nu es de pago) el proxy pinta su
propia página de condiciones: próximos 7 días y 5 pasados, tiempo hora a hora, mejor ventana de 3 horas, gráfica de
puntuación, sol en la pared, «por qué estas condiciones», preferencias, webcams y «Know this crag?». También está
suelta en `/_proxy/conditions/<cat>/<país>/<escuela>`.

- **Frontend**: `app/static/conditions.{js,css}`, inyectados en todo el HTML de 8a.nu. El JS espera a que Vue hidrate
  la página (si entra antes, Vue se queda el div) y se mete en `.crags-column-center` bajo la cabecera de la escuela,
  escondiendo el muro de «Log in» si lo hay. Las preferencias (rango de temperatura, unidades) van en `localStorage`.
- **Datos** (`GET /_proxy/conditions/…/data?tmin=&tmax=`, `app/conditions_api.py`): la escuela y sus sectores de la API
  pública de 8a.nu (`/api/unification/outdoor/v1/web/crags/…`, sin sesión: coordenadas, sectores, `rainSafety`,
  temporada); el tiempo de [Open-Meteo](https://open-meteo.com) (horario, 5 días atrás + 8 adelante, caché 30 min).
- **Orientación de la pared**: la que haya puesto el usuario en «Know this crag?» (`POST …/aspect`, también por
  sector) o, si no, estimada por el relieve: elevaciones de Open-Meteo en 16 rumbos a 60–300 m; la pared mira al lado
  contrario de donde más sube el terreno (`rays_aspect`). Es una estimación (las coordenadas de 8a.nu no siempre
  están en la pared) y la página lo dice.
- **Sol en la pared**: posición del sol (NOAA) cada 5 min; hay sol si está a más de 3° y por delante de la pared. La
  incertidumbre (±) es la diferencia con la orientación ±15°. No tiene en cuenta el horizonte (montañas).
- **Puntuación v2** (`app/conditions.py`, ideas de [SendScore](https://www.sendscore.info/thescore)), por horas,
  0–100 → Great ≥80, Good ≥65, OK ≥45, Bad. Se simula la roca hora a hora: temperatura con inercia (~3 h; sol en la
  pared, aire, viento, enfriamiento nocturno con cielo despejado), humedad acumulada (lluvia × absorción del tipo de
  roca —el de 8a.nu, `ROCKS`— × abrigo si es `rainSafety`; condensación cuando la roca está por debajo del punto de
  rocío; secado por sol, viento y aire seco) y viento efectivo (entero si sopla de cara a la pared, un tercio de
  espaldas; rachas). Factores 0–1 (temperatura sentida respecto al rango del usuario, humedad, viento, sol, rocío,
  roca mojada, lluvia y su probabilidad, luz) multiplicados. El día vale la **peor hora de su mejor ventana de 3 h**.
  «Why these conditions?» enseña cada factor con una barra. Las preferencias tienen estilo (deportiva 12–22 °C,
  bloque 4–14 °C por defecto). No está ajustada con ascensiones.
- **Meteorología detallada** (`app/meteo.py`, `static/meteo.js`, `GET …/meteo`): tarjeta plegada al final de la página
  de condiciones; solo se pide al abrirla (y se recuerda abierta). Avisos por día (tormentas: CAPE ≥800 J/kg con
  índice de elevación ≤−2; isocero a menos de 300 m de la altitud de la escuela; niebla: visibilidad <1 km de día;
  rachas ≥60 km/h; UV ≥8; calor ≥32 °C), tendencia de presión y poder de secado (déficit de presión de vapor);
  meteograma SVG de 72 h por paneles (temperatura con la banda p10–p90 del ensemble de ECMWF de 51 miembros, punto de
  rocío, lluvia con su p90 y la probabilidad del ensemble, nubes bajas/medias/altas, viento, rachas y dirección, VPD;
  al tocar enseña todos los valores de esa hora); tabla por día de ECMWF IFS, ICON, GFS, Météo-France (AROME 1,3 km en
  el norte de España) y UKMO con su grado de acuerdo; radar de lluvia en directo (RainViewer, Leaflet cargado a
  demanda). Caché de 30 min por escuela; tres llamadas a Open-Meteo al abrirla.
- **Mapa** (`/_proxy/map`, `app/map_api.py`, `static/map.*`, enlace «🗺 Conditions map» en cada página de
  condiciones): las ~13 800 escuelas de 8a.nu con su histograma de vías por grado (API del mapa de 8a.nu,
  `collection/v1/web/map/items`, pedida por recuadros que se parten en cuatro cuando se llenan, porque la segunda
  página da 500). Bajarla entera son unos 6 min, así que se guarda en `~/.cache/8anu-proxy/map-crags.json` y se
  renueva en segundo plano cada día. Leaflet + agrupación de marcadores: cada grupo es un círculo con la mejor
  condición (izquierda) y la peor (derecha) de sus escuelas. Filtros: estilo, rango de grados y **mínimo de vías en
  ese rango** (Urdón no sale con 9a–9c), valoración, condiciones y nombre; selector de día.
  Condiciones de todo lo visible (hasta 2000 escuelas, por tandas de 400): el tiempo se pide por celdas de 0,1°
  (las escuelas cercanas lo comparten), hasta 100 celdas por llamada a Open-Meteo, con una hora de caché; el sol de
  cada día se calcula una vez por lugar. 300 escuelas ≈ 2 llamadas y 4 s; repetido, 0,1 s.
- **Orientación** (`app/aspect.py`): usuario > acantilados de OpenStreetMap (`natural=cliff` a <250 m; en OSM la
  pared mira a la derecha del sentido de la línea; Overpass por cuadrículas de 0,25°, guardadas en
  `~/.cache/8anu-proxy/cliffs/`, con servidores alternativos porque Overpass va saturado) > relieve (Open-Meteo) >
  sur supuesto. En el mapa se resuelven en segundo plano (relieve enseguida, OSM despacio) y el mapa vuelve a
  preguntar; en la página de una escuela se resuelve al abrirla.
- **Datos del usuario**: orientaciones y webcams en SQLite (`~/.local/share/8anu-proxy/data.db`, `DATA_DIR`).

## Croquis

Tarjeta plegada «Topos» bajo la cabecera de cada escuela (y de sus sectores: solo los de ese sector), con el visor
propio `static/topos.js` (ver `app/topos.py`). **Modelo único**: un croquis es una foto más una lista de vías con su
trazo (puntos en píxeles de la foto), vengan de donde vengan:

- **8a.nu premium**, importados con `tools/eight/import_topos.py ~/tmp/8a-alias` de lo bajado con la cuenta alias
  (ver el `ESTADO.md` de esa carpeta): la ficha de cada escuela (`datos/escuelas.jsonl`: acceso, aproximación, parking,
  coordenadas, sectores), los croquis (`crops/`, que 8a.nu sirve con las líneas, números y grados ya pintados:
  `baked`), las fotos de acceso (`accesos/`) y, cuando exista `datos/vias.jsonl`, el trazo de cada vía (la API de vías
  de 8a.nu da `path` en píxeles de `reference_width`, el hash del croquis y el número; `--only-routes` para
  importarlo después). Las imágenes se enlazan en duro en `DATA_DIR/topos/<hash>.jpg`; tablas `crag_info`, `topo` y
  `topo_route` en `data.db`. Con `tools/eight/fetch_blank.py --cookie ck.txt` se baja además la versión **sin líneas**
  (`blank=true`) y el visor pinta él las líneas (encadenadas en verde, grosor por popularidad, botón de ocultar).
  `tools/eight/after_download.sh` espera a que acabe la descarga de la carpeta alias y hace las dos cosas.
- **theCrag**: el visor los pide a `/_proxy/topos/thecrag`, que se los pide una vez al proxy de theCrag
  (`THECRAG_PROXY`, por defecto `http://127.0.0.1:8471`) y los guarda en la base `THECRAG_TTL_DAYS` días (30); las
  fotos se sirven desde `/_proxy/topos/img/tc/<sha1>.jpg` (bajadas de image.thecrag.com la primera vez, en
  `topos/thecrag/`). Si theCrag corta, lo guardado aunque sea viejo; «refresh» en el visor los vuelve a pedir.
- **Papel**: una foto (página de guía, pantallazo) subida desde el propio visor («+ Paper topo»; `PUT
  …/upload?heading=&sector=&baked=`, JPEG o PNG en el cuerpo, en `DATA_DIR/topos/paper/`) y las vías trazadas en el
  editor (✎ en cada croquis local, también para corregir los de 8a.nu): se elige la vía de la lista de 8a.nu de la
  escuela (API pública de vías por el proxy: nombre, grado, id, y así encadenes y «Log it») o una suelta, y se toca la
  foto de abajo arriba; `PUT /_proxy/topos/<hash>/routes` las guarda, `PATCH`/`DELETE /_proxy/topos/<hash>` retoca o
  retira. La skill «croquis-papel» de la operadora hace el grueso (cruce de números con vías) y deja el ajuste fino
  al editor.
- **Sin red**: en la página suelta (`…/page`, enlace «Open alone» en la tarjeta), «Save offline» registra un service
  worker de ámbito `/_proxy/topos/` (`static/topos-sw.js`, servido en `/_proxy/topos/sw.js`) y guarda en la caché
  `nut-v1` la página, el JS, el JSON y las fotos de esa escuela; `/_proxy/topos/` lista lo guardado. No toca el resto
  del proxy.

`GET /_proxy/topos/<cat>/<país>/<escuela>[?sector=]` devuelve ficha premium, fotos de acceso y croquis con vías;
`/_proxy/topos/img/<hash>.jpg[?blank=1]` la foto; `…/page` una página suelta con el visor. Todo pide la contraseña
del proxy (`PROXY_PASSWORD`): es contenido de pago. **Encadenes**: los de quien mira, con su sesión de 8a.nu: el
visor pide por el proxy `/api/dotnet/me` (su slug) y sus ascensiones de esa escuela (`ascent/v1/web/users/<slug>/
ascents?searchQuery=<escuela>`, 10 min en sessionStorage) y cruza por slug de vía (`sent`) o por nombre (en la misma
escuela `sent`; en otra con grado cercano `maybe`); sin sesión no hay verde. El servidor no guarda ascensiones. En
el visor: filtros All / Popular / Sent / Popular to do y por fuente; en las fotos con líneas pintadas,
tocar una vía la resalta (trazo invisible encima) y las encadenadas llevan un punto verde al final; «Log it on 8a.nu»
abre su página de vía. El bloque «Access & approach» enseña la aproximación premium, parking y escuela en Google Maps
y las fotos de acceso.

## Cómo funciona

- **8a.nu** es una app Nuxt (SSR + SPA). El HTML, la API (`/api/dotnet/…`, `/api/unification/…`) y el JS/CSS
  (`/_nuxt/…`) salen todos de www.8a.nu, así que pasan por el proxy. Imágenes (CloudFront `d3byf4kaqtov0k`,
  `d1ffqbcmevre4q`…), Mapbox, YouTube, anuncios y reCAPTCHA se cargan directamente de su origen.
- **Reescritura**: `https://www.8a.nu` (y `//`, `\/\/` en JSON, `//` en el payload de Nuxt) pasa a ser el
  origen del proxy en HTML, JSON, JS y CSS, y en `Location`; `Set-Cookie` pierde el `Domain=`. Hacia arriba,
  `Referer`, `Origin` y cuerpos de formularios vuelven a 8a.nu.
- **Login**: 8a.nu usa OpenID Connect contra el Keycloak de Vertical-Life (`vlatka.vertical-life.info/auth/…`,
  cliente `8a-nu`). `/login` crea la sesión (`nu8a_session`) y manda a Keycloak con
  `redirect_uri=https://www.8a.nu/callback`, que es la única que Keycloak acepta. Si el navegador fuera a vlatka
  directamente, al acabar Keycloak lo mandaría a www.8a.nu y la sesión se quedaría allí. Por eso el proxy sirve también
  Keycloak bajo `/auth/` (en www.8a.nu esa ruta no existe): `vlatka.vertical-life.info` se reescribe al origen del
  proxy, sus cookies (`Path=/auth/realms/Vertical-Life/`) se quedan en el proxy, y la redirección final a
  `https://www.8a.nu/callback?code=…` se convierte en `/callback` del proxy, que canjea el código con 8a.nu. Las URLs
  **codificadas** en parámetros (la `redirect_uri`) no se tocan a propósito.
  Funciona con usuario y contraseña; «Sign in with Apple» (broker de Keycloak) vuelve a vlatka directamente y no.
- **reCAPTCHA**: algunas acciones (comentarios, borrar ascensiones…) mandan un token de reCAPTCHA, cuya clave de sitio
  está limitada a los dominios de 8a.nu. En el proxy puede fallar.
- **WAF**: 8a.nu va detrás de Cloudflare con gestión de bots. Con httpx y cabeceras de navegador (lo que basta en
  theCrag) dejó pasar ~160 peticiones y luego cortó al proxy con 403 «Attention Required» durante más de 20 minutos,
  mientras que a curl desde la misma IP lo seguía dejando: mira la huella TLS/HTTP2. Por eso el proxy habla con 8a.nu
  con [curl_cffi](https://github.com/lexiforest/curl_cffi), imitando a la misma familia de navegador que el cliente
  (`impersonate_for`: Chrome, Chrome Android, Safari iOS, Safari, Firefox) y pasándole su `User-Agent` (la versión
  móvil de 8a.nu depende de él). La sesión de curl_cffi descarta las cookies (`discard_cookies`): van y vienen en
  las cabeceras de cada cliente.
  Además, el script de detección de Cloudflare (`/cdn-cgi/challenge-platform/…/jsd`, pasa por el proxy) marca como bot
  a un Chrome **headless** y a partir de ahí da 403 a las peticiones con esa cookie de sesión: las pruebas con
  Playwright cargan media portada y se cortan. Un navegador normal no tiene ese problema, y una petición sin cookies
  (curl) sigue pasando. Para reducir peticiones, los ficheros de `/_nuxt/` (con hash, `immutable`) se guardan en
  `~/.cache/8anu-proxy` (`CACHE_DIR`) y solo se piden una vez, y no van más de 6 peticiones a la vez
  (`MAX_CONCURRENCY`).
- **No indexable**: `X-Robots-Tag: noindex` en todo y `robots.txt` con `Disallow: /`. Con `PROXY_PASSWORD` pide
  autenticación básica la primera vez (luego cookie `nuproxy_auth` de un año).

## Desarrollo

```sh
uv run pytest -q
uv run uvicorn app.main:app --port 8472   # http://localhost:8472/
```

Con curl basta `curl -H 'Accept: text/html' http://localhost:8472/…` (el proxy pone la huella y las cabeceras de
Chrome). Un navegador headless acaba bloqueado por Cloudflare (ver arriba).

## Despliegue en arc2

Como el de theCrag: servicio de usuario (`deploy/8anu-proxy.service`, `127.0.0.1:8472`) publicado por el túnel de
Cloudflare de arc2 (`cloudflared.service`, gestionado desde el panel de Zero Trust): hostname público
`8a.adrianlzt.com` → `http://localhost:8472`.

```sh
cp deploy/8anu-proxy.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now 8anu-proxy
git pull && systemctl --user restart 8anu-proxy   # nueva versión
journalctl --user -u 8anu-proxy                   # logs
```
