[CmdletBinding()]
param(
    [string]$Source = 'D:\a_data',
    [string]$SnapshotRoot = 'data\raw\imports',
    [string]$SnapshotId = 'ashare-2018-2025-v1',
    [switch]$RunWalkForward,
    [switch]$AllowInterpreterFallback
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

$localEnvPath = Join-Path $repo '.env'
if (Test-Path -LiteralPath $localEnvPath) {
    foreach ($line in Get-Content -LiteralPath $localEnvPath) {
        if ($line -match '^\s*(TINYSHARE_TOKEN|TINYSHARE_PYTHON)\s*=\s*(.+?)\s*$') {
            $name = $Matches[1]
            if (-not (Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue)) {
                $value = $Matches[2].Trim().Trim('"').Trim("'")
                Set-Item -LiteralPath "Env:$name" -Value $value
            }
        }
    }
}

$mainPython = Join-Path $repo '.venv\Scripts\python.exe'
$defaultIsolatedPython = Join-Path $repo 'data\runtime\tinyshare-venv\Scripts\python.exe'
$configuredIsolatedPython = $env:TINYSHARE_PYTHON
if ($configuredIsolatedPython -and (Test-Path -LiteralPath $configuredIsolatedPython)) {
    $isolatedPython = $configuredIsolatedPython
} elseif ($configuredIsolatedPython) {
    # PR 6: a configured interpreter that does not exist is an error, not
    # a signal to guess.  Only an explicit opt-in allows the fallback.
    if ($AllowInterpreterFallback) {
        Write-Warning "Configured TINYSHARE_PYTHON does not exist; using the project-isolated interpreter (explicit fallback)."
        $isolatedPython = $defaultIsolatedPython
    } else {
        throw "Configured TINYSHARE_PYTHON does not exist: $configuredIsolatedPython (pass -AllowInterpreterFallback to override)"
    }
} else {
    $isolatedPython = $defaultIsolatedPython
}

if (-not (Test-Path -LiteralPath $mainPython)) {
    throw "Main Python does not exist: $mainPython"
}
if (-not (Test-Path -LiteralPath $isolatedPython)) {
    throw "Isolated Python does not exist: $isolatedPython"
}
if (-not (Test-Path -LiteralPath $Source)) {
    throw "Purchased data directory does not exist: $Source"
}

# The default is the purchase root. If it contains one dated child directory,
# use that directory without embedding a locale-specific path in this script.
if ((Get-Item -LiteralPath $Source).PSIsContainer) {
    $hasCsv = Get-ChildItem -LiteralPath $Source -Filter '*.csv' -File -ErrorAction SilentlyContinue
    if (-not $hasCsv) {
        $children = @(Get-ChildItem -LiteralPath $Source -Directory -ErrorAction SilentlyContinue)
        if ($children.Count -eq 1) { $Source = $children[0].FullName }
    }
}

$secure = $null
$bstr = [IntPtr]::Zero
$token = $env:TINYSHARE_TOKEN
if ([string]::IsNullOrWhiteSpace($token)) {
    $secure = Read-Host 'Enter TinyShare token (session-only; never written to disk)' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}

try {
    $env:TINYSHARE_PYTHON = $isolatedPython
    $env:TINYSHARE_TOKEN = $token

    & $mainPython -m app.cli probe-supplement-provider
    if ($LASTEXITCODE -ne 0) { throw "TinyShare capability probe failed, exit code $LASTEXITCODE" }

    & $mainPython -m app.cli import-purchased-snapshot `
        --source $Source `
        --snapshot-root $SnapshotRoot `
        --snapshot-id $SnapshotId `
        --supplement-tinyshare
    if ($LASTEXITCODE -ne 0) { throw "Purchased snapshot supplement failed, exit code $LASTEXITCODE" }

    & $mainPython -m app.cli validate-snapshot (Join-Path $SnapshotRoot "$SnapshotId\manifest.json")
    if ($LASTEXITCODE -ne 0) { throw "Snapshot audit failed, exit code $LASTEXITCODE" }

    if ($RunWalkForward) {
        & $mainPython -m app.cli run-walk-forward `
            --experiment-code trend-quality-wf-2018-2025-purchased-v1 `
            --manifest (Join-Path $SnapshotRoot "$SnapshotId\manifest.json")
        if ($LASTEXITCODE -ne 0) { throw "Walk-forward experiment failed, exit code $LASTEXITCODE" }
    }
} finally {
    Remove-Item Env:TINYSHARE_TOKEN -ErrorAction SilentlyContinue
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $token = $null
}
