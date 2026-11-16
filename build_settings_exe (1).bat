@echo off
REM Builds MaxSettings.exe - the separate Settings app (run from the project folder, with your .venv active)
REM Result: dist\MaxSettings.exe   (uses the icon maxSettingsAA.ico)
pip install pyinstaller
set ICON=
set DATA=
if exist maxSettingsAA.ico set ICON=--icon maxSettingsAA.ico
if exist maxSettingsAA.ico set DATA=--add-data "maxSettingsAA.ico;."
pyinstaller --onefile --noconsole --name MaxSettings %ICON% %DATA% ^
  --hidden-import PIL.ImageTk ^
  --hidden-import PIL._tkinter_finder ^
  max_settings.py
echo.
echo Done. Your Settings app is in the "dist" folder: dist\MaxSettings.exe
if not defined NOPAUSE pause
