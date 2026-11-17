# Installs Jarvis on Windows. Run it by double-clicking "Install Jarvis.bat".
# Puts Jarvis in your user folder (C:\Users\<you>\Jarvis), installs Git, Python (via uv),
# Jarvis's parts and Claude Code, logs you in, downloads his voice and hearing, and adds
# a Jarvis shortcut to your Desktop. Safe to run again: your settings and memory are kept.
param([switch]$BrainOnly)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$env:PYTHONUTF8 = '1'

$Here = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\')
$Dest = if ($BrainOnly) { $Here } else { Join-Path $env:USERPROFILE 'Jarvis' }
$Py = Join-Path $Dest '.venv\Scripts\python.exe'
$Log = Join-Path $env:TEMP 'jarvis-install-log.txt'
try { Start-Transcript -Path $Log -Force | Out-Null } catch {}

function Step($t) { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan }
function Fail($t) {
    Write-Host ''
    Write-Host $t -ForegroundColor Red
    Write-Host 'Something went wrong. Take a photo of this window (or copy the text) and send it to Claude.' -ForegroundColor Yellow
    Write-Host "A copy of everything is saved in $Log"
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$env:USERPROFILE\.local\bin;$env:APPDATA\npm;$m;$u"
}
function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Run-Installer($url, $name) {
    # Downloads an official install script and runs it in its own PowerShell, so it can't close this one.
    $f = Join-Path $env:TEMP $name
    try {
        Invoke-WebRequest -Uri $url -OutFile $f -UseBasicParsing
        powershell -NoProfile -ExecutionPolicy Bypass -File $f
    } catch { Write-Host "  Download failed: $_" -ForegroundColor Yellow }
    Refresh-Path
}
function Find-Claude {
    Push-Location $Dest
    $p = & $Py -c "import brain; print(brain.find_claude() or '')" 2>$null
    Pop-Location
    if ($p) { return ($p | Select-Object -Last 1).Trim() }
    return $null
}
function Test-Claude($claude) {
    $out = & $claude -p 'Reply with just OK' 2>$null
    return ($LASTEXITCODE -eq 0 -and $out)
}
function Setup-Brain {
    Step "Claude Code (Jarvis's brain)"
    $claude = Find-Claude
    if (-not $claude) {
        Write-Host '  Installing Claude Code (a few minutes)...'
        for ($i = 1; $i -le 2 -and -not $claude; $i++) {
            Run-Installer 'https://claude.ai/install.ps1' 'claude-install.ps1'
            $claude = Find-Claude
        }
        if (-not $claude -and (Have npm)) {
            Write-Host '  Trying npm instead...'
            npm install -g @anthropic-ai/claude-code
            Refresh-Path
            $claude = Find-Claude
        }
    }
    if (-not $claude) { Fail "Claude Code didn't install." }
    Write-Host "  Found Claude Code at $claude"
    Write-Host "  Checking you're logged in..."
    if (-not (Test-Claude $claude)) {
        Write-Host ''
        Write-Host 'You need to log in to Claude Code once:' -ForegroundColor Yellow
        Write-Host '  1. Claude Code opens in this window. Pick a colour style, then choose to log in'
        Write-Host '     with your Claude account (your paid plan).'
        Write-Host '  2. A web page opens. Click Authorize, then come back to this window.'
        Write-Host '  3. If it asks whether to trust this folder, say yes.'
        Write-Host '  4. When you see the box where you can type, type /exit and press Enter.'
        Read-Host 'Press Enter to start' | Out-Null
        Push-Location $Dest
        & $claude
        Pop-Location
        if (-not (Test-Claude $claude)) {
            Fail "Claude Code still isn't logged in. Double-click 'Fix Jarvis Brain' in the Jarvis folder to try again."
        }
    }
    Write-Host '  Brain is working.' -ForegroundColor Green
}

Refresh-Path

if ($BrainOnly) {
    if (-not (Test-Path $Py)) { Fail 'Jarvis is not installed yet. Double-click "Install Jarvis" first.' }
    Setup-Brain
    Push-Location $Dest
    & $Py brain.py
    Pop-Location
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host 'Press Enter to close' | Out-Null
    exit 0
}

Write-Host 'Setting up Jarvis on this PC. This takes about 10 to 20 minutes, mostly downloading.' -ForegroundColor Green
Write-Host 'Leave this window open. It will ask you a few simple questions along the way.'

# ---- 1. files ----
Step "1/6 Copying Jarvis to $Dest"
if ($Here -ine $Dest) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    robocopy $Here $Dest /E /NFL /NDL /NJH /NJS /NP /XD .venv __pycache__ .git models sounds `
        /XF config.json memory.json calendar.json .spotify_token oura_tokens.json jarvis.log jarvis.ico | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "Couldn't copy the files to $Dest." }
    if (-not (Test-Path (Join-Path $Dest 'config.json'))) { Copy-Item (Join-Path $Here 'config.json') $Dest }
}
Get-ChildItem $Dest -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
Set-Location $Dest
Write-Host '  Done.'

# ---- 2. Git ----
Step '2/6 Git (Claude Code needs it, and it lets Jarvis undo his own changes)'
if (-not (Have git)) {
    if (Have winget) {
        Write-Host '  Installing Git. If Windows asks "Do you want to allow this app to make changes?", click Yes.'
        winget install --id Git.Git -e --source winget --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
    }
    if (-not (Have git)) {
        Write-Host '  Downloading Git directly...'
        try {
            $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -UseBasicParsing
            $asset = $rel.assets | Where-Object { $_.name -match '^Git-.*-64-bit\.exe$' } | Select-Object -First 1
            $exe = Join-Path $env:TEMP $asset.name
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $exe -UseBasicParsing
            Write-Host '  Installing Git. Click Yes if Windows asks.'
            Start-Process -FilePath $exe -ArgumentList '/VERYSILENT', '/NORESTART', '/SP-' -Wait
        } catch { Write-Host "  Git download failed: $_" -ForegroundColor Yellow }
        Refresh-Path
    }
    if (-not (Have git)) { Fail "Git didn't install. You can get it from https://git-scm.com/download/win, then run Install Jarvis again." }
}
Write-Host '  Git is ready.'

# ---- 3. Python ----
Step '3/6 Python'
if (-not (Have uv)) { Run-Installer 'https://astral.sh/uv/install.ps1' 'uv-install.ps1' }
if (-not (Have uv)) { Fail "Couldn't install uv (the tool that sets up Python)." }
if (-not (Test-Path $Py)) {
    if (Test-Path '.venv') { Remove-Item '.venv' -Recurse -Force }
    uv venv --seed --python 3.12 .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Py)) { Fail "Couldn't set up Python." }
}
Write-Host '  Python is ready.'

# ---- 4. packages ----
Step "4/6 Jarvis's parts (a few minutes)"
uv pip install --python $Py -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Couldn't install Jarvis's parts." }

# ---- 5. brain ----
Setup-Brain

# ---- 6. voice, hearing, apps ----
Step '6/6 Voice, hearing and apps'
& $Py setup_windows.py
if ($LASTEXITCODE -ne 0) { Fail 'The voice and hearing setup stopped early.' }

# ---- shortcuts ----
$ws = New-Object -ComObject WScript.Shell
function Make-Shortcut($folder) {
    $lnk = $ws.CreateShortcut((Join-Path $folder 'Jarvis.lnk'))
    $lnk.TargetPath = Join-Path $Dest 'Start Jarvis.bat'
    $lnk.WorkingDirectory = $Dest
    $ico = Join-Path $Dest 'jarvis.ico'
    if (Test-Path $ico) { $lnk.IconLocation = $ico }
    $lnk.Description = 'Start Jarvis'
    $lnk.Save()
}
try {
    Make-Shortcut ([Environment]::GetFolderPath('Desktop'))
    Make-Shortcut (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
} catch { Write-Host "  Couldn't make the Desktop shortcut: $_" -ForegroundColor Yellow }

try { Stop-Transcript | Out-Null } catch {}
Write-Host ''
Write-Host 'All done! Jarvis is installed.' -ForegroundColor Green
Write-Host 'Start him any time by double-clicking "Jarvis" on your Desktop.'
Write-Host 'Then say "Hey Jarvis" or clap twice.'
$go = Read-Host 'Start Jarvis now? (y/n)'
if ($go -match '^y') { Start-Process -FilePath (Join-Path $Dest 'Start Jarvis.bat') -WorkingDirectory $Dest }
