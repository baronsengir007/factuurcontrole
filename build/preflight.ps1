# Preflight for PLAN-factuurcontrole-done.md. Prints one line per prerequisite. Never prints secret values.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$fail = 0
function Report($ok, $name, $detail) {
    if ($ok) { Write-Output "ok       $name" } else { Write-Output "FAIL     $name - $detail"; $script:fail++ }
}
# P1 Docker
docker info *> $null
Report ($LASTEXITCODE -eq 0) 'P1 Docker Desktop running' 'start Docker Desktop'
# P2 Claude token in .env
$envFile = Join-Path $root '.env'
$token = $null
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*CLAUDE_CODE_OAUTH_TOKEN\s*=\s*(.+?)\s*$') { $token = $Matches[1] }
    }
}
if (-not $token) {
    Report $false 'P2 CLAUDE_CODE_OAUTH_TOKEN in .env' 'add CLAUDE_CODE_OAUTH_TOKEN=... to .env'
} else {
    $tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("pf-" + [guid]::NewGuid()))
    Push-Location $tmp
    $env:CLAUDE_CODE_OAUTH_TOKEN = $token
    Remove-Item Env:CLAUDECODE -ErrorAction SilentlyContinue
    Remove-Item Env:CLAUDE_CODE_ENTRYPOINT -ErrorAction SilentlyContinue
    $out = claude -p "Reply with the single word ok." --output-format json --no-session-persistence 2>$null
    Pop-Location
    Remove-Item $tmp -Recurse -Force
    $ok = $false
    try { $j = $out | ConvertFrom-Json; $ok = (-not $j.is_error) -and ($j.result -match 'ok') } catch {}
    Report $ok 'P2 Claude token works' 'the claude CLI rejected the token or returned no ok'
}
# P3 tools
foreach ($t in @('uv --version', 'python --version', 'node --version', 'git --version')) {
    $exe, $arg = $t.Split(' ')
    & $exe $arg *> $null
    Report ($LASTEXITCODE -eq 0) "P3 $exe" "$exe not found"
}
# P4 Codex (warning only)
$c = cmd /c "codex exec --skip-git-repo-check -s read-only ""Reply with the single word OK."" < NUL 2>&1"
if (($c -join "`n") -match '(?m)^OK\s*$') { Write-Output 'ok       P4 Codex reachable' } else { Write-Output 'WARN     P4 Codex not reachable now (usage limit until 17:26?); the final review waits' }
if ($fail -gt 0) { Write-Output "preflight: $fail failed"; exit 1 } else { Write-Output 'preflight: all required ok'; exit 0 }
