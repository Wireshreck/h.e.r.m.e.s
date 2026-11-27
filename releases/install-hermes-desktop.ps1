<#
.SYNOPSIS
  H.E.R.M.E.S Desktop installer wizard.

.DESCRIPTION
  Installs the Electron agent-canvas desktop app (and its Python backend) from
  the prebuilt release ZIP. Choices:
    - this user or all users (all-users self-elevates via one UAC prompt)
    - Desktop shortcut
    - Start Menu shortcut

  No GUI is shown with -Silent (used for unattended installs / verification).

  Release base can be overridden with -ReleaseBase for testing.
#>
[CmdletBinding()]
param(
    [ValidateSet('User', 'AllUsers')] [string] $Scope = 'User',
    [switch] $Desktop,
    [switch] $StartMenu,
    [string] $InstallPath,
    [switch] $Silent,
    [string] $LocalZip,
    [string] $ReleaseBase = 'https://github.com/Wireshreck/h.e.r.m.e.s/releases/latest/download',
    [switch] $Launch
)

$ErrorActionPreference = 'Stop'
[console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Log($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m
    Write-Host $line
    try {
        Add-Content -Path "$env:LOCALAPPDATA\hermes\desktop-install.log" -Value $line -ErrorAction SilentlyContinue
    } catch { }
}
function Native {
    # PS 5.1 turns redirected native stderr into a terminating error under Stop.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        if ($args.Count -ge 2) { & $args[0] @($args[1..($args.Count - 1)]) }
        else { & $args[0] }
    } finally { $ErrorActionPreference = $prev }
}
function IsAdmin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function DefaultPath($scope) {
    if ($scope -eq 'AllUsers') { Join-Path $env:ProgramFiles 'H.E.R.M.E.S' }
    else { Join-Path $env:LOCALAPPDATA 'Programs\H.E.R.M.E.S' }
}
function MakeShortcut($lnk, $target, $arguments, $workdir, $icon) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $target
    $s.Arguments = $arguments
    $s.WorkingDirectory = $workdir
    if ($icon) { $s.IconLocation = $icon }
    $s.Save()
}

function Install-Desktop {
    param($scope, $desktopShortcut, $startShortcut, $path, $zipOverride)
    New-Item -ItemType Directory -Force -Path "$env:LOCALAPPDATA\hermes" | Out-Null
    Log "install started: scope=$scope path=$path"

    if ($scope -eq 'AllUsers' -and -not (IsAdmin)) {
        # Re-launch the compiled exe (or this script) elevated with the choices.
        $args2 = @('-Scope', 'AllUsers', '-Silent', '-InstallPath', "`"$path`"")
        if ($desktopShortcut) { $args2 += '-Desktop' }
        if ($startShortcut) { $args2 += '-StartMenu' }
        if ($zipOverride) { $args2 += @('-LocalZip', "`"$zipOverride`"") }
        $cmd = [Environment]::GetCommandLineArgs()[0]
        if ($cmd -and $cmd -match '\.exe$') {
            $p = Start-Process -FilePath $cmd -Verb RunAs -Wait -PassThru -ArgumentList $args2
        } else {
            $p = Start-Process -FilePath 'powershell' -Verb RunAs -Wait -PassThru -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath) + $args2
        }
        Log "elevated installer exit: $($p.ExitCode)"
        return
    }

    # -- Python + PyYAML (the backend needs both) ----------------------------
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Log 'Python missing - installing via winget...'
            Native winget install --id Python.Python.3.14 -e --silent --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            $py = Get-Command python -ErrorAction SilentlyContinue
        }
    }
    if ($py) {
        $yamlOK = $false
        try { $yamlOK = -not (Native $py.Source -c 'import yaml') } catch { }
        # Native() returns no reliable status; probe with a direct call under Continue.
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $py.Source -c 'import yaml' 2>$null | Out-Null
        $yamlOK = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $prev
        if (-not $yamlOK) {
            Log 'PyYAML missing - installing...'
            Native $py.Source -m pip install --user pyyaml --quiet
        }
        Log "python: $($py.Source)"
    } else {
        Log 'WARNING: Python not found. The desktop backend will not start until Python 3 is installed.'
    }

    # -- Hermes runtime check (never claim connected when it isn't) ----------
    if (Get-Command hermes -ErrorAction SilentlyContinue) {
        Log 'Hermes CLI found.'
    } else {
        Log 'WARNING: Hermes CLI missing. Install the fleet first with HERMES-Fleet-Setup.exe (release v0.1.0).'
        Log "         Desktop app: $ReleaseBase"
    }

    # -- download / extract app ---------------------------------------------
    $zip = $zipOverride
    if (-not $zip) {
        $zip = Join-Path $env:TEMP 'HERMES-Desktop.zip'
        Log 'downloading HERMES-Desktop.zip from the release...'
        Invoke-WebRequest -Uri "$ReleaseBase/HERMES-Desktop.zip" -OutFile $zip
    } else {
        Log "using local ZIP: $zip"
    }
    if (-not (Test-Path $zip)) { throw "ZIP not found: $zip" }

    $stage = Join-Path $env:TEMP ('hermes-desktop-stage-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Log 'extracting application...'
    Expand-Archive -Path $zip -DestinationPath $stage -Force
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    Copy-Item -Path (Join-Path $stage '*') -Destination $path -Recurse -Force
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue

    $electron = Join-Path $path 'desktop\node_modules\electron\dist\electron.exe'
    if (-not (Test-Path $electron)) { throw "Electron binary missing after install: $electron" }
    Log "application installed at $path"

    # -- shortcuts -----------------------------------------------------------
    $lnkName = 'H.E.R.M.E.S Agent Canvas.lnk'
    if ($desktopShortcut) {
        $desk = if ($scope -eq 'AllUsers') { [Environment]::GetFolderPath('CommonDesktopDirectory') }
                else { [Environment]::GetFolderPath('Desktop') }
        MakeShortcut (Join-Path $desk $lnkName) $electron '.' (Join-Path $path 'desktop') $electron
        Log "desktop shortcut: $desk\$lnkName"
    }
    if ($startShortcut) {
        $sm = if ($scope -eq 'AllUsers') { [Environment]::GetFolderPath('CommonPrograms') }
              else { [Environment]::GetFolderPath('Programs') }
        $dir = Join-Path $sm 'H.E.R.M.E.S'
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        MakeShortcut (Join-Path $dir $lnkName) $electron '.' (Join-Path $path 'desktop') $electron
        MakeShortcut (Join-Path $dir 'H.E.R.M.E.S Desktop README.lnk') "$env:WINDIR\system32\notepad.exe" `
            "`"$path\desktop\README.md`"" (Join-Path $path 'desktop') $electron
        Log "start menu: $dir"
    }
    Log 'INSTALL COMPLETE'
}

# --------------------------------------------------------------------------- GUI
if ($Silent) {
    Install-Desktop $Scope $Desktop $StartMenu $InstallPath $LocalZip
    if ($Launch) {
        $e = Join-Path (DefaultPath $Scope) 'desktop\node_modules\electron\dist\electron.exe'
        if (Test-Path $e) { Start-Process -FilePath $e -ArgumentList '.' -WorkingDirectory (Join-Path (DefaultPath $Scope) 'desktop') }
    }
    return
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'H.E.R.M.E.S Desktop — Installer'
$form.Size = New-Object System.Drawing.Size(600, 470)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$title = New-Object System.Windows.Forms.Label
$title.Text = 'H.E.R.M.E.S / DESKTOP AGENT CANVAS'
$title.Font = New-Object System.Drawing.Font('Consolas', 15, [System.Drawing.FontStyle]::Bold)
$title.ForeColor = [System.Drawing.Color]::FromArgb(0, 200, 220)
$title.Location = New-Object System.Drawing.Point(24, 18); $title.Size = New-Object System.Drawing.Size(540, 30)
$form.Controls.Add($title)

$info = New-Object System.Windows.Forms.Label
$info.Text = 'Installs the interactive desktop app and its Python backend from the release package.'
$info.Location = New-Object System.Drawing.Point(26, 52); $info.Size = New-Object System.Drawing.Size(540, 22)
$form.Controls.Add($info)

$grp = New-Object System.Windows.Forms.GroupBox
$grp.Text = 'Install for'; $grp.Location = New-Object System.Drawing.Point(24, 84); $grp.Size = New-Object System.Drawing.Size(540, 80)
$rbUser = New-Object System.Windows.Forms.RadioButton
$rbUser.Text = 'This user (recommended)'; $rbUser.Checked = $true
$rbUser.Location = New-Object System.Drawing.Point(16, 24); $rbUser.Size = New-Object System.Drawing.Size(220, 22)
$rbAll = New-Object System.Windows.Forms.RadioButton
$rbAll.Text = 'All users (requires administrator)'
$rbAll.Location = New-Object System.Drawing.Point(16, 50); $rbAll.Size = New-Object System.Drawing.Size(300, 22)
$grp.Controls.Add($rbUser); $grp.Controls.Add($rbAll); $form.Controls.Add($grp)

$grp2 = New-Object System.Windows.Forms.GroupBox
$grp2.Text = 'Shortcuts'; $grp2.Location = New-Object System.Drawing.Point(24, 174); $grp2.Size = New-Object System.Drawing.Size(540, 72)
$cbDesktop = New-Object System.Windows.Forms.CheckBox
$cbDesktop.Text = 'Add to Desktop'; $cbDesktop.Checked = $true
$cbDesktop.Location = New-Object System.Drawing.Point(16, 24); $cbDesktop.Size = New-Object System.Drawing.Size(200, 22)
$cbStart = New-Object System.Windows.Forms.CheckBox
$cbStart.Text = 'Add to Start Menu'; $cbStart.Checked = $true
$cbStart.Location = New-Object System.Drawing.Point(240, 24); $cbStart.Size = New-Object System.Drawing.Size(220, 22)
$grp2.Controls.Add($cbDesktop); $grp2.Controls.Add($cbStart); $form.Controls.Add($grp2)

$lblPath = New-Object System.Windows.Forms.Label
$lblPath.Text = 'Destination'; $lblPath.Location = New-Object System.Drawing.Point(26, 258); $lblPath.Size = New-Object System.Drawing.Size(120, 20)
$txtPath = New-Object System.Windows.Forms.TextBox
$txtPath.Text = DefaultPath 'User'; $txtPath.Location = New-Object System.Drawing.Point(26, 280); $txtPath.Size = New-Object System.Drawing.Size(450, 24)
$form.Controls.Add($lblPath); $form.Controls.Add($txtPath)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = 'Vertical'
$logBox.Location = New-Object System.Drawing.Point(26, 316); $logBox.Size = New-Object System.Drawing.Size(540, 86)
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9); $logBox.BackColor = [System.Drawing.Color]::FromArgb(10, 12, 17)
$logBox.ForeColor = [System.Drawing.Color]::FromArgb(200, 215, 225)
$form.Controls.Add($logBox)

$btnInstall = New-Object System.Windows.Forms.Button
$btnInstall.Text = 'INSTALL'; $btnInstall.Location = New-Object System.Drawing.Point(390, 414); $btnInstall.Size = New-Object System.Drawing.Size(88, 30)
$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = 'CANCEL'; $btnCancel.Location = New-Object System.Drawing.Point(486, 414); $btnCancel.Size = New-Object System.Drawing.Size(78, 30)
$form.Controls.Add($btnInstall); $form.Controls.Add($btnCancel)

$rbUser.add_CheckedChanged({
    if ($rbUser.Checked) { $txtPath.Text = DefaultPath 'User' }
})
$rbAll.add_CheckedChanged({
    if ($rbAll.Checked) { $txtPath.Text = DefaultPath 'AllUsers' }
})
$btnCancel.add_Click({ $form.Close() })

$runInstall = {
    $btnInstall.Enabled = $false
    $scope = if ($rbAll.Checked) { 'AllUsers' } else { 'User' }
    $path = $txtPath.Text.Trim()
    if (-not $path) { [System.Windows.Forms.MessageBox]::Show('Choose a destination folder.'); $btnInstall.Enabled = $true; return }

    if ($scope -eq 'AllUsers' -and -not (IsAdmin)) {
        $arg2 = @('-Scope', 'AllUsers', '-Silent', '-InstallPath', "`"$path`"")
        if ($cbDesktop.Checked) { $arg2 += '-Desktop' }
        if ($cbStart.Checked) { $arg2 += '-StartMenu' }
        if ($LocalZip) { $arg2 += @('-LocalZip', "`"$LocalZip`"") }
        $cmd = [Environment]::GetCommandLineArgs()[0]
        if ($cmd -and $cmd -match '\.exe$') {
            Start-Process -FilePath $cmd -Verb RunAs -Wait -ArgumentList $arg2
        } else {
            Start-Process -FilePath 'powershell' -Verb RunAs -Wait -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath) + $arg2
        }
        $logBox.Text += "`r`nElevated setup finished."
        $btnInstall.Enabled = $true
        return
    }

    try {
        $logBox.Text += "`r`nInstalling… see %LOCALAPPDATA%\hermes\desktop-install.log"
        [System.Windows.Forms.Application]::DoEvents()
        Install-Desktop $scope $cbDesktop.Checked $cbStart.Checked $path $LocalZip
        $logBox.AppendText("`r`nDone. You can launch H.E.R.M.E.S from your shortcut.")
        $btnInstall.Text = 'DONE'
    } catch {
        $logBox.AppendText("`r`nERROR: $($_.Exception.Message)")
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'Install failed')
        $btnInstall.Enabled = $true
    }
}
$btnInstall.add_Click($runInstall)

[System.Windows.Forms.Application]::Run($form)
