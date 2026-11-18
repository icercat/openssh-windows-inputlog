@echo off
rem Run from the build workspace documented in BUILD-AND-TEST.md.
if not exist "work\temp" mkdir "work\temp"
set "TEMP=%CD%\work\temp"
set "TMP=%TEMP%"
set "PYINSTALLER_CONFIG_DIR=%CD%\work\reader-build-cache"
if not exist "work\reader-build-venv\Scripts\python.exe" (
  python -m venv work\reader-build-venv
  if errorlevel 1 exit /b 1
)
"work\reader-build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input --index-url https://pypi.org/simple -r work\openssh-portable\contrib\win32\openssh\inputlog-build\reader-build-requirements.txt
if errorlevel 1 exit /b 1
"work\reader-build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --distpath work\reader-dist --workpath work\reader-build-gui work\openssh-portable\contrib\win32\openssh\inputlog-build\reader.spec
exit /b %ERRORLEVEL%
