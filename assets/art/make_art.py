import io
import pathlib
import zipfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
from skimage import morphology, restoration

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "art" / "sources"
OUT = ROOT
LAYERS = OUT / "layers"
HEAD_PHOTO = SOURCES / "met-257864" / "view-0.jpg"
INTER_ZIP = SOURCES / "inter" / "Inter.zip"

SEED = 2026
MASTER_W, MASTER_H = 2400, 1200
COVER_W, COVER_H = 1200, 600
HEADER_W, HEADER_H = 1600, 560
AVATAR = 512
PITCH = 6
AVATAR_PITCH = 4
FADE_START = 0.8

INDIGO = np.array((0x1a, 0x10, 0xb8), float)
DEEP = np.array((0x0a, 0x0a, 0x9a), float)
LILAC_DOT = np.array((0xa9, 0xa9, 0xcc), float)
PAPER = np.array((0xf4, 0xf4, 0xfd), float)
NAVY = np.array((0x14, 0x14, 0x2a), float)
TEXT_WHITE = (255, 255, 255)
TEXT_GREY = (0x9a, 0x9a, 0xb4)

LIGHT = np.array([-0.48, -0.58, 0.66])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
FINGER_Z = 0.3

FINGERS = {
    "index": {"knuckle": (0.37, 0.96, 0.00), "lengths": (0.46, 0.28, 0.23), "radii": (0.150, 0.130, 0.114, 0.090), "splay": 18},
    "middle": {"knuckle": (0.125, 1.03, 0.02), "lengths": (0.51, 0.31, 0.24), "radii": (0.154, 0.134, 0.116, 0.092), "splay": 3},
    "ring": {"knuckle": (-0.125, 0.99, 0.01), "lengths": (0.47, 0.29, 0.23), "radii": (0.146, 0.126, 0.110, 0.088), "splay": -13},
    "little": {"knuckle": (-0.355, 0.87, -0.01), "lengths": (0.36, 0.23, 0.20), "radii": (0.128, 0.110, 0.096, 0.078), "splay": -30},
}

GESTURES = {
    "open": {"index": 5, "middle": 4, "ring": 6, "little": 7, "thumb": 6, "spread": 1.0},
    "relaxed": {"index": 10, "middle": 16, "ring": 20, "little": 22, "thumb": 12, "spread": 0.78},
    "reach": {"index": 3, "middle": 10, "ring": 16, "little": 20, "thumb": 8, "spread": 0.85},
}

TIER_FLOOR = {0: 0.28, 1: 0.4}

BUST_OUTLINE = [
    (430, 1300), (600, 1300), (650, 1290), (640, 1340), (680, 1390), (770, 1420), (830, 1445),
    (930, 1455), (985, 1440), (1040, 1390), (1090, 1360), (1140, 1300), (1170, 1270), (1290, 1270),
    (1290, 1960), (430, 1960),
]

EYE_AXIS = 837
EYE_DY = 8
EYE_LOW = 0.36
EYE_HIGH = 0.60
EYE_CENTRE = (952, 782)
EYE_INK = {"D": 1.0, "m": 0.62, "l": 0.34, "w": 0.0}
EYE_GLYPH = [
    ".lmmmmmmml.",
    ".wwwwwwwww.",
    "..mDDDDDDm.",
    ".DlwDDDwwlD",
    "...llDlll..",
]
AVATAR_EYE_GLYPH = [
    "..mmmmm..",
    ".wwwwwww.",
    ".mDDDDDm.",
    "DlwDDwwlD",
    "..lllll..",
]

COVER_SCALE = {0: 136, 1: 112}
COVER_POLAR = [
    (1, -42, 300, "open", 3, 0.0),
    (1, -2, 478, "open", -3, 0.42),
    (1, 26, 448, "open", 3, 0.42),
    (0, -16, 325, "open", -5, 0.0),
    (0, 12, 330, "relaxed", 4, 0.0),
    (0, 40, 262, "open", -3, 0.0),
]
AVATAR_SCALE = {0: 68, 1: 50}
AVATAR_POLAR = [
    (1, -48, 172, "open", 2, 0.0),
    (1, -2, 205, "open", -2, 0.3),
    (1, 37, 222, "open", 2, 0.3),
    (1, 76, 174, "open", -2, 0.0),
    (0, -22, 166, "open", -4, 0.0),
    (0, 18, 170, "relaxed", 3, 0.0),
    (0, 56, 172, "open", -2, 0.0),
]


@dataclass
class Limb:
    shoulder: tuple
    elbow: tuple
    wrist: tuple
    scale: float
    thumb_sign: int
    gesture: str
    aim: float
    dissolve: float
    tier: int


def smoothstep(lo, hi, x):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def resize_f(field, size, method=Image.BICUBIC):
    return np.asarray(Image.fromarray(field.astype(np.float32), "F").resize(size, method))


def stamp_cone(buf, a, b, ra, rb, k):
    pad = max(ra, rb) + 2
    h, w = buf.shape
    x0 = int(max(np.floor(min(a[0], b[0]) - pad), 0))
    x1 = int(min(np.ceil(max(a[0], b[0]) + pad), w))
    y0 = int(max(np.floor(min(a[1], b[1]) - pad), 0))
    y1 = int(min(np.ceil(max(a[1], b[1]) + pad), h))
    if x1 <= x0 or y1 <= y0:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    len2 = dx * dx + dy * dy
    t = np.zeros_like(xs) if len2 < 1e-9 else np.clip(((xs - a[0]) * dx + (ys - a[1]) * dy) / len2, 0, 1)
    r = ra + (rb - ra) * t
    d2 = (xs - (a[0] + t * dx)) ** 2 + (ys - (a[1] + t * dy)) ** 2
    inside = d2 < r * r
    if not inside.any():
        return
    z = a[2] + t * dz + np.sqrt(np.maximum(r * r - d2, 0))
    view = buf[y0:y1, x0:x1]
    old = view[inside]
    new = z[inside]
    empty = ~np.isfinite(old)
    mix = np.clip(0.5 + 0.5 * (new - np.where(empty, new, old)) / k, 0, 1)
    view[inside] = np.where(empty, new, old * (1 - mix) + new * mix + k * mix * (1 - mix))


def smooth_merge(a, b, k):
    both = np.isfinite(a) & np.isfinite(b)
    out = np.where(np.isfinite(a), a, b)
    mix = np.clip(0.5 + 0.5 * (b[both] - a[both]) / k, 0, 1)
    out[both] = a[both] * (1 - mix) + b[both] * mix + k * mix * (1 - mix)
    return out


def soften(height, sigma):
    mask = np.isfinite(height)
    filled = np.where(mask, height, 0.0)
    blurred = ndi.gaussian_filter(filled, sigma) / (ndi.gaussian_filter(mask.astype(float), sigma) + 1e-6)
    return np.where(mask, blurred, -np.inf)


def hand_frame(direction, thumb_sign):
    forward = np.array([np.cos(direction), np.sin(direction), 0.0])
    across = thumb_sign * np.array([-forward[1], forward[0], 0.0])
    return across, forward, np.array([0.0, 0.0, 1.0])


def to_world(point, wrist, frame, scale):
    across, forward, out = frame
    return wrist + scale * (point[0] * across + point[1] * forward + point[2] * out)


def finger_height(shape, wrist, frame, scale, spec, curl, spread):
    pos = np.array(spec["knuckle"], dtype=float)
    splay = np.radians(spec["splay"] * spread)
    bend = np.radians(curl)
    radii = spec["radii"]
    buf = np.full(shape, -np.inf)
    for i, length in enumerate(spec["lengths"]):
        c = bend * (i + 1) * 0.9
        step = np.array([np.sin(splay) * np.cos(c), np.cos(splay) * np.cos(c), np.sin(c) * FINGER_Z])
        nxt = pos + step * length
        stamp_cone(buf, to_world(pos, wrist, frame, scale), to_world(nxt, wrist, frame, scale),
                   radii[i] * scale, radii[i + 1] * scale, 0.05 * scale)
        pos = nxt
    return buf


def hand_height(shape, wrist, direction, thumb_sign, scale, gesture):
    frame = hand_frame(direction, thumb_sign)
    pose = GESTURES[gesture]
    k_palm = 0.22 * scale
    palm = np.full(shape, -np.inf)

    def w(p):
        return to_world(np.array(p, dtype=float), wrist, frame, scale)

    metacarpals = [
        ((0.16, 0.00, 0.00), (0.37, 0.96, 0.00), 0.17, 0.175),
        ((0.05, -0.02, 0.00), (0.125, 1.03, 0.02), 0.18, 0.18),
        ((-0.06, -0.02, 0.00), (-0.125, 0.99, 0.01), 0.18, 0.17),
        ((-0.17, 0.02, 0.00), (-0.355, 0.87, -0.01), 0.17, 0.15),
    ]
    for a, b, ra, rb in metacarpals:
        stamp_cone(palm, w(a), w(b), ra * scale, rb * scale, k_palm)
    stamp_cone(palm, w((0.16, 0.12, 0.06)), w((0.42, 0.44, 0.05)), 0.25 * scale, 0.18 * scale, k_palm)
    stamp_cone(palm, w((-0.18, 0.12, 0.05)), w((-0.36, 0.60, 0.03)), 0.21 * scale, 0.16 * scale, k_palm)
    height = soften(palm, 0.05 * scale)
    for name, spec in FINGERS.items():
        finger = finger_height(shape, wrist, frame, scale, spec, pose[name], pose["spread"])
        height = smooth_merge(height, finger, 0.05 * scale)
    thumb = np.full(shape, -np.inf)
    theta = np.radians(58 * pose["spread"] ** 0.5)
    pos = np.array((0.36, 0.20, 0.07))
    segments = [(0.40, 0.20, 0.17), (0.31, 0.17, 0.145), (0.25, 0.145, 0.11)]
    for i, (length, ra, rb) in enumerate(segments):
        c = np.radians(pose["thumb"]) * (i + 1) * 0.8
        turn = theta - np.radians(11) * i
        step = np.array([np.sin(turn) * np.cos(c), np.cos(turn) * np.cos(c), np.sin(c) * FINGER_Z])
        nxt = pos + step * length
        stamp_cone(thumb, w(pos), w(nxt), ra * scale, rb * scale, 0.05 * scale)
        pos = nxt
    return smooth_merge(height, thumb, 0.07 * scale)


def arm_centerline(wrist, elbow, shoulder, n=70):
    fore = np.linalg.norm(elbow - wrist)
    upper = np.linalg.norm(shoulder - elbow)
    cut = 0.28 * min(fore, upper)
    e0 = elbow + (wrist - elbow) * cut / fore
    e1 = elbow + (shoulder - elbow) * cut / upper
    a = np.linspace(0, 1, n)[:, None]
    t = np.linspace(0, 1, 24)[:, None]
    turn = (1 - t) ** 2 * e0 + 2 * (1 - t) * t * elbow + t ** 2 * e1
    pts = np.vstack([wrist + (e0 - wrist) * a, turn[1:], e1 + (shoulder - e1) * a[1:]])
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts[:, :2], axis=0), axis=1))]
    return pts, d / d[-1], fore / (fore + upper)


def arm_height(shape, shoulder, elbow, wrist, scale):
    pts, u, elbow_u = arm_centerline(wrist, elbow, shoulder)
    knots_u = [0.0, 0.10, 0.30, 0.62, 0.86, 1.0, 1.14, 1.42, 1.72, 2.0]
    knots_r = [0.31, 0.32, 0.38, 0.45, 0.42, 0.40, 0.42, 0.50, 0.52, 0.56]
    v = np.where(u < elbow_u, u / elbow_u, 1 + (u - elbow_u) / (1 - elbow_u))
    radii = ndi.gaussian_filter1d(np.interp(v, knots_u, knots_r) * scale, 5, mode="nearest")
    buf = np.full(shape, -np.inf)
    for i in range(len(pts) - 1):
        stamp_cone(buf, pts[i], pts[i + 1], radii[i], radii[i + 1], 0.06 * scale)
    return soften(buf, 0.04 * scale)


def shade(height, scale, ambient=0.18, diffuse=1.0):
    mask = np.isfinite(height)
    sigma = 0.02 * scale
    filled = np.where(mask, height, 0.0)
    m = mask.astype(float)
    smooth = ndi.gaussian_filter(filled, sigma) / (ndi.gaussian_filter(m, sigma) + 1e-6)
    gy, gx = np.gradient(smooth)
    nz = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
    lit = np.clip(-gx * nz * LIGHT[0] - gy * nz * LIGHT[1] + nz * LIGHT[2], 0, 1)
    wide = ndi.gaussian_filter(filled, 0.10 * scale) / (ndi.gaussian_filter(m, 0.10 * scale) + 1e-6)
    occl = np.clip((wide - smooth) / (0.10 * scale), 0, 1)
    light = (ambient + diffuse * lit) * (1 - 0.8 * occl)
    rim = np.exp(-ndi.distance_transform_edt(mask) / (0.055 * scale))
    tone = np.maximum(np.clip(1 - light, 0, 1), rim * 0.97)
    alpha = np.clip((ndi.gaussian_filter(m, 0.8) - 0.5) * 2.4 + 0.5, 0, 1)
    return np.where(mask, tone, ndi.grey_dilation(np.where(mask, tone, 0), size=3)), alpha


def limb_bbox(limb, shape):
    pts = np.array([limb.shoulder[:2], limb.elbow[:2], limb.wrist[:2]], float)
    fore = pts[2] - pts[1]
    tip = pts[2] + fore / np.linalg.norm(fore) * 2.3 * limb.scale
    pts = np.vstack([pts, tip])
    pad = 1.6 * limb.scale
    x0, y0 = np.floor(pts.min(0) - pad).astype(int)
    x1, y1 = np.ceil(pts.max(0) + pad).astype(int)
    return max(x0, 0), max(y0, 0), min(x1, shape[1]), min(y1, shape[0])


def render_limb(shape, limb):
    x0, y0, x1, y1 = limb_bbox(limb, shape)
    local = (y1 - y0, x1 - x0)
    origin = np.array((x0, y0, 0.0))
    wrist = np.array(limb.wrist, float) - origin
    elbow = np.array(limb.elbow, float) - origin
    shoulder = np.array(limb.shoulder, float) - origin
    fore = wrist[:2] - elbow[:2]
    direction = np.arctan2(fore[1], fore[0]) + np.radians(limb.aim)
    arm = arm_height(local, shoulder, elbow, wrist, limb.scale)
    hand = hand_height(local, wrist, direction, limb.thumb_sign, limb.scale, limb.gesture)
    tone, alpha = shade(smooth_merge(arm, hand, 0.16 * limb.scale), limb.scale)
    ys, xs = np.mgrid[0:local[0], 0:local[1]].astype(float)
    along = (xs - elbow[0]) * np.cos(direction) + (ys - elbow[1]) * np.sin(direction)
    reach = np.linalg.norm(fore) + 2.1 * limb.scale
    full_tone, full_alpha, full_dis = np.zeros(shape), np.zeros(shape), np.zeros(shape)
    full_tone[y0:y1, x0:x1] = tone
    full_alpha[y0:y1, x0:x1] = alpha
    full_dis[y0:y1, x0:x1] = smoothstep(0.55, 1.0, along / reach) * limb.dissolve
    return full_tone, full_alpha, full_dis


def load_head():
    rgb = np.asarray(Image.open(HEAD_PHOTO).convert("RGB")).astype(float) / 255
    warm = ndi.gaussian_filter(rgb[..., 0] - rgb[..., 2], 1.5) > 0.035
    warm = morphology.opening(warm, morphology.disk(3))
    labels, count = ndi.label(warm)
    if count == 0:
        raise RuntimeError("no marble found in head photo")
    sizes = ndi.sum(warm, labels, range(1, count + 1))
    mask = ndi.binary_fill_holes(labels == 1 + int(np.argmax(sizes)))
    cut = Image.new("L", (rgb.shape[1], rgb.shape[0]), 255)
    ImageDraw.Draw(cut).polygon(
        [(320, 560), (440, 560), (462, 640), (470, 760), (420, 960), (320, 960)], fill=0
    )
    mask = morphology.closing(mask & (np.asarray(cut) > 0), morphology.disk(9))
    alpha = np.clip((ndi.gaussian_filter(mask.astype(float), 2.0) - 0.5) * 3 + 0.5, 0, 1)

    lum = 0.62 * rgb[..., 0] + 0.33 * rgb[..., 1] + 0.05 * rgb[..., 2]
    lum = restoration.denoise_tv_chambolle(mirror_eye(lum), weight=0.04)
    fill = lum.copy()
    fill[~mask] = np.median(lum[mask])
    field = ndi.gaussian_filter(fill, 170)
    flat = fill / field ** 0.75 * np.median(field) ** 0.75
    ys, xs = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]].astype(float)
    face = np.exp(-(((xs - 842) / 270) ** 2 + ((ys - 860) / 300) ** 2) ** 2)
    sharp = flat + (0.55 + 0.25 * (1 - face)) * (flat - ndi.gaussian_filter(flat, 26))
    high = 0.80 + 0.18 * (1 - face)
    t = np.clip((sharp - 0.22) / (high - 0.22), 0, 1)
    ink = 1 - t * t * (3 - 2 * t)
    bust = Image.new("L", (rgb.shape[1], rgb.shape[0]), 0)
    ImageDraw.Draw(bust).polygon(BUST_OUTLINE, fill=255)
    shadow = ndi.gaussian_filter(np.asarray(bust, dtype=float) / 255, 8) * (0.86 + 0.12 * smoothstep(1300, 1600, ys))
    return np.clip(np.maximum(eye_tone(ink, flat, xs, ys), shadow), 0, 1), alpha


def mirror_eye(lum):
    x0, x1, y0, y1 = 880, 1034, 722, 842
    patch = lum[y0:y1, x0:x1][:, ::-1]
    tx0, tx1 = 2 * EYE_AXIS - x1, 2 * EYE_AXIS - x0
    h, w = patch.shape
    ys, xs = np.mgrid[0:h, 0:w]
    feather = smoothstep(0, 1, np.minimum.reduce([xs, w - 1 - xs, ys, h - 1 - ys]) / 26)
    ty0, ty1 = y0 + EYE_DY, y1 + EYE_DY
    target = lum[ty0:ty1, tx0:tx1]
    out = lum.copy()
    out[ty0:ty1, tx0:tx1] = target * (1 - feather) + (patch + np.median(target) - np.median(patch)) * feather
    return out


def eye_tone(ink, flat, xs, ys):
    soft = ndi.gaussian_filter(flat, 2.5)
    zone = np.exp(-(((xs - 955) / 98) ** 2 + ((ys - 774) / 56) ** 2) ** 2)
    out = ink * (1 - zone) + np.minimum(1 - smoothstep(EYE_LOW, EYE_HIGH, soft), 0.5) * zone
    x0, x1, y0, y1 = 850, 1062, 700, 852
    patch = out[y0:y1, x0:x1][:, ::-1]
    weight = zone[y0:y1, x0:x1][:, ::-1]
    tx0, tx1 = 2 * EYE_AXIS - x1, 2 * EYE_AXIS - x0
    target = out[y0 + EYE_DY:y1 + EYE_DY, tx0:tx1]
    out[y0 + EYE_DY:y1 + EYE_DY, tx0:tx1] = target * (1 - weight) + patch * weight
    return out


def stamp_eyes(cells, p, cx, top, scale, glyph=EYE_GLYPH):
    h, w = len(glyph), len(glyph[0])
    y = top + (EYE_CENTRE[1] - 262) * scale
    xr = cx + (EYE_CENTRE[0] - 846) * scale
    xl = cx + (2 * EYE_AXIS - EYE_CENTRE[0] - 846) * scale
    for x, centre_y, flip in ((xr, y, False), (xl, y + EYE_DY * scale, True)):
        c0 = int(round(x / p - w / 2))
        r0 = int(round(centre_y / p - h / 2))
        for r, row in enumerate(glyph):
            text = row[::-1] if flip else row
            for c, ch in enumerate(text):
                if ch != "." and 0 <= r0 + r < cells.shape[0] and 0 <= c0 + c < cells.shape[1]:
                    cells[r0 + r, c0 + c] = EYE_INK[ch]
    return cells


def place_head(ink, alpha, shape, cx, top, scale):
    src_h, src_w = ink.shape
    size = (int(round(src_w * scale)), int(round(src_h * scale)))
    ink_s = resize_f(ink, size, Image.LANCZOS)
    alpha_s = resize_f(alpha, size, Image.LANCZOS)
    x0 = int(round(cx - 846 * scale))
    y0 = int(round(top - 262 * scale))
    out_i, out_a = np.zeros(shape), np.zeros(shape)
    sx0, sy0 = max(-x0, 0), max(-y0, 0)
    dx0, dy0 = max(x0, 0), max(y0, 0)
    w = min(size[0] - sx0, shape[1] - dx0)
    h = min(size[1] - sy0, shape[0] - dy0)
    out_i[dy0:dy0 + h, dx0:dx0 + w] = ink_s[sy0:sy0 + h, sx0:sx0 + w]
    out_a[dy0:dy0 + h, dx0:dx0 + w] = alpha_s[sy0:sy0 + h, sx0:sx0 + w]
    return np.clip(out_i, 0, 1), np.clip(out_a, 0, 1)


def crack_lines(draw, rng, x, y, inward, heading, steps, step_len, width, depth):
    points = [(x, y)]
    for i in range(steps):
        heading += rng.normal(0, 0.28)
        if rng.random() < 0.3:
            heading += rng.choice((-1, 1)) * rng.uniform(0.5, 0.9)
        offset = (heading - inward + np.pi) % (2 * np.pi) - np.pi
        heading = inward + np.clip(offset, -1.15, 1.15)
        length = step_len * rng.uniform(0.6, 1.4)
        x += np.cos(heading) * length
        y += np.sin(heading) * length
        points.append((x, y))
        if depth > 0 and i > 0 and rng.random() < 0.42:
            turn = rng.choice((-1, 1)) * rng.uniform(0.5, 1.0)
            crack_lines(draw, rng, x, y, inward, heading + turn, max(steps // 2, 3), step_len * 0.85, width, depth - 1)
    draw.line(points, fill=255, width=width)


def mottle(shape, rng, sigma, octaves=3):
    out = np.zeros(shape)
    amp = 1.0
    for _ in range(octaves):
        layer = ndi.gaussian_filter(rng.standard_normal(shape), sigma, mode="wrap")
        out += amp * layer / layer.std()
        amp *= 0.6
        sigma /= 2
    return out / out.std()


def ground_layer(shape, seed_offset=0):
    rng = np.random.default_rng(SEED + seed_offset)
    h, w = shape
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)
    step = w / 90
    for side in (0, 1):
        x = 0.0 if side == 0 else float(w)
        inward = 0.0 if side == 0 else np.pi
        for row in range(8):
            y = h * (0.02 + row * 0.105 + rng.uniform(-0.03, 0.03))
            crack_lines(draw, rng, x, y, inward, inward + rng.normal(0, 0.5), int(rng.integers(6, 11)), step, 10, 3)
    lines = ndi.gaussian_filter(np.asarray(img, dtype=float) / 255, 1.0)
    small = (h // 4, w // 4)
    cloud = smoothstep(-0.1, 1.5, mottle(small, rng, 14)) * 0.36
    cloud = resize_f(cloud, (w, h), Image.BICUBIC)
    ys, xs = np.mgrid[0:h, 0:w].astype(float)
    nx = np.abs(xs - w / 2) / (w / 2)
    return np.clip(np.maximum(lines * 0.9 * smoothstep(0.66, 0.9, nx), cloud * smoothstep(0.76, 0.99, nx)), 0, 1)


def compose(shape, limbs, place, ground, cell, ramp=True):
    arms_ink = np.zeros(shape)
    arms_alpha = np.zeros(shape)
    dissolve = np.zeros(shape)
    cx, top, scale = place
    head_ink, head_alpha = place_head(*load_head(), shape, cx, top, scale)
    coarse = head_alpha[::4, ::4] < 0.5
    dist = resize_f(ndi.distance_transform_edt(coarse) * 4, (shape[1], shape[0]), Image.BILINEAR)
    near = np.exp(-dist / (105 * scale))
    for limb in sorted(limbs, key=lambda item: -item.tier):
        tone, alpha, dis = render_limb(shape, limb)
        floor = TIER_FLOOR[limb.tier]
        tone = np.clip(floor + (1 - floor) * tone + 0.65 * near, 0, 1)
        halo = np.clip(ndi.maximum_filter(alpha, size=2 * cell + 1) - alpha, 0, 1)
        arms_ink = arms_ink * (1 - halo)
        arms_ink = arms_ink * (1 - alpha) + tone * alpha
        arms_alpha = np.maximum(arms_alpha, alpha)
        dissolve = dissolve * (1 - alpha) + dis * alpha
    rows = np.arange(shape[0])[:, None] / shape[0]
    aura = np.exp(-dist / (70 * scale)) * 0.9 * (1 - smoothstep(0.7, 0.86, rows))
    full_ground = np.maximum(ground, aura)
    if ramp:
        rise = np.clip((rows - 0.7) / 0.3, 0, 1) ** 1.5 * 0.95
        full_ground = np.maximum(full_ground, rise * np.ones(shape))
        head_ink = np.maximum(head_ink, rise * 0.9 * np.ones(shape))
    return {
        "ground": np.clip(full_ground, 0, 1),
        "arms_ink": arms_ink,
        "arms_alpha": arms_alpha,
        "dissolve": dissolve,
        "head_ink": head_ink,
        "head_alpha": head_alpha,
    }


def to_cells(field, p):
    edge_r = np.arange(0, field.shape[0], p)
    edge_c = np.arange(0, field.shape[1], p)
    total = np.add.reduceat(np.add.reduceat(field, edge_r, axis=0), edge_c, axis=1)
    counts = np.add.reduceat(np.add.reduceat(np.ones_like(field), edge_r, axis=0), edge_c, axis=1)
    return total / counts


def radius_lut():
    grid = (np.arange(64) + 0.5) / 64 - 0.5
    gx, gy = np.meshgrid(grid, grid)
    dist = np.hypot(gx, gy)
    radii = np.linspace(0, 0.72, 240)
    return np.array([(dist <= r).mean() for r in radii]), radii


COVER_LUT = radius_lut()


def tone_curve(cells, floor=0.09, punch=0.6):
    t = np.clip(cells, 0, 1)
    t = t * (1 - punch) + smoothstep(0.08, 0.82, t) * punch
    return floor + (1 - floor) * t


def dot_fields(toned, p, width, height):
    rel = np.interp(toned, COVER_LUT[0], COVER_LUT[1]) * p
    ys, xs = np.mgrid[0:height, 0:width].astype(float)
    cy = (ys // p).astype(int)
    cx = (xs // p).astype(int)
    best = np.full((height, width), -1e9)
    owner = np.zeros((height, width))
    rows, cols = toned.shape
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            ny = np.clip(cy + oy, 0, rows - 1)
            nx = np.clip(cx + ox, 0, cols - 1)
            signed = rel[ny, nx] - np.hypot(xs + 0.5 - (nx + 0.5) * p, ys + 0.5 - (ny + 0.5) * p)
            better = signed > best
            best = np.where(better, signed, best)
            owner = np.where(better, toned[ny, nx], owner)
    return best, owner


def paint(cells, p, width, height):
    best, owner = dot_fields(tone_curve(cells), p, width, height)
    cover = np.clip(best + 0.5, 0, 1)[..., None]
    w = smoothstep(0.17, 0.36, owner)[..., None]
    d = smoothstep(0.8, 1.0, owner)[..., None]
    ink = (LILAC_DOT * (1 - w) + INDIGO * w) * (1 - d) + DEEP * d
    return PAPER[None, None] * (1 - cover) + ink * cover, cover[..., 0]


def fold_cells(layers, p, seed, width, height):
    rng = np.random.default_rng(seed)
    fields = {k: to_cells(resize_f(v, (width, height), Image.BOX), p) for k, v in layers.items()}
    keep = rng.random(fields["ground"].shape) >= fields["dissolve"]
    dust_zone = ndi.gaussian_filter(fields["dissolve"] * fields["arms_alpha"], 2.0)
    dust = (rng.random(keep.shape) < dust_zone * 0.75) & (fields["arms_alpha"] < 0.3)
    dust_tone = rng.uniform(0.3, 0.75, keep.shape)
    alpha = fields["arms_alpha"]
    mixed = np.where(keep, fields["ground"] * (1 - alpha) + fields["arms_ink"], fields["ground"])
    mixed = np.where(dust, np.maximum(mixed, dust_tone), mixed)
    final = mixed * (1 - fields["head_alpha"]) + fields["head_ink"] * fields["head_alpha"]
    return {
        "final": final,
        "keep": keep,
        "dust": dust,
        "dust_tone": dust_tone,
        "arms": np.clip(fields["arms_ink"] / np.maximum(alpha, 1e-3), 0, 1) * (alpha > 0.02),
        "head": fields["head_ink"],
    }


def apply_fade(rgb, start=FADE_START):
    h = rgb.shape[0]
    t = smoothstep(start, 1.0, np.arange(h)[:, None] / (h - 1))[..., None]
    return rgb * (1 - t) + NAVY[None, None] * t


def save_rgb(rgb, path):
    Image.fromarray(np.clip(np.round(rgb), 0, 255).astype(np.uint8), "RGB").save(path, optimize=True)


def save_rgba(rgb, alpha, path):
    stacked = np.dstack([np.clip(np.round(rgb), 0, 255), np.clip(np.round(alpha * 255), 0, 255)])
    Image.fromarray(stacked.astype(np.uint8), "RGBA").save(path, optimize=True)


def grey_rgba(ink, alpha, path):
    value = (1 - np.clip(ink, 0, 1)) * 255
    save_rgba(np.dstack([value, value, value]), alpha, path)


def polar_limb(side, tier, angle, radius, gesture, aim, dissolve, centre, jitter, unit, scales):
    a = np.radians(angle)
    bend = np.radians(angle - 11)
    wrist = centre + np.array((side * np.cos(a), -np.sin(a))) * radius * unit + jitter
    elbow = centre + np.array((side * np.cos(bend), -np.sin(bend))) * min(0.62 * radius, 240) * unit + jitter * 0.5
    shoulder = centre + np.array((side * 90, 170)) * unit
    return Limb(tuple(shoulder) + (0,), tuple(elbow) + (0,), tuple(wrist) + (0,),
                scales[tier], -side, gesture, -side * aim, dissolve, tier)


def build_limbs(polar, scales, centre, unit, seed):
    rng = np.random.default_rng(seed)
    swap_gesture = {"relaxed": "open"}
    limbs = []
    for tier, angle, radius, gesture, aim, dissolve in polar:
        limbs.append(polar_limb(-1, tier, angle, radius, gesture, aim, dissolve, centre, np.zeros(2), unit, scales))
        jitter = rng.uniform(-7, 7, 2) * unit
        tweak = rng.uniform(-3, 3)
        other = swap_gesture.get(gesture, "relaxed" if tier == 1 and angle > 0 else gesture)
        limbs.append(polar_limb(1, tier, angle + tweak, radius, other, aim, dissolve, centre, jitter, unit, scales))
    return limbs


def build_cover(layers, place):
    cx, top, scale = place
    folded = fold_cells(layers, PITCH, SEED + 1, COVER_W, COVER_H)
    cells = stamp_eyes(folded["final"], PITCH, cx / 2, top / 2, scale / 2)
    return apply_fade(paint(cells, PITCH, COVER_W, COVER_H)[0])


def build_layers(layers, place):
    cx, top, scale = place
    p = PITCH * 2
    folded = fold_cells(layers, p, SEED + 1, MASTER_W, MASTER_H)
    head_cells = stamp_eyes(folded["head"].copy(), p, cx, top, scale)
    head_rgb, _ = paint(head_cells, p, MASTER_W, MASTER_H)
    save_rgba(head_rgb, layers["head_alpha"], LAYERS / "head.png")

    ys, xs = np.mgrid[0:MASTER_H, 0:MASTER_W]
    cy, cx_cells = ys // p, xs // p
    arm_cells = np.where(folded["dust"], folded["dust_tone"], folded["arms"])
    arm_rgb, arm_cover = paint(arm_cells, p, MASTER_W, MASTER_H)
    keep = folded["keep"][cy, cx_cells]
    dust = folded["dust"][cy, cx_cells]
    save_rgba(arm_rgb, np.maximum(layers["arms_alpha"] * keep, arm_cover * dust), LAYERS / "hands.png")

    arms_grey = np.clip(layers["arms_ink"] / np.maximum(layers["arms_alpha"], 1e-3), 0, 1)
    grey_rgba(layers["head_ink"], layers["head_alpha"], LAYERS / "head-grey.png")
    grey_rgba(arms_grey, layers["arms_alpha"], LAYERS / "hands-grey.png")
    grey_rgba(layers["ground"], np.ones_like(layers["ground"]), LAYERS / "ground-grey.png")

    under = layers["ground"] * (1 - layers["arms_alpha"]) + layers["arms_ink"]
    composite = under * (1 - layers["head_alpha"]) + layers["head_ink"] * layers["head_alpha"]
    Image.fromarray(np.round((1 - np.clip(composite, 0, 1)) * 255).astype(np.uint8), "L").save(
        LAYERS / "composite-grey.png", optimize=True
    )


def inter_font(name, size):
    with zipfile.ZipFile(INTER_ZIP) as archive:
        data = archive.read(f"extras/ttf/{name}.ttf")
    return ImageFont.truetype(io.BytesIO(data), size)


def build_header(layers, place):
    art_h = HEADER_H
    art_w = art_h * 2
    bleed = 110
    gain = art_h / MASTER_H
    folded = fold_cells(layers, PITCH, SEED + 1, art_w, art_h)
    cx, top, scale = place
    cells = stamp_eyes(folded["final"], PITCH, cx * gain, top * gain, scale * gain)
    art = apply_fade(paint(cells, PITCH, art_w, art_h)[0])
    canvas = np.tile(NAVY, (HEADER_H, HEADER_W, 1))
    x0 = HEADER_W - art_w + bleed
    xs = np.arange(HEADER_W)[None, :, None]
    mask = smoothstep(x0 + 110, x0 + 330, xs)
    canvas[:, x0:] = art[:, :art_w - bleed]
    canvas = canvas * mask + NAVY[None, None] * (1 - mask)
    img = Image.fromarray(np.clip(np.round(canvas), 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    word = inter_font("Inter-Bold", 66)
    tag = inter_font("Inter-Regular", 28)
    draw.text((64, 268), "hermes-superagent", font=word, fill=TEXT_WHITE, anchor="ls")
    draw.text((66, 320), "one voice, a hundred hands", font=tag, fill=TEXT_GREY, anchor="ls")
    img.save(OUT / "readme-header.png", optimize=True)


def build_avatar():
    shape = (AVATAR * 2, AVATAR * 2)
    place = (float(AVATAR), 189.0, 0.52)
    centre = np.array((float(AVATAR), 500.0))
    limbs = build_limbs(AVATAR_POLAR, AVATAR_SCALE, centre, 2.0, SEED + 2)
    ground = np.zeros(shape)
    layers = compose(shape, limbs, place, ground, AVATAR_PITCH * 2, ramp=False)
    folded = fold_cells(layers, AVATAR_PITCH, SEED + 3, AVATAR, AVATAR)
    cx, top, scale = place
    cells = stamp_eyes(folded["final"], AVATAR_PITCH, cx / 2, top / 2, scale / 2, AVATAR_EYE_GLYPH)
    rows = np.arange(cells.shape[0])[:, None] / cells.shape[0]
    cells = cells * (1 - smoothstep(0.76, 0.95, rows))
    toned = tone_curve(cells, floor=0.0, punch=0.75)
    toned = np.where(toned < 0.12, 0, toned)
    best, _ = dot_fields(toned, AVATAR_PITCH, AVATAR, AVATAR)
    bitmap = (best <= 0).astype(np.uint8) * 255
    Image.fromarray(bitmap, "L").convert("1", dither=Image.NONE).save(OUT / "avatar.png", optimize=True)


def main():
    OUT.mkdir(exist_ok=True)
    LAYERS.mkdir(exist_ok=True)
    shape = (MASTER_H, MASTER_W)
    place = (MASTER_W / 2, 12.0, 0.94)
    centre = np.array((MASTER_W / 2, 540.0))
    limbs = build_limbs(COVER_POLAR, COVER_SCALE, centre, 2.0, SEED)
    layers = compose(shape, limbs, place, ground_layer(shape), PITCH * 2)
    save_rgb(build_cover(layers, place), OUT / "cover.png")
    build_header(layers, place)
    build_layers(layers, place)
    build_avatar()


if __name__ == "__main__":
    main()
