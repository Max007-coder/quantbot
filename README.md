# quantbot

Autonomous multi-venue trading bot with a dual paper/live dashboard.
Venues: Binance spot, MetaTrader 5, Solana (Phantom keypair).

**Where this lives:** `C:\Users\mageb\quantbot` - that folder is the app.

## Quick start (Windows, local)

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt
    copy .env.example .env
    python start.py            # asks mode + platform, then launches

`start.py` is the front door: it asks whether you want paper or live money,
which platform the orders should go to, and which pairs to trade. Everything it
does can also be passed as flags:

    python start.py --paper --platform binance --symbols BTC/USDT,ETH/USDT
    python start.py --live  --platform mt5 --equity 500

Then open http://127.0.0.1:8080 (the token, if set, is `QB_DASH_TOKEN` in `.env`).

## Run it in the background (no console)

Once it is running there is no window to keep open, and it can start itself:

    run\install_autostart.bat     once - starts hidden at every login
    run\start_bot.bat             start now, hidden, and open the dashboard
    run\stop_bot.bat              stop the dashboard and both engines
    run\status_bot.bat            which ports are up, plus the last log lines
    run\uninstall_autostart.bat   remove the login entry

Settings (pairs, timeframe, risk, platforms, `--live`) are one line in
`run\bot.cmd`; change it there and restart. Output goes to `logs\duo.out`, so
nothing is lost without a console, and the paper book plus the platform choice
survive the restart. Live mode still needs the MT5 terminal running in your
session - the bot itself stays windowless.

## Choosing the platform

Platform means where the order is actually sent, and it is chosen two ways:

- **For the run** - `start.py`, or `--venues binance,mt5` on `quantbot.py` and
  `--paper-venues` / `--live-venues` on `duo.py`. Enable several to offer a choice.
- **Per trade** - the PLATFORM dropdown on each dashboard panel. It applies to the
  next entry on that engine, with no restart, and is saved with the paper state so
  it survives a restart. `auto` routes each pair to its natural venue.

| platform | pairs | side |
|---|---|---|
| binance | BTC/USDT, ETH/USDT, ... | long only (spot) |
| mt5 | BTCUSD, ETHUSD, XAUUSD, ... | long or short (CFD) |
| solana | SOL/USDC via Jupiter | long only (DEX) |

Symbols are written the same way everywhere (`BTC/USDT`); the engine translates to
each venue's spelling (`BTCUSD` for MT5, `SOL/USDC` for Jupiter).

## What the dashboard shows

- green PAPER panel (virtual money) and red LIVE panel (real money) side by side
- equity curve per panel, sampled every 30s from the journal
- the last few fills per panel, plus open positions with live P&L
- PLATFORM selector per panel; PAUSE / FLATTEN when not launched with `--lock`

## Paper state survives restarts

Paper positions, cash, day-loss baseline, cooldowns and the platform choice are
written to `data/state-paper.json` on every fill and every few seconds, then
restored on boot. Restarting no longer resets the book to a clean $10,000, so
long-run paper results mean something.

Live state is deliberately **not** reloaded from disk: those positions already
exist on the exchange, and inventing them from a file would make the bot manage
trades it does not own.

Known limit: one process per paper state file. Do not run two copies of the same
paper engine against the same `data/` folder.

## Order safety

Paper fills are simulated. Live fills are checked before they are booked:

- quantity is rounded to the exchange lot size and refused below minimum amount /
  minimum notional (Binance), minimum volume (MT5), $1 swap (Jupiter)
- only the **actually filled** quantity is booked, so a partial fill cannot turn
  into a phantom position
- Solana swaps are confirmed on-chain before the trade is recorded
- a fill more than 2% away from the last price logs a slippage warning
- shorts are refused on venues with no short side (Binance spot, Solana)

## Layout

    start.py         interactive launcher (mode + platform + pairs)
    quantbot.py      engine: brokers, risk, journal, dashboard, backtest
    signals.py       regime detection + strategy library
    notify.py        gmail + toast alerts
    envfile.py       .env loader
    advisor.py       GO-alert watcher
    duo.py           dual dashboard
    run/             windows launchers
    deploy/          services, TLS, firewall, docker
    data/            sqlite + paper state (gitignored)
    logs/            stdout capture (gitignored)
    docs/            setup, deploy, security, runbook

## Docs

- docs/SETUP.md - install and connect each venue
- docs/DEPLOY.md - hosting online
- docs/SECURITY.md - key hygiene
- docs/RUNBOOK.md - incident response

Paper first. Always.
