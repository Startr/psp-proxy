# psp-proxy

A small, fast HTTPS bridge **and podcast station** for old Sony PSPs.

The PSP's built-in browser can't do modern TLS, so every HTTPS site is
unreachable. This runs on a computer on your LAN, speaks plain HTTP to the PSP,
and does the HTTPS talking for it — then adds a podcast search landing page,
browsable episode lists, a media byte-proxy with **Range support** (so playback
and seeking work), and **proxied RSS feeds** you can drop into the PSP's native
*RSS Channel* so episodes download to the Memory Stick.

Everything is served as PSP-friendly HTML: ~464 px wide, tiny CSS, no
JavaScript needed to navigate.

## Quick launch

The modern, zero-setup way (uv reads the script's inline dependencies and runs
it — nothing to install first):

```bash
uv run psp_proxy.py            # then open http://<this-computer-LAN-IP>:8080/
uv run psp_proxy.py -p 8080    # pick a port
```

Ephemeral run straight from the repo, no clone (uvx = `uv tool run`, the npx of
Python and the modern pipx replacement):

```bash
uvx --from git+https://github.com/Startr/psp-proxy psp-proxy
```

Prefer pipx? It works too:

```bash
pipx run --spec git+https://github.com/Startr/psp-proxy psp-proxy
# or install it:  pipx install git+https://github.com/Startr/psp-proxy
```

Don't have uv yet? `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Ship it to a Raspberry Pi

Option 1 — copy the script (no remote needed; `uv run` resolves the inline
dependencies on first launch):

```bash
ssh pi@<pi-ip> mkdir -p /opt/psp-proxy
scp psp_proxy.py pi@<pi-ip>:/opt/psp-proxy/
ssh pi@<pi-ip> uv run /opt/psp-proxy/psp_proxy.py
```

Option 2 — run straight from the repo:

```bash
uvx --from git+https://github.com/Startr/psp-proxy psp-proxy
```

To keep it running after reboots, install it as a systemd service — see
`TODO_pi_setup.md` for the unit file and the full retro-AP setup
(hostapd WPA-TKIP network, dnsmasq, captive portal).

## Point the PSP at it

This is a **portal you browse to**, not a proxy setting.

1. Put the PSP on the same Wi-Fi as the computer.
2. Run the server — it prints your computer's LAN IP on startup.
3. In the PSP browser, go to that address, e.g. `http://192.168.1.42:8080/`
   (set it as your home page for one-tap access).
4. Search a podcast, open a show, tap **Play / Download**.

For hands-off listening, open a show and copy its **RSS Channel link**
(`/feed?url=...`) into the PSP under **Network → RSS Channel**. New episodes
download to the Memory Stick automatically.

## PSP-1000 notes

The original "fat" PSP has **32 MB RAM** (half the 2000/3000), so its browser
runs out of memory on heavy pages. This build is tuned for it:

- Reader mode is on by default and strips page chrome (nav/header/footer/ads).
- Episode lists are paginated (25 per page) so long feeds don't blow up.
- If pages still fail to load: raise the browser's memory allocation in its
  own menu, and/or start the server with **`--no-images`** to drop all images.
- **RSS Channel needs firmware ≥ 2.60.** Audio (MP3) plays natively — no
  transcoding needed. Video is not supported yet (see roadmap).

## What's inside

| Route          | Purpose                                                    |
|----------------|------------------------------------------------------------|
| `/`            | Landing page + podcast search box                          |
| `/podcasts?q=` | Podcast search (Apple/iTunes Search API, no key needed)    |
| `/show?feed=`  | Episode list for a show + its RSS Channel link             |
| `/feed?url=`   | Proxied RSS feed (enclosures rewritten through the bridge) |
| `/proxy?url=`  | Generic byte proxy for audio/images/video, with Range      |
| `/browse?url=` | Web page proxy with a lite "reader mode" (`&raw=1` = full) |

## PSP media notes

- Most podcasts ship **MP3**, which the PSP plays natively — no transcoding
  needed, just the HTTPS→HTTP bridge this provides.
- **Video** podcasts often need re-encoding to PSP-friendly H.264/AAC MP4.
  That's intentionally **not** in this build yet (it needs ffmpeg) — see below.

## Roadmap (backlog)

- **Video + transcoding** — an opt-in ffmpeg path that re-encodes video
  podcasts to PSP-friendly H.264/AAC MP4. Audio-first for now; this comes next.
- **More catalog sources** — Podcast Index (needs a free key) and others, so
  search isn't Apple-only.
- **Reader mode upgrade** via `readability-lxml` for cleaner article text.
- **On-disk cache** for feeds/artwork to cut repeat latency.
- **Saved subscriptions** page.

## CLI

```
psp-proxy [-p PORT] [--host ADDR] [--no-images]
```

- `-p, --port`   port to listen on (default 8080)
- `--host`       bind address (default 0.0.0.0 — all interfaces, for LAN)
- `--no-images`  drop images everywhere (use if the PSP-1000 runs out of memory)

## Requirements

Python ≥ 3.11. Dependencies (aiohttp, feedparser, beautifulsoup4, lxml) are
handled automatically by `uv run` / `uvx` / `pipx`.

Licensed under the AGPL-3.0-or-later — if you run a modified version as a
network service, you must offer its source to users.
