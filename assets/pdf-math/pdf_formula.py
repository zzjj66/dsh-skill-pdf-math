#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 PDF 里把公式按「文本层 + 坐标」还原成线性写法, 而不是靠看图猜。

为什么需要它:
  LaTeX 排出的 PDF 里, 公式是真实文字(带坐标/字号/字体), 不是图片。
  但直接复制文本会退化: 上下标被压平(u_i 变成 u i)、大运算符变成乱码
  (cmex10 里的 Σ 存成字母 'X', 开区间括号存成私用区码 U+F8EE)。
  本工具按基线偏移和字号还原上下标, 并用 TeX 字体编码表还原大运算符。

用法:
    python pdf_formula.py paper.pdf --page 30 --bbox 240,150,480,200
    python pdf_formula.py paper.pdf --page 30 --lines 3      # 该页第3个公式带
    python pdf_formula.py paper.pdf --page 30 --scan         # 列出所有公式带

输出: 线性化公式 + 无法映射的字形位置(用 render 出来的 PNG 自己核对)
"""
import argparse
import os
import re
import sys

from pathlib import Path

import pymupdf

sys.stdout.reconfigure(encoding="utf-8")

# TeX cmex10 (big operators / extensible delimiters)
# 只有「亲眼核对过渲染结果」的映射才放进 VERIFIED, 其余放 GUESS。
# 猜错的映射比不映射更糟: 它会输出一个看起来合理、实际错误的符号。
VERIFIED = {
    0x58: "∑",        # 显示体 Σ, 第 30 页式 (2.2) 已与 OCR 交叉核对
    0x50: "∑",        # 文本体 Σ
    0x52: "∫", 0x53: "∫", 0x5A: "∫", 0x5B: "∫",
    0xF8EE: "[", 0xF8EF: "[", 0xF8F0: "[",   # 高 [ 的上/中/下三段
    0xF8F9: "]", 0xF8FA: "]", 0xF8FB: "]",
}
# 按 TeX cmex10 惯例推断, 但未在本机逐字核对过 -> 输出时会被标注
GUESS = {
    0x51: "∏", 0x59: "∏", 0x48: "∮", 0x49: "∮",
    0x54: "⋃", 0x55: "⋃", 0x5C: "⋃", 0x5D: "⋃",
    0x56: "⋂", 0x57: "⋂", 0x5E: "⋂", 0x5F: "⋂",
    0x60: "∐", 0x61: "∐",
    0x00: "⎛", 0x01: "⎞", 0x02: "⎡", 0x03: "⎤", 0x04: "⎣", 0x05: "⎦",
    0x08: "⎧", 0x09: "⎫", 0x0A: "⎩", 0x0B: "⎭",
    0x0E: "⎜", 0x0F: "⎟", 0x10: "⎪", 0x11: "⎪",
    0x38: "⌈", 0x39: "⌉", 0x3A: "⌊", 0x3B: "⌋", 0x3C: "⟨", 0x3D: "⟩",
    0x70: "√", 0x71: "√", 0x72: "√", 0x74: "√", 0x75: "√", 0x76: "√",
    0xF8EB: "[", 0xF8EC: "[", 0xF8ED: "]",
    0xF8F1: "(", 0xF8F2: "(", 0xF8F3: ")", 0xF8F4: ")",
    0xF8F6: "[", 0xF8F7: "[", 0xF8F8: "]",
}
# 同一符号在「同一 x、相邻 y」重复出现 = 一个高定界符被拆成了多段, 需要折叠
DELIMS = set("[](){}⎡⎤⎣⎦⎛⎞⎜⎟⎧⎫⎩⎭⎪⌈⌉⌊⌋⟨⟩")

MAIN_TOL = 0.35      # 上标: 基线抬高 > 0.35em
SUB_TOL = 0.25       # 下标: 基线降低 > 0.25em


def is_math_font(name: str) -> bool:
    """只认真正的数学字体。

    CMR10 是正文罗马体(Computer Modern Roman), 名字里也带 'CM';
    识别错会让整页正文都被当成公式。
    """
    n = name.lower()
    if n.startswith(("cmmi", "cmsy", "cmex", "cmbsy", "cmmib")):
        return True
    return any(k in n for k in ("msam", "msbm", "stix", "symbol", "math", "euler", "rsfs"))


def decode(ch, font):
    """返回 (符号, 是否已验证)。None 表示这个字形没有被映射。"""
    c = ch["c"]
    code = ord(c)
    is_cmex = "CMEX" in font.upper()
    if is_cmex:
        if code in VERIFIED:
            return VERIFIED[code], True
        if code in GUESS:
            return GUESS[code], False
        return None, True                 # cmex10 里的未知槽位, 输出为 ?
    # 非 cmex 字体: 编码表绝不能参与, 否则 'I'=0x49 会变成 ∮, '='=0x3D 会变成 ⟩
    if 0xE000 <= code <= 0xF8FF:          # 私用区字形, 只可能是定界符
        if code in VERIFIED:
            return VERIFIED[code], True
        if code in GUESS:
            return GUESS[code], False
        return None, True
    if code < 0x20 and code not in (0x0A, 0x09):
        return None, True
    return c, True


def linearize(page, clip, unknown, unverified):
    """按 x 排序 + 基线偏移, 还原成带上下标的线性串。"""
    d = page.get_text("rawdict", clip=clip)
    chars = []
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    if ch["c"].isspace():
                        continue
                    ch = dict(ch)
                    ch["font"] = sp["font"]
                    ch["size"] = sp["size"]
                    chars.append(ch)
    if not chars:
        return ""
    from collections import Counter
    em = max(c["size"] for c in chars)
    # 主基线只看「正文字号」的字, 否则整行都是上标时机体会算错
    mains = [c for c in chars if c["size"] > 0.9 * em]
    base = Counter(round(c["origin"][1], 1) for c in mains or chars).most_common(1)[0][0]
    # x 相同者先排上面的(极限 N 在上, j=1 在下) -> ^{N}_{j=1}
    chars.sort(key=lambda c: (round(c["origin"][0], 1), c["origin"][1]))

    runs = []      # [[mode, text, x0, x1]]
    last = None    # (符号, x0, y) 用于折叠被拆成多段的高定界符
    for c in chars:
        s, ok = decode(c, c["font"])
        if s is None:
            unknown.append((c["font"], hex(ord(c["c"])), tuple(round(v, 1) for v in c["bbox"])))
            s = "?"
        elif not ok:
            unverified.append((c["font"], hex(ord(c["c"])), s))
        x0, y = c["bbox"][0], c["origin"][1]
        # 一个高 [ 在 PDF 里是「上/中/下」三段同 x 叠起来的, 不折叠会变成 [[[
        if (s in DELIMS and last and last[0] == s
                and abs(x0 - last[1]) < 1.5 and y > last[2] + 1.0):
            last = (s, x0, y)
            continue
        if s in DELIMS:
            last = (s, x0, y)
        else:
            last = None
        dy = y - base
        if c["size"] < 0.9 * em:
            mode = "sub" if dy > 0 else "sup"
        else:
            mode = None
        if runs and runs[-1][0] == mode:
            runs[-1][1] += s
            runs[-1][3] = c["bbox"][2]
        else:
            runs.append([mode, s, c["bbox"][0], c["bbox"][2]])

    out = []
    prev_end = None
    for mode, text, x0, x1 in runs:
        if prev_end is not None and mode is None and x0 - prev_end > 0.12 * em:
            out.append(" ")
        if mode:
            out.append(("^{" if mode == "sup" else "_{") + text + "}")
        else:
            out.append(text)
        prev_end = x1
    return "".join(out)


def find_bands(page):
    """找出显示公式带。

    两个要点:
      1. 只取「数学字体占比高」的 block —— 否则正文段落里带一个行内符号就整段入选;
      2. 相邻的候选块要合并 —— 矩阵在 PDF 里被拆成一行一个 block, 不合并的话
         交出去的是一个碎片, 公式 OCR 会把它当正文硬解, 输出大段垃圾。
    """
    d = page.get_text("dict")
    cands = []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        n_all = n_math = 0
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                n = len(sp["text"])
                n_all += n
                if is_math_font(sp["font"]):
                    n_math += n
        if n_all and n_math / n_all >= 0.35:
            cands.append(pymupdf.Rect(b["bbox"]))
    if not cands:
        return []

    cands.sort(key=lambda r: (r.y0, r.x0))

    def near(a, b):
        vgap = max(0.0, max(a.y0, b.y0) - min(a.y1, b.y1))
        hgap = max(0.0, max(a.x0, b.x0) - min(a.x1, b.x1))
        hov = min(a.x1, b.x1) - max(a.x0, b.x0)
        vov = min(a.y1, b.y1) - max(a.y0, b.y0)
        h = max(1.0, min(a.height, b.height))
        # 上下相邻(同一公式换行)
        if vgap < 0.8 * h and hov > 0.3 * min(max(a.width, 1.0), max(b.width, 1.0)):
            return True
        # 左右相邻(同一行的不同片段, 例如 "u_i = cK" 与后面的主体)
        return hgap < 0.5 * h and vov > 0.5 * h

    bands = list(cands)
    changed = True
    while changed:                      # 反复合并直到稳定, 片段常常要连好几轮
        changed = False
        for i in range(len(bands)):
            for j in range(i + 1, len(bands)):
                if near(bands[i], bands[j]):
                    bands[i] = bands[i] | bands[j]
                    del bands[j]
                    changed = True
                    break
            if changed:
                break
    bands.sort(key=lambda r: (r.y0, r.x0))
    return bands


def probe(page):
    """这页的公式是「可提取的文字」还是「图片」? 这决定用哪条路。"""
    d = page.get_text("dict")
    math_chars, text_chars = 0, 0
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                n = len(sp["text"])
                text_chars += n
                if is_math_font(sp["font"]):
                    math_chars += n
    imgs = page.get_image_info()
    # 图片里有没有文字覆盖 -> 有则是插图, 没有则可能是「公式截图」
    pic_pages = 0
    for info in imgs:
        r = pymupdf.Rect(info["bbox"])
        inside = sum(len(sp["text"]) for b in d["blocks"] if b.get("type") == 0
                     for ln in b.get("lines", []) for sp in ln["spans"]
                     if pymupdf.Rect(sp["bbox"]) in r)
        if inside < 3 and r.width * r.height > 400:
            pic_pages += 1
    print(f"页面: {page.number + 1}")
    print(f"  文字字符 {text_chars}, 其中数学字体 {math_chars}"
          f" ({math_chars / max(text_chars, 1):.1%}) -> " +
          ("公式是可提取文字, 用本工具的文本层模式" if math_chars > 20 else
           "几乎没有数学字体"))
    print(f"  图片 {len(imgs)} 张, 其中 {pic_pages} 张区域内没有文字")
    if pic_pages:
        print("  ⚠ 这些图里的公式/数据没有文本层, 只能看图识别 -> 裁切放大后目视")
        for info in imgs[:6]:
            print(f"    bbox {[round(v, 1) for v in info['bbox']]}  "
                  f"{info['width']}x{info['height']}px")


def _ocr_dirs():
    """OCR 运行时的候选目录, 按优先级排列。"""
    env = os.environ.get("DSH_FORMULA_OCR")
    if env:
        yield Path(env)
    home = Path.home()
    yield home / ".dsh" / "runtimes" / "formula-ocr" / "py"   # 全局安装位置
    for parent in Path(__file__).resolve().parents:            # 旧的工作区位置
        yield parent / ".tools" / "py"


def _boot_ocr():
    """找到本地安装的 rapid_latex_ocr, 避免依赖外部 PYTHONPATH。

    位置优先级: $DSH_FORMULA_OCR > ~/.dsh/runtimes/formula-ocr/py > <上层>/.tools/py

    注意插入顺序: pymupdf 不会提前加载 numpy, 所以把找到的目录放到 sys.path[0],
    能让 onnxruntime 拿到它自己那份 numpy 1.26, 而不是系统的 2.x。
    """
    try:
        from rapid_latex_ocr import LaTeXOCR
        return LaTeXOCR
    except ImportError:
        pass
    for cand in _ocr_dirs():
        if not (cand / "rapid_latex_ocr").is_dir():
            continue
        sys.path.insert(0, str(cand))
        try:
            from rapid_latex_ocr import LaTeXOCR
            return LaTeXOCR
        except ImportError:
            sys.path.pop(0)
    return None


def _glyph_style(font: str) -> str:
    """字体名 -> 该字体里拉丁字母的 LaTeX 语义。"""
    f = font.upper()
    if "CMSY" in f or "CMBSY" in f:
        return "mathcal"        # cmsy10 的 A-Z 就是 \mathcal{A}..\mathcal{Z}
    if "MSBM" in f:
        return "mathbb"
    if "EUFM" in f:
        return "mathfrak"
    if "RSFS" in f:
        return "mathscr"
    if "CMMI" in f:
        return "mathit"
    return "plain"


MACRO_RE = re.compile(r"\\[A-Za-z]+")


def crosscheck(page, clip, latex):
    """把 OCR 结果与文本层的字母对照, 指出可疑符号。

    两个通道的强项不同:
      * 文本层: 字母/数字/Greek 是精确的, 而且**字体名告诉你它的 LaTeX 语义**
        (cmsy10 的 L 就是 \\mathcal{L});
      * OCR: 结构(分式/上下标/矩阵)是对的, 但个别字形会认错(\\mathcal{L}->\\angle->Z)。
    所以只要两边字母对不上, 那个位置就有问题, 而字体能告诉我们应该是什么。
    """
    d = page.get_text("rawdict", clip=clip)
    layer = {}
    for b in d["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    c = ch["c"]
                    if not (c.isalpha() and c.isascii()):
                        continue
                    # cmex10 里的大运算符在文本层就是普通字母(X=Σ), 不是真字母;
                    # 混进来会造成假告警(实测把 Σ 报成可疑字母 X)。
                    if "CMEX" in sp["font"].upper():
                        continue
                    style = _glyph_style(sp["font"])
                    key = (c, style, sp["font"])
                    layer[key] = layer.get(key, 0) + 1

    ocred = [c for c in MACRO_RE.sub("", latex) if c.isalpha() and c.isascii()]
    ocr_counts = {}
    for c in ocred:
        ocr_counts[c] = ocr_counts.get(c, 0) + 1

    layer_letters = {}
    for (c, style, font), n in layer.items():
        layer_letters[c] = layer_letters.get(c, 0) + n

    only_ocr = {c: n for c, n in ocr_counts.items() if c not in layer_letters}
    only_layer = {c: n for c, n in layer_letters.items() if c not in ocr_counts}

    print("\n=== 交叉核对 ===")
    if not only_ocr and not only_layer:
        print("  两边的字母集合一致, 没有明显可疑符号")
        return
    if only_ocr:
        print("  只在 OCR 里出现(可能是认错的字形):")
        for c, n in sorted(only_ocr.items(), key=lambda kv: -kv[1])[:6]:
            print(f"    {c!r} x{n}")
    if only_layer:
        print("  只在文本层里出现(OCR 可能把它认成了别的):")
        for c in sorted(only_layer):
            for (ch, style, font), n in layer.items():
                if ch == c:
                    macro = f"\\{style}{{{c}}}" if style != "plain" else c
                    print(f"    {c!r} x{n}  <- {font} ({macro})")
                    break
    print("\n  提示: 以文本层为准看字母与字体; 以 OCR 为准看结构。")


def _ocr_once(cls, page, clip, dpi):
    """把页面区域直接以像素数组交给模型。

    不落临时文件: 工作区外的沙箱可能连系统 temp 都不给写
    (tempfile 会抛 "No usable temporary directory found")。
    numpy 必须在这里才 import —— _boot_ocr 已经把 OCR 自带的
    numpy 1.26 放到了 sys.path[0], 提前 import 会拿到系统的 2.x。
    """
    import time

    import numpy as np

    pix = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n
    )
    if pix.n == 1:
        img = np.repeat(img, 3, axis=2)
    elif pix.n == 4:
        img = img[:, :, :3]
    t0 = time.time()
    latex, _ = cls()(img)
    return latex, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, required=True, help="1-based 页码")
    ap.add_argument("--bbox", help="x0,y0,x1,y1 (PDF 点)")
    ap.add_argument("--lines", type=int, help="该页第 N 个公式带 (1-based)")
    ap.add_argument("--scan", action="store_true", help="只列出公式带")
    ap.add_argument("--probe", action="store_true", help="判断本页公式是文字还是图片")
    ap.add_argument("--crosscheck", action="store_true",
                    help="配合 --ocr: 用文本层字母+字体核对 OCR 结果, 标出可疑符号")
    ap.add_argument("--verify", action="store_true",
                    help="配合 --ocr: 换一个裁切框再识别一次, 不一致会告警")
    ap.add_argument("--ocr", action="store_true",
                    help="再走一遍公式 OCR(rapid_latex_ocr), 需 PYTHONPATH 含 .tools/py")
    ap.add_argument("--render", help="同时把该区域渲染成 PNG, 便于目视核对")
    ap.add_argument("--dpi", type=int, default=420)
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    page = doc[a.page - 1]

    if a.probe:
        probe(page)
        return

    if a.bbox:
        clip = pymupdf.Rect(*[float(v) for v in a.bbox.split(",")])
    else:
        bands = find_bands(page)
        if a.scan or not bands:
            for i, r in enumerate(bands, 1):
                print(f"band {i}: {[round(v, 1) for v in r]}")
            return
        # block 的 bbox 不含上下标的出血部分: Σ 的 j=1 常在框外, 必须留白
        b = bands[a.lines - 1 if a.lines else 0]
        pad_x = max(2.0, 0.02 * b.width)
        pad_y = max(4.0, 0.30 * b.height)
        clip = pymupdf.Rect(b.x0 - pad_x, b.y0 - pad_y, b.x1 + pad_x, b.y1 + pad_y)

    unknown, unverified = [], []
    print(linearize(page, clip, unknown, unverified))
    if unknown:
        print("\n无法映射的字形(输出里显示为 ?):")
        for f, code, bb in unknown[:20]:
            print(f"  {f:10s} {code:6s} at {bb}")
    if unverified:
        seen = {}
        for _f, code, sym in unverified:
            seen[(code, sym)] = seen.get((code, sym), 0) + 1
        print("\n注意: 以下字形映射未经验证, 符号可能是错的:")
        for (code, sym), n in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {code:6s} -> {sym}  x{n}")

    if a.render:
        page.get_pixmap(clip=clip, dpi=a.dpi).save(a.render)
        print(f"\n已渲染: {a.render}")

    if a.ocr:
        cls = _boot_ocr()
        if cls is None:
            print("\n[OCR] 未找到 rapid_latex_ocr, 跳过。"
                  "安装: pip install --target .tools/py rapid_latex_ocr")
            return
        latex, dt = _ocr_once(cls, page, clip, 600)
        print(f"\n[OCR {dt:.1f}s] {latex}")
        if a.crosscheck:
            crosscheck(page, clip, latex)
        if a.verify:
            # 同一个公式, 裁切边界差几点, 结果就可能不同(\mathcal{L}<->\angle,
            # j=1 <-> i-1)。换一个框再跑一次, 不一致就说明这次不可信。
            import re
            alt = pymupdf.Rect(clip.x0 - 2, clip.y0 - 3, clip.x1 + 2, clip.y1 + 3)
            latex2, dt2 = _ocr_once(cls, page, alt, 1000)
            norm = lambda s: re.sub(r"\s+|\\quad|\\qquad|\\,|\\;", "", s)
            same = norm(latex) == norm(latex2)
            print(f"[OCR 复核 {dt2:.1f}s] {latex2}")
            print("[复核] " + ("两次一致" if same
                               else "⚠ 两次裁切结果不一致 -> 这条公式需要人工确认"))


if __name__ == "__main__":
    main()
