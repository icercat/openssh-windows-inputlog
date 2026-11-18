# read-input-log.exe

Windows x64 console tool for reading OpenSSH InputLog1 recordings. Python is
bundled; you do not need to install Python on the machine where you run it.

Extract the complete ZIP. Keep these together:

```text
read-input-log.exe
read-input-log-runtime/
```

In PowerShell, run:

```powershell
.\read-input-log.exe '.\input-logs\input-123-example.jsonl'
.\read-input-log.exe '.\input-logs\input-123-example.jsonl' > readable.txt
.\read-input-log.exe recording.jsonl --channel 1
.\read-input-log.exe recording.jsonl --channel 1 --raw-stdin stdin.bin
.\read-input-log.exe --help
```

The first commands produce readable JSONL with escaped terminal controls and
Unicode escapes. The raw-input command reconstructs exact input bytes for the
selected channel and only creates a NEW file; it never overwrites an existing
file. The command syntax matches the optional `read-input-log.py` source version.

Use Administrator PowerShell to read live SYSTEM/Administrators-only recording
files on the server. The executable runs with the permissions of the terminal;
it does not elevate itself. Keep the executable and runtime in a trusted folder
protected from writes by other users. The server installer copies both into its
protected installation directory.

This is a command-line tool. Double-clicking without a recording filename only
shows argument help/errors; there is no graphical file picker. Exit status is
0 on success, 1 for recording/I/O errors, and 2 for argument errors.

Built for Windows x64 with Python 3.13.15 and PyInstaller 6.22.3 in folder-bundle
mode. No Python installation, pip command, or service installation is needed to
use this reader. The executable is a custom unsigned build. Included license
notices cover the bundled Python runtime and this recording-reader addition.

Source/build recipes: https://github.com/icercat/openssh-windows-inputlog
