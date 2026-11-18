[CmdletBinding()]
param([string] $LogDirectory = "$env:ProgramData\ssh\input-logs")
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from PowerShell as Administrator.'
}
$fullPath = [IO.Path]::GetFullPath($LogDirectory)
if ($fullPath -eq [IO.Path]::GetPathRoot($fullPath)) { throw 'Choose a dedicated log directory, not a drive root.' }
$directory = New-Item -ItemType Directory -Path $fullPath -Force
if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Log directory must not be a junction or symbolic link.' }
$acl = [Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in 'S-1-5-18','S-1-5-32-544') {
    $identity = [Security.Principal.SecurityIdentifier]::new($sid)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $identity, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow))
}
# Fresh descriptor contains only a DACL; no SACL/SeSecurityPrivilege request.
Set-Acl -LiteralPath $fullPath -AclObject $acl
Write-Output "Prepared SYSTEM/Administrators-only directory: $fullPath"
Write-Output 'Add these global settings to the PATCHED server configuration before any Match block:'
Write-Output ('SessionInputLogDirectory "' + $fullPath.Replace('\','/') + '"')
Write-Output 'LogSessionInput yes'
