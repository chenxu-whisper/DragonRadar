# Windows startup gate; invoked by CI independently of pytest.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$probe = Join-Path $root (".temp/reset-start-test-" + [Guid]::NewGuid().ToString("N"))

try {
    $scripts = Join-Path $probe "scripts"
    New-Item -ItemType Directory -Path $scripts | Out-Null
    Copy-Item (Join-Path $root "scripts/private-common.ps1") $scripts
    Copy-Item (Join-Path $root "scripts/start-reset.ps1") $scripts
    Copy-Item (Join-Path $root "scripts/reset-owner.ps1") $scripts

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

    . (Join-Path $scripts "private-common.ps1")
    $state = Initialize-PrivateDirectory (Join-Path $probe ".temp/reset-secrets")
    $secret = Join-Path $state "compose.env"
    [System.IO.File]::WriteAllText(
        $secret,
        "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_PUBLIC_BIND=127.0.0.1`nDRAGON_PUBLIC_PORT=48123`nDRAGON_OWNER_PORT=48124`n"
    )
    Set-PrivateFileAcl $secret | Out-Null
    $certificate = Join-Path $state "owner-localhost-root.crt"
    Save-PrivatePublicFile $certificate ([System.Text.Encoding]::ASCII.GetBytes("synthetic-public-certificate"))
    $dockerCalls = New-Object 'System.Collections.Generic.List[object]'
    function docker {
        $dockerCalls.Add(@($args))
        foreach ($name in @("DRAGON_DB_PASSWORD", "DRAGON_PUBLIC_BIND", "DRAGON_PUBLIC_PORT", "DRAGON_OWNER_PORT", "DRAGON_OWNER_SECRETS_DIR", "COMPOSE_FILE")) {
            if ([Environment]::GetEnvironmentVariable($name, "Process")) {
                throw "Inherited Compose settings reached owner control."
            }
        }
        if ($args -contains "owner-pair") {
            $outputIndex = [Array]::IndexOf($args, "--output") + 1
            $filename = [System.IO.Path]::GetFileName($args[$outputIndex])
            [System.IO.File]::WriteAllText((Join-Path $state $filename), "synthetic-pair-only")
        }
        $global:LASTEXITCODE = 0
    }
    $oldOwnerPort = $env:DRAGON_OWNER_PORT
    $oldOwnerState = $env:DRAGON_OWNER_SECRETS_DIR
    $oldComposeFile = $env:COMPOSE_FILE
    try {
        $env:DRAGON_OWNER_PORT = "59999"
        $env:DRAGON_OWNER_SECRETS_DIR = "inherited-state"
        $env:COMPOSE_FILE = "inherited-compose"
        & (Join-Path $scripts "reset-owner.ps1") -Action Pair | Out-Null
        & (Join-Path $scripts "reset-owner.ps1") -Action Revoke | Out-Null
        & (Join-Path $scripts "reset-owner.ps1") -Action Certificate | Out-Null
        if ($env:DRAGON_OWNER_PORT -ne "59999") { throw "Owner control did not restore inherited settings." }
        if ($env:DRAGON_OWNER_SECRETS_DIR -ne "inherited-state" -or $env:COMPOSE_FILE -ne "inherited-compose") {
            throw "Owner control did not restore Compose settings."
        }
        if ($dockerCalls.Count -ne 2) { throw "Unexpected owner control Docker call count." }
        foreach ($call in $dockerCalls) {
            if (($call | Where-Object { $_ -eq "-f" }).Count -ne 1) { throw "Owner control used Compose overlays." }
            if ($call -notcontains "dragon-radar-reset" -or $call -notcontains "owner-api") {
                throw "Owner control targeted the wrong application."
            }
            if ($call -notcontains (Join-Path $probe "compose.yaml")) { throw "Owner control missed unified Compose." }
        }
        if ($dockerCalls[0] -notcontains "https://localhost:48124") {
            throw "Owner pairing ignored the configured owner port."
        }
        Write-Output "Reset owner control checks passed."
    } finally {
        $env:DRAGON_OWNER_PORT = $oldOwnerPort
        $env:DRAGON_OWNER_SECRETS_DIR = $oldOwnerState
        $env:COMPOSE_FILE = $oldComposeFile
        Remove-Item Function:docker -ErrorAction SilentlyContinue
    }
} finally {
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Recurse -Force
    }
}
