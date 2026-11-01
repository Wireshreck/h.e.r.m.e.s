<#
.SYNOPSIS
  One-shot installer for the H.E.R.M.E.S fleet + Command Palette integration.

.DESCRIPTION
  Sets up, idempotently, on a fresh Windows machine:
    1. Pre-requisites via winget when missing (Node.js LTS, PowerToys)
    2. The Hermes Agent CLI  (official installer: irm https://hermes-agent.nousresearch.com/install.ps1)
    3. REA MCP server        (npm i -g rea-agents, used by security/reverse-engineer bots)
    4. Free-provider keys    (OPENROUTER_API_KEY / GOOGLE_API_KEY prompted, written to hermes .env)
    5. The fleet repository  (git clone https://github.com/Wireshreck/h.e.r.m.e.s)
    6. The 13-bot roster     (python scripts/bootstrap_fleet.py --with-board)
    7. The gateway           (dispatches cards every 60s; heroes run as background workers)
    8. The Command Palette extension (signed .msix + trust certificate from this release)

  Usage (PowerShell):
    Set-ExecutionPolicy Bypass -Scope Process -Force
    irm https://github.com/Wireshreck/h.e.r.m.e.s/releases/latest/download/install-hermes-fleet.ps1 -OutFile install-hermes-fleet.ps1
    .\install-hermes-fleet.ps1

  Or fully self-driving:  .\install-hermes-fleet.ps1 -ApiKey OPENROUTER_API_KEY=sk-or-... -ApiKey GOOGLE_API_KEY=AIza...

.NOTES
  Safe to re-run: every step detects existing state and skips. No admin shell is
  required overall; one UAC prompt is used only to trust the extension's signing
  certificate (LocalMachine\TrustedPeople).
#>
[CmdletBinding()]
param(
    # Target directory for the fleet repository.
    [string] $RepoDir = "$env:USERPROFILE\h.e.r.m.e.s",

    # GitHub release asset base; 'latest' resolves the newest tag automatically.
    [string] $ReleaseBase = "https://github.com/Wireshreck/h.e.r.m.e.s/releases/latest/download",

    # Keyboard provider keys as FOO=BAR pairs (repeatable). Prompts for missing ones otherwise.
    [string[]] $ApiKey = @(),

    # Reinstall/repair the Command Palette extension even if it looks installed.
    [switch] $ForcePalette,

    # Pretend keys are already configured; skip prompting entirely.
    [switch] $SkipKeys,

    # Do not touch the Command Palette extension.
    [switch] $SkipPalette
)

$ErrorActionPreference = 'Stop'
[console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Banner($m)  { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Step($m)    { Write-Host "  - $m" }
function Good($m)    { Write-Host "  + $m" -ForegroundColor Green }
function Warn($m)    { Write-Host "  ! $m" -ForegroundColor Yellow }

Banner 'H.E.R.M.E.S fleet installer'

# ----------------------------------------------------------------------------- 1. winget
Banner 'Pre-requisites (winget present? node? PowerToys?)'
$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Warn 'winget not found (needed to install Node.js LTS and PowerToys).'
    Warn 'Install "App Installer" from the Microsoft Store, then re-run.'
}
$wingetArgs = @('--silent', '--accept-package-agreements', '--accept-source-agreements')

# Node.js LTS + global REA MCP server (used by the security / reverse-engineer bots).
if (-not (Get-Command node -ErrorAction SilentlyContinue) -and $winget) {
    Step 'Installing Node.js LTS (needed by the REA MCP server)...'
    & winget install --id OpenJS.NodeJS.LTS -e @wingetArgs
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
if (Get-Command npm -ErrorAction SilentlyContinue) {
    $reaMjs = "$env:APPDATA\npm\node_modules\rea-agents\scripts\rea.mjs"
    if (-not (Test-Path $reaMjs)) {
        Step 'Installing REA MCP server globally (npm i -g rea-agents)...'
        & npm install -g rea-agents --silent
    }
    if (Test-Path $reaMjs) { Good 'REA MCP server available.' }
} else {
    Warn 'npm not available - REA MCP will be skipped (only affects security/reverse-engineer bots).'
}

# PowerToys (the Command Palette host, Win+Alt+Space).
$pt = @(Get-AppxPackage -Name '*PowerToys*' -ErrorAction SilentlyContinue)
if ($pt.Count -eq 0 -and $winget) {
    Step 'Installing PowerToys (Command Palette host)...'
    & winget install --id Microsoft.PowerToys -e @wingetArgs
}
Good 'PowerToys / prerequisites section done.'

# ----------------------------------------------------------------------------- 2. hermes CLI
Banner 'Hermes Agent CLI'
$hermes = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermes) {
    Step 'Installing Hermes Agent (official installer)...'
    iex (irm 'https://hermes-agent.nousresearch.com/install.ps1')
    $hermes = Get-Command hermes -ErrorAction SilentlyContinue
}
if (-not $hermes) { throw 'hermes CLI still missing after install - aborting.' }
Good "hermes CLI: $($hermes.Source)"

# Hermes home layout + managed python (hermes ships its own; system python is preferred for the roster script).
$hermesHome   = "$env:LOCALAPPDATA\hermes"
$managedPy    = Get-ChildItem "$hermesHome\tools" -Filter 'python.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
$systemPy     = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
$py = $systemPy
if (-not $py) { $py = $managedPy }
if (-not $py) { throw 'No Python available (system or hermes-managed) - aborting.' }
Step "roster script will run with: $py"

# ----------------------------------------------------------------------------- 3. keys
Banner 'Free-provider keys (OpenRouter + Google AI Studio)'
$envFile = "$hermesHome\.env"
New-Item -ItemType Directory -Force -Path $hermesHome | Out-Null
$envLines = @()
if (Test-Path $envFile) { $envLines = @(Get-Content $envFile -ErrorAction SilentlyContinue) }

function Get-EnvVal($name) {
    $k = $envLines | Where-Object { $_ -match ("^\s*"+$name+"\s*=") } | Select-Object -First 1
    if ($k) { return ($k -split '=', 2)[1].Trim() }
    # fall back to machine/user environment
    $v = [Environment]::GetEnvironmentVariable($name, 'User'); if ($v) { return $v }
    return [Environment]::GetEnvironmentVariable($name, 'Machine')
}

# merge explicitly -ApiKey O=V arguments first
foreach ($pair in $ApiKey) {
    if ($pair -notmatch '^\s*([A-Z0-9_]+)\s*=(.*)$') { Warn "ignore bad -ApiKey '$pair'"; continue }
    $name, $val = $Matches[1], $Matches[2].Trim()
    if ($val) { [Environment]::SetEnvironmentVariable($name, $val, 'User') }
}

if ($SkipKeys) {
    Warn '-SkipKeys: not prompting for keys.'
} else {
    foreach ($keyName in @('OPENROUTER_API_KEY', 'GOOGLE_API_KEY')) {
        if (-not (Get-EnvVal $keyName)) {
            Write-Host "  Enter $keyName (Enter to skip):" -NoNewline
            $val = Read-Host ' '
            if ($val) { [Environment]::SetEnvironmentVariable($keyName, $val, 'User') }
        }
    }
}
# write both keys into the hermes .env (profiles copy them at roster build time)
$updated = 0
foreach ($keyName in @('OPENROUTER_API_KEY', 'GOOGLE_API_KEY')) {
    $val = Get-EnvVal $keyName
    if (-not $val) { Warn "$keyName not set - that provider will be unavailable (install keys later or re-run)."; continue }
    if ($envLines -notmatch ("^\s*"+$keyName+"\s*=")) {
        $envLines += "$keyName=$val"
        $updated++
    }
}
if ($updated -gt 0) { Set-Content -Path $envFile -Value $envLines; Good "$updated key(s) written to $envFile" }
else { Good 'hermes .env already carries the configured keys.' }

# ----------------------------------------------------------------------------- 4. repo
Banner 'Fleet repository'
if (Test-Path "$RepoDir\.git") {
    Step "updating existing repo at $RepoDir"
    & git -C $RepoDir pull --ff-only 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Warn 'git pull failed (offline?)- using existing checkout.' }
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    Step "cloning https://github.com/Wireshreck/h.e.r.m.e.s into $RepoDir"
    & git clone --depth 1 'https://github.com/Wireshreck/h.e.r.m.e.s.git' $RepoDir
} else {
    Step 'git missing - downloading the release source zip instead...'
    $zip = Join-Path $env:TEMP 'hermes-fleet-source.zip'
    Invoke-WebRequest -Uri 'https://codeload.github.com/Wireshreck/h.e.r.m.e.s/zip/refs/heads/main' -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
    $extracted = Get-ChildItem $env:TEMP -Directory -Filter 'h.e.r.m.e.s-*' | Sort-Object Name -Descending | Select-Object -First 1
    Move-Item $extracted.FullName $RepoDir -Force
    Good "source extracted to $RepoDir"
}
if (-not (Test-Path "$RepoDir\scripts\bootstrap_fleet.py")) { throw "Fleet scripts not found under $RepoDir" }
Good "fleet repo ready: $RepoDir"

# ----------------------------------------------------------------------------- 5. roster
Banner 'Building the 13-bot roster (python scripts/bootstrap_fleet.py --with-board)'
$pyyamlOk = & $py -c 'import yaml' 2>$null
if ($LASTEXITCODE -ne 0) {
    Step 'installing PyYAML for the roster script...'
    & $py -m pip install --user pyyaml --quiet
}
Push-Location $RepoDir
try {
    & $py 'scripts\bootstrap_fleet.py' --with-board
    if ($LASTEXITCODE -ne 0) { throw 'bootstrap_fleet.py failed - see output above.' }
} finally { Pop-Location }
Good 'Fleet roster built (the profile ``hermes`` is reserved: orchestrator id is hermes-orchestrator).'

# ----------------------------------------------------------------------------- 6. gateway
Banner 'Gateway (card dispatcher, ticks every 60s)'
& hermes gateway status 2>$null
if ($LASTEXITCODE -ne 0) { Step 'starting gateway...'; & hermes gateway start 2>$null | Out-Null }
& hermes kanban init 2>$null | Out-Null
Good 'Gateway + kanban board ready. Cards in triage get fan-out automatically.'

# ----------------------------------------------------------------------------- 7. Command Palette extension
if (-not $SkipPalette) {
    Banner 'Command Palette extension (Win+Alt+Space -> type any task)'
    $pkg = Get-AppxPackage -Name 'HermesCmdPalExtension' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg -and -not $ForcePalette) {
        Good "already installed: $($pkg.PackageFullName)"
    } else {
        $tmp = New-Item -ItemType Directory -Force -Path "$env:TEMP\hermes-cmdpal" | ForEach-Object { $_.FullName }
        Write-Host "  - downloading signed extension + certificate from the release..."
        $msix = Join-Path $tmp 'HermesCmdPalExtension.msix'
        $cer  = Join-Path $tmp 'HermesCmdPalExtension.cer'
        Invoke-WebRequest -Uri "$ReleaseBase/HermesCmdPalExtension.msix" -OutFile $msix
        Invoke-WebRequest -Uri "$ReleaseBase/HermesCmdPalExtension.cer"  -OutFile $cer

        if ($pkg) {
            Write-Host "  - closing the running extension/palette for a clean update..."
            Get-Process Microsoft.CmdPal.UI, HermesCmdPalExtension -ErrorAction SilentlyContinue | Stop-Process -Force
        }

        Write-Host "  - trusting the signing certificate (accept the UAC prompt)..."
        $psCmd = "`$c = Import-Certificate -FilePath '$cer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople; if (-not `$c) { exit 1 }"
        try {
            $ea = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $psCmd
        } catch {
            $ea = $null
        }
        if (-not $ea -or $ea.ExitCode -ne 0) {
            Warn 'certificate trust was refused - palette extension step skipped (re-run to retry).'
        } else {
            Step 'registering the extension...'
            Add-AppxPackage -Path $msix -ForceApplicationShutdown
            $pkg = Get-AppxPackage -Name 'HermesCmdPalExtension' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($pkg -and $pkg.Status -eq 'Ok') { Good "palette extension installed: $($pkg.PackageFullName)" }
            else { Warn 'extension installed with errors - use  Reload  inside the palette, or re-run with -ForcePalette.' }
        }
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        $aumid = (Get-StartApps | Where-Object Name -match 'Command Palette' | Select-Object -First 1).AppID
        if ($aumid -and -not (Get-Process Microsoft.CmdPal.UI -ErrorAction SilentlyContinue)) {
            Step 'opening the Command Palette once so it picks up the extension...'
            Start-Process "shell:AppsFolder\$aumid" | Out-Null
        }
    }
}

# ----------------------------------------------------------------------------- 8. final report
Banner 'Verify'
& hermes profile list 2>$null | Select-Object -First 18 | ForEach-Object { Write-Host "  $_" }
& hermes kanban stats 2>$null | Select-Object -First 8  | ForEach-Object { Write-Host "  $_" }

Write-Host ''
Write-Host 'HERMES is ready.' -ForegroundColor Green
Write-Host '  - Command Palette: press Win+Alt+Space, type any task, Enter on  "Give task to HERMES: ..."'
Write-Host '  - Terminal:        hermes -p hermes-orchestrator chat'
Write-Host '  - Board:           hermes kanban watch    |    http://localhost:9119'
Write-Host '  - Add keys later:  put OPENROUTER_API_KEY / GOOGLE_API_KEY in %LOCALAPPDATA%\hermes\.env, then run scripts/bootstrap_fleet.py again'
