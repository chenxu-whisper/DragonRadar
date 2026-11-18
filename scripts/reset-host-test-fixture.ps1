param([ValidateSet("Environment", "Dispatch", "MissingHost")][string]$Mode)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "private-common.ps1")
if ($Mode -eq "Environment") {
    $names = @(Get-ResetEnvironmentNames @{ DRAGON_DB_PASSWORD = "synthetic-test-only" })
    $before = @{}
    foreach ($name in $names) { $before[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
    if ($PSVersionTable.PSVersion.Major -gt 5) {
        $windowsPS = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell/v1.0/powershell.exe"
        & $windowsPS -NoProfile -File $PSCommandPath -Mode Environment
        if ($LASTEXITCODE -ne 0) { throw "Private-operation host failed environment restoration." }
        foreach ($name in $names) {
            if ([Environment]::GetEnvironmentVariable($name, "Process") -cne $before[$name]) {
                throw "Dispatch changed caller environment: $name"
            }
        }
        exit 0
    }
    foreach ($entry in @("start-reset.ps1", "reset-owner.ps1")) {
        foreach ($fail in @($false, $true)) {
            $env:RESET_TEST_FAIL = ""
            if ($fail) {
                if ($entry -eq "start-reset.ps1") { $env:RESET_TEST_FAIL = "build" }
                else { $env:RESET_TEST_FAIL = "exec -T owner-api dragon-radar owner-revoke" }
            }
            $caught = $false
            try {
                if ($entry -eq "start-reset.ps1") { & (Join-Path $PSScriptRoot $entry) | Out-Null }
                else { & (Join-Path $PSScriptRoot $entry) -Action Revoke | Out-Null }
            } catch { $caught = $true }
            # PS7 dispatch exits the calling host; direct restoration assertions
            # therefore run in the actual PS5.1 private-operation host.
            if ($caught -ne $fail) { throw "Unexpected fixture outcome." }
            foreach ($name in $names) {
                if ([Environment]::GetEnvironmentVariable($name, "Process") -cne $before[$name]) {
                    throw "Environment was not restored: $name"
                }
            }
        }
    }
    Write-Output "Environment restoration passed"
    exit 0
}
function Invoke-FixtureChild {
    param([string]$Path, [string[]]$Arguments)
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $PSHOME "pwsh.exe"
    $start.Arguments = ((@("-NoProfile", "-File", $Path) + $Arguments) | ForEach-Object { '"' + $_ + '"' }) -join " "
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $child = [Diagnostics.Process]::Start($start)
    $stdout = $child.StandardOutput.ReadToEndAsync()
    $stderr = $child.StandardError.ReadToEndAsync()
    if (-not $child.WaitForExit(30000)) { $child.Kill(); throw "Dispatch fixture timed out." }
    $result = @{ Code = $child.ExitCode; Output = $stdout.Result + $stderr.Result }
    $child.Dispose()
    return $result
}
foreach ($entry in @("start-reset.ps1", "reset-owner.ps1")) {
    $source = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $entry))
    $tokens = $null
    $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
    $rootAssignment = $ast.EndBlock.Statements | Where-Object {
        $_ -is [Management.Automation.Language.AssignmentStatementAst] -and $_.Left.Extent.Text -eq '$root'
    } | Select-Object -First 1
    if (-not $rootAssignment) { throw "Cannot identify entry dispatch boundary." }
    $prefix = $source.Substring(0, $rootAssignment.Extent.StartOffset)
    $path = Join-Path $PSScriptRoot ("probe-" + $entry)
    if ($Mode -eq "MissingHost") {
        # Simulate only an absent OS executable, not command resolution, private
        # state, or Docker. The real trusted host is used by all other cases.
        $paramEnd = $ast.ParamBlock.Extent.EndOffset
        $prefix = $prefix.Insert($paramEnd, @'

function Test-Path {
    param([string]$LiteralPath, [string]$PathType)
    if ($LiteralPath -eq (Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell/v1.0/powershell.exe")) {
        return $false
    }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
'@)
        [IO.File]::WriteAllText($path, $prefix + "`nthrow 'Dispatch fell through to private state.'")
        $result = Invoke-FixtureChild $path @()
        if ($result.Code -eq 0 -or -not $result.Output.Contains("Windows PowerShell 5.1") -or
            -not $result.Output.Contains("install") -or $result.Output.Contains("fell through")) {
            throw "Missing-host failure was not actionable: $($result.Output)"
        }
        continue
    }
    # Keep the actual entry header and dispatch bytes, replacing only the
    # private-operation tail with a PS5.1 receiver and nonzero exit status.
    [IO.File]::WriteAllText($path, $prefix + @'

@{ Major = $PSVersionTable.PSVersion.Major; Bound = $PSBoundParameters; Extras = @($args) } | ConvertTo-Json -Compress
exit 23
'@)
    $cases = ,@()
    if ($entry -eq "start-reset.ps1") {
        $cases += ,@("-PublicPort", "48123")
        $cases += ,@("-OwnerPort", "48124")
        $cases += ,@("-PublicPort", "48123", "-OwnerPort", "48124")
    } else {
        foreach ($action in @("Pair", "Revoke", "Certificate")) { $cases += ,@("-Action", $action) }
    }
    foreach ($arguments in $cases) {
        $result = Invoke-FixtureChild $path $arguments
        if ($result.Code -ne 23) { throw "Dispatch did not preserve exit 23: $($result.Output)" }
        $received = ConvertFrom-Json $result.Output
        if ($received.Major -ne 5) { throw "Dispatch did not use Windows PowerShell 5.1." }
        if (@($received.Extras).Count -ne 0) { throw "Dispatch forwarded undeclared arguments." }
        $properties = @($received.Bound.PSObject.Properties)
        if ($properties.Count -ne $arguments.Count / 2) { throw "Dispatch forwarded defaults or extra parameters." }
        for ($i = 0; $i -lt $arguments.Count; $i += 2) {
            $name = $arguments[$i].TrimStart("-")
            if ([string]$received.Bound.$name -ne $arguments[$i + 1]) { throw "Dispatch lost a bound parameter." }
        }
    }
    $result = Invoke-FixtureChild $path @("-Unexpected", "must-not-forward")
    if ($result.Code -ne 23) { throw "Extra-argument dispatch failed: $($result.Output)" }
    $received = ConvertFrom-Json $result.Output
    if (@($received.Bound.PSObject.Properties).Count -ne 0 -or @($received.Extras).Count -ne 0) {
        throw "Dispatch forwarded undeclared arguments or defaults."
    }
}
Write-Output "$Mode contract passed"
