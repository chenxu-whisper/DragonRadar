param(
    [ValidateSet("Pair", "Revoke", "Certificate")]
    [string]$Action = "Certificate"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "private-common.ps1")
$root = Split-Path -Parent $PSScriptRoot
$state = Initialize-PrivateDirectory (Join-Path $root ".temp/private-secrets")
$compose = @(Get-PrivateCompose $root)
$portLine = @(Select-String -LiteralPath (Join-Path $state "compose.env") -Pattern '^DRAGON_PRIVATE_PORT=([0-9]+)$')
if ($portLine.Count -ne 1) { throw "Private port configuration is missing or ambiguous." }
$port = [int]$portLine[0].Matches[0].Groups[1].Value
if ($port -lt 1024 -or $port -gt 65535) { throw "Invalid private port." }
$oldPassword = $env:DRAGON_DB_PASSWORD
$oldPort = $env:DRAGON_PRIVATE_PORT
try {
    Remove-Item Env:DRAGON_DB_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:DRAGON_PRIVATE_PORT -ErrorAction SilentlyContinue
    switch ($Action) {
        "Certificate" {
            $certificate = Join-Path $state "localhost-root.crt"
            Assert-PrivateFile $certificate
            Write-Output "Public CA certificate: $certificate"
            Write-Output "CA file SHA256: $((Get-FileHash -LiteralPath $certificate -Algorithm SHA256).Hash)"
            Write-Output "No certificate was installed or removed."
        }
        "Pair" {
            $filename = "pair-$([Guid]::NewGuid().ToString('N')).txt"
            & docker @compose exec -T api dragon-radar owner-pair `
                --origin "https://localhost:$port" --output "/run/dragon-owner/$filename"
            if ($LASTEXITCODE -ne 0) { throw "Pairing issuance failed." }
            $pairFile = Join-Path $state $filename
            Assert-PrivateFile $pairFile
            Write-Output "Pairing file: $pairFile"
            Write-Output "Use only at the trusted https://localhost:$port entry within 5 minutes."
        }
        "Revoke" {
            & docker @compose exec -T api dragon-radar owner-revoke
            if ($LASTEXITCODE -ne 0) { throw "Owner revocation failed." }
            Write-Output "Owner sessions and unused pairing codes were revoked."
        }
    }
} finally {
    $env:DRAGON_DB_PASSWORD = $oldPassword
    $env:DRAGON_PRIVATE_PORT = $oldPort
}
