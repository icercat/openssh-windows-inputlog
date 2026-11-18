@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
cl /nologo /W4 /WX /utf-8 /Fe"work\writer-test.exe" /Fo"work\writer-test.obj" "work\openssh-portable\regress\windows-input-log\writer-test.c" /link bcrypt.lib advapi32.lib
