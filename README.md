<img width="2752" height="1536" alt="Gemini_Generated_Image_vzrzluvzrzluvzrz" src="https://github.com/user-attachments/assets/104ad293-17eb-4692-aebe-754d01250ea5" />
<img width="2752" height="1536" alt="Gemini_Generated_Image_bft8brbft8brbft8" src="https://github.com/user-attachments/assets/87bcf001-6f96-4daf-b569-52cfb011036d" />
<img width="2752" height="1536" alt="Gemini_Generated_Image_25x75t25x75t25x7" src="https://github.com/user-attachments/assets/dc1a041a-b839-427f-a4f4-703495026e30" />
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
- Circles and curves are drawn with smooth (anti-aliased) edges using Pillow. If Pillow's Tk bridge (`PIL.ImageTk`)
  is missing, Max falls back to plain Tk shapes, which still work but have slightly jagged edges.
- The status pill, loading window and typing box are animated at about 60 frames per second with eased fades,
  slides and colour changes. If it ever feels heavy on a slow PC, set `OVERLAY = False` (or `SHOW_LOADING_SCREEN = False`).

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

## Emails and PC info
- **Draft an email:** `hey max, open gmail and draft a leave mail to the principal for vacations`.
  Max writes the letter and opens a Gmail "new message" window with the subject and text filled in.
  He never sends it - you check it, add the address if he didn't have it, and press Send.
  He won't invent an email address; say it if you want it filled in.
- **Specs:** `hey max, open task manager and tell me my specs` opens Task Manager and reads out your
  CPU, RAM, graphics card and disk space.

## Tasks: Max works by looking at the screen
For jobs with many steps, Max switches to task mode: he takes a screenshot, does 1-3 mouse/keyboard actions,
looks again, and repeats (up to `AGENT_MAX_STEPS`, default 30). Examples:
- `hey max, open Blender and add a cube, then scale it to double size`
- `hey max, order a margherita pizza from <your food app>` (the browser must already be logged in)
- `hey max, play a game of chess against the computer`

**Stopping a task:** press **Ctrl+Alt+X** (`STOP_HOTKEY`), slam the mouse into the top-left corner, or use
**Stop current task** in the tray menu.

**Safety rules built in**
- Steps that would place an order, pay, send, post, delete or install are held back until your original
  command contained a word like "place the order", "pay", "buy" or "send it". Otherwise Max stops at the
  checkout and tells you he is ready.
- Max never types card or account numbers, passwords or one-time codes. If a login or payment detail is
  needed he stops and tells you.
- Blocked shortcuts: Alt+F4, Win+R, Win+X, Ctrl+Alt+Del. Shutdown, restart and sign out are never allowed.
- The "risky" label comes from the AI itself, so it is a safety net, not a guarantee. Don't leave Max
  alone with accounts, saved payment methods or files you can't afford to lose.

**Limits:** every step takes a few seconds, so Max can only play slow or turn-based games (chess, cards,
idle/clicker games), not fast action games. Automating online multiplayer games can break their rules and
get your account banned. For harder tasks set `AGENT_MODEL` to a stronger Gemini model.

## More abilities
Everything below is stored on your PC in `%APPDATA%\Max` (memory.json, skills.json, schedules.json). Nothing is
encrypted there, so never ask Max to remember passwords, card numbers or codes (he refuses to).
- **Memory:** `hey max, remember that the principal's email is ...`, `forget the principal's email`,
  `what do you remember`. Facts are used automatically later (pre-addressed letters, your usual order).
  Saved facts are sent to Gemini with your requests.
- **Clipboard helper:** copy some text, then `hey max, fix the grammar of what I copied` / `translate this to Hindi` /
  `explain this`. The result is put on your clipboard; add `and paste it` to paste it for you.
  The copied text is sent to Gemini; Max only reads the clipboard when you mention what you copied.
- **Saved skills:** after Max opens apps/websites or drafts an email, say `save that as morning routine`.
  Later `run morning routine` repeats it instantly, without the AI. Skills can only contain opening apps and
  websites, email drafts and brightness (clicks and typing can't be replayed safely). `list my skills`,
  `delete skill morning routine`.
- **Screen watcher:** `hey max, tell me when the download finishes`. Max checks your screen every ~15 seconds
  (up to 30 minutes) and speaks when it happens. Say `stop watching` to cancel. Screenshots are sent to Gemini
  for every check, and each check uses one request of your free quota.
- **Study mode:** `hey max, start study mode for 1 hour`. If the active window looks like a distraction
  (YouTube, Instagram, games... edit `STUDY_DISTRACTIONS`), he reminds you out loud. He never closes anything.
  `stop study mode` ends it.
- **Scheduled routines:** `hey max, every day at 7:30 open gmail and tell me my specs`,
  `list my schedules`, `remove the gmail schedule`. They only run while Max is running, and scheduled runs may
  only open apps/websites, draft emails, read specs, take screenshots and change brightness.
- **Hindi + English:** set `REPLY_LANGUAGE = "hinglish"` (Hindi in English letters, any voice can read it) and,
  for Hindi speech, `LANGUAGE = "en-IN"` or `"hi-IN"`. `"hindi"` writes Devanagari and needs a Hindi voice
  installed in Windows.

## Make a Setup.exe installer
`build_installer.bat` builds a normal Windows installer, `installer\MaxSetup.exe`, that you can give to anyone.
1. Install **Inno Setup 6** (free): https://jrsoftware.org/isdl.php
2. Keep these files in one folder: `gemini_assistant.py`, `max_settings.py`, `maxAA.ico`, `maxSettingsAA.ico`, `README.md`,
   `MaxSetup.iss`, `installer_assets\`, `build_exe.bat`, `build_settings_exe.bat`, `build_installer.bat`.
3. In a terminal with your `.venv` active, run `build_installer.bat`. It builds `Max.exe` and `MaxSetup.exe`
   (the Settings app), then the installer. This takes a few minutes.

The installer shows a dark Max-styled wizard, installs for the current user (no admin password needed unless you pick
"all users"), adds Start Menu shortcuts for Max and Max Settings, and can add desktop shortcuts and "start Max when
Windows starts" (both off by default). The uninstaller removes everything and asks whether to also delete your saved
settings, key, memory and skills. Change the version number at the top of `MaxSetup.iss`.
Windows SmartScreen or your antivirus may warn about a new, unsigned installer; that is normal for programs
without a paid code-signing certificate.

## Settings app (separate from Max)
`max_settings.py` is its own program with its own window (or build `MaxSettings.exe` with `build_settings_exe.bat`).
It only edits a file, `%APPDATA%\Max\settings.json`, and Max checks that file every few seconds, so you can leave both
open. The top right of the Settings window shows whether Max is running and which AI model he uses.
- **Applied right away:** microphone, speech and reply language, voice speed and volume, wake words, AI model,
  beep, study-mode words, and the coding settings.
- **Applied the next time Max starts:** floating pill, loading screen, tray icon and the two hotkeys.
- Pages: General, Voice & language, Wake words, AI & account (change/forget the API key), Hotkeys, Coding,
  Study mode, Data (see and remove what Max remembers, your skills and schedules), About.
- Leaving the microphone or model on "(Max's own default)" keeps the value written in `gemini_assistant.py`.
- Two other small files let the apps talk: `status.json` (Max is running) and `devices.json` (your microphones).

## Coding helper
Ask Max to write code: `hey max, write a Python program that ...`. He saves the file in your projects folder
(default `Documents\Max Projects`, never overwriting an older file) and opens it in your editor (Notepad by default,
or VS Code / your own program - set it in the Settings app). To understand or fix code, copy it first and say
`explain what I copied` or `debug this`. Teaching style comes from the "Explain things at" setting (beginner,
intermediate, expert).
- Max **never runs** the code he writes, and opens it in an editor on purpose (double-clicking a `.py` file would run
  it). File names are cleaned, folders can't be escaped, and program-type extensions like `.exe` or `.bat` are replaced.
- Code, and any copied text, is sent to Gemini to answer you.

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

##the images are ai edited 
