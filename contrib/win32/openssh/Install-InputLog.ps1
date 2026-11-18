#requires -Version 5.1
<#
.SYNOPSIS
Replaces the target PC's existing LocalSystem OpenSSH service with InputLog1.
.DESCRIPTION
Run on the destination Windows x64 server, from the extracted binary package,
in PowerShell as Administrator. Creates a separate active configuration, keeps
the original binaries/configuration/host keys, and restores the old service if
activation fails. Does not install on another computer remotely.
New installation files, recordings, and backups default to subdirectories of
the current PowerShell working directory, resolved to permanent absolute paths.
.EXAMPLE
.\Install-InputLog.ps1 -WhatIf
.EXAMPLE
.\Install-InputLog.ps1
.EXAMPLE
.\Install-InputLog.ps1 -RollbackBackup 'C:\SSH-InputLog\backups\<id>'
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $SourceDirectory = $PSScriptRoot,
    [string] $InstallRoot,
    [string] $LogDirectory,
    [string] $BackupRoot,
    [string] $ConfigPath,
    [ValidatePattern('^[A-Za-z0-9_.-]+$')][string] $ServiceName = 'sshd',
    [string] $RollbackBackup
)

Set-StrictMode -Version 2.0

function Assert-InputLogAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run PowerShell as Administrator on the destination Windows server.'
    }
    if (![Environment]::Is64BitOperatingSystem -or ![Environment]::Is64BitProcess) {
        throw 'Use 64-bit PowerShell on an x64 Windows server.'
    }
    $architecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
    if ($architecture -ne 'AMD64') { throw 'This package requires an x64 Windows server.' }
}

function Get-InputLogLocalPath([string] $Path) {
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path -match '["\r\n\x00]') {
        throw "Use an absolute local drive path without quotes or newlines: $Path"
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    if ($full.Length -le 3) { throw 'A drive root cannot be used as an installation/log/backup directory.' }
    $current = $full
    while ($current -and $current.Length -gt 3) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Junctions and symbolic links are not supported in deployment paths: $current"
            }
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
    return $full
}

function Protect-InputLogDirectory([string] $Path, [switch] $UserRead) {
    $full = Get-InputLogLocalPath $Path
    [IO.Directory]::CreateDirectory($full) | Out-Null
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    # Set an administrative owner too; a previous non-admin owner must not
    # retain the implicit ability to replace the directory's DACL.
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in 'S-1-5-18', 'S-1-5-32-544') {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid),
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    if ($UserRead) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),
            [Security.AccessControl.FileSystemRights]::ReadAndExecute,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    Set-Acl -LiteralPath $full -AclObject $acl -ErrorAction Stop
}

function Split-InputLogCommandLine([string] $CommandLine) {
    if (!('InputLogInstaller.NativeArguments' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace InputLogInstaller {
    public static class NativeArguments {
        [DllImport("shell32.dll", SetLastError=true)]
        public static extern IntPtr CommandLineToArgvW(
            [MarshalAs(UnmanagedType.LPWStr)] string command, out int count);
        [DllImport("kernel32.dll")] public static extern IntPtr LocalFree(IntPtr value);
    }
}
'@
    }
    $count = 0
    $pointer = [InputLogInstaller.NativeArguments]::CommandLineToArgvW($CommandLine, [ref] $count)
    if ($pointer -eq [IntPtr]::Zero) { throw 'Cannot parse the existing service command.' }
    try {
        for ($i = 0; $i -lt $count; $i++) {
            [Runtime.InteropServices.Marshal]::PtrToStringUni(
                [Runtime.InteropServices.Marshal]::ReadIntPtr($pointer, $i * [IntPtr]::Size))
        }
    } finally { [InputLogInstaller.NativeArguments]::LocalFree($pointer) | Out-Null }
}

function ConvertTo-InputLogArgument([AllowEmptyString()][string] $Value) {
    # Windows argv escaping, including a quoted argument ending in backslash.
    $text = [Text.StringBuilder]::new()
    [void] $text.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void] $text.Append(('\' * (2 * $slashes + 1)))
        } else { [void] $text.Append(('\' * $slashes)) }
        [void] $text.Append($character)
        $slashes = 0
    }
    [void] $text.Append(('\' * (2 * $slashes)))
    [void] $text.Append('"')
    return $text.ToString()
}

function Get-InputLogService([string] $Name) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction Stop
    if (!$service) { throw "Existing OpenSSH service '$Name' was not found on this computer." }
    return $service
}

function Get-InputLogLaunch($Service, [string] $RequestedConfig) {
    $argv = @(Split-InputLogCommandLine ([Environment]::ExpandEnvironmentVariables($Service.PathName)))
    if ($argv.Count -eq 0 -or [IO.Path]::GetFileName($argv[0]) -ine 'sshd.exe') {
        throw 'The existing service must directly launch sshd.exe, not a wrapper.'
    }
    $oldConfig = $null
    $arguments = [Collections.Generic.List[string]]::new()
    for ($i = 1; $i -lt $argv.Count; $i++) {
        if ($argv[$i] -ceq '-f') {
            if (++$i -ge $argv.Count -or $oldConfig) { throw 'Invalid or duplicate service -f option.' }
            $oldConfig = $argv[$i]
        } elseif ($argv[$i].StartsWith('-f') -and $argv[$i].Length -gt 2) {
            if ($oldConfig) { throw 'Duplicate service -f option.' }
            $oldConfig = $argv[$i].Substring(2)
        } elseif ($argv[$i] -cmatch '^-(?:[DditT]|V)$') {
            throw "The service uses a non-service/diagnostic flag: $($argv[$i])"
        } else { $arguments.Add($argv[$i]) }
    }
    if (!$RequestedConfig) {
        $RequestedConfig = if ($oldConfig) { $oldConfig } else { "$env:ProgramData\ssh\sshd_config" }
    }
    [pscustomobject]@{
        Executable = $argv[0]
        Config = Get-InputLogLocalPath $RequestedConfig
        Arguments = [string[]] $arguments.ToArray()
    }
}

function New-InputLogConfig([string] $Original, [string] $Release, [string] $Logs) {
    if ($Original -match '(?im)^\s*Include\s') {
        throw 'This automatic installer requires a self-contained sshd_config. Flatten active Include directives first so recording cannot be disabled by an included Match block.'
    }
    foreach ($path in $Release, $Logs) {
        if ($path -match '["\r\n\x00]') { throw 'Unsafe configuration path.' }
    }
    $releasePath = $Release.Replace('\', '/')
    $logPath = $Logs.Replace('\', '/')
    $header = @(
        '# Managed by OpenSSH InputLog1 installer; original config is backed up.',
        'LogSessionInput yes',
        ('SessionInputLogDirectory "' + $logPath + '"'),
        ('SshdSessionPath "' + $releasePath + '/sshd-session.exe"'),
        ('SshdAuthPath "' + $releasePath + '/sshd-auth.exe"'),
        ''
    )
    # Remove old recording directives in global AND Match sections so an old
    # opt-out cannot silently turn off this requested recording of all users.
    $body = @($Original -split '\r?\n' | Where-Object {
        $_ -notmatch '^\s*(?:LogSessionInput|SessionInputLogDirectory|SshdSessionPath|SshdAuthPath)\s'
    })
    return (($header + $body) -join "`r`n")
}

function Invoke-InputLogDaemon([string] $Executable, [string] $Arguments) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        if (!$process.Start()) { throw "Cannot launch $Executable" }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (!$process.WaitForExit(30000)) {
            $process.Kill()
            throw 'OpenSSH preflight timed out.'
        }
        [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        }
    } finally { $process.Dispose() }
}

function Test-InputLogDeployment([string] $Release, [string] $Config,
                                [string[]] $Arguments, [string] $Logs) {
    $exe = Join-Path $Release 'sshd.exe'
    $version = Invoke-InputLogDaemon $exe '-V'
    if ($version.ExitCode -ne 0 -or $version.Output -notmatch 'Win32-OpenSSH-InputLog1') {
        throw "The package is not a runnable InputLog1 build. Check the x64 Visual C++ runtime. $($version.Output)"
    }
    $reader = Invoke-InputLogDaemon (Join-Path $Release 'read-input-log-cli.exe') '--help'
    if ($reader.ExitCode -ne 0 -or $reader.Output -notmatch 'raw-stdin') {
        throw "The recording reader cannot run. Keep read-input-log.exe and read-input-log-runtime together. $($reader.Output)"
    }
    $gui = Invoke-InputLogDaemon (Join-Path $Release 'read-input-log.exe') '--check-runtime'
    if ($gui.ExitCode -ne 0) {
        throw 'The graphical reader runtime cannot load. Extract the complete package, including its Tcl/Tk files.'
    }
    $tail = (@($Arguments | ForEach-Object { ConvertTo-InputLogArgument $_ }) -join ' ')
    $configArgument = '-f ' + (ConvertTo-InputLogArgument $Config)
    foreach ($mode in '-t', '-T') {
        $result = Invoke-InputLogDaemon $exe "$mode $configArgument $tail"
        if ($result.ExitCode -ne 0) { throw "OpenSSH preflight $mode failed: $($result.Output)" }
        if ($mode -ceq '-T') {
            $expected = @{
                logsessioninput = 'yes'
                sessioninputlogdirectory = $Logs.Replace('\', '/')
                sshdsessionpath = $Release.Replace('\', '/') + '/sshd-session.exe'
                sshdauthpath = $Release.Replace('\', '/') + '/sshd-auth.exe'
            }
            foreach ($name in $expected.Keys) {
                $line = @($result.Output -split '\r?\n' | Where-Object { $_ -match "^$name\s" })
                if ($line.Count -ne 1 -or $line[0].Substring($name.Length).Trim() -ine $expected[$name]) {
                    throw "The effective '$name' setting is incorrect. Expected '$($expected[$name])'; got '$($line -join '; ')'. Check existing service -o overrides."
                }
            }
        }
    }
}

function Get-InputLogFirewallRules([string] $OldExecutable) {
    # Only existing LOCAL rules scoped to the old executable are adjusted.
    # Existing port/address/profile/action restrictions remain as configured.
    $old = [Environment]::ExpandEnvironmentVariables($OldExecutable)
    $filters = @(Get-NetFirewallApplicationFilter -PolicyStore PersistentStore -ErrorAction Stop)
    foreach ($filter in $filters) {
        if ([Environment]::ExpandEnvironmentVariables($filter.Program) -ine $old) { continue }
        foreach ($rule in @($filter | Get-NetFirewallRule -ErrorAction Stop)) {
            [pscustomobject]@{ Name = $rule.Name; Program = $filter.Program }
        }
    }
}

function Set-InputLogFirewallProgram([string] $Name, [string] $Program) {
    Get-NetFirewallRule -Name $Name -PolicyStore PersistentStore -ErrorAction Stop |
        Get-NetFirewallApplicationFilter -ErrorAction Stop |
        Set-NetFirewallApplicationFilter -Program $Program -ErrorAction Stop | Out-Null
}

function Set-InputLogServiceCommand([string] $Name, [string] $Command, [string] $StartMode) {
    $arguments = @{ PathName = $Command }
    if ($StartMode) { $arguments.StartMode = if ($StartMode -eq 'Auto') { 'Automatic' } else { $StartMode } }
    $result = Invoke-CimMethod -InputObject (Get-InputLogService $Name) -MethodName Change -Arguments $arguments -ErrorAction Stop
    if ($result.ReturnValue -ne 0) { throw "Changing service '$Name' failed (code $($result.ReturnValue))." }
}

function Stop-InputLogService([string] $Name) {
    $controller = Get-Service -Name $Name -ErrorAction Stop
    if ($controller.Status -ne 'Stopped') {
        Stop-Service -Name $Name -ErrorAction Stop
        $controller.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
}

function Start-InputLogService([string] $Name) {
    Start-Service -Name $Name -ErrorAction Stop
    $controller = Get-Service -Name $Name -ErrorAction Stop
    $controller.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    Start-Sleep -Seconds 2
    $controller.Refresh()
    if ($controller.Status -ne 'Running') { throw "Service '$Name' stopped during its startup check." }
}

function Restore-InputLogState($State) {
    if ($State.Version -ne 1 -or $State.ServiceName -notmatch '^[A-Za-z0-9_.-]+$') {
        throw 'Unrecognized installer backup manifest.'
    }
    $current = Get-InputLogService $State.ServiceName
    if ($current.PathName -ne $State.NewServiceCommand -and $current.PathName -ne $State.OriginalServiceCommand) {
        throw 'The service was changed after this backup. Refusing to overwrite its newer configuration.'
    }
    Stop-InputLogService $State.ServiceName
    Set-InputLogServiceCommand $State.ServiceName $State.OriginalServiceCommand $State.OriginalStartMode
    $firewallErrors = @()
    foreach ($rule in $State.FirewallRules) {
        try { Set-InputLogFirewallProgram $rule.Name $rule.Program }
        catch { $firewallErrors += $_.Exception.Message }
    }
    if ($State.OriginalWasRunning) { Start-InputLogService $State.ServiceName }
    if ($firewallErrors.Count) { throw ('Service restored, but firewall restore failed: ' + ($firewallErrors -join '; ')) }
}

function Invoke-InputLogInstall {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [string] $SourceDirectory = $PSScriptRoot,
        [string] $InstallRoot,
        [string] $LogDirectory,
        [string] $BackupRoot,
        [string] $ConfigPath,
        [ValidatePattern('^[A-Za-z0-9_.-]+$')][string] $ServiceName = 'sshd',
        [string] $RollbackBackup
    )
    $ErrorActionPreference = 'Stop'
    if ($RollbackBackup) {
        $backup = Get-InputLogLocalPath $RollbackBackup
        if (!$PSCmdlet.ShouldProcess($backup, 'Restore the previous OpenSSH service and program firewall rules')) { return }
        Assert-InputLogAdministrator
        $state = Get-Content -LiteralPath (Join-Path $backup 'state.json') -Raw | ConvertFrom-Json
        Restore-InputLogState $state
        Write-Host 'Previous OpenSSH service restored. Recordings and installed files have been retained.'
        return
    }
    $service = Get-InputLogService $ServiceName
    if ($service.StartName -notin @('LocalSystem', 'NT AUTHORITY\SYSTEM', '.\LocalSystem')) {
        throw 'This installer supports an existing LocalSystem OpenSSH service. Custom service accounts need custom log permissions.'
    }
    if ($service.State -notin @('Running', 'Stopped')) { throw 'Wait for the existing SSH service to finish its current state transition.' }
    $launch = Get-InputLogLaunch $service $ConfigPath
    $source = Get-InputLogLocalPath $SourceDirectory
    if (!$InstallRoot -or !$LogDirectory -or !$BackupRoot) {
        $location = Get-Location
        if ($location.Provider.Name -ne 'FileSystem') {
            throw 'Run the installer from a local filesystem working directory, or provide all three destination paths.'
        }
        # Capture PowerShell's working directory, not the script directory or
        # the service's working directory. Store absolute paths for restarts.
        $workingDirectory = $location.ProviderPath
        if (!$InstallRoot) { $InstallRoot = Join-Path $workingDirectory 'install' }
        if (!$LogDirectory) { $LogDirectory = Join-Path $workingDirectory 'input-logs' }
        if (!$BackupRoot) { $BackupRoot = Join-Path $workingDirectory 'backups' }
    }
    $install = Get-InputLogLocalPath $InstallRoot
    $logs = Get-InputLogLocalPath $LogDirectory
    $backups = Get-InputLogLocalPath $BackupRoot
    $paths = @($install, $logs, $backups)
    for ($i = 0; $i -lt $paths.Count; $i++) {
        foreach ($forbidden in @($env:ProgramFiles, $env:ProgramData, [IO.Path]::GetDirectoryName($launch.Config))) {
            if ($paths[$i] -ieq $forbidden) { throw 'Choose dedicated installation, log, and backup directories.' }
        }
        for ($j = $i + 1; $j -lt $paths.Count; $j++) {
            if ($paths[$i] -ieq $paths[$j] -or $paths[$i].StartsWith($paths[$j] + '\', [StringComparison]::OrdinalIgnoreCase) -or
                $paths[$j].StartsWith($paths[$i] + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Installation, logs, and backups must use separate, non-nested directories.'
            }
        }
    }
    $required = @('sshd.exe', 'sshd-session.exe', 'sshd-auth.exe', 'ssh-shellhost.exe',
                  'sftp-server.exe', 'ssh-keygen.exe', 'crypto-57.dll', 'Install-InputLog.ps1',
                  'read-input-log.exe', 'read-input-log-cli.exe')
    foreach ($name in $required) {
        $file = Join-Path $source $name
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Extract the entire x64 binary package first. Missing: $file" }
        if ((Get-Item -LiteralPath $file).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Package file is a reparse point: $file" }
    }
    $readerRuntime = Get-InputLogLocalPath (Join-Path $source 'read-input-log-runtime')
    if (!(Test-Path -LiteralPath $readerRuntime -PathType Container)) {
        throw 'Extract the entire binary package, including the read-input-log-runtime folder.'
    }
    if (!(Test-Path -LiteralPath $launch.Config -PathType Leaf)) { throw "Existing configuration not found: $($launch.Config)" }
    Write-Host "Target: $env:COMPUTERNAME; existing service: $ServiceName"
    Write-Host "Original configuration: $($launch.Config)"
    Write-Host "Destination: $install; input recording files: $logs"
    if (!$PSCmdlet.ShouldProcess("$env:COMPUTERNAME / $ServiceName", 'Back up and replace the existing OpenSSH server, enable input recording, and restart SSH')) { return }
    Assert-InputLogAdministrator

    $id = 'InputLog1-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $release = Join-Path $install $id
    $backup = Join-Path $backups $id
    Protect-InputLogDirectory $install -UserRead
    Protect-InputLogDirectory $release -UserRead
    Protect-InputLogDirectory $backups
    Protect-InputLogDirectory $backup
    Protect-InputLogDirectory $logs
    $files = $required + @('ssh.exe', 'read-input-log.py', 'input_log_reader.py', 'input_log_gui.py',
                           'read-input-log-gui.py', 'INPUT-RECORDING.md', 'BUILD-AND-TEST.md',
                           'LICENCE', 'NOTICE', 'prepare-input-log-directory.ps1', 'INSTALL.md',
                           'README-READER.md')
    foreach ($name in $files) {
        $file = Join-Path $source $name
        if (!(Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        Copy-Item -LiteralPath $file -Destination (Join-Path $release $name) -ErrorAction Stop
        if ((Get-FileHash -LiteralPath $file).Hash -ne (Get-FileHash -LiteralPath (Join-Path $release $name)).Hash) {
            throw "Copied package file failed verification: $name"
        }
    }
    if (Test-Path -LiteralPath (Join-Path $source 'licenses')) {
        Copy-Item -LiteralPath (Join-Path $source 'licenses') -Destination $release -Recurse -ErrorAction Stop
    }
    Copy-Item -LiteralPath $readerRuntime -Destination $release -Recurse -ErrorAction Stop
    Copy-Item -LiteralPath $launch.Config -Destination (Join-Path $backup 'sshd_config.original') -ErrorAction Stop
    $encoding = [Text.UTF8Encoding]::new($false, $true)
    $original = [IO.File]::ReadAllText($launch.Config, $encoding)
    $activeConfig = Join-Path $backup 'sshd_config.inputlog'
    [IO.File]::WriteAllText($activeConfig, (New-InputLogConfig $original $release $logs), $encoding)
    Test-InputLogDeployment -Release $release -Config $activeConfig -Arguments $launch.Arguments -Logs $logs
    $tail = (@($launch.Arguments | ForEach-Object { ConvertTo-InputLogArgument $_ }) -join ' ')
    $newExecutable = Join-Path $release 'sshd.exe'
    $newCommand = (ConvertTo-InputLogArgument $newExecutable) + ' -f ' + (ConvertTo-InputLogArgument $activeConfig)
    if ($tail) { $newCommand += ' ' + $tail }
    $rules = @(Get-InputLogFirewallRules $launch.Executable)
    $state = [pscustomobject]@{
        Version = 1; ServiceName = $ServiceName
        CreatedUtc = [DateTime]::UtcNow.ToString('o')
        OriginalServiceCommand = $service.PathName
        OriginalStartMode = $service.StartMode
        OriginalWasRunning = ($service.State -eq 'Running')
        OriginalConfig = $launch.Config
        OriginalConfigSHA256 = (Get-FileHash -LiteralPath $launch.Config).Hash
        NewServiceCommand = $newCommand; NewExecutable = $newExecutable
        ActiveConfig = $activeConfig; Release = $release; LogDirectory = $logs
        FirewallRules = $rules
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $backup 'state.json') -Encoding UTF8
    Write-Host "Backup: $backup"
    try {
        Stop-InputLogService $ServiceName
        $newStartMode = if ($service.StartMode -eq 'Disabled') { 'Automatic' } else { $service.StartMode }
        Set-InputLogServiceCommand $ServiceName $newCommand $newStartMode
        foreach ($rule in $rules) { Set-InputLogFirewallProgram $rule.Name $newExecutable }
        Start-InputLogService $ServiceName
    } catch {
        $activationError = $_.Exception.Message
        try { Restore-InputLogState $state }
        catch { throw "Activation failed: $activationError. Automatic rollback also reported: $($_.Exception.Message). Backup: $backup" }
        throw "Activation failed; the original service was restored. $activationError. Backup: $backup"
    }
    Write-Host "InputLog1 installed and service '$ServiceName' is running."
    Write-Host "Active configuration: $activeConfig"
    Write-Host "Recordings: $logs\input-*.jsonl"
    $rollbackScript = "'" + (Join-Path $release 'Install-InputLog.ps1').Replace("'", "''") + "'"
    $rollbackDirectory = "'" + $backup.Replace("'", "''") + "'"
    Write-Host ("Rollback: & $rollbackScript -RollbackBackup $rollbackDirectory")
    [pscustomobject]@{ Service = $ServiceName; Release = $release; Backup = $backup; Logs = $logs; Config = $activeConfig }
}

# Dot-sourcing loads helpers for tests; normal invocation performs installation.
if ($MyInvocation.InvocationName -ne '.') { Invoke-InputLogInstall @PSBoundParameters }
