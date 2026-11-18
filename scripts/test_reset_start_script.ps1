# Windows startup gate; run with powershell.exe -NoProfile -File.
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -ne 5) {
    throw "Run this regression parent with Windows PowerShell 5.1."
}
$root = Split-Path -Parent $PSScriptRoot
$externalTemp = Join-Path (Split-Path -Parent $root) ".temp"
$probe = Join-Path $externalTemp ("reset-start-test-" + [Guid]::NewGuid().ToString("N"))
$windowsPS = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell/v1.0/powershell.exe"
$pwshHome = & pwsh.exe -NoProfile -Command '$PSHOME'
if ($LASTEXITCODE -ne 0 -or -not $pwshHome) { throw "PowerShell 7 is required for this regression gate." }
$pwsh = Join-Path $pwshHome "pwsh.exe"
$oldPath = $env:PATH
$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$environmentNames = @(
    "DRAGON_DB_PASSWORD", "DRAGON_PUBLIC_BIND", "DRAGON_PUBLIC_PORT",
    "DRAGON_OWNER_PORT", "DRAGON_OWNER_SECRETS_DIR", "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME", "DRAGON_AI_ENABLED", "DRAGON_PRIVATE_TEST",
    "DRAGON_WEB_PORT", "DRAGON_E2E_STATE_DIR",
    "RESET_TEST_LOG", "RESET_TEST_STATE", "RESET_TEST_CERT", "RESET_TEST_FAIL"
)
$oldEnvironment = @{}
foreach ($name in $environmentNames) {
    $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$script:checks = 0
function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:checks++
}
function Invoke-TestChild {
    param([string]$HostPath, [string]$ScriptPath, [string[]]$Arguments = @())
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $HostPath
    # All test arguments are controlled simple tokens or paths without quotes.
    $tokens = @("-NoProfile", "-File", $ScriptPath) + $Arguments
    $start.Arguments = ($tokens | ForEach-Object { '"' + $_ + '"' }) -join " "
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(60000)) {
        $process.Kill()
        throw "Child host timed out: $HostPath"
    }
    $result = @{
        Code = $process.ExitCode
        Output = $stdout.Result + $stderr.Result
    }
    $process.Dispose()
    Assert-Test (-not $result.Output.Contains("synthetic-test-only")) "A child exposed the database password."
    return $result
}
function New-TestInstance {
    param([string]$Name)
    $instance = Join-Path $probe $Name
    $scripts = Join-Path $instance "scripts"
    New-Item -ItemType Directory -Path $scripts | Out-Null
    foreach ($file in @("private-common.ps1", "start-reset.ps1", "reset-owner.ps1", "reset-host-test-fixture.ps1")) {
        Copy-Item (Join-Path $PSScriptRoot $file) $scripts
    }
    $env:RESET_TEST_LOG = Join-Path $instance "docker.jsonl"
    $env:RESET_TEST_STATE = Join-Path $instance ".temp/reset-secrets"
    $env:RESET_TEST_FAIL = ""
    return $instance
}
function Get-TestCalls {
    if (Test-Path -LiteralPath $env:RESET_TEST_LOG) {
        foreach ($line in [IO.File]::ReadAllLines($env:RESET_TEST_LOG)) {
            ConvertFrom-Json $line
        }
    }
}
function Initialize-TestSettings {
    param([string]$Instance, [string]$Content = "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_PUBLIC_BIND=127.0.0.1`nDRAGON_PUBLIC_PORT=48123`nDRAGON_OWNER_PORT=48124`n")
    $state = Initialize-PrivateDirectory (Join-Path $Instance ".temp/reset-secrets")
    $secret = Join-Path $state "compose.env"
    [IO.File]::WriteAllText($secret, $Content)
    Set-PrivateFileAcl $secret | Out-Null
}
function Assert-TestAcl {
    param([string]$Path)
    $acl = Get-Acl -LiteralPath $Path
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Assert-Test ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $owner) "Unexpected owner: $Path"
    Assert-Test $acl.AreAccessRulesProtected "DACL is not protected: $Path"
    $sids = @($acl.Access | ForEach-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        Assert-Test ($_.AccessControlType -eq "Allow" -and $_.FileSystemRights -eq "FullControl") "Unexpected ACL rule."
    } | Where-Object { $_ -is [string] })
    Assert-Test ($sids.Count -eq 2 -and $sids -contains $owner -and $sids -contains "S-1-5-18") "DACL is not exactly owner and SYSTEM."
}
try {
    $bin = Join-Path $probe "native bin"
    New-Item -ItemType Directory -Path $bin | Out-Null
    $env:TEMP = $probe
    $env:TMP = $probe
    # Compile an actual PATH executable so child-host command resolution and
    # native exit status are exercised, without contacting a Docker daemon.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;
public class ResetDockerShim {
    public static int Main(string[] args) {
        var json = new JavaScriptSerializer();
        var names = new [] { "DRAGON_DB_PASSWORD", "DRAGON_PUBLIC_BIND",
            "DRAGON_PUBLIC_PORT", "DRAGON_OWNER_PORT", "DRAGON_OWNER_SECRETS_DIR",
            "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "DRAGON_AI_ENABLED",
            "DRAGON_PRIVATE_TEST", "DRAGON_WEB_PORT", "DRAGON_E2E_STATE_DIR" };
        var inherited = names.Where(n => Environment.GetEnvironmentVariable(n) != null).ToArray();
        File.AppendAllText(Environment.GetEnvironmentVariable("RESET_TEST_LOG"),
            json.Serialize(new { Args = args, Inherited = inherited }) + "\n");
        if (inherited.Length > 0) return 91;
        int f = Array.IndexOf(args, "-f");
        var operation = args.Skip(f + 2).ToArray();
        if (String.Join(" ", operation) == Environment.GetEnvironmentVariable("RESET_TEST_FAIL") ||
            (args.Contains("owner-pair") && Environment.GetEnvironmentVariable("RESET_TEST_FAIL") == "owner-pair"))
            return 37;
        if (args.Contains("owner-pair")) {
            int i = Array.IndexOf(args, "--output");
            File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("RESET_TEST_STATE"),
                Path.GetFileName(args[i + 1])), "synthetic-pair-only");
        }
        if (operation.Length > 0 && operation[0] == "exec" && args.Contains("owner-caddy"))
            Console.Write(File.ReadAllText(Environment.GetEnvironmentVariable("RESET_TEST_CERT")));
        return 0;
    }
}
'@ -ReferencedAssemblies System.Web.Extensions, System.Core, System -OutputAssembly (Join-Path $bin "docker.exe") -OutputType ConsoleApplication
    Copy-Item (Join-Path $bin "docker.exe") (Join-Path $bin "powershell.exe")
    $env:PATH = "$bin;$oldPath"
    Assert-Test ((Get-Command docker -CommandType Application | Select-Object -First 1).Source -eq (Join-Path $bin "docker.exe")) "Docker shim is not first on PATH."
    foreach ($name in $environmentNames | Where-Object { $_ -notlike "RESET_TEST_*" }) {
        [Environment]::SetEnvironmentVariable($name, "inherited-$name", "Process")
    }
    $env:RESET_TEST_CERT = Join-Path $probe "public-test.crt"
    $rsa = [Security.Cryptography.RSA]::Create(2048)
    $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
        "CN=reset-script-test", $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
    $cert = $request.CreateSelfSigned([DateTimeOffset]::Now.AddDays(-1), [DateTimeOffset]::Now.AddDays(1))
    try {
        $pem = "-----BEGIN CERTIFICATE-----`n" +
            [Convert]::ToBase64String($cert.RawData, [Base64FormattingOptions]::InsertLineBreaks) +
            "`n-----END CERTIFICATE-----`n"
        [IO.File]::WriteAllText($env:RESET_TEST_CERT, $pem)
    } finally {
        $cert.Dispose()
        $rsa.Dispose()
    }
    . (Join-Path $PSScriptRoot "private-common.ps1")
    foreach ($hostPath in @($windowsPS, $pwsh)) {
        $hostName = [IO.Path]::GetFileNameWithoutExtension($hostPath)
        $instance = New-TestInstance "$hostName-equal"
        $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/start-reset.ps1") @("-PublicPort", "48123", "-OwnerPort", "48123")
        Assert-Test ($result.Code -ne 0 -and $result.Output.Contains("Reset ports must be different.")) "$hostName did not reject equal ports before private state: $($result.Output)"
        Assert-Test (-not (Test-Path (Join-Path $instance ".temp"))) "$hostName equal ports created state."
        Assert-Test (@(Get-TestCalls).Count -eq 0) "Equal ports reached Docker."

        $instance = New-TestInstance "$hostName-start"
        $startScript = Join-Path $instance "scripts/start-reset.ps1"
        $result = Invoke-TestChild $hostPath $startScript
        Assert-Test ($result.Code -eq 0) "$hostName first-run failed: $($result.Output)"
        Assert-Test ($result.Output.Contains(":8080") -and $result.Output.Contains(":8031")) "Default ports were lost."
        $passwordLine = [IO.File]::ReadAllLines((Join-Path $env:RESET_TEST_STATE "compose.env")) |
            Where-Object { $_.StartsWith("DRAGON_DB_PASSWORD=") }
        $generatedPassword = $passwordLine.Substring("DRAGON_DB_PASSWORD=".Length)
        Assert-Test (-not $result.Output.Contains($generatedPassword)) "First-run output exposed the generated database password."
        $generatedPassword = $null
        Assert-TestAcl $env:RESET_TEST_STATE
        foreach ($file in @("compose.env", "owner-localhost-root.crt")) {
            Assert-TestAcl (Join-Path $env:RESET_TEST_STATE $file)
        }
        $calls = @(Get-TestCalls)
        $operations = @($calls | ForEach-Object {
            $f = [Array]::IndexOf($_.Args, "-f")
            ($_.Args[($f + 2)..($_.Args.Count - 1)] -join " ")
        })
        Assert-Test (($operations -join "|") -eq "config --quiet|build|up -d --wait db|run --rm --no-deps analysis-worker migrate|up -d owner-caddy|run --rm owner-api owner-init|up -d --wait|exec -T owner-caddy cat /data/caddy/pki/authorities/local/root.crt") "Startup order/migration changed."
        foreach ($call in $calls) {
            Assert-Test (@($call.Inherited).Count -eq 0) "Inherited settings reached native Docker."
            Assert-Test (@($call.Args | Where-Object { $_ -eq "-f" }).Count -eq 1) "Compose overlays were used."
            Assert-Test ($call.Args -contains "dragon-radar-reset" -and $call.Args -contains (Join-Path $instance "compose.yaml")) "Wrong Compose target."
        }

        $instance = New-TestInstance "$hostName-custom"
        Initialize-TestSettings $instance
        $startScript = Join-Path $instance "scripts/start-reset.ps1"
        foreach ($arguments in @(@(), @("-PublicPort", "48123"), @("-OwnerPort", "48124"), @("-PublicPort", "48123", "-OwnerPort", "48124"))) {
            $result = Invoke-TestChild $hostPath $startScript $arguments
            Assert-Test ($result.Code -eq 0 -and $result.Output.Contains(":48123") -and $result.Output.Contains(":48124")) "$hostName lost saved custom ports: $($result.Output)"
        }
        $before = @(Get-TestCalls).Count
        $result = Invoke-TestChild $hostPath $startScript @("-PublicPort", "48125")
        Assert-Test ($result.Code -ne 0 -and $result.Output.Contains("existing reset instance uses different ports")) "Explicit port mismatch was accepted."
        Assert-Test (@(Get-TestCalls).Count -eq $before) "Port mismatch reached Docker."
        $ownerScript = Join-Path $instance "scripts/reset-owner.ps1"
        foreach ($action in @("Pair", "Revoke", "Certificate")) {
            $result = Invoke-TestChild $hostPath $ownerScript @("-Action", $action)
            Assert-Test ($result.Code -eq 0) "$hostName $action failed: $($result.Output)"
        }
        $before = @(Get-TestCalls).Count
        $result = Invoke-TestChild $hostPath $ownerScript
        Assert-Test ($result.Code -eq 0 -and $result.Output.Contains("No certificate was installed or removed.")) "Default owner action was lost."
        Assert-Test (@(Get-TestCalls).Count -eq $before) "Certificate action contacted Docker."
        $pairCalls = @(Get-TestCalls | Where-Object { $_.Args -contains "owner-pair" })
        Assert-Test ($pairCalls.Count -eq 1 -and $pairCalls[0].Args -contains "https://localhost:48124") "Pair ignored saved port/action."
        Get-ChildItem -LiteralPath $env:RESET_TEST_STATE -Filter "pair-*.txt" | ForEach-Object { Assert-TestAcl $_.FullName }

        foreach ($operation in $operations) {
            $instance = New-TestInstance ("$hostName-fail-" + [Guid]::NewGuid().ToString("N"))
            Initialize-TestSettings $instance
            $env:RESET_TEST_FAIL = $operation
            $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/start-reset.ps1")
            Assert-Test ($result.Code -ne 0) "Startup failure was swallowed: $operation"
            Assert-Test (@(Get-TestCalls).Count -eq ([Array]::IndexOf($operations, $operation) + 1)) "Startup continued after failure: $operation"
        }
        foreach ($action in @("Pair", "Revoke")) {
            $instance = New-TestInstance "$hostName-fail-$action"
            Initialize-TestSettings $instance
            if ($action -eq "Pair") {
                # Pair includes a random output filename.
                $env:RESET_TEST_FAIL = "owner-pair"
            } else { $env:RESET_TEST_FAIL = "exec -T owner-api dragon-radar owner-revoke" }
            $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/reset-owner.ps1") @("-Action", $action)
            Assert-Test ($result.Code -ne 0) "Owner failure was swallowed: $action"
            Assert-Test (@(Get-TestCalls).Count -eq 1) "Owner failure continued."
        }
        foreach ($content in @(
            "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_PUBLIC_BIND=127.0.0.1`nDRAGON_PUBLIC_PORT=48123`nDRAGON_OWNER_PORT=48123`n",
            "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_PUBLIC_BIND=0.0.0.0`nDRAGON_PUBLIC_PORT=48123`nDRAGON_OWNER_PORT=48124`n",
            "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_PUBLIC_BIND=127.0.0.1`nDRAGON_PUBLIC_PORT=invalid`nDRAGON_OWNER_PORT=48124`n",
            "DRAGON_DB_PASSWORD=synthetic-test-only`n",
            "DRAGON_DB_PASSWORD=synthetic-test-only`nDRAGON_DB_PASSWORD=duplicate`n"
        )) {
            $instance = New-TestInstance ("$hostName-invalid-" + [Guid]::NewGuid().ToString("N"))
            Initialize-TestSettings $instance $content
            $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/start-reset.ps1")
            Assert-Test ($result.Code -ne 0) "Invalid saved configuration was accepted."
            Assert-Test (@(Get-TestCalls).Count -eq 0) "Invalid saved configuration reached Docker."
        }
        $instance = New-TestInstance "$hostName-broad-acl"
        Initialize-TestSettings $instance
        $secret = Join-Path $env:RESET_TEST_STATE "compose.env"
        $acl = [IO.File]::GetAccessControl($secret, [Security.AccessControl.AccessControlSections]::Access)
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            (New-Object Security.Principal.SecurityIdentifier("S-1-1-0")), "Read", "Allow"
        )))
        (Get-Item -LiteralPath $secret).SetAccessControl($acl)
        foreach ($entry in @("start-reset.ps1", "reset-owner.ps1")) {
            $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/$entry")
            Assert-Test ($result.Code -ne 0 -and $result.Output.Contains("broader than the owner and SYSTEM")) "Broad DACL was accepted."
        }
        Assert-Test (@(Get-TestCalls).Count -eq 0) "Broad DACL reached Docker."

        $instance = New-TestInstance "$hostName-reparse"
        $target = Join-Path $probe "$hostName-junction-target"
        New-Item -ItemType Directory -Path $target | Out-Null
        $junction = Join-Path $instance ".temp"
        New-Item -ItemType Junction -Path $junction -Target $target | Out-Null
        try {
            foreach ($entry in @("start-reset.ps1", "reset-owner.ps1")) {
                $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/$entry")
                Assert-Test ($result.Code -ne 0 -and $result.Output.Contains("cannot contain reparse points")) "Reparse ancestor was accepted."
            }
            Assert-Test (@(Get-ChildItem $target -Force).Count -eq 0) "Reparse target was modified."
        } finally {
            [IO.Directory]::Delete($junction)
        }

        $instance = New-TestInstance "$hostName-environment"
        Initialize-TestSettings $instance
        $result = Invoke-TestChild $hostPath (Join-Path $instance "scripts/reset-host-test-fixture.ps1") @("-Mode", "Environment")
        Assert-Test ($result.Code -eq 0 -and $result.Output.Contains("Environment restoration passed")) "$hostName did not restore environment: $($result.Output)"
        Write-Output "$hostName real child-process regressions passed."
    }
    $instance = New-TestInstance "dispatch-contract"
    foreach ($mode in @("Dispatch", "MissingHost")) {
        $result = Invoke-TestChild $pwsh (Join-Path $instance "scripts/reset-host-test-fixture.ps1") @("-Mode", $mode)
        Assert-Test ($result.Code -eq 0) "$mode contract failed: $($result.Output)"
    }
    Write-Output "Reset startup regression checks passed ($script:checks assertions)."
} finally {
    $env:PATH = $oldPath
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], "Process")
    }
    if (Test-Path -LiteralPath $probe) {
        Remove-Item -LiteralPath $probe -Recurse -Force
    }
}
