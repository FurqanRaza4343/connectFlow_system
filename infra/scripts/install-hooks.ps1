# Run ONCE by the repo owner after `git init`: points git at the versioned hooks in .githooks (local repo setting only).
Set-Location (git rev-parse --show-toplevel)
git config core.hooksPath .githooks
Write-Host "git hooks enabled: .githooks/pre-commit will scan every commit for secrets."
