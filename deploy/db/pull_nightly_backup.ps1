# deploy/db/pull_nightly_backup.ps1
#
# Off-box leg of the nightly backup strategy: pulls the VPS's most recent successful nightly
# dump (written by deploy/db/nightly_backup.sh) down to this admin machine, verifies its checksum
# on both ends, and keeps it in a location this VPS has no credentials to reach -- a compromised or
# lost VPS cannot also destroy its own backups. Read-only on the VPS side (scp only).
#
# Usage: powershell -File pull_nightly_backup.ps1 [-SshKey <path>] [-Host root@IP] [-RetainDays 30]
param(
    [string]$SshKeyPath = "$env:USERPROFILE\.ssh\donchian_deploy",
    [string]$VpsHost = "root@116.203.220.219",
    [string]$OutDir = "$env:USERPROFILE\donchian_backups\nightly",
    [int]$RetainDays = 30
)
$ErrorActionPreference = "Stop"
$logFile = "$env:USERPROFILE\donchian_backups\nightly_pull.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK') $msg"
    Add-Content -Path $logFile -Value $line
    Write-Output $line
}

try {
    $latest = (ssh -i $SshKeyPath -o BatchMode=yes $VpsHost `
        "ls -1 /opt/donchian/backups/nightly/ 2>/dev/null | sort | tail -1").Trim()
    if (-not $latest) { throw "no nightly backup directories found on the VPS" }

    $dest = Join-Path $OutDir $latest
    if (Test-Path "$dest\production.dump.sha256") {
        Write-Log "SKIP $latest already pulled"
        exit 0
    }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    $remote = "/opt/donchian/backups/nightly/$latest"
    & scp -q -i $SshKeyPath -o BatchMode=yes `
        "${VpsHost}:${remote}/production.dump" `
        "${VpsHost}:${remote}/production.dump.sha256" `
        $dest
    if ($LASTEXITCODE -ne 0) { throw "scp failed (exit $LASTEXITCODE)" }

    $expected = (Get-Content "$dest\production.dump.sha256").Split(' ')[0]
    $actual = (Get-FileHash "$dest\production.dump" -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "checksum mismatch: expected $expected got $actual" }

    icacls $dest /reset /Q | Out-Null
    Get-ChildItem $dest | ForEach-Object { icacls $_.FullName /inheritance:e /Q | Out-Null }

    $size = (Get-Item "$dest\production.dump").Length
    Write-Log "OK $latest bytes=$size sha256=$actual"

    Get-ChildItem $OutDir -Directory | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetainDays) } | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force
        Write-Log "retention: removed $($_.Name)"
    }
    exit 0
} catch {
    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
