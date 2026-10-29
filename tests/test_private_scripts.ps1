$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root "scripts/private-common.ps1")
$probe = Join-Path $root (".temp/acl-test-" + [Guid]::NewGuid().ToString("N"))
try {
    Initialize-PrivateDirectory $probe | Out-Null
    Initialize-PrivateDirectory $probe | Out-Null
    $directoryAcl = Get-Acl -LiteralPath $probe
    if (-not $directoryAcl.AreAccessRulesProtected) { throw "Directory ACL inherits." }
    if ($directoryAcl.Access.Count -ne 2) { throw "Directory ACL has unexpected rules." }
    $file = Join-Path $probe "fixture.txt"
    [System.IO.File]::WriteAllText($file, "synthetic-test-only")
    Set-PrivateFileAcl $file | Out-Null
    Assert-PrivateFile $file
    $acl = Get-Acl -LiteralPath $file
    $everyone = New-Object System.Security.Principal.SecurityIdentifier("S-1-1-0")
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $everyone, "Read", "Allow"
    )
    $acl.AddAccessRule($rule)
    # Set-Acl requests ACCESS_SYSTEM_SECURITY (SeSecurityPrivilege) for a
    # DACL-protected file; the DACL-only FileInfo extension needs no elevated
    # token and still persists the intentionally broadened ACL for rejection.
    (Get-Item -LiteralPath $file).SetAccessControl($acl)
    $rejected = $false
    try { Assert-PrivateFile $file } catch { $rejected = $true }
    if (-not $rejected) { throw "Private file with Everyone access was accepted." }
    $publicFile = Join-Path $probe "public-fixture.txt"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("synthetic-public-material")
    Save-PrivatePublicFile $publicFile $bytes
    Save-PrivatePublicFile $publicFile $bytes
    $rejected = $false
    try {
        Save-PrivatePublicFile $publicFile ([System.Text.Encoding]::UTF8.GetBytes("changed"))
    } catch { $rejected = $true }
    if (-not $rejected) { throw "Changed public file replaced the existing file." }
    if ([System.IO.File]::ReadAllText($publicFile) -ne "synthetic-public-material") {
        throw "Existing public file was modified."
    }
    $rejected = $false
    try { Save-PrivatePublicFile $probe $bytes } catch { $rejected = $true }
    if (-not $rejected) { throw "A directory was accepted as public output." }
    Write-Output "Private directory and file ACL checks passed."
} finally {
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Recurse -Force
    }
}
