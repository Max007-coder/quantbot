# Setup

## 0. Install

1. Python 3.11+ from python.org, tick "Add to PATH"
2. Terminal in project root
3. `python -m venv .venv`
4. `.venv\Scripts\activate`
5. `pip install -r requirements.txt`
6. `copy .env.example .env` - every script reads `.env` at startup;
   values already set in the shell still win
7. `python quantbot.py --once` - JSON output means success

## 1. Gmail

1. Google Account > Security > 2-Step Verification ON
2. Security > App passwords > Mail / Windows > generate
3. Put the 16 chars in QB_GMAIL_APP_PASSWORD
4. Test `python advisor.py --once`

Normal password will not work.

## 2. Paper

`run\run_paper.bat` then http://127.0.0.1:8080
No keys needed. Virtual fills, live prices.

## 3. Binance testnet

1. https://testnet.binance.vision > Generate HMAC Key
2. Set QB_BINANCE_KEY / QB_BINANCE_SECRET, QB_BINANCE_TESTNET=true
3. `python quantbot.py --mode live --venues binance --label TEST --db data/test.db --port 8083`
4. Confirm the order shows in the testnet UI

## 4. Binance live

1. API Management > Create API
2. Spot Trading ON, Withdrawals OFF
3. IP-restrict to your VPS
4. QB_BINANCE_TESTNET=false, new keys
5. Start at $200-500, --risk 0.25

## 5. MetaTrader 5

1. Install MT5, open a demo account (server ends in -Demo)
2. Tools > Options > Expert Advisors > Allow algorithmic trading
3. Log in and leave the terminal running
4. Set QB_MT5_LOGIN / PASSWORD / SERVER / PATH
5. `python quantbot.py --mode live --venues mt5 --symbols EURUSD --db data/mt5.db --port 8084`

Bot orders carry magic 770001.

Note: two processes sharing one MT5 terminal can be flaky. Paper on Binance, live on MT5.

## 6. Phantom

1. Phantom > Settings > Security > Export Private Key
2. Use a burner wallet, fund with USDC
3. Set QB_SOLANA_KEY
4. `--venues solana,binance --symbols SOL/USDT`

## 7. Both sides

`run\run_duo.bat` - paper left, live right.

## 8. Verify

- logs/live.log
- `sqlite3 data/live.db "select * from ev order by id desc limit 20"`
- Binance order history
- Screenshot dashboard beside exchange balance