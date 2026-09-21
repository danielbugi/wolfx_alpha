<#
.SYNOPSIS
  Replay the whole First Light experience into the DEV channel, in the order a subscriber would meet it. Never touches production.

.DESCRIPTION
  1. the pinned "Start here" post
  2. the daily digest (market card, header with buttons, Breakout, Near breakout) - rebuilt from the database
  3. the promotion post for the assistant (image + caption)
  That is all a CHANNEL carries: data, promotion, news and information. The private assistant's screens are NEVER posted to a channel.
  Everything goes to TELEGRAM_DEV_CHAT_ID. Production is locked (PROD_SENDING_ENABLED) and this script never passes --to prod.
  With -Tour the assistant's screens (labelled [QA n/N]) are sent to YOUR PRIVATE CHAT WITH THE BOT - not to the channel.

.PARAMETER Tour       Also send the assistant tour to your private chat with the bot.
.PARAMETER SkipDigest Do not rebuild/send the digest (about 1 minute).

.EXAMPLE
  .\replay_dev_channel.ps1
#>
[CmdletBinding()]
param([switch]$Tour, [switch]$SkipDigest)

Set-Location -LiteralPath $PSScriptRoot
$py = if ($env:FIRST_LIGHT_PYTHON) { $env:FIRST_LIGHT_PYTHON } else { 'python' }

function Step([string]$label, [scriptblock]$run) {
    Write-Host ("`n=== {0} ===" -f $label)
    & $run
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: $label (exit $LASTEXITCODE)"; exit $LASTEXITCODE }
}

Step '1/3 Start here (pinned)' { & $py mechanism/alerts/channel_posts.py --start-here --send --to dev | Out-Host }
if (-not $SkipDigest) {
    Step '2/3 daily digest' { & .\run_first_light_morning.ps1 -Send -To dev -SkipUpdate -Force | Out-Host }
}
Step '3/3 promotion post' { & $py mechanism/alerts/channel_posts.py --promo --send --to dev | Out-Host }
if ($Tour) {
    Step 'assistant tour -> your PRIVATE chat with the bot' { & $py mechanism/alerts/qa_live.py --send-to-owner | Out-Host }
}
Write-Host "`nDone. The channel got: Start here, the digest, the promotion. Nothing else."
