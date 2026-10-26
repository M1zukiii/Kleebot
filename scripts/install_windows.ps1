param([switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $root
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Test-KleePython([string]$candidate) {
    if (!(Test-Path -LiteralPath $candidate)) { return $false }
    try {
        & $candidate -c "import sys, tkinter, venv; assert sys.version_info[:2] == (3, 12)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

try {
    # Prefer an existing local environment; never depend on Store app aliases.
    $candidates = @(
        (Join-Path $root '.venv-local\Scripts\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    )
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $located = & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $located) { $candidates += [string]$located }
    }
    $python = $null
    foreach ($candidate in $candidates) {
        if (Test-KleePython $candidate) { $python = $candidate; break }
    }
    if (!$python) {
        Write-Host 'Installing Python 3.12 for your Windows account...'
        # Fixed official installer; verify Authenticode before executing it.
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('klee-python-' + [guid]::NewGuid().ToString('N') + '.exe')
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $temporary
            $signature = Get-AuthenticodeSignature -LiteralPath $temporary
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                throw 'Python installer signature verification failed.'
            }
            $installed = Start-Process -FilePath $temporary -ArgumentList @('/quiet', 'InstallAllUsers=0', 'Include_launcher=1', 'InstallLauncherAllUsers=0', 'Include_tcltk=1', 'Include_pip=1', 'Include_test=0', 'PrependPath=0', 'Shortcuts=0') -WindowStyle Hidden -Wait -PassThru
            if ($installed.ExitCode -notin @(0, 3010)) { throw "Python install failed: $($installed.ExitCode)" }
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        }
        $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (!(Test-KleePython $python)) { throw 'Python is unavailable. Install Python 3.12 with Tcl/Tk and retry.' }
    }
    $arguments = @((Join-Path $PSScriptRoot 'install_windows.py'), '--root', $root)
    if ($NoLaunch) { $arguments += '--no-launch' }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Klee installation did not complete. Check the message above and retry.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
