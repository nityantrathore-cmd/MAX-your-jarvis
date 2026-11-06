#pls read hello.text frist!!
#
# Install:
#     pip install requests SpeechRecognition soundcard numpy pyautogui pillow mss screen-brightness-control
#
# Say:  "hey max, open notepad"  /  "hey max, click the start button"
# Stop: say "hey max, stop listening"  (or press Ctrl+C)
# Emergency stop: slam the mouse into the TOP-LEFT corner of the screen.

import base64
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import webbrowser
from collections import deque

import numpy as np
import pyautogui
import requests
import soundcard as sc
import speech_recognition as sr

warnings.filterwarnings("ignore", message="data discontinuity")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # match real screen pixels on scaled displays
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import winsound  # Windows only

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None

# ---------------- Config ----------------
API_KEY = "past your api key here"  # free key: https://aistudio.google.com/apikey
MODEL = "gemini-3.1-flash-lite"  # fastest in your test. (gemini-3.8-flash was overloaded: 503)
BASE = "https://generativelanguage.googleapis.com/v1beta"
FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]  # used if the main model is busy
THINK_OPTIONS = [{"thinkingLevel": "minimal"}, {"thinkingBudget": 0}, None]  # tries to turn thinking off (faster)
think_i = [0]
# ---- Wake words: add your own here, in lowercase, inside the quotes ----
WAKE_WORDS = ["max", "macs", "mack", "maxx", "mex","hemax","marks","famous","mx"]  # work on their own: "max, open notepad"
PREFIX_ONLY_WORDS = ["mac"]  # only count after a prefix: "hey mac"
WAKE_PREFIXES = ["hey", "ok", "okay", "hi", "hello"]  # optional words before the name
WAKE_BEEP = True  # short beep when Max hears his name, so you know when to talk
MAX_ACTIONS = 10  # safety cap per command
SCREEN_WORDS = ("screen", "click", "look at", "what's on", "on my display")  # only these send a screenshot (slower)

MIC_NAME = "Conexant SmartAudio HD"  # matches: Microphone (Conexant SmartAudio HD)
LANGUAGE = "en-US"  # speech recognition language (e.g. "en-IN", "hi-IN")
VOICE_MODE = "fast"  # "fast" = keeps the voice loaded (quickest), "wav" or "direct" = slower fallbacks
FORCE_IPV4 = True  # fixes many "no response" timeouts on Windows (broken IPv6). Set False if you need IPv6
IGNORE_PROXY = False  # set True if a Windows proxy/VPN setting is breaking the connection
AUTO_PICK_MODEL = True  # at startup, test several Gemini models and use the fastest one that answers
DEBUG = True  # prints what the mic hears so you can see what's going on

if API_KEY.startswith("PASTE"):
    sys.exit("Paste your Gemini key into API_KEY at the top of the file.")

pyautogui.FAILSAFE = True  # mouse to top-left corner = emergency stop
pyautogui.PAUSE = 0.15

HEADERS = {"x-goog-api-key": API_KEY, "Content-Type": "application/json"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.trust_env = not IGNORE_PROXY
if FORCE_IPV4:
    import socket
    import urllib3.util.connection as _urllib3_connection

    _urllib3_connection.allowed_gai_family = lambda: socket.AF_INET

SYSTEM_PROMPT = "\n".join([
    "You are Max, a voice assistant that controls the user's Windows PC.",
    "Reply with ONLY a JSON object, no markdown:",
    "{\"say\": \"short spoken reply\", \"actions\": [ ... ]}",
    "",
    "Allowed actions:",
    "{\"type\":\"move\",\"x\":int,\"y\":int}",
    "{\"type\":\"click\",\"x\":int,\"y\":int,\"button\":\"left|right\",\"clicks\":1}   (x,y optional = current position)",
    "{\"type\":\"scroll\",\"amount\":int}              (positive up, negative down)",
    "{\"type\":\"type\",\"text\":\"...\"}",
    "{\"type\":\"press\",\"key\":\"enter\"}",
    "{\"type\":\"hotkey\",\"keys\":[\"ctrl\",\"c\"]}",
    "{\"type\":\"open_app\",\"name\":\"notepad\"}",
    "{\"type\":\"open_url\",\"url\":\"https://...\"}",
    "{\"type\":\"screenshot\"}",
    "{\"type\":\"brightness\",\"level\":0-100}",
    "{\"type\":\"wait\",\"seconds\":1}",
    "",
    "Rules: keep \"say\" under 12 words, natural and quick. If a screenshot is attached, coordinates",
    "must be in that screenshot's pixel space. Use [] for actions if only chatting.",
    "Never do anything destructive (deleting files, formatting, payments) without",
    "the user clearly asking for it.",
])

# ---------------- Microphone ----------------
RATE = 16000
CHUNK_SEC = 0.1
THRESHOLD = 0.01
NOISE = 0.004  # running estimate of the background noise
recognizer = sr.Recognizer()
mic = None
rec = None


def open_mic():
    # Open the microphone once and keep it open (so no speech is missed between phrases).
    global mic, rec
    print("Microphones found:")
    for m in sc.all_microphones():
        print(f"   - {m.name}")
    mic = sc.get_microphone(MIC_NAME) if MIC_NAME else sc.default_microphone()
    print(f"Using: {mic.name}")
    rec = mic.recorder(samplerate=RATE, channels=1)
    rec.__enter__()


def calibrate(seconds=1.0):
    # Measure background noise so we know what counts as speech.
    global NOISE, THRESHOLD
    data = rec.record(numframes=int(RATE * seconds))[:, 0]
    NOISE = float(np.sqrt(np.mean(data ** 2)))
    THRESHOLD = max(NOISE * 2.5, 0.008)
    print(f"Background level: {NOISE:.4f}  ->  speech threshold: {THRESHOLD:.4f}")


def _powershell(cmd, **extra_env):
    env = {**os.environ, **extra_env}
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          env=env, capture_output=True, text=True,
                          creationflags=0x08000000)  # no console window


def _speak_wav(text):
    # Make a WAV file with Windows' voice, then play it with Python's winsound.
    wav = os.path.join(tempfile.gettempdir(), "max_voice.wav")
    cmd = ("Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
           "$s.SetOutputToWaveFile($env:SPEAK_WAV); "
           "$s.Speak($env:SPEAK_TEXT); $s.Dispose()")
    r = _powershell(cmd, SPEAK_TEXT=text, SPEAK_WAV=wav)
    if r.returncode != 0 or not os.path.exists(wav):
        raise RuntimeError(r.stderr.strip()[:300] or "could not create the voice file")
    winsound.PlaySound(wav, winsound.SND_FILENAME)  # waits until finished


def _speak_direct(text):
    cmd = ("Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
           "$s.Volume = 100; $s.SetOutputToDefaultAudioDevice(); "
           "$s.Speak($env:SPEAK_TEXT)")
    r = _powershell(cmd, SPEAK_TEXT=text)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "PowerShell speech failed")


_voice = None


def _start_voice():
    # Start one PowerShell that keeps the Windows voice loaded, so speaking starts instantly.
    global _voice
    script = ("Add-Type -AssemblyName System.Speech; "
              "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Volume = 100; "
              "while (($l = [Console]::In.ReadLine()) -ne $null) "
              "{ $s.Speak($l); [Console]::Out.WriteLine('done'); [Console]::Out.Flush() }")
    _voice = subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                              creationflags=0x08000000)


def _speak_fast(text):
    global _voice
    try:
        if _voice is None or _voice.poll() is not None:
            _start_voice()
        _voice.stdin.write(" ".join(text.split()) + "\n")
        _voice.stdin.flush()
        if _voice.stdout.readline() == "":
            raise RuntimeError("voice process closed")
    except Exception:
        _voice = None  # restart it next time
        raise


def speak(text):
    # Speak with Windows' built-in voice. Tries up to three methods so one of them works.
    print(f"Max: {text}")
    order = {"fast": [_speak_fast, _speak_wav, _speak_direct],
             "wav": [_speak_wav, _speak_direct],
             "direct": [_speak_direct, _speak_wav]}[VOICE_MODE]
    for method in order:
        try:
            method(text)
            break
        except Exception as e:
            print(f"  [Voice error in {method.__name__}] {e}")
    if rec:
        rec.flush()  # throw away audio of Max's own voice


def record_phrase(timeout=5, limit=8, silence=0.6):
    # Wait for speech, record until you stop talking. Returns AudioData or None.
    global NOISE, THRESHOLD
    chunk = int(RATE * CHUNK_SEC)
    preroll = deque(maxlen=5)  # keeps the half second before speech starts
    frames, started, quiet, waited, loud_run, voiced = [], False, 0.0, 0.0, 0, 0

    while True:
        data = rec.record(numframes=chunk)[:, 0]
        level = float(np.sqrt(np.mean(data ** 2)))
        loud = level > THRESHOLD

        if not started:
            preroll.append(data)
            loud_run = loud_run + 1 if loud else 0
            if not loud:  # adapt to changing background noise
                NOISE = 0.95 * NOISE + 0.05 * level
                THRESHOLD = max(NOISE * 2.5, 0.008)
            if loud_run >= 2:  # 2 loud chunks in a row = speech, not a click
                started, voiced = True, 2
                frames.extend(preroll)
                if DEBUG:
                    print("  (voice detected...)")
            else:
                waited += CHUNK_SEC
                if waited >= timeout:
                    return None
            continue

        frames.append(data)
        if loud:
            voiced += 1
            quiet = 0.0
        else:
            quiet += CHUNK_SEC
        if quiet >= silence or len(frames) * CHUNK_SEC >= limit:
            break

    if voiced < 3:  # under 0.3s of sound = cough, click, bump
        return None
    pcm = (np.clip(np.concatenate(frames), -1, 1) * 32767).astype(np.int16).tobytes()
    return sr.AudioData(pcm, RATE, 2)


def listen(timeout=5, limit=8):
    # Returns (best guess, all alternative guesses). ("", []) if nothing was understood.
    audio = record_phrase(timeout, limit)
    if audio is None:
        return "", []
    try:
        result = recognizer.recognize_google(audio, language=LANGUAGE, show_all=True)
    except sr.UnknownValueError:
        result = None
    except sr.RequestError as e:
        print(f"  [Speech service error - check your internet] {e}")
        return "", []
    alts = []
    if isinstance(result, dict):
        alts = [a["transcript"].lower() for a in result.get("alternative", []) if a.get("transcript")]
    if not alts:
        if DEBUG:
            print("  (couldn't understand that)")
        return "", []
    if DEBUG:
        print(f"  heard: {alts[0]}")
    return alts[0], alts


def build_wake_regex():
    # Builds the wake word pattern from the WAKE_WORDS lists at the top of the file.
    def alt(words):
        return "|".join(re.escape(w.lower()) for w in sorted(words, key=len, reverse=True))

    prefix = rf"(?:{alt(WAKE_PREFIXES)})\s+"
    pattern = rf"\b(?:{prefix})?(?:{alt(WAKE_WORDS)})\b"
    if PREFIX_ONLY_WORDS:
        pattern += rf"|\b{prefix}(?:{alt(PREFIX_ONLY_WORDS)})\b"
    return re.compile(pattern)


WAKE_REGEX = build_wake_regex()


def find_wake(texts):
    # Looks for the wake word in the best guess AND every alternative guess.
    # Returns the command spoken after it ("" if none), or None if there was no wake word.
    for text in texts:
        m = WAKE_REGEX.search(text)
        if m:
            return text[m.end():].strip(" ,.!?")
    return None


def beep():
    if WAKE_BEEP:
        try:
            winsound.Beep(1000, 120)
        except Exception:
            pass
        if rec:
            rec.flush()  # don't record our own beep


# ---------------- Screen ----------------
SCALE = 1.0


def grab_screen():
    # Capture the screen with Pillow (or mss), NOT pyautogui/pyscreeze, which break on new Python versions.
    first_error = None
    try:
        from PIL import ImageGrab
        return ImageGrab.grab().convert("RGB")
    except Exception as e:
        first_error = e
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception as e:
        raise RuntimeError(f"Cannot capture the screen ({first_error} / {e}). "
                           "Try: pip install --upgrade pillow mss")


def screenshot_b64():
    # Take a screenshot, shrink it, return base64 JPEG + its size.
    global SCALE
    img = grab_screen()
    screen_w = pyautogui.size()[0]
    if img.width > 1024:
        img = img.resize((1024, int(img.height * 1024 / img.width)))
    SCALE = screen_w / img.width  # converts screenshot coordinates to real screen coordinates
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode(), img.size


def check_screen():
    try:
        img = grab_screen()
        w, h = pyautogui.size()
        print(f"Screen access OK (screenshot {img.width}x{img.height}, mouse area {w}x{h})")
    except Exception as e:
        print(f"[Screen problem] {e}")


# ---------------- Gemini ----------------
history = []  # list of {"role": "user"/"model", "parts": [{"text": ...}]}


def pick_model():
    # Ask Google which models exist and switch to a working Flash model.
    global MODEL
    r = SESSION.get(f"{BASE}/models", params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = sorted(n for n in names if n.startswith("gemini") and "flash" in n
                   and not any(x in n for x in ("lite", "image", "tts", "live", "audio", "thinking")))
    if not flash:
        sys.exit("No usable Gemini model found for your key.")
    MODEL = "gemini-flash-latest" if "gemini-flash-latest" in flash else flash[-1]
    print(f"[Model not found - switched to {MODEL}]")


def ask_gemini(command):
    parts = [{"text": command}]
    if any(w in command for w in SCREEN_WORDS):
        b64, (w, h) = screenshot_b64()
        parts[0]["text"] = f"{command}\n(Screenshot attached, {w}x{h} pixels.)"
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64}})

    gen_config = {"responseMimeType": "application/json", "maxOutputTokens": 600}
    if THINK_OPTIONS[think_i[0]]:
        gen_config["thinkingConfig"] = THINK_OPTIONS[think_i[0]]

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history[-10:] + [{"role": "user", "parts": parts}],
        "generationConfig": gen_config,
    }

    def post(model):
        return SESSION.post(f"{BASE}/models/{model}:generateContent",
                            json=body, timeout=(5, 25))  # 5s to connect, 25s to answer

    def try_post(model):
        try:
            return post(model)
        except requests.exceptions.RequestException as e:
            print(f"[Network problem: {type(e).__name__}] {str(e)[:160]}")
            return None

    busy = (429, 500, 502, 503, 504)
    r = None
    for model in [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]:
        for attempt in range(2):
            r = try_post(model)
            if r is not None and r.status_code not in busy:
                break
            wait = 2 * (attempt + 1)
            why = "no response" if r is None else r.status_code
            print(f"[Gemini busy ({why}) - retrying in {wait}s]")
            time.sleep(wait)
        if r is None or r.status_code not in busy:
            break  # got an answer, or it's a network problem (other models won't help)
        print(f"[{model} is overloaded - trying another model]")

    if r is None:
        raise RuntimeError("Gemini did not respond (network timeout). "
                           "Check your internet, VPN, firewall or antivirus.")

    if r.status_code == 404:  # model name doesn't exist -> pick a real one
        pick_model()
        r = post(MODEL)
    while (r.status_code == 400 and "think" in r.text.lower()
           and think_i[0] < len(THINK_OPTIONS) - 1):  # model dislikes that thinking setting
        think_i[0] += 1
        gen_config.pop("thinkingConfig", None)
        if THINK_OPTIONS[think_i[0]]:
            gen_config["thinkingConfig"] = THINK_OPTIONS[think_i[0]]
        r = post(MODEL)
    if not r.ok:
        print(f"[Gemini says] {r.text[:400]}")
        r.raise_for_status()
    reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]

    history.append({"role": "user", "parts": [{"text": command}]})  # text only, no images
    history.append({"role": "model", "parts": [{"text": reply}]})

    try:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        return {"say": reply, "actions": []}


# ---------------- PC control ----------------
def xy(a):
    return int(a["x"] * SCALE), int(a["y"] * SCALE)


def run_action(a):
    t = a.get("type")
    if t == "move":
        x, y = xy(a)
        pyautogui.moveTo(x, y, duration=0.3)
    elif t == "click":
        kw = {"button": a.get("button", "left"), "clicks": int(a.get("clicks", 1))}
        if "x" in a and "y" in a:
            kw["x"], kw["y"] = xy(a)
            pyautogui.moveTo(kw["x"], kw["y"], duration=0.3)
        pyautogui.click(**kw)
    elif t == "scroll":
        pyautogui.scroll(int(a["amount"]) * 100)
    elif t == "type":
        pyautogui.write(a["text"], interval=0.03)
    elif t == "press":
        pyautogui.press(a["key"])
    elif t == "hotkey":
        pyautogui.hotkey(*a["keys"])
    elif t == "open_app":
        name = a["name"]
        if re.fullmatch(r"[\w .\-]+", name):  # block shell tricks
            subprocess.Popen(["cmd", "/c", "start", "", name])
    elif t == "open_url":
        if a["url"].startswith(("http://", "https://")):
            webbrowser.open(a["url"])
    elif t == "screenshot":
        grab_screen().save(f"screenshot_{int(time.time())}.png")
    elif t == "brightness" and sbc:
        sbc.set_brightness(max(0, min(100, int(a["level"]))))
    elif t == "wait":
        time.sleep(min(float(a.get("seconds", 1)), 10))


def handle(command):
    t0 = time.time()
    try:
        result = ask_gemini(command)
    except Exception as e:
        print(f"[Gemini error] {e}")
        speak("Sorry, I couldn't reach Gemini.")
        return
    if DEBUG:
        print(f"  [Gemini answered in {time.time() - t0:.1f}s]")

    talker = None
    if result.get("say"):  # talk and act at the same time
        talker = threading.Thread(target=speak, args=(result["say"],))
        talker.start()
    for action in result.get("actions", [])[:MAX_ACTIONS]:
        try:
            run_action(action)
        except pyautogui.FailSafeException:
            speak("Emergency stop.")
            break
        except Exception as e:
            print(f"[Action error] {action}: {e}")
    if talker:
        talker.join()


# ---------------- Main loop ----------------
def auto_pick_model():
    # Ask several models a tiny question at the same time and keep the fastest one that answers.
    global MODEL
    extra = ["gemini-3.5-flash", "gemini-3.8-flash"]
    candidates = []
    for m in [MODEL] + FALLBACK_MODELS + extra:
        if m not in candidates:
            candidates.append(m)
    results = {}

    def probe(m):
        t = time.time()
        try:
            r = SESSION.post(f"{BASE}/models/{m}:generateContent", timeout=(5, 20),
                             json={"contents": [{"role": "user", "parts": [{"text": "Say OK"}]}],
                                   "generationConfig": {"maxOutputTokens": 20}})
            if r.status_code == 200:
                results[m] = time.time() - t
                print(f"   {m}: answered in {results[m]:.1f}s")
            else:
                print(f"   {m}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"   {m}: no answer ({type(e).__name__})")

    print("Testing Gemini models (a few seconds)...")
    threads = [threading.Thread(target=probe, args=(m,)) for m in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if results:
        MODEL = min(results, key=results.get)
        print(f"Using model: {MODEL}")
    else:
        print("[No Gemini model answered. Run net_test.py and paste the output to get help.]")


def check_gemini():
    # Quick test at startup so you know right away if Gemini is reachable.
    t = time.time()
    try:
        r = SESSION.get(f"{BASE}/models", params={"pageSize": 1}, timeout=(5, 15))
        if r.ok:
            print(f"Gemini connection OK ({time.time() - t:.1f}s)")
        elif r.status_code in (400, 401, 403):
            print(f"[Gemini rejected your API key ({r.status_code})] Create a new key at aistudio.google.com/apikey")
        else:
            print(f"[Gemini returned {r.status_code}] {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        print(f"[Cannot reach Gemini: {type(e).__name__}] Check your internet, VPN, firewall or antivirus.")


def main():
    check_gemini()
    check_screen()
    if AUTO_PICK_MODEL:
        auto_pick_model()
    open_mic()
    print("Calibrating microphone (stay quiet for a second)...")
    calibrate()
    if VOICE_MODE == "fast":
        try:
            _start_voice()
        except Exception as e:
            print(f"  [Voice warm-up failed] {e}")
    speak("Max is ready. Say hey max to wake me.")

    try:
        while True:
            heard, alts = listen(timeout=5, limit=8)
            if not heard:
                continue

            command = find_wake(alts)
            if command is None:
                continue  # not for Max
            if command == "":
                beep()  # "I'm listening" beep, then take the command
                command, _ = listen(timeout=6, limit=10)
                if not command:
                    continue

            print(f"You: {command}")
            if any(p in command for p in ("stop listening", "exit", "goodbye")):
                speak("Goodbye.")
                break

            handle(command)
    except KeyboardInterrupt:
        pass
    finally:
        rec.__exit__(None, None, None)
        if _voice and _voice.poll() is None:
            _voice.terminate()


if __name__ == "__main__":
    main()
