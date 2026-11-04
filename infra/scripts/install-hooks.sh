#!/bin/sh
# Run ONCE by the repo owner after `git init`: points git at the versioned hooks in .githooks (local setting only).
cd "$(git rev-parse --show-toplevel)" || exit 1
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit 2>/dev/null
echo "git hooks enabled: .githooks/pre-commit will scan every commit for secrets."
