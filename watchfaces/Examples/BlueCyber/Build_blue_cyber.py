#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Blue_Cyber -- cyberpunk watch face builder for the TOOBUR IDW20 (VeryFit / IDO .iwf)

Generates the NON-COMPILED watch face source tree (same layout as f468.zip, but with
widget-type folder names instead of g620/g12/...), a 272x324 preview rendered inside the
supplied bezel, and optionally compiles it to a binary .iwf.

    python build_blue_cyber.py                 # source tree + zip
    python build_blue_cyber.py --compile       # ...and Blue_Cyber.iwf

Needs: Pillow, numpy.   Inputs next to this script (or pass paths): ChickenPunk.ttf, 840.png

Output tree (out/Blue_Cyber/):
    iwf.json  font.json  preview.png  files61.png
    hour/0..9.png   min/0..9.png   day/0..9.png   week/en_sun..en_sat.png
    battery/0..10.png (10 = '%')   step/0..9.png   calorie/0..9.png

Every image is pre-quantised exactly the way the watch stores it (RGB565 + 4-bit alpha,
ordered-dithered), so the PNGs in the source tree are what the watch will display and
compiling is lossless.
"""
import argparse
import json
import math
import os
import random
import struct
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ----------------------------------------------------------------------------------------
# Identity / manifest constants
# ----------------------------------------------------------------------------------------
NAME = "Blue_Cyber"
AUTHOR = "uysx"
DEVICE = "IDW20"
SCREEN_W, SCREEN_H = 320, 385          # background size (native panel resolution)
PREV_W, PREV_H = 272, 324              # preview.png size
BACKGROUND_FILE = "files61.png"        # same entry name as the stock face
WEEK_ORDER = ["sun", "mon", "tue", "wed", "thur", "fri", "sat"]   # order used by stock face

# ----------------------------------------------------------------------------------------
# Palette ("Blue Cyber")
# ----------------------------------------------------------------------------------------
CYAN = (0, 229, 255)
BLUE = (41, 121, 255)
DEEP = (18, 52, 190)
VIOLET = (128, 108, 255)
ICE = (222, 250, 255)


def lighten(c, t):
    return tuple(int(v + (255 - v) * t) for v in c)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ----------------------------------------------------------------------------------------
# Widget layout (design space = 320x385).  Box = exactly the widest string it can show.
# ----------------------------------------------------------------------------------------
# glyph style: top/bottom = vertical gradient of the glyph body, glow = (radius_px, strength)
STYLES = {
    "hour":    dict(top=(255, 255, 255), bottom=(150, 238, 255), glow_c=CYAN, glow=[(1.5, .95), (4, .7), (8, .38)],
                    extrude=3, ext_c=(16, 66, 214), stripes=True),
    "min":     dict(top=(170, 232, 255), bottom=(44, 132, 255), glow_c=BLUE, glow=[(1.5, .95), (4, .75), (8, .42)],
                    extrude=3, ext_c=(8, 30, 140), stripes=True),
    "day":     dict(top=ICE, bottom=CYAN, glow_c=CYAN, glow=[(1.2, .9), (3, .55)], extrude=0),
    "week":    dict(top=ICE, bottom=CYAN, glow_c=CYAN, glow=[(1.2, .9), (3.5, .6)], extrude=1, ext_c=(10, 50, 170)),
    "step":    dict(top=ICE, bottom=CYAN, glow_c=CYAN, glow=[(1.2, .9), (3, .55)], extrude=1, ext_c=(8, 60, 150)),
    "calorie": dict(top=(236, 230, 255), bottom=VIOLET, glow_c=VIOLET, glow=[(1.2, .9), (3, .55)], extrude=1,
                    ext_c=(40, 28, 140)),
    "battery": dict(top=ICE, bottom=(96, 176, 255), glow_c=BLUE, glow=[(1.2, .9), (3, .55)], extrude=1, ext_c=(8, 30, 130)),
}

# type, cell(w,h) of one glyph bitmap, ink height of digits, max chars, box position, alignment
WIDGETS = [
    dict(type="hour",    cell=(60, 100), ink=66, maxch=2, x=20,  y=92,  align="left",   addwh=True,  fontnum=10),
    dict(type="min",     cell=(60, 100), ink=66, maxch=2, x=180, y=92,  align="left",   addwh=True,  fontnum=10),
    dict(type="day",     cell=(22, 32),  ink=22, maxch=2, x=250, y=40,  align="center", addwh=False, fontnum=10),
    dict(type="week",    cell=(84, 30),  ink=19, maxch=1, x=28,  y=41,  align="left",   addwh=False, fontnum=7, style=0),
    dict(type="battery", cell=(20, 28),  ink=19, maxch=3, x=137, y=309, align="left",   addwh=True,  fontnum=11,
         extra_w=26),                                     # + one '%' glyph (26 px wide)
    dict(type="step",    cell=(24, 34),  ink=25, maxch=5, x=25,  y=250, align="center", addwh=True,  fontnum=10),
    dict(type="calorie", cell=(24, 34),  ink=25, maxch=5, x=175, y=250, align="center", addwh=True,  fontnum=10),
]
for _w in WIDGETS:
    _w["w"] = _w["cell"][0] * _w["maxch"] + _w.get("extra_w", 0)
    _w["h"] = _w["cell"][1]
    if _w["type"] == "week":
        _w["w"] = _w["cell"][0]
WBY = {w["type"]: w for w in WIDGETS}

# sample values used for the preview image
SAMPLE = {"hour": "10", "min": "24", "day": "16", "week": "mon", "battery": "87%", "step": "8426", "calorie": "312"}

# ----------------------------------------------------------------------------------------
# Quantisation: what the watch can actually store (RGB565 big-endian, 4-bit alpha)
# ----------------------------------------------------------------------------------------
_B8 = np.array([[0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26], [12, 44, 4, 36, 14, 46, 6, 38],
                [60, 28, 52, 20, 62, 30, 54, 22], [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
                [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]])
_B4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]])


def _thr(h, w, m):
    n = m.shape[0]
    t = np.tile((m + .5) / (n * n), (h // n + 1, w // n + 1))[:h, :w]
    return t


def quantize_rgb(rgb01):
    """float (H,W,3) in 0..1 -> uint8 (H,W,3) holding exactly-representable RGB565 values."""
    h, w, _ = rgb01.shape
    t = _thr(h, w, _B8)
    r5 = np.clip(np.floor(rgb01[..., 0] * 31 + t), 0, 31).astype(np.int32)
    g6 = np.clip(np.floor(rgb01[..., 1] * 63 + t), 0, 63).astype(np.int32)
    b5 = np.clip(np.floor(rgb01[..., 2] * 31 + t), 0, 31).astype(np.int32)
    return np.dstack([r5 * 255 // 31, g6 * 255 // 63, b5 * 255 // 31]).astype(np.uint8)


def quantize_rgba(rgba01):
    h, w, _ = rgba01.shape
    rgb = quantize_rgb(rgba01[..., :3])
    a4 = np.clip(np.floor(rgba01[..., 3] * 15 + _thr(h, w, _B4)), 0, 15).astype(np.int32)
    rgb[a4 == 0] = 0
    return np.dstack([rgb, (a4 * 17).astype(np.uint8)])


# ----------------------------------------------------------------------------------------
# Glyph (digit / label) rendering with neon glow
# ----------------------------------------------------------------------------------------
class Glyphs:
    SS = 4

    def __init__(self, font_path):
        self.font_path = font_path
        self._cache = {}

    def font_for(self, ref_char, ink_px):
        key = (ref_char, ink_px)
        if key not in self._cache:
            big = ImageFont.truetype(self.font_path, 1000)
            l, t, r, b = big.getbbox(ref_char, anchor="ls")
            size = max(4, round(1000 * ink_px / (b - t)))
            self._cache[key] = ImageFont.truetype(self.font_path, size)
        return self._cache[key]

    def render(self, text, cell, ink, style, ref_char="0", left_pad=None):
        SS = self.SS
        cw, ch = cell
        W, H = cw * SS, ch * SS
        font = self.font_for(ref_char, ink * SS)
        _, t0, _, b0 = font.getbbox(ref_char, anchor="ls")
        base = H / 2 - (t0 + b0) / 2
        l, t, r, b = font.getbbox(text, anchor="ls")
        x = (W / 2 - (l + r) / 2) if left_pad is None else (left_pad * SS - l)
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).text((x, base), text, font=font, fill=255, anchor="ls")
        mk = np.asarray(m, np.float32) / 255

        # extrusion (down-right)
        ext = np.zeros_like(mk)
        for i in range(1, style.get("extrude", 0) * SS + 1):
            ext = np.maximum(ext, np.roll(np.roll(mk, int(round(i * .7)), 1), i, 0))
        union = np.maximum(mk, ext)

        # glow
        u_img = Image.fromarray((union * 255).astype(np.uint8))
        galpha = np.zeros_like(mk)
        for rad, k in style["glow"]:
            galpha += k * np.asarray(u_img.filter(ImageFilter.GaussianBlur(rad * SS)), np.float32) / 255
        galpha = np.clip(galpha, 0, 1)
        # feather the glow to zero at the cell border so it never shows a hard cut-off
        ey = np.minimum(np.arange(H), H - 1 - np.arange(H))[:, None]
        ex = np.minimum(np.arange(W), W - 1 - np.arange(W))[None, :]
        win = np.clip(np.minimum(ey, ex) / (5.0 * SS), 0, 1)
        galpha *= win * win * (3 - 2 * win)

        # body gradient
        yy = (np.arange(H)[:, None] - (base + t0)) / max(1, (b0 - t0))
        g = np.clip(yy, 0, 1)[..., None]
        body = np.array(style["top"], np.float32) * (1 - g) + np.array(style["bottom"], np.float32) * g
        body = np.broadcast_to(body, (H, W, 3))

        core_a = mk.copy()
        if style.get("stripes"):
            ys = np.arange(H) // SS
            mid = (base + t0 + (b0 - t0) * .45) / SS
            cut = ((ys % 4) == 3) & (ys > mid)
            core_a[cut, :] *= .42

        def layer(rgb, a):
            arr = np.zeros((H, W, 4), np.float32)
            arr[..., :3] = np.broadcast_to(np.array(rgb, np.float32), (H, W, 3)) if np.ndim(rgb) == 1 else rgb
            arr[..., 3] = a * 255
            return Image.fromarray(arr.round().astype(np.uint8), "RGBA")

        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out = Image.alpha_composite(out, layer(np.array(style["glow_c"]), galpha))
        if style.get("extrude"):
            out = Image.alpha_composite(out, layer(np.array(style["ext_c"]), ext))
        out = Image.alpha_composite(out, layer(body, core_a))
        out = out.resize((cw, ch), Image.LANCZOS)
        a = np.asarray(out, np.float32) / 255
        return Image.fromarray(quantize_rgba(a), "RGBA")


# ----------------------------------------------------------------------------------------
# Background artwork
# ----------------------------------------------------------------------------------------
class Art:
    """Float RGB canvas at 2x supersampling; neon primitives are screen-blended."""
    S = 2

    def __init__(self, font_path):
        S = self.S
        self.W, self.H = SCREEN_W * S, SCREEN_H * S
        self.buf = np.zeros((self.H, self.W, 3), np.float32)
        self.font_path = font_path
        self._f = {}

    # --- helpers
    def font(self, size):
        k = round(size * self.S)
        if k not in self._f:
            self._f[k] = ImageFont.truetype(self.font_path, k)
        return self._f[k]

    def mask(self, fn):
        im = Image.new("L", (self.W, self.H), 0)
        fn(ImageDraw.Draw(im), self.S)
        return im

    @staticmethod
    def arr(im):
        return np.asarray(im, np.float32) / 255

    def blur(self, im, r):
        return self.arr(im.filter(ImageFilter.GaussianBlur(r * self.S)))

    def screen(self, m, color, k=1.0):
        c = np.array(color, np.float32) / 255
        self.buf = 1 - (1 - self.buf) * (1 - np.clip(m, 0, 1)[..., None] * c * k)

    def over(self, m, color, a=1.0):
        c = np.array(color, np.float32) / 255
        mm = (np.clip(m, 0, 1) * a)[..., None]
        self.buf = self.buf * (1 - mm) + mm * c

    def neon(self, fn, color, core=None, glows=((1.2, .85), (4, .55), (10, .30)), alpha=1.0):
        im = self.mask(fn)
        for r, k in glows:
            self.screen(self.blur(im, r), color, k * alpha)
        self.over(self.arr(im), core if core else lighten(color, .55), alpha)

    # --- primitives (design coordinates)
    def poly_line(self, pts, w, close=True):
        def fn(d, S):
            p = [(x * S, y * S) for x, y in pts]
            if close:
                p = p + [p[0], p[1]]
            d.line(p, fill=255, width=max(1, round(w * S)), joint="curve")
        return fn

    def poly_fill(self, pts):
        def fn(d, S):
            d.polygon([(x * S, y * S) for x, y in pts], fill=255)
        return fn

    def text(self, xy, s, size, color, track=0.0, anchor="l", glow=True, alpha=1.0, core=None):
        S = self.S
        f = self.font(size)
        widths = [f.getlength(c) for c in s]
        total = sum(widths) + track * S * (len(s) - 1)
        x0 = xy[0] * S - (total / 2 if anchor == "c" else total if anchor == "r" else 0)

        def fn(d, _S):
            x = x0
            for c, w in zip(s, widths):
                d.text((x, xy[1] * S), c, font=f, fill=255, anchor="ls")
                x += w + track * S
        self.neon(fn, color, core=core, glows=((.8, .7), (2.5, .45)) if glow else (), alpha=alpha)


def chamfer(x0, y0, x1, y1, tl=0, tr=0, br=0, bl=0):
    return [(x0 + tl, y0), (x1 - tr, y0), (x1, y0 + tr), (x1, y1 - br), (x1 - br, y1), (x0 + bl, y1),
            (x0, y1 - bl), (x0, y0 + tl)]


def clean(pts):
    out = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    if out[0] == out[-1]:
        out.pop()
    return out


def draw_background(font_path):
    A = Art(font_path)
    S, W, H = A.S, A.W, A.H
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, SCREEN_W, W, dtype=np.float32)[None, :]
    ypx = np.linspace(0, SCREEN_H, H, dtype=np.float32)[:, None]

    # 1. base gradient + glows ------------------------------------------------------------
    stops = [(0.0, (2, 4, 13)), (.5, (4, 10, 32)), (1.0, (4, 14, 44))]
    base = np.zeros((H, 1, 3), np.float32)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = ((yy >= p0) & (yy <= p1))[..., None]
        t = np.clip((yy - p0) / (p1 - p0), 0, 1)[..., None]
        base = np.where(m, np.array(c0) * (1 - t) + np.array(c1) * t, base)
    A.buf = np.broadcast_to(base / 255, (H, W, 3)).copy()

    def blob(cx, cy, sx, sy, color, k):
        g = np.exp(-(((xx - cx) / sx) ** 2 + ((ypx - cy) / sy) ** 2))
        A.screen(g, color, k)
    blob(160, 142, 150, 84, (10, 64, 200), .62)       # behind time
    blob(160, 140, 70, 40, (30, 110, 255), .25)
    blob(60, 20, 90, 60, (60, 40, 170), .20)          # violet haze top-left
    blob(260, 300, 110, 70, (30, 40, 150), .22)       # violet haze lower-right
    blob(160, 376, 190, 20, (0, 140, 255), .55)       # horizon glow

    # 2. faint tech grid + dot matrix --------------------------------------------------------
    grid = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grid)
    for x in range(0, SCREEN_W + 1, 16):
        gd.line([(x * S, 0), (x * S, H)], fill=255, width=1)
    for y in range(0, SCREEN_H + 1, 16):
        gd.line([(0, y * S), (W, y * S)], fill=255, width=1)
    A.screen(A.arr(grid), (30, 90, 220), .055)

    # 3. skyline (drawn before HUD so panels sit over it) ---------------------------------------
    rng = random.Random(11)
    x = -4
    blds = []
    while x < SCREEN_W + 4:
        bw = rng.randint(12, 26)
        bh = rng.randint(12, 38)
        blds.append((x, bw, bh))
        x += bw + rng.randint(-2, 3)
    far = Image.new("L", (W, H), 0)
    near = Image.new("L", (W, H), 0)
    fd, nd = ImageDraw.Draw(far), ImageDraw.Draw(near)
    for (bx, bw, bh) in blds:                      # far layer, shifted
        h2 = int(bh * .8) + 6
        fd.rectangle([(bx + 5) * S, (SCREEN_H - 16 - h2) * S, (bx + 5 + bw) * S, SCREEN_H * S], fill=255)
    for (bx, bw, bh) in blds:
        nd.rectangle([bx * S, (SCREEN_H - 12 - bh) * S, (bx + bw) * S, SCREEN_H * S], fill=255)
        if rng.random() < .35:                     # antenna
            ax = bx + bw / 2
            nd.line([ax * S, (SCREEN_H - 12 - bh - 9) * S, ax * S, (SCREEN_H - 12 - bh) * S], fill=255, width=S)
    A.over(A.arr(far), (6, 20, 62), .85)
    A.over(A.arr(near), (3, 8, 26), .96)
    edge = Image.new("L", (W, H), 0)
    ed = ImageDraw.Draw(edge)
    for (bx, bw, bh) in blds:
        y0 = SCREEN_H - 12 - bh
        ed.line([bx * S, y0 * S, (bx + bw) * S, y0 * S], fill=255, width=S)
    A.screen(A.blur(edge, 1.2), (0, 150, 255), .7)
    A.screen(A.arr(edge), (0, 200, 255), .45)
    win = Image.new("L", (W, H), 0)
    wd = ImageDraw.Draw(win)
    wins = []
    for (bx, bw, bh) in blds:
        for wy in range(int(SCREEN_H - 12 - bh + 4), SCREEN_H - 3, 5):
            for wx in range(int(bx + 3), int(bx + bw - 3), 4):
                if rng.random() < .30:
                    wins.append((wx, wy, rng.random()))
                    wd.rectangle([wx * S, wy * S, (wx + 2) * S, (wy + 2) * S], fill=255)
    A.screen(A.arr(win), (60, 190, 255), .65)
    A.screen(A.blur(win, 2), (0, 110, 255), .6)
    # street line
    A.neon(A.poly_line([(0, SCREEN_H - 5), (SCREEN_W, SCREEN_H - 5)], 1, close=False), CYAN, alpha=.7)

    # 4. glass panels -------------------------------------------------------------------------
    def glass(pts, color=(6, 20, 56), a=.62):
        A.over(A.arr(A.mask(A.poly_fill(pts))), color, a)

    time_pts = clean(chamfer(12, 84, 308, 200, tl=18, br=18))
    glass(time_pts, (5, 16, 50), .60)
    cardL = clean(chamfer(14, 214, 156, 298, tr=14, bl=14))
    cardR = clean(chamfer(164, 214, 306, 298, tl=14, br=14))
    glass(cardL, (5, 18, 54), .66)
    glass(cardR, (14, 12, 54), .66)
    badge = clean(chamfer(240, 36, 304, 76, tr=10, bl=10))
    glass(badge, (6, 20, 60), .7)

    # 5. time frame ---------------------------------------------------------------------------
    A.neon(A.poly_line(time_pts, 1.1), CYAN, alpha=.9)
    inner = clean(chamfer(17, 89, 303, 195, tl=14, br=14))
    A.neon(A.poly_line(inner, .6), BLUE, glows=((1.5, .3),), alpha=.45)
    # corner brackets (heavy)
    A.neon(A.poly_line([(12, 112), (12, 102), (30, 84), (56, 84)], 2.6, close=False), CYAN, core=ICE)
    A.neon(A.poly_line([(308, 172), (308, 182), (290, 200), (264, 200)], 2.6, close=False), CYAN, core=ICE)
    A.neon(A.poly_line([(306, 84), (308, 86)], 2, close=False), BLUE)
    # label tabs
    for lx, s in ((26, "HRS"), (186, "MIN")):
        A.text((lx, 101), s, 8, CYAN, track=1.6, glow=False, alpha=.85)
    # colon diamonds
    for cy in (122, 162):
        dm = [(160, cy - 8), (168, cy), (160, cy + 8), (152, cy)]
        A.neon(A.poly_line(dm, 1.2), CYAN, core=ICE, glows=((1.2, .9), (4, .7), (9, .5)))
        A.screen(A.arr(A.mask(A.poly_fill([(160, cy - 4.5), (164.5, cy), (160, cy + 4.5), (155.5, cy)]))), ICE, .9)
    # edge ruler ticks (inside, left/right)
    tk = Image.new("L", (W, H), 0)
    td = ImageDraw.Draw(tk)
    for i, ty in enumerate(range(112, 194, 6)):
        ln = 5 if i % 3 == 0 else 3
        td.line([(18 * S, ty * S), ((18 + ln) * S, ty * S)], fill=255, width=1)
        td.line([((302 - ln) * S, ty * S), (302 * S, ty * S)], fill=255, width=1)
    A.screen(A.arr(tk), CYAN, .5)
    # tiny data strip under the minute digits' frame
    A.text((26, 194), "TIME//LOCAL", 7.5, BLUE, track=1.2, glow=False, alpha=.8)

    # 6. top HUD ------------------------------------------------------------------------------
    A.text((160, 24), "BLUE // CYBER", 11, ICE, track=2.4, anchor="c", alpha=.9, core=ICE)
    for sgn in (-1, 1):
        x0 = 160 + sgn * 62
        x1 = 160 + sgn * 112
        A.neon(A.poly_line([(x0, 20), (x1, 20)], .8, close=False), CYAN, glows=((1.2, .5),), alpha=.8)
        A.neon(A.poly_line([(x1, 20), (x1 + sgn * 6, 14)], .8, close=False), CYAN, glows=((1.2, .5),), alpha=.8)
        A.screen(A.arr(A.mask(A.poly_fill([(x1 + sgn * 8, 11), (x1 + sgn * 12, 11), (x1 + sgn * 12, 15),
                                           (x1 + sgn * 8, 15)]))), CYAN, .8)

    # header: weekday bar + day badge
    A.screen(A.arr(A.mask(A.poly_fill([(19, 40), (22, 40), (22, 70), (19, 70)]))), CYAN, .9)
    A.screen(A.blur(A.mask(A.poly_fill([(19, 40), (22, 40), (22, 70), (19, 70)])), 3), CYAN, .6)
    A.neon(A.poly_line(badge, 1.1), CYAN, alpha=.9)
    A.neon(A.poly_line([(240, 60), (240, 36), (256, 36)], 2.4, close=False), CYAN, core=ICE)
    # barcode / signal bars between weekday and badge
    rb = random.Random(3)
    bars = Image.new("L", (W, H), 0)
    bd = ImageDraw.Draw(bars)
    xb = 124
    for i in range(18):
        h_ = rb.choice([4, 6, 9, 12, 16])
        bd.rectangle([xb * S, (66 - h_) * S, (xb + 2) * S, 66 * S], fill=255)
        xb += rb.choice([4, 5, 6])
        if xb > 226:
            break
    A.screen(A.arr(bars), BLUE, .85)
    A.screen(A.blur(bars, 2), BLUE, .6)
    A.neon(A.poly_line([(122, 69), (232, 69)], .7, close=False), CYAN, glows=((1.5, .4),), alpha=.65)
    A.text((122, 46), "CALENDAR", 8, CYAN, track=1.6, glow=False, alpha=.75)

    # 7. mid divider ---------------------------------------------------------------------------
    A.neon(A.poly_line([(20, 207), (130, 207), (136, 211), (184, 211), (190, 207), (300, 207)], .9, close=False),
           CYAN, glows=((1.5, .55), (5, .3)), alpha=.75)
    for i in range(3):
        A.screen(A.arr(A.mask(A.poly_fill([(146 + i * 10, 204), (152 + i * 10, 204), (152 + i * 10, 208),
                                           (146 + i * 10, 208)]))), CYAN if i == 1 else BLUE, .9)

    # 8. cards ----------------------------------------------------------------------------------
    A.neon(A.poly_line(cardL, 1.1), CYAN, alpha=.9)
    A.neon(A.poly_line(cardR, 1.1), VIOLET, alpha=.95, core=lighten(VIOLET, .6))
    A.neon(A.poly_line([(14, 240), (14, 214), (34, 214)], 2.4, close=False), CYAN, core=ICE)
    A.neon(A.poly_line([(306, 272), (306, 298), (286, 298)], 2.4, close=False), VIOLET, core=ICE)
    # header strips + tick rulers
    for (x0, x1, col) in ((22, 148, CYAN), (172, 298, VIOLET)):
        A.neon(A.poly_line([(x0, 243), (x1, 243)], .6, close=False), col, glows=((1.2, .3),), alpha=.5)
        tk = Image.new("L", (W, H), 0)
        td = ImageDraw.Draw(tk)
        for i, tx in enumerate(np.arange(x0 + 4, x1 - 2, 6)):
            td.line([(tx * S, 289 * S), (tx * S, (292 if i % 4 else 295) * S)], fill=255, width=1)
        A.screen(A.arr(tk), col, .55)
    A.text((140, 235), "01", 8, BLUE, anchor="r", glow=False, alpha=.8)
    A.text((290, 235), "02", 8, (110, 100, 220), anchor="r", glow=False, alpha=.8)

    # icons
    def icon(pts_list, ox, oy, color, w=1.3):
        for pts in pts_list:
            A.neon(A.poly_line([(ox + x, oy + y) for x, y in pts], w), color, core=ICE,
                   glows=((1.2, .8), (4, .5)))
    shoe = [[(1, 13), (1, 8), (5, 7), (7, 3), (9.5, 4), (10.5, 7.5), (15, 8.5), (19, 12), (19, 15), (1, 15)]]
    flame = [[(10, 0.5), (12.5, 4), (15.5, 7.5), (17, 11.5), (16, 15.5), (13, 18.5), (10, 19.5), (7, 18.5),
              (4, 15.5), (3, 11.5), (4.5, 8), (7, 10.5), (7.5, 6), (9, 3)],
             [(10, 17.5), (8.2, 15.2), (10, 11.5), (11.8, 15.2)]]
    icon(shoe, 24, 221, CYAN)
    icon(flame, 175, 220, VIOLET)
    A.text((50, 236), "STEPS", 12, ICE, track=2, core=ICE)
    A.text((200, 236), "KCAL", 12, ICE, track=2, core=ICE)

    # 9. battery row ----------------------------------------------------------------------------
    cx, cy = 115, 323
    A.neon(A.poly_line([(cx + 12 * math.cos(a * math.pi / 3 + math.pi / 6), cy + 12 * math.sin(a * math.pi / 3 + math.pi / 6))
                        for a in range(6)], 1.2), BLUE, alpha=.95, core=lighten(BLUE, .6))
    bolt_pts = [(cx + 1.5, cy - 8), (cx - 4.5, cy + 1), (cx - .5, cy + 1), (cx - 1.5, cy + 8), (cx + 4.5, cy - 1.5), (cx + .5, cy - 1.5)]
    A.neon(A.poly_line(bolt_pts, 1.0), CYAN, core=ICE, glows=((1, .8), (3, .5)))
    for sgn, x_end in ((-1, 22), (1, 298)):
        xs = 94 if sgn < 0 else 226
        A.neon(A.poly_line([(xs, 323), (x_end + (6 if sgn < 0 else -6), 323)], .8, close=False), BLUE,
               glows=((1.5, .4),), alpha=.7)
        tk = Image.new("L", (W, H), 0)
        td = ImageDraw.Draw(tk)
        for i in range(8):
            tx = (x_end + 12 * i) if sgn < 0 else (x_end - 12 * i)
            if (sgn < 0 and tx > 92) or (sgn > 0 and tx < 228):
                continue
            td.rectangle([tx * S, 318 * S, (tx + 2) * S, 328 * S], fill=255)
        A.screen(A.arr(tk), BLUE, .6)

    # 10. scanlines + vignette ------------------------------------------------------------------
    sl = np.ones((H, 1, 1), np.float32)
    sl[::3 * S // 1, :, :] = .93
    A.buf *= sl
    vx = ((xx - SCREEN_W / 2) / (SCREEN_W / 2)) ** 2
    vy = ((ypx - SCREEN_H / 2) / (SCREEN_H / 2)) ** 2
    vig = 1 - .38 * np.clip((vx * .6 + vy * .5), 0, 1.4) ** 1.4
    A.buf *= vig[..., None]

    img = A.buf.reshape(SCREEN_H, S, SCREEN_W, S, 3).mean((1, 3))
    return Image.fromarray(quantize_rgb(np.clip(img, 0, 1)), "RGB")


# ----------------------------------------------------------------------------------------
# Preview (272x324) rendered inside the supplied bezel
# ----------------------------------------------------------------------------------------
def widget_text(glyphs, wd, text):
    """Lay out `text` for a widget; returns [(img, x, y)] in screen space."""
    seq = []
    if wd["type"] == "week":
        seq = [glyphs["week"]["en_" + text]]
    else:
        for ch in text:
            seq.append(glyphs[wd["type"]]["10" if ch == "%" else ch])
    tw = sum(i.width for i in seq)
    x0 = wd["x"] + {"left": 0, "center": (wd["w"] - tw) / 2, "right": wd["w"] - tw}[wd["align"]]
    out, x = [], x0
    for im in seq:
        out.append((im, int(round(x)), wd["y"]))
        x += im.width
    return out


def make_preview(bg, glyphs, border_path):
    face = bg.convert("RGBA")
    for wd in WIDGETS:
        for im, x, y in widget_text(glyphs, wd, SAMPLE[wd["type"]]):
            face.alpha_composite(im, (x, y))
    k = max(PREV_W / SCREEN_W, PREV_H / SCREEN_H)
    sw, sh = round(SCREEN_W * k), round(SCREEN_H * k)
    face = face.convert("RGB").resize((sw, sh), Image.LANCZOS)
    ox, oy = (sw - PREV_W) // 2, (sh - PREV_H) // 2
    face = face.crop((ox, oy, ox + PREV_W, oy + PREV_H))

    border = Image.open(border_path).convert("RGB")
    assert border.size == (PREV_W, PREV_H), "border must be %dx%d" % (PREV_W, PREV_H)
    gray = np.asarray(border.convert("L"))
    allowed = np.asarray(gray < 40, np.uint8) * 255
    cur = Image.new("L", (PREV_W, PREV_H), 0)
    cur.putpixel((PREV_W // 2, PREV_H // 2), 255)         # flood the bezel interior from the centre
    for _ in range(PREV_W + PREV_H):
        nxt = Image.fromarray(np.minimum(np.asarray(cur.filter(ImageFilter.MaxFilter(3))), allowed))
        if np.array_equal(np.asarray(nxt), np.asarray(cur)):
            break
        cur = nxt
    inside = cur.filter(ImageFilter.MaxFilter(5))         # tuck the face 2px under the border ring
    f = np.asarray(face, np.float32) * (np.asarray(inside, np.float32) / 255)[..., None]
    out = np.maximum(f, np.asarray(border, np.float32)) / 255
    q = quantize_rgb(out)
    return Image.fromarray(np.dstack([q, np.full(q.shape[:2], 255, np.uint8)]), "RGBA")


# ----------------------------------------------------------------------------------------
# Source tree writer
# ----------------------------------------------------------------------------------------
def build_manifest():
    items = []
    for wd in WIDGETS:
        it = {"widget": "custom", "type": wd["type"]}
        if wd["addwh"]:
            it["isShowAddwh"] = True
        it.update({"x": wd["x"], "y": wd["y"], "w": wd["w"], "h": wd["h"],
                   "fgcolor": "0xFFFFFFFF", "fgrender": "0xFFFFFFFF", "align": wd["align"]})
        if "style" in wd:
            it["style"] = wd["style"]
        it.update({"font": wd["type"], "fontnum": wd["fontnum"]})
        items.append(it)
    return {"version": 1, "clouddialversion": 3, "preview": "preview.png", "name": NAME, "author": AUTHOR,
            "description": DEVICE, "deviceId": DEVICE, "bluetooth": False, "disturb": False, "battery": False,
            "compress": "LZ4", "environment": "Production", "item": items, "bkground": BACKGROUND_FILE}


def dumps(obj):                      # same text format as the stock face
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def generate(font_path, border_path, out_root):
    src = os.path.join(out_root, NAME)
    os.makedirs(src, exist_ok=True)
    G = Glyphs(font_path)
    glyphs = {}
    for wd in WIDGETS:
        t, st = wd["type"], STYLES[wd["type"]]
        folder = os.path.join(src, t)
        os.makedirs(folder, exist_ok=True)
        glyphs[t] = {}
        if t == "week":
            for d in WEEK_ORDER:
                name = {"thur": "THU"}.get(d, d.upper())
                glyphs[t]["en_" + d] = G.render(name, wd["cell"], wd["ink"], st, ref_char="M", left_pad=3)
        else:
            for i in range(10):
                glyphs[t][str(i)] = G.render(str(i), wd["cell"], wd["ink"], st)
            if t == "battery":
                glyphs[t]["10"] = G.render("%", (26, wd["cell"][1]), wd["ink"], st)
        for k, im in glyphs[t].items():
            im.save(os.path.join(folder, k + ".png"))
        # sanity: ink must stay inside its cell
        for k, im in glyphs[t].items():
            a = np.asarray(im)[..., 3]
            if a[:, 0].max() >= 34 or a[:, -1].max() >= 34 or a[0].max() >= 34 or a[-1].max() >= 34:
                print("  warning: %s/%s touches the cell edge (glow clipped)" % (t, k))

    bg = draw_background(font_path)
    bg.save(os.path.join(src, BACKGROUND_FILE))
    prev = make_preview(bg, glyphs, border_path)
    prev.save(os.path.join(src, "preview.png"))

    manifest = build_manifest()
    with open(os.path.join(src, "iwf.json"), "wb") as f:
        f.write(dumps(manifest))
    fonts = {"item": [{"name": w["type"], "bpp": 16, "format": "png"} for w in WIDGETS]}
    with open(os.path.join(src, "font.json"), "wb") as f:
        f.write(dumps(fonts))
    return src


def make_zip(src, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, src))


# ----------------------------------------------------------------------------------------
# .iwf compiler  (format: see WATCHFACE.md)
# ----------------------------------------------------------------------------------------
def encode_raw(path, alpha):
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    a = np.asarray(im, np.uint16)
    px = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    body = px.astype(">u2").tobytes()                       # RGB565, big-endian
    if not alpha:
        return struct.pack("<4sHHBBBBI", b"RAW\0", w, h, 0x85, 0, 0, 0, 0) + body
    a4 = (a[..., 3].flatten() >> 4).astype(np.uint8)        # 4-bit alpha, first pixel in HIGH nibble
    if len(a4) % 2:
        a4 = np.append(a4, 0)
    packed = ((a4[0::2] << 4) | a4[1::2]).astype(np.uint8).tobytes()
    return struct.pack("<4sHHBBBBI", b"RAW\0", w, h, 0x85, 0x66, 0, 0, len(body)) + body + packed


def compile_iwf(src, out_path):
    read = lambda n: open(os.path.join(src, n), "rb").read()
    manifest = json.loads(read("iwf.json"))
    fonts = json.loads(read("font.json"))
    entries = [("iwf.json", read("iwf.json")), ("font.json", read("font.json")),
               (manifest["preview"], encode_raw(os.path.join(src, manifest["preview"]), False)),
               (manifest["bkground"], encode_raw(os.path.join(src, manifest["bkground"]), False))]

    def key(stem):
        if stem.isdigit():
            return (0, int(stem))
        return (1, WEEK_ORDER.index(stem.split("_", 1)[1]) if stem.split("_", 1)[-1] in WEEK_ORDER else 99)
    for f in fonts["item"]:
        folder = os.path.join(src, f["name"])
        stems = sorted((os.path.splitext(n)[0] for n in os.listdir(folder) if n.endswith(".png")), key=key)
        for s in stems:
            name = "%s_%s" % (f["name"], s)
            assert len(name.encode()) < 32, name
            entries.append((name, encode_raw(os.path.join(folder, s + ".png"), True)))
    off = 8 + 40 * len(entries)
    table, data = b"", b""
    for name, blob in entries:
        table += name.encode().ljust(32, b"\0") + struct.pack("<II", off + len(data), len(blob))
        data += blob
    with open(out_path, "wb") as f:
        f.write(b"iwf\0" + struct.pack("<HH", 1, len(entries)) + table + data)
    return len(entries)


# ----------------------------------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Build the Blue_Cyber watch face (IDW20)")
    ap.add_argument("--font", default=os.path.join(here, "ChickenPunk.ttf"))
    ap.add_argument("--border", default=os.path.join(here, "840.png"))
    ap.add_argument("--out", default=os.path.join(here, "build"))
    ap.add_argument("--compile", action="store_true", help="also write Blue_Cyber.iwf")
    a = ap.parse_args()
    for p in (a.font, a.border):
        if not os.path.exists(p):
            sys.exit("missing input: " + p)
    os.makedirs(a.out, exist_ok=True)
    print("rendering %s ..." % NAME)
    src = generate(a.font, a.border, a.out)
    z = os.path.join(a.out, NAME + ".zip")
    make_zip(src, z)
    print("source tree :", src)
    print("source zip  :", z)
    if a.compile:
        p = os.path.join(a.out, NAME + ".iwf")
        n = compile_iwf(src, p)
        print("compiled    : %s (%d entries, %d bytes)" % (p, n, os.path.getsize(p)))


if __name__ == "__main__":
    main()