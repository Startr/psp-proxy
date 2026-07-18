#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "aiohttp>=3.9",
#     "feedparser>=6.0",
#     "beautifulsoup4>=4.12",
#     "lxml>=5.0",
#     "yarl>=1.9",
# ]
# ///
"""
psp-proxy — a small, fast proxy that lets an old Sony PSP reach the modern web
and (mainly) enjoy podcasts.

Why this exists
---------------
The PSP's built-in NetFront browser can't negotiate modern TLS, so every HTTPS
site is unreachable. This proxy sits on your LAN, speaks plain HTTP to the PSP,
and does the HTTPS talking on its behalf. On top of that bridge it adds a
podcast search landing page, browsable episode lists, an audio/media byte-proxy
with HTTP Range support (so playback and seeking work), and proxied RSS feeds
you can drop straight into the PSP's native "RSS Channel" so episodes download
to the Memory Stick.

Everything is served as PSP-friendly HTML: ~464px content width, minimal CSS,
no JavaScript required for navigation, plain GET forms.

Quick launch (see README for more):
    uv run psp_proxy.py                 # zero-setup, uv reads the header above
    uvx --from git+<repo-url> psp-proxy # ephemeral, no clone
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import socket
from urllib.parse import quote, unquote, urljoin, urlparse

import aiohttp
import feedparser
from aiohttp import web
from bs4 import BeautifulSoup
from yarl import URL

# --- constants ---------------------------------------------------------------

ITUNES_SEARCH = "https://itunes.apple.com/search"

# A normal desktop UA for UPSTREAM fetches (so sites serve real content, not a
# 2005 mobile stub). The PSP only ever talks to us, never upstream.
UPSTREAM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "Gecko/20100101 Firefox/128.0"
)

# Stripped from browse output entirely (noise / unsupported on PSP).
STRIP_TAGS = ["script", "style", "noscript", "svg", "iframe", "canvas",
              "video", "audio", "source", "picture", "template"]
# Removed in reader mode (chrome around the article).
READER_STRIP = ["nav", "header", "footer", "aside", "form"]

CSS = (
    # --- base (PSP-safe, dark default) ---
    "body{background:#09090b;color:#e4e4e7;font-family:sans-serif;"
    "font-size:15px;line-height:1.4;margin:0;padding:0;max-width:464px}"
    "a{color:#60a5fa;text-decoration:none}"
    "h1{font-size:20px;margin:0 0 8px;color:#fafafa;font-weight:bold}"
    "h2{font-size:14px;margin:14px 0 6px;color:#a1a1aa;"
    "text-transform:uppercase;letter-spacing:1px}"
    # --- nav bar ---
    ".nb{background:#18181b;border-bottom:2px solid #3b82f6;"
    "padding:9px 12px;margin:0 0 14px;overflow:hidden}"
    ".nb a{color:#60a5fa;text-decoration:none;font-weight:bold;"
    "display:inline-block;margin-right:16px;font-size:14px}"
    ".nb .logo{color:#fafafa;font-size:16px;font-weight:bold;"
    "margin-right:18px;text-decoration:none}"
    # --- content area ---
    ".ct{padding:0 12px 16px}"
    # --- cards ---
    ".card{background:#18181b;border:1px solid #27272a;"
    "border-radius:8px;padding:10px 12px;margin:8px 0;overflow:hidden}"
    ".card .t{font-weight:bold;font-size:15px;color:#60a5fa;"
    "text-decoration:none}"
    # --- muted / meta ---
    ".muted{color:#71717a;font-size:13px;line-height:1.3}"
    # --- thumbs ---
    "img.thumb{width:56px;height:56px;float:left;margin:0 10px 4px 0;"
    "border-radius:8px}"
    # --- form ---
    "input,button{font-size:15px;padding:8px 10px;border-radius:6px;"
    "border:1px solid #27272a;box-sizing:border-box}"
    "input[type=text]{width:66%;background:#18181b;color:#e4e4e7}"
    "button{background:#3b82f6;color:#fff;border:none;font-weight:bold}"
    # --- pills ---
    ".pill{display:inline-block;background:#052e16;border:1px solid #166534;"
    "border-radius:6px;padding:4px 10px;margin:2px 4px 2px 0;"
    "font-size:13px;text-decoration:none;color:#4ade80}"
    # --- responsive (PSP ignores @media) ---
    "@media(min-width:480px){"
    "body{max-width:640px;margin:0 auto}"
    ".card{border-radius:10px}"
    "input[type=text]{width:50%}}"
    # --- light theme (PSP ignores prefers-color-scheme) ---
    "@media(prefers-color-scheme:light){"
    "body{background:#fafafa;color:#18181b}"
    ".nb{background:#fff;border-bottom-color:#2563eb}"
    ".nb a{color:#2563eb}.nb .logo{color:#18181b}"
    "a{color:#2563eb}"
    "h1{color:#09090b}h2{color:#71717a}"
    ".card{background:#fff;border-color:#e5e7eb}"
    ".card .t{color:#2563eb}"
    ".muted{color:#a1a1aa}"
    "input[type=text]{background:#fff;color:#18181b;border-color:#d4d4d8}"
    "button{background:#2563eb}"
    ".pill{background:#f0fdf4;border-color:#86efac;color:#166534}}"
)


# --- html helpers ------------------------------------------------------------

def page(title: str, body: str, *, search: bool = False) -> web.Response:
    """Wrap body content in a minimal, PSP-safe HTML document."""
    box = (
        '<form action="/podcasts" method="get">'
        '<input type="text" name="q" placeholder="Search podcasts&hellip;">'
        ' <button type="submit">Search</button></form>'
        if search else ""
    )
    doc = (
        "<!DOCTYPE html><html><head>"
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        '<div class="nb"><span class="logo">PSP Proxy</span>'
        '<a href="/">Home</a>'
        '<a href="/podcasts">Podcasts</a><a href="/browse">Web</a></div>'
        f'<div class="ct">{box}{body}</div></body></html>'
    )
    return web.Response(text=doc, content_type="text/html", charset="utf-8")


def proxied(url: str) -> str:
    return "/proxy?url=" + quote(url, safe="")


def browse_link(url: str) -> str:
    return "/browse?url=" + quote(url, safe="")


# --- landing -----------------------------------------------------------------

async def handle_index(request: web.Request) -> web.Response:
    body = (
        "<h1>PSP Proxy</h1>"
        '<p class="muted">A bridge to the modern web for your PSP. '
        "Search podcasts, browse pages, stream media.</p>"
        "<h2>Podcasts</h2>"
        "<p>Type a show or topic above, or try "
        '<a href="/podcasts?q=technology">technology</a>, '
        '<a href="/podcasts?q=history">history</a>, '
        '<a href="/podcasts?q=news">news</a>.</p>'
        "<h2>Web</h2>"
        '<p>Open any site through the proxy: '
        '<a href="/browse?url=https://en.wikipedia.org/wiki/PlayStation_Portable">'
        "Wikipedia: PSP</a></p>"
        '<p class="muted">Tip: for hands-off listening, open a show and add its '
        '"RSS Channel" link in the PSP under Network &gt; RSS Channel.</p>'
    )
    return page("PSP Proxy", body, search=True)


# --- podcast search (iTunes) -------------------------------------------------

async def handle_podcasts(request: web.Request) -> web.Response:
    q = (request.query.get("q") or "").strip()
    if not q:
        return page("Podcasts", "<p>Enter a search above to find podcasts.</p>",
                    search=True)

    session: aiohttp.ClientSession = request.app["session"]
    params = {"media": "podcast", "entity": "podcast", "limit": "20", "term": q}
    try:
        async with session.get(ITUNES_SEARCH, params=params) as r:
            data = await r.json(content_type=None)
    except Exception as e:  # noqa: BLE001
        return page("Podcasts", f'<p class="muted">Search failed: '
                    f"{html.escape(str(e))}</p>", search=True)

    results = data.get("results", [])
    if not results:
        return page("Podcasts", f"<p>No podcasts found for "
                    f"<b>{html.escape(q)}</b>.</p>", search=True)

    cards = []
    for it in results:
        feed = it.get("feedUrl")
        if not feed:
            continue
        name = html.escape(it.get("collectionName", "Untitled"))
        artist = html.escape(it.get("artistName", ""))
        art = it.get("artworkUrl100") or it.get("artworkUrl60") or ""
        genre = html.escape(it.get("primaryGenreName", ""))
        count = it.get("trackCount")
        thumb = (f'<img class="thumb" src="{proxied(art)}" alt="">'
                 if art and request.app["images"] else "")
        meta = " · ".join(x for x in [artist, genre,
                          f"{count} eps" if count else ""] if x)
        show = "/show?feed=" + quote(feed, safe="")
        cards.append(
            f'<div class="card">{thumb}'
            f'<a class="t" href="{show}">{name}</a><br>'
            f'<span class="muted">{meta}</span></div>'
        )
    body = f"<h1>Results for {html.escape(q)}</h1>" + "".join(cards)
    return page(f"Podcasts: {q}", body, search=True)


# --- podcast show / episodes -------------------------------------------------

async def handle_show(request: web.Request) -> web.Response:
    feed_url = request.query.get("feed")
    if not feed_url:
        return page("Show", "<p>Missing feed.</p>")

    session: aiohttp.ClientSession = request.app["session"]
    try:
        async with session.get(feed_url) as r:
            raw = await r.read()
    except Exception as e:  # noqa: BLE001
        return page("Show", f'<p class="muted">Could not load feed: '
                    f"{html.escape(str(e))}</p>")

    parsed = feedparser.parse(raw)
    title = html.escape(parsed.feed.get("title", "Podcast"))
    subscribe = "/feed?url=" + quote(feed_url, safe="")

    head = (
        f"<h1>{title}</h1>"
        f'<p><a class="pill" href="{subscribe}">RSS Channel link</a>'
        '<span class="muted">add this under Network &gt; RSS Channel on the '
        "PSP</span></p>"
    )

    # Paginate: a 300-episode DOM will exhaust the PSP-1000's browser memory.
    per = 25
    start = max(0, int(request.query.get("start", 0) or 0))
    entries = parsed.entries
    window = entries[start:start + per]

    cards = []
    for e in window:
        etitle = html.escape(e.get("title", "Episode"))
        date = html.escape(e.get("published", "") or e.get("updated", ""))
        dur = html.escape(str(e.get("itunes_duration", "")))
        audio = _first_enclosure(e)
        meta = " · ".join(x for x in [date, dur] if x)
        if audio:
            play = f'<a class="pill" href="{proxied(audio)}">Play / Download</a>'
        else:
            play = '<span class="muted">no media</span>'
        cards.append(
            f'<div class="card"><span class="t">{etitle}</span><br>'
            f'<span class="muted">{meta}</span><br>{play}</div>'
        )
    if not cards:
        cards = ["<p>No episodes found in this feed.</p>"]

    feed_q = quote(feed_url, safe="")
    nav = []
    if start > 0:
        prev = max(0, start - per)
        nav.append(f'<a class="pill" href="/show?feed={feed_q}&start={prev}">'
                   "&larr; Newer</a>")
    if start + per < len(entries):
        nav.append(f'<a class="pill" href="/show?feed={feed_q}&'
                   f'start={start + per}">Older &rarr;</a>')
    pager = f'<p>{"".join(nav)}</p>' if nav else ""
    return page(title, head + pager + "".join(cards) + pager)


def _first_enclosure(entry) -> str | None:
    """Best-effort audio/video URL from a feedparser entry."""
    for enc in entry.get("enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href:
            return href
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


# --- proxied RSS feed (rewrite enclosures + art through /proxy) ---------------

async def handle_feed(request: web.Request) -> web.Response:
    url = request.query.get("url")
    if not url:
        return web.Response(status=400, text="missing url")

    session: aiohttp.ClientSession = request.app["session"]
    base = str(request.url.with_path("").with_query(""))  # our own origin
    try:
        async with session.get(url) as r:
            text = await r.text(errors="replace")
    except Exception as e:  # noqa: BLE001
        return web.Response(status=502, text=f"feed fetch failed: {e}")

    # Rewrite enclosure + media URLs to route through /proxy so the PSP (which
    # can't do HTTPS) can download them. lxml-xml keeps the RSS structure intact.
    soup = BeautifulSoup(text, "lxml-xml")
    for enc in soup.find_all("enclosure"):
        if enc.get("url"):
            enc["url"] = base + proxied(enc["url"])
    for tag in soup.find_all(["url", "href"]):  # itunes:image href, etc.
        pass  # handled below via attribute sweep
    for tag in soup.find_all(True):
        for attr in ("href",):
            v = tag.get(attr)
            if v and v.startswith("http") and _looks_media(v):
                tag[attr] = base + proxied(v)
    return web.Response(text=str(soup), content_type="application/rss+xml",
                        charset="utf-8")


def _looks_media(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".mp3", ".m4a", ".aac", ".mp4", ".m4v", ".ogg",
                          ".jpg", ".jpeg", ".png"))


# --- generic byte proxy with Range support (audio / images / video) ----------

# Hop-by-hop and problematic headers we never forward downstream.
HOP = {"content-encoding", "transfer-encoding", "connection",
       "keep-alive", "content-security-policy", "strict-transport-security"}


async def handle_proxy(request: web.Request) -> web.StreamResponse:
    url = request.query.get("url")
    if not url:
        return web.Response(status=400, text="missing url")

    session: aiohttp.ClientSession = request.app["session"]
    # identity: stop upstream gzip so the Content-Length we forward matches the
    # bytes we actually stream (otherwise audio truncates mid-episode).
    fwd = {"User-Agent": UPSTREAM_UA, "Accept-Encoding": "identity"}
    rng = request.headers.get("Range")
    if rng:
        fwd["Range"] = rng  # let the PSP seek

    try:
        # encoded=True keeps the URL byte-for-byte as the feed gave it to us:
        # yarl's default re-quoting breaks signed CDN URLs (%2F etc. in the
        # signature get normalized -> upstream returns AccessDenied).
        upstream = await session.get(URL(url, encoded=True), headers=fwd)
    except Exception as e:  # noqa: BLE001
        return web.Response(status=502, text=f"proxy failed: {e}")

    out = {}
    for k, v in upstream.headers.items():
        if k.lower() in HOP:
            continue
        out[k] = v
    out.setdefault("Accept-Ranges", "bytes")

    resp = web.StreamResponse(status=upstream.status, headers=out)
    try:
        await resp.prepare(request)
        async for chunk in upstream.content.iter_chunked(64 * 1024):
            await resp.write(chunk)
        await resp.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # PSP closed the stream; normal for stop/seek
    finally:
        upstream.release()
    return resp


# --- web browsing proxy (with lite reader mode) ------------------------------

async def handle_browse(request: web.Request) -> web.Response:
    url = request.query.get("url")
    if not url:
        body = (
            "<h1>Web</h1><p>Enter a full URL:</p>"
            '<form action="/browse" method="get">'
            '<input type="text" name="url" placeholder="https://example.com">'
            '<button type="submit">Open</button></form>'
            '<p class="muted">Add <b>&amp;raw=1</b> to a URL to skip reader '
            "mode and keep the full page.</p>"
        )
        return page("Web", body)

    if "://" not in url:
        url = "https://" + url
    reader = request.query.get("raw") != "1"

    session: aiohttp.ClientSession = request.app["session"]
    try:
        async with session.get(url, headers={"User-Agent": UPSTREAM_UA}) as r:
            ctype = r.headers.get("Content-Type", "")
            if "text/html" not in ctype.lower():
                # Not a page — hand it to the byte proxy instead.
                raise web.HTTPFound(proxied(url))
            text = await r.text(errors="replace")
            final_url = str(r.url)
    except web.HTTPFound:
        raise
    except Exception as e:  # noqa: BLE001
        return page("Web", f'<p class="muted">Failed to load '
                    f"{html.escape(url)}: {html.escape(str(e))}</p>")

    cleaned = _rewrite_html(text, final_url, reader=reader,
                            images=request.app["images"])
    return web.Response(text=cleaned, content_type="text/html", charset="utf-8")


def _rewrite_html(text: str, base: str, *, reader: bool, images: bool = True) -> str:
    """Simplify a page and rewrite links/images to stay inside the proxy."""
    soup = BeautifulSoup(text, "lxml")

    for tag in soup.find_all(STRIP_TAGS + (READER_STRIP if reader else [])):
        tag.decompose()

    # Drop inline event handlers and style attributes (PSP ignores/chokes).
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.startswith("on") or attr in ("style", "class", "srcset"):
                del tag[attr]

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("javascript:"):
            del a["href"]           # make inert; PSP JS is unreliable anyway
            continue
        if href.startswith(("#", "mailto:")):
            continue                # in-page / email links are harmless
        a["href"] = browse_link(urljoin(base, href))

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and images:
            img["src"] = proxied(urljoin(base, src))
        else:
            img.decompose()

    # Inject our stylesheet + a small header bar.
    title = soup.title.string if soup.title and soup.title.string else base
    style = soup.new_tag("style")
    style.string = CSS
    if soup.head:
        soup.head.append(style)
    bar = BeautifulSoup(
        '<div class="nb"><span class="logo">PSP Proxy</span>'
        '<a href="/">Home</a>'
        '<a href="/podcasts">Podcasts</a><a href="/browse">Web</a></div>'
        f'<p class="muted" style="padding:0 12px">'
        f'{html.escape(str(title))[:80]} · '
        f'<a href="/browse?url={quote(base, safe="")}&raw=1">full page</a></p>',
        "lxml",
    )
    if soup.body:
        soup.body.insert(0, bar)
    return str(soup)


# --- misc routes -------------------------------------------------------------

async def handle_robots(request: web.Request) -> web.Response:
    return web.Response(text="User-agent: *\nDisallow: /\n")


async def handle_favicon(request: web.Request) -> web.Response:
    return web.Response(status=204)


# --- app wiring --------------------------------------------------------------

def lan_ip() -> str | None:
    """Best-effort primary LAN IP (no packets sent; just picks the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


async def _on_startup(app: web.Application) -> None:
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=60)
    # requote_redirect_url=False: podcast CDNs redirect to pre-signed URLs;
    # re-quoting the Location header corrupts the signature -> AccessDenied.
    app["session"] = aiohttp.ClientSession(timeout=timeout,
                                           requote_redirect_url=False)


async def _on_cleanup(app: web.Application) -> None:
    await app["session"].close()


def make_app(*, images: bool = True) -> web.Application:
    app = web.Application()
    app["images"] = images
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.add_routes([
        web.get("/", handle_index),
        web.get("/podcasts", handle_podcasts),
        web.get("/show", handle_show),
        web.get("/feed", handle_feed),
        web.get("/proxy", handle_proxy),
        web.get("/browse", handle_browse),
        web.get("/robots.txt", handle_robots),
        web.get("/favicon.ico", handle_favicon),
    ])
    return app


def main() -> None:
    ap = argparse.ArgumentParser(
        description="PSP proxy: web + podcasts for old PSPs")
    ap.add_argument("-p", "--port", type=int, default=8080)
    ap.add_argument("--host", default="0.0.0.0",
                    help="bind address (default: all interfaces, so the PSP "
                         "can reach it over LAN)")
    ap.add_argument("--no-images", action="store_true",
                    help="drop images from lists and pages — use if the "
                         "PSP-1000 browser runs out of memory")
    args = ap.parse_args()

    ip = lan_ip()
    where = f"http://{ip}:{args.port}/" if ip else \
        f"http://<this-computer-LAN-IP>:{args.port}/"
    print("=" * 52)
    print("  psp-proxy is running.")
    print(f"  On the PSP browser, go to:  {where}")
    print("  (set it as your home page for one-tap access)")
    print("  PSP-1000: needs firmware 2.60+ for RSS Channel. If pages fail to")
    print("  load, raise the browser's memory in its menu, or use --no-images.")
    print("=" * 52)
    web.run_app(make_app(images=not args.no_images),
                host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
