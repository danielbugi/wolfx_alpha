<#
.SYNOPSIS
  The channel's pre-market "who reports today" post (mechanism/alerts/send_earnings_today.py).

.DESCRIPTION
  Safe by default: with no -Send it only PREVIEWS (prints the post, sends nothing). Scheduled once a day at
  11:00 Israel time -- a few hours before the US pre-market, not right before it (confirmed with the owner,
  DATA_ML_MILESTONES.md M3). Skips entirely on a day the US market is closed (weekend/holiday) -- nobody
  reports, nobody trades, not relevant to post; that gate lives in the Python script (market_calendar.is_trading_day),
  this wrapper just runs it and logs the result.

.NOTES
  PRODUCTION IS LOCKED: -Send -To prod is refused (exit 4) until PROD_SENDING_ENABLED=1 is set in .env.

.PARAMETER Send    Really post to Telegram (without it nothing is sent).
.PARAMETER To      dev (default) or prod.
.PARAMETER Force   Send even if already sent today, or on a non-trading day.
#>
[CmdletBinding()]
param(
    [switch]$Send,
    [ValidateSet('dev', 'prod')][string]$To = 'dev',
    [switch]$Force
)

Set-Location -LiteralPath $PSScriptRoot
$py = if ($env:FIRST_LIGHT_PYTHON) { $env:FIRST_LIGHT_PYTHON } else { 'python' }
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('earnings_today_{0:yyyyMMdd}.log' -f (Get-Date))

function Log([string]$m) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $m
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

Log ("Earnings-today post | target={0} send={1} force={2}" -f $To, [bool]$Send, [bool]$Force)

# launch lock: posting to the PUBLIC channel is off until PROD_SENDING_ENABLED=1 is set in .env
if ($Send -and $To -eq 'prod') {
    & $py -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); raise SystemExit(0 if os.getenv('PROD_SENDING_ENABLED','').strip()=='1' else 4)"
    if ($LASTEXITCODE -ne 0) {
        Log 'BLOCKED: production sending is switched OFF (PROD_SENDING_ENABLED is not 1 in .env). Nothing was sent.'
        exit 4
    }
}

$pyArgs = @('mechanism/alerts/send_earnings_today.py', '--to', $To)
if ($Send) { $pyArgs += '--send' }
if ($Force) { $pyArgs += '--force' }
& $py @pyArgs | Out-Host
$rc = $LASTEXITCODE
Log "END: exit code $rc"
exit $rc
