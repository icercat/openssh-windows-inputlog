# Install on your Windows SSH server

Copy `OpenSSH-InputLog-Windows-x64.zip` to the **destination Windows server**.
Extract the entire ZIP into a folder on that server. The installer changes the
computer where you run it; it does not remotely install from your current PC.

Open **64-bit PowerShell as Administrator on the destination server**, change
into the extracted folder, and run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-InputLog.ps1
```

The execution-policy override applies to this one PowerShell process. The
package is an experimental, unsigned custom build. Run from a local console or
another way into the server, because replacing SSH restarts the service and may
disconnect an SSH session used for installation.

For an optional preview with no installation or service changes:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-InputLog.ps1 -WhatIf
```

## What the installer does

- Detects the existing `sshd` service and its `-f` configuration path, or uses
  `C:\ProgramData\ssh\sshd_config` when no `-f` option exists.
- Copies the matching server executables and crypto DLL into a new versioned
  directory under `C:\Program Files\OpenSSH-InputLog`.
- Keeps the original binaries, configuration file, host keys, authentication
  settings, and network restrictions available. It backs up the configuration
  and original service command under `C:\ProgramData\ssh\inputlog-backups`.
- Creates a separate active configuration enabling input recording for all
  shell/exec users. Existing recording opt-outs are removed from that copy.
- Creates the recording directory with access for SYSTEM and Administrators.
- Checks the new executable and configuration before stopping existing SSH.
- Switches the existing service to the new executable and active configuration.
  It preserves service arguments, its account, and startup mode, except that a
  disabled service is enabled with Automatic startup so it can run.
- Updates existing local firewall rules that specifically name the old
  `sshd.exe`, preserving their port/address/profile/action restrictions.
- Starts SSH and checks that it stays running through a short startup check.
  Activation failure triggers an attempt to restore the original service and
  program firewall rules automatically.

It prints the installation directory, active configuration, backup directory,
recording directory, and a rollback command. Keep the backup directory: it also
contains the **active** configuration used by the new service. Make future
server configuration changes to that active file, whose path is printed.

The installer does not uninstall Windows' OpenSSH optional feature or remove
old files. The existing `sshd` service uses the new version; retaining the old
files allows rollback. Windows Update does not maintain this custom fork.

## Verify recording

Connect from another machine using the server's usual Tailscale IP, enter a
command, then exit. In elevated PowerShell on the server, list the recordings:

```powershell
Get-ChildItem C:\ProgramData\ssh\input-logs\*.jsonl
```

The package's `read-input-log.py` turns a recording into readable text using
Python 3; the server itself does not require Python:

```powershell
py .\read-input-log.py 'C:\ProgramData\ssh\input-logs\input-123-example.jsonl' > readable.txt
```

## Roll back

Use the exact rollback command printed by the installer. Alternatively, run the
script from the extracted package with the printed backup directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-InputLog.ps1 -RollbackBackup 'C:\ProgramData\ssh\inputlog-backups\InputLog1-REPLACE-WITH-YOUR-BACKUP-ID'
```

Rollback restores the previous service command, startup mode, running/stopped
state, and saved program firewall rules. Logs and installation files are kept.
It refuses to overwrite a service that was subsequently pointed at some other
installation.

## Supported setups and options

This installer supports x64 Windows, Windows PowerShell 5.1 or newer, and an
existing OpenSSH service running as LocalSystem. It requires a self-contained
configuration file: flatten active `Include` directives before installing so
included Match blocks cannot silently disable recording. Unsupported custom
service flags or conflicting command-line recording settings fail preflight.

Options include `-ServiceName`, `-ConfigPath`, `-InstallRoot`, `-LogDirectory`,
`-BackupRoot`, and `-SourceDirectory`. Defaults are suitable for the ordinary
Windows OpenSSH installation. Use dedicated, local, protected directories;
junctions, symbolic links, UNC paths, drive roots, and overlapping installation/
log/backup directories are rejected. The default Program Files and ProgramData
locations are recommended. Avoid changing these paths to shared folders.

No broad firewall allow rule is created, and Tailscale policies are not changed.
Managed firewall policies referring to the old executable need adjustment by
their administrator. A remote login test is still necessary; a running service
alone does not prove network access or authentication from another computer.

The bundled crypto DLL includes its C runtime; no separate OpenSSL installation
or Visual C++ redistributable installation is required for this package.

Installer replacement/rollback were tested with mocked service/firewall changes
in Windows PowerShell 5.1 and PowerShell 7, and its real daemon preflight was
tested. Elevated installation as LocalSystem on your server has not been run.
The underlying server passed ten real localhost SSH integration cases.

See `INPUT-RECORDING.md` for the recording format and scope. Passwords entered
inside SSH sessions are captured. Tell users their input is being recorded.

Implementation references: [Windows service Change method](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/change-method-in-class-win32-service),
[firewall application filters](https://learn.microsoft.com/en-us/powershell/module/netsecurity/set-netfirewallapplicationfilter).
