import json

import httpcore
import pytest

import httpx
from tests.utils import AsyncMockTransport, MockTransport


def redirects(request: httpx.Request) -> httpx.Response:
    if request.url.scheme not in ("http", "https"):
        raise httpcore.UnsupportedProtocol(
            f"Scheme {request.url.scheme!r} not supported."
        )

    if request.url.path == "/no_redirect":
        return httpx.Response(200)

    elif request.url.path == "/redirect_301":
        status_code = httpx.codes.MOVED_PERMANENTLY
        content = b"<a href='https://example.org/'>here</a>"
        headers = {"location": "https://example.org/"}
        return httpx.Response(status_code, headers=headers, content=content)

    elif request.url.path == "/redirect_302":
        status_code = httpx.codes.FOUND
        headers = {"location": "https://example.org/"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/redirect_303":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "https://example.org/"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/relative_redirect":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "/"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/malformed_redirect":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "https://:443/"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/invalid_redirect":
        status_code = httpx.codes.SEE_OTHER
        raw_headers = [(b"location", "https://😇/".encode("utf-8"))]
        return httpx.Response(status_code, headers=raw_headers)

    elif request.url.path == "/no_scheme_redirect":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "//example.org/"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/multiple_redirects":
        params = httpx.QueryParams(request.url.query)
        count = int(params.get("count", "0"))
        redirect_count = count - 1
        status_code = httpx.codes.SEE_OTHER if count else httpx.codes.OK
        if count:
            location = "/multiple_redirects"
            if redirect_count:
                location += f"?count={redirect_count}"
            headers = {"location": location}
        else:
            headers = {}
        return httpx.Response(status_code, headers=headers)

    if request.url.path == "/redirect_loop":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "/redirect_loop"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/cross_domain":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "https://example.org/cross_domain_target"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/cross_domain_target":
        status_code = httpx.codes.OK
        content = json.dumps({"headers": dict(request.headers)}).encode("utf-8")
        return httpx.Response(status_code, content=content)

    elif request.url.path == "/redirect_body":
        status_code = httpx.codes.PERMANENT_REDIRECT
        headers = {"location": "/redirect_body_target"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/redirect_no_body":
        status_code = httpx.codes.SEE_OTHER
        headers = {"location": "/redirect_body_target"}
        return httpx.Response(status_code, headers=headers)

    elif request.url.path == "/redirect_body_target":
        content = json.dumps(
            {"body": request.content.decode("ascii"), "headers": dict(request.headers)}
        ).encode("utf-8")
        return httpx.Response(200, content=content)

    elif request.url.path == "/cross_subdomain":
        if request.headers["Host"] != "www.example.org":
            status_code = httpx.codes.PERMANENT_REDIRECT
            headers = {"location": "https://www.example.org/cross_subdomain"}
            return httpx.Response(status_code, headers=headers)
        else:
            return httpx.Response(200, content=b"Hello, world!")

    elif request.url.path == "/redirect_custom_scheme":
        status_code = httpx.codes.MOVED_PERMANENTLY
        headers = {"location": "market://details?id=42"}
        return httpx.Response(status_code, headers=headers)

    if request.method == "HEAD":
        return httpx.Response(200)

    return httpx.Response(200, content=b"Hello, world!")


def test_no_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.com/no_redirect"
    response = client.get(url)
    assert response.status_code == 200
    with pytest.raises(httpx.NotRedirectResponse):
        response.next()


def test_redirect_301():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.post("https://example.org/redirect_301")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert len(response.history) == 1


def test_redirect_302():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.post("https://example.org/redirect_302")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert len(response.history) == 1


def test_redirect_303():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("https://example.org/redirect_303")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert len(response.history) == 1


def test_disallow_redirects():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.post("https://example.org/redirect_303", allow_redirects=False)
    assert response.status_code == httpx.codes.SEE_OTHER
    assert response.url == "https://example.org/redirect_303"
    assert response.is_redirect is True
    assert len(response.history) == 0

    response = response.next()
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert response.is_redirect is False
    assert len(response.history) == 1


def test_head_redirect():
    """
    Contrary to Requests, redirects remain enabled by default for HEAD requests.
    """
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.head("https://example.org/redirect_302")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert response.request.method == "HEAD"
    assert len(response.history) == 1
    assert response.text == ""


def test_relative_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("https://example.org/relative_redirect")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert len(response.history) == 1


def test_malformed_redirect():
    # https://github.com/encode/httpx/issues/771
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("http://example.org/malformed_redirect")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org:443/"
    assert len(response.history) == 1


def test_invalid_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    with pytest.raises(httpx.RemoteProtocolError):
        client.get("http://example.org/invalid_redirect")


def test_no_scheme_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("https://example.org/no_scheme_redirect")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/"
    assert len(response.history) == 1


def test_fragment_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("https://example.org/relative_redirect#fragment")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/#fragment"
    assert len(response.history) == 1


def test_multiple_redirects():
    client = httpx.Client(transport=MockTransport(redirects))
    response = client.get("https://example.org/multiple_redirects?count=20")
    assert response.status_code == httpx.codes.OK
    assert response.url == "https://example.org/multiple_redirects"
    assert len(response.history) == 20
    assert response.history[0].url == "https://example.org/multiple_redirects?count=20"
    assert response.history[1].url == "https://example.org/multiple_redirects?count=19"
    assert len(response.history[0].history) == 0
    assert len(response.history[1].history) == 1


@pytest.mark.usefixtures("async_environment")
async def test_async_too_many_redirects():
    async with httpx.AsyncClient(transport=AsyncMockTransport(redirects)) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await client.get("https://example.org/multiple_redirects?count=21")


@pytest.mark.usefixtures("async_environment")
async def test_async_too_many_redirects_calling_next():
    async with httpx.AsyncClient(transport=AsyncMockTransport(redirects)) as client:
        url = "https://example.org/multiple_redirects?count=21"
        response = await client.get(url, allow_redirects=False)
        with pytest.raises(httpx.TooManyRedirects):
            while response.is_redirect:
                response = await response.anext()


def test_sync_too_many_redirects():
    client = httpx.Client(transport=MockTransport(redirects))
    with pytest.raises(httpx.TooManyRedirects):
        client.get("https://example.org/multiple_redirects?count=21")


def test_sync_too_many_redirects_calling_next():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/multiple_redirects?count=21"
    response = client.get(url, allow_redirects=False)
    with pytest.raises(httpx.TooManyRedirects):
        while response.is_redirect:
            response = response.next()


def test_redirect_loop():
    client = httpx.Client(transport=MockTransport(redirects))
    with pytest.raises(httpx.TooManyRedirects):
        client.get("https://example.org/redirect_loop")


def test_cross_domain_redirect_with_auth_header():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.com/cross_domain"
    headers = {"Authorization": "abc"}
    response = client.get(url, headers=headers)
    assert response.url == "https://example.org/cross_domain_target"
    assert "authorization" not in response.json()["headers"]


def test_cross_domain_redirect_with_auth():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.com/cross_domain"
    response = client.get(url, auth=("user", "pass"))
    assert response.url == "https://example.org/cross_domain_target"
    assert "authorization" not in response.json()["headers"]


def test_same_domain_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/cross_domain"
    headers = {"Authorization": "abc"}
    response = client.get(url, headers=headers)
    assert response.url == "https://example.org/cross_domain_target"
    assert response.json()["headers"]["authorization"] == "abc"


def test_body_redirect():
    """
    A 308 redirect should preserve the request body.
    """
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/redirect_body"
    data = b"Example request body"
    response = client.post(url, data=data)
    assert response.url == "https://example.org/redirect_body_target"
    assert response.json()["body"] == "Example request body"
    assert "content-length" in response.json()["headers"]


def test_no_body_redirect():
    """
    A 303 redirect should remove the request body.
    """
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/redirect_no_body"
    data = b"Example request body"
    response = client.post(url, data=data)
    assert response.url == "https://example.org/redirect_body_target"
    assert response.json()["body"] == ""
    assert "content-length" not in response.json()["headers"]


def test_can_stream_if_no_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/redirect_301"
    with client.stream("GET", url, allow_redirects=False) as response:
        assert not response.is_closed
    assert response.status_code == httpx.codes.MOVED_PERMANENTLY
    assert response.headers["location"] == "https://example.org/"


def test_cannot_redirect_streaming_body():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.org/redirect_body"

    def streaming_body():
        yield b"Example request body"  # pragma: nocover

    with pytest.raises(httpx.RequestBodyUnavailable):
        client.post(url, data=streaming_body())


def test_cross_subdomain_redirect():
    client = httpx.Client(transport=MockTransport(redirects))
    url = "https://example.com/cross_subdomain"
    response = client.get(url)
    assert response.url == "https://www.example.org/cross_subdomain"


def cookie_sessions(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/":
        cookie = request.headers.get("Cookie")
        if cookie is not None:
            content = b"Logged in"
        else:
            content = b"Not logged in"
        return httpx.Response(200, content=content)

    elif request.url.path == "/login":
        status_code = httpx.codes.SEE_OTHER
        headers = {
            "location": "/",
            "set-cookie": (
                "session=eyJ1c2VybmFtZSI6ICJ0b21; path=/; Max-Age=1209600; "
                "httponly; samesite=lax"
            ),
        }
        return httpx.Response(status_code, headers=headers)

    else:
        assert request.url.path == "/logout"
        status_code = httpx.codes.SEE_OTHER
        headers = {
            "location": "/",
            "set-cookie": (
                "session=null; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; "
                "httponly; samesite=lax"
            ),
        }
        return httpx.Response(status_code, headers=headers)


def test_redirect_cookie_behavior():
    client = httpx.Client(transport=MockTransport(cookie_sessions))

    # The client is not logged in.
    response = client.get("https://example.com/")
    assert response.url == "https://example.com/"
    assert response.text == "Not logged in"

    # Login redirects to the homepage, setting a session cookie.
    response = client.post("https://example.com/login")
    assert response.url == "https://example.com/"
    assert response.text == "Logged in"

    # The client is logged in.
    response = client.get("https://example.com/")
    assert response.url == "https://example.com/"
    assert response.text == "Logged in"

    # Logout redirects to the homepage, expiring the session cookie.
    response = client.post("https://example.com/logout")
    assert response.url == "https://example.com/"
    assert response.text == "Not logged in"

    # The client is not logged in.
    response = client.get("https://example.com/")
    assert response.url == "https://example.com/"
    assert response.text == "Not logged in"


def test_redirect_custom_scheme():
    client = httpx.Client(transport=MockTransport(redirects))
    with pytest.raises(httpx.UnsupportedProtocol) as e:
        client.post("https://example.org/redirect_custom_scheme")
    assert str(e.value) == "Scheme 'market' not supported."
