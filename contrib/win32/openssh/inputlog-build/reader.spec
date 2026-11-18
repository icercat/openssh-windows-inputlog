# Build the GUI and CLI from one dependency graph and shared runtime folder.
from pathlib import Path

source = Path(SPECPATH).parent
gui_source = source / 'read-input-log-gui.py'
cli_source = source / 'read-input-log.py'
analysis = Analysis([str(gui_source), str(cli_source)], pathex=[str(source)],
                    binaries=[], datas=[], hiddenimports=[], hookspath=[],
                    hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
archive = PYZ(analysis.pure)
entry_names = {'read-input-log-gui', 'read-input-log'}
hooks = [item for item in analysis.scripts if item[0] not in entry_names]
gui_entry = [item for item in analysis.scripts if item[0] == 'read-input-log-gui']
cli_entry = [item for item in analysis.scripts if item[0] == 'read-input-log']
assert len(gui_entry) == len(cli_entry) == 1
gui = EXE(archive, hooks + gui_entry, [], exclude_binaries=True,
          name='read-input-log', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False,
          contents_directory='read-input-log-runtime')
cli = EXE(archive, hooks + cli_entry, [], exclude_binaries=True,
          name='read-input-log-cli', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=True,
          contents_directory='read-input-log-runtime')
bundle = COLLECT(gui, cli, analysis.binaries, analysis.datas, strip=False,
                 upx=False, name='read-input-log')
