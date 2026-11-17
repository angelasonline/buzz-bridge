#!/usr/bin/env python3
"""buzz-ops-bridge — mirror a Buzz announcements channel to a Nostr geohash channel.

Reads kind-9 messages from the Buzz announcements channel via the authenticated
`buzz messages get` CLI (the same query pattern used to read the channel earlier —
the agent authenticates as itself, a channel member; it does NOT hold the owner's
private key) and re-publishes each NEW message as a kind-20000 ephemeral event tagged
["g", <geohash>] + ["n", <label>], content prefixed with a neutral banner.

Design points that make this a SELF-MIRROR (not a broadcast to strangers):
  * Destination geohash defaults to s0000 (open-ocean cell, no residents). The owner
    teleports their own Bitchat client to that cell to receive.
  * Publishing key is LABELED and PERSISTENT (stored in --keyfile). An attributable
    identity must reuse the same key across runs, so we generate once and reuse.
  * Dry-run is the default. --live is required to actually publish, and even then the
    owner gates go-live.

Only stdlib + `coincurve` (schnorr) + `websockets` (asyncio) are required.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import subprocess
import sys
import time
from pathlib import Path

import websockets
from coincurve import PrivateKey

# DEPRECATED fixed relay set. Bitchat does NOT use a fixed relay list for geohash
# channels — it picks the relays geographically nearest the geohash center (see
# select_relays below). Publishing to this fixed set caused announcements to be
# accepted+retrievable on OUR relays yet invisible on the phone, which subscribes to
# a DIFFERENT, geo-nearest set. Kept only as a last-resort fallback if no directory
# CSV is available. Do not publish geohash events here.
BITCHAT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.primal.net",
    "wss://offchain.pub",
]

# The same remote directory Bitchat's GeoRelayDirectory fetches at runtime. The phone
# selects its subscription relays from THIS list, so the bridge must too.
REMOTE_RELAY_CSV = ("https://raw.githubusercontent.com/permissionlesstech/"
                    "georelays/refs/heads/main/nostr_relays.csv")

BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


# ---------------------------------------------------------------- geohash routing
def geohash_decode_center(gh: str) -> tuple[float, float]:
    """Decode a geohash to its center (lat, lon) — matches Bitchat's Geohash.decodeCenter."""
    lat = (-90.0, 90.0)
    lon = (-180.0, 180.0)
    is_lon = True
    for c in gh.strip().lower():
        cd = BASE32.index(c)
        for mask in (16, 8, 4, 2, 1):
            if is_lon:
                mid = (lon[0] + lon[1]) / 2
                lon = (mid, lon[1]) if cd & mask else (lon[0], mid)
            else:
                mid = (lat[0] + lat[1]) / 2
                lat = (mid, lat[1]) if cd & mask else (lat[0], mid)
            is_lon = not is_lon
    return (lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2


def _haversine_km(la1: float, lo1: float, la2: float, lo2: float) -> float:
    import math
    dla = math.radians(la2 - la1)
    dlo = math.radians(lo2 - lo1)
    a = (math.sin(dla / 2) ** 2
         + math.cos(math.radians(la1)) * math.cos(math.radians(la2)) * math.sin(dlo / 2) ** 2)
    return 6371.0 * 2 * math.atan2(a ** 0.5, (1 - a) ** 0.5)


def parse_relay_csv(text: str) -> list[tuple[str, float, float]]:
    """Parse the georelay CSV (host,lat,lon) — mirrors GeoRelayDirectory.parseCSV."""
    out: list[tuple[str, float, float]] = []
    for i, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if i == 0 and "relay url" in line.lower():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
