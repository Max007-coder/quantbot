# Security

1. Token mandatory. The engine refuses to bind non-loopback without --token.
2. 48+ char random token, not a word.
3. Binance key: spot trade only, withdrawals OFF, IP-restricted.
4. Phantom: burner wallet, never the main bag.
5. Caddy IP allowlist as a second layer.
6. .env never committed. Verify with `git status`.
7. Separate data/ per host; do not copy live.db mid-run.
8. MT5 auto-trading on, terminal logged in, added to startup.
9. Rotate Binance keys every 90 days.
10. RDP on nonstandard port, no default admin username.

## Exposure checklist

- [ ] `curl -I https://bot.domain` returns 200 and HSTS
- [ ] `https://bot.domain/api/status` without token returns 401
- [ ] port 8080-8082 refuse external connections
- [ ] Binance key cannot withdraw
- [ ] kill drill: PAUSE then FLATTEN returns flat positions