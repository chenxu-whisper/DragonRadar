param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$Workbench
)

if ($Workbench -and -not $PSBoundParameters.ContainsKey("Port")) {
    $Port = 8020
}

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$state = Join-Path $root ".temp"
$secretName = if ($Workbench) { "workbench-compose.env" } else { "local-compose.env" }
$secret = Join-Path $state $secretName
New-Item -ItemType Directory -Force $state | Out-Null

if (-not (Test-Path $secret)) {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    $password = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
    [System.IO.File]::WriteAllText(
        $secret,
        "DRAGON_DB_PASSWORD=$password`n",
        (New-Object System.Text.UTF8Encoding($false))
    )
}

# Keep generated credentials local to this Windows owner and SYSTEM.
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls $secret /inheritance:r /grant:r "*${identity}:(F)" "*S-1-5-18:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not restrict the generated credential file ACL."
}

$previousPort = $env:DRAGON_API_PORT
$previousWebPort = $env:DRAGON_WEB_PORT
$previousPassword = $env:DRAGON_DB_PASSWORD
$env:DRAGON_API_PORT = "$Port"
$env:DRAGON_WEB_PORT = "$Port"
$projectName = if ($Workbench) { "dragon-radar-workbench" } else { "dragon-radar-foundation" }
$overlay = if ($Workbench) { "compose.workbench.yaml" } else { "compose.dev.yaml" }
# Explicit env-file is the only password source, not inherited shell variables.
Remove-Item Env:DRAGON_DB_PASSWORD -ErrorAction SilentlyContinue
$compose = @(
    "compose", "--env-file", $secret,
    "--project-name", $projectName,
    "--project-directory", $root,
    "-f", (Join-Path $root "compose.yaml"),
    "-f", (Join-Path $root $overlay)
)

try {
    & docker @compose build
    if ($LASTEXITCODE -ne 0) { throw "Container build failed." }
    & docker @compose up -d --wait db
    if ($LASTEXITCODE -ne 0) { throw "Database startup failed." }
    & docker @compose run --rm api migrate
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
    $service = if ($Workbench) { "web" } else { "api" }
    & docker @compose up -d --wait $service
    if ($LASTEXITCODE -ne 0) { throw "API readiness failed." }
    $entryName = if ($Workbench) { "Local research workbench" } else { "Local read API" }
    Write-Output "${entryName}: http://127.0.0.1:$Port"
    Write-Output "No synthetic or real market data was imported."
} finally {
    $env:DRAGON_API_PORT = $previousPort
    $env:DRAGON_WEB_PORT = $previousWebPort
    $env:DRAGON_DB_PASSWORD = $previousPassword
}
