# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9","feedparser>=6.0","beautifulsoup4>=4.12","lxml>=5.0"]
# ///
"""Offline tests for the pure functions (no network needed)."""
import psp_proxy as p


def test_looks_media():
    assert p._looks_media("https://x.com/a/ep1.mp3")
    assert p._looks_media("https://x.com/cover.JPG?y=1".split("?")[0])
    assert not p._looks_media("https://x.com/page.html")
    print("OK _looks_media")


def test_first_enclosure():
    e1 = {"enclosures": [{"href": "https://x.com/a.mp3", "type": "audio/mpeg"}]}
    assert p._first_enclosure(e1) == "https://x.com/a.mp3"
    e2 = {"enclosures": [], "links": [
        {"rel": "enclosure", "href": "https://x.com/b.mp3"}]}
    assert p._first_enclosure(e2) == "https://x.com/b.mp3"
    e3 = {"enclosures": [], "links": []}
    assert p._first_enclosure(e3) is None
    print("OK _first_enclosure")


def test_link_helpers():
    assert p.proxied("https://x.com/a b.mp3").startswith("/proxy?url=https%3A")
    assert p.browse_link("https://x.com/p").startswith("/browse?url=https%3A")
    print("OK link helpers")


def test_rewrite_html():
    sample = """<html><head><title>Hi</title></head><body>
      <script>evil()</script><style>.a{}</style>
      <nav>menu</nav>
      <a href="/rel">rel</a>
      <a href="https://other.com/x">abs</a>
      <a href="javascript:void(0)">js</a>
      <img src="/img/pic.png" onclick="x()" style="w:1">
      <p onclick="bad()">text</p>
      </body></html>"""
    out = p._rewrite_html(sample, "https://site.com/dir/page", reader=True)
    assert "<script" not in out and "evil()" not in out
    assert "<nav" not in out            # reader mode strips chrome
    # relative link resolved against base and routed through /browse
    assert "/browse?url=https%3A%2F%2Fsite.com%2Frel" in out
    assert "/browse?url=https%3A%2F%2Fother.com%2Fx" in out
    assert "javascript:" not in out
    # image resolved + routed through /proxy, handlers/style stripped
    assert "/proxy?url=https%3A%2F%2Fsite.com%2Fimg%2Fpic.png" in out
    assert "onclick" not in out and 'style="w:1"' not in out
    # our chrome injected
    assert 'href="/"' in out and "full page" in out
    print("OK _rewrite_html")


def test_rewrite_no_images():
    sample = '<html><body><img src="/pic.png"><p>hi</p></body></html>'
    out = p._rewrite_html(sample, "https://s.com/", reader=True, images=False)
    assert "<img" not in out and "hi" in out
    print("OK _rewrite_html images=False")


def test_page():
    r = p.page("T", "<b>hey</b>", search=True)
    assert r.content_type == "text/html"
    assert "charset=utf-8" in r.text and "Search podcasts" in r.text
    print("OK page")


if __name__ == "__main__":
    test_looks_media()
    test_first_enclosure()
    test_link_helpers()
    test_rewrite_html()
    test_rewrite_no_images()
    test_page()
    print("\nAll pure-function tests passed.")
