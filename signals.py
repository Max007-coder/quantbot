#!/usr/bin/env python3
"""signals.py - regime detection, strategy library, confluence scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from statistics import mean, pstdev
from typing import Optional


def _ema(v, p):
    k = 2.0 / (p + 1.0)
    out, prev = [], None
    for x in v:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def _rma(v, p):
    out, prev = [], None
    for x in v:
        prev = x if prev is None else (prev * (p - 1) + x) / p
        out.append(prev)
    return out


def _tr(h, l, c):
    return [h[i] - l[i] if i == 0 else max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
            for i in range(len(c))]


def _atr(h, l, c, p=14):
    return _rma(_tr(h, l, c), p)


def _rsi(c, p=14):
    g, ls = [], []
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        g.append(max(d, 0.0))
        ls.append(max(-d, 0.0))
    ag, al = _rma(g, p), _rma(ls, p)
    out = [50.0]
    for a, b in zip(ag, al):
        out.append(100.0 if b == 0 else 100.0 - 100.0 / (1.0 + a / b))
    return out


def _adx(h, l, c, p=14):
    pm, mn = [], []
    for i in range(1, len(c)):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pm.append(up if up > dn and up > 0 else 0.0)
        mn.append(dn if dn > up and dn > 0 else 0.0)
    a = _rma(_tr(h, l, c)[1:], p)
    pdi = [100.0 * (x / y if y else 0.0) for x, y in zip(_rma(pm, p), a)]
    mdi = [100.0 * (x / y if y else 0.0) for x, y in zip(_rma(mn, p), a)]
    dx = [100.0 * abs(x - y) / (x + y) if (x + y) else 0.0 for x, y in zip(pdi, mdi)]
    return _rma(dx, p)


def _bb(c, p=20, mult=2.0):
    mid, up, lo, width = [], [], [], []
    for i in range(len(c)):
        w = c[max(0, i - p + 1):i + 1]
        m = mean(w)
        s = pstdev(w) if len(w) > 1 else 0.0
        mid.append(m)
        up.append(m + mult * s)
        lo.append(m - mult * s)
        width.append((2 * mult * s) / m * 100.0 if m else 0.0)
    return mid, up, lo, width


def _donchian(h, l, p=20):
    up, lo = [], []
    for i in range(len(h)):
        w = slice(max(0, i - p), i)
        up.append(max(h[w]) if i else h[0])
        lo.append(min(l[w]) if i else l[0])
    return up, lo


class Regime(str, Enum):
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    CHOP = "CHOP"
    SQUEEZE = "SQUEEZE"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"
    DUMP = "DUMP"
    BREAKOUT = "BREAKOUT"


BULLISH = {Regime.STRONG_UP, Regime.UP, Regime.BREAKOUT}
BEARISH = {Regime.STRONG_DOWN, Regime.DOWN, Regime.DUMP}


@dataclass
class RegimeInfo:
    regime: Regime
    confidence: float
    slope: float
    adx: float
    atr_pct: float
    vol_z: float
    rsi: float
    bb_width: float
    price: float
    atr: float


@dataclass
class Ind:
    close: list
    high: list
    low: list
    volume: list
    open: list
    ema_fast: list
    ema_slow: list
    atr: list
    rsi: list
    adx: list
    bb_mid: list
    bb_up: list
    bb_lo: list
    bb_w: list
    dc_up: list
    dc_lo: list


def build(candles) -> Ind:
    c = [x.close for x in candles]
    h = [x.high for x in candles]
    l = [x.low for x in candles]
    v = [x.volume for x in candles]
    o = [x.open for x in candles]
    mid, up, lo, w = _bb(c)
    du, dl = _donchian(h, l)
    return Ind(c, h, l, v, o, _ema(c, 21), _ema(c, 55), _atr(h, l, c), _rsi(c),
               _adx(h, l, c), mid, up, lo, w, du, dl)


def detect(candles, lookback: int = 6) -> Optional[RegimeInfo]:
    if len(candles) < 60:
        return None
    i = build(candles)
    px = i.close[-1]
    if px <= 0:
        return None

    atr_v = i.atr[-1]
    atr_pct = atr_v / px * 100.0
    slope = (i.ema_fast[-1] - i.ema_fast[-1 - lookback]) / px * 100.0
    adx_v = i.adx[-1]
    rsi_v = i.rsi[-1]
    bw = i.bb_w[-1]

    recent_v = i.volume[-21:-1]
    vol_z = 0.0
    if recent_v:
        sd = pstdev(recent_v) or 1e-9
        vol_z = (i.volume[-1] - mean(recent_v)) / sd

    body = abs(i.close[-1] - i.open[-1])
    rng = max(i.high[-1] - i.low[-1], 1e-9)
    body_ratio = body / rng

    hist_w = [x for x in i.bb_w[-120:] if x > 0]
    squeeze = bool(hist_w) and bw <= sorted(hist_w)[max(0, int(len(hist_w) * 0.2) - 1)]

    if vol_z > 2.0 and body_ratio > 0.55 and px < i.ema_fast[-1] and slope < 0:
        reg = Regime.DUMP
    elif px > i.dc_up[-2] and vol_z > 0.8:
        reg = Regime.BREAKOUT
    elif adx_v >= 30 and slope > 0:
        reg = Regime.STRONG_UP
    elif adx_v >= 30 and slope < 0:
        reg = Regime.STRONG_DOWN
    elif adx_v >= 20 and slope > 0:
        reg = Regime.UP
    elif adx_v >= 20 and slope < 0:
        reg = Regime.DOWN
    elif squeeze:
        reg = Regime.SQUEEZE
    else:
        reg = Regime.CHOP

    conf = min(1.0, (adx_v / 40.0) * 0.6 + min(abs(vol_z), 3.0) / 3.0 * 0.4)
    return RegimeInfo(reg, round(conf, 3), round(slope, 4), round(adx_v, 2),
                      round(atr_pct, 3), round(vol_z, 2), round(rsi_v, 1),
                      round(bw, 3), px, atr_v)


@dataclass
class Candidate:
    name: str
    side: str
    score: float
    stop: float
    target: float
    reason: str
    regime: str = ""


@dataclass
class Rules:
    atr_mult: float = 2.0
    reward_risk: float = 2.0
    vol_z_min: float = 1.2


def _mk(name, side, score, px, atr_v, r, reason, regime):
    d = atr_v * r.atr_mult
    if d <= 0:
        return None
    stop = px - d if side == "BUY" else px + d
    tgt = px + d * r.reward_risk if side == "BUY" else px - d * r.reward_risk
    return Candidate(name, side, round(max(0.0, min(1.0, score)), 3), stop, tgt, reason, regime)


def s_trend_pullback(i, info, r):
    if info.regime not in (Regime.UP, Regime.STRONG_UP):
        return None
    touched = i.low[-1] <= i.ema_fast[-1] <= i.high[-1]
    held = i.close[-1] > i.ema_fast[-1] and i.close[-2] > i.ema_slow[-2]
    if not (touched and held) or not (38 <= info.rsi <= 62):
        return None
    return _mk("trend_pullback", "BUY", 0.55 + 0.3 * (info.adx / 40), info.price,
               info.atr, r, "pullback to ema21 in uptrend", info.regime.value)


def s_breakout(i, info, r):
    if info.regime is not Regime.BREAKOUT or info.vol_z < r.vol_z_min:
        return None
    return _mk("breakout", "BUY", 0.6 + min(info.vol_z, 3) * 0.12, info.price,
               info.atr, r, f"breakout vol_z={info.vol_z}", info.regime.value)


def s_momentum_ignition(i, info, r):
    body = abs(i.close[-1] - i.close[-2])
    if body < info.atr * 1.5 or info.vol_z < 2.0:
        return None
    up = i.close[-1] > i.close[-2]
    return _mk("momentum_ignition", "BUY" if up else "SELL",
               0.5 + min(info.vol_z, 4) * 0.1, info.price, info.atr, r,
               f"ignition bar vol_z={info.vol_z}", info.regime.value)


def s_capitulation(i, info, r):
    if info.regime is not Regime.DUMP or info.rsi > 28:
        return None
    rng = max(i.high[-1] - i.low[-1], 1e-9)
    lower = min(i.close[-1], i.high[-1]) - i.low[-1]
    if lower / rng < 0.55:
        return None
    return _mk("capitulation", "BUY", 0.5 + (28 - info.rsi) * 0.02, info.price,
               info.atr, r, f"dump wick rsi={info.rsi}", info.regime.value)


def s_mean_reversion(i, info, r):
    if info.regime not in (Regime.CHOP, Regime.SQUEEZE):
        return None
    if not (i.close[-1] < i.bb_lo[-1] and info.rsi < 32):
        return None
    return _mk("mean_reversion", "BUY", 0.45 + (32 - info.rsi) * 0.02, info.price,
               info.atr, r, f"below lower band rsi={info.rsi}", info.regime.value)


def s_range_fade(i, info, r):
    if info.regime is not Regime.CHOP:
        return None
    if not (i.close[-1] > i.bb_up[-1] and info.rsi > 68):
        return None
    return _mk("range_fade", "SELL", 0.45 + (info.rsi - 68) * 0.02, info.price,
               info.atr, r, f"above upper band rsi={info.rsi}", info.regime.value)


def s_exhaustion_short(i, info, r):
    if info.regime not in BULLISH or info.rsi < 76:
        return None
    engulf = (i.close[-1] < i.close[-2] and i.close[-1] < i.open[-2]
              and i.close[-2] > i.open[-3])
    if not engulf:
        return None
    return _mk("exhaustion_short", "SELL", 0.5 + (info.rsi - 76) * 0.02, info.price,
               info.atr, r, f"engulf after rsi={info.rsi}", info.regime.value)


def s_trend_short(i, info, r):
    if info.regime not in (Regime.DOWN, Regime.STRONG_DOWN):
        return None
    if not (i.high[-1] >= i.ema_fast[-1] and i.close[-1] < i.ema_fast[-1]):
        return None
    return _mk("trend_short", "SELL", 0.55 + 0.3 * (info.adx / 40), info.price,
               info.atr, r, "rally into ema21 in downtrend", info.regime.value)


ALL = [s_trend_pullback, s_breakout, s_momentum_ignition, s_capitulation,
       s_mean_reversion, s_range_fade, s_exhaustion_short, s_trend_short]

WEIGHTS = {
    Regime.STRONG_UP: {"trend_pullback": 1.0, "breakout": 1.0, "momentum_ignition": 0.7,
                       "exhaustion_short": 0.5},
    Regime.UP: {"trend_pullback": 1.0, "breakout": 0.8, "momentum_ignition": 0.6,
                "exhaustion_short": 0.5},
    Regime.BREAKOUT: {"breakout": 1.0, "momentum_ignition": 0.9, "trend_pullback": 0.6},
    Regime.CHOP: {"mean_reversion": 1.0, "range_fade": 1.0},
    Regime.SQUEEZE: {"mean_reversion": 0.6, "breakout": 0.8},
    Regime.DOWN: {"trend_short": 1.0, "capitulation": 0.6},
    Regime.STRONG_DOWN: {"trend_short": 1.0, "capitulation": 0.8},
    Regime.DUMP: {"capitulation": 1.0, "trend_short": 0.5},
}


@dataclass
class Verdict:
    candidates: list = field(default_factory=list)
    score: float = 0.0
    side: Optional[str] = None
    best: Optional[Candidate] = None
    conflict: bool = False


def evaluate(candles, rules: Rules = Rules(), threshold: float = 0.55) -> Verdict:
    info = detect(candles)
    if info is None:
        return Verdict()
    ind = build(candles)
    weights = WEIGHTS.get(info.regime, {})

    cands = []
    for fn in ALL:
        try:
            c = fn(ind, info, rules)
        except Exception:
            continue
        if c:
            cands.append(c)

    buy = sum(c.score * weights.get(c.name, 0.5) for c in cands if c.side == "BUY")
    sell = sum(c.score * weights.get(c.name, 0.5) for c in cands if c.side == "SELL")

    conflict = buy > 0 and sell > 0 and abs(buy - sell) < 0.25
    side = "BUY" if buy > sell else "SELL" if sell > buy else None
    score = max(buy, sell)

    best = None
    if side:
        same = [c for c in cands if c.side == side]
        if same:
            best = max(same, key=lambda c: c.score * weights.get(c.name, 0.5))

    ok = score >= threshold and best is not None and not conflict
    return Verdict(cands, round(score, 3), side if ok else None,
                   best if ok else None, conflict)