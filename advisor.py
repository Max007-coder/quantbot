#!/usr/bin/env python3
"""advisor.py - emails you when conditions turn tradeable."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

import envfile
import notify
import signals

LOG = logging.getLogger("advisor")
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "advisor/1.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode())


def host() -> str:
    for h in HOSTS:
        try:
            get(h + "/api/v3/ping")
            return h
        except Exception:
            continue
    raise RuntimeError("no data host")


def candles(h: str, symbol: str, tf: str, limit: int = 300) -> list[Bar]:
    raw = get(f"{h}/api/v3/klines?symbol={symbol.replace('/', '')}&interval={tf}&limit={limit}")
    return [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in raw[:-1]]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTC/USDT,ETH/USDT")
    p.add_argument("--timeframe", default="15m", choices=["5m", "15m", "1h"])
    p.add_argument("--every", type=float, default=15.0)
    p.add_argument("--threshold", type=float, default=0.6)
    p.add_argument("--once", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    envfile.load()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    n = notify.from_env()
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    LOG.info("watching %s on %s every %sm", ",".join(syms), a.timeframe, a.every)
    if not n.enabled:
        LOG.warning("email not configured - console only")

    while True:
        try:
            h = host()
            for sym in syms:
                bars = candles(h, sym, a.timeframe)
                info = signals.detect(bars)
                if info is None:
                    continue
                v = signals.evaluate(bars, signals.Rules(), a.threshold)
                if v.side and v.best:
                    n.send(
                        f"[GO] {sym} {v.side} - {info.regime.value}",
                        f"{sym} {a.timeframe}\nregime {info.regime.value} conf {info.confidence}\n"
                        f"side {v.side} score {v.score}\nstrategy {v.best.name}\n"
                        f"reason {v.best.reason}\nprice {info.price}\n"
                        f"stop {v.best.stop:.6f}\ntarget {v.best.target:.6f}\n"
                        f"atr% {info.atr_pct}\n\nstart: run_duo.bat\n\n{datetime.now():%Y-%m-%d %H:%M}",
                        key=f"go:{sym}:{v.side}", urgent=True)
                else:
                    LOG.info("%s %s score=%.2f%s", sym, info.regime.value, v.score,
                             " (conflict)" if v.conflict else "")
        except Exception:
            LOG.exception("scan failed")
        if a.once:
            return 0
        time.sleep(a.every * 60)


if __name__ == "__main__":
    sys.exit(main())