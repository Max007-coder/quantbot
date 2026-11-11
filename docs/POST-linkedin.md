# LinkedIn post draft

Paste the block below into LinkedIn. Everything in it was checked on this
machine; nothing claims live trading or a profit.

---

I built a trading bot that refuses to pretend.

Quantbot runs two books side by side: a paper book with virtual money and a live
book that stays switched off until you ask for it. The green panel is fake money,
the red panel is real, and they share one journal.

What actually took the work:

**Surviving restarts.** Positions, cash, cooldowns and the chosen platform are
written to disk on every fill and restored on boot. Restarting used to reset the
paper book to a clean $10,000, which makes any long-run result meaningless.

**Sizing you can check by hand.** Risk comes from the stop distance, not a
gut feeling. The last real paper trade: 0.081 BTC at $74,075.79, stop at
$73,583.69, exit at the stop for -$39.87 - 0.40% of the book, plus $5.98 of fees.
Every number reconciles.

**Not trading when it cannot.** A bug I found while recording a demo: with only
MT5 enabled, the venue lookup still returned Binance and the engine skipped every
pair forever. Healthy-looking, silently doing nothing. It now falls back to an
enabled venue and logs the skip.

**Running without a terminal.** On Windows it launches windowless and starts at
login from a Startup entry, guarded against ever running twice; two engines on one
paper book would double-trade it. On Linux it installs from a pinned requirements
file and runs headless.

Paper first. Always.

#Python #Trading #QuantitativeFinance #Automation #SideProject

---

## Attach

- a screenshot of the dashboard (PAPER panel, equity curve, recent trades)
- the repo: https://github.com/Max007-coder/quantbot

Code is up here: https://github.com/Max007-coder/quantbot
