#!/usr/bin/env python3
"""Read-only preflight for live trading.

Places no orders and changes nothing. It answers one question: could the live
engine safely start right now, and if not, exactly what is missing.

    .venv\\Scripts\\python.exe run\\live_preflight.py
    .venv\\Scripts\\python.exe run\\live_preflight.py --venues binance,mt5

Exit codes: 0 = everything green, 1 = warnings only, 2 = blocked.
Secrets are never printed - only whether a value is present and what the
exchange says about the key's permissions.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import envfile  # noqa: E402

OK, WARN, FAIL = "PASS", "WARN", "FAIL"
rows: list[tuple[str, str, str]] = []


def check(state: str, name: str, detail: str = "") -> None:
    rows.append((state, name, detail))
    print(f"[{state}] {name}" + (f" - {detail}" if detail else ""))


def have(key: str) -> bool:
    v = os.getenv(key, "") or ""
    return bool(v.strip())


def binance() -> None:
    if not have("QB_BINANCE_KEY") or not have("QB_BINANCE_SECRET"):
        check(FAIL, "binance credentials", "QB_BINANCE_KEY / QB_BINANCE_SECRET are empty")
        return
    testnet = (os.getenv("QB_BINANCE_TESTNET", "true") or "").lower() == "true"
    check(WARN if testnet else OK, "binance network",
          "TESTNET (no real money)" if testnet else "MAINNET (real money)")
    try:
        import ccxt
        ex = ccxt.binance({
            "apiKey": os.environ["QB_BINANCE_KEY"],
            "secret": os.environ["QB_BINANCE_SECRET"],
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if testnet:
            ex.set_sandbox_mode(True)
        # permissions first: this is the one that must never say withdrawals=on
        try:
            res = ex.sapi_get_account_apirestrictions()
            can_trade = res.get("enableSpotAndMarginTrading", res.get("enableReading"))
            can_withdraw = res.get("enableWithdrawals", None)
            if can_withdraw is True:
                check(FAIL, "binance key can WITHDRAW", "revoke it now and create a trade-only key")
            else:
                check(OK, "binance key cannot withdraw", f"enableWithdrawals={can_withdraw}")
            check(OK if can_trade else FAIL, "binance key can spot trade",
                  f"enableSpotAndMarginTrading={can_trade}")
        except Exception as exc:
            check(WARN, "binance key permissions unreadable", str(exc)[:90])
        bal = ex.fetch_balance()
        usdt = float((bal.get("free") or {}).get("USDT", 0.0) or 0.0)
        check(OK if usdt >= 20 else FAIL, "binance free USDT",
              f"${usdt:.2f}" + ("" if usdt >= 20 else " (too small for min notional + risk sizing)"))
    except Exception as exc:
        check(FAIL, "binance reachable / keys valid", str(exc)[:110])


def mt5() -> None:
    if not have("QB_MT5_LOGIN") or not have("QB_MT5_SERVER"):
        check(FAIL, "mt5 credentials", "QB_MT5_LOGIN / QB_MT5_SERVER are empty")
        return
    try:
        import MetaTrader5 as mt5mod  # noqa
    except Exception:
        check(FAIL, "mt5 module", "MetaTrader5 import failed (Windows + terminal required)")
        return
    path = os.getenv("QB_MT5_PATH", "")
    if path and not Path(path).exists():
        check(FAIL, "mt5 terminal", f"not found at {path}")
        return
    ok = mt5mod.initialize(path if path else None)
    if not ok:
        check(FAIL, "mt5 initialize", f"error {mt5mod.last_error()} - is the terminal open and logged in in THIS session?")
        return
    try:
        info = mt5mod.account_info()
        if info is None:
            check(FAIL, "mt5 account_info", "terminal logged out")
            return
        check(OK if info.trade_allowed else FAIL, "mt5 trading allowed", f"account {info.login} on {info.server}")
        check(OK if info.balance >= 20 else FAIL, "mt5 balance", f"${info.balance:.2f}")
        term = mt5mod.terminal_info()
        check(OK if getattr(term, "trade_allowed", False) else FAIL, "mt5 terminal autotrading",
              "AutoTrading button must be ON")
    finally:
        mt5mod.shutdown()


def solana() -> None:
    if not have("QB_SOLANA_KEY"):
        check(FAIL, "solana key", "QB_SOLANA_KEY is empty")
        return
    try:
        import base58
        from solders.keypair import Keypair
        kp = Keypair.from_bytes(base58.b58decode(os.environ["QB_SOLANA_KEY"]))
        check(OK, "solana keypair parses", f"address {kp.pubkey()}")
        check(WARN, "solana wallet hygiene", "must be a burner wallet, never the main bag")
    except Exception as exc:
        check(FAIL, "solana key", f"cannot parse QB_SOLANA_KEY: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues", default="binance,mt5,solana")
    a = ap.parse_args()
    envfile.load()

    check(OK, "engine present", str(ROOT.name))
    live_any = any((os.getenv(f"QB_{v.upper()}_KEY") or "").strip() for v in a.venues.split(","))
    check(OK if live_any else FAIL, "at least one venue configured",
          "live mode cannot start without credentials")

    for v in [x.strip().lower() for x in a.venues.split(",") if x.strip()]:
        print()
        {"binance": binance, "mt5": mt5, "solana": solana}.get(v, lambda: check(WARN, v, "unknown venue"))()

    fails = sum(1 for s, _, _ in rows if s == FAIL)
    warns = sum(1 for s, _, _ in rows if s == WARN)
    print()
    if fails:
        print(f"BLOCKED: {fails} blocking issue(s), {warns} warning(s). Live mode must not start.")
        return 2
    if warns:
        print(f"WARNINGS: {warns}. Review each before going live.")
        return 1
    print("ALL GREEN: live mode prerequisites look satisfied. Still test on testnet first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
