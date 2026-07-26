#!/usr/bin/env python3
"""runner — a durable, observable supervisor for the Buzz→Bitchat bridge.

Solves #2: keep the forwarder running unattended AND make it viewable to others.

Two things in one process:
  1. A forward LOOP — every --interval seconds it runs the translate-then-forward
     step (`translate_forward.py --live`), so new Buzz announcements keep flowing to
     the Bitchat geohash without anyone babysitting it. A crash in one iteration is
     caught and logged; the loop never dies.
  2. A status HTTP SERVER — binds $PORT (or --port) and serves a tiny public page +
     JSON so anyone can SEE the bridge is alive and what it's doing, without needing
     any credentials. This is what makes it "online and viewable" (Angela's ask).

Deployment shapes this supports:
  - Local, durable: run under a macOS launchd LaunchAgent (survives logout/reboot).
    See DEPLOY.md. launchd keeps it up whenever the Mac is on.
  - Hosted, always-online + public: run as a Render/Fly *Web Service* (binds $PORT,
    gets a public URL). Same container, credentials come from the host's secret store.
    See Dockerfile + DEPLOY.md.

SECRETS: this file reads NOTHING secret from disk. The three Buzz credentials
(BUZZ_RELAY_URL / BUZZ_PRIVATE_KEY / BUZZ_AUTH_TAG) and LANGLAYER_URL are read from
the process ENVIRONMENT, which the host (launchd env file or cloud secret store)
provides. Nothing sensitive is written by this script.

Stdlib only (http.server, subprocess, threading) + whatever translate_forward.py needs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# In-process status, published by the loop and read by the HTTP server.
STATUS: dict = {
    "state": "starting",           # starting | idle | forwarding | error
    "started_at": None,            # epoch seconds
    "last_run_at": None,
    "last_run_ok": None,
    "runs": 0,
    "errors": 0,
    "last_forward_summary": None,  # short text from the most recent run
    "last_error": None,
    "interval_s": None,
    "geohash": None,
    "langlayer_url": None,
    "languages": None,
}
_LOCK = threading.Lock()


def _set(**kw) -> None:
    with _LOCK:
        STATUS.update(kw)


def _snapshot() -> dict:
    with _LOCK:
        s = dict(STATUS)
    now = int(time.time())
    if s["started_at"]:
        s["uptime_s"] = now - s["started_at"]
    if s["last_run_at"] and s["interval_s"]:
        s["next_run_in_s"] = max(0, s["interval_s"] - (now - s["last_run_at"]))
    return s


# ------------------------------------------------------------------ HTTP status
_PAGE = """<!doctype html><meta charset=utf-8><title>Buzz→Bitchat bridge</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:640px;margin:6vh auto;padding:0 1rem;color:#1a1a1a}}
h1{{font-size:1.3rem}} .dot{{display:inline-block;width:.7em;height:.7em;border-radius:50%;margin-right:.4em}}
.ok{{background:#1a7f37}} .warn{{background:#bf8700}} .err{{background:#cf222e}}
code{{background:#f0f0f0;padding:.1em .35em;border-radius:4px}}
table{{border-collapse:collapse;margin-top:1rem;width:100%}} td{{padding:.35rem .5rem;border-top:1px solid #eee}}
td:first-child{{color:#666;width:11rem}} .muted{{color:#888;font-size:.85rem;margin-top:1.5rem}}</style>
<h1><span class="dot {cls}"></span>Buzz→Bitchat bridge — {state}</h1>
<p>Mirrors a Buzz announcements channel into a Bitchat geohash, translated per-language
via Langlayer. One post → every language → the right place on the map, offline-capable.</p>
<table>
<tr><td>state</td><td><b>{state}</b></td></tr>
<tr><td>uptime</td><td>{uptime}</td></tr>
<tr><td>runs / errors</td><td>{runs} / {errors}</td></tr>
<tr><td>last run</td><td>{last_run} ({last_ok})</td></tr>
<tr><td>last activity</td><td>{last_summary}</td></tr>
<tr><td>next run in</td><td>{next_run}</td></tr>
<tr><td>destination geohash</td><td><code>{geohash}</code></td></tr>
<tr><td>languages</td><td>{languages}</td></tr>
<tr><td>langlayer</td><td><code>{langlayer}</code></td></tr>
</table>
<p class="muted">Source & how it works: this is an automated one-way mirror. Machine-readable
status at <a href="/status">/status</a>, health at <a href="/healthz">/healthz</a>.</p>
"""


def _fmt_dur(sec) -> str:
    if not sec and sec != 0:
        return "—"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return (f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        s = _snapshot()
        if self.path.rstrip("/") in ("", "/") or self.path.startswith("/?"):
            cls = {"idle": "ok", "forwarding": "ok", "error": "err",
                   "starting": "warn"}.get(s["state"], "warn")
            html = _PAGE.format(
                cls=cls, state=s["state"], uptime=_fmt_dur(s.get("uptime_s")),
                runs=s["runs"], errors=s["errors"],
                last_run=(time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(s["last_run_at"]))
                          if s["last_run_at"] else "—"),
                last_ok=("ok" if s["last_run_ok"] else "failed" if s["last_run_ok"] is False else "—"),
                last_summary=(s["last_forward_summary"] or "—"),
                next_run=_fmt_dur(s.get("next_run_in_s")),
                geohash=s["geohash"] or "—", languages=s["languages"] or "—",
                langlayer=s["langlayer_url"] or "(offline stub)")
            self._send(200, html.encode(), "text/html; charset=utf-8")
        elif self.path.rstrip("/") == "/status":
            self._send(200, json.dumps(s, indent=2).encode(), "application/json")
        elif self.path.rstrip("/") == "/healthz":
            ok = s["state"] != "error"
            self._send(200 if ok else 503,
                       json.dumps({"ok": ok, "state": s["state"]}).encode(),
                       "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):  # silence default request logging
        pass


def serve_status(port: int) -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.serve_forever()


# ------------------------------------------------------------------ forward loop
def run_once(args) -> tuple[bool, str]:
    """Run one translate-then-forward pass. Returns (ok, short_summary)."""
    cmd = [sys.executable, "translate_forward.py", "--live",
           "--geohash", args.geohash,
           "--languages", *args.languages,
           "--relay-count", str(args.relay_count)]
    if args.langlayer_url:
        cmd += ["--langlayer-url", args.langlayer_url]
    if args.source_language:
        cmd += ["--source-language", args.source_language]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.abspath(__file__)))
    out = (proc.stdout or "") + (proc.stderr or "")
    # Pull a one-line summary out of the child's output.
    summary = "no new messages"
    for line in out.splitlines():
        line = line.strip()
        if "PUBLISHING" in line or "WOULD PUBLISH" in line or "new message" in line:
            summary = line
    if proc.returncode != 0:
        tail = out.strip().splitlines()[-1] if out.strip() else "unknown error"
        return False, f"exit {proc.returncode}: {tail[:160]}"
    return True, summary[:160]


def loop(args) -> None:
    _set(state="idle", started_at=int(time.time()), interval_s=args.interval,
         geohash=args.geohash, langlayer_url=args.langlayer_url,
         languages=" ".join(args.languages))
    while True:
        _set(state="forwarding")
        try:
            ok, summary = run_once(args)
            with _LOCK:
                STATUS["runs"] += 1
                STATUS["last_run_at"] = int(time.time())
                STATUS["last_run_ok"] = ok
                STATUS["last_forward_summary"] = summary
                if ok:
                    STATUS["state"] = "idle"
                    STATUS["last_error"] = None
                else:
                    STATUS["errors"] += 1
                    STATUS["state"] = "error"
                    STATUS["last_error"] = summary
            print(f"[runner] run #{STATUS['runs']} ok={ok} :: {summary}", flush=True)
        except Exception as e:  # noqa: BLE001 — a loop must never die
            with _LOCK:
                STATUS["errors"] += 1
                STATUS["state"] = "error"
                STATUS["last_error"] = str(e)
                STATUS["last_run_at"] = int(time.time())
            print(f"[runner] iteration crashed (loop continues): {e}", flush=True)
        time.sleep(args.interval)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Durable, observable Buzz→Bitchat bridge runner")
    p.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL", "60")),
                   help="seconds between forward passes (default 60)")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8787")),
                   help="status HTTP port (default 8787; hosts set $PORT)")
    p.add_argument("--geohash", default=os.environ.get("GEOHASH", "1r23b"))
    p.add_argument("--langlayer-url", default=os.environ.get("LANGLAYER_URL"))
    p.add_argument("--source-language", default=os.environ.get("SOURCE_LANGUAGE", "en"))
    p.add_argument("--languages", nargs="+",
                   default=os.environ.get("LANGUAGES", "en es zh").split())
    p.add_argument("--relay-count", type=int, default=int(os.environ.get("RELAY_COUNT", "8")))
    return p.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    # Fail fast with a clear message if the read-side credentials aren't present.
    missing = [k for k in ("BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY", "BUZZ_AUTH_TAG")
               if not os.environ.get(k)]
    if missing:
        print(f"[runner] FATAL: missing required env: {', '.join(missing)}. "
              f"The host (launchd env file or cloud secret store) must provide these.",
              file=sys.stderr)
        return 3
    threading.Thread(target=serve_status, args=(args.port,), daemon=True).start()
    print(f"[runner] status page on :{args.port}  |  forwarding every {args.interval}s "
          f"→ geohash {args.geohash}  |  langlayer={args.langlayer_url or '(stub)'}",
          flush=True)
    loop(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
