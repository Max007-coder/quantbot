# Deploy online

MT5 needs Windows. Choose accordingly.

## Windows VPS (full stack)

1. Contabo / Hetzner / Vultr Windows Server 2022, 2 vCPU 4 GB, region near broker
2. RDP on nonstandard port, strong password, firewall on
3. Install Python, Git, MT5
4. `git clone`, venv, `pip install -r requirements.txt`, mkdir data logs
5. `copy .env.example .env`, fill it, generate token:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
6. `Set-ExecutionPolicy -Scope Process Bypass`
7. `.\deploy\install_service.ps1`
8. `.\deploy\firewall.ps1`
9. Caddy to C:\Caddy\, DNS A record, Caddyfile, run once to issue cert, install as service
10. https://bot.yourdomain.com/?t=TOKEN

Ports 8080-8082 stay closed; Caddy proxies over loopback.

## Linux VPS (no forex)

    sudo apt install -y python3.11-venv git caddy
    git clone ... /opt/quantbot && cd /opt/quantbot
    python3.11 -m venv .venv && .venv/bin/pip install -r requirements-linux.txt
    mkdir -p data logs && cp .env.example .env && nano .env
    sudo cp deploy/*.service /etc/systemd/system/
    sudo systemctl enable --now quantbot-paper quantbot-live quantbot-advisor

Caddy: reverse_proxy 127.0.0.1:8081 for paper, 8082 for live.

## Docker (Linux, no forex)

    cd deploy && docker compose up -d

## Costs

Windows 2 vCPU 4 GB: $10-20/mo. Linux: about half.
A sleeping laptop costs more than a VPS.