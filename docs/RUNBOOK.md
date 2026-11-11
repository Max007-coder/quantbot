# Runbook

## Live instance halted
Cause: daily loss limit tripped. Positions already flattened.
1. Read logs/live.log for the loss reason
2. Check the chart at the halt time
3. Fix the cause, then POST /api/resume?t=TOKEN
4. Or leave halted until the next UTC day - the cap resets at midnight

## MT5 disconnected
1. RDP in, confirm terminal running and logged in
2. Tools > Options > Expert Advisors > Allow algorithmic trading
3. Restart: `nssm restart quantbot-live`
4. Bot positions carry magic 770001 - filter and reconcile manually

## Positions stuck open
1. `POST /api/flatten?t=TOKEN` on the right port
2. If the API is down, close manually in Binance / MT5
3. Reconcile: `sqlite3 data/live.db "select * from ev where kind='open' order by id desc"`

## Binance key revoked
1. Engine logs "invalid api key"
2. Generate new key, same permissions and IP restriction
3. Update .env, `nssm restart quantbot-live`

## Everything is wrong
    nssm stop quantbot-live
    nssm stop quantbot-duo
Flatten in the broker UI. Reconcile the DB before restarting.

## Phone flatten
Keep the token in a password manager. Bookmark:
`https://bot.domain/?t=TOKEN` - tap FLATTEN.