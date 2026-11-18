@echo off
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul
if errorlevel 1 exit /b 1
set "TEMP=%CD%\work\temp"
set "TMP=%TEMP%"
set "TASK_CMAKE=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
set "TASK_NINJA=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
"%TASK_CMAKE%" -S work\libressl-4.3.3 -B work\libressl-ninja -G Ninja "-DCMAKE_MAKE_PROGRAM=%TASK_NINJA%" -DCMAKE_BUILD_TYPE=Release -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded "-DCMAKE_C_FLAGS_DEBUG=/Od /MDd" "-DCMAKE_EXE_LINKER_FLAGS_DEBUG=/INCREMENTAL:NO" -DLIBRESSL_APPS=OFF -DLIBRESSL_TESTS=OFF -DBUILD_SHARED_LIBS=ON -DUSE_STATIC_MSVC_RUNTIMES=ON "-DCMAKE_INSTALL_PREFIX=%CD%/work/native-deps"
if errorlevel 1 exit /b 1
"%TASK_CMAKE%" --build work\libressl-ninja --parallel 6
if errorlevel 1 exit /b 1
"%TASK_CMAKE%" --install work\libressl-ninja
if errorlevel 1 exit /b 1
"%TASK_CMAKE%" -S work\zlib -B work\zlib-ninja -G Ninja "-DCMAKE_MAKE_PROGRAM=%TASK_NINJA%" -DCMAKE_BUILD_TYPE=Release -DCMAKE_MSVC_DEBUG_INFORMATION_FORMAT=Embedded "-DCMAKE_C_FLAGS_DEBUG=/Od /MDd" "-DCMAKE_EXE_LINKER_FLAGS_DEBUG=/INCREMENTAL:NO" -DZLIB_BUILD_TESTING=OFF -DZLIB_BUILD_SHARED=OFF -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded "-DCMAKE_INSTALL_PREFIX=%CD%/work/native-deps"
if errorlevel 1 exit /b 1
"%TASK_CMAKE%" --build work\zlib-ninja --parallel 6
if errorlevel 1 exit /b 1
"%TASK_CMAKE%" --install work\zlib-ninja
exit /b %ERRORLEVEL%
