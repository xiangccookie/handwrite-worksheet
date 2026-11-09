# -*- coding: utf-8 -*-
"""
手写生成器（图形界面）—— 把文字写成「手写字体 + 信纸」的图片与 PDF

界面用 tkinter（Python 自带，打包后用户不需要另装任何东西）。
每个按钮都是自绘的圆角按钮，**按下会下沉并变色，松手带一个轻微过冲的回弹动画**。

命令行参数：
    手写生成器.exe                正常打开界面
    手写生成器.exe --selftest 图.png   自检：自动填样例文字、生成、截图、退出（给开发者验证用）
"""
from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

# ---------------------------------------------------------------- 路径与常量
APP_TITLE = "手写生成器 · 手写稿"

# 纸型：(界面显示名, paper_write.py 里的键)
PAPER_OPTIONS = [
    ("横线信纸", "ruled"),
    ("英语四线三格", "english"),
    ("方格纸", "grid"),
    ("点阵纸", "dots"),
    ("实验记录纸", "lab"),
    ("素纸", "blank"),
    ("白纸", "white"),
]
APP_VERSION = "1.0"

BG      = "#F2EFE8"      # 窗口底色（接近米白纸）
PANEL   = "#FFFFFF"      # 面板底色
INK     = "#252A33"      # 主文字色
SUB     = "#6B7280"      # 次要文字色
LINE    = "#E2DED4"      # 分隔线
ACCENT  = "#2F6FEB"      # 主按钮
ACCENT2 = "#3F7CF5"      # 悬停
ACCENTD = "#2359C4"      # 按下
GREEN   = "#1F9D55"
GREEND  = "#177A42"
GRAY    = "#7A8290"
GRAYD   = "#5C6472"


def resource_path(rel: str) -> str:
    """打包成 exe 之后，附带文件被解到临时目录 _MEIPASS；源码运行时就在脚本旁边。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


# 字体目录按顺序找：仓库版**不附带**任何字体（第三方商业字体不能分发），
# 用户把自己的 .ttf 丢进「字体」文件夹，或点界面上的「＋ 添加字体」按钮即可。
FONT_DIR_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                                  else __file__)), "字体"),   # 便携：exe 旁边
    resource_path("字体"),
    resource_path("HandWrite字体"),     # 本机旧目录名，兼容
    os.path.join(os.getcwd(), "字体"),
]

# 系统自带中文字体兜底（Windows 上基本都有楷体/黑体/宋体）
SYSTEM_FONTS = [
    ("楷体（系统）", r"C:\Windows\Fonts\simkai.ttf"),
    ("仿宋（系统）", r"C:\Windows\Fonts\simfang.ttf"),
    ("黑体（系统）", r"C:\Windows\Fonts\simhei.ttf"),
    ("宋体（系统）", r"C:\Windows\Fonts\simsun.ttc"),
    ("微软雅黑（系统）", r"C:\Windows\Fonts\msyh.ttc"),
]


def user_font_dir() -> str:
    """用户自己添加的字体存在这里（exe 旁边的「字体」文件夹）。"""
    return FONT_DIR_CANDIDATES[0]


def pick_out_dir() -> str:
    """挑一个**真的能写**的输出目录。

    不能写死成桌面：实测在受限环境里程序对桌面目录没有写权限，
    写死会导致 PermissionError [Errno 13]，而且报错发生在后台线程里、界面看着像卡住。
    这里逐个试，第一个写得进去的就用。
    """
    exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                             else __file__))
    candidates = [
        os.path.join(exe_dir, "输出"),                                   # 便携模式：就在 exe 旁边
        os.path.join(os.path.expanduser("~"), "Documents", "手写稿输出"),  # 文档目录
        os.path.join(os.path.expanduser("~"), "手写稿输出"),               # 用户目录
        os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "手写稿输出"),  # 临时目录
        os.getcwd(),                                                     # 当前目录兜底
    ]
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return d
        except Exception:
            continue
    raise RuntimeError("找不到任何可写的输出目录")


OUT_DIR_DEFAULT = None      # 启动时由 pick_out_dir() 决定

# 让 paper_write.py / handwrite.py 能被 import（它们被一起打进 exe）
_BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)


def list_fonts():
    """返回 [(显示名, 字体文件路径), ...]。先扫用户字体目录，再把系统字体补在后面。"""
    out, seen = [], set()
    for d in FONT_DIR_CANDIDATES:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".ttf", ".otf")):
                path = os.path.join(d, f)
                key = os.path.basename(f).lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((os.path.splitext(f)[0], path))
    for name, path in SYSTEM_FONTS:
        if os.path.isfile(path) and os.path.basename(path).lower() not in seen:
            seen.add(os.path.basename(path).lower())
            out.append((name, path))
    return out


# ---------------------------------------------------------------- 圆角按钮
class BounceButton(tk.Canvas):
    """自绘圆角按钮：悬停变色、按下下沉 2px、松手带回弹过冲。"""

    def __init__(self, master, text, command=None, width=132, height=40,
                 fill=ACCENT, hover=ACCENT2, press=ACCENTD, fg="#FFFFFF",
                 radius=11, font=("Microsoft YaHei UI", 10, "bold")):
        bg = master["bg"] if "bg" in master.keys() else BG
        super().__init__(master, width=width, height=height, highlightthickness=0,
                         bd=0, bg=bg, cursor="hand2")
        # 注意：不能用 self._w / self._h —— 它们是 tkinter 内部保存控件名（Tcl 路径）的属性，
        # 一旦被覆盖，之后所有 Tk 调用都会报 invalid command name。
        self._bw, self._bh = width, height
        self._text = text
        self._cmd = command
        self._fills = (fill, hover, press)
        self._fg = fg
        self._r = radius
        self._font = font
        self._offset = 0          # 向下位移（按下 = 2）
        self._enabled = True
        self._hover = False
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -- 绘制 ------------------------------------------------------------
    def _round_points(self, x1, y1, x2, y2, r, steps=8):
        """生成圆角矩形的顶点序列（用多边形近似圆角，点数够多就看不出折线）。"""
        import math
        pts = []
        for cx, cy, a0 in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
                           (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
            for i in range(steps + 1):
                a = math.radians(a0 + 90 * i / steps)
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
        return pts

    def _draw(self):
        self.delete("all")
        o = self._offset
        if not self._enabled:
            body = "#C8CBD2"
        elif self._offset > 0:
            body = self._fills[2]
        elif self._hover:
            body = self._fills[1]
        else:
            body = self._fills[0]
        pad = 3
        # 底部阴影（按钮下沉时阴影变薄，视觉上就是“按进去了”）
        if self._enabled:
            self.create_polygon(
                self._round_points(pad, pad + 3 - o, self._bw - pad, self._bh - pad + 1 - o, self._r),
                fill="#D6D2C8", outline="")
        self.create_polygon(
            self._round_points(pad, pad - o, self._bw - pad, self._bh - pad - o, self._r),
            fill=body, outline="")
        self.create_text(self._bw / 2, (self._bh - pad) / 2 - o + 0.5,
                         text=self._text, fill=self._fg, font=self._font)

    # -- 交互 ------------------------------------------------------------
    def _on_enter(self, _=None):
        if self._enabled:
            self._hover = True
            self._draw()

    def _on_leave(self, _=None):
        self._hover = False
        self._offset = 0
        self._draw()

    def _on_press(self, _=None):
        if not self._enabled:
            return
        self._offset = 2
        self._draw()

    def _on_release(self, event=None):
        if not self._enabled:
            return
        inside = (0 <= event.x <= self._bw and 0 <= event.y <= self._bh) if event else True
        self._spring()                      # 回弹动画
        if inside and self._cmd:
            self._cmd()

    def _spring(self):
        """松手回弹：从 2px 升到 -1px 再落回 0，像弹簧。"""
        for i, off in enumerate((1, 0, -1, 0)):
            self.after(18 * (i + 1), lambda o=off: self._set_offset(o))

    def _set_offset(self, v):
        self._offset = v
        self._draw()

    def set_enabled(self, flag: bool):
        self._enabled = bool(flag)
        self.configure(cursor="hand2" if flag else "arrow")
        self._draw()


# ---------------------------------------------------------------- 滑杆行
class SliderRow(tk.Frame):
    """一行「标题 + 滑杆 + 当前值」，值变化时回调。"""

    def __init__(self, master, label, lo, hi, init, fmt="{:.2f}",
                 resolution=None, command=None, **kw):
        super().__init__(master, bg=master["bg"], **kw)
        self._fmt = fmt
        self._cb = command
        tk.Label(self, text=label, bg=self["bg"], fg=INK,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        row = tk.Frame(self, bg=self["bg"]); row.pack(fill="x")
        self.var = tk.DoubleVar(value=init)
        # 滑块（thumb）必须给 sliderlength + raised 浮雕，否则它会和轨道同色、看不见
        sc = tk.Scale(row, from_=lo, to=hi, orient="horizontal", variable=self.var,
                      resolution=resolution or (hi - lo) / 100.0, showvalue=0,
                      bg=self["bg"], fg=INK, troughcolor="#DED9CE", highlightthickness=0,
                      bd=0, width=12, sliderlength=18, sliderrelief="raised",
                      activebackground=ACCENT, command=self._on_move)
        sc.pack(side="left", fill="x", expand=True)
        self._lbl = tk.Label(row, text=fmt.format(init), width=6, anchor="e",
                             bg=self["bg"], fg=SUB, font=("Consolas", 9))
        self._lbl.pack(side="left", padx=(6, 0))
        self._scale = sc

    def _on_move(self, _=None):
        self._lbl.configure(text=self._fmt.format(self.var.get()))
        if self._cb:
            self._cb()

    def get(self):
        return self.var.get()


# ---------------------------------------------------------------- 主窗口
class App(tk.Tk):
    def __init__(self, selftest_out: str | None = None, log_path: str | None = None):
        super().__init__()
        self.selftest_out = selftest_out
        self.log_path = log_path
        self.title(f"{APP_TITLE}  v{APP_VERSION}")
        self.configure(bg=BG)
        self.geometry("1180x840")
        self.minsize(1060, 760)

        self.out_dir = pick_out_dir()
        self.pages: list[str] = []
        self.page_idx = 0
        self._preview_img = None
        self._busy = False

        self._build_ui()
        if self.selftest_out:
            self._fill_sample()

    # -- 界面骨架 --------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=0, minsize=440)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = tk.Frame(self, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(18, 9), pady=14)
        right = tk.Frame(self, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(9, 18), pady=14)

        # 标题
        tk.Label(left, text="手写生成器", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(left, text="手写字体 + 六种纸型（信纸 / 英语四线三格 / 方格 / 点阵 / 实验记录 / 素纸）",
                 bg=BG, fg=SUB, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 12))

        # 文字输入
        tk.Label(left, text="要写的文字", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        box = tk.Frame(left, bg=LINE, padx=1, pady=1)
        box.pack(fill="both", expand=True, pady=(4, 10))
        self.text = tk.Text(box, height=12, wrap="word", bd=0, relief="flat",
                            font=("Microsoft YaHei UI", 10), bg=PANEL, fg=INK,
                            insertbackground=INK, padx=10, pady=8, undo=True)
        sb = tk.Scrollbar(box, command=self.text.yview, width=12)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 字体
        frow = tk.Frame(left, bg=BG); frow.pack(fill="x", pady=(0, 8))
        tk.Label(frow, text="字体", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.fonts = list_fonts()
        names = [n for n, _ in self.fonts]
        self.font_var = tk.StringVar(value=names[0] if names else "")
        # 用 ttk.Combobox 而不是 OptionMenu：OptionMenu 的箭头是字符画，中文字体下会显示成方框
        style = ttk.Style(self)
        try:
            style.theme_use("clam")          # clam 主题允许改配色，其余主题改不动
        except Exception:
            pass
        style.configure("Hand.TCombobox", fieldbackground=PANEL, background=PANEL,
                        foreground=INK, arrowcolor=INK, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=4)
        self.font_box = ttk.Combobox(frow, textvariable=self.font_var,
                                     values=names or ["（无字体）"],
                                     state="readonly", width=20,
                                     font=("Microsoft YaHei UI", 9),
                                     style="Hand.TCombobox")
        self.font_box.pack(side="left", padx=(8, 0))

        self.btn_font = BounceButton(frow, "＋ 添加字体", self.on_add_font,
                                     width=118, height=32,
                                     fill=GRAY, hover="#8A93A1", press=GRAYD,
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.btn_font.pack(side="left", padx=(8, 0))

        # 滑杆
        self.sl_size = SliderRow(left, "字号（磅，1 磅 ≈ 1/72 英寸）", 8, 20, 11.5, "{:.1f}")
        self.sl_size.pack(fill="x", pady=(2, 4))
        self.sl_ls = SliderRow(left, "行距（字号 × 倍数）", 1.4, 2.2, 1.75, "{:.2f}")
        self.sl_ls.pack(fill="x", pady=(2, 4))
        self.sl_wo = SliderRow(left, "笔画抖动（越大越潦草）", 0.0, 1.5, 0.45, "{:.2f}")
        self.sl_wo.pack(fill="x", pady=(2, 4))
        self.sl_mg = SliderRow(left, "左右留白（毫米）", 8, 30, 16, "{:.0f}")
        self.sl_mg.pack(fill="x", pady=(2, 8))

        # 选项
        opt = tk.Frame(left, bg=BG); opt.pack(fill="x", pady=(0, 10))
        tk.Label(opt, text="纸型", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.paper_var = tk.StringVar(value=PAPER_OPTIONS[0][0])
        self.paper_box = ttk.Combobox(opt, textvariable=self.paper_var,
                                      values=[n for n, _ in PAPER_OPTIONS],
                                      state="readonly", width=13,
                                      font=("Microsoft YaHei UI", 9),
                                      style="Hand.TCombobox")
        self.paper_box.pack(side="left", padx=(8, 16))
        self.var_pages = tk.BooleanVar(value=True)
        tk.Checkbutton(opt, text="长文分页（A5）", variable=self.var_pages, bg=BG, fg=INK,
                       activebackground=BG, selectcolor=PANEL, bd=0, highlightthickness=0,
                       font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(16, 0))

        # 按钮区（全部是回弹按钮）
        brow = tk.Frame(left, bg=BG); brow.pack(fill="x", pady=(2, 6))
        self.btn_gen = BounceButton(brow, "生成预览", self.on_generate, width=140)
        self.btn_gen.pack(side="left")
        self.btn_png = BounceButton(brow, "保存图片", self.on_save_png, width=132,
                                    fill=GREEN, hover="#25B463", press=GREEND)
        self.btn_png.pack(side="left", padx=8)
        self.btn_pdf = BounceButton(brow, "导出 PDF", self.on_save_pdf, width=132,
                                    fill=GREEN, hover="#25B463", press=GREEND)
        self.btn_pdf.pack(side="left")

        brow2 = tk.Frame(left, bg=BG); brow2.pack(fill="x")
        self.btn_open = BounceButton(brow2, "打开输出文件夹", self.on_open_dir, width=152,
                                     fill=GRAY, hover="#8A93A1", press=GRAYD)
        self.btn_open.pack(side="left")
        self.btn_clear = BounceButton(brow2, "清空文字", self.on_clear, width=112,
                                      fill=GRAY, hover="#8A93A1", press=GRAYD)
        self.btn_clear.pack(side="left", padx=8)
        self.btn_quit = BounceButton(brow2, "退出", self.destroy, width=90,
                                     fill="#C0453C", hover="#D25349", press="#9E362E")
        self.btn_quit.pack(side="left")

        # 状态栏
        self.status_var = tk.StringVar(value="填好文字后点「生成预览」。")
        tk.Label(left, textvariable=self.status_var, bg=BG, fg=SUB, anchor="w",
                 wraplength=400, justify="left",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x", pady=(10, 0))

        # ---- 右侧预览 ----
        head = tk.Frame(right, bg=BG); head.pack(fill="x")
        tk.Label(head, text="预览", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.page_lbl = tk.Label(head, text="", bg=BG, fg=SUB,
                                 font=("Microsoft YaHei UI", 9))
        self.page_lbl.pack(side="right")
        self.btn_prev = BounceButton(head, "上一页", self.on_prev, width=84, height=32,
                                     fill=GRAY, hover="#8A93A1", press=GRAYD,
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.btn_next = BounceButton(head, "下一页", self.on_next, width=84, height=32,
                                     fill=GRAY, hover="#8A93A1", press=GRAYD,
                                     font=("Microsoft YaHei UI", 9, "bold"))
        self.btn_next.pack(side="right", padx=(0, 0))
        self.btn_prev.pack(side="right", padx=(0, 8))

        self.canvas = tk.Canvas(right, bg="#DCD8CE", highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.canvas.bind("<Configure>", lambda e: self._render_preview())

    def on_add_font(self):
        """选一个 .ttf/.otf，复制到 exe 旁边的「字体」文件夹，然后刷新下拉列表。"""
        path = filedialog.askopenfilename(
            title="选择字体文件（.ttf / .otf）",
            filetypes=[("字体文件", "*.ttf *.otf"), ("所有文件", "*.*")])
        if not path:
            return
        d = user_font_dir()
        try:
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, os.path.basename(path))
            shutil.copy2(path, dst)
        except Exception as e:
            messagebox.showerror("添加字体失败", f"{e}\n\n可以手动把字体文件放进：{d}")
            return
        self.fonts = list_fonts()
        self.font_box.configure(values=[n for n, _ in self.fonts])
        self.font_var.set(os.path.splitext(os.path.basename(dst))[0])
        self.status_var.set(f"已添加字体 {os.path.basename(dst)}，存放在 {d}")

    def _wlog(self, msg: str):
        """写一行日志到 selftest 日志文件（打包成 exe 后没有控制台，只能靠文件看错误）。"""
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(msg.rstrip() + "\n")
        except Exception:
            pass

    # -- 参数收集 --------------------------------------------------------
    def _collect(self):
        name = self.font_var.get()
        path = dict(self.fonts).get(name)
        if not path:
            raise RuntimeError("没选字体（HandWrite字体 目录为空？）")
        return dict(font_path=path,
                    size_pt=round(self.sl_size.get(), 2),
                    line_spacing=round(self.sl_ls.get(), 2),
                    wobble=round(self.sl_wo.get(), 2),
                    margin_mm=int(round(self.sl_mg.get())),
                    paper=dict(PAPER_OPTIONS)[self.paper_var.get()],
                    pages=bool(self.var_pages.get()))

    # -- 生成（后台线程，避免界面卡死）----------------------------------
    def on_generate(self):
        if self._busy:
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("还没写文字", "先在左边输入要写的文字。")
            return
        try:
            p = self._collect()
        except Exception as e:
            messagebox.showerror("参数有问题", str(e)); return

        out_dir = self.out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._busy = True
        self.btn_gen.set_enabled(False)
        self.status_var.set("正在生成…（文字越长越慢，请稍等）")
        self.update_idletasks()

        q = queue.Queue()
        threading.Thread(target=self._worker, args=(text, p, out_dir, q), daemon=True).start()
        self.after(80, lambda: self._poll(q))

    def _worker(self, text, p, out_dir, q):
        try:
            import importlib
            pw = importlib.import_module("paper_write")
            importlib.reload(pw)
            if p["pages"]:
                info = pw.write_pages(text, os.path.join(out_dir, "手写稿"), p["font_path"],
                                      size_pt=p["size_pt"], line_spacing=p["line_spacing"],
                                      wobble=p["wobble"], margin_mm=p["margin_mm"],
                                      paper=p["paper"])
                files = [x["file"] for x in info["pages"]]
                q.put(("ok", files, info))
            else:
                fn = os.path.join(out_dir, "手写稿.png")
                info = pw.write_on_paper(text, fn, p["font_path"],
                                         size_pt=p["size_pt"], line_spacing=p["line_spacing"],
                                         wobble=p["wobble"], margin_mm=p["margin_mm"],
                                         paper=p["paper"])
                q.put(("ok", [info["file"]], info))
        except Exception:
            q.put(("err", traceback.format_exc(), None))

    def _poll(self, q):
        try:
            kind, payload, info = q.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._poll(q))
            return
        self._busy = False
        self.btn_gen.set_enabled(True)
        if kind == "err":
            self._wlog("ERR\n" + str(payload))
            self.status_var.set("生成失败，原因见弹窗。")
            if not self.log_path:                 # 自检模式下只写日志，不弹模态框
                messagebox.showerror("生成失败", payload[-1200:])
            return
        self.pages = payload
        self.page_idx = 0
        self._wlog(f"OK pages={len(self.pages)}")
        self._render_preview()
        self.status_var.set(f"完成：{len(self.pages)} 页，已存到 {self.out_dir}")

    # -- 预览 ------------------------------------------------------------
    def _render_preview(self):
        self.canvas.delete("all")
        cw = max(self.canvas.winfo_width(), 50)
        ch = max(self.canvas.winfo_height(), 50)
        if not self.pages:
            self.page_lbl.configure(text="")
            self.canvas.create_text(cw / 2, ch / 2, text="生成后这里显示预览",
                                    fill="#8A8378", font=("Microsoft YaHei UI", 11))
            return
        self.page_idx = max(0, min(self.page_idx, len(self.pages) - 1))
        self.page_lbl.configure(text=f"第 {self.page_idx + 1} / {len(self.pages)} 页")
        try:
            im = Image.open(self.pages[self.page_idx]).convert("RGB")
        except Exception:
            self.canvas.create_text(cw / 2, ch / 2, text="预览读取失败", fill="#8A8378")
            return
        ratio = min((cw - 24) / im.width, (ch - 24) / im.height)
        w, h = max(1, int(im.width * ratio)), max(1, int(im.height * ratio))
        thumb = im.resize((w, h), Image.LANCZOS)
        # 画一圈细边，像纸放在桌面上
        self.canvas.create_rectangle((cw - w) / 2 - 1, (ch - h) / 2 - 1,
                                     (cw + w) / 2 + 1, (ch + h) / 2 + 1,
                                     outline="#B9B4A8")
        self._preview_img = ImageTk.PhotoImage(thumb)
        self.canvas.create_image(cw / 2, ch / 2, image=self._preview_img)

    def on_prev(self):
        if self.pages:
            self.page_idx = (self.page_idx - 1) % len(self.pages)
            self._render_preview()

    def on_next(self):
        if self.pages:
            self.page_idx = (self.page_idx + 1) % len(self.pages)
            self._render_preview()

    # -- 保存 / 打开 / 清空 ----------------------------------------------
    def on_save_png(self):
        if not self.pages:
            messagebox.showinfo("还没有生成", "先点「生成预览」。"); return
        if len(self.pages) == 1:
            dst = filedialog.asksaveasfilename(
                title="保存图片", defaultextension=".png",
                initialfile="手写稿.png", filetypes=[("PNG 图片", "*.png")])
            if dst:
                Image.open(self.pages[0]).save(dst)
                self.status_var.set(f"已保存：{dst}")
        else:
            d = filedialog.askdirectory(title="选择保存目录（会写入每一页 PNG）")
            if d:
                for i, src in enumerate(self.pages, 1):
                    Image.open(src).save(os.path.join(d, f"手写稿_{i}.png"))
                self.status_var.set(f"已保存 {len(self.pages)} 张到：{d}")

    def on_save_pdf(self):
        if not self.pages:
            messagebox.showinfo("还没有生成", "先点「生成预览」。"); return
        dst = filedialog.asksaveasfilename(
            title="导出 PDF", defaultextension=".pdf",
            initialfile="手写稿.pdf", filetypes=[("PDF 文件", "*.pdf")])
        if not dst:
            return
        imgs = [Image.open(p).convert("RGB") for p in self.pages]
        imgs[0].save(dst, save_all=True, append_images=imgs[1:],
                     resolution=300.0, quality=95)
        self.status_var.set(f"已导出 PDF（{len(imgs)} 页）：{dst}")

    def on_open_dir(self):
        os.makedirs(self.out_dir, exist_ok=True)
        try:
            os.startfile(self.out_dir)
        except Exception as e:
            messagebox.showerror("打不开", str(e))

    def on_clear(self):
        self.text.delete("1.0", "end")

    # -- 自检用 ----------------------------------------------------------
    def _fill_sample(self):
        self.text.insert("1.0",
                         "见字如面。\n\n"
                         "这些年我一直在想，写下来的字比说出去的话更耐得住时间。"
                         "纸会发黄，字迹会变淡，可你翻开它的那一刻，写信的人就又活过来了。\n\n"
                         "—— 二〇二六年秋，窗前")
        self.after(300, self.on_generate)


def main():
    selftest = None
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        if i + 1 < len(sys.argv):
            selftest = sys.argv[i + 1]
    log_path = None
    if selftest:
        log_path = os.path.splitext(selftest)[0] + "_log.txt"
        if os.path.exists(log_path):
            os.remove(log_path)
    app = App(selftest_out=selftest, log_path=log_path)

    if selftest:
        import time as _time
        deadline = _time.time() + 60

        def check():
            # 等到“生成完成”或超时；不要用固定等待，打包后启动更慢会误判失败
            if app.pages or _time.time() > deadline:
                try:
                    app.attributes("-topmost", True)
                    app.lift()
                    app.update()
                    x, y = app.winfo_rootx(), app.winfo_rooty()
                    w, h = app.winfo_width(), app.winfo_height()
                    from PIL import ImageGrab
                    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(selftest)
                    app._wlog(f"SHOT pages={len(app.pages)} "
                              f"status={app.status_var.get()}")
                except Exception:
                    app._wlog("SHOT_FAIL\n" + traceback.format_exc())
                finally:
                    app.destroy()
            else:
                app.after(400, check)

        app.after(600, check)

    app.mainloop()


if __name__ == "__main__":
    main()
