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
try:                            # the Settings app can switch it off (settings.json, same folder as the saved key)
    with open(os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "Max", "settings.json"),
              encoding="utf-8") as _f:
        SHOW_LOADING_SCREEN = bool(json.load(_f).get("loading_screen", SHOW_LOADING_SCREEN))
except Exception:
    pass
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)     # sharp window on scaled displays
except Exception:
    pass
_splash = {"gen": 0, "text": "Loading...", "close_at": None}


# ---------------- Smooth (anti-aliased) drawing: Tk's own circles have jagged edges ----------------
class AAPainter:
    # Draws every shape 2-4x bigger, shrinks it down, and shows it as one picture - so edges are smooth.
    def __init__(self, root, canvas, W, H, bg, ss):
        from PIL import Image, ImageDraw, ImageTk
        self.Image, self.ImageDraw = Image, ImageDraw
        self.res = getattr(Image, "Resampling", Image).LANCZOS
        self.W, self.H, self.bg, self.ss = int(W), int(H), tuple(bg), ss
        self.photo = ImageTk.PhotoImage(Image.new("RGB", (self.W, self.H), self.bg), master=root)
        canvas.create_image(0, 0, anchor="nw", image=self.photo)

    @staticmethod
    def _c(c):
        return None if c is None else tuple(int(max(0, min(255, v))) for v in c)

    def begin(self):
        self.img = self.Image.new("RGB", (self.W * self.ss, self.H * self.ss), self.bg)
        self.d = self.ImageDraw.Draw(self.img)

    def rrect(self, x1, y1, x2, y2, r, fill=None, outline=None, width=0):
        k = self.ss
        self.d.rounded_rectangle([x1 * k, y1 * k, x2 * k, y2 * k], radius=r * k, fill=self._c(fill),
                                 outline=self._c(outline), width=max(1, int(width * k)) if outline else 0)

    def oval(self, cx, cy, r, fill=None, outline=None, width=0):
        if r <= 0:
            return
        k = self.ss
        self.d.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], fill=self._c(fill),
                       outline=self._c(outline), width=max(1, int(width * k)) if outline else 0)

    def arc(self, cx, cy, r, start, extent, color, width):
        k = self.ss
        self.d.arc([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k], start, start + extent,
                   fill=self._c(color), width=max(1, int(width * k)))

    def rect(self, x1, y1, x2, y2, fill, radius=0):
        k = self.ss
        if radius > 0:
            self.d.rounded_rectangle([x1 * k, y1 * k, x2 * k, y2 * k], radius=radius * k, fill=self._c(fill))
        else:
            self.d.rectangle([x1 * k, y1 * k, x2 * k, y2 * k], fill=self._c(fill))

    def end(self):
        self.photo.paste(self.img.resize((self.W, self.H), self.res))


class ItemPainter:
    # Fallback with the same methods, using plain Tk shapes (edges are jagged) - used only if Pillow's ImageTk is missing.
    def __init__(self, canvas, W, H, bg):
        self.cv = canvas

    @staticmethod
    def _h(c):
        return "" if c is None else "#%02x%02x%02x" % tuple(int(max(0, min(255, v))) for v in c)

    def begin(self):
        self.cv.delete("frame")

    def rrect(self, x1, y1, x2, y2, r, fill=None, outline=None, width=0):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.cv.create_polygon(pts, smooth=True, fill=self._h(fill), outline=self._h(outline),
                               width=int(width), tags="frame")

    def oval(self, cx, cy, r, fill=None, outline=None, width=0):
        if r > 0:
            self.cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=self._h(fill), outline=self._h(outline),
                                width=int(width), tags="frame")

    def arc(self, cx, cy, r, start, extent, color, width):
        self.cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=-start, extent=-extent, style="arc",
                           outline=self._h(color), width=int(width), tags="frame")

    def rect(self, x1, y1, x2, y2, fill, radius=0):
        self.cv.create_rectangle(x1, y1, x2, y2, fill=self._h(fill), outline="", tags="frame")

    def end(self):
        self.cv.tag_lower("frame")


def make_painter(root, canvas, W, H, bg, scale):
    try:
        return AAPainter(root, canvas, W, H, bg, 4 if scale <= 1.5 else 3 if scale <= 2 else 2)
    except Exception:
        return ItemPainter(canvas, W, H, bg)



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
        return v * scale

    W, H = int(s(290)), int(s(96))
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
    cv = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
    cv.pack()
    PANEL, EDGE, ACCENT = (18, 20, 26), (20, 84, 63), (0, 190, 135)
    painter = make_painter(root, cv, W, H, (1, 2, 3), scale)
    cx, cy = s(48), H / 2
    rr = s(19)
    cv.create_text(s(88), s(32), anchor="w", text="MAX", fill="#00be87", font=("Segoe UI", -int(s(11)), "bold"))
    status = cv.create_text(s(88), s(56), anchor="w", text=_splash["text"], fill="#e8eaf0",
                            font=("Segoe UI Semibold", -int(s(15))))
    born = time.time()
    end = [None]
    shown = [_splash["text"]]

    def tick():
        t0 = time.perf_counter()
        now = time.time()
        if _splash["gen"] != gen or now - born > 90:
            root.destroy()
            return
        due = _splash["close_at"]
        if end[0] is None and due is not None and now >= due:
            end[0] = now
        if end[0] is not None:                            # eased fade-out, then close
            a = 0.96 * max(0.0, 1 - (now - end[0]) / 0.25)
            if a <= 0.0:
                root.destroy()
                return
        else:                                             # eased fade-in
            p = min(1.0, (now - born) / 0.25)
            a = 0.96 * (1 - (1 - p) ** 3)
        root.attributes("-alpha", a)
        painter.begin()
        painter.rrect(2, 2, W - 2, H - 2, s(26), fill=PANEL, outline=EDGE, width=s(2))
        painter.arc(cx, cy, rr, (now * 330) % 360, 250, ACCENT, s(4))
        painter.oval(cx, cy, s(7) + s(1.5) * math.sin(now * 4), fill=ACCENT)
        painter.end()
        if _splash["text"] != shown[0]:
            shown[0] = _splash["text"]
            cv.itemconfigure(status, text=shown[0])
        root.after(max(1, int(16 - (time.perf_counter() - t0) * 1000)), tick)

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
task_stop = threading.Event()                           # set by the stop hotkey / tray menu to end a running task
say_queue = queue.Queue()                               # things Max should say by himself (screen watcher, study mode)
scheduled_queue = queue.Queue()                         # scheduled commands whose time has come


def pending_work():
    return not (text_commands.empty() and scheduled_queue.empty() and say_queue.empty())

try:
    import screen_brightness_control as sbc
except ImportError:
    sbc = None

# ---------------- Config ----------------
KEY_STORE = os.path.join(DATA_DIR, "key.dat")


def icon_file(name="maxAA.ico"):
    # Finds the icon next to the program (or inside the .exe).
    for base in (getattr(sys, "_MEIPASS", None), APP_DIR):
        if base and os.path.exists(os.path.join(base, name)):
            return os.path.join(base, name)
    return None


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
    try:                                                      # window icon = maxAA.ico (or the Max orb)
        ico = icon_file()
        if ico:
            root.iconbitmap(ico)
        else:
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

    cx, cy = W // 2, s(66)
    painter = make_painter(root, cv, W, s(130), BG, scale)
    born = time.time()
    root.attributes("-alpha", 0.0)
    faded = [False]

    def animate():
        if not alive[0]:
            return
        t0 = time.perf_counter()
        t = time.time()
        if not faded[0]:                                      # smooth fade-in
            p = min(1.0, (t - born) / 0.22)
            root.attributes("-alpha", 1 - (1 - p) ** 3)
            faded[0] = p >= 1.0
        painter.begin()
        for k in range(4):                                    # soft glow rings
            painter.oval(cx, cy, s(30) + k * s(9) + s(2) * math.sin(t * 2 - k * 0.8),
                         outline=mix(ACCENT, BG, 0.45 + 0.14 * k), width=s(2))
        painter.oval(cx, cy, s(24) + s(1.5) * math.sin(t * 2), fill=ACCENT)
        painter.oval(cx, cy, s(10), fill=mix(ACCENT, (255, 255, 255), 0.8))
        painter.end()
        root.after(max(1, int(16 - (time.perf_counter() - t0) * 1000)), animate)

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
PREFIX_ONLY_WORDS = ["mac", "mat", "matt", "mask", "marks", "mix", "makes", "maxs", "macks", "mags", "mats"]   # only after a prefix: "hey mac"
SOLO_SOUNDALIKES = {"mac", "mat", "matt", "macks", "maxs"}   # a one-word phrase like "mac" still wakes him
WAKE_PREFIXES = ["hey", "ok", "okay", "hi", "hello"]  # optional words before the name
OVERLAY = True         # cool floating status pill near the clock: glowing orb that reacts to your voice
TRAY_ICON = True       # small icon near the clock with "Open log" and "Quit Max" (needs: pip install pystray)
MIC_SENSITIVITY = 6      # 1 (ignores quiet sounds) to 10 (hears whispers). Raise it if Max misses his name, lower it if noise wakes him
WAKE_BEEP = True       # short beep when Max hears his name, so you know when to talk
TEXT_HOTKEY = "ctrl+alt+m"  # press this anywhere to open a small box and TYPE a command to Max. "" = off
AGENT_MAX_STEPS = 30     # longest a task can run (each step = one screenshot + 1-3 actions)
AGENT_MODEL = None      # None = same model as normal chat. For harder tasks try a stronger one, e.g. "gemini-3.5-flash"
STOP_HOTKEY = "ctrl+alt+x"   # press anywhere to stop a running task at once. "" = off
MAX_ACTIONS = 10                             # safety cap per command
SCREEN_WORDS = ("screen", "click", "look at", "what's on", "on my display")   # only these send a screenshot (slower)

MIC = "Conexant SmartAudio HD"   # part of your microphone's name. If it is not found, Max uses the Windows default mic.
                       # Use MIC = None to always use the Windows default microphone.
                       # (the full list of your microphones is saved in microphones.txt next to this file)
LANGUAGE = "en-US"     # speech recognition language (e.g. "en-IN", "hi-IN")
VOICE_RATE = 0          # speaking speed: -10 (slow) to 10 (fast)   - set it in the Settings app
VOICE_VOLUME = 100      # 0 to 100
CODE_EDITOR = "notepad" # program that opens code Max writes (e.g. "code" for VS Code, or a full path)
PROJECTS_FOLDER = ""    # where Max saves code. "" = Documents\Max Projects
CODING_LANGUAGE = "python"
CODING_LEVEL = "beginner"   # beginner, intermediate or expert
REPLY_LANGUAGE = "english"   # "english", "hinglish" (Hindi in English letters - any voice can read it) or "hindi" (needs a Hindi Windows voice)
STUDY_DISTRACTIONS = ["youtube", "instagram", "facebook", "netflix", "twitter", "reddit", "steam", "tiktok",
                      "minecraft", "fortnite", "roblox", "valorant", "pubg"]   # words in the window title that count as a distraction
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
    "{\"type\":\"open_app\",\"name\":\"notepad\"}              (program name: notepad, taskmgr, calc, chrome, spotify...)",
    "{\"type\":\"open_url\",\"url\":\"https://...\"}",
    "{\"type\":\"screenshot\"}",
    "{\"type\":\"brightness\",\"level\":0-100}",
    "{\"type\":\"wait\",\"seconds\":1}",
    "{\"type\":\"close_app\",\"name\":\"notepad\"}        (closes that program by its name, e.g. chrome, spotify, notepad)",
    "{\"type\":\"lock_pc\"}                                (locks the PC. ONLY when the user clearly says lock)",
    "{\"type\":\"compose_email\",\"to\":\"\",\"subject\":\"...\",\"body\":\"the whole letter\"}   (opens a Gmail draft. It never sends.)",
    "{\"type\":\"system_info\"}                            (reads this PC's specs; you then get them back and answer)",
    "{\"type\":\"drag\",\"x1\":int,\"y1\":int,\"x2\":int,\"y2\":int}",
    "{\"type\":\"hold_key\",\"key\":\"w\",\"seconds\":2}",
    "{\"type\":\"remember\",\"text\":\"the principal's email is ...\"}   (saves a fact about the user; only when they say remember/note/save)",
    "{\"type\":\"forget\",\"text\":\"part of the fact\"}",
    "{\"type\":\"read_clipboard\"}   (reads the text the user copied; only when they talk about what they copied. You then get it back)",
    "{\"type\":\"set_clipboard\",\"text\":\"...\"}   (puts text on the clipboard)",
    "{\"type\":\"paste\"}   (presses Ctrl+V)",
    "{\"type\":\"watch_screen\",\"condition\":\"the download finishes\",\"every_seconds\":15,\"max_minutes\":30}   (watches the screen and tells the user when it happens)",
    "{\"type\":\"stop_watching\"}",
    "{\"type\":\"study_mode\",\"minutes\":60}   (reminds the user when they open distractions like games or YouTube)",
    "{\"type\":\"stop_study\"}",
    "{\"type\":\"add_schedule\",\"time\":\"07:30\",\"days\":\"daily|weekdays\",\"command\":\"open gmail\"}   (runs a command by itself at that time)",
    "{\"type\":\"list_schedules\"}",
    "{\"type\":\"remove_schedule\",\"text\":\"part of the command, or all\"}",
    "{\"type\":\"write_code\",\"filename\":\"hello.py\",\"code\":\"the complete code\"}   (saves the code in the projects folder and opens it in the editor. It never runs it.)",
    "{\"type\":\"start_task\",\"goal\":\"...\"}   (for jobs that need MANY steps while LOOKING at the screen: playing a slow game, using Blender, shopping or ordering food, filling in forms. Max then works step by step with screenshots.)",
    "",
    "Rules: keep \"say\" under 12 words, natural and quick. If a screenshot is attached, coordinates",
    "must be in that screenshot's pixel space. Use [] for actions if only chatting.",
    "Never do anything destructive (deleting files, formatting, payments) without",
    "the user clearly asking for it. Never shut down, restart or sign out the PC.",
    "Emails: write the complete polite letter in body (about 100-150 words). Leave \"to\" empty unless the user",
    "said the address - never invent one. Use placeholders like [your name] or [dates] for unknown details.",
    "For system_info put only a short acknowledgement in say (for example: Checking.).",
    "For multi-step jobs that need looking at the screen use start_task, with one short sentence in say first.",
    "After read_clipboard you get the text back: put the improved, translated or fixed text in a set_clipboard",
    "action and say it is on the clipboard (add paste only if the user asked for it).",
    "Never save passwords, card numbers or one-time codes with remember.",
])

# ---------------- Microphone ----------------
RATE = 16000
CHUNK_SEC = 0.1
THRESHOLD = 0.01
NOISE = 0.004                      # running estimate of the background noise
stt_fails = [0]                    # how many speech-recognition calls in a row failed (internet down?)


def speech_threshold():
    # How loud a sound must be to count as speech. Higher MIC_SENSITIVITY = lower threshold = hears quieter voices.
    k = max(1, min(10, int(MIC_SENSITIVITY)))
    ratio = max(1.4, 3.2 - 0.22 * k)
    floor = max(0.003, 0.0125 - 0.001 * k)
    return max(NOISE * ratio, floor)
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
    write_json_atomic(DEVICES_FILE, {"mics": names, "updated": time.time()})
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
    THRESHOLD = speech_threshold()
    print(f"Background level: {NOISE:.4f}  ->  speech threshold: {THRESHOLD:.4f}")


def _powershell(cmd, **extra_env):
    env = {**os.environ, **extra_env}
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          env=env, capture_output=True, text=True,
                          creationflags=0x08000000)       # no console window


def _voice_setup():
    return f"$s.Volume = {max(0, min(100, int(VOICE_VOLUME)))}; $s.Rate = {max(-10, min(10, int(VOICE_RATE)))}; "


def _speak_wav(text):
    # Make a WAV file with Windows' voice, then play it with Python's winsound.
    wav = os.path.join(tempfile.gettempdir(), "max_voice.wav")
    cmd = ("Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " + _voice_setup() +
           "$s.SetOutputToWaveFile($env:SPEAK_WAV); "
           "$s.Speak($env:SPEAK_TEXT); $s.Dispose()")
    r = _powershell(cmd, SPEAK_TEXT=text, SPEAK_WAV=wav)
    if r.returncode != 0 or not os.path.exists(wav):
        raise RuntimeError(r.stderr.strip()[:300] or "could not create the voice file")
    winsound.PlaySound(wav, winsound.SND_FILENAME)        # waits until finished


def _speak_direct(text):
    cmd = ("Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " + _voice_setup() +
           "$s.SetOutputToDefaultAudioDevice(); "
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
              "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " + _voice_setup() +
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


def _speak_unlocked(text):
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


_speak_lock = threading.Lock()


def speak(text):
    with _speak_lock:                      # only one voice at a time (talking + task can overlap)
        _speak_unlocked(text)


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
                THRESHOLD = speech_threshold()
            if loud_run >= 2:                              # 2 loud chunks in a row = speech, not a click
                started, voiced = True, 2
                frames.extend(preroll)
                if DEBUG:
                    print("  (voice detected...)")
            else:
                waited += CHUNK_SEC
                if waited >= timeout or stop_event.is_set() or pending_work():
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

    if voiced < 2:                                         # a click or bump (a short "max" is still kept)
        return None
    pcm = (np.clip(np.concatenate(frames), -1, 1) * 32767).astype(np.int16).tobytes()
    return sr.AudioData(pcm, RATE, 2)


def listen(timeout=5, limit=8):
    # Returns (best guess, all alternative guesses). ("", []) if nothing was understood.
    audio = record_phrase(timeout, limit)
    if audio is None:
        return "", []
    result, ok = None, False
    for _ in range(2):                                     # one retry if the internet hiccups
        try:
            result = recognizer.recognize_google(audio, language=LANGUAGE, show_all=True)
            ok = True
            break
        except sr.UnknownValueError:
            ok = True
            break
        except sr.RequestError as e:
            print(f"  [Speech service error - check your internet] {e}")
            time.sleep(0.4)
    if ok:
        stt_fails[0] = 0
    else:
        stt_fails[0] += 1
        if stt_fails[0] == 3:                              # tell you once that he cannot hear you right now
            say_queue.put("I can't reach the speech service. Please check your internet.")
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
        if text.strip(" ,.!?") in SOLO_SOUNDALIKES:         # the recogniser heard just "mac" / "matt" ...
            return ""
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


def ask_gemini(command, allow_screen=True):
    parts = [{"text": command}]
    if allow_screen and any(w in command.lower() for w in SCREEN_WORDS):
        b64, (w, h) = screenshot_b64()
        parts[0]["text"] = f"{command}\n(Screenshot attached, {w}x{h} pixels.)"
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": b64}})

    gen_config = {"responseMimeType": "application/json", "maxOutputTokens": 600}
    if THINK_OPTIONS[think_i[0]]:
        gen_config["thinkingConfig"] = THINK_OPTIONS[think_i[0]]

    body = {
        "systemInstruction": {"parts": [{"text": build_system_prompt()}]},
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
last_actions = []            # the actions of your last command (so you can say: save that as ...)
BLOCKED_HOTKEYS = [{"alt", "f4"}, {"win", "r"}, {"win", "x"}, {"win", "l"}, {"ctrl", "alt", "delete"}, {"ctrl", "alt", "del"}]

# What to type for programs whose spoken name is not their run name
APP_OPEN_ALIASES = {"task manager": "taskmgr", "calculator": "calc", "paint": "mspaint", "file explorer": "explorer",
                    "explorer": "explorer", "settings": "ms-settings:", "control panel": "control",
                    "snipping tool": "snippingtool", "word": "winword", "excel": "excel", "powerpoint": "powerpnt",
                    "google chrome": "chrome", "microsoft edge": "msedge", "edge": "msedge", "vs code": "code",
                    "visual studio code": "code", "device manager": "devmgmt.msc"}


def compose_email(to, subject, body):
    # Opens a Gmail "new message" window with everything filled in. It NEVER sends: you press Send.
    from urllib.parse import urlencode
    to = re.sub(r"[^\w.@+,\-]", "", str(to))                 # only characters an email address can have
    base = "https://mail.google.com/mail/?"
    url = base + urlencode({"view": "cm", "fs": "1", "to": to, "su": subject, "body": body})
    note = None
    if len(url) > 1900:                                      # too long for a link: copy the letter instead
        _powershell("Set-Clipboard -Value $env:MAX_CLIP", MAX_CLIP=str(body))
        url = base + urlencode({"view": "cm", "fs": "1", "to": to, "su": subject})
        note = "The letter was too long for the link, so it was copied. Tell the user to press Ctrl+V in the message."
    webbrowser.open(url)
    return note


def system_info():
    # Reads the PC's specs with Windows' own tools and returns them as one line of text.
    script = ("$o = [ordered]@{ "
              "cpu = @(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,LoadPercentage); "
              "os = @(Get-CimInstance Win32_OperatingSystem | Select-Object Caption,OSArchitecture,TotalVisibleMemorySize,FreePhysicalMemory); "
              "gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name); "
              "disk = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace); "
              "pc = @(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model) }; "
              "$o | ConvertTo-Json -Depth 4 -Compress")
    try:
        r = _powershell(script)
        data = json.loads(r.stdout)
    except Exception as e:
        return f"Could not read the specs ({e}). Basic info: {os.cpu_count()} CPU threads."

    def first(key):
        v = data.get(key) or [{}]
        return (v[0] if isinstance(v, list) else v) or {}

    def gb(kb=None, b=None):
        return round((kb / 1048576) if kb is not None else (b / 1073741824), 1)

    parts = []
    pc, cpu, osr = first("pc"), first("cpu"), first("os")
    if pc.get("Model"):
        parts.append(f"PC: {pc.get('Manufacturer', '')} {pc['Model']}".strip())
    if osr.get("Caption"):
        parts.append(f"OS: {osr['Caption']} {osr.get('OSArchitecture', '')}".strip())
    if cpu.get("Name"):
        parts.append(f"CPU: {' '.join(cpu['Name'].split())}, {cpu.get('NumberOfCores')} cores, "
                     f"{cpu.get('NumberOfLogicalProcessors')} threads, load {cpu.get('LoadPercentage')}%")
    if osr.get("TotalVisibleMemorySize"):
        total, free = osr["TotalVisibleMemorySize"], osr.get("FreePhysicalMemory", 0)
        parts.append(f"RAM: {gb(kb=total)} GB total, {gb(kb=free)} GB free")
    gpus = data.get("gpu") or []
    gpus = gpus if isinstance(gpus, list) else [gpus]
    if gpus:
        parts.append("GPU: " + ", ".join(g.get("Name", "?") for g in gpus))
    disks = data.get("disk") or []
    disks = disks if isinstance(disks, list) else [disks]
    if disks:
        parts.append("Disks: " + "; ".join(f"{d['DeviceID']} {gb(b=d['Size'])} GB total, {gb(b=d['FreeSpace'])} GB free"
                                            for d in disks if d.get("Size")))
    return " | ".join(parts) or "No specs could be read."


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
    if UNATTENDED[0] and t not in UNATTENDED_OK:                  # scheduled routines may only do safe things
        print(f"  [Blocked in a scheduled run: {t}]")
        return None
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
        if re.search(r"\d[\d \-]{11,}\d", str(a["text"])):       # 13+ digits in a row = card / account number
            print("  [Blocked: Max never types card or account numbers]")
        else:
            pyautogui.write(str(a["text"]), interval=0.03)
    elif t == "press":
        pyautogui.press(a["key"])
    elif t == "hotkey":
        keyset = {str(k).lower() for k in a["keys"]}
        if any(blocked <= keyset for blocked in BLOCKED_HOTKEYS):
            print(f"  [Blocked hotkey {sorted(keyset)}: not allowed]")
        else:
            pyautogui.hotkey(*a["keys"])
    elif t == "drag":
        pyautogui.moveTo(int(a["x1"] * SCALE), int(a["y1"] * SCALE), duration=0.25)
        pyautogui.dragTo(int(a["x2"] * SCALE), int(a["y2"] * SCALE), duration=0.6, button=a.get("button", "left"))
    elif t == "hold_key":
        pyautogui.keyDown(a["key"])
        try:
            time.sleep(min(float(a.get("seconds", 1)), 10))
        finally:
            pyautogui.keyUp(a["key"])
    elif t == "start_task":
        agent_task(str(a.get("goal", "")), last_command[0])
    elif t == "write_code":
        if said(CODE_WORDS):
            return write_code(a.get("filename", ""), a.get("code", ""))
        print("  [Blocked write_code: you did not ask for code]")
    elif t == "remember":
        if said(("remember", "note", "save", "keep in mind", "don't forget", "dont forget")):
            return remember(a.get("text", ""))
        print("  [Blocked remember: you did not ask me to remember anything]")
    elif t == "forget":
        if said(("forget", "remove", "delete", "erase", "clear")):
            forget(a.get("text", ""))
    elif t == "read_clipboard":
        if said(CLIP_WORDS):
            return read_clipboard()
        print("  [Blocked read_clipboard: you did not mention what you copied]")
    elif t == "set_clipboard":
        set_clipboard(a.get("text", ""))
    elif t == "paste":
        pyautogui.hotkey("ctrl", "v")
    elif t == "watch_screen":
        if said(("watch", "tell me when", "let me know when", "notify", "alert", "keep an eye", "warn me")):
            start_watch(a.get("condition", ""), a.get("every_seconds", 15), a.get("max_minutes", 30))
        else:
            print("  [Blocked watch_screen: you did not ask me to watch]")
    elif t == "stop_watching":
        stop_watch()
    elif t == "study_mode":
        if said(("study", "focus", "concentrate")):
            start_study(a.get("minutes", 60), a.get("extra", []))
    elif t == "stop_study":
        stop_study()
    elif t == "add_schedule":
        if said(("every", "daily", "each", "schedule", "remind", "weekday", "routine")):
            return add_schedule(a.get("time", ""), a.get("days", "daily"), a.get("command", ""))
        print("  [Blocked add_schedule: you did not ask for a schedule]")
    elif t == "list_schedules":
        return list_schedules()
    elif t == "remove_schedule":
        if said(("remove", "delete", "cancel", "stop", "clear", "forget")):
            remove_schedule(a.get("text", ""))
    elif t == "open_app":
        name = " ".join(str(a["name"]).split())
        name = APP_OPEN_ALIASES.get(name.lower(), name)
        if re.fullmatch(r"[\w .\-:]+", name):                # block shell tricks
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
        if said(CLOSE_WORDS):
            close_app(a.get("name", ""))
        else:
            print("  [Blocked close_app: you did not ask to close anything]")
    elif t == "lock_pc":
        if said(("lock",)):
            time.sleep(1.5)                                   # let Max finish his sentence
            ctypes.windll.user32.LockWorkStation()
        else:
            print("  [Blocked lock_pc: you did not ask to lock the PC]")
    elif t == "compose_email":
        return compose_email(a.get("to", ""), a.get("subject", ""), a.get("body", ""))
    elif t == "system_info":
        return system_info()
    return None


# ---------------- Extra abilities: memory, skills, schedules, screen watcher, study mode, clipboard ----------------
def load_json(name, default):
    try:
        with open(os.path.join(DATA_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, data):
    try:
        with open(os.path.join(DATA_DIR, name), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  [Could not save {name}] {e}")


MEMORY = [m for m in load_json("memory.json", []) if isinstance(m, str)]
SKILLS = {k: v for k, v in load_json("skills.json", {}).items() if isinstance(v, list)}
SCHEDULES = [e for e in load_json("schedules.json", []) if isinstance(e, dict)]
UNATTENDED = [False]
UNATTENDED_OK = {"open_app", "open_url", "compose_email", "system_info", "screenshot", "wait", "brightness"}
SKILL_OK = {"open_app", "open_url", "wait", "brightness", "compose_email"}   # no clicks/typing: they can't be replayed blindly
SECRET_WORDS = ("password", "passcode", "otp", "cvv", "card number", "secret")
CLIP_WORDS = ("this code", "my code", "debug", "review this", "what does this do", "clipboard", "copied", "this text", "translate this", "fix this", "explain this", "grammar",
              "summarize this", "summarise this", "rewrite", "what i copied", "paste", "correct this")
LANGUAGE_RULES = {"english": "",
                  "hinglish": "\nReply in Hinglish when the user speaks Hindi: Hindi written in English letters, mixed "
                              "naturally with English words. If the user speaks English, reply in English.",
                  "hindi": "\nReply in Hindi (Devanagari script) when the user speaks Hindi."}


def has_word(text, words):
    # Whole-word match, so "notepad" does not count as "note" and "display" does not count as "pay".
    t = text.lower()
    return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t) for w in words)


def said(words):
    # True if your own words (not the AI's) contain one of these - the hard safety check for sensitive actions.
    return has_word(last_command[0], words)


def memory_prompt():
    if not MEMORY:
        return ""
    return ("\nThings you remember about the user (use them when relevant, e.g. email addresses and usual orders):\n"
            + "\n".join("- " + m for m in MEMORY))


def coding_prompt():
    return (f"\nCoding help: the user's preferred language is {CODING_LANGUAGE} and their level is {CODING_LEVEL}. "
            "When asked to write code, use write_code with complete, working, well-commented code and a short say "
            "(for example: Done, it is open in your editor). You cannot run code, so never say you ran it. "
            "When asked to explain code or teach a concept, explain simply for their level in up to 3 short spoken "
            "sentences, step by step. To explain or fix code the user copied, use read_clipboard first.")


def build_system_prompt():
    return SYSTEM_PROMPT + memory_prompt() + coding_prompt() + LANGUAGE_RULES.get(REPLY_LANGUAGE, "")


# ---- memory ----
def looks_secret(text):
    t = text.lower()
    return bool(re.search(r"\d[\d \-]{7,}\d", t)) or any(w in t for w in SECRET_WORDS)


def remember(text):
    text = " ".join(str(text).split())[:200]
    if not text:
        return None
    if looks_secret(text):
        return "NOT saved: Max never stores passwords, codes or long numbers. Tell the user that."
    if text.lower() not in [m.lower() for m in MEMORY]:
        MEMORY.append(text)
        del MEMORY[:-50]                                      # keep the newest 50 facts
        save_json("memory.json", MEMORY)
    return None


def forget(text):
    key = " ".join(str(text).lower().split())
    if not key:
        return
    if key in ("everything", "all", "all of it", "all memories"):
        MEMORY.clear()
    else:
        MEMORY[:] = [m for m in MEMORY if key not in m.lower()]
    save_json("memory.json", MEMORY)


# ---- clipboard ----
def read_clipboard():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                       capture_output=True, creationflags=0x08000000)
    text = r.stdout.decode("utf-8", errors="replace").strip()
    return ("Clipboard text:\n" + text[:6000]) if text else "The clipboard is empty or has no text."


def set_clipboard(text):
    _powershell("Set-Clipboard -Value $env:MAX_CLIP", MAX_CLIP=str(text)[:20000])


# ---- saved skills (replay without the AI) ----
def clean_name(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", text.lower())).strip()


def run_skill(name):
    speak(f"Running {name}.")
    for a in SKILLS[name][:20]:
        if a.get("type") not in SKILL_OK:
            continue
        try:
            run_action(a)
        except pyautogui.FailSafeException:
            speak("Emergency stop.")
            return
        except Exception as e:
            print(f"  [Action error] {a}: {e}")


def handle_builtin(command):
    # Commands Max understands himself, without asking Gemini. Returns True if it handled the command.
    c = command.lower().strip().rstrip(".!?")

    m = re.match(r"^save (?:that|this|it|the last (?:thing|task|command|one)) as (.+)$", c)
    if m:
        name = clean_name(m.group(1))
        usable = [a for a in last_actions if a.get("type") in SKILL_OK]
        if not name:
            speak("What should I call it?")
        elif not last_actions or len(usable) != len(last_actions):
            speak("I can only save things made of opening apps and websites, drafting emails and brightness. "
                  "Clicks and typing can't be replayed safely.")
        else:
            SKILLS[name] = [dict(a) for a in last_actions]
            save_json("skills.json", SKILLS)
            speak(f"Saved. Say run {name} any time.")
        return True

    m = re.match(r"^(?:delete|remove) (?:the |my )?skill (.+)$", c)
    if m:
        name = clean_name(m.group(1))
        if SKILLS.pop(name, None) is not None:
            save_json("skills.json", SKILLS)
            speak(f"Deleted {name}.")
        else:
            speak("I don't have a skill with that name.")
        return True

    if re.search(r"\b(?:list|show|what are) (?:my |all )?(?:the )?skills\b|\bwhat skills\b", c):
        speak("Your skills are: " + ", ".join(SKILLS) + "." if SKILLS else "You have no saved skills yet.")
        return True

    name = clean_name(re.sub(r"^(?:please )?(?:run|do|start|play|execute|launch)\s+(?:my |the )?", "", c))
    for candidate in (name, re.sub(r" (?:skill|routine)$", "", name)):
        if candidate in SKILLS:
            run_skill(candidate)
            return True

    if re.search(r"\bstop watching\b|\bcancel (?:the )?watch", c):
        stop_watch()
        speak("Okay, I stopped watching.")
        return True
    if re.search(r"\b(?:stop|end|cancel) (?:the )?study mode\b|\bstop studying\b", c):
        stop_study()
        speak("Study mode is off.")
        return True
    if re.search(r"\bwhat do you (?:remember|know about me)\b", c):
        speak("I remember: " + "; ".join(MEMORY[-8:]) + "." if MEMORY else "I don't remember anything yet.")
        return True
    return False


# ---- scheduled routines ----
def add_schedule(time_str, days, command):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(time_str).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return "Schedule NOT saved: the time was not clear. Ask the user for a time like 7:30."
    command = " ".join(str(command).split())[:200]
    if not command:
        return "Schedule NOT saved: there was no command."
    SCHEDULES.append({"time": f"{int(m.group(1)):02d}:{m.group(2)}",
                      "days": "weekdays" if "week" in str(days).lower() else "daily",
                      "command": command, "last_run": ""})
    del SCHEDULES[:-20]
    save_json("schedules.json", SCHEDULES)
    return None


def list_schedules():
    if not SCHEDULES:
        return "There are no schedules."
    return "Schedules:\n" + "\n".join(f"{e['time']} {e['days']}: {e['command']}" for e in SCHEDULES)


def remove_schedule(text):
    key = " ".join(str(text).lower().split())
    if key in ("all", "everything", "all of them"):
        SCHEDULES.clear()
    else:
        SCHEDULES[:] = [e for e in SCHEDULES if key not in e["command"].lower() and key != e["time"]]
    save_json("schedules.json", SCHEDULES)


def scheduler_loop():
    # Checks every 20 seconds if a scheduled command is due (only while Max is running).
    while not stop_event.is_set():
        now = time.localtime()
        hhmm, today = time.strftime("%H:%M", now), time.strftime("%Y-%m-%d", now)
        for entry in SCHEDULES:
            if entry.get("time") != hhmm or entry.get("last_run") == today:
                continue
            if entry.get("days") == "weekdays" and now.tm_wday >= 5:
                continue
            entry["last_run"] = today
            save_json("schedules.json", SCHEDULES)
            scheduled_queue.put(entry.get("command", ""))
        stop_event.wait(20)


# ---- screen watcher ----
WATCH_PROMPT = ("You watch a Windows screen for the user. Reply with ONLY JSON: "
                "{\"met\": true or false, \"say\": \"one short spoken sentence about what you see, only when met\"}. "
                "Say met true only when the condition is clearly visible on the screen. If unsure, say false.")
_watch = {"stop": None}


def small_screen_b64(max_w=800):
    img = grab_screen()
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=55)
    return base64.b64encode(buf.getvalue()).decode()


def watch_loop(condition, every, max_minutes, stop):
    deadline = time.time() + max_minutes * 60
    fails = 0
    while not stop.is_set() and not stop_event.is_set():
        if time.time() > deadline:
            say_queue.put(f"I stopped watching for {condition}. The time limit ended.")
            return
        try:
            reply = gemini_json(WATCH_PROMPT,
                                [{"text": f"Condition to watch for: {condition}\nIs it clearly true on this screen right now?"},
                                 {"inlineData": {"mimeType": "image/jpeg", "data": small_screen_b64()}}],
                                200, None, (5, 25))
            fails = 0
            if reply.get("met") is True:
                say_queue.put(str(reply.get("say") or f"Update: {condition}."))
                return
        except Exception as e:
            fails += 1
            print(f"  [Watcher error] {e}")
            if fails >= 5:
                say_queue.put("I had to stop watching because I lost the connection.")
                return
        stop.wait(every)


def stop_watch():
    if _watch["stop"] is not None:
        _watch["stop"].set()


def start_watch(condition, every=15, max_minutes=30):
    stop_watch()
    condition = " ".join(str(condition).split())[:150]
    if not condition:
        return
    try:
        every = max(10, min(int(every), 120))
        max_minutes = max(1, min(int(max_minutes), 180))
    except (TypeError, ValueError):
        every, max_minutes = 15, 30
    _watch["stop"] = threading.Event()
    threading.Thread(target=watch_loop, args=(condition, every, max_minutes, _watch["stop"]), daemon=True).start()
    print(f"[Watching for: {condition} - every {every}s for up to {max_minutes} min]")


# ---- study mode ----
REMINDERS = ["You're in study mode. Back to work!", "That looks like a distraction. Focus!",
             "Hey, remember your study time. Close it and keep going."]
_study = {"stop": None}


def foreground_title():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def study_loop(minutes, words, stop):
    end = time.time() + minutes * 60
    hits, last_warn = 0, 0.0
    while not stop.is_set() and not stop_event.is_set():
        if time.time() >= end:
            say_queue.put(f"Study time is over. You got distracted {hits} {'time' if hits == 1 else 'times'}." if hits
                          else "Study time is over. Great focus, no distractions!")
            return
        try:
            title = foreground_title().lower()
        except Exception:
            title = ""
        if any(w in title for w in words) and time.time() - last_warn > 90:
            hits += 1
            last_warn = time.time()
            say_queue.put(REMINDERS[hits % len(REMINDERS)])
        stop.wait(3)


def stop_study():
    if _study["stop"] is not None:
        _study["stop"].set()


def start_study(minutes=60, extra=None):
    stop_study()
    try:
        minutes = max(1, min(int(minutes), 240))
    except (TypeError, ValueError):
        minutes = 60
    words = [w.lower() for w in STUDY_DISTRACTIONS]
    for w in (extra if isinstance(extra, list) else [])[:10]:
        w = str(w).lower().strip()
        if 2 <= len(w) <= 30:
            words.append(w)
    _study["stop"] = threading.Event()
    threading.Thread(target=study_loop, args=(minutes, words, _study["stop"]), daemon=True).start()
    print(f"[Study mode for {minutes} min]")


# ---------------- Coding helper: Max writes code files (and never runs them) ----------------
CODE_WORDS = ("write", "create", "make", "build", "generate", "code", "script", "program", "game", "app",
              "website", "function", "class", "fix", "debug", "convert")
LANG_EXT = {"python": ".py", "javascript": ".js", "typescript": ".ts", "html": ".html", "css": ".css", "java": ".java",
            "c": ".c", "c++": ".cpp", "cpp": ".cpp", "c#": ".cs", "csharp": ".cs", "go": ".go", "rust": ".rs",
            "php": ".php", "ruby": ".rb", "kotlin": ".kt", "swift": ".swift", "sql": ".sql", "lua": ".lua",
            "markdown": ".md", "json": ".json"}
ALLOWED_CODE_EXT = set(LANG_EXT.values()) | {".txt", ".csv", ".xml", ".yaml", ".yml", ".h", ".hpp", ".jsx", ".tsx", ".ini"}
RESERVED_NAMES = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "lpt1", "lpt2", "lpt3"}


def projects_dir():
    path = (PROJECTS_FOLDER or "").strip() or os.path.join(os.path.expanduser("~"), "Documents", "Max Projects")
    path = os.path.expandvars(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def open_in_editor(path):
    # Always opens the file in a text editor. (Double-clicking a .py file would RUN it - Max never does that.)
    editor = (CODE_EDITOR or "notepad").strip()
    if not re.fullmatch(r"[\w .\-:\\/()]+", editor):
        editor = "notepad"
    try:
        subprocess.Popen(["cmd", "/c", "start", "", editor, path], creationflags=0x08000000)
    except Exception as e:
        print(f"  [Could not open the editor] {e}")


def write_code(filename, code):
    code = str(code)
    if not code.strip():
        return "NOTHING was saved: no code was given. Tell the user."
    if len(code) > 200_000:
        return "NOT saved: the code is too long. Tell the user."
    name = os.path.basename(str(filename).replace("\\", "/")).strip()
    name = re.sub(r"[^\w\-. ]", "", name)[:80].strip(". ")
    stem, ext = os.path.splitext(name)
    stem = stem.strip() or "max_code"
    if stem.lower() in RESERVED_NAMES:
        stem = "max_" + stem
    if ext.lower() not in ALLOWED_CODE_EXT:                    # also replaces dangerous ones like .exe or .bat
        ext = LANG_EXT.get(str(CODING_LANGUAGE).lower(), ".txt")
    folder = projects_dir()
    path, n = os.path.join(folder, stem + ext.lower()), 2
    while os.path.exists(path):                                # never overwrite your files
        path = os.path.join(folder, f"{stem}_{n}{ext.lower()}")
        n += 1
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    print(f"  Saved code: {path}")
    open_in_editor(path)
    return None


AGENT_PROMPT = "\n".join([
    "You are Max, controlling a Windows PC for the user, who may be away. You get a screenshot every step.",
    "Reply with ONLY a JSON object, no markdown:",
    "{\"thought\":\"what you see and what you will do\",\"actions\":[...1 to 3 actions...],\"done\":false,"
    "\"risky\":false,\"risky_reason\":\"\",\"say\":\"\"}",
    "Allowed actions:",
    "{\"type\":\"move\",\"x\":int,\"y\":int}",
    "{\"type\":\"click\",\"x\":int,\"y\":int,\"button\":\"left|right\",\"clicks\":1}",
    "{\"type\":\"drag\",\"x1\":int,\"y1\":int,\"x2\":int,\"y2\":int}",
    "{\"type\":\"scroll\",\"amount\":int}   (positive up, negative down)",
    "{\"type\":\"type\",\"text\":\"...\"}",
    "{\"type\":\"press\",\"key\":\"enter\"}",
    "{\"type\":\"hotkey\",\"keys\":[\"ctrl\",\"s\"]}",
    "{\"type\":\"hold_key\",\"key\":\"w\",\"seconds\":2}",
    "{\"type\":\"open_app\",\"name\":\"notepad\"}",
    "{\"type\":\"open_url\",\"url\":\"https://...\"}",
    "{\"type\":\"wait\",\"seconds\":2}",
    "Rules:",
    "- Coordinates are in the screenshot's pixel space. Do 1 to 3 small actions, then look at the next screenshot.",
    "- Wait for pages and programs to load. If nothing changed, try something different.",
    "- Set done to true and put a short spoken summary in say when the goal is finished, or if it is impossible.",
    "- Set risky to true (and explain in risky_reason) whenever your actions would place an order, pay, send a",
    "  message or email, post publicly, delete data, install software, or change account or security settings.",
    "  The user must approve those first; never do them while risky is false.",
    "- Never type passwords, card numbers, one-time codes or other secrets. If a login or payment detail is",
    "  needed, set done to true and tell the user what they need to do.",
    "- Never shut down, restart or sign out. Stay inside the task the user asked for.",
    "- Games: only slow or turn-based games. Use hold_key for movement. Blender: use keyboard shortcuts",
    "  (Shift+A add, G move, S scale, R rotate, N side panel) and type exact numbers.",
])
RISK_OK_WORDS = ("place the order", "place order", "confirm the order", "confirm order", "pay", "buy",
                 "purchase", "send it", "send the", "post it", "delete", "install")   # your words that allow risky steps


def gemini_json(system_text, parts, max_tokens=800, first_model=None, timeout=(5, 40)):
    # One Gemini call that must answer in JSON. Retries and switches model when Google is busy.
    body = {"systemInstruction": {"parts": [{"text": system_text}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens}}
    busy = (429, 500, 502, 503, 504)
    first = first_model or MODEL
    r = None
    for model in [first] + [m for m in FALLBACK_MODELS if m != first]:
        for attempt in range(2):
            try:
                r = SESSION.post(f"{BASE}/models/{model}:generateContent", json=body, timeout=timeout)
            except requests.exceptions.RequestException as e:
                print(f"[Network problem: {type(e).__name__}]")
                r = None
                time.sleep(2)
                continue
            if r.status_code not in busy:
                break
            time.sleep(2 * (attempt + 1))
        if r is not None and r.status_code not in busy:
            break
    if r is None:
        raise RuntimeError("Gemini did not respond")
    r.raise_for_status()
    reply = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    try:
        return json.loads(reply[reply.index("{"): reply.rindex("}") + 1])
    except ValueError:
        return {"_raw": reply}


def agent_ask(goal, history, b64, size):
    text = (f"GOAL: {goal}\nScreenshot size: {size[0]}x{size[1]} pixels.\nSteps so far:\n"
            + ("\n".join(history[-10:]) or "(none yet)") + "\nWhat is the next step?")
    reply = gemini_json(AGENT_PROMPT + memory_prompt(),
                        [{"text": text}, {"inlineData": {"mimeType": "image/jpeg", "data": b64}}],
                        800, AGENT_MODEL)
    if "_raw" in reply:
        return {"thought": reply["_raw"][:100], "actions": [], "done": False}
    return reply


def agent_task(goal, user_words):
    # Works step by step: look at the screen, do 1-3 actions, look again. Stops on done, errors,
    # the step limit, the stop hotkey (Ctrl+Alt+X), or the mouse in the top-left corner.
    task_stop.clear()
    history = []
    print(f"[Task started] {goal}")
    for step in range(1, AGENT_MAX_STEPS + 1):
        if task_stop.is_set() or stop_event.is_set():
            speak("Okay, I stopped.")
            return
        set_state("acting", f"Task: step {step}")
        try:
            b64, size = screenshot_b64()
            reply = agent_ask(goal, history, b64, size)
        except Exception as e:
            print(f"[Task error] {e}")
            speak("I lost the connection, so I stopped the task.")
            return
        actions = [x for x in reply.get("actions", []) if isinstance(x, dict)][:3]
        print(f"  step {step}: {str(reply.get('thought', ''))[:110]} -> {[x.get('type') for x in actions]}")

        if reply.get("risky") and not has_word(user_words, RISK_OK_WORDS):
            why = reply.get("risky_reason") or "do something that needs your approval"
            speak(f"I'm ready to {why}. Tell me to go ahead when you are back.")
            return
        for action in actions:
            if task_stop.is_set():
                break
            try:
                run_action(action)
            except pyautogui.FailSafeException:
                speak("Emergency stop.")
                return
            except Exception as e:
                print(f"  [Action error] {action}: {e}")
        history.append(f"{step}. {str(reply.get('thought', ''))[:120]} -> {[x.get('type') for x in actions]}")
        if reply.get("done"):
            speak(reply.get("say") or "The task is finished.")
            return
        time.sleep(1.0)                                       # give the screen time to change
    speak("I used all my steps. Tell me to continue if you want me to keep going.")


def handle(command, unattended=False):
    # Runs one command. unattended=True (scheduled routines) only allows the safe actions in UNATTENDED_OK.
    UNATTENDED[0] = unattended
    try:
        if not unattended and handle_builtin(command):
            return
        _handle(command)
    finally:
        UNATTENDED[0] = False


def _handle(command):
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
    results = []                               # text produced by actions like system_info
    actions = [x for x in result.get("actions", []) if isinstance(x, dict)][:MAX_ACTIONS]
    for action in actions:
        try:
            output = run_action(action)
            if output:
                results.append(str(output))
        except pyautogui.FailSafeException:
            speak("Emergency stop.")
            break
        except Exception as e:
            print(f"[Action error] {action}: {e}")
    if talker:
        talker.join()
    if actions and not UNATTENDED[0]:
        last_actions[:] = actions              # remembered so you can say "save that as ..."

    if results:                                # let Gemini read the results and answer you
        set_state("thinking")
        try:
            follow = ask_gemini("Results of your actions:\n" + "\n".join(results) +
                                "\nNow answer the user's request using these results. Speak in up to 3 short "
                                "sentences. Only use set_clipboard or paste actions if the user asked you to "
                                "replace or paste text; otherwise use an empty actions list.", allow_screen=False)
        except Exception as e:
            print(f"[Gemini error] {e}")
            speak("Sorry, I couldn't read the results.")
            return
        for act in [x for x in follow.get("actions", []) if isinstance(x, dict)][:3]:
            if act.get("type") in ("set_clipboard", "paste"):
                try:
                    run_action(act)
                except Exception as e:
                    print(f"[Action error] {act}: {e}")
        if follow.get("say"):
            speak(follow["say"])


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
        color, k = COLORS[name], 4                            # drawn 4x bigger, then shrunk = smooth edges
        img = Image.new("RGBA", (64 * k, 64 * k), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2 * k, 2 * k, 62 * k, 62 * k), fill=(20, 22, 28, 255), outline=color, width=4 * k)
        d.ellipse((18 * k, 18 * k, 46 * k, 46 * k), fill=color)               # glowing orb
        d.ellipse((24 * k, 24 * k, 40 * k, 40 * k), fill=(255, 255, 255, 200))
        _tray_images[name] = img.resize((64, 64), getattr(Image, "Resampling", Image).LANCZOS)
    return _tray_images[name]


def run_overlay():
    # A small floating status pill (bottom-right, above the taskbar). Click-through, so it never
    # blocks Max's mouse clicks. Runs in its own thread. ~60 frames per second, everything eases
    # (fade, slide, colour, size, text), and every shape is drawn with smooth (anti-aliased) edges.
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
        return v * scale

    W, H = int(s(300)), int(s(70))
    x0, y0, slide = sw - W - int(s(24)), sh - H - int(s(76)), s(14)
    root.geometry(f"{W}x{H}+{x0}+{y0}")
    cv = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
    cv.pack()

    try:                                                      # click-through + hidden from Alt+Tab
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x20 | 0x80)
    except Exception:
        pass

    PANEL, TEXTC = (18, 20, 26), (232, 234, 240)

    def hexcol(c):
        return "#%02x%02x%02x" % tuple(int(max(0, min(255, v))) for v in c)

    def mix(c1, c2, t):
        t = max(0.0, min(1.0, t))
        return tuple(a * (1 - t) + b * t for a, b in zip(c1, c2))

    painter = make_painter(root, cv, W, H, (1, 2, 3), scale)  # shapes (smooth edges)
    cx, cy = s(38), H / 2
    title = cv.create_text(s(72), s(24), anchor="w", text="MAX", fill=hexcol(COLORS["idle"]),
                           font=("Segoe UI", -int(s(11)), "bold"))
    status = cv.create_text(s(72), s(46), anchor="w", text=STATE["text"], fill=hexcol(TEXTC),
                            font=("Segoe UI Semibold", -int(s(14))))

    names = ("idle", "hearing", "thinking", "speaking", "acting", "error")
    w = {n: 0.0 for n in names}                               # how strongly each look is shown (0..1)
    w["idle"] = 1.0
    cur = list(COLORS["idle"])
    me = {"alpha": 0.0, "level": 0.0, "fade": 1.0, "text": STATE["text"], "last": time.perf_counter(), "y": None}

    def tick():
        if stop_event.is_set():
            root.destroy()
            return
        t0 = time.perf_counter()
        now = time.time()
        dt = min(t0 - me["last"], 0.05)
        me["last"] = t0
        name = STATE["name"]

        # ---- fade + slide (frame-rate independent easing) ----
        show = (name != "idle" or now - STATE["since"] < 2.5) and not OVERLAY_HIDE[0]
        if OVERLAY_HIDE[0]:
            me["alpha"] = 0.0                                 # vanish instantly for screenshots
        else:
            me["alpha"] += ((0.94 if show else 0.0) - me["alpha"]) * (1 - math.exp(-dt * 11))
        root.attributes("-alpha", max(0.0, round(me["alpha"], 3)))
        y = int(y0 + slide * (1 - min(1.0, me["alpha"] / 0.94)))
        if y != me["y"]:
            me["y"] = y
            root.geometry(f"+{x0}+{y}")
        if me["alpha"] < 0.01:                                # invisible: relax and save CPU
            root.after(50, tick)
            return

        # ---- blend between looks and colours ----
        k = 1 - math.exp(-dt * 12)
        for n in names:
            w[n] += ((1.0 if n == name else 0.0) - w[n]) * k
        target = COLORS.get(name, COLORS["idle"])
        kc = 1 - math.exp(-dt * 9)
        for i in range(3):
            cur[i] += (target[i] - cur[i]) * kc
        col = tuple(cur)

        raw = mic_level[0]                                    # fast attack, slow release
        me["level"] = me["level"] + (raw - me["level"]) * (1 - math.exp(-dt * 14)) if raw > me["level"] \
            else max(raw, me["level"] - dt * 3.5)
        level = me["level"]
        total = sum(w.values()) or 1.0

        # ---- draw one frame ----
        painter.begin()
        painter.rrect(2, 2, W - 2, H - 2, s(26), fill=PANEL, outline=mix(PANEL, col, 0.38), width=s(2))

        if w["hearing"] > 0.02:                               # listening: breathing ring
            painter.oval(cx, cy, s(20) + s(4) * math.sin(now * 6), outline=mix(PANEL, col, 0.45 * w["hearing"]),
                         width=s(2))
        if w["acting"] > 0.02:                                # working: rings that expand and fade
            for offset in (0.0, 0.5):
                q = (now * 1.5 + offset) % 1
                painter.oval(cx, cy, s(7) + s(15) * q, outline=mix(PANEL, col, w["acting"] * (1 - q)), width=s(2))
        if w["thinking"] > 0.02:                              # thinking: spinner
            painter.arc(cx, cy, s(17), (now * 320) % 360, 260, mix(PANEL, col, w["thinking"]), s(4))
        if w["speaking"] > 0.02:                              # speaking: equalizer bars
            for i in range(5):
                h = (s(5) + s(16) * abs(math.sin(now * 7 + i * 1.2))) * w["speaking"]
                bx = cx - s(16) + i * s(8)
                painter.rect(bx - s(2), cy - h, bx + s(2), cy + h, mix(PANEL, col, w["speaking"]), radius=s(2))

        orb_r = (w["idle"] * (s(10) + s(1.5) * math.sin(now * 2))   # orb: a blend of what each look wants
                 + w["hearing"] * (s(10) + s(14) * level)
                 + w["thinking"] * s(5) + w["acting"] * s(6) + w["error"] * s(14)) / total
        blink = 1 - w["error"] * 0.6 * (0.5 - 0.5 * math.sin(now * 8))
        painter.oval(cx, cy, orb_r, fill=mix(PANEL, col, blink))
        painter.end()

        cv.itemconfigure(title, fill=hexcol(col))

        # ---- the status text fades out, changes, and fades back in ----
        if STATE["text"] != me["text"]:
            me["fade"] -= dt * 9
            if me["fade"] <= 0:
                me["text"] = STATE["text"]
                cv.itemconfigure(status, text=me["text"])
                me["fade"] = 0.0
        else:
            me["fade"] = min(1.0, me["fade"] + dt * 9)
        cv.itemconfigure(status, fill=hexcol(mix(PANEL, TEXTC, me["fade"])))

        root.after(max(1, int(16 - (time.perf_counter() - t0) * 1000)), tick)

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
    # Listens for the global hotkeys (they work while you are in any other program).
    user32 = ctypes.windll.user32
    registered = []
    for hid, spec, label in ((1, TEXT_HOTKEY, "type a command to Max"), (2, STOP_HOTKEY, "stop what Max is doing")):
        if not spec:
            continue
        try:
            flags, vk = parse_hotkey(spec)
        except ValueError as e:
            print(f"[Hotkey '{spec}' problem: {e}]")
            continue
        if user32.RegisterHotKey(None, hid, flags | 0x4000, vk):        # 0x4000 = no repeat while held
            registered.append(hid)
            print(f"Hotkey {spec}: {label}")
        else:
            print(f"[The hotkey {spec} is already used by another program. Change it at the top of the file.]")
    if not registered:
        return
    msg = wintypes.MSG()
    try:
        while not stop_event.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0x0312, 0x0312, 1):   # 0x0312 = WM_HOTKEY
                if msg.wParam == 2:
                    task_stop.set()
                else:
                    text_bar_open.set()
            else:
                time.sleep(0.05)
    finally:
        for hid in registered:
            user32.UnregisterHotKey(None, hid)


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
    try:                                                      # the typing box gets the Max icon too
        ico = icon_file()
        if ico:
            bar.iconbitmap(ico)
    except Exception:
        pass
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

    def fade_in(born):
        p = min(1.0, (time.time() - born) / 0.16)
        bar.attributes("-alpha", 1 - (1 - p) ** 3)
        if p < 1.0:
            bar.after(16, lambda: fade_in(born))

    def show():
        bar.geometry(f"{W}x{H}+{(sw - W) // 2}+{int(sh * 0.28)}")
        bar.attributes("-alpha", 0.0)
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
        fade_in(time.time())

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

    def stop_task(icon, item):
        task_stop.set()

    def stop_watching_item(icon, item):
        stop_watch()

    def stop_study_item(icon, item):
        stop_study()

    menu = pystray.Menu(pystray.MenuItem("Stop current task", stop_task),
                        pystray.MenuItem("Stop watching the screen", stop_watching_item),
                        pystray.MenuItem("End study mode", stop_study_item),
                        pystray.MenuItem("Microphone", pystray.Menu(mic_items)),
                        pystray.MenuItem("Open log", open_log),
                        pystray.MenuItem("Forget API key and quit", forget_key),
                        pystray.MenuItem("Quit Max", quit_app))
    icon = pystray.Icon("Max", tray_image("idle"), "Max - ready", menu)
    _tray[0] = icon
    icon.run_detached()
    return icon


# ---------------- Connection to the Settings app (settings.json, status.json, devices.json) ----------------
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
_settings_seen = [None]


def write_json_atomic(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        print(f"  [Could not write {os.path.basename(path)}] {e}")


def apply_settings(data, first=False):
    # Copies the values saved by the Settings app into Max's live settings.
    global MIC, WAKE_REGEX, _voice
    g = globals()

    def pick(key, name, kind):
        if key not in data:
            return
        v = data[key]
        try:
            if kind == "bool":
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif kind == "int":
                v = int(v)
            elif kind == "str":
                v = None if v is None else str(v)
            elif kind == "strlist":
                v = [str(x).strip() for x in v if str(x).strip()]
        except (TypeError, ValueError):
            return
        g[name] = v

    old_mic, old_voice = MIC, (VOICE_RATE, VOICE_VOLUME)
    for key, name, kind in (("mic", "MIC", "str"), ("reply_language", "REPLY_LANGUAGE", "str"),
                            ("speech_language", "LANGUAGE", "str"), ("model", "MODEL", "str"),
                            ("auto_pick_model", "AUTO_PICK_MODEL", "bool"), ("agent_model", "AGENT_MODEL", "str"),
                            ("agent_max_steps", "AGENT_MAX_STEPS", "int"), ("text_hotkey", "TEXT_HOTKEY", "str"),
                            ("stop_hotkey", "STOP_HOTKEY", "str"), ("overlay", "OVERLAY", "bool"),
                            ("tray_icon", "TRAY_ICON", "bool"), ("wake_beep", "WAKE_BEEP", "bool"),
                            ("loading_screen", "SHOW_LOADING_SCREEN", "bool"),
                            ("study_distractions", "STUDY_DISTRACTIONS", "strlist"),
                            ("editor", "CODE_EDITOR", "str"), ("projects_folder", "PROJECTS_FOLDER", "str"),
                            ("coding_language", "CODING_LANGUAGE", "str"), ("coding_level", "CODING_LEVEL", "str"),
                            ("voice_rate", "VOICE_RATE", "int"), ("voice_volume", "VOICE_VOLUME", "int"),
                            ("mic_sensitivity", "MIC_SENSITIVITY", "int")):
        pick(key, name, kind)
    if isinstance(data.get("wake_words"), list):
        words = [str(w).lower().strip() for w in data["wake_words"] if str(w).strip()]
        if words:
            WAKE_WORDS[:] = words
            WAKE_REGEX = build_wake_regex()
    # keep the values in a sensible range
    g["AGENT_MAX_STEPS"] = max(1, min(100, int(AGENT_MAX_STEPS)))
    g["VOICE_RATE"] = max(-10, min(10, int(VOICE_RATE)))
    g["VOICE_VOLUME"] = max(0, min(100, int(VOICE_VOLUME)))
    g["MIC_SENSITIVITY"] = max(1, min(10, int(MIC_SENSITIVITY)))
    if str(CODING_LEVEL).lower() not in ("beginner", "intermediate", "expert"):
        g["CODING_LEVEL"] = "beginner"
    if REPLY_LANGUAGE not in LANGUAGE_RULES:
        g["REPLY_LANGUAGE"] = "english"
    if not first:                                              # changes that can be applied while Max runs
        if MIC != old_mic:
            new_mic, MIC = MIC, old_mic
            mic_request[:] = [("set", new_mic)]
        if (VOICE_RATE, VOICE_VOLUME) != old_voice and _voice is not None:
            try:
                _voice.terminate()                             # the voice restarts with the new speed/volume
            except Exception:
                pass
            _voice = None


def reload_settings_if_changed():
    try:
        stamp = os.path.getmtime(SETTINGS_FILE)
    except OSError:
        return
    if stamp != _settings_seen[0]:
        first = _settings_seen[0] is None
        _settings_seen[0] = stamp
        apply_settings(load_json("settings.json", {}), first)
        if not first:
            print("[Settings reloaded from the Settings app]")
            set_state("idle", "Settings updated")


def heartbeat_loop():
    # Tells the Settings app that Max is running (and which model/microphone he uses).
    while not stop_event.is_set():
        write_json_atomic(STATUS_FILE, {"running": True, "pid": os.getpid(), "time": time.time(),
                                        "state": STATE["name"], "model": MODEL,
                                        "mic": mic.name if mic is not None else None,
                                        "reply_language": REPLY_LANGUAGE})
        stop_event.wait(3)
    write_json_atomic(STATUS_FILE, {"running": False, "time": time.time()})


def main():
    lock = single_instance()                  # keep a reference so the lock stays active
    reload_settings_if_changed()              # first read of settings.json (made by the Settings app)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    splash_status("Starting...")
    tray = start_tray()
    if OVERLAY:
        threading.Thread(target=run_overlay, daemon=True).start()
    if TEXT_HOTKEY or STOP_HOTKEY:
        threading.Thread(target=hotkey_loop, daemon=True).start()
    if TEXT_HOTKEY:
        threading.Thread(target=run_text_mode, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
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
            reload_settings_if_changed()       # picks up changes saved in the Settings app
            apply_mic_request()                # switch microphone if you picked one in the tray menu
            if not say_queue.empty():          # Max has something to tell you (screen watcher, study mode)
                speak(say_queue.get())
                continue
            if not scheduled_queue.empty():    # a scheduled routine is due
                scheduled = scheduled_queue.get()
                print(f"[Scheduled] {scheduled}")
                set_state("hearing", "Scheduled routine")
                handle(scheduled, unattended=True)
                set_state("idle", "Done")
                continue
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
            try:
                heard, alts = listen(timeout=5, limit=8)
            except Exception as e:                         # the microphone stopped (unplugged, Bluetooth switch, sleep)
                print(f"[Microphone problem: {e}] Reopening it...")
                close_mic()
                time.sleep(2)
                try:
                    open_mic()
                    calibrate()
                except Exception as e2:
                    print(f"[Could not reopen the microphone: {e2}]")
                    time.sleep(5)
                continue
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
