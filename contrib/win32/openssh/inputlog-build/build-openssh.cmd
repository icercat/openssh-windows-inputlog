@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "TEMP=%CD%\work\temp"
set "TMP=%TEMP%"
set "MSBUILDDISABLENODEREUSE=1"
set "SolutionDir=%CD%\work\openssh-portable\contrib\win32\openssh\"
for %%P in (config openbsd_compat libssh win32iocompat sshd sshd-session sshd-auth keygen ssh-shellhost sftp-server ssh) do (
  echo Building %%P
  "C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe" "work\openssh-portable\contrib\win32\openssh\%%P.vcxproj" /t:Build /m:1 /nr:false /nologo /verbosity:minimal "/p:Configuration=Release;Platform=x64;PlatformToolset=v143;VCToolsInstallDir_170=C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.44.35207/;WindowsTargetPlatformVersion=10.0.26100.0;WindowsSDKVersion=10.0.26100.0;SpectreMitigation=false;VcpkgEnabled=false;UseVcpkg=false;DisableRegistryUse=true;WindowsSdkDir_10=;SSLLib=;ZLibName=zs.lib" "/p:ForceImportBeforeCppTargets=%CD%\work\local-build.targets"
  if errorlevel 1 exit /b 1
)
exit /b %ERRORLEVEL%
