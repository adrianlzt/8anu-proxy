"""Proxy inverso de www.8a.nu.

https://8a.adrianlzt.com/<ruta> sirve https://www.8a.nu/<ruta>: reescribe los enlaces absolutos, las redirecciones
y las cookies para que la navegación siga en el proxy. El login de 8a.nu es OpenID Connect contra el Keycloak de
Vertical-Life (vlatka.vertical-life.info/auth/…), que también se sirve desde el proxy bajo /auth/ para que la vuelta
(`/callback`) llegue al proxy y la sesión se quede aquí (ver README). Imágenes (CloudFront), Mapbox y reCAPTCHA se
cargan directamente de su origen.

Uso personal: con PROXY_PASSWORD pide autenticación básica (una vez; luego queda una cookie de un año) y todas las
respuestas llevan X-Robots-Tag: noindex.
"""

import asyncio
import base64
import hashlib
import hmac
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from curl_cffi.requests import AsyncSession
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from . import aspect, conditions_api, map_api

UPSTREAM = os.environ.get("UPSTREAM", "https://www.8a.nu").rstrip("/")
# Keycloak de Vertical-Life: sus rutas empiezan todas por /auth/, que en www.8a.nu no existe (404).
AUTH_UPSTREAM = os.environ.get("AUTH_UPSTREAM", "https://vlatka.vertical-life.info").rstrip("/")
AUTH_PREFIX = "/auth/"
PASSWORD = os.environ.get("PROXY_PASSWORD", "")
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))
AUTH_COOKIE = "nuproxy_auth"
ROBOTS = "noindex, nofollow, noarchive, nosnippet"

# 8a.nu va detrás de Cloudflare y su WAF bloquea al proxy (403 «Attention Required») aunque mande cabeceras de
# navegador: con httpx dejó pasar ~160 peticiones y luego lo cortó, mientras que a curl desde la misma IP lo seguía
# dejando. Mira la huella TLS/HTTP2 del cliente, así que el proxy habla con 8a.nu con curl_cffi imitando a un navegador
# (huella, HTTP/2 y cabeceras sec-ch-ua…). Se imita la familia del navegador del cliente (la versión móvil de 8a.nu
# depende del User-Agent) y se le pasa su User-Agent.
IMPERSONATE_DEFAULT = os.environ.get("NU8A_IMPERSONATE", "chrome")


def impersonate_for(user_agent: str) -> str:
    ua = user_agent.lower()
    if not ua.startswith("mozilla/"):
        return IMPERSONATE_DEFAULT
    if "iphone" in ua or "ipad" in ua:
        return "safari_ios"
    if "android" in ua:
        return "chrome_android"
    if "safari" in ua and "chrome" not in ua and "chromium" not in ua:
        return "safari"
    if "firefox" in ua:
        return "firefox"
    return "chrome"


HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "host", "content-length",
}  # fmt: skip
# Cabeceras de la petición que se reenvían. El resto no: ni las del túnel (IP y país del cliente, cf-*,
# x-forwarded-*) ni las de navegador (sec-ch-*, sec-fetch-*, user-agent), que pone curl_cffi según el navegador que
# imita (el user-agent del cliente se le pasa aparte). `authorization` sí: la API de 8a.nu podría usar
# tokens Bearer además de la cookie de sesión.
FORWARD_REQUEST = {
    "accept", "accept-language", "cookie", "referer", "origin", "content-type", "authorization",
    "x-requested-with", "if-none-match", "if-modified-since", "range", "cache-control", "pragma",
    "recaptcha-token",
}  # fmt: skip
DROP_RESPONSE = HOP_BY_HOP | {
    "content-encoding", "date", "server", "set-cookie", "report-to", "nel", "alt-svc", "strict-transport-security",
}  # fmt: skip
TEXT_TYPES = ("text/", "application/json", "application/javascript", "application/x-javascript", "+json", "+xml")

# https://www.8a.nu, //8a.nu, https:\/\/www.8a.nu (JSON), https://www.8a.nu (payload de Nuxt) y lo mismo
# con vlatka.vertical-life.info. Las URLs codificadas en parámetros (`redirect_uri=https%3A%2F%2Fwww.8a.nu%2Fcallback`)
# NO se tocan: Keycloak solo acepta como redirect_uri la de www.8a.nu, y el código se canjea con esa misma.
_SEP = r"(//|\\/\\/|\\u002F\\u002F)"
_ABS_RE = re.compile(rf"(https?:)?{_SEP}(?:www\.)?8a\.nu(?![\w.-])", re.I)
_AUTH_ABS_RE = re.compile(rf"(https?:)?{_SEP}vlatka\.vertical-life\.info(?![\w.-])", re.I)
_COOKIE_DOMAIN_RE = re.compile(r";\s*domain=[^;]*", re.I)

# Los ficheros de /_nuxt/ (JS y CSS con hash en el nombre, `immutable`) se guardan en disco: una carga de página pide
# ~60 y el WAF de 8a.nu corta al proxy (403) si le llegan demasiadas peticiones seguidas. Una vez en caché, solo se
# piden a 8a.nu el HTML y la API. Se guardan tal cual (se reescriben al servirlos) y no caducan: un despliegue nuevo
# de 8a.nu trae nombres nuevos.
CACHE_DIR = Path(os.environ.get("CACHE_DIR", Path.home() / ".cache" / "8anu-proxy"))
_CACHE_RE = re.compile(r"/_nuxt/[\w.-]+\.(js|css|woff2?|svg|png|jpg|webp)")


def _auth_token() -> str:
    return hmac.new(PASSWORD.encode(), b"8anu-proxy", hashlib.sha256).hexdigest()


def _authorized(request: Request) -> bool:
    if not PASSWORD:
        return True
    if hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _auth_token()):
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except ValueError:
            return False
        return hmac.compare_digest(pw.encode(), PASSWORD.encode())
    return False


def _origin(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def to_public(text: str, origin: str) -> str:
    scheme, host = origin.split("://", 1)

    def sub(m: re.Match) -> str:
        return (f"{scheme}:" if m.group(1) else "") + m.group(2) + host

    return _AUTH_ABS_RE.sub(sub, _ABS_RE.sub(sub, text))


def upstream_for(path: str) -> str:
    return AUTH_UPSTREAM if path.startswith(AUTH_PREFIX) else UPSTREAM


def to_upstream_url(url: str, origin: str) -> str:
    """Una URL del proxy (Referer, Origin) → la de 8a.nu o la de Keycloak según la ruta."""
    if url == origin or url.startswith(origin + "/"):
        path = url[len(origin) :]
        return upstream_for(path) + path
    return url


def to_upstream(text: str, origin: str, upstream: str) -> str:
    return text.replace(origin, upstream)


def _request_headers(request: Request, origin: str) -> dict[str, str]:
    headers = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk not in FORWARD_REQUEST:
            continue
        if lk == "cookie":
            v = "; ".join(c for c in v.split("; ") if not c.startswith(AUTH_COOKIE + "="))
            if not v:
                continue
        elif lk == "authorization" and v.lower().startswith("basic "):
            continue  # la del propio proxy (PROXY_PASSWORD)
        elif lk == "origin":
            # El Origin no lleva ruta: el de una petición a Keycloak es el de Keycloak.
            v = upstream_for(request.url.path) if v == origin else v
        elif lk == "referer":
            v = to_upstream_url(v, origin)
        headers[lk] = v
    ua = request.headers.get("user-agent", "")
    if ua.lower().startswith("mozilla/"):
        headers["user-agent"] = ua
    return headers


@dataclass
class Upstream:
    status: int
    headers: list[tuple[str, str]]
    content: bytes

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers if k.lower() == name), "")

    @property
    def text(self) -> str:
        m = re.search(r"charset=([\w-]+)", self.header("content-type"), re.I)
        return self.content.decode(m.group(1) if m else "utf-8", "replace")


async def fetch(app: Starlette, method: str, url: str, headers: dict[str, str], body: bytes, ua: str) -> Upstream:
    """Petición a 8a.nu/Keycloak con la huella del navegador del cliente. Sin seguir redirecciones (las sigue el
    navegador, por el proxy) y sin guardar cookies: van y vienen en las cabeceras de cada cliente."""
    target = impersonate_for(ua)
    sessions: dict[str, AsyncSession] = app.state.sessions
    if target not in sessions:
        sessions[target] = AsyncSession(impersonate=target, discard_cookies=True, timeout=30)
    r = await sessions[target].request(
        method, url, headers=headers, data=body or None, allow_redirects=False, default_headers=True
    )
    return Upstream(r.status_code, [(k, v) for k, v in r.headers.multi_items() if v is not None], r.content)


def _response_headers(upstream: Upstream, origin: str) -> list[tuple[bytes, bytes]]:
    out = []
    for k, v in upstream.headers:
        lk = k.lower()
        if lk in DROP_RESPONSE or lk.startswith("cf-"):
            continue
        if lk in ("location", "content-location", "link", "refresh"):
            v = to_public(v, origin)
        out.append((lk.encode(), v.encode("latin-1", "replace")))
    for cookie in (v for k, v in upstream.headers if k.lower() == "set-cookie"):
        out.append((b"set-cookie", _COOKIE_DOMAIN_RE.sub("", cookie).encode("latin-1", "replace")))
    out.append((b"x-robots-tag", ROBOTS.encode()))
    return out


_MEDIA = {
    ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".woff": "font/woff",
    ".woff2": "font/woff2", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
}  # fmt: skip


def _is_text(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return any(ct.startswith(t) or ct.endswith(t) for t in TEXT_TYPES)


async def robots(_: Request) -> Response:
    return PlainTextResponse("User-agent: *\nDisallow: /\n", headers={"x-robots-tag": ROBOTS})


async def healthz(_: Request) -> Response:
    return PlainTextResponse("ok")


def _cache_path(request: Request) -> Path | None:
    path = request.url.path
    if request.method != "GET" or request.url.query or not _CACHE_RE.fullmatch(path):
        return None
    return CACHE_DIR / path.removeprefix("/_nuxt/")


async def proxy(request: Request) -> Response:
    if not _authorized(request):
        return PlainTextResponse(
            "Autenticación requerida",
            status_code=401,
            headers={"www-authenticate": 'Basic realm="8anu-proxy"', "x-robots-tag": ROBOTS},
        )
    cache = _cache_path(request)
    if cache and cache.exists():
        data = cache.read_bytes()
        if cache.suffix in (".js", ".css"):
            data = to_public(data.decode("utf-8", "surrogateescape"), _origin(request)).encode("utf-8", "surrogateescape")
        return Response(
            data,
            media_type=_MEDIA.get(cache.suffix, "application/octet-stream"),
            headers={"cache-control": "public, max-age=31536000, immutable", "x-robots-tag": ROBOTS},
        )
    origin = _origin(request)
    upstream_base = upstream_for(request.url.path)
    url = upstream_base + request.scope["raw_path"].decode("latin-1")
    if request.url.query:
        url += "?" + request.url.query
    body = await request.body()
    req_type = request.headers.get("content-type", "")
    if body and (_is_text(req_type) or "urlencoded" in req_type):
        body = to_upstream(body.decode("utf-8", "surrogateescape"), origin, upstream_base).encode(
            "utf-8", "surrogateescape"
        )

    async with request.app.state.sem:
        upstream = await fetch(
            request.app, request.method, url, _request_headers(request, origin), body,
            request.headers.get("user-agent", ""),
        )  # fmt: skip
    headers = _response_headers(upstream, origin)
    if PASSWORD and request.cookies.get(AUTH_COOKIE) != _auth_token():
        headers.append(
            (
                b"set-cookie",
                f"{AUTH_COOKIE}={_auth_token()}; Max-Age=31536000; Path=/; HttpOnly; Secure; SameSite=Lax".encode(),
            )
        )
    if cache and upstream.status == 200:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{os.getpid()}.{id(request)}.tmp")
        tmp.write_bytes(upstream.content)
        tmp.replace(cache)
    content = upstream.content
    if _is_text(upstream.header("content-type")):
        content = to_public(upstream.text, origin).encode("utf-8")
        headers = [h for h in headers if h[0] != b"content-type"]
        ctype = re.sub(r";\s*charset=[\w-]+", "", upstream.header("content-type"), flags=re.I)
        headers.append((b"content-type", f"{ctype}; charset=utf-8".encode()))
    if upstream.header("content-type").startswith("text/html"):
        content = conditions_api.inject(content)
    if request.method == "HEAD":
        content = b""
    resp = Response(content, status_code=upstream.status)
    resp.raw_headers = headers + [(b"content-length", str(len(content)).encode())]
    return resp


@asynccontextmanager
async def lifespan(app: Starlette):
    app.state.sessions = {}
    app.state.plain = AsyncSession(timeout=20)
    app.state.sem = asyncio.Semaphore(MAX_CONCURRENCY)
    workers = [asyncio.create_task(aspect.worker(app, source)) for source in ("terrain", "osm")]
    try:
        yield
    finally:
        for w in workers:
            w.cancel()
        await app.state.plain.close()
        for session in app.state.sessions.values():
            await session.close()


METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
app = Starlette(
    routes=[
        Route("/robots.txt", robots),
        Route("/_proxy/healthz", healthz),
        *conditions_api.routes,
        *map_api.routes,
        Route("/{path:path}", proxy, methods=METHODS),
    ],
    lifespan=lifespan,
    # El JSON del mapa (~4600 escuelas) y el HTML de 8a.nu se comprimen hacia el móvil.
    middleware=[Middleware(GZipMiddleware, minimum_size=2000)],
)
