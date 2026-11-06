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

## Settings (top of `gemini_assistant.py`)
- `WAKE_WORDS` - add your own wake words, lowercase.
- `MIC_NAME` - part of your microphone's name (the startup list shows the names), or `None` for the default.
- `LANGUAGE` - speech recognition language, e.g. `en-US`, `en-IN`.
- `MODEL` - Gemini model. With `AUTO_PICK_MODEL = True` it tests a few at startup and uses the fastest.

## Troubleshooting
- `python net_test.py` checks your connection to Gemini and which models respond.
- `python voice_test.py` checks that Windows can speak.
- 503 = Google's model is busy (Max retries and switches models). 429 = free usage limit reached.

Speech recognition sends your voice clips to Google's free web speech service.
