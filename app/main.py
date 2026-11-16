"""Proxy inverso de www.8a.nu.

https://8anu.adrianlzt.com/<ruta> sirve https://www.8a.nu/<ruta>: reescribe los enlaces absolutos, las redirecciones
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
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

UPSTREAM = os.environ.get("UPSTREAM", "https://www.8a.nu").rstrip("/")
# Keycloak de Vertical-Life: sus rutas empiezan todas por /auth/, que en www.8a.nu no existe (404).
AUTH_UPSTREAM = os.environ.get("AUTH_UPSTREAM", "https://vlatka.vertical-life.info").rstrip("/")
AUTH_PREFIX = "/auth/"
PASSWORD = os.environ.get("PROXY_PASSWORD", "")
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "6"))
AUTH_COOKIE = "nuproxy_auth"
ROBOTS = "noindex, nofollow, noarchive, nosnippet"

# 8a.nu va detrás de Cloudflare, como theCrag: se presenta como un navegador para que el WAF no lo bloquee. El
# user-agent del navegador del cliente se respeta (el del proxy solo sustituye al de un cliente HTTP).
USER_AGENT = os.environ.get(
    "NU8A_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
)
BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "es-ES,es;q=0.9,en;q=0.8",
}
# httpx pone los nombres de cabecera en minúscula; en HTTP/1.1 los WAF de Cloudflare lo notan (en theCrag daba 403).
HEADER_CASE = {
    b"accept": b"Accept",
    b"accept-encoding": b"Accept-Encoding",
    b"accept-language": b"Accept-Language",
    b"content-type": b"Content-Type",
    b"cookie": b"Cookie",
    b"if-modified-since": b"If-Modified-Since",
    b"if-none-match": b"If-None-Match",
    b"origin": b"Origin",
    b"pragma": b"Pragma",
    b"referer": b"Referer",
    b"user-agent": b"User-Agent",
    b"x-requested-with": b"X-Requested-With",
}

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade", "host", "content-length",
}  # fmt: skip
# Cabeceras de la petición que se reenvían. El resto no: ni las del túnel (IP y país del cliente, cf-*,
# x-forwarded-*) ni las de navegador (sec-ch-*, sec-fetch-*). `authorization` sí: la API de 8a.nu podría usar
# tokens Bearer además de la cookie de sesión.
FORWARD_REQUEST = {
    "user-agent", "accept", "accept-language", "cookie", "referer", "origin", "content-type", "authorization",
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


def _header_name(name: str) -> str:
    return HEADER_CASE.get(name.encode(), b"-".join(p.capitalize() for p in name.encode().split(b"-"))).decode()


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
    for k, v in BROWSER_HEADERS.items():
        headers.setdefault(k, v)
    if headers.get("accept") == "*/*" and request.method == "GET" and not request.url.path.startswith("/api/"):
        headers["accept"] = BROWSER_HEADERS["accept"]
    if not headers.get("user-agent") or headers["user-agent"].lower().startswith(("curl/", "python-", "httpx/")):
        headers["user-agent"] = USER_AGENT
    headers["accept-encoding"] = "gzip, deflate, br"
    return {_header_name(k): v for k, v in headers.items()}


def _response_headers(upstream: httpx.Response, origin: str) -> list[tuple[bytes, bytes]]:
    out = []
    for k, v in upstream.headers.multi_items():
        lk = k.lower()
        if lk in DROP_RESPONSE or lk.startswith("cf-"):
            continue
        if lk in ("location", "content-location", "link", "refresh"):
            v = to_public(v, origin)
        out.append((lk.encode(), v.encode("latin-1", "replace")))
    for cookie in upstream.headers.get_list("set-cookie"):
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
    client: httpx.AsyncClient = request.app.state.client
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
        req = client.build_request(request.method, url, headers=_request_headers(request, origin), content=body)
        upstream = await client.send(req, stream=True)
        headers = _response_headers(upstream, origin)
        if PASSWORD and request.cookies.get(AUTH_COOKIE) != _auth_token():
            headers.append(
                (
                    b"set-cookie",
                    f"{AUTH_COOKIE}={_auth_token()}; Max-Age=31536000; Path=/; HttpOnly; Secure; SameSite=Lax".encode(),
                )
            )
        ctype = upstream.headers.get("content-type", "")
        if cache and upstream.status_code == 200:
            try:
                await upstream.aread()
            finally:
                await upstream.aclose()
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(f"{cache.name}.{os.getpid()}.{id(request)}.tmp")
            tmp.write_bytes(upstream.content)
            tmp.replace(cache)
            data = upstream.content
            if _is_text(ctype):
                data = to_public(upstream.text, origin).encode(upstream.encoding or "utf-8", "replace")
            resp = Response(data, status_code=200)
            resp.raw_headers = [h for h in headers if h[0] not in (b"content-length",)] + [
                (b"content-length", str(len(data)).encode())
            ]
            return resp
        if not _is_text(ctype) or request.method == "HEAD":
            resp = StreamingResponse(
                upstream.aiter_bytes(), status_code=upstream.status_code, background=BackgroundTask(upstream.aclose)
            )
            resp.raw_headers = headers
            return resp
        try:
            await upstream.aread()
        finally:
            await upstream.aclose()

    content = to_public(upstream.text, origin).encode(upstream.encoding or "utf-8", "replace")
    resp = Response(content, status_code=upstream.status_code)
    resp.raw_headers = [h for h in headers if h[0] != b"content-type"] + [
        (b"content-type", ctype.encode()),
        (b"content-length", str(len(content)).encode()),
    ]
    return resp


@asynccontextmanager
async def lifespan(app: Starlette):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, http2=False
    ) as client:
        app.state.client = client
        app.state.sem = asyncio.Semaphore(MAX_CONCURRENCY)
        yield


METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
app = Starlette(
    routes=[
        Route("/robots.txt", robots),
        Route("/_proxy/healthz", healthz),
        Route("/{path:path}", proxy, methods=METHODS),
    ],
    lifespan=lifespan,
)
