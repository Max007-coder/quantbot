#!/usr/bin/env python3
"""
quantbot.py - autonomous trading engine (paper + live).

    python quantbot.py --once
    python quantbot.py --symbols BTC/USDT --token secret
    python quantbot.py --mode live --venues binance --equity 500 --risk 0.25
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import envfile
import notify
import signals

LOG = logging.getLogger("quantbot")


# ═══════════════════════════════════════════════════════════════════
#  MODELS
# ═══════════════════════════════════════════════════════════════════


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def flip(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class Venue(str, Enum):
    BINANCE = "binance"
    MT5 = "mt5"
    SOLANA = "solana"


# A SELL entry is only holdable where the venue can actually short. Binance here
# is spot and Solana is a DEX, so a short on those is a trade live mode would
# reject. Paper and backtest obey the same rule, or their results are fiction.
SHORTABLE_VENUES = frozenset({Venue.MT5})


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    pid: str
    venue: Venue
    symbol: str
    side: Side
    qty: float
    entry: float
    stop: float
    target: float
    opened: float = field(default_factory=time.time)

    def pnl(self, price: float) -> float:
        d = price - self.entry if self.side is Side.BUY else self.entry - price
        return d * self.qty


@dataclass
class Signal:
    venue: Venue
    symbol: str
    side: Side
    stop: float
    target: float
    strength: float
    reason: str


@dataclass
class Fill:
    """What the exchange actually gave us - never what we asked for.

    Booking the requested quantity is how bots end up believing they hold a
    position that a minimum-size or partial-fill rejection quietly denied.
    """

    price: float
    qty: float
    fee: float = 0.0


@dataclass
class Account:
    equity: float
    cash: float


# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════


@dataclass
class Config:
    mode: str = "paper"
    label: str = "BOT"
    host: str = "127.0.0.1"
    venues: list[str] = field(default_factory=lambda: ["binance"])
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "5m"
    poll: float = 5.0
    equity: float = 10_000.0
    risk_pct: float = 0.5
    max_positions: int = 4
    daily_loss_pct: float = 3.0
    atr_mult: float = 2.0
    reward_risk: float = 2.0
    min_score: float = 0.65
    cooldown_bars: int = 3
    fee_bps: float = 5.0
    slip_bps: float = 3.0
    db: str = "data/paper.db"
    port: int = 8080
    token: Optional[str] = None
    max_exposure_pct: float = 60.0
    notify_on_fill: bool = True


# ═══════════════════════════════════════════════════════════════════
#  MARKET DATA
# ═══════════════════════════════════════════════════════════════════

DATA_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
]


def http_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "quantbot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post_json(url: str, payload: dict, timeout: float = 30.0):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": "quantbot/1.0", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def pair(symbol: str) -> str:
    return symbol.replace("/", "").upper()


class Feed:
    def __init__(self) -> None:
        self.host: Optional[str] = None
        self._lock = threading.Lock()

    def _base(self) -> str:
        with self._lock:
            if self.host:
                return self.host
            for h in DATA_HOSTS:
                try:
                    http_json(h + "/api/v3/ping", timeout=6)
                    self.host = h
                    LOG.info("data host %s", h.replace("https://", ""))
                    return h
                except Exception:
                    continue
            raise RuntimeError("no reachable Binance data host")

    def price(self, symbol: str) -> float:
        return float(http_json(f"{self._base()}/api/v3/ticker/price?symbol={pair(symbol)}")["price"])

    def candles(self, symbol: str, timeframe: str, limit: int = 300) -> list[Candle]:
        raw = http_json(f"{self._base()}/api/v3/klines?symbol={pair(symbol)}"
                        f"&interval={timeframe}&limit={limit}")
        rows = [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in raw]
        return rows[:-1]


# ═══════════════════════════════════════════════════════════════════
#  BROKERS
# ═══════════════════════════════════════════════════════════════════


def venue_symbol(venue: Venue, symbol: str) -> str:
    """Same market, each platform's own spelling: BTC/USDT, BTCUSD, SOL/USDC."""
    if venue is Venue.MT5:
        base, _, quote = symbol.upper().partition("/")
        if not quote:
            return symbol.upper()          # already an MT5 symbol: XAUUSD
        return base + ("USD" if quote == "USDT" else quote)
    if venue is Venue.SOLANA and symbol.upper().startswith("SOL"):
        return "SOL/USDC"
    return symbol.upper()


class Broker:
    venue: Venue

    def account(self, positions: list[Position], prices: dict) -> Account:
        raise NotImplementedError

    def market(self, symbol: str, side: Side, qty: float, ref: float) -> Fill:
        raise NotImplementedError

    def flatten(self, position: Position, ref: float) -> Fill:
        raise NotImplementedError


class PaperWallet:
    """One pot of virtual cash, shared by every paper venue.

    Give each venue its own pot and a $10k account trading two platforms shows
    $20k of equity - the bot then sizes both books off money that isn't there.
    """

    def __init__(self, cash: float):
        self.cash = cash


class PaperBroker(Broker):
    def __init__(self, venue: Venue, equity: float, fee_bps: float, slip_bps: float,
                 wallet: Optional[PaperWallet] = None):
        self.venue = venue
        self.wallet = wallet or PaperWallet(equity)
        self.fee_bps = fee_bps
        self.slip_bps = slip_bps

    @property
    def cash(self) -> float:
        return self.wallet.cash

    @cash.setter
    def cash(self, value: float) -> None:
        self.wallet.cash = value

    def account(self, positions: list[Position], prices: dict) -> Account:
        eq = self.cash
        for p in positions:
            if p.venue is self.venue:
                eq += p.pnl(prices.get(p.symbol, p.entry))
        return Account(eq, self.cash)

    def market(self, symbol: str, side: Side, qty: float, ref: float) -> Fill:
        drift = ref * self.slip_bps / 10_000.0
        px = ref + drift if side is Side.BUY else ref - drift
        fee = px * qty * self.fee_bps / 10_000.0
        self.cash -= fee
        return Fill(px, qty, fee)

    def flatten(self, position: Position, ref: float) -> Fill:
        f = self.market(position.symbol, position.side.flip, position.qty, ref)
        self.cash += position.pnl(f.price)
        return f


class BinanceBroker(Broker):
    venue = Venue.BINANCE

    def __init__(self, key: str, secret: str, testnet: bool):
        import ccxt  # noqa
        self.ex = ccxt.binance({"apiKey": key, "secret": secret,
                                "enableRateLimit": True,
                                "options": {"defaultType": "spot"}})
        if testnet:
            self.ex.set_sandbox_mode(True)
        self.ex.load_markets()

    def account(self, positions: list[Position], prices: dict) -> Account:
        b = self.ex.fetch_balance()
        return Account(float(b["total"].get("USDT", 0.0)), float(b["free"].get("USDT", 0.0)))

    def market(self, symbol: str, side: Side, qty: float, ref: float) -> Fill:
        m = self.ex.market(symbol)
        limits = m.get("limits") or {}
        try:
            q = float(self.ex.amount_to_precision(symbol, qty))
        except Exception:
            q = qty
        min_amt = (limits.get("amount") or {}).get("min") or 0.0
        min_cost = (limits.get("cost") or {}).get("min") or 0.0
        if q <= 0 or (min_amt and q < float(min_amt)):
            raise RuntimeError(f"{symbol}: {q} below exchange minimum amount {min_amt}")
        if min_cost and q * ref < float(min_cost):
            raise RuntimeError(f"{symbol}: ${q * ref:.2f} below exchange minimum notional ${min_cost}")
        r = self.ex.create_order(symbol, "market", side.value.lower(), q)
        filled = float(r.get("filled") or 0.0)
        if filled <= 0:
            raise RuntimeError(f"{symbol}: order not filled (status={r.get('status')}, id={r.get('id')})")
        fee = 0.0
        f = r.get("fee")
        if isinstance(f, dict) and f.get("cost"):
            fee = float(f["cost"])
        px = float(r.get("average") or r.get("price") or ref)
        return Fill(px, filled, fee)

    def flatten(self, position: Position, ref: float) -> Fill:
        return self.market(venue_symbol(self.venue, position.symbol),
                           position.side.flip, position.qty, ref)


class MT5Broker(Broker):
    venue = Venue.MT5
    MAGIC = 770001

    def __init__(self, login: int, password: str, server: str, path: str = ""):
        import MetaTrader5 as mt5  # noqa
        self.mt5 = mt5
        kw = {"login": login, "password": password, "server": server}
        if path:
            kw["path"] = path
        if not mt5.initialize(**kw):
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")

    def account(self, positions: list[Position], prices: dict) -> Account:
        i = self.mt5.account_info()
        return Account(i.equity, i.margin_free)

    def market(self, symbol: str, side: Side, qty: float, ref: float) -> Fill:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"MT5 does not offer {symbol}")
        step = info.volume_step or 0.01
        vol = round(round(qty / step) * step, 8)
        if vol < info.volume_min:
            raise RuntimeError(f"{symbol}: {vol} below MT5 minimum volume {info.volume_min}")
        vol = min(vol, info.volume_max)
        t = self.mt5.symbol_info_tick(symbol)
        px = t.ask if side is Side.BUY else t.bid
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": vol,
               "type": self.mt5.ORDER_TYPE_BUY if side is Side.BUY else self.mt5.ORDER_TYPE_SELL,
               "price": px, "deviation": 20, "magic": self.MAGIC,
               "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self.mt5.ORDER_FILLING_IOC}
        r = self.mt5.order_send(req)
        if not r or r.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 reject {getattr(r, 'retcode', '?')}")
        return Fill(float(r.price), float(getattr(r, "volume", vol) or vol))

    def flatten(self, position: Position, ref: float) -> Fill:
        vsym = venue_symbol(self.venue, position.symbol)
        t = self.mt5.symbol_info_tick(vsym)
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": vsym,
               "volume": position.qty, "position": int(position.pid),
               "type": self.mt5.ORDER_TYPE_SELL if position.side is Side.BUY else self.mt5.ORDER_TYPE_BUY,
               "price": t.bid if position.side is Side.BUY else t.ask,
               "deviation": 30, "magic": self.MAGIC,
               "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self.mt5.ORDER_FILLING_IOC}
        r = self.mt5.order_send(req)
        if not r or r.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MT5 close reject {getattr(r, 'retcode', '?')}")
        return Fill(ref, position.qty)


class SolanaBroker(Broker):
    venue = Venue.SOLANA
    USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    SOL = "So11111111111111111111111111111111111111112"
    JUP = "https://quote-api.jup.ag/v6"

    def __init__(self, rpc: str, private_key: str, slippage_bps: int, feed: Feed):
        import base58  # noqa
        from solders.keypair import Keypair  # noqa
        self.rpc = rpc
        self.kp = Keypair.from_bytes(base58.b58decode(private_key))
        self.slip = slippage_bps
        self.feed = feed

    @property
    def pubkey(self) -> str:
        return str(self.kp.pubkey())

    def _bal(self, mint: str) -> float:
        if mint == self.SOL:
            r = http_post_json(self.rpc, {"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                                          "params": [self.pubkey]})
            return r["result"]["value"] / 1e9
        r = http_post_json(self.rpc, {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                                      "params": [self.pubkey, {"mint": mint}, {"encoding": "jsonParsed"}]})
        accs = r["result"]["value"]
        return float(accs[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]) if accs else 0.0

    def account(self, positions: list[Position], prices: dict) -> Account:
        usdc = self._bal(self.USDC)
        sol = self._bal(self.SOL) * prices.get("SOL/USDT", 0.0)
        return Account(usdc + sol, usdc)

    def _confirmed(self, signature: str, tries: int = 8, pause: float = 1.5) -> bool:
        for _ in range(tries):
            try:
                r = http_post_json(self.rpc, {"jsonrpc": "2.0", "id": 1,
                                              "method": "getSignatureStatuses",
                                              "params": [[signature], {"searchTransactionHistory": True}]})
                st = (r.get("result") or {}).get("value") or []
                if st and st[0] and st[0].get("confirmationStatus") in ("confirmed", "finalized"):
                    return True
                if st and st[0] and st[0].get("err"):
                    return False
            except Exception:
                pass
            time.sleep(pause)
        return False

    def market(self, symbol: str, side: Side, qty: float, ref: float) -> Fill:
        import base64
        notional = qty * ref
        if notional < 1.0:
            raise RuntimeError(f"{symbol}: ${notional:.2f} below Solana swap minimum ($1)")
        inp, out = (self.USDC, self.SOL) if side is Side.BUY else (self.SOL, self.USDC)
        amt = int(qty * ref * 1e6) if side is Side.BUY else int(qty * 1e9)
        q = http_json(f"{self.JUP}/quote?inputMint={inp}&outputMint={out}"
                      f"&amount={amt}&slippageBps={self.slip}")
        s = http_post_json(f"{self.JUP}/swap", {"quoteResponse": q, "userPublicKey": self.pubkey,
                                                "wrapAndUnwrapSol": True})
        from solders.transaction import VersionedTransaction  # noqa
        tx = VersionedTransaction.from_bytes(base64.b64decode(s["swapTransaction"]))
        signed = VersionedTransaction(tx.message, [self.kp])
        b64 = base64.b64encode(bytes(signed)).decode()
        r = http_post_json(self.rpc, {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                                      "params": [b64, {"encoding": "base64", "maxRetries": 3}]})
        if "error" in r:
            raise RuntimeError(f"solana send failed: {r['error']}")
        sig = r.get("result")
        if not sig:
            raise RuntimeError("solana send returned no signature")
        if not self._confirmed(sig):
            raise RuntimeError(f"solana tx {sig[:8]}... not confirmed")
        return Fill(ref, qty)

    def flatten(self, position: Position, ref: float) -> Fill:
        return self.market(venue_symbol(self.venue, position.symbol),
                           position.side.flip, position.qty, ref)


# ═══════════════════════════════════════════════════════════════════
#  RISK
# ═══════════════════════════════════════════════════════════════════


class Risk:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.day = ""
        self.day_start = 0.0
        self.halted = False
        self.halt_reason = ""
        self.cooldown: dict[str, float] = {}

    def roll_day(self, equity: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.day_start, self.halted, self.halt_reason = today, equity, False, ""

    def check_loss(self, equity: float) -> bool:
        if self.day_start <= 0:
            return False
        dd = (self.day_start - equity) / self.day_start * 100.0
        if dd >= self.cfg.daily_loss_pct:
            self.halted = True
            self.halt_reason = f"daily loss {dd:.2f}% >= {self.cfg.daily_loss_pct}%"
            return True
        return False

    def size(self, equity: float, entry: float, stop: float) -> float:
        risk = abs(entry - stop)
        if risk <= 0:
            return 0.0
        return round((equity * self.cfg.risk_pct / 100.0) / risk, 8)

    def approve(self, sig: Signal, equity: float, positions: list[Position]) -> tuple[bool, str]:
        if self.halted:
            return False, "halted"
        if len(positions) >= self.cfg.max_positions:
            return False, "max positions"
        if any(p.symbol == sig.symbol for p in positions):
            return False, "already open"
        if self.cooldown.get(sig.symbol, 0) > time.time():
            return False, "cooldown"
        return True, "ok"


# ═══════════════════════════════════════════════════════════════════
#  JOURNAL
# ═══════════════════════════════════════════════════════════════════


class Journal:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS ev(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL,kind TEXT,"
            "venue TEXT,symbol TEXT,side TEXT,qty REAL,price REAL,pnl REAL,note TEXT);"
            "CREATE TABLE IF NOT EXISTS eq(id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL,equity REAL);")

    def write(self, kind: str, **kw) -> None:
        with self._lock:
            self.db.execute("INSERT INTO ev(ts,kind,venue,symbol,side,qty,price,pnl,note) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (time.time(), kind, kw.get("venue"), kw.get("symbol"),
                             kw.get("side"), kw.get("qty"), kw.get("price"),
                             kw.get("pnl"), kw.get("note")))
            self.db.commit()

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            cur = self.db.execute("SELECT * FROM ev ORDER BY id DESC LIMIT ?", (n,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        with self._lock:
            t, w = self.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(pnl>0),0) FROM ev WHERE kind='close'").fetchone()
        return {"trades": t, "wins": w, "win_rate": (w / t * 100.0) if t else 0.0,
                "realized": self.realized()}

    def realized(self) -> float:
        with self._lock:
            return float(self.db.execute("SELECT COALESCE(SUM(pnl),0) FROM ev WHERE kind='close'")
                         .fetchone()[0] or 0.0)

    def reset_equity(self) -> None:
        """Drop the curve when the book restarts at a different size."""
        with self._lock:
            self.db.execute("DELETE FROM eq")
            self.db.commit()

    def sample_equity(self, equity: float) -> None:
        with self._lock:
            self.db.execute("INSERT INTO eq(ts,equity) VALUES(?,?)", (time.time(), equity))
            self.db.execute("DELETE FROM eq WHERE id NOT IN "
                            "(SELECT id FROM eq ORDER BY id DESC LIMIT 2000)")
            self.db.commit()

    def equity_series(self, n: int = 240) -> list[dict]:
        with self._lock:
            cur = self.db.execute("SELECT ts,equity FROM eq ORDER BY id DESC LIMIT ?", (n,))
            rows = cur.fetchall()[::-1]
        return [{"ts": r[0], "equity": round(r[1], 2)} for r in rows]


# ═══════════════════════════════════════════════════════════════════
#  ENGINE
# ═══════════════════════════════════════════════════════════════════


class Engine:
    def __init__(self, cfg: Config, brokers: dict[Venue, Broker], feed: Feed,
                 journal: Journal, risk: Risk, notifier: Optional[notify.Notifier] = None):
        self.cfg = cfg
        self.brokers = brokers
        self.feed = feed
        self.journal = journal
        self.risk = risk
        self.notifier = notifier
        self.positions: dict[str, Position] = {}
        self.prices: dict[str, float] = {}
        self.last_bar: dict[str, int] = {}
        self.paused = False
        self.stopping = False
        self.started = time.time()
        self.ticks = 0
        self.active_venue: Optional[Venue] = None
        self._saved_at = 0.0
        self._sampled_at = 0.0
        self._restore()

    # ── platform choice ──────────────────────────────────────────────

    def set_venue(self, venue: Optional[Venue]) -> str:
        """Pick where the next trade goes, without restarting the engine."""
        if venue is not None and venue not in self.brokers:
            return f"{venue.value} is not enabled on this engine"
        self.active_venue = venue
        self._save(force=True)
        LOG.info("active venue set to %s", venue.value if venue else "auto")
        return "auto" if venue is None else venue.value

    def pick_venue(self, symbol: str) -> Venue:
        """Chosen platform if it can trade this pair, else the pair's natural home."""
        if self.active_venue is not None and self.active_venue in self.brokers:
            return self.active_venue
        return self.venue_for(symbol)

    # ── paper state that survives a restart ──────────────────────────

    def _state_path(self) -> str:
        mode, label = self.cfg.mode.lower(), self.cfg.label.lower()
        raw = mode if label == mode else f"{mode}-{label}"
        tag = "-".join(p for p in "".join(c if c.isalnum() else "-" for c in raw).split("-") if p)
        return os.path.join(os.path.dirname(self.cfg.db) or ".", f"state-{tag}.json")

    def _save(self, force: bool = False) -> None:
        """Paper only. Live positions already exist on the exchange; reloading
        them from a file after a restart would make us trade a phantom book."""
        if self.cfg.mode != "paper":
            return
        now = time.time()
        if not force and now - self._saved_at < 5.0:
            return
        payload = {
            "saved": now,
            "equity": self.cfg.equity,
            "active_venue": self.active_venue.value if self.active_venue else None,
            "day": self.risk.day,
            "day_start": self.risk.day_start,
            "halted": self.risk.halted,
            "halt_reason": self.risk.halt_reason,
            "cooldown": self.risk.cooldown,
            "last_bar": self.last_bar,
            "cash": {v.value: b.cash for v, b in self.brokers.items()
                     if isinstance(b, PaperBroker)},
            "positions": [{"pid": p.pid, "venue": p.venue.value, "symbol": p.symbol,
                           "side": p.side.value, "qty": p.qty, "entry": p.entry,
                           "stop": p.stop, "target": p.target, "opened": p.opened}
                          for p in self.positions.values()],
        }
        path = self._state_path()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1)
            os.replace(tmp, path)
            self._saved_at = now
        except Exception as exc:
            LOG.warning("could not save state: %s", exc)

    def _restore(self) -> None:
        if self.cfg.mode != "paper":
            return
        path = self._state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except Exception as exc:
            LOG.warning("state file unreadable, starting fresh: %s", exc)
            return
        saved_eq = st.get("equity")
        if saved_eq is not None and self.cfg.equity and \
                abs(float(saved_eq) - self.cfg.equity) > 0.01:
            # started with a different account size: carrying the old cash and
            # loss baseline across would report P&L that never happened
            LOG.info("paper equity changed $%.2f -> $%.2f, starting a fresh book",
                     float(saved_eq), self.cfg.equity)
            self.journal.reset_equity()
            return
        saved_cash = st.get("cash") or {}
        for venue_name, cash in saved_cash.items():
            try:
                b = self.brokers.get(Venue(venue_name))
                if isinstance(b, PaperBroker):
                    b.cash = float(cash)
            except ValueError:
                continue
        # the saved book is only meaningful for this run if every paper venue
        # in it is actually enabled now (e.g. a solana book cannot feed an mt5 run)
        covered = all(v.value in saved_cash for v, b in self.brokers.items()
                      if isinstance(b, PaperBroker))
        if saved_eq is None:
            # written before the account size was recorded: its loss baseline
            # cannot be matched to this run, so start the day from today's equity
            covered = False
        for row in st.get("positions") or []:
            try:
                p = Position(row["pid"], Venue(row["venue"]), row["symbol"],
                             Side(row["side"]), float(row["qty"]), float(row["entry"]),
                             float(row["stop"]), float(row["target"]),
                             float(row.get("opened") or time.time()))
            except Exception:
                continue
            if p.venue in self.brokers:
                self.positions[p.pid] = p
        if st.get("active_venue"):
            try:
                v = Venue(st["active_venue"])
                self.active_venue = v if v in self.brokers else None
            except ValueError:
                pass
        self.last_bar = {k: v for k, v in (st.get("last_bar") or {}).items()}
        self.risk.cooldown = {k: float(v) for k, v in (st.get("cooldown") or {}).items()}
        # restore the day first: roll_day compares against it and would otherwise
        # hand the engine a fresh loss baseline, quietly re-arming after a halt
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.risk.day = st.get("day") if st.get("day") == today else ""
        if st.get("day_start") and covered and st.get("day") == today:
            self.risk.day_start = float(st["day_start"])
            self.risk.halted = bool(st.get("halted"))
            self.risk.halt_reason = st.get("halt_reason") or ""
        elif st.get("day_start"):
            LOG.info("not restoring the loss baseline (%s, venues %s) - "
                     "today starts from the current equity",
                     "stale day" if st.get("day") != today else "different venues",
                     ",".join(sorted(saved_cash)) or "none")
        pots = {id(b.wallet): b.cash for b in self.brokers.values()
                if isinstance(b, PaperBroker)}
        LOG.info("restored paper state: %d position(s), free cash $%.2f",
                 len(self.positions), sum(pots.values()))

    # ── market data ─────────────────────────────────────────────────

    def refresh(self) -> None:
        for sym in self.cfg.symbols:
            try:
                self.prices[sym] = self.feed.price(sym)
            except Exception:
                continue

    def equity(self) -> float:
        positions = list(self.positions.values())
        total, seen = 0.0, set()
        for b in self.brokers.values():
            acct = b.account(positions, self.prices)
            w = getattr(b, "wallet", None)
            if w is None:
                total += acct.equity            # live: each venue reports its own
            elif id(w) in seen:
                total += acct.equity - acct.cash  # shared pot: count pnl only
            else:
                seen.add(id(w))
                total += acct.equity
        return total

    def exposure_pct(self, equity: float) -> float:
        if equity <= 0:
            return 100.0
        notional = sum(self.prices.get(p.symbol, p.entry) * p.qty for p in self.positions.values())
        return notional / equity * 100.0

    def tick(self) -> None:
        self.ticks += 1
        self.refresh()
        eq = self.equity()
        self.risk.roll_day(eq)
        self._save()
        if time.time() - self._sampled_at >= 30.0:
            self._sampled_at = time.time()
            self.journal.sample_equity(eq)

        if self.risk.check_loss(eq):
            LOG.warning("HALTED: %s", self.risk.halt_reason)
            if self.notifier:
                self.notifier.send(f"[{self.cfg.label}] HALTED", self.risk.halt_reason, key="halt")
            self.flatten_all("halt")
            return

        self.manage()
        if not self.paused:
            self.scan(eq)

    def manage(self) -> None:
        for pos in list(self.positions.values()):
            px = self.prices.get(pos.symbol)
            if px is None:
                continue
            hit_stop = px <= pos.stop if pos.side is Side.BUY else px >= pos.stop
            hit_tgt = px >= pos.target if pos.side is Side.BUY else px <= pos.target
            if hit_stop or hit_tgt:
                self.close(pos, px, "stop" if hit_stop else "target")

    def close(self, pos: Position, ref: float, reason: str) -> None:
        try:
            fill = self.brokers[pos.venue].flatten(pos, ref)
        except Exception as exc:
            LOG.error("close failed %s: %s", pos.symbol, exc)
            return
        pnl = pos.pnl(fill.price)
        self.positions.pop(pos.pid, None)
        self.journal.write("close", venue=pos.venue.value, symbol=pos.symbol,
                           side=pos.side.value, qty=pos.qty, price=fill.price,
                           pnl=pnl, note=reason)
        self.risk.cooldown[pos.symbol] = time.time() + self.cfg.cooldown_bars * 300
        LOG.info("EXIT %s %s %+.2f (%s)", pos.symbol, pos.side.value, pnl, reason)
        self._save(force=True)
        if self.notifier:
            self.notifier.send(
                f"[{self.cfg.label}] CLOSE {pos.symbol} {pnl:+.2f}",
                f"{pos.symbol} {pos.side.value} closed at {fill.price:.6f}\npnl {pnl:+.2f}\nreason {reason}",
                key=f"close:{pos.symbol}")

    def scan(self, equity: float) -> None:
        for sym in self.cfg.symbols:
            venue = self.pick_venue(sym)
            if venue not in self.brokers:
                LOG.debug("skip %s: %s is not enabled", sym, venue.value)
                continue
            try:
                candles = self.feed.candles(sym, self.cfg.timeframe, 300)
            except Exception as exc:
                LOG.debug("candles %s: %s", sym, exc)
                continue
            if not candles or candles[-1].ts == self.last_bar.get(sym):
                continue
            self.last_bar[sym] = candles[-1].ts

            rules = signals.Rules(atr_mult=self.cfg.atr_mult, reward_risk=self.cfg.reward_risk)
            v = signals.evaluate(candles, rules, self.cfg.min_score)
            if v.side and v.best:
                LOG.info("SIGNAL %s %s score=%.2f via %s (%s)",
                         sym, v.side, v.score, v.best.name, v.best.reason)
                self.enter(sym, venue, v.side, v.best, v.score, equity)
            elif v.conflict:
                LOG.debug("%s conflicting signals", sym)

    def enter(self, sym: str, venue: Venue, side: str, cand, score: float, equity: float) -> None:
        if Side(side) is Side.SELL and venue not in SHORTABLE_VENUES:
            LOG.info("SKIP %s: %s has no short side (spot/DEX), entry rejected", sym, venue.value)
            return
        sig = Signal(venue, sym, Side(side), cand.stop, cand.target, score, cand.name)
        ok, why = self.risk.approve(sig, equity, list(self.positions.values()))
        if not ok:
            LOG.info("SKIP %s: %s", sym, why)
            return
        px = self.prices.get(sym, 0.0)
        if px <= 0:
            return
        qty = self.risk.size(equity, px, cand.stop)
        if qty <= 0:
            return

        # Sizing is risk-based, so a tight stop asks for a huge notional (a 0.3%
        # stop on $10k at 0.5% risk wants 150% of equity). Spot has no leverage,
        # so clamp the position to whatever exposure room the book has left.
        used = sum(self.prices.get(p.symbol, p.entry) * p.qty
                   for p in self.positions.values())
        room = equity * self.cfg.max_exposure_pct / 100.0 - used
        if room <= 0:
            LOG.info("SKIP %s: exposure cap %.0f%% reached", sym, self.cfg.max_exposure_pct)
            return
        max_qty = room / px
        if qty > max_qty:
            LOG.info("size %s %.8f capped to %.8f by the %.0f%% exposure limit",
                     sym, qty, max_qty, self.cfg.max_exposure_pct)
            qty = max_qty
        try:
            fill = self.brokers[venue].market(venue_symbol(venue, sym), Side(side), qty, px)
        except Exception as exc:
            LOG.error("entry failed %s: %s", sym, exc)
            return
        qty = fill.qty
        if qty <= 0:
            LOG.error("entry %s: exchange reported no filled quantity", sym)
            return
        if px and abs(fill.price - px) / px * 100.0 > 2.0:
            LOG.warning("fill %s traded %.2f%% away from last price - slippage or wrong market",
                        sym, abs(fill.price - px) / px * 100.0)

        pid = f"{int(time.time()*1000)}-{len(self.positions)}"
        self.positions[pid] = Position(pid, venue, sym, Side(side), qty, fill.price,
                                       cand.stop, cand.target)
        self.journal.write("open", venue=venue.value, symbol=sym, side=side,
                           qty=qty, price=fill.price, note=cand.name)
        LOG.info("FILL %s %s %.6f @ %.6f stop=%.6f target=%.6f",
                 sym, side, qty, fill.price, cand.stop, cand.target)
        self._save(force=True)
        if self.notifier and self.cfg.notify_on_fill:
            self.notifier.send(
                f"[{self.cfg.label}] OPEN {sym} {side}",
                f"{sym} {side} {qty:.6f} @ {fill.price:.6f}\nvenue {venue.value}\nstrategy {cand.name}\n"
                f"stop {cand.stop:.6f}  target {cand.target:.6f}\nscore {score}",
                key=f"open:{sym}")

    def flatten_all(self, reason: str = "manual") -> None:
        for pos in list(self.positions.values()):
            self.close(pos, self.prices.get(pos.symbol, pos.entry), reason)

    def venue_for(self, symbol: str) -> Venue:
        """Where this pair would naturally trade - but only if it is enabled."""
        want = self._natural_venue(symbol)
        if want in self.brokers or not self.brokers:
            return want
        # the pair's natural home is switched off here (BTC/USDT on an MT5-only
        # engine, say). Route to something enabled instead of skipping the pair
        # forever with no explanation in the log.
        for v in (Venue.BINANCE, Venue.MT5, Venue.SOLANA):
            if v in self.brokers:
                return v
        return want

    def _natural_venue(self, symbol: str) -> Venue:
        if symbol.upper().startswith("SOL") and "solana" in self.cfg.venues:
            return Venue.SOLANA
        if "/" in symbol and symbol.split("/")[1] in ("USDT", "USDC", "BTC", "ETH"):
            return Venue.BINANCE
        return Venue.MT5

    def status(self) -> dict:
        eq = self.equity()
        return {
            "label": self.cfg.label,
            "uptime_s": int(time.time() - self.started),
            "ticks": self.ticks,
            "mode": self.cfg.mode,
            "venues": self.cfg.venues,
            "active_venue": self.active_venue.value if self.active_venue else "auto",
            "paused": self.paused,
            "halted": self.risk.halted,
            "halt_reason": self.risk.halt_reason,
            "equity": round(eq, 2),
            "day_pnl": round(eq - self.risk.day_start, 2) if self.risk.day_start else 0.0,
            "exposure_pct": round(self.exposure_pct(eq), 2),
            "positions": [{"symbol": p.symbol, "venue": p.venue.value, "side": p.side.value,
                           "qty": p.qty, "entry": p.entry, "stop": p.stop, "target": p.target,
                           "pnl": round(p.pnl(self.prices.get(p.symbol, p.entry)), 2)}
                          for p in self.positions.values()],
            "prices": self.prices,
            "stats": self.journal.stats(),
        }


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════

HTML = """<!doctype html><meta charset=utf-8><title>quantbot</title>
<style>
:root{--bg:#0b0e13;--p:#11161f;--l:#1d2430;--t:#c9d3e0;--d:#5d6b80;--u:#7fe3a1;--n:#ff8b8b}
body{margin:0;background:var(--bg);color:var(--t);font:13px ui-monospace,monospace;padding:20px}
h1{font-size:13px;letter-spacing:.18em;color:var(--u);margin:0 0 16px}
.g{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.c{background:var(--p);border:1px solid var(--l);border-radius:8px;padding:10px}
.c b{display:block;font-size:9px;color:var(--d);letter-spacing:.1em;margin-bottom:5px;font-weight:400}
.c s{font-size:18px;text-decoration:none}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--d);text-align:left;font-weight:400;padding:5px 4px}
td{padding:6px 4px;border-top:1px solid #1a212c}
button{background:none;border:1px solid var(--l);color:var(--t);padding:7px 14px;
border-radius:6px;cursor:pointer;font:inherit;margin-right:6px}
.u{color:var(--u)}.n{color:var(--n)}
</style>
<h1>QUANTBOT <span id=st style="color:var(--d)">connecting…</span></h1>
<div class=g>
<div class=c><b>EQUITY</b><s id=eq>—</s></div>
<div class=c><b>DAY P&L</b><s id=dp>—</s></div>
<div class=c><b>OPEN</b><s id=op>0</s></div>
<div class=c><b>WIN RATE</b><s id=wr>—</s></div>
</div>
<div style="margin-bottom:14px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">
<button onclick="p('pause')">PAUSE</button><button onclick="p('resume')">RESUME</button>
<button onclick="p('flatten')">FLATTEN</button>
<label style="color:var(--d);margin-left:10px;letter-spacing:.1em">TRADE ON</label>
<select id=ven onchange="setVen(this.value)" style="background:var(--p);color:var(--t);
border:1px solid var(--l);border-radius:6px;padding:6px 8px;font:inherit;font-size:12px"></select>
</div>
<div class=c style="margin-bottom:14px"><b>EQUITY CURVE</b>
<canvas id=chart width=900 height=90 style="width:100%;height:90px"></canvas></div>
<table><thead><tr><th>VENUE</th><th>SYMBOL</th><th>SIDE</th><th>QTY</th><th>ENTRY</th><th>STOP</th><th>P&L</th></tr></thead><tbody id=rows></tbody></table>
<h1 style="margin-top:20px">RECENT TRADES</h1>
<table><thead><tr><th>TIME</th><th>EVENT</th><th>VENUE</th><th>SYMBOL</th><th>SIDE</th><th>QTY</th><th>PRICE</th><th>P&L</th></tr></thead><tbody id=ev></tbody></table>
<script>
const T=new URLSearchParams(location.search).get('t')||'';
const q=T?'?t='+encodeURIComponent(T):'';
const p=k=>fetch('/api/'+k+q,{method:'POST'}).then(load);
const setVen=v=>fetch('/api/venue?v='+encodeURIComponent(v)+(T?'&t='+encodeURIComponent(T):''),{method:'POST'}).then(load);
function drawChart(pts){
 const c=chart.getContext('2d'), W=chart.width, H=chart.height;
 c.clearRect(0,0,W,H);
 if(pts.length<2){ c.fillStyle='#5d6b80'; c.font='10px ui-monospace,monospace';
  c.fillText('collecting samples - one every 30s',6,H/2); return; }
 const lo=Math.min(...pts.map(x=>x.equity)), hi=Math.max(...pts.map(x=>x.equity));
 const span=(hi-lo)||1;
 c.beginPath();
 pts.forEach((x,i)=>{ const px=i/(pts.length-1)*W, py=H-4-((x.equity-lo)/span)*(H-8);
  i?c.lineTo(px,py):c.moveTo(px,py); });
 c.strokeStyle=pts[pts.length-1].equity>=pts[0].equity?'#7fe3a1':'#ff8b8b';
 c.lineWidth=1.5; c.stroke();
 c.fillStyle='#5d6b80'; c.font='10px ui-monospace,monospace';
 c.fillText('$'+hi.toFixed(0),4,10); c.fillText('$'+lo.toFixed(0),4,H-2);
}
async function loadEvents(){
 const r=await fetch('/api/events'+q); if(!r.ok) return;
 const rows=await r.json();
 ev.innerHTML=rows.map(e=>`<tr><td>${new Date(e.ts*1000).toLocaleTimeString()}</td><td>${e.kind}</td>
  <td>${e.venue||''}</td><td>${e.symbol||''}</td><td>${e.side||''}</td>
  <td>${e.qty?e.qty.toFixed(6):''}</td><td>${e.price?e.price.toFixed(2):''}</td>
  <td class=${(e.pnl||0)>=0?'u':'n'}>${e.pnl==null?'':e.pnl.toFixed(2)}</td></tr>`).join('')
  || '<tr><td colspan=8 style="color:#5d6b80">no trades yet</td></tr>';
}
async function load(){
 const r=await fetch('/api/status'+q); if(!r.ok){st.textContent='unauthorized';return;}
 const d=await r.json();
 st.textContent=d.halted?'HALTED':d.paused?'PAUSED':'RUNNING';
 st.className=d.halted||d.paused?'n':'u';
 eq.textContent='$'+d.equity.toFixed(2);
 dp.textContent=(d.day_pnl>=0?'+':'')+'$'+d.day_pnl.toFixed(2);
 dp.className=d.day_pnl>=0?'u':'n';
 op.textContent=d.positions.length; wr.textContent=d.stats.win_rate.toFixed(1)+'%';
 rows.innerHTML=d.positions.map(x=>`<tr><td>${x.venue}</td><td>${x.symbol}</td>
  <td>${x.side}</td><td>${x.qty}</td><td>${x.entry}</td><td>${x.stop}</td>
  <td class=${x.pnl>=0?'u':'n'}>${x.pnl.toFixed(2)}</td></tr>`).join('')
  || '<tr><td colspan=7 style="color:#5d6b80">flat</td></tr>';
 if(ven.options.length!==d.venues.length+1){
  ven.innerHTML='<option value="auto">auto (per pair)</option>'+
   d.venues.map(v=>`<option value="${v}">${v}</option>`).join('');
 }
 ven.value=d.active_venue;
}
async function refresh(){
 await load();
 try{ const r=await fetch('/api/equity'+q); if(r.ok) drawChart(await r.json()); }catch(e){}
 await loadEvents();
}
refresh(); setInterval(refresh,5000);
</script>"""


def make_handler(engine: Engine, token: Optional[str]):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self, q) -> bool:
            return token is None or q.get("t", [None])[0] == token

        def _send(self, code: int, body, ctype="application/json"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                return self._send(200, HTML, "text/html; charset=utf-8")
            if not self._ok(q):
                return self._send(401, '{"error":"unauthorized"}')
            if u.path == "/api/status":
                return self._send(200, json.dumps(engine.status()))
            if u.path == "/api/events":
                return self._send(200, json.dumps(engine.journal.recent(50)))
            if u.path == "/api/equity":
                return self._send(200, json.dumps(engine.journal.equity_series(240)))
            self._send(404, '{"error":"not found"}')

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if not self._ok(q):
                return self._send(401, '{"error":"unauthorized"}')
            if u.path == "/api/pause":
                engine.paused = True
            elif u.path == "/api/resume":
                engine.paused, engine.risk.halted = False, False
            elif u.path == "/api/flatten":
                engine.flatten_all()
            elif u.path == "/api/venue":
                choice = q.get("v", ["auto"])[0]
                if choice != "auto":
                    try:
                        Venue(choice)
                    except ValueError:
                        return self._send(400, '{"error":"unknown venue"}')
                LOG.info("venue -> %s", choice)
                engine.set_venue(None if choice == "auto" else Venue(choice))
            else:
                return self._send(404, '{"error":"not found"}')
            self._send(200, json.dumps(engine.status()))

    return H


def serve(engine: Engine, port: int, token: Optional[str], host: str = "127.0.0.1") -> None:
    srv = ThreadingHTTPServer((host, port), make_handler(engine, token))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    LOG.info("dashboard http://%s:%d%s", host, port, f"?t={token}" if token else "")


# ═══════════════════════════════════════════════════════════════════
#  BACKTEST
# ═══════════════════════════════════════════════════════════════════


def backtest(cfg: Config, symbol: str, bars: int) -> None:
    feed = Feed()
    candles = feed.candles(symbol, cfg.timeframe, min(max(bars, 120), 1000))
    if len(candles) < 120:
        print(f"not enough history ({len(candles)} bars)")
        return

    risk = Risk(cfg)
    equity = cfg.equity
    peak = equity
    max_dd = 0.0
    pos: Optional[Position] = None
    wins = losses = 0
    rules = signals.Rules(atr_mult=cfg.atr_mult, reward_risk=cfg.reward_risk)

    for i in range(60, len(candles)):
        bar = candles[i]
        px = bar.close

        if pos:
            hit_s = px <= pos.stop if pos.side is Side.BUY else px >= pos.stop
            hit_t = px >= pos.target if pos.side is Side.BUY else px <= pos.target
            if hit_s or hit_t:
                exit_px = pos.stop if hit_s else pos.target
                pnl = pos.pnl(exit_px) - exit_px * pos.qty * cfg.fee_bps / 10_000
                equity += pnl
                wins += pnl > 0
                losses += pnl <= 0
                pos = None

        if pos is None:
            v = signals.evaluate(candles[:i + 1], rules, cfg.min_score)
            if v.side and v.best:
                if v.side == "SELL" and Venue.BINANCE not in SHORTABLE_VENUES:
                    continue  # spot: a short here would never have filled live
                qty = risk.size(equity, px, v.best.stop)
                if qty > 0:
                    pos = Position("bt", Venue.BINANCE, symbol, Side(v.side), qty, px,
                                   v.best.stop, v.best.target)

        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0)

    total = wins + losses
    ret = (equity - cfg.equity) / cfg.equity * 100.0
    print("\n" + "─" * 46)
    print(f"  BACKTEST  {symbol}  {cfg.timeframe}  {len(candles)} bars")
    print("─" * 46)
    print(f"  final equity   ${equity:,.2f}  ({ret:+.2f}%)")
    print(f"  trades         {total}")
    print(f"  win rate       {(wins/total*100 if total else 0):.1f}%")
    print(f"  max drawdown   {max_dd:.2f}%")
    if total:
        print(f"  expectancy     ${(equity-cfg.equity)/total:+.2f} / trade")
    print("─" * 46)
    if total < 15:
        print("  warning: fewer than 15 trades - statistically thin\n")


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════


def build_brokers(cfg: Config, feed: Feed) -> dict[Venue, Broker]:
    out: dict[Venue, Broker] = {}
    wallet = PaperWallet(cfg.equity)   # shared by every paper venue
    for v in cfg.venues:
        venue = Venue(v)
        try:
            if cfg.mode == "paper":
                out[venue] = PaperBroker(venue, cfg.equity, cfg.fee_bps, cfg.slip_bps, wallet)
            elif venue is Venue.BINANCE:
                out[venue] = BinanceBroker(os.getenv("QB_BINANCE_KEY", ""),
                                           os.getenv("QB_BINANCE_SECRET", ""),
                                           os.getenv("QB_BINANCE_TESTNET", "true") == "true")
            elif venue is Venue.MT5:
                out[venue] = MT5Broker(int(os.getenv("QB_MT5_LOGIN", "0")),
                                       os.getenv("QB_MT5_PASSWORD", ""),
                                       os.getenv("QB_MT5_SERVER", ""),
                                       os.getenv("QB_MT5_PATH", ""))
            elif venue is Venue.SOLANA:
                out[venue] = SolanaBroker(os.getenv("QB_SOLANA_RPC",
                                                    "https://api.mainnet-beta.solana.com"),
                                          os.getenv("QB_SOLANA_KEY", ""),
                                          int(os.getenv("QB_SOLANA_SLIP", "80")), feed)
            LOG.info("broker %s ready (%s)", venue.value, cfg.mode)
        except Exception as exc:
            LOG.error("venue %s disabled: %s", venue.value, exc)
    return out


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="quantbot engine")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--label", default="BOT")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--venues", default="binance")
    p.add_argument("--symbols", default="BTC/USDT")
    p.add_argument("--timeframe", default="5m", choices=["1m", "5m", "15m", "1h", "4h", "1d"])
    p.add_argument("--equity", type=float, default=10_000.0)
    p.add_argument("--risk", type=float, default=0.5)
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--min-score", type=float, default=0.65)
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--token", default=None)
    p.add_argument("--db", default="data/paper.db")
    p.add_argument("--backtest", type=int, metavar="BARS")
    p.add_argument("--once", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    envfile.load()
    a = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    cfg = Config(
        mode=a.mode, label=a.label, host=a.host,
        venues=[v.strip() for v in a.venues.split(",") if v.strip()],
        symbols=[s.strip().upper() for s in a.symbols.split(",") if s.strip()],
        timeframe=a.timeframe, poll=a.poll, equity=a.equity, risk_pct=a.risk,
        min_score=a.min_score, db=a.db, port=a.port, token=a.token,
    )

    if a.backtest:
        backtest(cfg, cfg.symbols[0], a.backtest)
        return 0

    if cfg.host != "127.0.0.1" and not cfg.token:
        LOG.error("binding to %s with no --token; refusing to start", cfg.host)
        return 1

    LOG.info("boot [%s] mode=%s venues=%s symbols=%s equity=%.2f",
             cfg.label, cfg.mode, ",".join(cfg.venues), ",".join(cfg.symbols), cfg.equity)
    if cfg.mode == "live":
        LOG.warning("LIVE MODE - real money")

    feed = Feed()
    try:
        feed._base()
    except Exception as exc:
        LOG.error("market data unavailable: %s", exc)
        return 1

    brokers = build_brokers(cfg, feed)
    if not brokers:
        LOG.error("no brokers available")
        return 1

    engine = Engine(cfg, brokers, feed, Journal(cfg.db), Risk(cfg), notify.from_env())
    engine.refresh()

    if a.once:
        engine.tick()
        print(json.dumps(engine.status(), indent=2))
        return 0

    serve(engine, cfg.port, cfg.token, cfg.host)
    LOG.info("engine started - %s fills", "virtual" if cfg.mode == "paper" else "real")

    def stop(*_):
        engine.stopping = True
        engine._save(force=True)
        LOG.info("shutdown (positions kept on disk, resumed next start)")
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    try:
        signal.signal(signal.SIGTERM, stop)
    except AttributeError:
        pass

    while not engine.stopping:
        try:
            engine.tick()
        except Exception:
            LOG.exception("tick failed")
        time.sleep(cfg.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())