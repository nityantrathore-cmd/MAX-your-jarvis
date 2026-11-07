# Max voice assistant (Gemini): wake word + listening + speaking + PC control.
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
import math
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import webbrowser
from collections import deque
from ctypes import wintypes

# ---------------- Small "Max is loading" window (starts first, before the slow imports) ----------------
SHOW_LOADING_SCREEN = True      # False = no loading window at startup
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)     # sharp window on scaled displays
except Exception:
    pass
_splash = {"gen": 0, "text": "Loading...", "close_at": None}


def splash_status(text):
    _splash["text"] = text


def splash_close(delay=0.0):
    _splash["close_at"] = time.time() + delay


def _splash_thread(gen):
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        return
    KEY = "#010203"                                       # this colour becomes see-through
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", KEY)
    except Exception:
        pass
    root.attributes("-alpha", 0.0)
    root.configure(bg=KEY)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = max(1.0, min(2.5, sh / 1080))

    def s(v):
        return int(v * scale)

    W, H = s(290), s(96)
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
    cv = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
    cv.pack()
    accent, dim = "#00be87", "#14543f"
    born = time.time()

    def tick():
        now = time.time()
        due = _splash["close_at"]
        if _splash["gen"] != gen or (due is not None and now >= due) or now - born > 90:
            root.destroy()
            return
        root.attributes("-alpha", min(0.96, (now - born) * 4))            # quick fade-in
        cv.delete("all")
        r = s(26)
        pts = [r, 2, W - r, 2, W - 2, 2, W - 2, r, W - 2, H - r, W - 2, H - 2, W - r, H - 2,
               r, H - 2, 2, H - 2, 2, H - r, 2, r, 2, 2]
        cv.create_polygon(pts, smooth=True, fill="#12141a", outline=dim, width=s(2))
        cx, cy = s(48), H // 2
        rr = s(19)
        cv.create_arc(cx - rr, cy - rr, cx + rr, cy + rr, start=(now * 330) % 360, extent=250,
                      style="arc", outline=accent, width=s(4))
        pr = s(7) + s(1.5) * math.sin(now * 4)
        cv.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, fill=accent, outline="")
        cv.create_text(s(88), s(32), anchor="w", text="MAX", fill=accent, font=("Segoe UI", -s(11), "bold"))
        cv.create_text(s(88), s(56), anchor="w", text=_splash["text"], fill="#e8eaf0",
                       font=("Segoe UI Semibold", -s(15)))
        root.after(33, tick)

    tick()
    root.mainloop()


def start_splash():
    if not SHOW_LOADING_SCREEN:
        return
    _splash["gen"] += 1
    _splash.update(close_at=None, text="Loading...")
    threading.Thread(target=_splash_thread, args=(_splash["gen"],), daemon=True).start()


start_splash()

import numpy as np
import pyautogui
import requests
import soundcard as sc
import speech_recognition as sr

warnings.filterwarnings("ignore", message="data discontinuity")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)     # match real screen pixels on scaled displays
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import winsound      # Windows only

# ---- Background mode: when started with pythonw.exe (no window), all messages go to max.log ----
FROZEN = getattr(sys, "frozen", False)                  # True when running as the .exe
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "Max")   # saved key + log live here
os.makedirs(DATA_DIR, exist_ok=True)
LOG_FILE = os.path.join(DATA_DIR, "max.log")
BACKGROUND = (sys.stdout is None
              or os.path.basename(sys.executable).lower().startswith("pythonw")
              or "--background" in sys.argv)
if BACKGROUND:
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1_000_000:
        os.remove(LOG_FILE)                            # keep the log small
    sys.stdout = sys.stderr = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    print(f"\n===== Max started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
stop_event = threading.Event()                          # set when you click Quit in the tray icon
text_commands = queue.Queue()                           # commands typed in the text window (hotkey)

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None

# ---------------- Config ----------------
KEY_STORE = os.path.join(DATA_DIR, "key.dat")


def fatal(message):
    # Shows a message box (the .exe has no console) and quits.
    splash_close()
    print(message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, "Max", 0x10)
    except Exception:
        pass
    sys.exit(1)


def _protect(data, decrypt=False):
    # Windows DPAPI: only YOUR Windows account can decrypt what it encrypts.
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p).value)
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("Windows could not encrypt/decrypt the key")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(dst.pbData))


def save_key(key):
    try:
        blob = b"dpapi:" + _protect(key.encode("utf-8"))
    except Exception:
        blob = b"plain:" + key.encode("utf-8")            # fallback if encryption is unavailable
    with open(KEY_STORE, "wb") as f:
        f.write(blob)


def read_saved_key():
    try:
        with open(KEY_STORE, "rb") as f:
            blob = f.read()
        if blob.startswith(b"dpapi:"):
            return _protect(blob[6:], decrypt=True).decode("utf-8").strip()
        if blob.startswith(b"plain:"):
            return blob[6:].decode("utf-8").strip()
    except Exception:
        pass
    return ""


def key_is_valid(key):
    # True = Google accepts it, False = rejected, None = couldn't check (offline)
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": key}, params={"pageSize": 1}, timeout=15)
        return r.status_code not in (400, 401, 403)
    except requests.exceptions.RequestException:
        return None


def prompt_for_key():
    # First-run window: dark, modern, with a glowing orb. Returns the key, or "" if cancelled.
    import tkinter as tk
    splash_close()                                            # hide the loading window while we ask

    BG, PANEL, BORDER = (15, 17, 22), (26, 29, 38), (48, 54, 70)
    ACCENT, TEXT, MUTED, ERR = (0, 190, 135), (236, 239, 245), (140, 150, 168), (255, 105, 105)

    def hx(c):
        return "#%02x%02x%02x" % tuple(int(v) for v in c)

    def mix(c1, c2, t):
        return tuple(a * (1 - t) + b * t for a, b in zip(c1, c2))

    root = tk.Tk()
    root.title("Max")
    root.configure(bg=hx(BG))
    root.resizable(False, False)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = max(1.0, min(2.5, sh / 1080))

    def s(v):
        return int(v * scale)

    W, H = s(500), s(470)
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")
    root.attributes("-topmost", True)

    try:                                                      # dark title bar (Windows 10/11)
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
    except Exception:
        pass
    try:                                                      # window icon = the Max orb
        buf = io.BytesIO()
        tray_image("idle").save(buf, format="PNG")
        root._icon = tk.PhotoImage(master=root, data=base64.b64encode(buf.getvalue()))
        root.iconphoto(True, root._icon)
    except Exception:
        pass

    result = {"key": ""}
    busy = [False]
    alive = [True]

    # --- glowing, breathing orb ---
    cv = tk.Canvas(root, width=W, height=s(130), bg=hx(BG), highlightthickness=0)
    cv.pack()

    def animate():
        if not alive[0]:
            return
        t = time.time()
        cv.delete("all")
        cx, cy = W // 2, s(66)
        for k in range(4):                                    # soft glow rings
            r = s(30) + k * s(9) + s(2) * math.sin(t * 2 - k * 0.8)
            col = hx(mix(ACCENT, BG, 0.45 + 0.14 * k))
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=col, width=s(2))
        r = s(24) + s(1.5) * math.sin(t * 2)
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=hx(ACCENT), outline="")
        r2 = s(10)
        cv.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill=hx(mix(ACCENT, (255, 255, 255), 0.8)), outline="")
        root.after(40, animate)

    animate()

    tk.Label(root, text="Welcome to Max", bg=hx(BG), fg=hx(TEXT),
             font=("Segoe UI Semibold", -s(26))).pack()
    tk.Label(root, text="Paste your Gemini API key to get started.\nYou only have to do this once.",
             bg=hx(BG), fg=hx(MUTED), font=("Segoe UI", -s(14)), justify="center").pack(pady=(s(6), s(18)))

    # --- key box ---
    box = tk.Frame(root, bg=hx(PANEL), highlightthickness=s(2), highlightbackground=hx(BORDER),
                   highlightcolor=hx(ACCENT))
    box.pack(padx=s(40), fill="x")
    entry = tk.Entry(box, show="\u2022", bg=hx(PANEL), fg=hx(TEXT), insertbackground=hx(TEXT), relief="flat",
                     bd=0, highlightthickness=0, font=("Segoe UI", -s(16)))
    entry.pack(side="left", fill="x", expand=True, padx=(s(14), s(6)), pady=s(12))

    def small_link(parent, text, command, color=ACCENT, **pack):
        lbl = tk.Label(parent, text=text, bg=parent["bg"], fg=hx(color), cursor="hand2",
                       font=("Segoe UI Semibold", -s(13)))
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.config(fg=hx(mix(color, (255, 255, 255), 0.35))))
        lbl.bind("<Leave>", lambda e: lbl.config(fg=hx(color)))
        lbl.pack(**pack)
        return lbl

    def toggle():
        showing = entry.cget("show") == ""
        entry.config(show="\u2022" if showing else "")
        toggle_lbl.config(text="Show" if showing else "Hide")

    def paste():
        try:
            entry.delete(0, "end")
            entry.insert(0, root.clipboard_get().strip())
        except Exception:
            pass

    toggle_lbl = small_link(box, "Show", toggle, MUTED, side="right", padx=(0, s(14)))
    small_link(box, "Paste", paste, ACCENT, side="right", padx=(0, s(12)))

    link_row = tk.Frame(root, bg=hx(BG))
    link_row.pack(fill="x", padx=s(40), pady=(s(10), 0))
    tk.Label(link_row, text="No key yet?", bg=hx(BG), fg=hx(MUTED), font=("Segoe UI", -s(13))).pack(side="left")
    small_link(link_row, "Get one free \u2192", lambda: webbrowser.open("https://aistudio.google.com/apikey"),
               ACCENT, side="left", padx=(s(6), 0))

    status = tk.Label(root, text="", bg=hx(BG), fg=hx(MUTED), font=("Segoe UI", -s(13)), height=2)
    status.pack(pady=(s(8), 0))

    def say(text, color=MUTED):
        status.config(text=text, fg=hx(color))

    # --- buttons ---
    def cancel(event=None):
        alive[0] = False
        result["key"] = ""
        root.destroy()

    def submit(event=None):
        if busy[0]:
            return
        key = entry.get().strip().strip('"').strip("'")
        if len(key) < 20:
            say("That doesn't look like a complete key.", ERR)
            return
        busy[0] = True
        save.config(text="Checking...", bg=hx(mix(ACCENT, BG, 0.5)))
        say("Checking your key with Google...", MUTED)
        outcome = {}

        def work():
            outcome["ok"] = key_is_valid(key)
            outcome["done"] = True

        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not alive[0]:
                return
            if not outcome.get("done"):
                root.after(100, poll)
                return
            busy[0] = False
            if outcome["ok"] is False:
                save.config(text="Save and start", bg=hx(ACCENT))
                say("Google rejected that key. Please check it and try again.", ERR)
                return
            result["key"] = key
            alive[0] = False
            root.destroy()

        poll()

    save = tk.Label(root, text="Save and start", bg=hx(ACCENT), fg=hx((4, 20, 14)), cursor="hand2",
                    font=("Segoe UI Semibold", -s(16)), pady=s(10))
    save.pack(padx=s(40), pady=(s(6), 0), fill="x")
    save.bind("<Button-1>", submit)
    save.bind("<Enter>", lambda e: None if busy[0] else save.config(bg=hx(mix(ACCENT, (255, 255, 255), 0.2))))
    save.bind("<Leave>", lambda e: None if busy[0] else save.config(bg=hx(ACCENT)))

    cancel_lbl = tk.Label(root, text="Cancel", bg=hx(BG), fg=hx(MUTED), cursor="hand2", font=("Segoe UI", -s(13)))
    cancel_lbl.pack(pady=(s(10), 0))
    cancel_lbl.bind("<Button-1>", cancel)
    tk.Label(root, text="Stored encrypted on this PC only. Never uploaded anywhere.", bg=hx(BG),
             fg=hx(mix(MUTED, BG, 0.35)), font=("Segoe UI", -s(11))).pack(side="bottom", pady=s(12))

    root.bind("<Return>", submit)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.after(250, lambda: (root.lift(), root.focus_force(), entry.focus_force()))
    root.mainloop()
    return result["key"]


def load_api_key():
    # Order: GEMINI_API_KEY variable, api_key.txt next to the program, the saved key, then ask once.
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(APP_DIR, "api_key.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    key = read_saved_key()
    if key:
        return key
    key = prompt_for_key()
    if key:
        save_key(key)
        start_splash()                                        # bring the loading window back
    return key


if "--reset-key" in sys.argv and os.path.exists(KEY_STORE):
    os.remove(KEY_STORE)                                  # forget the saved key; you'll be asked again


API_KEY = ""   # <- paste your Gemini key between these quotes. DELETE it again before uploading to GitHub!
if not API_KEY:
    API_KEY = load_api_key()
MODEL = "gemini-3.1-flash-lite"       # fastest in your test. (gemini-3.8-flash was overloaded: 503)
BASE = "https://generativelanguage.googleapis.com/v1beta"
FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-3.6-flash"]   # used if the main model is busy
THINK_OPTIONS = [{"thinkingLevel": "minimal"}, {"thinkingBudget": 0}, None]   # tries to turn thinking off (faster)
think_i = [0]
# ---- Wake words: add your own here, in lowercase, inside the quotes ----
WAKE_WORDS = ["max", "macs", "mack", "maxx", "mex"]   # work on their own: "max, open notepad"
PREFIX_ONLY_WORDS = ["mac"]                           # only count after a prefix: "hey mac"
WAKE_PREFIXES = ["hey", "ok", "okay", "hi", "hello"]  # optional words before the name
OVERLAY = True         # cool floating status pill near the clock: glowing orb that reacts to your voice
TRAY_ICON = True       # small icon near the clock with "Open log" and "Quit Max" (needs: pip install pystray)
WAKE_BEEP = True       # short beep when Max hears his name, so you know when to talk
TEXT_HOTKEY = "ctrl+alt+m"  # press this anywhere to open a small box and TYPE a command to Max. "" = off
MAX_ACTIONS = 10                             # safety cap per command
SCREEN_WORDS = ("screen", "click", "look at", "what's on", "on my display")   # only these send a screenshot (slower)

MIC = "Conexant SmartAudio HD"   # part of your microphone's name. If it is not found, Max uses the Windows default mic.
                       # Use MIC = None to always use the Windows default microphone.
                       # (the full list of your microphones is saved in microphones.txt next to this file)
LANGUAGE = "en-US"     # speech recognition language (e.g. "en-IN", "hi-IN")
VOICE_MODE = "fast"   # "fast" = keeps the voice loaded (quickest), "wav" or "direct" = slower fallbacks
FORCE_IPV4 = True      # fixes many "no response" timeouts on Windows (broken IPv6). Set False if you need IPv6
IGNORE_PROXY = False   # set True if a Windows proxy/VPN setting is breaking the connection
AUTO_PICK_MODEL = True # at startup, test several Gemini models and use the fastest one that answers
DEBUG = True           # prints what the mic hears so you can see what's going on

if not API_KEY:
    fatal("No Gemini API key was entered, so Max cannot start. Start Max again to enter it.")

pyautogui.FAILSAFE = True                    # mouse to top-left corner = emergency stop
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
    "{\"type\":\"close_app\",\"name\":\"notepad\"}        (closes that program by its name, e.g. chrome, spotify, notepad)",
    "{\"type\":\"lock_pc\"}                                (locks the PC. ONLY when the user clearly says lock)",
    "",
    "Rules: keep \"say\" under 12 words, natural and quick. If a screenshot is attached, coordinates",
    "must be in that screenshot's pixel space. Use [] for actions if only chatting.",
    "Never do anything destructive (deleting files, formatting, payments) without",
    "the user clearly asking for it. Never shut down, restart or sign out the PC.",
])

# ---------------- Microphone ----------------
RATE = 16000
CHUNK_SEC = 0.1
THRESHOLD = 0.01
NOISE = 0.004                      # running estimate of the background noise
recognizer = sr.Recognizer()
mic = None
rec = None


MIC_LIST = []          # names of the microphones found (filled in the main thread)
mic_request = []       # a microphone picked in the tray menu, waiting to be applied


def refresh_mic_list():
    names = []
    for m in sc.all_microphones():
        if m.name not in names:
            names.append(m.name)
    MIC_LIST[:] = names
    return names


def open_mic():
    # Open the microphone once and keep it open (so no speech is missed between phrases).
    global mic, rec
    refresh_mic_list()
    mics = sc.all_microphones()
    print("Microphones found (set MIC at the top of the file to part of a name):")
    for i, m in enumerate(mics):
        print(f"   {i} - {m.name}")
    try:                                   # also save the list in a text file, so you don't need the console
        with open(os.path.join(APP_DIR, "microphones.txt"), "w", encoding="utf-8") as f:
            f.write("Your microphones. In gemini_assistant.py set MIC to part of a name, e.g.  MIC = \"Conexant\"\n")
            f.write("(MIC = None uses the Windows default microphone)\n\n")
            for m in mics:
                f.write(m.name + "\n")
    except Exception:
        pass
    try:
        if MIC is None:
            mic = sc.default_microphone()
        elif isinstance(MIC, int):
            mic = mics[MIC]
        else:
            mic = sc.get_microphone(MIC)
    except Exception:
        print(f"  Microphone {MIC!r} not found - using the default microphone instead.")
        mic = sc.default_microphone()
    print(f"Using: {mic.name}")
    rec = mic.recorder(samplerate=RATE, channels=1)
    rec.__enter__()


def close_mic():
    global rec
    if rec is not None:
        try:
            rec.__exit__(None, None, None)
        except Exception:
            pass
        rec = None


def apply_mic_request():
    # Runs in the main loop: switches to the microphone picked in the tray menu (this session only).
    global MIC
    if not mic_request:
        return
    action, value = mic_request.pop()
    if action == "refresh":
        refresh_mic_list()
        set_state("idle", "Mic list updated")
        return
    old = MIC
    MIC = value
    close_mic()
    try:
        open_mic()
        calibrate()
    except Exception as e:
        print(f"  [Could not use that microphone: {e}] Going back to the previous one.")
        MIC = old
        close_mic()
        open_mic()
        calibrate()
        set_state("error", "Mic not available")
        return
    set_state("idle", f"Mic: {mic.name}")


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
                          creationflags=0x08000000)       # no console window


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
    winsound.PlaySound(wav, winsound.SND_FILENAME)        # waits until finished


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
        _voice = None                      # restart it next time
        raise


def speak(text):
    # Speak with Windows' built-in voice. Tries up to three methods so one of them works.
    print(f"Max: {text}")
    previous = STATE["name"]
    set_state("speaking", text)
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
        rec.flush()                        # throw away audio of Max's own voice
    if STATE["name"] == "speaking":
        set_state(previous if previous != "speaking" else "idle")


def record_phrase(timeout=5, limit=8, silence=0.6):
    # Wait for speech, record until you stop talking. Returns AudioData or None.
    global NOISE, THRESHOLD
    chunk = int(RATE * CHUNK_SEC)
    preroll = deque(maxlen=5)          # keeps the half second before speech starts
    frames, started, quiet, waited, loud_run, voiced = [], False, 0.0, 0.0, 0, 0

    while True:
        data = rec.record(numframes=chunk)[:, 0]
        level = float(np.sqrt(np.mean(data ** 2)))
        mic_level[0] = min(1.0, level / 0.15)        # feeds the glowing orb
        loud = level > THRESHOLD

        if not started:
            preroll.append(data)
            loud_run = loud_run + 1 if loud else 0
            if not loud:                                   # adapt to changing background noise
                NOISE = 0.95 * NOISE + 0.05 * level
                THRESHOLD = max(NOISE * 2.5, 0.008)
            if loud_run >= 2:                              # 2 loud chunks in a row = speech, not a click
                started, voiced = True, 2
                frames.extend(preroll)
                if DEBUG:
                    print("  (voice detected...)")
            else:
                waited += CHUNK_SEC
                if waited >= timeout or stop_event.is_set() or not text_commands.empty():
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

    if voiced < 3:                                         # under 0.3s of sound = cough, click, bump
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
            rec.flush()                                    # don't record our own beep


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


def grab_clean():
    # Hide Max's own overlay for a moment so it isn't in the screenshot.
    OVERLAY_HIDE[0] = True
    time.sleep(0.15)
    try:
        return grab_screen()
    finally:
        OVERLAY_HIDE[0] = False


def screenshot_b64():
    # Take a screenshot, shrink it, return base64 JPEG + its size.
    global SCALE
    img = grab_clean()
    screen_w = pyautogui.size()[0]
    if img.width > 1024:
        img = img.resize((1024, int(img.height * 1024 / img.width)))
    SCALE = screen_w / img.width            # converts screenshot coordinates to real screen coordinates
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
history = []   # list of {"role": "user"/"model", "parts": [{"text": ...}]}


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
    if any(w in command.lower() for w in SCREEN_WORDS):
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
                            json=body, timeout=(5, 25))      # 5s to connect, 25s to answer

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
            break                          # got an answer, or it's a network problem (other models won't help)
        print(f"[{model} is overloaded - trying another model]")

    if r is None:
        raise RuntimeError("Gemini did not respond (network timeout). "
                           "Check your internet, VPN, firewall or antivirus.")

    if r.status_code == 404:          # model name doesn't exist -> pick a real one
        pick_model()
        r = post(MODEL)
    while (r.status_code == 400 and "think" in r.text.lower()
           and think_i[0] < len(THINK_OPTIONS) - 1):      # model dislikes that thinking setting
        think_i[0] += 1
        gen_config.pop("thinkingConfig", None)
        if THINK_OPTIONS[think_i[0]]:
            gen_config["thinkingConfig"] = THINK_OPTIONS[think_i[0]]
        r = post(MODEL)
    if not r.ok:
        print(f"[Gemini says] {r.text[:400]}")
        r.raise_for_status()
    reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]

    history.append({"role": "user", "parts": [{"text": command}]})   # text only, no images
    history.append({"role": "model", "parts": [{"text": reply}]})

    try:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        return {"say": reply, "actions": []}


# ---------------- PC control ----------------
def xy(a):
    return int(a["x"] * SCALE), int(a["y"] * SCALE)


APP_ALIASES = {"google chrome": "chrome", "chrome": "chrome", "edge": "msedge", "microsoft edge": "msedge",
               "word": "winword", "microsoft word": "winword", "excel": "excel", "powerpoint": "powerpnt",
               "calculator": "calculatorapp", "calc": "calculatorapp", "paint": "mspaint",
               "task manager": "taskmgr", "visual studio code": "code", "vscode": "code", "vs code": "code",
               "pycharm": "pycharm64", "spotify": "spotify", "discord": "discord", "firefox": "firefox",
               "notepad": "notepad", "vlc": "vlc", "teams": "ms-teams", "whatsapp": "whatsapp"}
PROTECTED_PROCESSES = {"explorer.exe", "winlogon.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe",
                       "svchost.exe", "smss.exe", "dwm.exe", "system", "registry", "fontdrvhost.exe"}
CLOSE_WORDS = ("close", "quit", "exit", "kill", "terminate", "stop")     # you must say one of these
last_command = [""]


def close_app(name):
    # Politely closes a program (like clicking its X). Never touches Windows itself or Max.
    key = " ".join(str(name).lower().split())
    exe = APP_ALIASES.get(key, key)
    if not re.fullmatch(r"[\w .\-]+", exe):
        print(f"  [Refused to close '{name}']")
        return
    if not exe.endswith(".exe"):
        exe += ".exe"
    own = {os.path.basename(sys.executable).lower(), "python.exe", "pythonw.exe", "max.exe"}
    if exe in PROTECTED_PROCESSES or exe in own:
        print(f"  [Refused to close {exe} - it is protected]")
        return
    r = subprocess.run(["taskkill", "/IM", exe], capture_output=True, text=True, creationflags=0x08000000)
    if r.returncode == 0:
        print(f"  Closed {exe}")
    else:
        print(f"  [Could not close {exe}: {(r.stdout or r.stderr).strip()[:120]}]")


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
        if re.fullmatch(r"[\w .\-]+", name):                  # block shell tricks
            subprocess.Popen(["cmd", "/c", "start", "", name], creationflags=0x08000000)
    elif t == "open_url":
        if a["url"].startswith(("http://", "https://")):
            webbrowser.open(a["url"])
    elif t == "screenshot":
        grab_clean().save(f"screenshot_{int(time.time())}.png")
    elif t == "brightness" and sbc:
        sbc.set_brightness(max(0, min(100, int(a["level"]))))
    elif t == "wait":
        time.sleep(min(float(a.get("seconds", 1)), 10))
    elif t == "close_app":
        if any(w in last_command[0].lower() for w in CLOSE_WORDS):
            close_app(a.get("name", ""))
        else:
            print("  [Blocked close_app: you did not ask to close anything]")
    elif t == "lock_pc":
        if "lock" in last_command[0].lower():
            time.sleep(1.5)                                   # let Max finish his sentence
            ctypes.windll.user32.LockWorkStation()
        else:
            print("  [Blocked lock_pc: you did not ask to lock the PC]")


def handle(command):
    t0 = time.time()
    last_command[0] = command
    set_state("thinking")
    try:
        result = ask_gemini(command)
    except Exception as e:
        print(f"[Gemini error] {e}")
        set_state("error", "Can't reach Gemini")
        speak("Sorry, I couldn't reach Gemini.")
        time.sleep(1.0)
        return
    if DEBUG:
        print(f"  [Gemini answered in {time.time() - t0:.1f}s]")

    set_state("acting")
    talker = None
    if result.get("say"):                      # talk and act at the same time
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


# ---------------- How Max looks (tray icon + floating overlay) ----------------
STATE = {"name": "idle", "text": "Ready", "since": time.time()}
mic_level = [0.0]
OVERLAY_HIDE = [False]
_tray = [None]
_tray_images = {}

COLORS = {"idle": (0, 170, 130), "hearing": (0, 225, 140), "thinking": (255, 176, 0),
          "speaking": (70, 150, 255), "acting": (175, 95, 255), "error": (235, 70, 70)}
DEFAULT_TEXT = {"idle": "Ready", "hearing": "Listening...", "thinking": "Thinking...",
                "speaking": "Speaking", "acting": "On it...", "error": "Something went wrong"}


def set_state(name, text=None):
    # Called from everywhere: updates the overlay and the tray icon colour/tooltip.
    text = " ".join((text or DEFAULT_TEXT[name]).split())
    if len(text) > 30:
        text = text[:29] + "..."
    STATE.update(name=name, text=text, since=time.time())
    icon = _tray[0]
    if icon is not None:
        try:
            icon.icon = tray_image(name)
            icon.title = f"Max - {text}"
        except Exception:
            pass


def tray_image(name):
    if name not in _tray_images:
        from PIL import Image, ImageDraw
        color = COLORS[name]
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, 62, 62), fill=(20, 22, 28, 255), outline=color, width=4)
        d.ellipse((18, 18, 46, 46), fill=color)               # glowing orb
        d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 200))
        _tray_images[name] = img
    return _tray_images[name]


def run_overlay():
    # A small floating status pill (bottom-right, above the taskbar). Click-through, so it never
    # blocks Max's mouse clicks. Runs in its own thread.
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print(f"(No overlay: {e})")
        return

    KEY = "#010203"                                           # this colour becomes see-through
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-transparentcolor", KEY)
    except Exception:
        pass
    root.attributes("-alpha", 0.0)
    root.configure(bg=KEY)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = max(1.0, min(2.5, sh / 1080))

    def s(v):
        return int(v * scale)

    W, H = s(300), s(70)
    root.geometry(f"{W}x{H}+{sw - W - s(24)}+{sh - H - s(76)}")
    cv = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
    cv.pack()

    try:                                                      # click-through + hidden from Alt+Tab
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80)
    except Exception:
        pass

    def hexcol(c):
        return "#%02x%02x%02x" % tuple(int(v) for v in c)

    def rounded(x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def circle(cx, cy, r, **kw):
        return cv.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)

    state = {"alpha": 0.0, "level": 0.0}

    def draw(name, now):
        col = COLORS.get(name, COLORS["idle"])
        c = hexcol(col)
        dim = hexcol([v * 0.35 for v in col])
        cv.delete("all")
        rounded(s(2), s(2), W - s(2), H - s(2), s(26), fill="#12141a", outline=dim, width=s(2))
        cx, cy = s(38), H // 2
        state["level"] = state["level"] * 0.6 + mic_level[0] * 0.4

        if name == "hearing":                                 # orb grows with your voice
            circle(cx, cy, s(20) + s(4) * math.sin(now * 6), outline=dim, width=s(2))
            circle(cx, cy, s(10) + s(14) * state["level"], fill=c, outline="")
        elif name == "thinking":                              # spinner
            r = s(17)
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=(now * 320) % 360,
                          extent=260, style="arc", outline=c, width=s(4))
            circle(cx, cy, s(5), fill=c, outline="")
        elif name == "speaking":                              # equalizer bars
            for i in range(5):
                h = s(5) + s(16) * abs(math.sin(now * 7 + i * 1.2))
                x = cx - s(16) + i * s(8)
                cv.create_rectangle(x - s(2), cy - h, x + s(2), cy + h, fill=c, outline="")
        elif name == "acting":                                # expanding rings
            for k in (0.0, 0.5):
                q = (now * 1.5 + k) % 1
                circle(cx, cy, s(7) + s(15) * q, outline=c if q < 0.5 else dim, width=s(2))
            circle(cx, cy, s(6), fill=c, outline="")
        elif name == "error":                                 # blink
            circle(cx, cy, s(14), fill=c if int(now * 4) % 2 == 0 else dim, outline="")
        else:                                                 # idle: slow breathing
            circle(cx, cy, s(10) + s(1.5) * math.sin(now * 2), fill=c, outline="")

        cv.create_text(s(72), s(24), anchor="w", text="MAX", fill=c, font=("Segoe UI", -s(11), "bold"))
        cv.create_text(s(72), s(46), anchor="w", text=STATE["text"], fill="#e8eaf0",
                       font=("Segoe UI Semibold", -s(14)))

    def tick():
        if stop_event.is_set():
            root.destroy()
            return
        now = time.time()
        name = STATE["name"]
        show = (name != "idle" or now - STATE["since"] < 2.5) and not OVERLAY_HIDE[0]
        if OVERLAY_HIDE[0]:
            state["alpha"] = 0.0                              # vanish instantly for screenshots
        else:
            state["alpha"] += ((0.94 if show else 0.0) - state["alpha"]) * 0.25
        root.attributes("-alpha", max(0.0, round(state["alpha"], 3)))
        if state["alpha"] > 0.02:
            draw(name, now)
        root.after(33, tick)

    tick()
    root.mainloop()


# ---------------- Text mode: press the hotkey, type a command ----------------
text_bar_open = threading.Event()


def parse_hotkey(spec):
    mods = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8, "windows": 0x8}
    named = {"space": 0x20, "enter": 0x0D, "tab": 0x09}
    flags, vk, is_fkey = 0, None, False
    for part in spec.lower().replace(" ", "").split("+"):
        if part in mods:
            flags |= mods[part]
        elif part in named:
            vk = named[part]
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", part):
            vk, is_fkey = 0x6F + int(part[1:]), True
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key '{part}'")
    if vk is None or (flags == 0 and not is_fkey):
        raise ValueError("use at least one of ctrl/alt/shift/win plus a key, e.g. ctrl+alt+m")
    return flags, vk


def hotkey_loop():
    # Listens for the global hotkey (works while you are in any other program).
    try:
        flags, vk = parse_hotkey(TEXT_HOTKEY)
    except ValueError as e:
        print(f"[TEXT_HOTKEY problem: {e}]")
        return
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, flags | 0x4000, vk):         # 0x4000 = no repeat while held
        print(f"[The hotkey {TEXT_HOTKEY} is already used by another program. Change TEXT_HOTKEY.]")
        return
    print(f"Text mode: press {TEXT_HOTKEY} to type a command to Max.")
    msg = wintypes.MSG()
    try:
        while not stop_event.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):   # 0x0312 = WM_HOTKEY
                text_bar_open.set()
            else:
                time.sleep(0.05)
    finally:
        user32.UnregisterHotKey(None, 1)


def run_text_mode():
    # A small dark window to type a command to Max. Runs in its own thread.
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        print(f"(No text mode: {e})")
        return
    root.withdraw()

    BG, PANEL, BORDER, ACCENT, TEXT, MUTED = "#0f1116", "#1a1d26", "#30364a", "#00be87", "#ecf0f5", "#8c96a8"
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = max(1.0, min(2.5, sh / 1080))

    def s(v):
        return int(v * scale)

    bar = tk.Toplevel(root)
    bar.withdraw()
    bar.title("Max")
    bar.configure(bg=BG)
    bar.resizable(False, False)
    W, H = s(560), s(118)

    head = tk.Frame(bar, bg=BG)
    head.pack(fill="x", padx=s(22), pady=(s(14), s(8)))
    tk.Label(head, text="MAX", bg=BG, fg=ACCENT, font=("Segoe UI", -s(12), "bold")).pack(side="left")
    tk.Label(head, text="Type a command and press Enter", bg=BG, fg=MUTED,
             font=("Segoe UI", -s(12))).pack(side="left", padx=(s(10), 0))
    box = tk.Frame(bar, bg=PANEL, highlightthickness=s(2), highlightbackground=BORDER, highlightcolor=ACCENT)
    box.pack(fill="x", padx=s(22))
    entry = tk.Entry(box, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                     highlightthickness=0, font=("Segoe UI", -s(17)))
    entry.pack(fill="x", padx=s(12), pady=s(10))
    tk.Label(bar, text="Esc to close", bg=BG, fg=MUTED, font=("Segoe UI", -s(11))).pack(pady=(s(6), 0))

    def hide(event=None):
        bar.withdraw()

    def send(event=None):
        text = entry.get().strip()
        hide()
        if text:
            text_commands.put(text)

    def show():
        bar.geometry(f"{W}x{H}+{(sw - W) // 2}+{int(sh * 0.28)}")
        bar.deiconify()
        bar.attributes("-topmost", True)
        bar.lift()
        try:                                                  # dark title bar + bring to the front
            bar.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(bar.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        bar.focus_force()
        entry.delete(0, "end")
        entry.focus_force()

    entry.bind("<Return>", send)
    bar.bind("<Escape>", hide)
    bar.protocol("WM_DELETE_WINDOW", hide)

    def poll():
        if stop_event.is_set():
            root.destroy()
            return
        if text_bar_open.is_set():
            text_bar_open.clear()
            show()
        root.after(80, poll)

    poll()
    root.mainloop()


def single_instance():
    # Stops a second copy of Max from starting (two Maxes would both answer you).
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47653))
    except OSError:
        print("Max is already running.")
        sys.exit(0)
    return lock


def start_tray():
    # Small icon near the clock so you can see Max is running and quit him.
    if not TRAY_ICON:
        return None
    try:
        import pystray
    except ImportError:
        print("(No tray icon - to get one run: pip install pystray)")
        return None

    def open_log(icon, item):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)

    def quit_app(icon, item):
        stop_event.set()
        icon.stop()

    def forget_key(icon, item):
        if os.path.exists(KEY_STORE):
            os.remove(KEY_STORE)
        print("Saved API key removed. Start Max again to enter a new one.")
        stop_event.set()
        icon.stop()

    def choose_default(icon, item):
        set_state("idle", "Switching mic...")
        mic_request[:] = [("set", None)]

    def make_choose(name):
        def choose(icon, item):
            set_state("idle", "Switching mic...")
            mic_request[:] = [("set", name)]
        return choose

    def make_checked(name):
        return lambda item: MIC is not None and mic is not None and mic.name == name

    def refresh_list(icon, item):
        mic_request[:] = [("refresh", None)]

    def mic_items():
        yield pystray.MenuItem("Default (Windows)", choose_default,
                               checked=lambda item: MIC is None, radio=True)
        for name in list(MIC_LIST):
            yield pystray.MenuItem(name[:70], make_choose(name), checked=make_checked(name), radio=True)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Refresh list", refresh_list)

    menu = pystray.Menu(pystray.MenuItem("Microphone", pystray.Menu(mic_items)),
                        pystray.MenuItem("Open log", open_log),
                        pystray.MenuItem("Forget API key and quit", forget_key),
                        pystray.MenuItem("Quit Max", quit_app))
    icon = pystray.Icon("Max", tray_image("idle"), "Max - ready", menu)
    _tray[0] = icon
    icon.run_detached()
    return icon


def main():
    lock = single_instance()                  # keep a reference so the lock stays active
    splash_status("Starting...")
    tray = start_tray()
    if OVERLAY:
        threading.Thread(target=run_overlay, daemon=True).start()
    if TEXT_HOTKEY:
        threading.Thread(target=hotkey_loop, daemon=True).start()
        threading.Thread(target=run_text_mode, daemon=True).start()
    splash_status("Connecting to Gemini...")
    check_gemini()
    splash_status("Checking the screen...")
    check_screen()
    if AUTO_PICK_MODEL:
        splash_status("Finding the fastest model...")
        auto_pick_model()
    splash_status("Opening the microphone...")
    open_mic()
    print("Calibrating microphone (stay quiet for a second)...")
    splash_status("Calibrating - stay quiet...")
    calibrate()
    splash_status("Warming up the voice...")
    if VOICE_MODE == "fast":
        try:
            _start_voice()
        except Exception as e:
            print(f"  [Voice warm-up failed] {e}")
    set_state("idle", "Say \"hey max\"")
    splash_status("Ready!")
    splash_close(0.8)
    speak("Max is ready. Say hey max to wake me.")

    try:
        while not stop_event.is_set():
            apply_mic_request()                # switch microphone if you picked one in the tray menu
            if not text_commands.empty():      # a command typed in the text window
                typed = text_commands.get().strip()
                if typed:
                    print(f"You (typed): {typed}")
                    set_state("hearing", "Typed command")
                    if any(p in typed.lower() for p in ("stop listening", "goodbye", "exit max", "quit max")):
                        speak("Goodbye.")
                        break
                    handle(typed)
                    set_state("idle", "Done")
                continue
            heard, alts = listen(timeout=5, limit=8)
            if not heard:
                continue

            command = find_wake(alts)
            if command is None:
                continue                       # not for Max
            set_state("hearing")
            if command == "":
                beep()                         # "I'm listening" beep, then take the command
                command, _ = listen(timeout=6, limit=10)
                if not command:
                    set_state("idle", "Didn't catch that")
                    continue

            print(f"You: {command}")
            if any(p in command for p in ("stop listening", "goodbye", "exit max", "quit max")):
                speak("Goodbye.")
                break

            handle(command)
            set_state("idle", "Done")
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        close_mic()
        if _voice and _voice.poll() is None:
            _voice.terminate()
        if tray:
            tray.stop()
        lock.close()


if __name__ == "__main__":
    main()
