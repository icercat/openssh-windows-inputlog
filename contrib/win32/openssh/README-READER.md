# SSH 輸入紀錄檢視器

Windows x64 graphical viewer for OpenSSH InputLog1 recordings, with a Traditional
Chinese interface. Python and Tcl/Tk are bundled; no Python installation is needed.

Extract the complete ZIP. Keep these together:

```text
read-input-log.exe
read-input-log-cli.exe
read-input-log-runtime/
```

## 圖形介面 / GUI

Double-click **read-input-log.exe**. No terminal command is needed.

1. Click **開啟紀錄檔…** to choose a `.jsonl` recording.
2. The top section shows the authenticated user, source address and UTC start time.
3. Select a row to inspect its decoded input or the **原始位元組** hexadecimal view.
4. Filter by **頻道**, **事件**, or **搜尋**. Chinese text is displayed directly;
   terminal controls, non-printable marks and backslashes use visible escapes.
5. **匯出篩選結果為文字…** writes all filtered events, including other pages, to
   a new UTF-8 text file. **匯出頻道原始輸入…** writes the entire selected channel's
   stdin snapshot, regardless of the event/search filter, to a new binary file.

To browse multiple recordings, click **開啟資料夾…** and switch between
`.jsonl` files with the **紀錄檔** dropdown. The list shows files directly
in the selected folder, newest modified first. Only the selected file is
loaded. **重新讀取** rescans the folder and reloads the selected file.

PowerShell `-EncodedCommand` payloads are decoded as UTF-16LE and shown in
the **Base64 解碼** tab. The viewer joins input fragments within each channel
up to a line ending and displays the result on the final event of that line.
Decoded commands are searchable and included in text exports. The original
input remains available in the **輸入內容** and **原始位元組** tabs.
For a pasted command or standalone Base64 text, use **Base64 解碼…** in the
toolbar. For standalone Base64, choose UTF-8 or UTF-16LE before decoding.

Neither export overwrites an existing file. To export raw input, first select
one channel in the channel dropdown. No recorded command is executed by the viewer.

Use **重新讀取** or F5 to load a fresh snapshot if the server is still appending.
Exports refer to the snapshot currently displayed. Keyboard shortcuts: Ctrl+O
opens a file and Ctrl+F focuses search. A filename can also be passed to the GUI:

```powershell
.\read-input-log.exe '.\input-logs\input-123-example.jsonl'
```

The GUI displays 500 events per page and loads at most 64 MiB / 100,000 source
events per file. Use the CLI for larger recordings. The data remains raw client
input, including edits and control keys; it is not proof that a command executed.

## 命令列 / CLI

The previous console functionality is now **read-input-log-cli.exe**:

```powershell
.\read-input-log-cli.exe '.\input-logs\input-123-example.jsonl'
.\read-input-log-cli.exe '.\input-logs\input-123-example.jsonl' > readable.txt
.\read-input-log-cli.exe recording.jsonl --channel 1
.\read-input-log-cli.exe recording.jsonl --channel 1 --raw-stdin stdin.bin
.\read-input-log-cli.exe --help
```

The first commands produce readable JSONL with escaped terminal controls and
Unicode escapes. The raw-input command reconstructs exact input bytes for the
selected channel and only creates a NEW file; it never overwrites an existing
file. The command syntax matches the optional `read-input-log.py` source version.

For live SYSTEM/Administrators-only recording files, right-click the GUI and
choose Run as administrator, or start the CLI in Administrator PowerShell.
Neither executable elevates itself automatically. Keep the executables and runtime in a trusted folder
protected from writes by other users. The server installer copies both into its
protected installation directory.

CLI exit status is 0 on success, 1 for recording/I/O errors, and 2 for argument
errors. Python source entry points are `read-input-log-gui.py` for the GUI and
`read-input-log.py` for the CLI; keep the shared Python modules alongside them.

Built for Windows x64 with Python 3.13.15 and PyInstaller 6.22.3 in folder-bundle
mode with two executables sharing one runtime. No Python installation, pip command, or service installation is needed to
use this reader. The executable is a custom unsigned build. Included license
notices cover the bundled Python runtime and this recording-reader addition.

Source/build recipes: https://github.com/icercat/openssh-windows-inputlog
