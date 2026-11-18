param([Parameter(Mandatory=$true)][string] $BinaryDirectory,
      [Parameter(Mandatory=$true)][string] $TestDirectory)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
# No real service or firewall cmdlets are called by these tests.
$ErrorActionPreference = 'Stop'
. (Join-Path $repositoryRoot 'contrib\win32\openssh\Install-InputLog.ps1')
$actualPreflight = (Get-Command Test-InputLogDeployment).ScriptBlock
$actualFirewall = (Get-Command Get-InputLogFirewallRules).ScriptBlock
$actualChange = (Get-Command Set-InputLogServiceCommand).ScriptBlock
$scratch = Join-Path $TestDirectory ('installer-tests-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($scratch) | Out-Null
$package = Join-Path $scratch 'package'
[IO.Directory]::CreateDirectory($package) | Out-Null
foreach ($name in 'sshd.exe','sshd-session.exe','sshd-auth.exe','ssh-shellhost.exe',
                 'sftp-server.exe','ssh-keygen.exe','crypto-57.dll','Install-InputLog.ps1') {
    [IO.File]::WriteAllText((Join-Path $package $name), "dummy-$name")
}
$config = Join-Path $scratch 'original config'
$originalText = @'
# authentication and network settings must survive
Port 2222
PasswordAuthentication no
HostKey __PROGRAMDATA__/ssh/ssh_host_ed25519_key
LogSessionInput no
SessionInputLogDirectory C:/old-logs
SshdSessionPath C:/old/sshd-session.exe
Match User someone
    LogSessionInput no
    AllowTcpForwarding no
'@
[IO.File]::WriteAllText($config, $originalText)
$originalHash = (Get-FileHash $config).Hash
$script:caseNumber = 0
function Assert($Condition, [string] $Message) { if (!$Condition) { throw $Message } }
function Assert-Throws([scriptblock] $Action, [string] $MessagePattern) {
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $MessagePattern) { throw }
        return
    }
    throw "Expected failure matching $MessagePattern"
}

# Native argument quoting: tests use actual Windows CommandLineToArgvW.
$values = @('', 'a b', 'C:\Program Files\OpenSSH\', 'x"y', 'trailing\\', '-o', 'Port=2222')
$line = '"C:\test\sshd.exe" ' + (($values | ForEach-Object { ConvertTo-InputLogArgument $_ }) -join ' ')
$parsed = @(Split-InputLogCommandLine $line)
Assert ($parsed.Count -eq $values.Count + 1) 'Argument count did not round-trip'
for ($i = 0; $i -lt $values.Count; $i++) { Assert ($parsed[$i+1] -ceq $values[$i]) "Argument $i did not round-trip" }
$launch = Get-InputLogLaunch ([pscustomobject]@{
    PathName = '"C:\Program Files\Old SSH\sshd.exe" -f ' + (ConvertTo-InputLogArgument $config) + ' -p 2222 -o "PasswordAuthentication=no"'
}) ''
Assert ($launch.Config -eq $config) 'Custom -f config was not discovered'
Assert (($launch.Arguments -join '|') -eq '-p|2222|-o|PasswordAuthentication=no') 'Service flags were not preserved'
Assert-Throws { Get-InputLogLaunch ([pscustomobject]@{PathName='"C:\old\sshd.exe" -D'}) '' } 'diagnostic flag'
Assert-Throws { Get-InputLogLocalPath 'C:\' } 'drive root'
Assert-Throws { Get-InputLogLocalPath '\\server\share' } 'absolute local'
Assert-Throws { New-InputLogConfig 'Include extra.conf' 'C:\bin' 'C:\logs' } 'self-contained'
Write-Host 'PASS: native Windows quoting, service config discovery, preserved flags, invalid paths'

# All deployment effects below are simulated, except files under $scratch.
function Assert-InputLogAdministrator {}
function Protect-InputLogDirectory([string] $Path, [switch] $UserRead) {
    [IO.Directory]::CreateDirectory($Path) | Out-Null
}
function Get-InputLogService([string] $Name) { return $script:service }
function Stop-InputLogService([string] $Name) {
    $script:stops++
    $script:service.State = 'Stopped'
}
function Start-InputLogService([string] $Name) {
    $script:starts++
    if ($script:failStart) { $script:failStart = $false; throw 'simulated startup failure' }
    $script:service.State = 'Running'
}
function Set-InputLogServiceCommand([string] $Name, [string] $Command, [string] $StartMode) {
    $script:changes++
    $script:service.PathName = $Command
    $script:service.StartMode = $StartMode
}
function Get-InputLogFirewallRules([string] $OldExecutable) {
    [pscustomobject]@{Name='existing-restricted-rule'; Program='C:\old\sshd.exe'}
}
function Set-InputLogFirewallProgram([string] $Name, [string] $Program) {
    $script:firewallProgram = $Program
    if ($script:failFirewall) { $script:failFirewall = $false; throw 'simulated firewall update failure' }
}
function Test-InputLogDeployment([string] $Release, [string] $Config, [string[]] $Arguments, [string] $Logs) {
    if ($script:failPreflight) { throw 'simulated preflight failure' }
    $generated = [IO.File]::ReadAllText($Config)
    Assert ($generated -match '(?m)^LogSessionInput yes\r?$') 'Global recording not enabled'
    Assert ($generated -notmatch '(?m)^\s*LogSessionInput no') 'Old recording opt-out survived'
    Assert ($generated -match '(?m)^Port 2222\r?$') 'Port setting lost'
    Assert ($generated -match '(?m)^PasswordAuthentication no\r?$') 'Authentication setting lost'
    Assert ($generated -match 'Match User someone') 'Match block lost'
    Assert ($generated -match 'AllowTcpForwarding no') 'Match restriction lost'
    Assert ((Get-FileHash $script:config).Hash -eq $script:originalHash) 'Original config was modified'
}
function Reset-Case([string] $OriginalState='Running', [string] $Mode='Auto') {
    $script:caseNumber++
    $script:service = [pscustomobject]@{
        Name='sshd'; PathName='"C:\old\sshd.exe" -f ' + (ConvertTo-InputLogArgument $script:config)
        State=$OriginalState; StartMode=$Mode; StartName='LocalSystem'
    }
    $script:originalCommand = $script:service.PathName
    $script:stops=0; $script:starts=0; $script:changes=0
    $script:failStart=$false; $script:failPreflight=$false; $script:failFirewall=$false
    $script:firewallProgram='C:\old\sshd.exe'
    $script:options = @{
        SourceDirectory=$script:package
        InstallRoot=(Join-Path $script:scratch "case-$script:caseNumber\install")
        LogDirectory=(Join-Path $script:scratch "case-$script:caseNumber\logs")
        BackupRoot=(Join-Path $script:scratch "case-$script:caseNumber\backups")
    }
}

Reset-Case
Invoke-InputLogInstall @options -WhatIf
Assert ($stops -eq 0 -and $changes -eq 0 -and !(Test-Path $options.InstallRoot)) 'WhatIf changed something'
Write-Host 'PASS: WhatIf performs no writes or service/firewall changes'

# Omitted destination parameters use the caller's current folder, even when
# the script and source package live elsewhere. Service paths stay absolute.
Reset-Case
$workingFolder = Join-Path $scratch 'current working folder'
[IO.Directory]::CreateDirectory($workingFolder) | Out-Null
Push-Location -LiteralPath $workingFolder
try {
    Invoke-InputLogInstall -SourceDirectory $package -WhatIf
    Assert (!(Test-Path (Join-Path $workingFolder 'install'))) 'Default-path WhatIf created files'
    $installed = Invoke-InputLogInstall -SourceDirectory $package
    $expectedInstall = Join-Path $workingFolder 'install'
    $expectedBackups = Join-Path $workingFolder 'backups'
    $expectedLogs = Join-Path $workingFolder 'input-logs'
    Assert ($installed.Release.StartsWith($expectedInstall + '\')) 'Default install root was not based on caller working directory'
    Assert ($installed.Backup.StartsWith($expectedBackups + '\')) 'Default backup root was not based on caller working directory'
    Assert ($installed.Logs -eq $expectedLogs) 'Default recording directory was not based on caller working directory'
    Assert ($installed.Config.StartsWith($expectedBackups + '\')) 'Active config was not kept under the caller working directory'
    $recordedDefaultCommand = $service.PathName
} finally { Pop-Location }
Assert ($service.PathName -eq $recordedDefaultCommand) 'Service command changed with working directory'
Assert ($service.PathName.Contains($workingFolder)) 'Service command did not contain permanent absolute paths'
Invoke-InputLogInstall -RollbackBackup $installed.Backup
Assert ($service.PathName -eq $originalCommand) 'Current-directory default install did not roll back'
Write-Host 'PASS: current-working-directory defaults, spaces, absolute service paths and rollback from another folder'

Reset-Case
$installed = Invoke-InputLogInstall @options
Assert ($service.State -eq 'Running' -and $changes -eq 1 -and $stops -eq 1) 'Successful service replacement failed'
Assert ($firewallProgram -eq (Join-Path $installed.Release 'sshd.exe')) 'Program firewall rule was not updated'
Assert ((Get-FileHash $config).Hash -eq $originalHash) 'Source config was changed'
$manifest = Get-Content (Join-Path $installed.Backup 'state.json') -Raw | ConvertFrom-Json
Assert ($manifest.OriginalServiceCommand -eq $originalCommand) 'Original service command missing from backup'
Assert ($manifest.FirewallRules.Count -eq 1) 'Firewall backup missing'
Invoke-InputLogInstall -RollbackBackup $installed.Backup
Assert ($service.PathName -eq $originalCommand -and $service.State -eq 'Running') 'Rollback did not restore old service'
Assert ($firewallProgram -eq 'C:\old\sshd.exe') 'Rollback did not restore firewall'
Write-Host 'PASS: install, config preservation, backup, program firewall update and explicit rollback'

Reset-Case
$failStart=$true
Assert-Throws { Invoke-InputLogInstall @options } 'original service was restored'
Assert ($service.PathName -eq $originalCommand -and $service.State -eq 'Running') 'Automatic rollback failed'
Assert ($firewallProgram -eq 'C:\old\sshd.exe') 'Automatic firewall rollback failed'
Write-Host 'PASS: startup failure automatically restores the original service and firewall'

Reset-Case
$failFirewall=$true
Assert-Throws { Invoke-InputLogInstall @options } 'original service was restored'
Assert ($service.PathName -eq $originalCommand -and $service.State -eq 'Running') 'Firewall failure did not restore service'
Write-Host 'PASS: firewall-update failure automatically rolls back'

Reset-Case
$failPreflight=$true
Assert-Throws { Invoke-InputLogInstall @options } 'preflight failure'
Assert ($stops -eq 0 -and $changes -eq 0 -and $service.State -eq 'Running') 'Preflight failure touched the service'
Write-Host 'PASS: failed preflight leaves the existing service running'

Reset-Case 'Stopped' 'Disabled'
$installed = Invoke-InputLogInstall @options
Assert ($service.StartMode -eq 'Automatic' -and $service.State -eq 'Running') 'Disabled service was not enabled'
Invoke-InputLogInstall -RollbackBackup $installed.Backup
Assert ($service.StartMode -eq 'Disabled' -and $service.State -eq 'Stopped') 'Original disabled/stopped state was not restored'
Write-Host 'PASS: disabled/stopped original state is restored on rollback'

Reset-Case
$installed = Invoke-InputLogInstall @options
$service.PathName='"C:\newer\sshd.exe"'
Assert-Throws { Invoke-InputLogInstall -RollbackBackup $installed.Backup } 'changed after this backup'
Assert ($service.PathName -eq '"C:\newer\sshd.exe"') 'Rollback overwrote a subsequent service change'
Write-Host 'PASS: rollback refuses to overwrite a later service replacement'

# The actual firewall/service adapters, with native cmdlets replaced by mocks.
function Get-NetFirewallApplicationFilter {
    [CmdletBinding()]param([string] $PolicyStore)
    [pscustomobject]@{Program='C:\old\sshd.exe'; TestName='old-rule'}
    [pscustomobject]@{Program='C:\unrelated\other.exe'; TestName='other-rule'}
}
function Get-NetFirewallRule {
    [CmdletBinding()]param([Parameter(ValueFromPipeline=$true)] $InputObject)
    process { [pscustomobject]@{Name=$InputObject.TestName} }
}
$selected = @(& $actualFirewall 'C:\old\sshd.exe')
Assert ($selected.Count -eq 1 -and $selected[0].Name -eq 'old-rule') 'Unrelated firewall rule was selected'
function Invoke-CimMethod {
    [CmdletBinding()]param($InputObject, $MethodName, $Arguments)
    $script:capturedChange = $Arguments
    [pscustomobject]@{ReturnValue=$script:changeCode}
}
$changeCode=0
& $actualChange 'sshd' '"C:\new\sshd.exe"' 'Auto'
Assert ($capturedChange.StartMode -eq 'Automatic') 'CIM start mode was not normalized'
$changeCode=2
Assert-Throws { & $actualChange 'sshd' '"C:\new\sshd.exe"' 'Auto' } 'code 2'
Write-Host 'PASS: only matching program firewall rules are selected; CIM errors are checked'

# Exercise the real native -V/-t/-T preflight using scratch host keys and config.
$native = $BinaryDirectory
$nativeConfig = Join-Path $scratch 'native-config'
$hostKey = Join-Path $scratch 'host-key'
$keygen = Invoke-InputLogDaemon -Executable (Join-Path $native 'ssh-keygen.exe') -Arguments (
    '-q -t ed25519 -N "" -f ' + (ConvertTo-InputLogArgument $hostKey))
if ($keygen.ExitCode) { throw ('Cannot generate fixture host key: ' + $keygen.Output) }
$nativeOriginal = 'HostKey "' + $hostKey.Replace('\','/') + '"' + "`r`nPasswordAuthentication no`r`n"
[IO.File]::WriteAllText($nativeConfig, (New-InputLogConfig $nativeOriginal $native $scratch))
& $actualPreflight -Release $native -Config $nativeConfig -Arguments @() -Logs $scratch
Assert-Throws { & $actualPreflight -Release $native -Config $nativeConfig -Arguments @('-o','LogSessionInput=no') -Logs $scratch } "effective 'logsessioninput'"
Write-Host 'PASS: real patched daemon preflight, including detection of conflicting service overrides'
Write-Host "ALL INSTALLER TESTS PASSED. Scratch files: $scratch"
