# Backup everything that matters into backups\outreach-<date>.zip  (run from anywhere).
#   openwa\data          WhatsApp login session, OpenWA database, admin API key
#   orchestrator\state   leads, conversations, drafts, dashboard settings
#   data                 company knowledge files
#   orchestrator\.env    SECRETS (DeepSeek key, passwords) - keep the archive private!
# The stack is stopped for a few seconds so SQLite files are copied in a consistent state.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$docker = if (Get-Command docker -ErrorAction SilentlyContinue) { 'docker' } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
New-Item -ItemType Directory -Force backups | Out-Null
$out = "backups\outreach-$(Get-Date -Format 'yyyyMMdd-HHmmss').zip"
& $docker compose stop
try {
  Compress-Archive -Path 'openwa\data', 'orchestrator\state', 'data', 'orchestrator\.env' -DestinationPath $out -CompressionLevel Optimal
} finally {
  & $docker compose start
}
Write-Host "Backup written: $out"
