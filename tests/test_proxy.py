import httpx
import pytest
from starlette.testclient import TestClient

import app.main as main

PUBLIC = "https://8a.adrianlzt.com"
KC_AUTH = (
    "https://vlatka.vertical-life.info/auth/realms/Vertical-Life/protocol/openid-connect/auth"
    "?client_id=8a-nu&redirect_uri=https%3A%2F%2Fwww.8a.nu%2Fcallback&state=s"
)
SEEN: dict[str, object] = {}


@pytest.fixture
def client(monkeypatch, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "nuproxy_auth" not in request.headers.get("cookie", "")
        SEEN.update(host=request.url.host, headers=dict(request.headers), referer=request.headers.get("referer"))
        SEEN.update(origin=request.headers.get("origin"), body=request.content.decode())
        path = request.url.path
        if path.startswith("/auth/"):
            assert request.url.host == "vlatka.vertical-life.info"
            if path.endswith("/authenticate"):
                return httpx.Response(302, headers={"location": "https://www.8a.nu/callback?state=s&code=c"})
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "AUTH_SESSION_ID=x;Version=1;Path=/auth/realms/Vertical-Life/;Secure;HttpOnly"),
                    ("content-type", "text/html"),
                ],
                text='<form action="https://vlatka.vertical-life.info/auth/realms/Vertical-Life/login-actions/'
                'authenticate?client_data=e">',
            )
        assert request.url.host == "www.8a.nu"
        if path == "/login":
            return httpx.Response(
                302, headers=[("location", KC_AUTH), ("set-cookie", "nu8a_session=1; Path=/; HttpOnly; Secure")]
            )
        if path.startswith("/_nuxt/"):
            SEEN["nuxt"] = SEEN.get("nuxt", 0) + 1
            return httpx.Response(200, headers={"content-type": "text/javascript"}, text='fetch("https://www.8a.nu/a")')
        if path == "/api/x":
            return httpx.Response(200, json={"url": "https://www.8a.nu/crags", "img": "https://d3byf4kaqtov0k.cloudfront.net/a"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text='<a href="https://www.8a.nu/crags">x</a><script>{APP_URL:"https:\\u002F\\u002Fwww.8a.nu"}</script>',
        )

    async def fake_fetch(app, method, url, headers, body, ua):
        SEEN["impersonate"] = main.impersonate_for(ua)
        r = handler(httpx.Request(method, url, headers=headers, content=body))
        return main.Upstream(r.status_code, list(r.headers.multi_items()), r.content)

    monkeypatch.setattr(main, "fetch", fake_fetch)
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path)
    with TestClient(main.app, base_url=PUBLIC) as c:
        yield c


def test_reescribe_html_y_json(client):
    r = client.get("/")
    assert f'href="{PUBLIC}/crags"' in r.text
    assert 'APP_URL:"https:\\u002F\\u002F8a.adrianlzt.com"' in r.text
    assert r.headers["x-robots-tag"].startswith("noindex")
    r = client.get("/api/x")
    assert r.json() == {"url": f"{PUBLIC}/crags", "img": "https://d3byf4kaqtov0k.cloudfront.net/a"}


def test_login_por_el_proxy(client):
    # /login → Keycloak servido en /auth/ del proxy, con la redirect_uri original (la única que acepta Keycloak).
    r = client.get("/login", follow_redirects=False)
    assert r.headers["location"] == KC_AUTH.replace("https://vlatka.vertical-life.info", PUBLIC)
    assert "nu8a_session=1; Path=/; HttpOnly; Secure" in r.headers.get_list("set-cookie")
    r = client.get("/auth/realms/Vertical-Life/protocol/openid-connect/auth?x=1")
    assert SEEN["host"] == "vlatka.vertical-life.info"
    assert f'action="{PUBLIC}/auth/realms/' in r.text
    # Tras el usuario y la contraseña, Keycloak manda a www.8a.nu/callback: tiene que volver al proxy.
    r = client.post(
        "/auth/realms/Vertical-Life/login-actions/authenticate",
        headers={"origin": PUBLIC, "referer": f"{PUBLIC}/auth/realms/x", "content-type": "application/x-www-form-urlencoded"},
        content="username=a&password=b",
        follow_redirects=False,
    )
    assert r.headers["location"] == f"{PUBLIC}/callback?state=s&code=c"
    assert SEEN["origin"] == "https://vlatka.vertical-life.info"
    assert SEEN["referer"] == "https://vlatka.vertical-life.info/auth/realms/x"


def test_cabeceras_hacia_arriba(client):
    client.get("/crags", headers={"referer": f"{PUBLIC}/", "user-agent": "curl/8", "x-forwarded-for": "1.2.3.4"})
    assert "user-agent" not in SEEN["headers"]  # lo pone curl_cffi, el del navegador que imita
    assert "x-forwarded-for" not in SEEN["headers"]
    assert SEEN["referer"] == "https://www.8a.nu/"
    assert SEEN["impersonate"] == "chrome"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
    client.get("/crags", headers={"user-agent": iphone})
    assert SEEN["headers"]["user-agent"] == iphone
    assert SEEN["impersonate"] == "safari_ios"


def test_impersonate_for():
    android = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
    assert main.impersonate_for(android) == "chrome_android"
    assert main.impersonate_for("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0") == "firefox"
    assert main.impersonate_for("") == "chrome"


def test_robots(client):
    assert "Disallow: /" in client.get("/robots.txt").text


def test_cache_nuxt(client, tmp_path):
    SEEN["nuxt"] = 0
    for _ in range(3):
        r = client.get("/_nuxt/abc.js")
        assert r.text == f'fetch("{PUBLIC}/a")'
    assert SEEN["nuxt"] == 1
    assert (tmp_path / "abc.js").read_text() == 'fetch("https://www.8a.nu/a")'
    client.get("/_nuxt/abc.js?v=1")
    assert SEEN["nuxt"] == 2
