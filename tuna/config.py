"""TUNA configuration and constants"""
from pathlib import Path

# ─── Directories ─────────────────────────────────────────────────────────────
HOME         = Path.home()
TUNA_DIR     = HOME / ".config" / "tuna"
MUSIC_DIR    = HOME / "Music" / "tuna"
PLAYLIST_DIR = TUNA_DIR / "playlists"
CACHE_DIR    = TUNA_DIR / "cache"
CONFIG_FILE  = TUNA_DIR / "config.json"

for d in (TUNA_DIR, MUSIC_DIR, PLAYLIST_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE   = 44100
CHUNK_SIZE    = 512
CHANNELS      = 1
FFT_BINS      = 40
VIZ_SMOOTHING = 0.65

# ─── Visualizer ───────────────────────────────────────────────────────────────
BAR_FILL  = "█"
BAR_EMPTY = " "

# ─── ASCII art dimensions (normal mode) ──────────────────────────────────────
ART_WIDTH  = 20   # columns  (braille = 40×80 effective pixels)
ART_HEIGHT = 10   # rows

# ─── Colour pair IDs ─────────────────────────────────────────────────────────
# 1-14:  static UI colours
# 20-23: dynamic theme colours (re-set per track)
# 50+:   art pixel colours (allocated per-cell during draw)
C_TITLE    = 1
C_ARTIST   = 2
C_PLAYLIST = 3
C_BAR_LOW  = 4
C_BAR_MID  = 5
C_BAR_HIGH = 6
C_SELECTED = 7   # fixed white-on-dark — never changes with theme
C_BORDER   = 8
C_STATUS   = 9
C_DIM      = 10
C_PROGRESS = 11
C_ACCENT   = 12
C_NOW      = 13
C_HEADER   = 14

# Dynamic theme pairs
C_THEME_TITLE  = 20
C_THEME_ARTIST = 21
C_THEME_BAR    = 22
C_THEME_SEL    = 23   # selected bg — kept but used only where explicit

# ─── Keybindings ─────────────────────────────────────────────────────────────
KEY_QUIT         = ord('q')
KEY_PAUSE        = ord(' ')
KEY_NEXT         = ord('n')
KEY_PREV         = ord('p')
KEY_VOL_UP       = ord('+')
KEY_VOL_UP2      = ord('=')
KEY_VOL_DOWN     = ord('-')
KEY_SHUFFLE      = ord('s')
KEY_REPEAT       = ord('r')
KEY_NEW_PLAYLIST = ord('c')
KEY_DELETE       = ord('x')   # delete playlist (with confirm modal)
KEY_SEARCH       = ord('/')
KEY_HELP         = ord('?')
KEY_TAB          = 9
KEY_ENTER        = 10
KEY_ESC          = 27
KEY_SEEK_BACK    = ord('[')
KEY_SEEK_FWD     = ord(']')

# ─── Idle mode ────────────────────────────────────────────────────────────────
IDLE_TIMEOUT = 8.0   # seconds before full-screen art mode activates

# ─── Supported audio formats ──────────────────────────────────────────────────
AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav"}

# ─── Default config ───────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "volume":        80,
    "shuffle":       False,
    "repeat":        "none",
    "music_dir":     str(MUSIC_DIR),
    "last_playlist": None,
    "visualizer":    True,
    "dynamic_theme": True,
}
