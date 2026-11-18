# OpenSSH InputLog1: build and validation

Built on Windows x64 on 2026-11-18. This is an experimental source modification,
not an official Microsoft release. The archive is not code-signed.

## Inputs

- Repository: https://github.com/PowerShell/openssh-portable
- Revision: `0172cc6adb99d8b4324b8f0391b5b1fd6f39b2c6` on `latestw_all`
- Banner: `OpenSSH_for_Windows_10.5p1 Win32-OpenSSH-InputLog1`
- Visual Studio 2022 Community, MSVC 14.44.35207 (compiler 19.44.35229)
- Platform toolset v143, Windows SDK 10.0.26100.0, Release/x64
- LibreSSL 4.3.3, shared crypto DLL `crypto-57.dll`, static MSVC runtime (`/MT`)
- LibreSSL source: https://ftp.openbsd.org/pub/OpenBSD/LibreSSL/libressl-4.3.3.tar.gz
- LibreSSL source SHA512:
  `e18aa7016048aaec5988cedc3daea2f3f9c114760c0b7e15b18388ed3ab0a1a0386860a318a8cdbe961bb4369db28a9bf04f97b7fbafd45815693fd634`
- zlib 1.3.2, static library (`zs.lib`), source revision
  `da607da739fa6047df13e66a2af6b8bec7c2a498` from https://github.com/madler/zlib
- Windows reader: CPython 3.13.15 x64, PyInstaller 6.22.3; exact build-tool
  dependencies in `inputlog-build/reader-build-requirements.txt`.

The binary package includes matching `sshd.exe`, `sshd-session.exe`,
`sshd-auth.exe`, `ssh-shellhost.exe`, `sftp-server.exe`, `ssh-keygen.exe`,
`ssh.exe`, and `crypto-57.dll`. The client is optional; ordinary SSH clients work.
These server components must be kept together. The binary package includes
`Install-InputLog.ps1` for replacement of an existing LocalSystem sshd service
on the destination server; see `INSTALL.md`. It does not include ssh-agent.
`read-input-log.exe` and `read-input-log-runtime/` are included in both the
complete server package and a standalone `read-input-log-Windows-x64.zip`.
The reader does not require Python to be installed on the destination machine.

## Rebuild

`windows-input-recording.patch` applies to the revision above. The source zip
contains that revision with the patch already applied; it contains no Git
history, build outputs, test private keys, recordings, or local machine settings.

The `build-recipes` folder in the packages records the commands/overrides used
in this environment. The repository also has the same recipes in
`contrib/win32/openssh/inputlog-build`.
To use them, create a build workspace with this layout:

```text
build-workspace/
  work/
    openssh-portable/      # patched source (root contains servconf.c)
    libressl-4.3.3/        # extract the verified LibreSSL archive
    zlib/                 # clone and checkout the pinned zlib revision
    temp/                 # create an empty temporary directory
    build-native-deps.cmd # copy from build-recipes
    build-openssh.cmd     # copy from build-recipes
    build-reader.cmd      # copy from build-recipes; needs Python 3.13 x64 to build
    local-build.targets   # copy from build-recipes
```

Run from `build-workspace` in Command Prompt:

```bat
work\build-native-deps.cmd
work\build-openssh.cmd
work\build-reader.cmd
copy work\native-deps\bin\crypto-57.dll work\openssh-portable\bin\x64\Release\
xcopy "work\reader-dist\read-input-log\*" "work\openssh-portable\bin\x64\Release\" /E /I /Y
```

Adjust the Visual Studio installation/toolset paths if your machine differs.
The recipes disable vcpkg integration and use local dependency headers/libraries.
They override the base projects' v145/SDK requirements consistently, use embedded
compiler debug information, and omit linker PDB generation. They also define
`HAVE_EVP_DIGESTSIGN` and `HAVE_EVP_DIGESTVERIFY`, because LibreSSL 4.3.3 provides
those APIs. The recorded build disables Spectre-mitigated CRT selection; no
Spectre-mitigated libraries were available in this build environment. These
build overrides are separate from the logging feature patch. The recipes are
provided for rebuilding; bit-identical reproducibility has not been established.
LibreSSL is built with `USE_STATIC_MSVC_RUNTIMES=ON`; the final crypto DLL imports
only Windows system DLLs and does not require a separate VCRUNTIME140.dll.

The reader recipe creates a workspace-local Python virtual environment and
installs the pinned packaging dependencies from PyPI. It uses PyInstaller's
console/folder-bundle mode with `--contents-directory read-input-log-runtime`
and no UPX compression. Keep the EXE and runtime folder together. The runtime
is loaded from the protected installation directory, rather than extracting a
single-file archive each time privileged logs are read. See
[PyInstaller operating modes](https://pyinstaller.org/en/stable/operating-mode.html).
Python is a build dependency only; the compiled reader carries its own runtime.

## Completed checks

All following checks passed against the final executables:

- Compiled the complete x64 server/client executable set.
- `sshd -V`, config syntax validation, and `sshd -T` including the two new settings.
- Ten authenticated SSH integration cases on an ephemeral localhost port:
  1. Remote exec from the inbox Windows SSH client; exact requested command and close marker.
  2. Shell input and a nested CMD interpreter; exact input bytes.
  3. Python child copying stdin to stdout: all 256 byte values repeated, plus UTF-8;
     49,420 bytes matched byte-for-byte in both stdout and the recorded input.
  4. Forced PTY CMD session; real command output and recorded terminal input.
  5. Configured forced command; requested and effective commands both recorded.
  6. Logging off by default; no recording created.
  7. `Match User` opt-in; recording created.
  8. `Match User` opt-out; no recording created.
  9. SFTP subsystem protocol handshake; no input recording created.
  10. Missing recording directory; SSH exited 255, and the requested command's
      marker file was not created.
- Native writer tests compiled with MSVC `/W4 /WX /utf-8`: binary/UTF-8/control
  input, payload limits, invalid requests, unique filenames, non-inheritable
  handle, real write failure, and refusal of subsequent writes after failure.
- Actual newly created files had protected DACLs granting only SYSTEM and
  Builtin Administrators access. Fixture owners granted themselves read access
  only after this check so the test could inspect their recordings.
- Nine Python reader tests: binary bytes, split UTF-8 and separate channels,
  sequence validation, length mismatch, escaped terminal controls, truncated
  JSON, partial final character, empty file, and non-object JSON.
- Nine black-box EXE tests passed from a relocated package directory containing
  spaces, with Python removed from PATH and PYTHONHOME/PYTHONPATH pointing to a
  nonexistent directory: help, output matching the Python reader, Chinese text,
  separate channels, safe escaped controls, binary extraction, refusal to
  overwrite files, bad arguments, malformed recordings, and partial UTF-8 tails.
  This simulates missing external Python; a separate clean Windows VM was not
  used. No changes were made to the machine's installed Python.
- PowerShell directory helper parsed without syntax errors. Its elevated
  deployment path was not executed in this environment.
- Installer tests passed in Windows PowerShell 5.1 and PowerShell 7 using mock
  service/firewall operations and files confined to scratch directories:
  WhatIf, successful replacement, preserved config/flags, explicit rollback,
  startup-failure rollback, firewall-failure rollback, preflight failure,
  restoration of disabled/stopped state, refusal to overwrite a later service
  change, native Windows argument escaping, firewall selection, and CIM error
  handling. The real patched daemon's preflight was exercised, including
  detection of conflicting recording settings. Elevated installation was not
  executed on this PC.
  Reader/runtime copying, rejection of an incomplete reader package before
  changing the service, and the real reader EXE's preflight help command also
  passed in both PowerShell versions.
- Current-directory defaults were verified with a working folder containing
  spaces and the source package in another directory: binaries, logs, backups,
  and active configuration stay beneath that working folder, service paths are
  absolute, WhatIf creates nothing, and rollback works from another directory.
- Source patch whitespace check and application against the exact base revision.

Integration servers listened on 127.0.0.1 and ran as the current Windows user.
They used generated test keys and a scratch config, and were stopped afterward.
The existing service, host keys, Windows Firewall, Tailscale policies, and real
sshd_config were not modified. The test-only config used StrictModes=no; that
setting is not part of the deployment instructions.

LocalSystem service deployment, a real Tailscale connection, simultaneous
channels on one SSH connection, long-running load, disk-full recovery, and
upstream's full regression suite were not tested. There is no claim of a formal
security audit or resistance to a local administrator. The source is based on a
development branch; review and maintain upstream security fixes before relying
on this fork in production.

See `INPUT-RECORDING.md` for setup, scope, permissions, and rollback.

The portable installer regression test runs with mocked service/firewall
operations, using only the supplied scratch directory and daemon preflight:

```powershell
.\regress\windows-input-log\test-installer.ps1 -BinaryDirectory 'C:\build\work\openssh-portable\bin\x64\Release' -TestDirectory 'C:\build\work\test-scratch'
```

Use an absolute, writable scratch directory and set TEMP/TMP to a writable
location if your environment restricts temporary file access.

Executable reader checks (after `build-reader.cmd`):

```powershell
python .\regress\windows-input-log\test-reader-exe.py --reader 'C:\build\work\reader-dist\read-input-log\read-input-log.exe' --work-directory 'C:\build\work\test-scratch'
```
