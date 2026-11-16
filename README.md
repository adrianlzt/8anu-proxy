# 8anu-proxy

Proxy inverso de [8a.nu](https://www.8a.nu). Cambiando `www.8a.nu` por `8a.adrianlzt.com` en cualquier URL se ve
la misma página, servida desde el proxy. Por ahora no cambia nada del contenido: es la base para añadirle cosas,
como el [de theCrag](https://github.com/adrianlzt/thecrag-proxy), del que copia el enfoque.

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
