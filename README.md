# Max - a voice assistant for your Windows PC (Gemini)

Say a wake word, give a command, and Max answers out loud and controls your PC:
mouse, keyboard, apps, websites, brightness and screenshots. Powered by the Google Gemini API.

> **Warning:** Max can click and type on your computer. Don't leave it running unattended,
> and don't ask it to do anything destructive. Use at your own risk.

## Requirements
- Windows 10/11 (uses Windows' built-in voice and microphone APIs)
- Python 3.10+
- A free Gemini API key: https://aistudio.google.com/apikey

## Setup
```
pip install -r requirements.txt
```
Create a file named `api_key.txt` next to `gemini_assistant.py` and paste your key in it
(one line, nothing else). This file is listed in `.gitignore`, so it won't be uploaded.
Alternatively set the `GEMINI_API_KEY` environment variable.

## Run
```
python gemini_assistant.py
```
Stay quiet for a second while it calibrates, then wait for "Max is ready".

## Use
- `hey max, open notepad`
- `max, type hello world`
- `hey max, click the start button on the screen`
- Say only `hey max`, wait for the beep, then speak your command.
- Stop: `hey max, stop listening` or press Ctrl+C.
- **Emergency stop:** slam the mouse into the top-left corner of the screen.

## Run in the background (no window)
Double-click `run_max_background.vbs`. Max starts hidden, shows a small tray icon near the clock
(right-click it: **Open log** / **Quit Max**), and writes all messages to `max.log` (in `%APPDATA%\Max`).
You can also say `hey max, stop listening` to quit.
To start Max automatically when Windows starts: press Win+R, type `shell:startup`, and put a
shortcut to `run_max_background.vbs` in that folder.
Note: while Max runs, he is always listening for his wake word, and short voice clips are sent to
Google's speech service to be recognised.

## Build a single Max.exe
1. Open a terminal in the project folder with your virtual environment active.
2. Run `build_exe.bat` (it installs PyInstaller and builds the program).
3. Your program is `dist\Max.exe`. Double-click it - no Python needed.

The first time it starts, a window asks for your Gemini API key (hidden as you type). The key is
checked with Google, then saved encrypted for your Windows account in `%APPDATA%\Max`, so you are
never asked again. To enter a different key, right-click the tray icon > **Forget API key and quit**,
or start `Max.exe --reset-key`.
The exe runs without a console window; messages go to `%APPDATA%\Max\max.log`.
Antivirus programs sometimes flag PyInstaller programs that control the mouse/keyboard; if that
happens, add an exception for `Max.exe`.

## How Max looks while running
- **Floating status pill** (bottom-right, above the taskbar): a glowing orb that reacts to your voice while
  he listens, a spinner while he thinks, equalizer bars while he talks, and rings while he works.
  It fades away when idle, ignores mouse clicks, and hides itself while he takes a screenshot.
  Turn it off with `OVERLAY = False`.
- **Tray icon** near the clock changes colour with his state (hover for the status text).

## Apps, locking and text mode
- **Open / close apps:** `hey max, open notepad`, `hey max, close chrome`. Closing works like clicking the X
  (programs may ask you to save). Max refuses to close Windows itself or his own program, and only closes
  something if your command contains a word like close / quit / exit / kill.
- **Lock the PC:** `hey max, lock my pc`. It only locks if your command contains the word "lock".
  Max never shuts down, restarts or signs out.
- **Text mode:** press **Ctrl+Alt+M** anywhere, type a command in the small box and press Enter.
  Change the keys with `TEXT_HOTKEY` (for example `"ctrl+shift+space"`), or set it to `""` to turn it off.
- **Loading window:** a small "Max is loading" window shows while he starts. Set `SHOW_LOADING_SCREEN = False`
  (near the top of the file) to turn it off.
- To quit by voice say `stop listening`, `goodbye`, `exit max` or `quit max`.

## Settings (top of `gemini_assistant.py`)
- `WAKE_WORDS` - add your own wake words, lowercase.
- **Microphone** - `MIC = "Conexant SmartAudio HD"` (top of `gemini_assistant.py`) picks a microphone by part of its name; if it is not found, Max falls back to the Windows default microphone. Use `MIC = None` to always use the default. All microphone names are saved in `microphones.txt` next to the program each time Max starts.
- `LANGUAGE` - speech recognition language, e.g. `en-US`, `en-IN`.
- `MODEL` - Gemini model. With `AUTO_PICK_MODEL = True` it tests a few at startup and uses the fastest.

## Troubleshooting
- `python net_test.py` checks your connection to Gemini and which models respond.
- `python voice_test.py` checks that Windows can speak.
- 503 = Google's model is busy (Max retries and switches models). 429 = free usage limit reached.

Speech recognition sends your voice clips to Google's free web speech service.
