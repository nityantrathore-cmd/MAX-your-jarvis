# Max Settings - a separate app that edits Max's settings file.
# It saves to  %APPDATA%\Max\settings.json  and Max reloads that file by himself within a few seconds.
# Run:  python max_settings.py     (or build MaxSettings.exe with build_settings_exe.bat)

import ctypes
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)          # sharp window on scaled displays
except Exception:
    pass

APP_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "Max")
os.makedirs(DATA_DIR, exist_ok=True)
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
KEY_STORE = os.path.join(DATA_DIR, "key.dat")

# Colours (same family as Max's own windows)
BG, SIDEBAR, PANEL, BORDER = "#0f1116", "#12141a", "#1a1d26", "#30364a"
ACCENT, ACCENT_DIM, TEXT, MUTED, ERR = "#00be87", "#0a7a58", "#ecf0f5", "#8c96a8", "#ff6969"

DEFAULTS = {
    "overlay": True, "loading_screen": True, "tray_icon": True, "wake_beep": True,
    "speech_language": "en-US", "reply_language": "english", "voice_rate": 0, "voice_volume": 100, "mic_sensitivity": 6,
    "wake_words": ["max", "macs", "mack", "maxx", "mex"],
    "auto_pick_model": True, "agent_max_steps": 30,
    "text_hotkey": "ctrl+alt+m", "stop_hotkey": "ctrl+alt+x",
    "editor": "notepad", "projects_folder": "", "coding_language": "python", "coding_level": "beginner",
    "study_distractions": ["youtube", "instagram", "facebook", "netflix", "twitter", "reddit", "steam", "tiktok",
                           "minecraft", "fortnite", "roblox", "valorant", "pubg"],
}
SPEECH_LANGS = {"English (US)": "en-US", "English (India)": "en-IN", "English (UK)": "en-GB", "Hindi": "hi-IN"}
REPLY_LANGS = {"English": "english", "Hinglish (Hindi in English letters)": "hinglish", "Hindi (needs a Hindi voice)": "hindi"}
LEVELS = ["beginner", "intermediate", "expert"]
CODE_LANGS = ["python", "javascript", "typescript", "html", "css", "java", "c", "c++", "c#", "go", "rust", "php", "sql"]
EDITORS = {"Notepad": "notepad", "VS Code": "code", "Custom path...": ""}
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash"]
MAX_DEFAULT = "(Max's own default)"
HOTKEY_RE = re.compile(r"^(?:(?:ctrl|alt|shift|win)\+)*(?:[a-z0-9]|f(?:[1-9]|1[0-9]|2[0-4])|space|enter|tab)$")


# ---------------------------------------------------------------- files
def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)                                    # never leaves a half-written file for Max to read


def load_settings():
    data = read_json(SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def check_hotkey(spec):
    spec = spec.lower().replace(" ", "")
    if spec == "":
        return True
    if not HOTKEY_RE.match(spec):
        return False
    parts = spec.split("+")
    return len(parts) > 1 or bool(re.fullmatch(r"f\d+", parts[0]))


# ---------------------------------------------------------------- API key (same protection Max uses)
def _protect(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]
    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.c_void_p).value)
    dst = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("Windows could not encrypt the key")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(dst.pbData))


def save_key(key):
    try:
        blob = b"dpapi:" + _protect(key.encode("utf-8"))
    except Exception:
        blob = b"plain:" + key.encode("utf-8")
    with open(KEY_STORE, "wb") as f:
        f.write(blob)


def key_is_valid(key):
    # True = Google accepts it, False = rejected, None = could not check (offline)
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
                                 headers={"x-goog-api-key": key})
    try:
        urllib.request.urlopen(req, timeout=15).close()
        return True
    except urllib.error.HTTPError as e:
        return False if e.code in (400, 401, 403) else None
    except Exception:
        return None


def max_status():
    st = read_json(STATUS_FILE, {})
    running = bool(st.get("running")) and (time.time() - float(st.get("time", 0))) < 9
    return running, st


# ---------------------------------------------------------------- smooth drawing helpers (Pillow)
def hexmix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x * (1 - t) + y * t) for x, y in zip(a, b))


def to_rgb(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def make_switch_images(root, w, h):
    # Two smooth pictures (off / on) of a toggle switch, drawn big and shrunk for clean edges.
    # Returns None if Pillow's Tk bridge is missing (the switch then shows plain ON / OFF text).
    try:
        from PIL import Image, ImageDraw, ImageTk
        k = 4
        res = getattr(Image, "Resampling", Image).LANCZOS
        out = []
        for on in (False, True):
            img = Image.new("RGB", (w * k, h * k), to_rgb(BG))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=h * k // 2, fill=to_rgb(ACCENT if on else BORDER))
            r = h * k // 2 - 3 * k
            cx = (w * k - h * k // 2) if on else h * k // 2
            d.ellipse((cx - r, h * k // 2 - r, cx + r, h * k // 2 + r), fill=to_rgb(TEXT if on else MUTED))
            out.append(ImageTk.PhotoImage(img.resize((w, h), res), master=root))
        return out
    except Exception:
        return None


def make_orb_image(root, size):
    try:
        from PIL import Image, ImageDraw, ImageTk
        k = 4
        img = Image.new("RGB", (size * k, size * k), to_rgb(SIDEBAR))
        d = ImageDraw.Draw(img)
        c = size * k // 2
        for r, t in ((0.46, 0.55), (0.37, 0.4)):
            rr = int(size * k * r)
            d.ellipse((c - rr, c - rr, c + rr, c + rr), outline=to_rgb(hexmix(ACCENT, SIDEBAR, t)), width=2 * k)
        rr = int(size * k * 0.27)
        d.ellipse((c - rr, c - rr, c + rr, c + rr), fill=to_rgb(ACCENT))
        res = getattr(Image, "Resampling", Image).LANCZOS
        return ImageTk.PhotoImage(img.resize((size, size), res), master=root)
    except Exception:
        return None


# ---------------------------------------------------------------- the app
class SettingsApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Max Settings")
        self.root.configure(bg=BG)
        self.k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        W, H = int(940 * self.k), int(640 * self.k)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 3}")
        self.root.minsize(int(820 * self.k), int(560 * self.k))
        self._dark_title_bar()
        self._icon()
        self._style()

        self.vars = {}                                     # widget variables, by setting name
        self.lists = {}                                    # list editors, by setting name
        self.mics = read_json(DEVICES_FILE, {}).get("mics", [])
        self.saved = load_settings()
        self.page_frames, self.nav_labels, self.current = {}, {}, None
        self.switch_imgs = make_switch_images(self.root, int(46 * self.k), int(26 * self.k))

        self._layout()
        self.fill(self.saved)
        self.show("General")
        self.refresh_status()
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    # ---------- window basics
    def px(self, v):
        return int(v * self.k)

    def _dark_title_bar(self):
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:
            pass

    def _icon(self):
        for name in ("maxSettingsAA.ico", "maxAA.ico"):                  # its own gear icon first
            path = next((p for p in (os.path.join(getattr(sys, "_MEIPASS", APP_DIR), name), os.path.join(APP_DIR, name))
                         if os.path.exists(p)), None)
            if path:
                try:
                    self.root.iconbitmap(path)
                    return
                except Exception:
                    pass

    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=ACCENT,
                     bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL, padding=self.px(6))
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        st.configure("TSpinbox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=ACCENT,
                     bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL, padding=self.px(6))
        st.configure("Horizontal.TScale", background=BG, troughcolor=PANEL, bordercolor=BORDER,
                     lightcolor=ACCENT, darkcolor=ACCENT)
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    def font(self, size=10, weight="normal"):
        return ("Segoe UI Semibold" if weight == "bold" else "Segoe UI", size)

    # ---------- layout: sidebar + pages + footer
    def _layout(self):
        side = tk.Frame(self.root, bg=SIDEBAR, width=self.px(210))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.orb = make_orb_image(self.root, self.px(46))
        head = tk.Frame(side, bg=SIDEBAR)
        head.pack(fill="x", padx=self.px(18), pady=(self.px(22), self.px(18)))
        if self.orb is not None:
            tk.Label(head, image=self.orb, bg=SIDEBAR).pack(side="left")
        box = tk.Frame(head, bg=SIDEBAR)
        box.pack(side="left", padx=self.px(10))
        tk.Label(box, text="Max", bg=SIDEBAR, fg=TEXT, font=self.font(16, "bold")).pack(anchor="w")
        tk.Label(box, text="Settings", bg=SIDEBAR, fg=MUTED, font=self.font(10)).pack(anchor="w")

        for name in ("General", "Voice & language", "Wake words", "AI & account", "Hotkeys", "Coding", "Study mode",
                     "Data", "About"):
            lbl = tk.Label(side, text="   " + name, bg=SIDEBAR, fg=MUTED, anchor="w", cursor="hand2",
                           font=self.font(11), pady=self.px(9))
            lbl.pack(fill="x", padx=self.px(10), pady=self.px(1))
            lbl.bind("<Button-1>", lambda e, n=name: self.show(n))
            lbl.bind("<Enter>", lambda e, n=name: self.nav_labels[n].config(bg=PANEL) if n != self.current else None)
            lbl.bind("<Leave>", lambda e, n=name: self.nav_labels[n].config(bg=SIDEBAR) if n != self.current else None)
            self.nav_labels[name] = lbl

        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        top = tk.Frame(main, bg=BG)
        top.pack(fill="x", padx=self.px(32), pady=(self.px(22), 0))
        self.title = tk.Label(top, text="", bg=BG, fg=TEXT, font=self.font(20, "bold"))
        self.title.pack(side="left")
        self.chip = tk.Label(top, text="", bg=BG, fg=MUTED, font=self.font(10))
        self.chip.pack(side="right")

        self.body = tk.Frame(main, bg=BG)
        self.body.pack(fill="both", expand=True, padx=self.px(32), pady=self.px(14))

        foot = tk.Frame(main, bg=SIDEBAR)
        foot.pack(fill="x", side="bottom")
        self.msg = tk.Label(foot, text="", bg=SIDEBAR, fg=MUTED, font=self.font(10), anchor="w")
        self.msg.pack(side="left", padx=self.px(24), pady=self.px(14))
        self.button(foot, "Save", self.save, primary=True).pack(side="right", padx=(self.px(8), self.px(24)), pady=self.px(10))
        self.button(foot, "Reset to defaults", self.reset).pack(side="right", pady=self.px(10))

        pages = {"General": self.page_general, "Voice & language": self.page_voice, "Wake words": self.page_wake,
                 "AI & account": self.page_ai, "Hotkeys": self.page_hotkeys, "Coding": self.page_coding,
                 "Study mode": self.page_study, "Data": self.page_data, "About": self.page_about}
        for name, builder in pages.items():
            frame = tk.Frame(self.body, bg=BG)
            self.page_frames[name] = frame
            builder(frame)

    def show(self, name):
        if self.current:
            self.page_frames[self.current].pack_forget()
            self.nav_labels[self.current].config(bg=SIDEBAR, fg=MUTED)
        self.current = name
        self.page_frames[name].pack(fill="both", expand=True)
        self.nav_labels[name].config(bg=PANEL, fg=ACCENT)
        self.title.config(text=name)

    # ---------- small building blocks
    def button(self, parent, text, command, primary=False):
        bg, fg = (ACCENT, "#04140e") if primary else (PANEL, TEXT)
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2", font=self.font(10, "bold"),
                       padx=self.px(16), pady=self.px(8))
        hover = hexmix(bg, "#ffffff", 0.18)
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>", lambda e: lbl.config(bg=hover))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg))
        return lbl

    def row(self, parent, label, hint=""):
        r = tk.Frame(parent, bg=BG)
        r.pack(fill="x", pady=self.px(7))
        left = tk.Frame(r, bg=BG)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=label, bg=BG, fg=TEXT, font=self.font(11), anchor="w").pack(anchor="w")
        if hint:
            tk.Label(left, text=hint, bg=BG, fg=MUTED, font=self.font(9), anchor="w", justify="left",
                     wraplength=self.px(420)).pack(anchor="w")
        right = tk.Frame(r, bg=BG)
        right.pack(side="right")
        return right

    def switch(self, parent, key):
        var = tk.BooleanVar(value=bool(DEFAULTS.get(key, False)))
        self.vars[key] = var
        lbl = tk.Label(parent, bg=BG, cursor="hand2", fg=TEXT, font=self.font(10, "bold"))

        def paint(*_):
            on = var.get()
            if self.switch_imgs:
                lbl.config(image=self.switch_imgs[1 if on else 0])
            else:
                lbl.config(text="ON" if on else "OFF", fg=ACCENT if on else MUTED)
        lbl.bind("<Button-1>", lambda e: (var.set(not var.get()), paint()))
        var.trace_add("write", paint)
        paint()
        lbl.pack()

    def combo(self, parent, key, values, width=26, readonly=True):
        var = tk.StringVar()
        self.vars[key] = var
        box = ttk.Combobox(parent, textvariable=var, values=values, width=width,
                           state="readonly" if readonly else "normal")
        box.pack()
        return box

    def entry(self, parent, key, width=28):
        var = tk.StringVar()
        self.vars[key] = var
        e = tk.Entry(parent, textvariable=var, width=width, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightthickness=self.px(1), highlightbackground=BORDER, highlightcolor=ACCENT,
                     font=self.font(11))
        e.pack(ipady=self.px(5))
        return e

    def slider(self, parent, key, lo, hi, suffix=""):
        var = tk.IntVar(value=DEFAULTS.get(key, lo))
        self.vars[key] = var
        value = tk.Label(parent, text="", bg=BG, fg=ACCENT, font=self.font(10, "bold"), width=6)
        scale = ttk.Scale(parent, from_=lo, to=hi, orient="horizontal", length=self.px(190))

        def on_scale(v):                                   # slider moved -> update the number
            n = int(round(float(v)))
            if var.get() != n:
                var.set(n)
            value.config(text=f"{n}{suffix}")

        def on_var(*_):                                    # number changed (e.g. loading) -> move the slider
            if abs(float(scale.get()) - var.get()) > 0.5:
                scale.set(var.get())
            value.config(text=f"{var.get()}{suffix}")
        scale.config(command=on_scale)
        scale.pack(side="left")
        value.pack(side="left", padx=(self.px(8), 0))
        var.trace_add("write", on_var)
        on_var()

    def list_editor(self, parent, key, height=8, on_change=None, add_label="Add", allow_add=True):
        # A list with an add box and a remove button.
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, pady=self.px(6))
        lb = tk.Listbox(wrap, height=height, bg=PANEL, fg=TEXT, selectbackground=ACCENT_DIM, selectforeground=TEXT,
                        relief="flat", highlightthickness=self.px(1), highlightbackground=BORDER, activestyle="none",
                        font=self.font(11))
        lb.pack(side="left", fill="both", expand=True)
        side = tk.Frame(wrap, bg=BG)
        side.pack(side="left", fill="y", padx=(self.px(12), 0))
        var = tk.StringVar()
        e = tk.Entry(side, textvariable=var, width=26, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightthickness=self.px(1), highlightbackground=BORDER, highlightcolor=ACCENT,
                     font=self.font(11))
        if allow_add:
            e.pack(ipady=self.px(5), pady=(0, self.px(8)))

        def items():
            return list(lb.get(0, "end"))

        def add(event=None):
            text = var.get().strip()
            if text and text not in items():
                lb.insert("end", text)
                var.set("")
                if on_change:
                    on_change(items())

        def remove():
            for i in reversed(lb.curselection()):
                lb.delete(i)
            if on_change:
                on_change(items())
        e.bind("<Return>", add)
        if allow_add:
            self.button(side, add_label, add, primary=True).pack(fill="x", pady=self.px(3))
        self.button(side, "Remove selected", remove).pack(fill="x", pady=self.px(3))
        self.lists[key] = (lb, items)
        return lb

    def set_list(self, key, values):
        lb, _ = self.lists[key]
        lb.delete(0, "end")
        for v in values:
            lb.insert("end", v)

    # ---------- pages
    def page_general(self, f):
        for key, label, hint in (
                ("overlay", "Floating status pill", "The small glowing pill near the clock that shows what Max is doing."),
                ("loading_screen", "Loading screen", "The small \"Max is loading\" window when Max starts."),
                ("tray_icon", "Tray icon", "The little icon near the clock with Stop / Quit / Microphone."),
                ("wake_beep", "Beep when Max hears his name", "A short beep so you know when to talk.")):
            self.switch(self.row(f, label, hint), key)
        tk.Label(f, text="The pill, loading screen and tray icon change the next time Max starts.", bg=BG, fg=MUTED,
                 font=self.font(9)).pack(anchor="w", pady=(self.px(14), 0))

    def page_voice(self, f):
        mic_values = [MAX_DEFAULT, "Windows default"] + list(self.mics)
        self.combo(self.row(f, "Microphone", "Pick the microphone Max listens to. The list appears after Max has "
                                             "started once."), "mic", mic_values, 34, readonly=False)
        self.combo(self.row(f, "I speak", "Language of your voice (speech recognition)."), "speech_language",
                   list(SPEECH_LANGS), 26)
        self.combo(self.row(f, "Max answers in", "Hinglish is Hindi written in English letters, so any voice can "
                                                 "read it."), "reply_language", list(REPLY_LANGS), 34)
        self.slider(self.row(f, "Microphone sensitivity", "Raise it if Max misses his name (quiet voice or mic). "
                                                          "Lower it if noise keeps waking him."), "mic_sensitivity", 1, 10)
        self.slider(self.row(f, "Voice speed", "-10 is slow, 10 is fast."), "voice_rate", -10, 10)
        self.slider(self.row(f, "Voice volume"), "voice_volume", 0, 100, "%")
        self.button(self.row(f, "Test the voice"), "Play sample", self.test_voice).pack()

    def page_wake(self, f):
        tk.Label(f, text="Words that wake Max. Add spellings the microphone often hears when you say his name.",
                 bg=BG, fg=MUTED, font=self.font(10), wraplength=self.px(620), justify="left").pack(anchor="w")
        self.list_editor(f, "wake_words", 8, add_label="Add word")
        tk.Label(f, text="\"hey\", \"ok\" and \"hi\" before the name always work too.", bg=BG, fg=MUTED,
                 font=self.font(9)).pack(anchor="w")

    def page_ai(self, f):
        self.combo(self.row(f, "AI model", "Leave it on Max's own default unless a model is slow or missing."),
                   "model", [MAX_DEFAULT] + MODELS, 28, readonly=False)
        self.switch(self.row(f, "Pick the fastest model at startup", "Max quickly tests a few models when he starts."),
                    "auto_pick_model")
        self.combo(self.row(f, "Model for long tasks", "Used when Max works step by step on the screen."),
                   "agent_model", [MAX_DEFAULT] + MODELS, 28, readonly=False)
        spin = tk.IntVar(value=DEFAULTS["agent_max_steps"])
        self.vars["agent_max_steps"] = spin
        ttk.Spinbox(self.row(f, "Longest task (steps)", "Each step is one screenshot plus a few actions."),
                    from_=1, to=100, textvariable=spin, width=8).pack()
        r = self.row(f, "Gemini API key", "Stored encrypted on this PC. Max reads it when he starts.")
        self.button(r, "Forget key", self.forget_key).pack(side="right", padx=(self.px(8), 0))
        self.button(r, "Change key...", self.change_key, primary=True).pack(side="right")

    def page_hotkeys(self, f):
        self.entry(self.row(f, "Type a command to Max", "Press this anywhere to open the typing box. Example: ctrl+alt+m. "
                                                          "Empty = off."), "text_hotkey", 18)
        self.entry(self.row(f, "Stop what Max is doing", "Stops a running task at once. Example: ctrl+alt+x. "
                                                           "Empty = off."), "stop_hotkey", 18)
        tk.Label(f, text="Use ctrl, alt, shift or win plus one key (a letter, number, F1-F24, space...). "
                         "Hotkeys change the next time Max starts.", bg=BG, fg=MUTED, font=self.font(9),
                 wraplength=self.px(620), justify="left").pack(anchor="w", pady=(self.px(12), 0))

    def page_coding(self, f):
        tk.Label(f, text="Max can write code for you, save it in a folder, and open it in your editor. "
                         "He never runs the code.", bg=BG, fg=MUTED, font=self.font(10), wraplength=self.px(620),
                 justify="left").pack(anchor="w", pady=(0, self.px(8)))
        self.combo(self.row(f, "Programming language", "Used when you don't name one."), "coding_language", CODE_LANGS, 20,
                   readonly=False)
        self.combo(self.row(f, "Explain things at", "How simply Max explains code and ideas."), "coding_level",
                   LEVELS, 20)
        r = self.row(f, "Editor", "The program that opens the code files.")
        self.combo(r, "editor_choice", list(EDITORS), 16)
        self.vars["editor_choice"].trace_add("write", lambda *a: self.editor_changed())
        self.editor_path = self.entry(f, "editor_path", 52)
        self.editor_path.pack_configure(anchor="e", pady=(0, self.px(6)))
        r = self.row(f, "Projects folder", "Where code files are saved. Empty = Documents\\Max Projects.")
        self.button(r, "Open folder", self.open_projects).pack(side="right", padx=(self.px(8), 0))
        self.button(r, "Browse...", self.browse_projects).pack(side="right")
        self.entry(f, "projects_folder", 52).pack_configure(anchor="e")

    def page_study(self, f):
        tk.Label(f, text="In study mode Max reminds you when the active window's title contains one of these words.",
                 bg=BG, fg=MUTED, font=self.font(10), wraplength=self.px(620), justify="left").pack(anchor="w")
        self.list_editor(f, "study_distractions", 9, add_label="Add word")

    def page_data(self, f):
        tk.Label(f, text="Things Max remembers and does for you. Changes here are saved straight away.", bg=BG,
                 fg=MUTED, font=self.font(10)).pack(anchor="w")
        nb = ttk.Notebook(f)
        style = ttk.Style(self.root)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=TEXT, padding=(self.px(14), self.px(6)))
        style.map("TNotebook.Tab", background=[("selected", ACCENT_DIM)])
        nb.pack(fill="both", expand=True, pady=self.px(8))
        for key, title, filename in (("memory", "Memory", "memory.json"), ("skills", "Skills", "skills.json"),
                                     ("schedules", "Schedules", "schedules.json")):
            tab = tk.Frame(nb, bg=BG)
            nb.add(tab, text=title)
            self.list_editor(tab, key, 8, on_change=lambda items, k=key: self.save_data(k, items),
                             add_label="Add", allow_add=(key == "memory"))
            if key != "memory":
                tk.Label(tab, text="Create these by talking to Max (for example: \"save that as morning routine\"). "
                                   "You can remove them here.", bg=BG, fg=MUTED, font=self.font(9),
                         wraplength=self.px(620), justify="left").pack(anchor="w")
        self.load_data()

    def page_about(self, f):
        tk.Label(f, text="Max Settings", bg=BG, fg=TEXT, font=self.font(14, "bold")).pack(anchor="w")
        tk.Label(f, text="This app only edits a file: " + SETTINGS_FILE + "\nMax checks that file every few seconds "
                         "and applies your changes, so you can leave both open.", bg=BG, fg=MUTED, font=self.font(10),
                 justify="left", wraplength=self.px(620)).pack(anchor="w", pady=self.px(8))
        for text, cmd in (("Open data folder", lambda: self.open_path(DATA_DIR)),
                          ("Open Max's log", lambda: self.open_path(os.path.join(DATA_DIR, "max.log")))):
            self.button(f, text, cmd).pack(anchor="w", pady=self.px(4))

    # ---------- data tab
    def load_data(self):
        mem = read_json(os.path.join(DATA_DIR, "memory.json"), [])
        self.set_list("memory", [m for m in mem if isinstance(m, str)])
        skills = read_json(os.path.join(DATA_DIR, "skills.json"), {})
        self.set_list("skills", list(skills) if isinstance(skills, dict) else [])
        sched = read_json(os.path.join(DATA_DIR, "schedules.json"), [])
        self.set_list("schedules", [f"{e.get('time')} {e.get('days')}: {e.get('command')}"
                                    for e in sched if isinstance(e, dict)])

    def save_data(self, key, items):
        # Removing from a list also removes it from Max's file. (Skills and schedules are created by talking to Max.)
        try:
            if key == "memory":
                write_json(os.path.join(DATA_DIR, "memory.json"), items)
            elif key == "skills":
                skills = read_json(os.path.join(DATA_DIR, "skills.json"), {})
                write_json(os.path.join(DATA_DIR, "skills.json"), {k: v for k, v in skills.items() if k in items})
            elif key == "schedules":
                sched = read_json(os.path.join(DATA_DIR, "schedules.json"), [])
                keep = [e for e in sched if f"{e.get('time')} {e.get('days')}: {e.get('command')}" in items]
                write_json(os.path.join(DATA_DIR, "schedules.json"), keep)
            self.say("Saved.", ACCENT)
        except OSError as e:
            self.say(f"Could not save: {e}", ERR)

    # ---------- fill / collect / save
    def fill(self, data):
        d = {**DEFAULTS, **data}
        for key in ("overlay", "loading_screen", "tray_icon", "wake_beep", "auto_pick_model"):
            self.vars[key].set(bool(d.get(key, True)))
        mic = data.get("mic", MAX_DEFAULT) if "mic" in data else MAX_DEFAULT
        self.vars["mic"].set("Windows default" if ("mic" in data and data["mic"] is None) else (mic or MAX_DEFAULT))
        self.vars["speech_language"].set(next((k for k, v in SPEECH_LANGS.items() if v == d["speech_language"]), "English (US)"))
        self.vars["reply_language"].set(next((k for k, v in REPLY_LANGS.items() if v == d["reply_language"]), "English"))
        self.vars["voice_rate"].set(int(d["voice_rate"]))
        self.vars["voice_volume"].set(int(d["voice_volume"]))
        self.vars["mic_sensitivity"].set(int(d["mic_sensitivity"]))
        self.set_list("wake_words", d["wake_words"])
        self.vars["model"].set(data.get("model") or MAX_DEFAULT)
        self.vars["agent_model"].set(data.get("agent_model") or MAX_DEFAULT)
        self.vars["agent_max_steps"].set(int(d["agent_max_steps"]))
        self.vars["text_hotkey"].set(d["text_hotkey"])
        self.vars["stop_hotkey"].set(d["stop_hotkey"])
        self.vars["coding_language"].set(d["coding_language"])
        self.vars["coding_level"].set(d["coding_level"])
        editor = d["editor"]
        choice = next((k for k, v in EDITORS.items() if v == editor and v), "Custom path...")
        self.vars["editor_choice"].set(choice)
        self.vars["editor_path"].set(editor if choice == "Custom path..." else "")
        self.vars["projects_folder"].set(d["projects_folder"])
        self.set_list("study_distractions", d["study_distractions"])
        self.editor_changed()

    def editor_changed(self):
        custom = self.vars["editor_choice"].get() == "Custom path..."
        self.editor_path.config(state="normal" if custom else "disabled")

    def collect(self):
        v = self.vars
        data = {k: bool(v[k].get()) for k in ("overlay", "loading_screen", "tray_icon", "wake_beep", "auto_pick_model")}
        mic = v["mic"].get().strip()
        if mic == "Windows default":
            data["mic"] = None
        elif mic and mic != MAX_DEFAULT:
            data["mic"] = mic
        data["speech_language"] = SPEECH_LANGS.get(v["speech_language"].get(), "en-US")
        data["reply_language"] = REPLY_LANGS.get(v["reply_language"].get(), "english")
        data["voice_rate"] = int(v["voice_rate"].get())
        data["voice_volume"] = int(v["voice_volume"].get())
        data["mic_sensitivity"] = max(1, min(10, int(v["mic_sensitivity"].get())))
        data["wake_words"] = [w.strip().lower() for w in self.lists["wake_words"][1]() if w.strip()]
        for key in ("model", "agent_model"):
            value = v[key].get().strip()
            if value and value != MAX_DEFAULT:
                data[key] = value
        data["agent_max_steps"] = max(1, min(100, int(v["agent_max_steps"].get())))
        data["text_hotkey"] = v["text_hotkey"].get().strip().lower()
        data["stop_hotkey"] = v["stop_hotkey"].get().strip().lower()
        data["coding_language"] = v["coding_language"].get().strip().lower() or "python"
        data["coding_level"] = v["coding_level"].get()
        choice = v["editor_choice"].get()
        data["editor"] = (v["editor_path"].get().strip() or "notepad") if choice == "Custom path..." else EDITORS.get(choice, "notepad")
        data["projects_folder"] = v["projects_folder"].get().strip()
        data["study_distractions"] = [w.strip().lower() for w in self.lists["study_distractions"][1]() if w.strip()]
        return data

    def validate(self, data):
        for key, label in (("text_hotkey", "Typing hotkey"), ("stop_hotkey", "Stop hotkey")):
            if not check_hotkey(data[key]):
                return f"{label} is not valid. Try something like ctrl+alt+m."
        if data["text_hotkey"] and data["text_hotkey"] == data["stop_hotkey"]:
            return "The two hotkeys must be different."
        if not data["wake_words"]:
            return "Add at least one wake word."
        return None

    def save(self):
        try:
            data = self.collect()
        except (tk.TclError, ValueError) as e:
            self.say(f"Check your values: {e}", ERR)
            return
        problem = self.validate(data)
        if problem:
            self.say(problem, ERR)
            return
        try:
            write_json(SETTINGS_FILE, data)
        except OSError as e:
            self.say(f"Could not save: {e}", ERR)
            return
        running, _ = max_status()
        self.say("Saved. Max will apply it in a few seconds." if running else "Saved. Max will use it when he starts.", ACCENT)

    def reset(self):
        if messagebox.askyesno("Max Settings", "Put every setting back to Max's defaults?", parent=self.root):
            self.fill({})
            self.say("Defaults restored. Press Save to keep them.", MUTED)

    def say(self, text, color=MUTED):
        self.msg.config(text=text, fg=color)

    # ---------- actions
    def refresh_status(self):
        running, st = max_status()
        if running:
            model = st.get("model") or "?"
            self.chip.config(text=f"●  Max is running  ·  {model}", fg=ACCENT)
        else:
            self.chip.config(text="○  Max is not running", fg=MUTED)
        self.root.after(2000, self.refresh_status)

    def test_voice(self):
        rate, vol = int(self.vars["voice_rate"].get()), int(self.vars["voice_volume"].get())

        def run():
            script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      f"$s.Volume = {vol}; $s.Rate = {rate}; $s.Speak('Hello, I am Max. This is how I sound.')")
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=0x08000000)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def open_path(self, path):
        try:
            os.startfile(path)
        except Exception:
            self.say("That file or folder doesn't exist yet.", MUTED)

    def browse_projects(self):
        folder = filedialog.askdirectory(parent=self.root, title="Folder for Max's code")
        if folder:
            self.vars["projects_folder"].set(folder.replace("/", "\\"))

    def open_projects(self):
        folder = self.vars["projects_folder"].get().strip() or os.path.join(os.path.expanduser("~"), "Documents", "Max Projects")
        os.makedirs(folder, exist_ok=True)
        self.open_path(folder)

    def change_key(self):
        win = tk.Toplevel(self.root)
        win.title("Gemini API key")
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        win.geometry(f"{self.px(460)}x{self.px(210)}+{self.root.winfo_x() + self.px(160)}+{self.root.winfo_y() + self.px(160)}")
        tk.Label(win, text="Paste your Gemini API key", bg=BG, fg=TEXT, font=self.font(13, "bold")).pack(pady=(self.px(20), self.px(6)))
        var = tk.StringVar()
        e = tk.Entry(win, textvariable=var, show="\u2022", width=44, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                     highlightthickness=self.px(1), highlightbackground=BORDER, highlightcolor=ACCENT, font=self.font(11))
        e.pack(ipady=self.px(6))
        status = tk.Label(win, text="", bg=BG, fg=MUTED, font=self.font(9))
        status.pack(pady=self.px(8))

        def done():
            key = var.get().strip().strip('"').strip("'")
            if len(key) < 20:
                status.config(text="That doesn't look like a complete key.", fg=ERR)
                return
            status.config(text="Checking with Google...", fg=MUTED)
            win.update_idletasks()
            ok = key_is_valid(key)
            if ok is False:
                status.config(text="Google rejected that key.", fg=ERR)
                return
            save_key(key)
            win.destroy()
            self.say("New key saved. Restart Max to use it.", ACCENT)
        self.button(win, "Save key", done, primary=True).pack()
        e.bind("<Return>", lambda ev: done())
        e.focus_force()

    def forget_key(self):
        if messagebox.askyesno("Max Settings", "Forget the saved key? Max will ask for a new one next time he starts.",
                               parent=self.root):
            try:
                os.remove(KEY_STORE)
                self.say("Key forgotten.", ACCENT)
            except OSError:
                self.say("There was no saved key.", MUTED)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    SettingsApp().run()
