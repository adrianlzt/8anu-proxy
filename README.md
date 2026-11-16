# 8anu-proxy

Proxy inverso de [8a.nu](https://www.8a.nu). Cambiando `www.8a.nu` por `8anu.adrianlzt.com` en cualquier URL se ve
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
- **WAF**: 8a.nu va detrás de Cloudflare. Como en theCrag, el proxy manda cabeceras de navegador (`User-Agent`,
  `Accept`, `Accept-Language`, nombres en su grafía). Además, el WAF corta al proxy con 403 «Attention Required» si
  le llegan muchas peticiones seguidas (pasó con ~160 en 2–3 minutos, al cargar páginas con un navegador headless):
  por eso los ficheros de `/_nuxt/` (con hash e `immutable`) se guardan en `~/.cache/8anu-proxy` (`CACHE_DIR`) y solo
  se piden una vez, y no se mandan más de 6 peticiones a la vez (`MAX_CONCURRENCY`).
- **No indexable**: `X-Robots-Tag: noindex` en todo y `robots.txt` con `Disallow: /`. Con `PROXY_PASSWORD` pide
  autenticación básica la primera vez (luego cookie `nuproxy_auth` de un año).

## Desarrollo

```sh
uv run pytest -q
uv run uvicorn app.main:app --port 8472   # http://localhost:8472/
```

Para probar con curl hace falta un `User-Agent` de navegador completo y `Accept: text/html`.

## Despliegue en arc2

Como el de theCrag: servicio de usuario (`deploy/8anu-proxy.service`, `127.0.0.1:8472`) publicado por el túnel de
Cloudflare de arc2 (`cloudflared.service`, gestionado desde el panel de Zero Trust): hostname público
`8anu.adrianlzt.com` → `http://localhost:8472`.

```sh
cp deploy/8anu-proxy.service ~/.config/systemd/user/ && systemctl --user daemon-reload
systemctl --user enable --now 8anu-proxy
git pull && systemctl --user restart 8anu-proxy   # nueva versión
journalctl --user -u 8anu-proxy                   # logs
```
