"""Claude Code usage heatmap in the Windows tray. Left-click the icon to show/hide the popup.
Reads ~/.claude/projects/**/*.jsonl."""
import json, glob, os, queue, re, socket, subprocess, sys, time, tkinter as tk
from tkinter import font as tkfont
from datetime import date, datetime, timedelta
import pystray
from PIL import Image, ImageDraw

WEEKS, CELL, GAP, REFRESH_MS = 14, 20, 5, 60_000
FILES = os.path.expanduser("~/.claude/projects/**/*.jsonl")
FONT = "Segoe UI"
PRESETS = [  # Light, Dark, Green, Warm
    dict(bg="#fbfbfe", card="#f1f2fa", fg="#1d1d2b", dim="#8a8ba3",
         lv=["#eceef8", "#c9d6fb", "#9bb4f7", "#7a8cf0", "#7f5fe0", "#5b34b8"]),
    dict(bg="#14141c", card="#1d1d28", fg="#f0f0f7", dim="#80819a",
         lv=["#242433", "#3a4a9c", "#4f63d9", "#7a5cf0", "#a855f7", "#e879f9"]),
    dict(bg="#0d1117", card="#151b23", fg="#e6edf3", dim="#7d8590",
         lv=["#1b222b", "#0e4429", "#006d32", "#26a641", "#39d353", "#7ee787"]),
    dict(bg="#fff7f4", card="#ffece5", fg="#4d302b", dim="#b38f86",
         lv=["#f6e6df", "#ffdcbf", "#ffbd8d", "#fb9a60", "#e8743a", "#c4501d"]),
]
PRESET_NAMES = ["Light", "Dark", "Green", "Warm"]
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_usage_tray.json")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_cfg():
    try:
        return json.load(open(CONFIG, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cfg(**kw):  # theme index, USD/tokens mode, and your own colors all live in one small json file
    cfg.update(kw)
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), indent=1)


def valid(c):
    try:
        return all(HEX.match(c[k]) for k in ("bg", "card", "fg", "dim")) and len(c["lv"]) == 6 \
            and all(HEX.match(x) for x in c["lv"])
    except (KeyError, TypeError):
        return False


cfg = load_cfg()
CUSTOM = cfg["custom"] if valid(cfg.get("custom")) else dict(PRESETS[0])  # your own colors, set via tray > Colors...
THEMES = PRESETS + [CUSTOM]  # double-click the title to cycle through all of them
ti = int(cfg.get("theme", 0)) % len(THEMES)
T = THEMES[ti]
BG, CARD, FG, DIM, LEVELS = T["bg"], T["card"], T["fg"], T["dim"], T["lv"]
usd_mode = cfg.get("mode", "usd") != "tokens"
PRICE = {  # $/1M tokens: (input, output, cache read); cache write = 1.25x input (5m) or 2x (1h)
    "claude-opus-5-5": (4, 20, .20), "claude-opus-5": (5, 25, .5), "claude-sonnet-5-5": (2, 10, .20),
    "claude-sonnet-5": (2, 10, .2), "claude-fable-5-1": (10, 50, .25), "claude-fable-5": (10, 50, 1.0),
    "claude-haiku-5-5": (.1, .5, .01), "claude-opus-4-8": (5, 25, .5), "claude-opus-4-7": (5, 25, .5),
    "claude-opus-4-6": (5, 25, .5), "claude-sonnet-4-6": (3, 15, .3), "claude-haiku-4-5": (1, 5, .1)}
CODEX_PRICE = {  # $/1M tokens: (input, cached input, output); list prices from public pages, check before trusting
    "gpt-6-astra": (10, 1, 50), "gpt-5.6-sol": (4, .4, 20),  # sol: promo rate, list is 5 / .5 / 30
    "gpt-5.6-terra": (2, .2, 12), "gpt-5.5": (5, .5, 30), "gpt-5.4": (2.5, .25, 15),
    "gpt-5.4-mini": (.75, .075, 4.5), "gpt-5.3-codex": (1.75, .175, 14)}  # unlisted models count as $0
CODEX_FILES = [os.path.expanduser("~/.codex/sessions/**/*.jsonl"), os.path.expanduser("~/.codex/archived_sessions/**/*.jsonl")]
SOURCES = ["claude", "codex", "both"]
source = cfg.get("source", "claude") if cfg.get("source") in SOURCES else "claude"
_cache = {}  # path -> ((mtime, size), parsed log); shared by Claude and Codex files


def scan(path):
    out = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
                m, u = d["message"], d["message"]["usage"]
                day = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).astimezone().date()
            except (KeyError, ValueError, TypeError):
                continue
            n = sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens"))
            i, o, r = PRICE.get(m.get("model"), (0, 0, 0))  # unknown/synthetic models cost nothing
            w1 = (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
            w5 = (u.get("cache_creation_input_tokens") or 0) - w1
            usd = ((u.get("input_tokens") or 0) * i + (u.get("output_tokens") or 0) * o + w5 * i * 1.25
                   + w1 * i * 2 + (u.get("cache_read_input_tokens") or 0) * r) / 1e6
            out[m.get("id") or d.get("uuid")] = (day, n, usd)  # dedupe streamed duplicates
    return out


def scan_codex(path):  # -> [(day, tokens, usd)]; per-call deltas of the session's running total
    out, prev, model = [], (0, 0, 0), None
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if '"token_count"' not in line and '"turn_context"' not in line:
                continue
            try:
                d = json.loads(line)
                pl = d.get("payload") or {}
                if d.get("type") == "turn_context":
                    model = pl.get("model") or model
                elif pl.get("type") == "token_count" and pl.get("info"):
                    u = pl["info"]["total_token_usage"]
                    cur = (u.get("input_tokens") or 0, u.get("cached_input_tokens") or 0, u.get("output_tokens") or 0)
                    dl = [max(0, c - p) for c, p in zip(cur, prev)]
                    prev = cur
                    if dl[0] + dl[2]:
                        day = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).astimezone().date()
                        i, c, o = CODEX_PRICE.get(model, (0, 0, 0))  # input_tokens already includes the cached part
                        out.append((day, dl[0] + dl[2], ((dl[0] - dl[1]) * i + dl[1] * c + dl[2] * o) / 1e6))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def daily():  # -> ({day: tokens}, {day: usd}) for the selected source
    seen, tot, usd = {}, {}, {}
    if source in ("claude", "both"):
        for p in glob.glob(FILES, recursive=True):
            st = os.stat(p)
            key = (st.st_mtime, st.st_size)
            if _cache.get(p, (None,))[0] != key:
                _cache[p] = (key, scan(p))
            seen.update(_cache[p][1])
        rows = list(seen.values())
    else:
        rows = []
    if source in ("codex", "both"):
        for pat in CODEX_FILES:
            for p in glob.glob(pat, recursive=True):
                st = os.stat(p)
                key = (st.st_mtime, st.st_size)
                if _cache.get(p, (None,))[0] != key:
                    _cache[p] = (key, scan_codex(p))
                rows += _cache[p][1]
    for day, n, c in rows:
        tot[day] = tot.get(day, 0) + n
        usd[day] = usd.get(day, 0) + c
    return tot, usd


def money(c):
    return f"${c:,.0f}" if c >= 100 else f"${c:.2f}"


def fmt(n):
    for s, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= d:
            return f"{n/d:.1f}{s}"
    return str(int(n))


def shade(n, ranked):
    # quantile buckets over active days, so colors spread evenly regardless of outliers
    if n <= 0:
        return LEVELS[0]
    pct = sum(v <= n for v in ranked) / len(ranked)
    return LEVELS[1 + min(4, int(pct * 5))]


def rrect(cv, x, y, w, h, r, **kw):
    pts = [x+r, y, x+w-r, y, x+w, y, x+w, y+r, x+w, y+h-r, x+w, y+h, x+w-r, y+h,
           x+r, y+h, x, y+h, x, y+h-r, x, y+r, x, y]
    return cv.create_polygon(pts, smooth=True, **kw)


def tray_image():  # 3x3 mini heatmap in the theme colors
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for r in range(3):
        for c in range(3):
            lv = LEVELS[1 + (r * 3 + c) * 5 // 9]
            d.rounded_rectangle([4 + c * 20, 4 + r * 20, 20 + c * 20, 20 + r * 20], 4, fill=lv)
    return im


_lock = socket.socket()  # single instance: the port stays bound while the app lives
try:
    _lock.bind(("127.0.0.1", 47653))
except OSError:
    sys.exit()

try:  # DPI-aware so geometry and the work area share physical pixels (also crisper text)
    import ctypes
    from ctypes import wintypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    ctypes = None

PAD, LEFT, HEAD, CARD_H = 20, 36, 54, 78
F_TITLE, F_LAB, F_NUM, F_UNIT = (FONT, 13, "bold"), (FONT, 10), (FONT, 17, "bold"), (FONT, 11)
W = PAD * 2 + LEFT + WEEKS * (CELL + GAP) - GAP
CARD_W = (W - PAD * 2 - 10 * 2) / 3
GRID_Y = HEAD + CARD_H + 40  # header + cards + room for month labels
H = GRID_Y + 7 * (CELL + GAP) - GAP + 44

root = tk.Tk()
root.overrideredirect(True)  # borderless popup; hides when it loses focus
root.attributes("-topmost", True)
root.attributes("-alpha", 0.96)
root.configure(bg=BG)
cv = tk.Canvas(root, width=W, height=H, bg=BG, highlightthickness=0)
cv.pack()
fnum = tkfont.Font(font=F_NUM)
cells = {}  # canvas item id -> (day, tokens)
icon = None
events = queue.Queue()  # tray thread -> tk thread
hidden_at = shown_at = 0.0


def place():  # bottom-right of the work area, just above the taskbar
    try:
        wa = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
        root.geometry(f"{W}x{H}+{wa.right - W - 12}+{wa.bottom - H - 12}")
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.windll.user32.GetParent(root.winfo_id()), 33,
                                                   ctypes.byref(ctypes.c_int(2)), 4)  # rounded corners
    except Exception:
        root.geometry(f"{W}x{H}")


def hide(_=None):
    global hidden_at
    root.withdraw()
    hidden_at = time.time()


def show():
    global shown_at
    shown_at = time.time()
    draw()
    place()
    root.deiconify()
    root.lift()
    root.focus_force()


def toggle():
    if root.state() == "normal":
        hide()
    elif time.time() - hidden_at > 0.4:  # the tray click itself just stole focus and hid us
        show()


def quit_app():
    if icon:
        icon.stop()
    root.destroy()


def poll():
    try:
        while True:
            {"toggle": toggle, "quit": quit_app, "colors": open_settings}[events.get_nowait()]()
    except queue.Empty:
        pass
    root.after(100, poll)


def relaunch():  # the socket is freed first so the new copy can bind
    _lock.close()
    if icon:
        icon.stop()
    subprocess.Popen([sys.executable, os.path.abspath(__file__)])
    root.destroy()


def next_theme(e):
    save_cfg(theme=(ti + 1) % len(THEMES))
    relaunch()


def open_settings():
    from tkinter import colorchooser
    w = {k: THEMES[ti][k] for k in ("bg", "card", "fg", "dim")}
    w["lv"] = list(THEMES[ti]["lv"])
    win = tk.Toplevel(root)
    win.title("Claude usage - colors")
    win.attributes("-topmost", True)
    win.resizable(False, False)
    frm = tk.Frame(win, padx=14, pady=12)
    frm.pack()
    prev = tk.Canvas(frm, width=330, height=70, highlightthickness=1, highlightbackground="#999")
    prev.grid(row=0, column=0, columnspan=3, pady=(0, 10))
    rows = [("Background", "bg"), ("Cards", "card"), ("Text", "fg"), ("Dim text", "dim"),
            ("No usage", 0), ("Low", 1), ("Level 3", 2), ("Level 4", 3), ("Level 5", 4), ("High", 5)]
    btns = {}

    def repaint():
        prev.delete("all")
        prev.config(bg=w["bg"])
        rrect(prev, 10, 10, 90, 50, 8, fill=w["card"], outline="")
        prev.create_text(20, 26, text="TODAY", anchor="w", fill=w["dim"], font=(FONT, 8))
        prev.create_text(20, 46, text="$9.27", anchor="w", fill=w["fg"], font=(FONT, 13, "bold"))
        for i, c in enumerate(w["lv"]):
            rrect(prev, 116 + i * 34, 20, 28, 28, 6, fill=c, outline="")
        for key, b in btns.items():
            c = w[key] if isinstance(key, str) else w["lv"][key]
            light = sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) > 380
            b.config(bg=c, text=c, fg="#000000" if light else "#ffffff", activebackground=c)

    def pick(key):
        cur = w[key] if isinstance(key, str) else w["lv"][key]
        rgb, hexv = colorchooser.askcolor(color=cur, parent=win)
        if hexv:
            if isinstance(key, str):
                w[key] = hexv
            else:
                w["lv"][key] = hexv
            repaint()

    def load_preset(name):
        src = PRESETS[PRESET_NAMES.index(name)] if name in PRESET_NAMES else CUSTOM
        for k in ("bg", "card", "fg", "dim"):
            w[k] = src[k]
        w["lv"] = list(src["lv"])
        repaint()

    tk.Label(frm, text="Start from").grid(row=1, column=0, sticky="w")
    var = tk.StringVar(value="Current")
    tk.OptionMenu(frm, var, "Custom", *PRESET_NAMES, command=load_preset).grid(row=1, column=1, columnspan=2, sticky="ew")
    for r, (lab, key) in enumerate(rows, start=2):
        tk.Label(frm, text=lab).grid(row=r, column=0, sticky="w", pady=1)
        btns[key] = tk.Button(frm, width=10, relief="flat", command=lambda k=key: pick(k))
        btns[key].grid(row=r, column=1, pady=1)
    repaint()

    def apply():
        save_cfg(custom=w, theme=len(PRESETS))
        win.destroy()
        relaunch()

    bar = tk.Frame(frm)
    bar.grid(row=len(rows) + 2, column=0, columnspan=3, pady=(10, 0), sticky="e")
    tk.Button(bar, text="Cancel", width=8, command=win.destroy).pack(side="left", padx=4)
    tk.Button(bar, text="Apply", width=8, command=apply).pack(side="left")


def cycle_source(e=None):
    global source
    source = SOURCES[(SOURCES.index(source) + 1) % len(SOURCES)]
    save_cfg(source=source)
    draw()


def toggle_mode(e=None):
    global usd_mode
    usd_mode = not usd_mode
    save_cfg(mode="usd" if usd_mode else "tokens")
    draw()


def hover(e):
    it = cv.find_withtag("current")
    d = cells.get(it[0]) if it else None
    cv.itemconfig("tip", text=f"{d[0]:%a, %b %d}   {money(d[1])}  ·  {fmt(d[2])} tokens" if d else "")


cv.bind("<Motion>", hover)
root.bind("<FocusOut>", lambda e: hide() if root.state() == "normal" and e.widget is root and time.time() - shown_at > 1 else None)


def stat(x, y, label, value):
    num, unit = (value[:-1], value[-1]) if value[-1] in "BMK" else (value, "")
    cv.create_text(x + 14, y + 17, text=label, anchor="w", fill=DIM, font=F_LAB)
    cv.create_text(x + 14, y + 48, text=num, anchor="w", fill=FG, font=F_NUM)
    if unit:
        cv.create_text(x + 14 + fnum.measure(num) + 3, y + 52, text=unit, anchor="w", fill=DIM, font=F_UNIT)


def draw():
    tok, usd = daily()
    data = usd if usd_mode else tok  # metric shown on cards and used for shading
    show = money if usd_mode else fmt
    today = date.today()
    start = today - timedelta(days=(today.weekday() + 1) % 7 + (WEEKS - 1) * 7)  # Sunday-start grid
    ranked = sorted(v for v in data.values() if v > 0) or [1]
    cv.delete("all")
    cells.clear()
    cv.create_text(PAD, 28, text="Claude usage", anchor="w", fill=FG, font=F_TITLE, tags="title")
    cv.create_text(W - PAD, 28, text=f"{datetime.now():%H:%M}", anchor="e", fill=DIM, font=F_LAB)
    cv.tag_bind("title", "<Double-Button-1>", next_theme)
    rrect(cv, W - PAD - 108, 16, 52, 24, 12, fill=CARD, outline="", tags="mode")
    cv.create_text(W - PAD - 82, 28, text="USD" if usd_mode else "tokens", fill=FG, font=F_LAB, tags="mode")
    cv.tag_bind("mode", "<Button-1>", toggle_mode)
    rrect(cv, W - PAD - 174, 16, 60, 24, 12, fill=CARD, outline="", tags="src")
    cv.create_text(W - PAD - 144, 28, text=source.capitalize(), fill=FG, font=F_LAB, tags="src")
    cv.tag_bind("src", "<Button-1>", cycle_source)
    wk = sum(v for d, v in data.items() if d > today - timedelta(days=7))
    for i, (lab, val) in enumerate((("TODAY", show(data.get(today, 0))), ("7 DAYS", show(wk)),
                                    ("ALL TIME", show(sum(data.values()))))):
        x = PAD + i * (CARD_W + 10)
        rrect(cv, x, HEAD, CARD_W, CARD_H, 12, fill=CARD, outline="")
        stat(x, HEAD, lab, val)
    for r, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        cv.create_text(PAD, GRID_Y + r * (CELL + GAP) + CELL / 2, text=lab, anchor="w", fill=DIM, font=F_LAB)
    last_month, last_col = None, -9
    for i in range(WEEKS * 7):
        d = start + timedelta(days=i)
        if d > today:
            break
        col, row = divmod(i, 7)
        x, y = PAD + LEFT + col * (CELL + GAP), GRID_Y + row * (CELL + GAP)
        if row == 0 and d.month != last_month:
            if col - last_col >= 3:  # skip a label that would overlap the previous one
                cv.create_text(x, GRID_Y - 14, text=f"{d:%b}", anchor="w", fill=DIM, font=F_LAB)
                last_col = col
            last_month = d.month
        n = data.get(d, 0)
        it = rrect(cv, x, y, CELL, CELL, 5, fill=shade(n, ranked), outline=FG if d == today else "", width=1.5)
        cells[it] = (d, usd.get(d, 0), tok.get(d, 0))
    fy = H - 24
    cv.create_text(PAD, fy, text="", anchor="w", fill=FG, font=F_LAB, tags="tip")
    x = W - PAD
    cv.create_text(x, fy, text="More", anchor="e", fill=DIM, font=F_LAB)
    x -= 40
    for lv in reversed(LEVELS):
        rrect(cv, x - 12, fy - 6, 12, 12, 3, fill=lv, outline="")
        x -= 17
    cv.create_text(x + 2, fy, text="Less", anchor="e", fill=DIM, font=F_LAB)
    if icon:
        icon.title = f"Claude usage - today {show(data.get(today, 0))}, 7d {show(wk)}"


def tick():
    draw()
    root.after(REFRESH_MS, tick)


root.withdraw()
icon = pystray.Icon("claude-usage", tray_image(), "Claude usage", pystray.Menu(
    pystray.MenuItem("Show / hide", lambda: events.put("toggle"), default=True),
    pystray.MenuItem("Colors...", lambda: events.put("colors")),
    pystray.MenuItem("Quit", lambda: events.put("quit"))))
icon.run_detached()
poll()
root.after(REFRESH_MS, tick)
draw()
if "--colors" in sys.argv:
    root.after(300, open_settings)
if "--show" in sys.argv:  # open the popup immediately (handy for debugging)
    root.after(300, show)
root.mainloop()
