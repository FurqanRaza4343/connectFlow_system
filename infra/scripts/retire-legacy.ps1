# Archive (encrypted via openssl) then STOP the legacy stack. Never removes containers or data.
# Usage: powershell infra\scripts\retire-legacy.ps1 [-DryRun]
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$ts = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$out = "backups/legacy-$ts.tar.gz.enc"
if (-not (Test-Path legacy)) { Write-Host 'no legacy/ folder - nothing to retire'; exit 0 }
Write-Host "Will archive: legacy/ -> $out"
Write-Host 'Will run:     docker compose -f infra/docker-compose.yml --profile legacy stop'
if ($DryRun) { Write-Host '[dry-run] nothing done.'; exit 0 }
if ((Read-Host 'Type RETIRE to continue') -ne 'RETIRE') { Write-Host 'aborted'; exit 1 }
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) { throw 'openssl required (Git for Windows ships one)' }
New-Item -ItemType Directory -Force backups | Out-Null
tar -czf legacy.tmp.tar.gz legacy
try {
  openssl enc -aes-256-cbc -pbkdf2 -salt -in legacy.tmp.tar.gz -out $out
  Write-Host 'verifying archive decrypts (enter the same passphrase)...'
  openssl enc -d -aes-256-cbc -pbkdf2 -in $out -out legacy.verify.tmp
  tar -tzf legacy.verify.tmp | Out-Null
} finally {
  Remove-Item -ErrorAction SilentlyContinue legacy.tmp.tar.gz, legacy.verify.tmp
}
Write-Host "archive OK: $out"
docker compose -f infra/docker-compose.yml --profile legacy stop
Write-Host 'legacy stopped (not removed). Roll back: docker compose -f infra/docker-compose.yml --profile legacy start'
