param(
    [ValidateRange(1024, 65535)]
    [int]$PublicPort = 8080,
    [ValidateRange(1024, 65535)]
    [int]$OwnerPort = 8031
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "private-common.ps1")
$root = Split-Path -Parent $PSScriptRoot
$state = Initialize-PrivateDirectory (Join-Path $root ".temp/reset-secrets")
$secret = Join-Path $state "compose.env"

if (-not (Test-Path -LiteralPath $secret)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $password = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    $stream = [System.IO.File]::Open($secret, "CreateNew", "Write", "None")
    try {
        $content = [System.Text.Encoding]::UTF8.GetBytes(
            "DRAGON_DB_PASSWORD=$password`n" +
            "DRAGON_PUBLIC_BIND=127.0.0.1`n" +
            "DRAGON_PUBLIC_PORT=$PublicPort`n" +
            "DRAGON_OWNER_PORT=$OwnerPort`n"
        )
        $stream.Write($content, 0, $content.Length)
    } finally {
        $stream.Dispose()
        $password = $null
    }
}

Assert-PrivateFile $secret
$settings = @{}
foreach ($line in [System.IO.File]::ReadAllLines($secret)) {
    if ($line -notmatch '^([A-Z0-9_]+)=(.+)$') {
        throw "Reset configuration is malformed."
    }
    $settings[$Matches[1]] = $Matches[2]
}
foreach ($required in @(
    "DRAGON_DB_PASSWORD",
    "DRAGON_PUBLIC_BIND",
    "DRAGON_PUBLIC_PORT",
    "DRAGON_OWNER_PORT"
)) {
    if (-not $settings.ContainsKey($required)) {
        throw "Reset configuration is incomplete."
    }
}
if ($settings["DRAGON_PUBLIC_BIND"] -ne "127.0.0.1") {
    throw "Automatic non-loopback public binding is not allowed by this starter."
}
$configuredPublicPort = [int]$settings["DRAGON_PUBLIC_PORT"]
$configuredOwnerPort = [int]$settings["DRAGON_OWNER_PORT"]
if (
    $configuredPublicPort -lt 1024 -or $configuredPublicPort -gt 65535 -or
    $configuredOwnerPort -lt 1024 -or $configuredOwnerPort -gt 65535 -or
    $configuredPublicPort -eq $configuredOwnerPort
) {
    throw "Reset port configuration is invalid."
}
if (
    ($PSBoundParameters.ContainsKey("PublicPort") -and
        $PublicPort -ne $configuredPublicPort) -or
    ($PSBoundParameters.ContainsKey("OwnerPort") -and
        $OwnerPort -ne $configuredOwnerPort)
) {
    throw "The existing reset instance uses different ports."
}
$PublicPort = $configuredPublicPort
$OwnerPort = $configuredOwnerPort

$compose = @(
    "compose",
    "--project-name", "dragon-radar-reset",
    "--project-directory", $root,
    "--env-file", $secret,
    "-f", (Join-Path $root "compose.reset.yaml")
)
$oldValues = @{}
foreach ($name in $settings.Keys) {
    $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
try {
    & docker @compose config --quiet
    if ($LASTEXITCODE -ne 0) { throw "Reset Compose configuration is invalid." }
    & docker @compose build
    if ($LASTEXITCODE -ne 0) { throw "Reset container build failed." }
    & docker @compose up -d --wait db
    if ($LASTEXITCODE -ne 0) { throw "Reset database startup failed." }
    & docker @compose run --rm public-api migrate
    if ($LASTEXITCODE -ne 0) { throw "Reset database migrate step failed." }
    & docker @compose up -d owner-caddy
    if ($LASTEXITCODE -ne 0) { throw "Owner gateway namespace startup failed." }
    & docker @compose run --rm owner-api owner-init
    if ($LASTEXITCODE -ne 0) { throw "Reset owner initialization failed." }
    & docker @compose up -d --wait
    if ($LASTEXITCODE -ne 0) { throw "Reset service readiness failed." }

    $certificate = Join-Path $state "owner-localhost-root.crt"
    $pem = & docker @compose exec -T owner-caddy cat /data/caddy/pki/authorities/local/root.crt
    if ($LASTEXITCODE -ne 0) { throw "Could not export the owner CA certificate." }
    $certificateBytes = [System.Text.Encoding]::ASCII.GetBytes(($pem -join "`n") + "`n")
    $parsed = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,$certificateBytes)
    $parsed.Dispose()
    Save-PrivatePublicFile $certificate $certificateBytes

    Write-Output "Reset services are ready. No source data or model permit was enabled."
    Write-Output "Public read entry (loopback only): http://127.0.0.1:$PublicPort"
    Write-Output "Owner entry: https://localhost:$OwnerPort"
    Write-Output "Owner CA certificate: $certificate"
    Write-Output "Certificate trust has NOT been installed."
} finally {
    foreach ($name in $settings.Keys) {
        [Environment]::SetEnvironmentVariable(
            $name,
            $oldValues[$name],
            "Process"
        )
    }
}
