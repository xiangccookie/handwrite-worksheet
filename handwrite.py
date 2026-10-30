# -*- coding: utf-8 -*-
"""
handwrite.py —— 把一段文字渲染成「真实纸张纹理 + 手写笔迹感」的 PNG 图片

思路（两句说完）：
  1) 纸：用多层程序化噪声叠出纤维、斑驳、边缘泛黄、暗角，不依赖任何外部图片素材；
  2) 字：用楷体逐字绘制，每个字单独随机「偏移 / 旋转 / 缩放 / 墨色深浅 / 行内起伏」，
     再叠一点墨迹晕染，看起来就不像打印机打出来的。

用法见文件末尾的 __main__，或看同目录的 手写字_说明.md
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# ----------------------------------------------------------------------------
# 字体：用别名映射到 Windows 自带字体文件，找不到就自动降级
# ----------------------------------------------------------------------------
FONT_ALIASES = {
    "kai":      r"C:\Windows\Fonts\simkai.ttf",      # 楷体：带笔锋，最像手写
    "fangsong": r"C:\Windows\Fonts\simfang.ttf",     # 仿宋：清瘦，像钢笔字
    "song":     r"C:\Windows\Fonts\simsun.ttc",      # 宋体：印刷感，最不像手写
    "hei":      r"C:\Windows\Fonts\simhei.ttf",      # 黑体：无笔锋
    "deng":     r"C:\Windows\Fonts\Deng.ttf",        # 等线
    "yahei":    r"C:\Windows\Fonts\msyh.ttc",        # 微软雅黑
}


def resolve_font(name: str) -> str:
    """name 可以是字体别名、也可以是绝对路径。返回一个真实存在的字体文件路径。"""
    if os.path.isfile(name):
        return name
    key = name.lower().replace(".ttf", "").replace(".ttc", "")
    if key in FONT_ALIASES and os.path.isfile(FONT_ALIASES[key]):
        return FONT_ALIASES[key]
    for alias, path in FONT_ALIASES.items():
        if os.path.isfile(path):
            print(f"[字体] 未找到 {name!r}，自动降级为 {alias} ({path})", file=sys.stderr)
            return path
    raise FileNotFoundError("系统里找不到任何可用的中文字体")


# ----------------------------------------------------------------------------
# 程序化噪声（纸张质感的地基）
# ----------------------------------------------------------------------------
def _upnoise(h: int, w: int, gh: int, gw: int, rng: np.random.Generator) -> np.ndarray:
    """生成 h×w 的平滑噪声：先在 gh×gw 的小网格上取随机数，再双三次插值放大。"""
    gh, gw = max(2, gh), max(2, gw)
    grid = rng.random((gh, gw)).astype(np.float32)
    im = Image.fromarray((grid * 255).astype(np.uint8), mode="L").resize((w, h), Image.BICUBIC)
    return np.asarray(im, dtype=np.float32) / 255.0


def _norm(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-8:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)


def fractal_noise(h: int, w: int, rng, octaves: int = 6, base: int = 8, aniso: float = 1.0) -> np.ndarray:
    """多倍频噪声：低频给大块斑驳，高频给细微颗粒。aniso>1 会让纹理在横向上被拉长。"""
    total = np.zeros((h, w), np.float32)
    amp, norm = 1.0, 0.0
    for i in range(octaves):
        gh = min(h, max(2, base * (2 ** i)))
        gw = min(w, max(2, int(base * (2 ** i) * aniso)))
        total += amp * _upnoise(h, w, gh, gw, rng)
        norm += amp
        amp *= 0.5
    return _norm(total / norm)


# ----------------------------------------------------------------------------
# 纸张
# ----------------------------------------------------------------------------
def make_paper(w: int, h: int, rng, paper_rgb=(249, 246, 237), aged: float = 1.0):
    """返回 (paper_float[h,w,3] 0~1, fiber[h,w] 0~1)。fiber 后面用来模拟纤维吸墨不均。"""
    base = np.array(paper_rgb, dtype=np.float32) / 255.0

    # 1) 大块斑驳（纸浆分布不匀）
    mottle = fractal_noise(h, w, rng, octaves=5, base=6, aniso=1.0) - 0.5
    # 2) 横向纤维（行方向高频、列方向低频 → 拉成横丝）。两档尺度混着来，避免出现规律的“布纹”
    fiber_a = _norm(_upnoise(h, w, max(2, h // 2), 8, rng))
    fiber_b = _norm(_upnoise(h, w, max(2, h // 6), 3, rng))
    fiber = _norm(0.6 * fiber_a + 0.4 * fiber_b)
    # 3) 细颗粒（纸面微小起伏）
    grain = rng.normal(0.0, 1.0, (h, w)).astype(np.float32)
    grain = np.asarray(
        Image.fromarray(((grain * 0.5 + 0.5) * 255).clip(0, 255).astype(np.uint8), mode="L")
        .filter(ImageFilter.GaussianBlur(0.5)),
        dtype=np.float32,
    ) / 255.0 - 0.5

    # 振幅刻意调小：纸纹要“看得见但抢不了字的戏”，过大就变成粗布了
    relief = 0.030 * mottle + 0.009 * (fiber - 0.5) + 0.016 * grain

    paper = base[None, None, :] * (1.0 + relief[..., None])

    # 4) 暗角（像贴近平板扫描/拍照时的中心亮、四角暗）
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    vign = 1.0 - 0.075 * np.clip(r, 0, 1) ** 2.2
    paper *= vign[..., None]

    # 5) 边缘泛黄 / 氧化（aged 控制强度，0 = 全新白纸）
    edge = np.clip((r - 0.48) / 0.52, 0, 1) ** 1.4
    warm = np.array([0.042, 0.020, -0.030], dtype=np.float32)  # 偏黄、略降蓝
    paper += (edge * aged)[..., None] * warm[None, None, :]

    return np.clip(paper, 0, 1), fiber


def draw_ruled(page: np.ndarray, line_ys, rng, margin_x: int,
               rule_rgb=(178, 196, 216), rule_alpha=0.42,
               margin_rgb=(188, 84, 72), margin_alpha=0.30):
    """在纸面叠横线（信纸那种）。

    横线与左侧红边线必须分两层、分两种颜色上色 —— 否则红线会被横线的蓝灰染掉（我第一版就踩了这个坑）。
    线不是绝对直的：切成小段，每段给一点随机起伏。
    """
    h, w = page.shape[:2]

    # 第一层：横线（蓝灰）
    lay = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(lay)
    a0 = int(255 * rule_alpha)
    for y in line_ys:
        pts = []
        phase = rng.uniform(0, 2 * math.pi)
        for x in range(margin_x // 2, w - margin_x // 2, 6):
            dy = 0.9 * math.sin(x / 210.0 + phase) + rng.gauss(0, 0.35)
            pts.append((x, y + dy))
        d.line(pts, fill=int(a0 * rng.uniform(0.82, 1.0)), width=2, joint="curve")
    a = (np.asarray(lay, dtype=np.float32) / 255.0)[..., None]
    col = np.array(rule_rgb, dtype=np.float32) / 255.0
    page = page * (1 - a) + col[None, None, :] * a

    # 第二层：左侧红边线（单独一层，放在正文左边留白里）
    lay2 = Image.new("L", (w, h), 0)
    d2 = ImageDraw.Draw(lay2)
    xm = int(margin_x * 0.72)
    d2.line([(xm, int(h * 0.05)), (xm, int(h * 0.95))],
            fill=int(255 * margin_alpha), width=2)
    a2 = (np.asarray(lay2, dtype=np.float32) / 255.0)[..., None]
    col2 = np.array(margin_rgb, dtype=np.float32) / 255.0
    return page * (1 - a2) + col2[None, None, :] * a2


# ----------------------------------------------------------------------------
# 手写字
# ----------------------------------------------------------------------------
# 中文「避头尾」禁则：这些符号不许出现在行首（否则就会出现「。」单独占一行的丑相）
NO_LINE_START = set("，。、；：？！）〕］｝〉》」』”’·…‰%℃,.;:?!)]}>》")
# 这些不许吊在行尾
NO_LINE_END = set("（〔［｛〈《「『“‘([{<")


def _fix_kinsoku(lines):
    """把被挤到行首的标点拉回上一行末尾（必要时连上一个字一起挪下去）。"""
    lines = list(lines)
    for i in range(1, len(lines)):
        guard = 0
        while (guard < 8 and lines[i] and lines[i][0] in NO_LINE_START
               and len(lines[i - 1]) > 1):
            lines[i] = lines[i - 1][-1] + lines[i]
            lines[i - 1] = lines[i - 1][:-1]
            guard += 1
    for i in range(len(lines) - 1):
        guard = 0
        while guard < 8 and lines[i] and lines[i][-1] in NO_LINE_END:
            lines[i + 1] = lines[i][-1] + lines[i + 1]
            lines[i] = lines[i][:-1]
            guard += 1
    return lines


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float):
    """按像素宽度折行。中文逐字断，英文单词也会被断（已知简化，见说明文档）。

    返回 (lines, flags)：flags[i] 为 True 表示第 i 行是某一自然段的首行（用于首行缩进）。
    """
    lines, flags = [], []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            flags.append(False)
            continue
        cur = ""
        first = True
        for ch in para:
            if cur and font.getlength(cur + ch) > max_w:
                lines.append(cur)
                flags.append(first)
                first = False
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        flags.append(first)
    return _fix_kinsoku(lines), flags


def render_ink(w, h, lines, font_path, size_px, top_y, line_h, left_x, rng,
               indent_chars=2, ss=2, wobble=1.0, indent_flags=None):
    """把所有字画到一张超采样(ss倍)的灰度图上，再降采样 → 边缘顺滑。返回 L 模式 Image。"""
    big = Image.new("L", (w * ss, h * ss), 0)
    # 预建几档字号，模拟每个字大小不完全一致（反复 truetype 会慢，所以先缓存）
    variants = {}
    for d in (-0.030, -0.018, -0.008, 0.0, 0.008, 0.018, 0.030):
        px = max(6, int(round(size_px * ss * (1.0 + d))))
        variants[d] = ImageFont.truetype(font_path, px, index=0)
    base_font = variants[0.0]
    ascent0 = base_font.getmetrics()[0]

    for li, line in enumerate(lines):
        y_line = top_y * ss + li * line_h * ss
        x = left_x * ss
        is_para_head = (indent_flags is None) or (li < len(indent_flags) and indent_flags[li])
        if indent_chars and is_para_head and line:
            x += size_px * ss * indent_chars

        # 整行缓慢起伏 + 轻微倾斜：真实手写一行不会像尺子画的
        phase = rng.uniform(0, 2 * math.pi)
        period = rng.uniform(380, 820) * ss
        amp = rng.uniform(0.5, 1.8) * ss * wobble
        slope = rng.gauss(0, 0.0016) * ss

        for ch in line:
            if ch in (" ", "\u3000"):
                x += base_font.getlength(ch) * rng.uniform(0.90, 1.15)
                continue

            # 拉丁字母/数字：手抖幅度压到三成，否则 "Hello" 会抖成密码
            w_eff = wobble * (0.30 if ch.isascii() else 1.0)

            key = rng.choice(list(variants.keys()))
            fnt = variants[key]
            asc, desc = fnt.getmetrics()
            adv = fnt.getlength(ch)
            if adv <= 0:
                continue

            pad = max(6, int(size_px * ss * 0.30))
            gw = int(math.ceil(adv)) + 1
            gh = asc + desc + 1
            tmp = Image.new("L", (gw + 2 * pad, gh + 2 * pad), 0)
            d = ImageDraw.Draw(tmp)
            d.text((pad, pad + asc), ch, font=fnt, fill=255, anchor="ls")

            ang = rng.gauss(0, 2.0) * w_eff
            if abs(ang) > 0.35:
                tmp = tmp.rotate(ang, resample=Image.BICUBIC, expand=False,
                                 center=(pad + adv / 2.0, pad + asc))

            # 墨色浓淡（模拟落笔轻重 / 出墨量变化）
            dens = rng.uniform(0.84, 1.0)
            if dens < 0.995:
                tmp = tmp.point(lambda v, k=dens: int(v * k))

            px = x - pad + rng.gauss(0, 0.55 * w_eff) * ss
            py = (y_line - asc - pad
                  + rng.gauss(0, 0.85 * w_eff) * ss
                  + amp * math.sin(x / period + phase)
                  + slope * (x - left_x * ss))
            big.paste(tmp, (int(round(px)), int(round(py))), tmp)

            x += adv + rng.gauss(0, 0.010 * w_eff) * size_px * ss

    ink = big.resize((w, h), Image.LANCZOS)
    # 墨迹晕染：把字外扩一层很淡的边，像墨水渗进纸纤维
    soft = ink.filter(ImageFilter.GaussianBlur(0.75))
    ink = ImageChops.lighter(ink, soft.point(lambda v: int(v * 0.50)))
    return ink


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def build(text: str, out: str, *, font="kai", size_pt=11.0, dpi=300,
          width_mm=148.0, margin_mm=14.0, line_spacing=1.75,
          paper_rgb=(249, 246, 237), ink_rgb=(26, 28, 34),
          ruled=False, aged=1.0, wobble=1.0, indent_chars=2, seed=20261030):
    rng_np = np.random.default_rng(seed)
    rng_py = random.Random(seed)
    font_path = resolve_font(font)

    # 尺寸换算：mm → px
    px_per_mm = dpi / 25.4
    W = int(round(width_mm * px_per_mm))
    margin_x = int(round(margin_mm * px_per_mm))
    size_px = size_pt * dpi / 72.0
    line_h = size_px * line_spacing

    probe = ImageFont.truetype(font_path, int(round(size_px)), index=0)
    max_w = (W - 2 * margin_x) * 0.97
    lines, indent_flags = wrap_text(text, probe, max_w)

    top_y = margin_x + size_px * 0.6
    bottom = top_y + line_h * max(1, len(lines)) + margin_x * 0.6
    H = int(round(max(bottom, 100 * px_per_mm)))

    paper, fiber = make_paper(W, H, rng_np, paper_rgb=paper_rgb, aged=aged)

    if ruled:
        ys = [int(top_y + i * line_h + size_px * 0.78) for i in range(len(lines) + 1)]
        paper = draw_ruled(paper, ys, rng_py, margin_x)

    ink = render_ink(W, H, lines, font_path, size_px, top_y, line_h, margin_x,
                     rng_py, indent_chars=indent_chars, wobble=wobble,
                     indent_flags=indent_flags)

    # 墨迹合成：纸纤维让它吸收不均 → 墨色深浅有细碎变化
    a = np.asarray(ink, dtype=np.float32) / 255.0
    a = np.clip(a * (0.86 + 0.14 * fiber), 0, 1)[..., None]
    ink_col = np.array(ink_rgb, dtype=np.float32) / 255.0
    page = paper * (1 - a) + ink_col[None, None, :] * a

    # 收尾：极轻的颗粒 + 极轻的整体柔化，让字和纸在同一个光学平面上
    page += rng_np.normal(0, 0.004, (H, W, 1)).astype(np.float32)
    img = Image.fromarray((np.clip(page, 0, 1) * 255).astype(np.uint8), mode="RGB")
    img = img.filter(ImageFilter.GaussianBlur(0.3))

    img.save(out, dpi=(dpi, dpi))
    return out, W, H, len(lines), int(round(size_px))


def main(argv=None):
    p = argparse.ArgumentParser(description="文字 → 真实纸张 + 手写笔迹感 PNG")
    p.add_argument("--text", default=None, help="要写的文字，可用 \\n 分段")
    p.add_argument("--file", default=None, help="从 UTF-8 文本文件读文字（优先级低于 --text）")
    p.add_argument("--out", default="手写字.png")
    p.add_argument("--font", default="kai", help="kai/fangsong/song/hei/deng/yahei 或字体绝对路径")
    p.add_argument("--size", type=float, default=11.0, help="字号(磅, pt)")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--width-mm", type=float, default=148.0, help="纸宽(mm)，A5=148")
    p.add_argument("--margin-mm", type=float, default=14.0)
    p.add_argument("--line-spacing", type=float, default=1.75, help="行距=字号×该倍数")
    p.add_argument("--paper", default="249,246,237", help="纸色 R,G,B")
    p.add_argument("--ink", default="26,28,34", help="墨色 R,G,B")
    p.add_argument("--ruled", action="store_true", help="加信纸横线与红色边线")
    p.add_argument("--aged", type=float, default=1.0, help="边缘泛黄强度，0=新纸")
    p.add_argument("--wobble", type=float, default=1.0, help="手抖强度，0=像打印，1=正常手写")
    p.add_argument("--no-indent", action="store_true", help="首行不缩进")
    p.add_argument("--seed", type=int, default=20261030, help="随机种子，同种子同结果")
    a = p.parse_args(argv)

    if a.text is not None:
        text = a.text.replace("\\n", "\n")
    elif a.file:
        text = open(a.file, encoding="utf-8").read()
    else:
        raise SystemExit("需要 --text 或 --file")

    paper = tuple(int(v) for v in a.paper.split(","))
    ink = tuple(int(v) for v in a.ink.split(","))
    out, W, H, n, spx = build(
        text, a.out, font=a.font, size_pt=a.size, dpi=a.dpi,
        width_mm=a.width_mm, margin_mm=a.margin_mm, line_spacing=a.line_spacing,
        paper_rgb=paper, ink_rgb=ink, ruled=a.ruled, aged=a.aged,
        wobble=a.wobble, indent_chars=0 if a.no_indent else 2, seed=a.seed,
    )
    print(f"已生成: {out}  尺寸 {W}×{H}px  @{a.dpi}dpi  {n} 行  字号≈{spx}px")


if __name__ == "__main__":
    main()
