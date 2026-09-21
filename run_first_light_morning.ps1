<#
.SYNOPSIS
  First Light morning routine: refresh prices -> build the daily digest -> (optionally) post it to Telegram.

.DESCRIPTION
  Safe by default: with no switches it only PREVIEWS the digest (prints it, saves the market-card PNG, sends nothing, updates nothing).

  Steps of a real run (-Send):
    0. trading-day gate   skips everything on weekends / NYSE holidays / a session that was already sent (exit 0)
    1. market index updater   ~5 seconds (the index tiles of the market card)
    2. daily price updater    ~20 minutes (all ~3,000 symbols)   <- skipped with -SkipUpdate
    3. send_daily_digest.py   ~1 minute: market card + header + Breakout + Near breakout, then saves the snapshot the bot reads
    4. send_channel_posts.py  ~1 minute: ONE extra silent post (market health / sector rotation / gaps / near highs / education ... by weekday),
                              skipped with -NoExtraPost; on Sunday (no session) the weekly recap instead
    (-UpdateOnly also saves the day's snapshot, so history has no gaps even when nothing is posted)

  The 2.5-hour full pipeline (automation_pipeline.sh) is NOT needed before the digest; run it afterwards.

.NOTES
  PRODUCTION IS LOCKED: -Send -To prod is refused (exit 4) until PROD_SENDING_ENABLED=1 is set in .env - the launch switch.
  Everything is built and tested on the dev group, which is always open.

.EXAMPLE
  .\run_first_light_morning.ps1                          # preview only
  .\run_first_light_morning.ps1 -Send                    # post to the DEV group (updates prices first)
  .\run_first_light_morning.ps1 -Send -To prod           # post to the PRODUCTION channel
  .\run_first_light_morning.ps1 -UpdateOnly -To prod     # only refresh prices (schedule this ~05:00)
  .\run_first_light_morning.ps1 -Send -To prod -SkipUpdate   # only build + post (schedule this at 06:00)

.PARAMETER Send        Really post to Telegram (without it nothing is sent).
.PARAMETER To          dev (default) or prod.
.PARAMETER SkipUpdate  Do not refresh prices (use when the pipeline / -UpdateOnly already ran).
.PARAMETER Update      Refresh prices even in a preview.
.PARAMETER UpdateOnly  Refresh prices and stop (no digest).
.PARAMETER Force       Ignore the trading-day gate (re-send a session, or run on a weekend).
.PARAMETER NoImage     Send the text digest without the market-card picture.
.PARAMETER NoButtons   Send without the three popup buttons + the "Private assistant" link under the header.
#>
[CmdletBinding()]
param(
    [switch]$Send,
    [ValidateSet('dev', 'prod')][string]$To = 'dev',
    [switch]$SkipUpdate,
    [switch]$Update,
    [switch]$UpdateOnly,
    [switch]$Force,
    [switch]$NoImage,
    [switch]$NoButtons,
    [switch]$NoExtraPost
)

Set-Location -LiteralPath $PSScriptRoot
$py = if ($env:FIRST_LIGHT_PYTHON) { $env:FIRST_LIGHT_PYTHON } else { 'python' }
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('first_light_morning_{0:yyyyMMdd}.log' -f (Get-Date))

function Log([string]$m) {
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $m
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}

function Step([string]$name, [string[]]$pyArgs) {
    Log "START $name"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $py @pyArgs | Out-Host                     # Out-Host keeps the script's own output from being mixed into the return value
    $rc = $LASTEXITCODE
    Log ('END   {0}: exit code {1} after {2:n0}s' -f $name, $rc, $sw.Elapsed.TotalSeconds)
    return $rc
}

$key = "digest:$To"
$realRun = $Send -or $UpdateOnly
$doUpdate = ($realRun -or $Update) -and -not $SkipUpdate
Log ("First Light morning routine | target={0} send={1} update={2} force={3}" -f $To, [bool]$Send, $doUpdate, [bool]$Force)

# launch lock: posting to the PUBLIC channel is off until PROD_SENDING_ENABLED=1 is set in .env. Refuse before doing any slow work.
if ($Send -and $To -eq 'prod') {
    & $py -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); raise SystemExit(0 if os.getenv('PROD_SENDING_ENABLED','').strip()=='1' else 4)"
    if ($LASTEXITCODE -ne 0) {
        Log 'BLOCKED: production sending is switched OFF (PROD_SENDING_ENABLED is not 1 in .env). Nothing was updated or sent.'
        exit 4
    }
}

# 0. trading-day gate: on a weekend / holiday / already-sent session do NOTHING (and do not burn 20 minutes of price fetching)
if ($realRun -and -not $Force) {
    & $py mechanism/shared/market_calendar.py gate --key $key | Out-Host
    $gate = $LASTEXITCODE
    if ($gate -eq 3) {
        Log "SKIPPED: no new US session to send for '$key' (weekend, holiday, or already sent). Use -Force to run anyway."
        # Sunday has no session, but it is the day of the weekly recap (one extra post, sent by send_channel_posts.py, which has its own gate).
        if ($Send -and -not $NoExtraPost -and (Get-Date).DayOfWeek -eq 'Sunday') {
            $rc = Step 'weekly recap post' @('mechanism/alerts/send_channel_posts.py', '--send', '--to', $To)
            if ($rc -ne 0) { Log "WARNING: the weekly recap post exited with code $rc" }
        }
        exit 0
    }
    if ($gate -ne 0) { Log "WARNING: the trading-day gate itself failed (exit $gate) - continuing (fails open)" }
}

if ($doUpdate) {
    $rc = Step 'market index updater' @('mechanism/data_updaters/market_index_updater.py')
    if ($rc -ne 0) { Log 'WARNING: index update failed - the market card will show n/a for the index tiles; continuing' }
    $rc = Step 'daily price updater' @('mechanism/data_updaters/daily_data_updater.py')
    if ($rc -ne 0) { Log 'ABORT: the price update failed, so nothing is sent (the digest would describe old data).'; exit $rc }
}

if ($UpdateOnly) {
    # Save the day's snapshot (what the bot reads and the scoreboard measures) even when nothing is posted, so history has no gaps.
    $rc = Step 'daily snapshot' @('mechanism/alerts/send_daily_digest.py', '--snapshot-only')
    if ($rc -ne 0) { Log "WARNING: the snapshot step exited with code $rc (the bot keeps showing the previous session)" }
    Log 'Update-only run finished.'
    exit 0
}

$digestArgs = @('mechanism/alerts/send_daily_digest.py')
if (-not $NoImage) { $digestArgs += '--image' }
if ($Send -and -not $NoButtons) { $digestArgs += '--buttons' }
if ($Send) { $digestArgs += @('--send', '--to', $To) }
if ($Force) { $digestArgs += '--force' }
$rc = Step 'daily digest' $digestArgs
if ($rc -ne 0) { Log "FAILED: the digest step exited with code $rc" }

# One extra post per session after the digest (market health, sector rotation, gaps, ... - see CHANNEL_CONTENT_MILESTONES.md). A preview also
# previews it. A failure here never changes the digest's exit code: the digest is the product, the extra post is a bonus.
if ($rc -eq 0 -and -not $NoExtraPost) {
    $extraArgs = @('mechanism/alerts/send_channel_posts.py')
    if ($Send) { $extraArgs += @('--send', '--to', $To) }
    if ($Force) { $extraArgs += '--force' }
    $rc2 = Step 'extra channel post' $extraArgs
    if ($rc2 -ne 0) { Log "WARNING: the extra channel post exited with code $rc2 (the digest itself finished)" }
}
exit $rc
