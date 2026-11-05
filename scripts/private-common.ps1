function Initialize-PrivateDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw "Private paths cannot contain reparse points."
            }
        }
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $acl.SetOwner($owner)
    foreach ($sid in @($owner, $system)) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"
        )
        $acl.AddAccessRule($rule)
    }
    if (Test-Path -LiteralPath $full) {
        # Persist only the owner/DACL sections changed above, not the audit SACL.
        (Get-Item -LiteralPath $full -Force).SetAccessControl($acl)
    } else {
        [System.IO.Directory]::CreateDirectory($full, $acl) | Out-Null
    }
    return $full
}

function Set-PrivateFileAcl {
    # Elevated administrator tokens default a newly created file's owner to the
    # Administrators group (S-1-5-32-544) even when the parent directory was
    # created with an explicit owner. Normalize the file DACL immediately after
    # writing so Assert-PrivateFile enforces the same contract in every token.
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    $item = Get-Item -LiteralPath $full -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a regular private file."
    }
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $owner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $acl.SetOwner($owner)
    foreach ($sid in @($owner, $system)) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid, "FullControl", "None", "None", "Allow"
        )
        $acl.AddAccessRule($rule)
    }
    # Persist only the owner/DACL sections changed above, not the audit SACL.
    $item.SetAccessControl($acl)
    return $full
}

function Assert-PrivateFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a regular private file."
    }
    $acl = Get-Acl -LiteralPath $Path
    $allowed = @(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        "S-1-5-18"
    )
    $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin $allowed) {
        throw "Private file is owned by an unexpected principal."
    }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
        if ($rule.AccessControlType -eq "Allow" -and $sid -notin $allowed) {
            throw "Private file permissions are broader than the owner and SYSTEM."
        }
    }
}

function Save-PrivatePublicFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$Bytes
    )
    if (Test-Path -LiteralPath $Path) {
        Assert-PrivateFile $Path
        $existing = [System.IO.File]::ReadAllBytes($Path)
        $hash = [System.Security.Cryptography.SHA256]::Create()
        try {
            $oldHash = [Convert]::ToBase64String($hash.ComputeHash($existing))
            $newHash = [Convert]::ToBase64String($hash.ComputeHash($Bytes))
            if ($oldHash -ne $newHash) {
                throw "The public certificate changed. Review it explicitly; the existing file was not replaced."
            }
        } finally { $hash.Dispose() }
        return
    }
    $stream = [System.IO.File]::Open($Path, "CreateNew", "Write", "None")
    try { $stream.Write($Bytes, 0, $Bytes.Length) } finally { $stream.Dispose() }
    Set-PrivateFileAcl $Path | Out-Null
    Assert-PrivateFile $Path
}

function Get-PrivateCompose {
    param([Parameter(Mandatory = $true)][string]$Root)
    $secret = Join-Path $Root ".temp/private-secrets/compose.env"
    Assert-PrivateFile $secret
    return @(
        "compose", "--project-name", "dragon-radar-private-review",
        "--project-directory", $Root, "--env-file", $secret,
        "-f", (Join-Path $Root "compose.yaml"),
        "-f", (Join-Path $Root "compose.private.yaml")
    )
}
