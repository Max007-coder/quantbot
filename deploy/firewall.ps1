New-NetFirewallRule -DisplayName "Caddy HTTP"  -Direction Inbound -Protocol TCP -LocalPort 80  -Action Allow
New-NetFirewallRule -DisplayName "Caddy HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow
New-NetFirewallRule -DisplayName "RDP"         -Direction Inbound -Protocol TCP -LocalPort 3389 -Action Allow
Set-NetFirewallProfile -Profile Domain,Public,Private -DefaultInboundAction Block