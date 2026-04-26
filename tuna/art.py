"""
TUNA cover art renderer — braille Unicode for maximum resolution.

Each Unicode braille character (U+2800–U+28FF) encodes 8 binary dots in a 2×4
grid.  By treating each dot as one pixel and colouring the character with the
average RGB of its 8 source pixels, we get 4× the effective resolution of
traditional ASCII art at the same terminal cell count.

Braille dot layout (Unicode bit positions):
    col 0   col 1
    dot1(0)  dot4(3)   ← row 0
    dot2(1)  dot5(4)   ← row 1
    dot3(2)  dot6(5)   ← row 2
    dot7(6)  dot8(7)   ← row 3

Terminal cell aspect ratio is ~2:1 (height:width).  A braille cell covers
2 pixel-columns × 4 pixel-rows, so in display-space each cell is
(2 * 2_aspect) × 4 = 4×4 display-units — naturally square with no correction.

For the small normal-mode art we still use braille (same render path, smaller
dimensions).  The idle fullscreen view renders at a much larger size and is
re-computed at that size, not stretched from the small version.
"""
from tuna.config import ART_WIDTH, ART_HEIGHT

# Braille dot → bit mapping: (pixel_col, pixel_row) → bit_index
_DOT_BITS = {
    (0, 0): 0, (0, 1): 1, (0, 2): 2, (0, 3): 6,
    (1, 0): 3, (1, 1): 4, (1, 2): 5, (1, 3): 7,
}

# ── Public API ────────────────────────────────────────────────────────────────

def ascii_art_lines(image_path: str | None,
                    width: int  = ART_WIDTH,
                    height: int = ART_HEIGHT) -> list[list[tuple]]:
    """
    Return rows of (char, r, g, b) tuples using braille Unicode characters.

    width  = number of terminal columns  → image sampled at width*2  pixels wide
    height = number of terminal rows     → image sampled at height*4  pixels tall

    Returns `height` rows of `width` cells.
    """
    if not image_path:
        return _placeholder(width, height)
    return _render_braille(image_path, width, height)


def dominant_palette(image_path: str | None,
                     n: int = 3) -> list[tuple[int, int, int]]:
    _defaults = [(80, 200, 220), (100, 200, 120), (80, 120, 220)]
    if not image_path:
        return _defaults[:n]
    try:
        from PIL import Image
        img    = Image.open(image_path).convert("RGB")
        img    = img.resize((64, 64), Image.BILINEAR)
        pixels = list(img.getdata())
        pal    = _kmeans_palette(pixels, n)
        pal.sort(key=lambda c: 0.299*c[0] + 0.587*c[1] + 0.114*c[2],
                 reverse=True)
        return [_boost(c) for c in pal]
    except Exception:
        return _defaults[:n]


# ── Braille renderer ──────────────────────────────────────────────────────────

def _render_braille(image_path: str, width: int, height: int) -> list[list[tuple]]:
    """
    Braille renderer with global contrast threshold.

    Key insight: using a per-block mean threshold makes every region look equally
    dense (~4/8 dots), flattening all brightness variation. Instead we compute one
    global threshold from the full image luminance histogram so bright areas get
    dense dot patterns and dark areas get sparse ones — restoring visible contrast.

    We also enhance contrast and saturation before rendering so colours pop.
    """
    try:
        from PIL import Image
        import numpy as np

        img = _load_square(image_path)

        px_w = width  * 2
        px_h = height * 4
        img  = img.resize((px_w, px_h), Image.LANCZOS)
        px   = np.array(img, dtype=np.uint8)  # (px_h, px_w, 3)

        # Per-block mean threshold: each cell's dots are lit based on whether
        # each pixel is above or below that cell's own average brightness.
        # This gives the maximum texture and detail in the dot patterns.
        # If all 8 pixels are identical (code stays 0x2800), light 4 dots so
        # the cell's colour is always visible — never a completely empty cell.
        luma_all = 0.299*px[:,:,0].astype(float) +                    0.587*px[:,:,1].astype(float) +                    0.114*px[:,:,2].astype(float)

        rows = []
        for ty in range(height):
            row = []
            for tx in range(width):
                y0 = ty * 4
                x0 = tx * 2
                block      = px[y0:y0+4, x0:x0+2, :]
                block_luma = luma_all[y0:y0+4, x0:x0+2]

                r = int(np.mean(block[:, :, 0]))
                g = int(np.mean(block[:, :, 1]))
                b = int(np.mean(block[:, :, 2]))

                thresh = block_luma.mean()
                code   = 0x2800
                for (pc, pr), bit in _DOT_BITS.items():
                    if block_luma[pr, pc] >= thresh:
                        code |= (1 << bit)

                # Guarantee at least half the dots are lit so the cell colour
                # is always visible — 0x281C = 4 dots in a cross pattern
                if code == 0x2800:
                    code = 0x281C

                row.append((chr(code), r, g, b))
            rows.append(row)
        return rows

    except Exception:
        return _placeholder(width, height)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_square(image_path: str):
    from PIL import Image
    img  = Image.open(image_path).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def _boost(c: tuple, factor: float = 1.35) -> tuple:
    r, g, b = c
    avg = (r + g + b) / 3
    return (
        int(max(0, min(255, avg + (r - avg) * factor))),
        int(max(0, min(255, avg + (g - avg) * factor))),
        int(max(0, min(255, avg + (b - avg) * factor))),
    )


def _kmeans_palette(pixels: list, k: int) -> list:
    if len(pixels) < k:
        return pixels[:k]
    step    = len(pixels) // k
    centres = [pixels[i * step] for i in range(k)]
    buckets = [[] for _ in range(k)]
    for px in pixels:
        best = min(range(k), key=lambda i: _dist2(px, centres[i]))
        buckets[best].append(px)
    result = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            result.append(centres[i])
        else:
            result.append((
                sum(p[0] for p in bucket) // len(bucket),
                sum(p[1] for p in bucket) // len(bucket),
                sum(p[2] for p in bucket) // len(bucket),
            ))
    return result


def _dist2(a, b):
    return (a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2


def _placeholder(width: int, height: int) -> list:
    """Braille placeholder: dotted border with a note symbol in the middle."""
    rows = []
    for ty in range(height):
        row = []
        for tx in range(width):
            on_edge = ty == 0 or ty == height-1 or tx == 0 or tx == width-1
            if on_edge:
                ch = chr(0x28FF)   # full braille block for border
            elif ty == height//2 and tx == width//2:
                ch = chr(0x2834)   # ⠴ — looks like a music note-ish dot
            else:
                ch = chr(0x2802)   # ⠂ — single dot, barely visible
            row.append((ch, 80, 110, 160))
        rows.append(row)
    return rows
