#!/usr/bin/env python3
"""
start.py - the friendly front door: pick mode, platform and pairs, then launch.

    python start.py                      # asks questions, then starts the app
    python start.py --paper --platform binance --symbols BTC/USDT
    python start.py --live  --platform mt5 --equity 500

Platform means where the orders actually go, so it is worth choosing on purpose:
  binance  - spot crypto, long only
  mt5      - MetaTrader 5, long or short (needs the MT5 terminal + login)
  solana   - Jupiter DEX swap, long only
  mixed    - enable several and switch platform per trade from the dashboard
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

PLATFORMS = {
    "binance": "Binance spot      - crypto, long only",
    "mt5": "MetaTrader 5      - long or short, needs the MT5 terminal",
    "solana": "Solana / Jupiter  - DEX swap, long only",
    "mixed": "Binance + MT5     - enable both, pick per trade in the dashboard",
}

VENUES = {"binance": "binance", "mt5": "mt5", "solana": "solana",
          "mixed": "binance,mt5"}

QUOTES = ("USDT", "USDC", "BUSD", "USD")


def normalize_symbols(raw: str) -> str:
    """Accept BTC, btcusdt or BTC-USDT and hand the engine BTC/USDT pairs."""
    out = []
    for part in str(raw).replace(";", ",").replace(" ", ",").split(","):
        s = part.strip().upper().replace("-", "/").replace("_", "/")
        if not s:
            continue
        if "/" not in s:
            for q in QUOTES:
                if s.endswith(q) and len(s) > len(q):
                    s = s[:-len(q)] + "/" + q
                    break
            else:
                s += "/USDT"
        out.append(s)
    return ",".join(dict.fromkeys(out)) or "BTC/USDT,ETH/USDT"


def ask(prompt: str, default: str) -> str:
    try:
        answer = input(f"{prompt} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def choose_platform() -> str:
    print("\n  Where should the orders go?")
    for i, (key, line) in enumerate(PLATFORMS.items(), 1):
        print(f"    {i}) {line}")
    pick = ask("  platform number or name", "1")
    if pick.isdigit():
        keys = list(PLATFORMS)
        index = int(pick) - 1
        return keys[index] if 0 <= index < len(keys) else "binance"
    return pick.lower() if pick.lower() in PLATFORMS else "binance"


def use_project_venv(argv) -> int | None:
    """Re-run under .venv so ccxt/solders are importable in live mode."""
    venv_py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    if not os.path.exists(venv_py):
        return None
    if os.path.abspath(sys.executable).lower() == os.path.abspath(venv_py).lower():
        return None
    print(f"  switching to the project venv: {venv_py}")
    return subprocess.call([venv_py, os.path.abspath(__file__), *argv])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="launch the quantbot app")
    p.add_argument("--paper", action="store_true", help="virtual money (default)")
    p.add_argument("--live", action="store_true", help="real money - asks for confirmation")
    p.add_argument("--platform", default=None,
                   choices=sorted(PLATFORMS), help="where orders go")
    p.add_argument("--symbols", default=None)
    p.add_argument("--timeframe", default=None)
    p.add_argument("--equity", type=float, default=None)
    p.add_argument("--risk", type=float, default=None)
    p.add_argument("--no-ask", action="store_true", help="never prompt, use defaults")
    a = p.parse_args(argv)

    handed = use_project_venv(sys.argv[1:])
    if handed is not None:
        return handed

    print("\n  QUANTBOT")
    print("  " + "-" * 46)

    interactive = not a.no_ask and sys.stdin.isatty()

    if a.live:
        mode = "live"
    elif a.paper:
        mode = "paper"
    else:
        mode = "paper"
        if interactive:
            ans = ask("  mode: 1) paper (virtual)  2) live (real money)", "1")
            mode = "live" if ans.strip() in ("2", "live", "l") else "paper"

    platform = a.platform or (choose_platform() if interactive else "binance")
    venues = VENUES.get(platform, platform)

    symbols = normalize_symbols(
        a.symbols or (ask("  pairs", "BTC/USDT,ETH/USDT") if interactive else "BTC/USDT,ETH/USDT"))
    timeframe = a.timeframe or (ask("  timeframe (1m/5m/15m/1h/4h/1d)", "5m") if interactive else "5m")

    if mode == "live":
        equity = a.equity if a.equity is not None else (
            float(ask("  live equity ($)", "500")) if interactive else 500.0)
    else:
        equity = a.equity if a.equity is not None else (
            float(ask("  starting virtual equity ($)", "10000")) if interactive else 10_000.0)
    risk = a.risk if a.risk is not None else (
        float(ask("  risk per trade (% of equity)", "0.5")) if interactive else 0.5)

    if mode == "live":
        print("\n  LIVE MODE places real orders with real money.")
        print("  It needs exchange keys in .env and stops at your daily loss limit.")
        answer = ask("  type LIVE to confirm", "")
        if answer.strip().upper() != "LIVE":
            print("  cancelled - nothing was started.\n")
            return 1
        missing = []
        if "binance" in venues and not os.getenv("QB_BINANCE_KEY"):
            missing.append("QB_BINANCE_KEY / QB_BINANCE_SECRET")
        if "mt5" in venues:
            for k in ("QB_MT5_LOGIN", "QB_MT5_PASSWORD", "QB_MT5_SERVER"):
                if not os.getenv(k):
                    missing.append(k)
        if "solana" in venues and not os.getenv("QB_SOLANA_KEY"):
            missing.append("QB_SOLANA_KEY")
        if missing:
            print("  missing credentials in .env: " + ", ".join(sorted(set(missing))))
            print("  the live panel will stay OFFLINE until those are set.\n")

    cmd = [sys.executable, os.path.join(ROOT, "duo.py"),
           "--symbols", symbols, "--timeframe", timeframe, "--risk", str(risk),
           "--paper-venues", venues, "--live-venues", venues,
           "--paper-equity", str(equity), "--live-equity", str(equity if mode == "live" else 500)]
    if mode == "live":
        cmd.append("--live")

    print("\n  starting: " + " ".join(cmd[1:]))
    print("  dashboard: http://127.0.0.1:8080")
    print("  stop with ctrl-c\n")
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
