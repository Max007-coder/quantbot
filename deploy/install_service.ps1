$root = Split-Path -Parent $PSScriptRoot
$py   = Join-Path $root ".venv\Scripts\python.exe"
$nssm = "C:\nssm\nssm.exe"
if (-not (Test-Path $nssm)) { throw "download nssm to C:\nssm first" }

$svc = @(
  @{ n="quantbot-paper";   a="quantbot.py --label PAPER --mode paper --db data\paper.db --port 8081" },
  @{ n="quantbot-live";    a="quantbot.py --label LIVE --mode live --db data\live.db --port 8082 --venues binance --equity 500 --risk 0.25 --min-score 0.7" },
  @{ n="quantbot-duo";     a="duo.py --live" },
  @{ n="quantbot-advisor"; a="advisor.py --symbols BTC/USDT,ETH/USDT --every 15" }
)

foreach ($s in $svc) {
  & $nssm install $s.n $py $s.a
  & $nssm set $s.n AppDirectory $root
  & $nssm set $s.n AppStdout (Join-Path $root "logs\$($s.n).out.log")
  & $nssm set $s.n AppStderr (Join-Path $root "logs\$($s.n).err.log")
  & $nssm set $s.n Start SERVICE_AUTO_START
  & $nssm set $s.n AppExit Default Restart
  & $nssm set $s.n AppRestartDelay 10000
  & $nssm start $s.n
}