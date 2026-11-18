# OpenSSH InputLog1 for Windows

This is a custom, opt-in modification of Microsoft's Windows OpenSSH fork.
It records SSH shell/exec input on the Windows destination itself, including
connections made directly over Tailscale. No recording gateway is required.

## Enable file recording

For automated replacement of the existing server on **another PC**, copy the
entire binary ZIP to that destination server, extract it there, and follow
[INSTALL.md](INSTALL.md). Run `Install-InputLog.ps1` as Administrator **on the
destination server**. It creates a separate active configuration, backs up the
service command, switches the existing service, and supports rollback. The
installer defaults to `install`, `input-logs`, and `backups` beneath your current
working directory; its active configuration is kept under `backups`. Keep that
folder in place. The remaining setup steps in this section are the manual
alternative with explicitly selected Program Files/ProgramData locations.

The supplied x64 archive is an experimental, unsigned development build for
Windows. It is free to use. The logging source is included in the ISC-licensed
patch. No software has been installed and no running service has been changed.

Extract the archive's contents into a protected installation directory, for
example `C:\Program Files\OpenSSH-InputLog`. The examples below use that path.
Keep `crypto-57.dll` alongside the executables. The bundled crypto DLL includes
its C runtime. The Windows reader EXE includes its Python runtime, so no Python
installation is required to use the server or read recordings.

Use a matching set of the patched `sshd.exe`, `sshd-session.exe`, and
`sshd-auth.exe` binaries and their dependencies. The ordinary Windows OpenSSH
binaries do not recognize these new settings. Do not mix patched and stock
server components; their internal configuration format differs.

In PowerShell **as Administrator**, run the included helper to create a
dedicated directory accessible only to SYSTEM and Administrators:

```powershell
Set-Location 'C:\Program Files\OpenSSH-InputLog'
.\prepare-input-log-directory.ps1 -LogDirectory 'C:\ProgramData\ssh\input-logs'
```

Before editing the configuration, save the original and the existing service
command from an elevated PowerShell terminal:

```powershell
Copy-Item C:\ProgramData\ssh\sshd_config C:\ProgramData\ssh\sshd_config.before-inputlog
$service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
if (!$service) { throw 'An existing sshd service is required for the service-switch steps.' }
$service.PathName | Set-Content C:\ProgramData\ssh\sshd-service-path.before-inputlog.txt
```

Add the following **before the first `Match` block** in the server's
`C:\ProgramData\ssh\sshd_config`:

```text
SessionInputLogDirectory C:/ProgramData/ssh/input-logs
LogSessionInput yes
SshdSessionPath "C:/Program Files/OpenSSH-InputLog/sshd-session.exe"
SshdAuthPath "C:/Program Files/OpenSSH-InputLog/sshd-auth.exe"
```

`LogSessionInput` defaults to `no`. It can also be set inside `Match` blocks
to record selected users. The directory setting is global. The directory
must already exist, be a local absolute path, and must not be a junction or
symbolic link. Keep its parent directories protected from SSH users too.

Verify the configuration with the **patched** executable:

```powershell
.\sshd.exe -t -f C:\ProgramData\ssh\sshd_config
.\sshd.exe -T -f C:\ProgramData\ssh\sshd_config | Select-String 'logsessioninput|sessioninputlogdirectory'
```

For a side-by-side test before changing a running service, launch the
patched server from an elevated terminal on a separate port, with a copied
configuration and the recording options enabled. Permit that test port in
Windows Firewall and your Tailscale access policy. For example:

```powershell
.\sshd.exe -D -e -p 2222 -f C:\ProgramData\ssh\sshd_config
# On a client, substitute your Windows server's Tailscale IP:
ssh -p 2222 username@100.101.102.103
```

Test a shell command, a nested CMD or Python prompt, pasted multiline input,
and a non-interactive `ssh user@host command`. Check the resulting files.
Point the Windows service at the patched server only after that test succeeds.

### Use it with an existing Windows sshd service

Keep a local Administrator terminal open while testing the replacement. Use the
configuration and service-command backups made before editing in the steps above.

After adding the four global settings above and successfully testing on port
2222, stop the foreground test with Ctrl+C. Change the existing service's
executable and restart it from the elevated local terminal:

```powershell
$service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
$change = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{
    PathName = '"C:\Program Files\OpenSSH-InputLog\sshd.exe" -f "C:\ProgramData\ssh\sshd_config"'
}
if ($change.ReturnValue -ne 0) { throw "Service change failed: $($change.ReturnValue)" }
Restart-Service sshd
```

This uses the service's existing account and port/firewall/Tailscale settings.
The file permissions described here assume the usual LocalSystem service
account. A service using another account needs an explicitly designed log ACL.
Connect using your usual Tailscale IP and check the new `.jsonl` files.

To roll back, restore the saved service command and configuration, then restart:

```powershell
$service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
$change = Invoke-CimMethod -InputObject $service -MethodName Change -Arguments @{
    PathName = (Get-Content C:\ProgramData\ssh\sshd-service-path.before-inputlog.txt -Raw).Trim()
}
if ($change.ReturnValue -ne 0) { throw "Service restore failed: $($change.ReturnValue)" }
Copy-Item C:\ProgramData\ssh\sshd_config.before-inputlog C:\ProgramData\ssh\sshd_config -Force
Restart-Service sshd
```

## Files and readable output

Each authenticated SSH connection that starts a recorded shell/exec gets a uniquely named
`input-<monitor-pid>-<random>.jsonl` file. Multiple shell/exec channels on one
connection are distinguished by a recording channel ID. These IDs are unique
within the recording and are not SSH wire channel numbers.

The first record includes the authenticated Windows username, remote IP,
remote TCP port, and UTC time. Subsequent records contain a sequence number,
UTC time, channel ID, event type, byte count, and lossless hexadecimal bytes:

- `shell`: an interactive shell request;
- `exec`: the command string requested by a non-interactive SSH client;
- `effective-command`: a configured/key-forced command replacing that request;
- `stdin`: exact decrypted input bytes, before delivery to the shell/program;
- `close`: the session program exited or its recording channel was cleaned up.

Long payloads are split into records of at most 16 KiB. Concatenate `stdin`
bytes by channel and sequence to recover the exact input stream. Concatenate
`exec` or `effective-command` chunks to recover a long command string.

Read as Administrator. Double-click `read-input-log.exe` to open the graphical
viewer, select a recording, inspect its input, and export filtered text or a
channel's exact raw stdin. The Traditional Chinese GUI includes Unicode display,
search, channel/event filters, hexadecimal details, and pagination. Keep the
`read-input-log-runtime` directory alongside both EXEs; no Python installation
is needed. The console version is now `read-input-log-cli.exe`:

```powershell
.\read-input-log-cli.exe '.\input-logs\input-123-example.jsonl'
.\read-input-log-cli.exe '.\input-logs\input-123-example.jsonl' > readable.txt
# Recover exact input bytes for a selected channel into a NEW file:
.\read-input-log-cli.exe recording.jsonl --channel 1 --raw-stdin stdin.bin
```

Those reader examples use the automated installer's working-directory default.
Substitute your chosen log path if you used the manual configuration above.
The original `read-input-log.py` remains as a console entry point using the
shared `input_log_reader.py` module. See `README-READER.md` for GUI instructions
and the standalone Windows package.

Example decoded record:

```json
{"event":"stdin","time":"2026-11-18T10:00:00.000Z","sequence":1,"channel":1,"bytes":12,"text":"whoami /all\r"}
```

## What this records and its limits

The logger observes session input before shell-specific processing, so it
works with PowerShell, CMD, nested interpreters, and console applications.
It also records SSH `exec` command requests even when no stdin is supplied.
Bytes typed into password prompts *inside a session* are included. The SSH
authentication password is not recorded; recording starts after authentication.
Tell users their SSH input is recorded. Do not use these files as a password store.

This is raw input, not a claim that a command executed successfully. Backspace,
arrows, bracketed paste, binary bytes, and text inside editors are preserved.
Input generated internally by programs and commands loaded from existing files
are not client input. This extension does not record output, SFTP subsystem
requests, forwarding traffic, or sessions that happened before it was enabled.

The privileged monitor owns the files. Handles are not inherited by user
programs. Creation uses `CREATE_NEW`, random names, a protected DACL, and no
file-sharing for writes/deletes. Each record is flushed before acknowledging
the child; a recording failure stops the session before the corresponding
input is forwarded. A sudden crash can leave a missing close event or a partial
last JSON record, which the reader reports. There is no automatic log retention.

Recording is not tamper-proof against administrators, kernel-level access, or
someone able to alter the SSH session process. It does not replace maintaining
the custom build with upstream security fixes. The binaries are custom and
unsigned by Microsoft, and are not serviced by Windows Update.

## Source and build

Base repository: https://github.com/PowerShell/openssh-portable

Base commit: `0172cc6adb99d8b4324b8f0391b5b1fd6f39b2c6` (`latestw_all`,
OpenSSH for Windows 10.5 development source). The custom banner is
`Win32-OpenSSH-InputLog1` and the configuration blob version is incremented.

Apply `windows-input-recording.patch` to that exact source revision. Follow
Microsoft's Windows build instructions, including the C++ toolchain, Windows
SDK and crypto dependencies:

https://github.com/PowerShell/Win32-OpenSSH/wiki/Building-OpenSSH-for-Windows-(using-LibreSSL-crypto)

The base revision's x64 projects use platform toolset v145. A v143 build needs
consistent overrides for the projects and dependency triplets. The recorder's
new C source is part of `sshd-session.vcxproj`; other changes use existing
compilation units and do not require client-side changes.

Native writer tests: `regress/windows-input-log/writer-test.c` (MSVC, link
`bcrypt.lib` and `advapi32.lib`). Reader tests:

```powershell
py .\regress\windows-input-log\test-reader.py
```

For the writer tests, create a temporary local directory and pass its absolute
path to the test executable. The generated test files intentionally have
SYSTEM/Administrators-only DACLs. An administrator can then verify JSON round
trips, or the fixture owner can grant itself read access to those test files.

The input-recording addition is ISC-licensed. OpenSSH and dependency licenses
continue to apply; retain the included upstream license notices.

The supplied `BUILD-AND-TEST.md` describes the exact local build and completed
tests. Localhost tests were run as the current Windows user; service deployment
as LocalSystem and a connection through your Tailscale network still need to be
tested on the destination machine.
