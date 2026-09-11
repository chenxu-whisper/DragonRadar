param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8030
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "private-common.ps1")
$root = Split-Path -Parent $PSScriptRoot
$state = Initialize-PrivateDirectory (Join-Path $root ".temp/private-secrets")
$secret = Join-Path $state "compose.env"
if (-not (Test-Path -LiteralPath $secret)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    $stream = [System.IO.File]::Open($secret, "CreateNew", "Write", "None")
    try {
        $content = [System.Text.Encoding]::UTF8.GetBytes(
            "DRAGON_DB_PASSWORD=$password`nDRAGON_PRIVATE_PORT=$Port`n"
        )
        $stream.Write($content, 0, $content.Length)
    } finally { $stream.Dispose() }
}
Assert-PrivateFile $secret
$portLine = @(Select-String -LiteralPath $secret -Pattern '^DRAGON_PRIVATE_PORT=([0-9]+)$')
if ($portLine.Count -ne 1) { throw "Private port configuration is missing or ambiguous." }
$configuredPort = [int]$portLine[0].Matches[0].Groups[1].Value
if ($configuredPort -lt 1024 -or $configuredPort -gt 65535) {
    throw "Private port configuration is invalid."
}
if ($PSBoundParameters.ContainsKey("Port") -and $Port -ne $configuredPort) {
    throw "The existing private instance uses another port. Do not silently change its trusted origin."
}
$Port = $configuredPort
$compose = @(Get-PrivateCompose $root)
$oldPassword = $env:DRAGON_DB_PASSWORD
$oldPort = $env:DRAGON_PRIVATE_PORT
try {
    Remove-Item Env:DRAGON_DB_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:DRAGON_PRIVATE_PORT -ErrorAction SilentlyContinue
    & docker @compose build
    if ($LASTEXITCODE -ne 0) { throw "Private container build failed." }
    & docker @compose up -d --wait db
    if ($LASTEXITCODE -ne 0) { throw "Private database startup failed." }
    & docker @compose run --rm api migrate
    if ($LASTEXITCODE -ne 0) { throw "Private database migration failed." }
    & docker @compose run --rm api owner-init
    if ($LASTEXITCODE -ne 0) { throw "Owner initialization failed." }
    & docker @compose up -d --wait web
    if ($LASTEXITCODE -ne 0) { throw "Private service readiness failed." }
    $certificate = Join-Path $state "localhost-root.crt"
    $pem = & docker @compose exec -T web cat /data/caddy/pki/authorities/local/root.crt
    if ($LASTEXITCODE -ne 0) { throw "Could not export the public CA certificate." }
    $certificateBytes = [System.Text.Encoding]::ASCII.GetBytes(($pem -join "`n") + "`n")
    $parsedCertificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,$certificateBytes)
    $parsedCertificate.Dispose()
    Save-PrivatePublicFile $certificate $certificateBytes
    $fingerprint = (Get-FileHash -LiteralPath $certificate -Algorithm SHA256).Hash
    Write-Output "Private services are ready. No market data was imported."
    Write-Output "Private entry: https://localhost:$Port"
    Write-Output "Public CA certificate: $certificate"
    Write-Output "CA file SHA256: $fingerprint"
    Write-Output "Certificate trust has NOT been installed. Confirm trust before pairing."
    Write-Output "Use private-owner.ps1 only after validating the configured HTTPS origin."
} finally {
    $env:DRAGON_DB_PASSWORD = $oldPassword
    $env:DRAGON_PRIVATE_PORT = $oldPort
}
