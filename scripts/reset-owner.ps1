param(
    [ValidateSet("Pair", "Revoke", "Certificate")]
    [string]$Action = "Certificate"
)

. (Join-Path $PSScriptRoot "private-common.ps1")
Invoke-ResetHostDispatch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters `
    -ParameterNames @("Action")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$state = Initialize-PrivateDirectory (Join-Path $root ".temp/reset-secrets")
$compose = @(Get-ResetCompose $root)
$settings = @{}
foreach ($line in [System.IO.File]::ReadAllLines((Join-Path $state "compose.env"))) {
    if ($line -notmatch '^([A-Z0-9_]+)=(.+)$' -or $settings.ContainsKey($Matches[1])) {
        throw "Reset configuration is malformed."
    }
    $settings[$Matches[1]] = $Matches[2]
}
if (-not $settings.ContainsKey("DRAGON_OWNER_PORT")) {
    throw "Owner port configuration is missing."
}
$port = 0
if (-not [int]::TryParse($settings["DRAGON_OWNER_PORT"], [ref]$port) -or $port -lt 1024 -or $port -gt 65535) {
    throw "Invalid owner port."
}
$oldValues = @{}
$environmentNames = @(Get-ResetEnvironmentNames $settings)
foreach ($name in $environmentNames) {
    $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
try {
    switch ($Action) {
        "Certificate" {
            $certificate = Join-Path $state "owner-localhost-root.crt"
            Assert-PrivateFile $certificate
            Write-Output "Public CA certificate: $certificate"
            Write-Output "CA file SHA256: $((Get-FileHash -LiteralPath $certificate -Algorithm SHA256).Hash)"
            Write-Output "No certificate was installed or removed."
        }
        "Pair" {
            $filename = "pair-$([Guid]::NewGuid().ToString('N')).txt"
            & docker @compose exec -T owner-api dragon-radar owner-pair `
                --origin "https://localhost:$port" --output "/run/dragon-owner/$filename"
            if ($LASTEXITCODE -ne 0) { throw "Pairing issuance failed." }
            $pairFile = Join-Path $state $filename
            Set-PrivateFileAcl $pairFile | Out-Null
            Assert-PrivateFile $pairFile
            Write-Output "Pairing file: $pairFile"
            Write-Output "Use only at the trusted https://localhost:$port entry within 5 minutes."
        }
        "Revoke" {
            & docker @compose exec -T owner-api dragon-radar owner-revoke
            if ($LASTEXITCODE -ne 0) { throw "Owner revocation failed." }
            Write-Output "Owner sessions and unused pairing codes were revoked."
        }
    }
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $oldValues[$name], "Process")
    }
}
