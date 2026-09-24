<#
.SYNOPSIS
  First Light channel notices, twice a day: the general disclaimer notice + the private-assistant post (mechanism/alerts/send_channel_notices.py).

.DESCRIPTION
  Safe by default: with no -Send it only PREVIEWS (prints both posts, sends nothing). Schedule two runs a day, for example:
    slot 1 at 12:00  ->  .\run_first_light_notice.ps1 -Slot 1 -Send
    slot 2 at 20:00  ->  .\run_first_light_notice.ps1 -Slot 2 -Send
  A slot is sent once per day and target (a second run of the same slot is skipped). Both notices are silent. They do not depend on a US session, so
  they run every day, weekends included.

.NOTES
  PRODUCTION IS LOCKED: -Send -To prod is refused (exit 4) until PROD_SENDING_ENABLED=1 is set in .env - the launch switch.

.PARAMETER Slot   1 (midday) or 2 (evening).
.PARAMETER Send   Really post to Telegram (without it nothing is sent).
.PARAMETER To     dev (default) or prod.
.PARAMETER Force  Send even if this slot already went out today.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet(1, 2)][int]$Slot,
    [switch]$Send,
    [ValidateSet('dev', 'prod')][string]$To = 'dev',
    [switch]$Force
)

Set-Location -LiteralPath $PSScriptRoot
$py = if ($env:FIRST_LIGHT_PYTHON) { $env:FIRST_LIGHT_PYTHON } else { 'python' }
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('first_light_notice_{0:yyyyMMdd}.log' -f (Get-Date))

function Log([string]$m) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $m
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

Log ("First Light notices | slot={0} target={1} send={2} force={3}" -f $Slot, $To, [bool]$Send, [bool]$Force)

# launch lock: posting to the PUBLIC channel is off until PROD_SENDING_ENABLED=1 is set in .env
if ($Send -and $To -eq 'prod') {
    & $py -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); raise SystemExit(0 if os.getenv('PROD_SENDING_ENABLED','').strip()=='1' else 4)"
    if ($LASTEXITCODE -ne 0) {
        Log 'BLOCKED: production sending is switched OFF (PROD_SENDING_ENABLED is not 1 in .env). Nothing was sent.'
        exit 4
    }
}

$pyArgs = @('mechanism/alerts/send_channel_notices.py', '--slot', "$Slot", '--to', $To)
if ($Send) { $pyArgs += '--send' }
if ($Force) { $pyArgs += '--force' }
& $py @pyArgs | Out-Host
$rc = $LASTEXITCODE
Log "END: exit code $rc"
exit $rc
