@echo off
REM Builds the installer:  installer\MaxSetup.exe
REM Run this from the project folder with your .venv active.
REM It needs Inno Setup 6 (free): https://jrsoftware.org/isdl.php
set NOPAUSE=1

call build_exe.bat
if not exist dist\Max.exe goto fail
call build_settings_exe.bat
if not exist dist\MaxSettings.exe goto fail

set "P1=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
set "P2=%ProgramFiles%\Inno Setup 6\ISCC.exe"
set "P3=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
set "ISCC="
for %%P in ("%P1%" "%P2%" "%P3%") do if exist %%P set "ISCC=%%~P"
if "%ISCC%"=="" goto noinno

"%ISCC%" MaxSetup.iss
if errorlevel 1 goto fail
echo.
echo Done! Your installer is: installer\MaxSetup.exe
explorer installer
pause
exit /b 0

:noinno
echo.
echo Inno Setup 6 was not found. Install it (free), then run this file again:
echo https://jrsoftware.org/isdl.php
start https://jrsoftware.org/isdl.php
pause
exit /b 1

:fail
echo.
echo Something went wrong. Read the messages above, and send them to me if you need help.
pause
exit /b 1
