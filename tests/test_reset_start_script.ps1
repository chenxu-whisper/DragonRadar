$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$probe = Join-Path $root (".temp/reset-start-test-" + [Guid]::NewGuid().ToString("N"))

try {
    $scripts = Join-Path $probe "scripts"
    New-Item -ItemType Directory -Path $scripts | Out-Null
    Copy-Item (Join-Path $root "scripts/private-common.ps1") $scripts
    Copy-Item (Join-Path $root "scripts/start-reset.ps1") $scripts

    $rejected = $false
    try {
        & (Join-Path $scripts "start-reset.ps1") -PublicPort 48123 -OwnerPort 48123
    } catch {
        $rejected = $_.Exception.Message -eq "Reset ports must be different."
    }
    if (-not $rejected) {
        throw "Equal first-run ports were not rejected with the expected error."
    }
    if (Test-Path -LiteralPath (Join-Path $probe ".temp")) {
        throw "Equal first-run ports created reset state artifacts."
    }

    Write-Output "Reset first-run validation checks passed."
} finally {
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Recurse -Force
    }
}
