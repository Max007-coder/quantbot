# Going live — the checklist

Chosen settings: **Binance, testnet first · $100 live equity · 0.25% risk per
trade · live engine runs from autostart.**

Nothing below trades real money until step 6.

## 1. What the code actually does

| piece | detail |
|---|---|
| `--mode live` | `build_brokers()` builds a real `BinanceBroker` (ccxt, spot only) |
| sandbox | `QB_BINANCE_TESTNET=true` calls `set_sandbox_mode(True)` — testnet balance, no real money |
| sizing | risk % of equity ÷ stop distance, then clamped by the 60% exposure cap |
| halt | a daily loss over `daily_loss_pct` sets `halted`, flattens everything, and refuses new entries until the next day |
| kill switch | dashboard PAUSE / FLATTEN, or `POST /api/flatten` |
| restart | live positions are deliberately **not** restored from disk — the exchange holds them, and inventing them from a file would make the bot manage trades it does not own |

## 2. Create the keys (you, not me)

Binance → API Management → create key:

- **Enable Spot & Margin Trading** yes
- **Enable Withdrawals** must stay **off**
- **Restrict access to trusted IPs** — add your IP
- copy both values into `C:\Users\mageb\quantbot\.env`:

```
QB_BINANCE_KEY=...
QB_BINANCE_SECRET=...
QB_BINANCE_TESTNET=true
```

Never paste them into a chat window. `.env` is gitignored; verify with `git status`.

## 3. Wait for testnet email verification

Testnet keys come from a separate site (`testnet.binance.vision`), not the main
Binance account. Mainnet keys will **not** work against testnet and vice versa.

## 4. Prove it before money

```
.venv\Scripts\python.exe run\live_preflight.py
```

Read-only. It refuses to continue if the key can withdraw, if the balance is
under $20, or if MT5's terminal is not logged in / AutoTrading is off. Exit code
2 means blocked.

## 5. Watch the first fills on testnet

1. dashboard `:8080`, live panel on the right, PLATFORM binance
2. wait for a signal — check `logs\duo.out` for `FILL` on the live engine
3. confirm the fill price is within 2% of the last price and the quantity matches
   the sizing log line
4. press PAUSE, then FLATTEN, and confirm the position actually closes

## 6. Only then flip to mainnet

```
QB_BINANCE_TESTNET=false
```

**This is the dangerous switch.** From the next login the autostart launches the
live engine with real money, unattended, every time the machine reboots. If you
want live to be manual instead, remove `--live` from `run\bot.cmd`.

## Sizing reality at $100 / 0.25%

Risk per trade is **$0.25**. A BTC setup with a 0.66% stop at $73,000 risks
~$482 per coin, so size ≈ 0.00052 BTC ≈ **$38 notional**, which the 60% cap
($60) does not bind.

The catch is fees: 0.1% taker on both sides of $38 is **$0.076**, about 30% of
the money you are risking. At this size the fee is a real part of the edge, so
expect a strategy that looked fine on paper to look worse live. That is a
conclusion to draw from testnet numbers, not a reason to skip testnet.

## Kill drill (do this once per deployment)

1. PAUSE — new entries stop, open positions stay
2. FLATTEN — every position closes at market
3. confirm `positions: []` and flat equity on the panel
4. if the dashboard is unreachable: `run\stop_bot.bat`, then close positions in
   the exchange UI itself

## Rolling back to paper only

Edit `run\bot.cmd`, delete `--live`, then `run\stop_bot.bat` + `run\start_bot.bat`.
State and history are untouched.
