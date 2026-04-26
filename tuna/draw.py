"""TUNA curses colour setup and drawing primitives"""
import curses
from tuna.config import *


def init_colors():
    curses.start_color()
    curses.use_default_colors()

    def pair(n, fg, bg=-1):
        try:
            curses.init_pair(n, fg, bg)
        except Exception:
            pass

    pair(C_TITLE,    curses.COLOR_CYAN,    -1)
    pair(C_ARTIST,   curses.COLOR_GREEN,   -1)
    pair(C_PLAYLIST, curses.COLOR_YELLOW,  -1)
    pair(C_BAR_LOW,  curses.COLOR_GREEN,   -1)
    pair(C_BAR_MID,  curses.COLOR_YELLOW,  -1)
    pair(C_BAR_HIGH, curses.COLOR_RED,     -1)
    try:
        pair(C_SELECTED, curses.COLOR_WHITE, 238)
    except Exception:
        pair(C_SELECTED, curses.COLOR_WHITE, curses.COLOR_BLACK)
    try:
        pair(C_BORDER, 236, -1)
    except Exception:
        pair(C_BORDER, curses.COLOR_WHITE, -1)
    pair(C_STATUS,   curses.COLOR_MAGENTA, -1)
    pair(C_DIM,      curses.COLOR_WHITE,   -1)
    pair(C_PROGRESS, curses.COLOR_CYAN,    -1)
    pair(C_ACCENT,   curses.COLOR_CYAN,    -1)
    pair(C_NOW,      curses.COLOR_GREEN,   -1)
    pair(C_HEADER,   curses.COLOR_YELLOW,  -1)
    pair(C_THEME_TITLE,  curses.COLOR_CYAN,  -1)
    pair(C_THEME_ARTIST, curses.COLOR_GREEN, -1)
    pair(C_THEME_BAR,    curses.COLOR_CYAN,  -1)
    pair(C_THEME_SEL,    curses.COLOR_WHITE, curses.COLOR_BLUE)

    # Pre-initialise art colour pairs: pair N = xterm colour N as fg.
    # This lets art draw call color_pair(c256) with zero allocation overhead.
    for _n in range(24, 256):
        try:
            curses.init_pair(_n, _n, -1)
        except Exception:
            break


def apply_theme(palette: list[tuple[int, int, int]]):
    if not curses.can_change_color() or len(palette) < 2:
        return
    SLOT_T, SLOT_A, SLOT_B, SLOT_S = 240, 241, 242, 243

    def to_k(v: int) -> int:
        return int(v / 255 * 1000)

    def set_slot(slot, r, g, b):
        try:
            curses.init_color(slot, to_k(r), to_k(g), to_k(b))
        except Exception:
            pass

    r0, g0, b0 = palette[0]
    r1, g1, b1 = palette[1] if len(palette) > 1 else palette[0]
    r2, g2, b2 = palette[2] if len(palette) > 2 else palette[0]
    set_slot(SLOT_T, r0, g0, b0)
    set_slot(SLOT_A, r1, g1, b1)
    set_slot(SLOT_B, r2, g2, b2)
    set_slot(SLOT_S, max(0, r2 // 5), max(0, g2 // 5), max(0, b2 // 5))
    try:
        curses.init_pair(C_THEME_TITLE,  SLOT_T, -1)
        curses.init_pair(C_THEME_ARTIST, SLOT_A, -1)
        curses.init_pair(C_THEME_BAR,    SLOT_B, -1)
        curses.init_pair(C_THEME_SEL,    curses.COLOR_WHITE, SLOT_S)
    except Exception:
        pass


def cattr(pair_id: int, bold: bool = False, dim: bool = False) -> int:
    attr = curses.color_pair(pair_id)
    if bold:
        attr |= curses.A_BOLD
    if dim:
        attr |= curses.A_DIM
    return attr


def safe_addstr(win, y: int, x: int, text: str, attr: int = 0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    avail = w - x - 1
    if avail <= 0:
        return
    try:
        win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def hline(win, y: int, x: int, length: int, char: str = "─", attr: int = 0):
    safe_addstr(win, y, x, char * length, attr)


def draw_timeline(win, y: int, x: int, width: int,
                  position: float, duration: float,
                  pos_str: str, rem_str: str):
    """
    Integrated scrubber bar with elapsed on left, remaining on right.

      0:45 ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░ -2:58

    No dot — clean filled/empty blocks only. All ASCII-safe chars.
    """
    if width < 8:
        return

    left_w  = len(pos_str)
    right_w = len(rem_str)
    gap     = 1
    bar_x   = x + left_w + gap
    bar_end = x + width - right_w - gap
    bar_w   = max(4, bar_end - bar_x)

    ratio  = min(1.0, position / duration) if duration > 0 else 0.0
    filled = max(0, min(int(ratio * bar_w), bar_w))

    safe_addstr(win, y, x, pos_str, cattr(C_DIM))
    safe_addstr(win, y, bar_x,          "▓" * filled,           cattr(C_THEME_BAR, bold=True))
    safe_addstr(win, y, bar_x + filled, "░" * (bar_w - filled), cattr(C_BORDER, dim=True))
    safe_addstr(win, y, bar_end + gap,  rem_str,                 cattr(C_DIM))


def draw_volume_inline(win, y: int, x: int, width: int, volume: int):
    """
    ▪ vol ▓▓▓▓▓▓▓▓░░░░  70%
    """
    label    = "▪ vol "
    n_segs   = 20
    filled   = max(0, min(round(volume / 100 * n_segs), n_segs))
    pct_str  = f"  {volume:3d}%"

    col = x
    safe_addstr(win, y, col, label, cattr(C_DIM))
    col += len(label)
    safe_addstr(win, y, col, "▓" * filled,           cattr(C_THEME_BAR, bold=True))
    col += filled
    safe_addstr(win, y, col, "░" * (n_segs - filled), cattr(C_BORDER, dim=True))
    col += (n_segs - filled)
    safe_addstr(win, y, col, pct_str,                 cattr(C_DIM))


def draw_progress_bar(win, y: int, x: int, width: int,
                      position: float, duration: float,
                      theme_bar: bool = True):
    """Simple scrubber without timestamps — used in idle fullscreen view."""
    if width < 4:
        return
    ratio  = min(1.0, position / duration) if duration > 0 else 0.0
    filled = max(0, min(int(ratio * width), width))

    safe_addstr(win, y, x,          "▓" * filled,           cattr(C_THEME_BAR, bold=True))
    safe_addstr(win, y, x + filled, "░" * (width - filled), cattr(C_BORDER, dim=True))


def draw_volume_bar(win, y: int, x: int, volume: int):
    """Legacy wrapper."""
    draw_volume_inline(win, y, x, 40, volume)
