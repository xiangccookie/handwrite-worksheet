# -*- coding: utf-8 -*-
"""
paper_write.py —— 文字 → 「手写字体 + 程序化信纸」的成品图（300 dpi PNG）

和 photo_handwrite.py 的区别：
  · photo_handwrite.py 是拿**真实纸张照片**当底，要做透视校正；
  · 本脚本是**自己生成纸**（纸纹、横线、红边线全用算法画），不需要任何照片。

两个关键点：
  1. **手写字体**来自 HandWrite 安装包里的 `ttf_library/`（8 款中文手写体），
     比系统自带的楷体像手写得多；
  2. **行必须坐在横线上**：把 handright 的行距随机扰动（line_spacing_sigma）设为 0，
     让每行严格等距；再按**实际渲染出的第一行底边**为锚点、每隔一个行距画一条横线。
     如果保留行距扰动，每行会上下随机偏，就会出现你说的“字没在格子内”。

用法见 main()，或看同目录 `信纸成品_说明.md`
依赖：handright（BSD-3）、Pillow、numpy，以及同目录的 handwrite.py
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 已实测：HandWrite 包里 8 款字体**全部缺「〇」(U+3007)**，
# 张维镜手写楷书还缺「」和 —。这里做最小替换，避免出现空白字。
SUBST = {"〇": "零", "「": "“", "」": "”", "—": "－"}


def _load_handwrite():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "handwrite.py")
    if not os.path.isfile(path):
        raise SystemExit(f"缺少 handwrite.py（应与本脚本同目录）：{path}")
    spec = importlib.util.spec_from_file_location("handwrite", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ink_rows(alpha_np, thresh=0.02, merge_frac=0.50, pitch=None):
    """按垂直投影找出“文字行”。

    必须合并间距过小的相邻带：一行字里有空隙（比如「二」的上横和下横之间），
    不合并的话一行会被劈成好几段，横线就会画错位置。
    """
    H, W = alpha_np.shape
    rowsum = (alpha_np > 40).sum(axis=1)
    thr = max(6, int(thresh * W))
    raw, cur = [], None
    for y in range(H):
        if rowsum[y] > thr:
            if cur is None:
                cur = y
        else:
            if cur is not None:
                raw.append((cur, y - 1))
                cur = None
    if cur is not None:
        raw.append((cur, H - 1))
    if not raw:
        return []
    lim = (pitch or (raw[0][1] - raw[0][0] + 1)) * merge_frac
    out = [list(raw[0])]
    for t, b in raw[1:]:
        if t - out[-1][1] <= lim:
            out[-1][1] = b
        else:
            out.append([t, b])
    return [(t, b) for t, b in out]


def write_on_paper(text, out_png, font_path, *, dpi=300, width_mm=148.0, margin_mm=16.0,
                   size_pt=11.5, line_spacing=1.75, ruled=True, aged=1.0, seed=7,
                   ink_rgb=(24, 26, 36), wobble=0.45, ss=2):
    """渲染一张成品图。返回字典（含输出路径、像素尺寸、行数、横线条数）。"""
    from handright import Template, handwrite

    hw = _load_handwrite()
    for k, v in SUBST.items():
        text = text.replace(k, v)

    rng_np = np.random.default_rng(seed)
    rng_py = random.Random(seed)
    ppm = dpi / 25.4
    W = int(round(width_mm * ppm))
    mx = int(round(margin_mm * ppm))
    size_px = size_pt * dpi / 72.0
    line_h = size_px * line_spacing

    # 缩进只加在**段落开头**。之前给每一行都加（包括折行产生的续行），
    # 结果每行多两个字 → handright 二次折行 → 冒出孤立单字。
    paras = text.split("\n")
    body = "\n".join(("　　" + p if p.strip() else "") for p in paras)

    # 先给一块够高的画布，渲染完再按实际墨迹裁掉多余高度
    text_w = W - 2 * mx
    est = sum(1 for p in paras if p.strip())
    for p in paras:
        if p.strip():
            est += int(len(p) * size_px * 1.02 / text_w)
    H_tall = int(size_px * 1.2 + max(4, est) * line_h * 1.4 + mx * 2)

    s = max(0.15, size_px * ss / 60.0) * wobble
    tpl = Template(
        background=Image.new("RGBA", (W * ss, H_tall * ss), (0, 0, 0, 0)),
        font=ImageFont.truetype(font_path, int(size_px * ss), index=0),
        line_spacing=line_h * ss,
        fill=(0, 0, 0, 255),
        left_margin=mx * ss, right_margin=mx * ss,
        top_margin=size_px * 0.9 * ss, bottom_margin=0,
        word_spacing=0.0,
        line_spacing_sigma=0.0,          # ← 归零：行行等距，才能对上线
        font_size_sigma=2.0 * s,
        word_spacing_sigma=2.0 * s,
        perturb_x_sigma=2.6 * s,
        perturb_y_sigma=2.6 * s,
        perturb_theta_sigma=0.05 * wobble,
        start_chars="“（[<《「『",
        end_chars="，。、；：？！）]》」』”’",
    )
    pages = list(handwrite(body, tpl, seed=seed))
    if len(pages) > 1:
        print(f"[提示] 内容超出画布被分成 {len(pages)} 页，这里只取第 1 页。", file=sys.stderr)
    alpha_tall = pages[0].getchannel("A")

    rows = ink_rows(np.asarray(alpha_tall), pitch=line_h * ss)
    if not rows:
        raise RuntimeError("没渲染出任何文字，检查字体是否含这些字")
    Hh = int(rows[-1][1] / ss) + int(mx * 0.9)
    alpha_big = alpha_tall.crop((0, 0, W * ss, Hh * ss))
    rows = ink_rows(np.asarray(alpha_big), pitch=line_h * ss)

    paper, fiber = hw.make_paper(W, Hh, rng_np, paper_rgb=(250, 247, 238), aged=aged)
    alpha = alpha_big.resize((W, Hh), Image.LANCZOS)
    a = np.asarray(alpha, np.float32) / 255.0
    a = np.clip(a * (0.86 + 0.14 * fiber), 0, 1)[..., None]
    ink = np.array(ink_rgb, np.float32) / 255.0
    img = Image.fromarray((np.clip(paper * (1 - a) + ink[None, None, :] * a, 0, 1) * 255)
                          .astype(np.uint8), "RGB")

    n_rules = 0
    if ruled and rows:
        d = ImageDraw.Draw(img)
        y = rows[0][1] / ss                       # 第一行的底边 = 网格锚点
        while y < Hh - mx * 0.35:
            d.line([(mx - 34, int(y) + 3), (W - mx + 24, int(y) + 3)],
                   fill=(174, 192, 212), width=2)
            n_rules += 1
            y += line_h
        d.line([(int(mx * 0.72), int(Hh * 0.04)), (int(mx * 0.72), int(Hh * 0.96))],
               fill=(186, 84, 72), width=2)
    img.save(out_png, dpi=(dpi, dpi))
    return {"file": out_png, "px": f"{W}x{Hh}", "ink_rows": len(rows),
            "rules": n_rules, "size_px": int(size_px), "line_h": round(line_h, 1)}


FONT_DIR = "HandWrite字体"


def write_pages(text, out_prefix, font_path, *, dpi=300, width_mm=148.0, height_mm=210.0,
                margin_mm=16.0, size_pt=11.5, line_spacing=1.75, ruled=True, aged=1.0,
                seed=7, ink_rgb=(24, 26, 36), wobble=0.45, ss=2):
    """长文分页：按 A5（或指定尺寸）逐页排版，每页都让字坐在横线上。

    做法：先在一块超高画布上把**整篇**渲染出来（这样行距全局一致、不会在页边界重排），
    再按“整行”为单位切开——切口落在两行中间（半个行距处），绝不会切断一行字。
    """
    from handright import Template, handwrite

    hw = _load_handwrite()
    for k, v in SUBST.items():
        text = text.replace(k, v)

    rng_np = np.random.default_rng(seed)
    ppm = dpi / 25.4
    W = int(round(width_mm * ppm))
    Hp = int(round(height_mm * ppm))
    mx = int(round(margin_mm * ppm))
    size_px = size_pt * dpi / 72.0
    line_h = size_px * line_spacing

    paras = text.split("\n")
    body = "\n".join(("　　" + p if p.strip() else "") for p in paras)
    text_w = W - 2 * mx
    est = sum(1 for p in paras if p.strip())
    for p in paras:
        if p.strip():
            est += int(len(p) * size_px * 1.02 / text_w)
    H_tall = int(size_px * 1.2 + max(6, est) * line_h * 1.5 + mx * 2)

    s = max(0.15, size_px * ss / 60.0) * wobble
    tpl = Template(
        background=Image.new("RGBA", (W * ss, H_tall * ss), (0, 0, 0, 0)),
        font=ImageFont.truetype(font_path, int(size_px * ss), index=0),
        line_spacing=line_h * ss, fill=(0, 0, 0, 255),
        left_margin=mx * ss, right_margin=mx * ss,
        top_margin=size_px * 0.9 * ss, bottom_margin=0, word_spacing=0.0,
        line_spacing_sigma=0.0,
        font_size_sigma=2.0 * s, word_spacing_sigma=2.0 * s,
        perturb_x_sigma=2.6 * s, perturb_y_sigma=2.6 * s,
        perturb_theta_sigma=0.05 * wobble,
        start_chars="“（[<《「『", end_chars="，。、；：？！）]》」』”’",
    )
    pages = list(handwrite(body, tpl, seed=seed))
    if len(pages) > 1:
        print(f"[提示] 行数超过单张画布，handright 分了 {len(pages)} 页；"
              f"本函数只读取第 1 页，请把 --height-mm 调大或减小字号。", file=sys.stderr)
    alpha_tall = pages[0].getchannel("A")
    A = np.asarray(alpha_tall)

    rows = ink_rows(A, pitch=line_h * ss)
    if not rows:
        raise RuntimeError("没渲染出任何文字")

    pitch_ss = line_h * ss
    base = rows[0][1]                                   # 第一行墨迹的底边（ss 坐标）
    n_lines = int(round((rows[-1][1] - base) / pitch_ss)) + 1

    content_ss = (Hp - 2 * mx) * ss                     # 每页可用高度（ss）
    L = max(1, int(content_ss // pitch_ss))             # 每页行数
    n_pages = int(np.ceil(n_lines / L))

    out_files = []
    top_ss = mx * ss
    for p in range(n_pages):
        # 切口落在两行中间，避免切断任何一行
        y_from = int(round(base + (p * L - 0.5) * pitch_ss))
        y_to = int(round(base + ((p + 1) * L - 0.5) * pitch_ss))
        y_from = max(0, y_from)
        y_to = min(A.shape[0], y_to)
        if y_to <= y_from:
            break
        chunk = alpha_tall.crop((0, y_from, W * ss, y_to))

        page_alpha = Image.new("L", (W * ss, Hp * ss), 0)
        page_alpha.paste(chunk, (0, top_ss))

        # 本页第一行墨迹的底边（页面坐标），作为横线网格锚点
        ch_rows = ink_rows(np.asarray(page_alpha), pitch=pitch_ss)
        paper, fiber = hw.make_paper(W, Hp, rng_np, paper_rgb=(250, 247, 238), aged=aged)
        alpha = page_alpha.resize((W, Hp), Image.LANCZOS)
        a = np.asarray(alpha, np.float32) / 255.0
        a = np.clip(a * (0.86 + 0.14 * fiber), 0, 1)[..., None]
        ink = np.array(ink_rgb, np.float32) / 255.0
        img = Image.fromarray((np.clip(paper * (1 - a) + ink[None, None, :] * a, 0, 1) * 255)
                              .astype(np.uint8), "RGB")

        n_rules = 0
        if ruled and ch_rows:
            d = ImageDraw.Draw(img)
            anchor = ch_rows[0][1] / ss
            v = anchor - ((anchor - mx * 0.6) // line_h) * line_h
            while v < Hp - mx * 0.4:
                if v > mx * 0.4:
                    d.line([(mx - 34, int(v) + 3), (W - mx + 24, int(v) + 3)],
                           fill=(174, 192, 212), width=2)
                    n_rules += 1
                v += line_h
            d.line([(int(mx * 0.72), mx), (int(mx * 0.72), Hp - mx)],
                   fill=(186, 84, 72), width=2)

        fn = f"{out_prefix}_{p + 1}.png"
        img.save(fn, dpi=(dpi, dpi))
        out_files.append({"file": fn, "px": f"{W}x{Hp}", "rules": n_rules})

    return {"pages": out_files, "total_lines": n_lines, "lines_per_page": L}


def main(argv=None):
    p = argparse.ArgumentParser(description="手写字体 + 程序化信纸 → 成品 PNG")
    p.add_argument("--text", default=None)
    p.add_argument("--file", default=None)
    p.add_argument("--out", default="成品.png")
    p.add_argument("--font", default=os.path.join(FONT_DIR, "品如手写体.ttf"),
                   help="字体路径。HandWrite字体/ 下有 8 款可选")
    p.add_argument("--size", type=float, default=11.5, help="字号(磅)")
    p.add_argument("--line-spacing", type=float, default=1.75, help="行距=字号×该倍数")
    p.add_argument("--width-mm", type=float, default=148.0, help="纸宽(mm)，A5=148")
    p.add_argument("--height-mm", type=float, default=210.0, help="单页纸高(mm)，A5=210")
    p.add_argument("--pages", action="store_true",
                   help="长文分页模式：按 A5 逐页排，输出 <out>_1.png/_2.png…")
    p.add_argument("--margin-mm", type=float, default=16.0)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--aged", type=float, default=1.0, help="边缘泛黄强度，0=新纸")
    p.add_argument("--wobble", type=float, default=0.45, help="笔画抖动强度")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--no-ruled", action="store_true", help="不画横线（素纸）")
    a = p.parse_args(argv)

    if a.text is not None:
        text = a.text.replace("\\n", "\n")
    elif a.file:
        text = open(a.file, encoding="utf-8").read()
    else:
        raise SystemExit("需要 --text 或 --file")

    if a.pages:
        info = write_pages(text, a.out.replace(".png", ""), a.font, dpi=a.dpi,
                           width_mm=a.width_mm, height_mm=a.height_mm,
                           margin_mm=a.margin_mm, size_pt=a.size,
                           line_spacing=a.line_spacing, ruled=not a.no_ruled,
                           aged=a.aged, seed=a.seed, wobble=a.wobble)
        print(info)
        return info

    info = write_on_paper(text, a.out, a.font, dpi=a.dpi, width_mm=a.width_mm,
                          margin_mm=a.margin_mm, size_pt=a.size,
                          line_spacing=a.line_spacing, ruled=not a.no_ruled,
                          aged=a.aged, seed=a.seed, wobble=a.wobble)
    print(info)
    return info


if __name__ == "__main__":
    main()
