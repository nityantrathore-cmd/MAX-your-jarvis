@echo off
REM Builds Max.exe  (run this from the project folder, with your .venv active)
REM Result: dist\Max.exe   (uses the icon maxAA.ico)
pip install pyinstaller pystray
set ICON=
set DATA=
if exist maxAA.ico set ICON=--icon maxAA.ico
if exist maxAA.ico set DATA=--add-data "maxAA.ico;."
pyinstaller --onefile --noconsole --name Max %ICON% %DATA% ^
  --collect-data speech_recognition ^
  --collect-data soundcard ^
  --collect-submodules screen_brightness_control ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL.ImageTk ^
  --hidden-import PIL._tkinter_finder ^
  gemini_assistant.py
echo.
echo Done. Your program is in the "dist" folder: dist\Max.exe
if not defined NOPAUSE pause
