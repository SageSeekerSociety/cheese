"""Generate every 知是 brand asset: the mark, the 知是 / cheese wordmarks, the
lockups, the web app icons, the tile the desktop app opens on and the parts
the docs site animates.

Everything comes from one hand-drawn source (mouse.svg, the mouse from the
original logo) and two open fonts, plus the measured edits docs/brand.md
explains. Rerun after changing a constant or the source drawing:

    uv run --with fonttools --with skia-pathops --with uharfbuzz \
        --with py7zr --with pillow python scripts/brand/build_brand.py

Fonts are fetched once from pinned releases into ~/.cache/cheese-brand-fonts
and checked against their hashes, so the same constants always draw the same
outlines. The two login-scene textures that Paper Shaders preprocesses are
not made here; brand_scene_textures.html makes them from logo.svg.
"""

import hashlib
import io
import math
import os
import re
import urllib.request

import pathops
import uharfbuzz as hb
from fontTools import subset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps
from fontTools.varLib.instancer import OverlapMode, instantiateVariableFont
from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
ASSETS = os.path.join(ROOT, 'frontend', 'src', 'assets')
PUBLIC = os.path.join(ROOT, 'frontend', 'public')
CACHE = os.path.expanduser('~/.cache/cheese-brand-fonts')

FONTS = {
    # Resource Han Rounded is not in google/fonts; its own release is pinned
    'ResourceHanRoundedCN-Bold': dict(
        url='https://github.com/CyanoHao/Resource-Han-Rounded/releases/download/v0.990/RHR-CN-0.990.7z',
        archive_sha256='e7005f7b4a7a0b8352d32c4a1358ff47564eb73be7fdb2db00d9f792755e9dc7',
        member='ResourceHanRoundedCN-Bold.ttf',
        sha256='f713907a21a10701cd68a7ce3e345ccdce46c789e1809d65ace54e095d7107c3'),
    'Nunito': dict(
        url='https://github.com/google/fonts/raw/23e54b51ddffbc7713c583748e3bd86f62b1fa4a/ofl/nunito/Nunito%5Bwght%5D.ttf',
        sha256='bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207'),
}

BRAND = '#FFA20F'                                # the mark's one colour
TILE = '#FFFFFF'                                 # app icon ground: white, showing through the mouse and holes
EDGE = '#E5E3DF'                                 # hairline round a tile that sits on a page or a dock
INK = {'light': '#191A1C', 'dark': '#F3F4F6'}    # 与 --ink 两套主题一致

# 图形标：三个孔从鼻尖前面起，一个比一个大，间距也逐级放大，圆心都在一条弧上
HOLE_RADII = (34, 56, 92)
HOLE_GAPS = (54, 64)
HOLE_ARC = (560, 420, 200)                       # 弧的圆心和半径，1000 单位的画布
HOLE_START = 66                                  # 第一个孔在弧上的角度（度，y 向下）

# 中文字标，底字思源柔黑粗体
KOU_SCALE = 1.10       # 「知」的圆口：外径相对原来「口」的宽度
CHAR_GAP = 145         # 两字墨色之间的空隙
SQUASH = 0.90          # 整体压扁到九成高；圆口单独按正圆重建
RI_WIDTH = 0.86        # 「是」的「日」：宽度收到八成六
RI_RADIUS = 0.30       # 「日」的圆角，占框高的比例
LOW_STROKE = 108       # 「疋」重画的撇和捺，与它的横同粗
# 撇和捺的画法，坐标是「知是」轮廓压扁前的单位（y 向下）；换字体或改字距后要重画
LOW_CUT = ((1000, 536, 1444, 940), (1444, 780, 2000, 940))      # 去掉字体原来的撇、捺和底横
LOW_STEM = (1445, 700, 1567, 850)                               # 竖延长到新的底横里
LOW_BOTTOM = 850                                                # 底横的中线
LOW_PIE = [('M', (1262, 592)), ('Q', (1236, 770), (1092, 858))]
LOW_NA = [('M', (1232, 716)), ('C', (1290, 812), (1380, LOW_BOTTOM), (1520, LOW_BOTTOM))]
# 英文字标
EN_WEIGHT = 781        # 中英上下排时，英文笔画约为中文的八成
EN_TRACKING = -10      # 在字体自带字偶间距之上再收紧，单位是 1/1000 em


# ---- fonts -------------------------------------------------------------------

def fetch(name):
    spec = FONTS[name]
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, name + '.ttf')
    if not os.path.exists(local):
        data = urllib.request.urlopen(spec['url']).read()
        if 'archive_sha256' in spec:
            if hashlib.sha256(data).hexdigest() != spec['archive_sha256']:
                raise SystemExit(f"{spec['url']}: archive hash mismatch")
            import tempfile

            import py7zr
            with tempfile.TemporaryDirectory() as tmp, py7zr.SevenZipFile(io.BytesIO(data)) as z:
                z.extract(path=tmp, targets=[spec['member']])
                data = open(os.path.join(tmp, spec['member']), 'rb').read()
        open(local, 'wb').write(data)
    data = open(local, 'rb').read()
    if hashlib.sha256(data).hexdigest() != spec['sha256']:
        raise SystemExit(f'{local}: hash mismatch; delete it and rerun')
    return local


def font(name, text, wght=None):
    f = TTFont(fetch(name))
    sub = subset.Subsetter(subset.Options(layout_features=['kern'], notdef_outline=True))
    sub.populate(text=text)
    sub.subset(f)
    if wght is not None:
        return instantiateVariableFont(f, {'wght': wght}, overlap=OverlapMode.REMOVE)
    removeOverlaps(f)
    return f


def split(rec):
    cs, cur = [], []
    for op in rec:
        cur.append(op)
        if op[0] in ('closePath', 'endPath'):
            cs.append(cur)
            cur = []
    return cs


def glyph(f, name):
    p = RecordingPen()
    f.getGlyphSet()[name].draw(p)
    return split(p.value)


def rbounds(cs):
    b = BoundsPen(None)
    r = RecordingPen()
    r.value = [op for c in cs for op in c]
    r.replay(b)
    return b.bounds


def mapped(cs, fn):
    return [[(op, tuple(fn(x, y) for x, y in args)) for op, args in c] for c in cs]


K = 0.5522847498


def rec_circle(cx, cy, r):
    pts = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    rec = [('moveTo', (pts[0],))]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        ta = (-(a[1] - cy) * K, (a[0] - cx) * K)
        tb = ((b[1] - cy) * K, -(b[0] - cx) * K)
        rec.append(('curveTo', ((a[0] + ta[0], a[1] + ta[1]), (b[0] + tb[0], b[1] + tb[1]), b)))
    rec.append(('closePath', ()))
    return rec


# ---- geometry (skia-pathops, y down) -----------------------------------------

U = lambda a, b: pathops.op(a, b, pathops.PathOp.UNION)
D = lambda a, b: pathops.op(a, b, pathops.PathOp.DIFFERENCE)
I = lambda a, b: pathops.op(a, b, pathops.PathOp.INTERSECTION)


def union(*ps):
    out = pathops.Path()
    for p in ps:
        out = U(out, p)
    return out


def circle(cx, cy, r):
    p = pathops.Path()
    k = K * r
    p.moveTo(cx + r, cy)
    p.cubicTo(cx + r, cy + k, cx + k, cy + r, cx, cy + r)
    p.cubicTo(cx - k, cy + r, cx - r, cy + k, cx - r, cy)
    p.cubicTo(cx - r, cy - k, cx - k, cy - r, cx, cy - r)
    p.cubicTo(cx + k, cy - r, cx + r, cy - k, cx + r, cy)
    p.close()
    return p


def rect(x0, y0, x1, y1):
    p = pathops.Path()
    p.moveTo(x0, y0)
    p.lineTo(x1, y0)
    p.lineTo(x1, y1)
    p.lineTo(x0, y1)
    p.close()
    return p


def rrect(x0, y0, x1, y1, r):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    if r <= 0.5:
        return rect(x0, y0, x1, y1)
    return union(rect(x0 + r, y0, x1 - r, y1), rect(x0, y0 + r, x1, y1 - r),
                 circle(x0 + r, y0 + r, r), circle(x1 - r, y0 + r, r),
                 circle(x0 + r, y1 - r, r), circle(x1 - r, y1 - r, r))


def stroke(cmds, width):
    """An open path stroked at one width with round ends."""
    p = pathops.Path()
    for c, *pts in cmds:
        if c == 'M':
            p.moveTo(*pts[0])
        elif c == 'L':
            p.lineTo(*pts[0])
        elif c == 'Q':
            p.quadTo(*pts[0], *pts[1])
        else:
            p.cubicTo(*pts[0], *pts[1], *pts[2])
    p.stroke(width, pathops.LineCap.ROUND_CAP, pathops.LineJoin.ROUND_JOIN, 4)
    p.convertConicsToQuads()
    return U(p, p)


def transformed(p, m):
    q = pathops.Path()
    p.draw(TransformPen(q.getPen(), m))
    return q


def from_d(d):
    p = pathops.Path(fillType=pathops.FillType.EVEN_ODD)
    parse_path(d, p.getPen())
    p.simplify()
    return p


def num(v):
    return ('%.1f' % v).rstrip('0').rstrip('.')


def d_of(p):
    pen = SVGPathPen(None, ntos=num)
    p.draw(pen)
    return pen.getCommands()


def pieces(p):
    out = []
    for c in p.contours:
        q = pathops.Path()
        c.draw(q.getPen())
        out.append(q)
    return out


# ---- the mark ----------------------------------------------------------------

def source():
    """The source drawing's moon, mouse and eye, and where the three holes go."""
    src = open(os.path.join(HERE, 'mouse.svg')).read()
    attrs = lambda i: dict(re.findall(r'(\w+)="([^"]*)"', re.search(rf'<\w+ id="{i}"[^>]*>', src).group(0)))
    moon, eye = attrs('moon'), attrs('eye')
    acx, acy, rho = HOLE_ARC
    phi = math.radians(HOLE_START)
    holes = [(acx + rho * math.cos(phi), acy + rho * math.sin(phi), HOLE_RADII[0])]
    for r0, r1, g in zip(HOLE_RADII, HOLE_RADII[1:], HOLE_GAPS):
        phi -= 2 * math.asin((r0 + r1 + g) / (2 * rho))
        holes.append((acx + rho * math.cos(phi), acy + rho * math.sin(phi), r1))
    return (tuple(float(moon[k]) for k in ('cx', 'cy', 'r')), attrs('mouse')['d'],
            tuple(float(eye[k]) for k in ('cx', 'cy', 'r')), holes)


def mark():
    """The moon with the mouse cut out of it, its eye, and three holes. 1000 units."""
    moon, mouse, eye, holes = source()
    body = U(D(circle(*moon), from_d(mouse)), circle(*eye))
    for x, y, r in holes:
        body = D(body, circle(x, y, r))
    return body


def tile_svg(indent):
    """The mark as the home tile draws it: dark on the brand colour, the holes
    left as circles so the desktop app's first page can make them breathe.
    Written into both first pages, which must match to the pixel."""
    moon, mouse, eye, holes = source()
    c = lambda x, y, r: f'cx="{num(x)}" cy="{num(y)}" r="{num(r)}"'
    lines = ['<svg viewBox="0 0 1000 1000" aria-hidden="true">',
             '  <mask id="boot-holes" maskUnits="userSpaceOnUse" x="0" y="0" width="1000" height="1000">',
             '    <rect width="1000" height="1000" fill="#fff" />',
             '    <g fill="#000">',
             f'      <path d="{mouse}" />']
    lines += [f'      <circle {c(*h)} />' for h in holes]
    lines += ['    </g>', '  </mask>', '  <g fill="#23242a">',
              f'    <circle {c(*moon)} mask="url(#boot-holes)" />', f'    <circle {c(*eye)} />', '  </g>', '</svg>']
    return ('\n' + ' ' * indent).join(lines)


def motion_parts():
    """What the docs site's logo animation moves, in the mark's 1000 units."""
    import json
    moon, mouse, eye, holes = source()
    m = from_d(mouse)
    x1 = m.bounds[2]
    tip = I(m, rect(x1 - 6, 0, x1 + 1, 1000)).bounds       # the snout: the mouse's rightmost point
    return json.dumps(dict(colour=BRAND, moon=moon, mouse=mouse, eye=eye, nose=(x1, (tip[1] + tip[3]) / 2),
                           holes=[tuple(round(v, 2) for v in h) for h in holes]), ensure_ascii=False) + '\n'


FIRST_PAGES = (os.path.join(ROOT, 'frontend', 'index.html'), os.path.join(ROOT, 'desktop', 'shell', 'index.html'))


# ---- 知是 ---------------------------------------------------------------------

def zh_source():
    """知 with its 口 as a ring, and 是 beside it, from the font; y down, tight box."""
    f = font('ResourceHanRoundedCN-Bold', '知是')
    cmap = f.getBestCmap()
    zc = glyph(f, cmap[ord('知')])
    bs = [rbounds([c]) for c in zc]
    right = [j for j, b in enumerate(bs) if b[0] > 520]
    ob = max((bs[j] for j in right), key=lambda b: b[2] - b[0])
    ib = min((bs[j] for j in right), key=lambda b: b[2] - b[0])
    t = ib[0] - ob[0]
    left_edge = max(b[2] for j, b in enumerate(bs) if j not in right)
    r = (ob[2] - ob[0]) / 2 * KOU_SCALE
    cx = max((ob[0] + ob[2]) / 2, left_edge + 22 + r)
    cy = (ob[1] + ob[3]) / 2 + 10
    zc = [c for j, c in enumerate(zc) if j not in right] + [rec_circle(cx, cy, r), rec_circle(cx, cy, r - t * 1.05)]
    sc = glyph(f, cmap[ord('是')])
    za, zb = rbounds(zc), rbounds(sc)
    dx = za[2] + CHAR_GAP - zb[0]
    cs = zc + mapped(sc, lambda x, y: (x + dx, y))
    x0, y0, x1, y1 = rbounds(cs)
    p = pathops.Path(fillType=pathops.FillType.EVEN_ODD)
    pen = TransformPen(p.getPen(), (1, 0, 0, -1, -x0, y1))
    rr = RecordingPen()
    rr.value = [op for c in cs for op in c]
    rr.replay(pen)
    p.simplify()
    return p, x1 - x0, y1 - y0


def zh_word():
    ink, w, h = zh_source()
    parts = pieces(ink)
    box = lambda q: q.bounds
    square = lambda q: abs((box(q)[2] - box(q)[0]) - (box(q)[3] - box(q)[1])) < 8
    ring_in, ring_out = sorted((q for q in parts if square(q) and 550 < (box(q)[0] + box(q)[2]) / 2 < 900),
                               key=lambda q: box(q)[2] - box(q)[0])
    ri = max((q for q in parts if box(q)[0] > 1000 and box(q)[3] < 480), key=lambda q: box(q)[2] - box(q)[0])
    counters = sorted((q for q in parts if q is not ri and box(ri)[0] < box(q)[0] and box(q)[2] < box(ri)[2]
                       and box(ri)[1] < box(q)[1] and box(q)[3] < box(ri)[3]), key=lambda q: box(q)[1])

    # the ring is taken out here and rebuilt round after the squash
    ob, ib = box(ring_out), box(ring_in)
    rcx, rcy, R = (ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2, (ob[2] - ob[0]) / 2
    rt = R - (ib[2] - ib[0]) / 2
    ink = D(ink, ring_out)

    # 日: a rounded box narrower than the character, the font's stroke weights kept
    cb = box(ri)
    top, bot = box(counters[0]), box(counters[1])
    side, t_top, t_mid, t_bot = top[0] - cb[0], top[1] - cb[1], bot[1] - top[3], cb[3] - bot[3]
    cx, W, H = (cb[0] + cb[2]) / 2, (cb[2] - cb[0]) * RI_WIDTH, cb[3] - cb[1]
    x0, x1, y0, y1 = cx - W / 2, cx + W / 2, cb[1], cb[3]
    outer = rrect(x0, y0, x1, y1, H * RI_RADIUS)
    inner = rrect(x0 + side, y0 + t_top, x1 - side, y1 - t_bot, max(H * RI_RADIUS - side, 0))
    my = y0 + t_top + ((y1 - t_bot) - (y0 + t_top) - t_mid) / 2
    ink = U(D(ink, ri), U(D(outer, inner), rect(x0 + side / 2, my, x1 - side / 2, my + t_mid)))

    # 疋: the font's 撇 starts in a ball and its 捺 is a wedge that swallows the
    # corner; both are redrawn as single-weight strokes (LOW_*). The long
    # horizontal, the stem and the short stroke to its right stay the font's.
    ink = D(ink, union(rect(*LOW_CUT[0]), rect(*LOW_CUT[1])))
    ink = U(ink, rect(*LOW_STEM))                      # the stem runs into the new bottom stroke
    yb, xr = LOW_BOTTOM, w - LOW_STROKE / 2
    na = LOW_NA + [('L', (xr, yb))]
    ink = union(ink, stroke(LOW_PIE, LOW_STROKE), stroke(na, LOW_STROKE))

    ink = transformed(ink, (1, 0, 0, SQUASH, 0, 0))
    R2, cy2 = R * (1 + SQUASH) / 2, rcy * SQUASH       # between the old width and the new height
    ink = U(ink, D(circle(rcx, cy2, R2), circle(rcx, cy2, R2 - rt)))
    return ink, w, h * SQUASH


def en_word():
    f = font('Nunito', 'cheese', EN_WEIGHT)
    buf = io.BytesIO()
    f.save(buf)
    data = buf.getvalue()
    b = hb.Buffer()
    b.add_str('cheese')
    b.guess_segment_properties()
    hb.shape(hb.Font(hb.Face(hb.Blob(data))), b, {'kern': True, 'liga': False})
    f = TTFont(io.BytesIO(data))
    x, cs = 0, []
    for info, pos in zip(b.glyph_infos, b.glyph_positions):
        cs += mapped(glyph(f, f.getGlyphName(info.codepoint)), lambda px, py, ox=x + pos.x_offset: (px + ox, py))
        x += pos.x_advance + EN_TRACKING
    x0, y0, x1, y1 = rbounds(cs)
    p = pathops.Path()
    rr = RecordingPen()
    rr.value = [op for c in cs for op in c]
    rr.replay(TransformPen(p.getPen(), (1, 0, 0, -1, -x0, y1)))
    p.simplify()
    return p, x1 - x0, y1 - y0


# ---- SVG files ---------------------------------------------------------------

def svg(w, h, inner, label=''):
    aria = f' role="img" aria-label="{label}"' if label else ''
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {num(w)} {num(h)}"{aria}>{inner}</svg>\n'


def placed(p, x, y, k, fill):
    return f'<path fill="{fill}" d="{d_of(transformed(p, (k, 0, 0, k, x, y)))}"/>'


def lockups(logo, zh, en):
    files = {}
    H = 100.0                                          # icon height; everything scales from it
    icon = lambda x, y: placed(logo, x, y, H / 1000, BRAND)
    (zp, zw, zh_), (ep, ew, eh) = zh, en
    for theme, ink in INK.items():
        for key, (p, w, h), ratio, label in (('zh', zh, 0.54, '知是'), ('en', en, 0.50, 'cheese')):
            th = H * ratio
            k = th / h
            files[f'lockup-{key}-{theme}.svg'] = svg(H * 1.3 + w * k, H, icon(0, 0) + placed(p, H * 1.3, (H - th) / 2, k, ink), label)
        # 横排中英：两行同宽，整体与图标垂直居中
        W = H * 0.56 * zw / zh_
        kz, ke = W / zw, W / ew
        gap = H * 0.11
        top = (H - (zh_ * kz + gap + eh * ke)) / 2
        x = H * 1.26
        files[f'lockup-zh-en-{theme}.svg'] = svg(x + W, H, icon(0, 0) + placed(zp, x, top, kz, ink)
                                                 + placed(ep, x, top + zh_ * kz + gap, ke, ink), '知是 cheese')
        # 上下排：图标在上，中英两行同宽居中
        W = H * 0.96
        kz, ke = W / zw, W / ew
        width = max(H, W)
        y = H * 1.2
        files[f'lockup-stacked-{theme}.svg'] = svg(
            width, y + zh_ * kz + H * 0.1 + eh * ke,
            icon((width - H) / 2, 0) + placed(zp, (width - W) / 2, y, kz, ink)
            + placed(ep, (width - W) / 2, y + zh_ * kz + H * 0.1, ke, ink), '知是 cheese')
    return files


# ---- raster icons -------------------------------------------------------------

def polygons(p, steps=24):
    rec = RecordingPen()
    p.draw(rec)
    out, cur, last = [], [], None
    for op, args in rec.value:
        if op == 'moveTo':
            cur, last = [args[0]], args[0]
        elif op == 'lineTo':
            cur.append(args[0])
            last = args[0]
        elif op == 'curveTo':
            p0, (p1, p2, p3) = last, args
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                cur.append(tuple(u ** 3 * p0[j] + 3 * u * u * t * p1[j] + 3 * u * t * t * p2[j] + t ** 3 * p3[j] for j in (0, 1)))
            last = p3
        elif op == 'qCurveTo':
            p0, (p1, p2) = last, args
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                cur.append(tuple(u * u * p0[j] + 2 * u * t * p1[j] + t * t * p2[j] for j in (0, 1)))
            last = p2
        elif op in ('closePath', 'endPath'):
            out.append(cur)
    return out


def coverage(p, size, scale, dx, dy, ss=8):
    """Even-odd coverage of a path at size x size, supersampled."""
    S = size * ss
    acc = Image.new('L', (S, S), 0)
    for poly in polygons(p):
        layer = Image.new('L', (S, S), 0)
        ImageDraw.Draw(layer).polygon([((x * scale + dx) * ss, (y * scale + dy) * ss) for x, y in poly], fill=255)
        acc = ImageChops.logical_xor(acc.convert('1'), layer.convert('1')).convert('L')
    return acc.resize((size, size), Image.LANCZOS)


def rgb(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def icon_png(logo, size, mark_frac, corner=0.0, inset=0.0):
    """The mark on the white tile.

    corner > 0 rounds the tile, leaves the rest clear and draws a hairline edge:
    those tiles sit on a page or a dock, where white on white would vanish.
    inset > 0 leaves a clear margin round the tile, as a macOS app icon has."""
    t0 = size * inset
    ts = size - 2 * t0
    scale = ts * mark_frac / 1000
    off = t0 + ts * (1 - mark_frac) / 2
    img = Image.new('RGBA', (size, size), rgb(TILE) + (255,))
    img.paste(Image.new('RGBA', (size, size), rgb(BRAND) + (255,)), (0, 0), coverage(logo, size, scale, off, off))
    if corner:
        r = ts * corner
        w = max(1.0, size / 256)
        outer = rrect(t0, t0, t0 + ts, t0 + ts, r)
        ring = D(outer, rrect(t0 + w, t0 + w, t0 + ts - w, t0 + ts - w, r - w))
        img.paste(Image.new('RGBA', (size, size), rgb(EDGE) + (255,)), (0, 0), coverage(ring, size, 1, 0, 0))
        img.putalpha(coverage(outer, size, 1, 0, 0))
    return img


def build():
    logo = mark()
    zh, en = zh_word(), en_word()
    out = {}
    d = d_of(logo)
    out[os.path.join(ASSETS, 'logo.svg')] = svg(1000, 1000, f'<path fill="{BRAND}" d="{d}"/>', '知是')
    # no fill of its own: it inherits one where it is inlined, and masks as black.
    # No name either: it is always an icon inside something that already has one.
    out[os.path.join(ASSETS, 'logo-plain.svg')] = svg(1000, 1000, f'<path d="{d}"/>')
    for key, (p, w, h), label in (('zh', zh, '知是'), ('en', en, 'cheese')):
        out[os.path.join(ASSETS, 'brand', f'wordmark-{key}.svg')] = svg(w, h, f'<path fill="currentColor" d="{d_of(p)}"/>', label)
    for name, text in lockups(logo, zh, en).items():
        out[os.path.join(ASSETS, 'brand', name)] = text
    tile = f'<rect x="12" y="12" width="976" height="976" rx="214" fill="{TILE}" stroke="{EDGE}" stroke-width="24"/>'
    out[os.path.join(PUBLIC, 'favicon.svg')] = svg(1000, 1000, tile + placed(logo, 150, 150, 0.7, BRAND))
    for path, text in out.items():
        open(path, 'w').write(text)
        print(os.path.relpath(path, ROOT))
    parts = os.path.join(ROOT, 'docs', 'site', 'logo', 'parts.json')
    open(parts, 'w').write(motion_parts())
    print(os.path.relpath(parts, ROOT))
    for path in FIRST_PAGES:
        page = open(path).read()
        m = re.search(r'( *)<svg viewBox="[^"]*" aria-hidden="true">.*?</svg>', page, re.S)
        page = page[:m.start()] + m.group(1) + tile_svg(len(m.group(1))) + page[m.end():]
        open(path, 'w').write(page)
        print(os.path.relpath(path, ROOT))

    pngs = {
        # full-bleed tiles: the platform draws its own mask
        os.path.join(PUBLIC, 'pwa-192x192.png'): (192, 0.70, 0, 0),
        os.path.join(PUBLIC, 'pwa-512x512.png'): (512, 0.70, 0, 0),
        os.path.join(PUBLIC, 'apple-touch-icon-180x180.png'): (180, 0.70, 0, 0),
        # maskable: the mark stays inside the 80% safe circle
        os.path.join(PUBLIC, 'pwa-maskable-512x512.png'): (512, 0.50, 0, 0),
        # shown on a page, so it carries its own rounded corners
        os.path.join(ASSETS, 'app-icon.png'): (256, 0.70, 0.225, 0),
        # the desktop app: macOS draws no mask, so the icon is Apple's template
        # shape itself, an 824 tile with rounded corners in a clear 1024 square.
        # Every desktop icon is made from it by `pnpm --dir desktop icons`.
        os.path.join(ROOT, 'desktop', 'icon-source.png'): (1024, 0.70, 185 / 824, 100 / 1024),
    }
    for path, (size, frac, corner, inset) in pngs.items():
        img = icon_png(logo, size, frac, corner, inset)
        (img if corner else img.convert('RGB')).save(path, optimize=True)
        print(os.path.relpath(path, ROOT))
    icon_png(logo, 256, 0.70, 0.22).save(os.path.join(PUBLIC, 'favicon.ico'), sizes=[(16, 16), (32, 32), (48, 48)])
    print('frontend/public/favicon.ico')
    # the brand scene's colour texture: the mark alone on a clear ground
    scene = Image.new('RGBA', (640, 640), (0, 0, 0, 0))
    scene.paste(Image.new('RGBA', (640, 640), rgb(BRAND) + (255,)), (0, 0), coverage(logo, 640, 0.6, 20, 20))
    scene.save(os.path.join(ASSETS, 'brand-scene', 'logo-color.png'), optimize=True)
    print('frontend/src/assets/brand-scene/logo-color.png')
    # the notification email's header: the mark alone on a clear ground. Mail
    # clients show no SVG, and a white tile would sit as a white square in a
    # dark-mode letter. Drawn at 4x the 36px it is shown at.
    mail = Image.new('RGBA', (144, 144), (0, 0, 0, 0))
    mail.paste(Image.new('RGBA', (144, 144), rgb(BRAND) + (255,)), (0, 0), coverage(logo, 144, 0.144, 0, 0))
    mail.save(os.path.join(PUBLIC, 'email-mark.png'), optimize=True)
    print('frontend/public/email-mark.png')


if __name__ == '__main__':
    build()
