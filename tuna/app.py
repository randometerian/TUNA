"""TUNA — main application: curses UI, input handling, view routing"""
import curses
import time
import random
import math
from pathlib import Path

from tuna.config   import *
from tuna.draw     import (init_colors, apply_theme, cattr, safe_addstr,
                            hline, draw_progress_bar, draw_volume_bar,
                            draw_timeline, draw_volume_inline)
from tuna.settings   import ConfigManager
from tuna.playlist   import PlaylistManager, Track
from tuna.player     import Player
from tuna.visualizer import Visualizer
from tuna.art        import ascii_art_lines, dominant_palette
from tuna.metadata   import format_duration, read_metadata

# Tuna can ASCII art — used on help screen and title bar
LOGO_LINES = [
    "  ╭──────────────────────────────────╮  ",
    "  ╞══════════════════════════════════╡  ",
    "  │                                  │  ",
    "  │    ><(((º>        T U N A        │  ",
    "  │                                  │  ",
    "  ╞══════════════════════════════════╡  ",
    "  ╰──────────────────────────────────╯  ",
]
# Single-line title for the top border
TITLE_STR = "  ><(((º>  T · U · N · A  "

VIEW_PLAYER = 0
VIEW_HELP   = 1
VIEW_IDLE  = 2   # full-screen art + visualizer

SIDEBAR_W = 26


class TunaApp:

    # Init
    def __init__(self):
        self.cfg    = ConfigManager()
        self.plm    = PlaylistManager()
        self.player = Player()
        self.player.start()
        self.viz    = Visualizer()

        self._view          = VIEW_PLAYER
        self._pl_idx        = 0
        self._track_idx     = 0
        self._scroll_offset = 0
        self._focus         = 1   # 0=sidebar, 1=tracklist
        self._shuffle       = self.cfg.get("shuffle", False)
        self._repeat        = self.cfg.get("repeat", "none")
        self._search_buf    = ""
        self._searching     = False
        self._status_msg    = ""
        self._status_ts     = 0.0
        self._art_cache:   dict[str, list] = {}
        self._pal_cache:   dict[str, list] = {}
        self._input_modal: dict | None     = None
        self._running       = True
        self._last_track_path  = ""
        self._last_key_time    = time.time()   # for idle timeout
        self._now_playing_path = ""            # path of track currently in mpv

        self.plm.scan_library(self.cfg.get("music_dir"))
        # Start background watcher — auto-detects new files in music_dir
        self.plm.start_watcher(self.cfg.get("music_dir"))

        last_pl = self.cfg.get("last_playlist")
        if last_pl is not None:
            for i, pl in enumerate(self.plm.playlists):
                if pl.id == last_pl:
                    self._pl_idx = i
                    break

    # ══════════════════════════════════════════════════════════════════════════
    # Entry point
    # ══════════════════════════════════════════════════════════════════════════

    def run(self):
        curses.wrapper(self._main)

    # Main loop
    def _main(self, stdscr):
        self._stdscr = stdscr
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(50)   # ~20 FPS
        init_colors()
        try:
            while self._running:
                self._handle_player_state()
                self._maybe_update_theme()
                self._maybe_enter_idle()
                self._draw(stdscr)
                keys = []
                while True:
                    k = stdscr.getch()
                    if k == -1:
                        break
                    keys.append(k)
                if keys:
                    self._last_key_time = time.time()
                    if self._view == VIEW_IDLE:
                        # Any key wakes from idle
                        self._view = VIEW_PLAYER
                    else:
                        self._handle_keys(keys)
        finally:
            self.player.quit()
            self.viz.stop()
            self.cfg.set("last_playlist",
                         self.plm.playlists[self._pl_idx].id
                         if self.plm.playlists else None)

    # ══════════════════════════════════════════════════════════════════════════
    # Idle mode
    # ══════════════════════════════════════════════════════════════════════════

    def _maybe_enter_idle(self):
        if (self._view == VIEW_PLAYER
                and self.player.playing
                and time.time() - self._last_key_time > IDLE_TIMEOUT):
            self._view = VIEW_IDLE

    # ══════════════════════════════════════════════════════════════════════════
    # Dynamic theme
    # ══════════════════════════════════════════════════════════════════════════

    def _maybe_update_theme(self):
        if not self.cfg.get("dynamic_theme", True):
            return
        # Use cursor track for theme — scrolling updates the colour palette live
        track = self._current_track()
        path  = track.cover_path if track else ""
        if path == self._last_track_path:
            return
        self._last_track_path = path
        if path not in self._pal_cache:
            self._pal_cache[path] = dominant_palette(path or None, n=3)
        apply_theme(self._pal_cache[path])

    # ══════════════════════════════════════════════════════════════════════════
    # Player state
    # ══════════════════════════════════════════════════════════════════════════

    def _handle_player_state(self):
        if self.player.finished:
            self.player.finished = False
            self._auto_advance()

    def _auto_advance(self):
        pl = self._current_playlist()
        if not pl or not pl.tracks:
            return
        n = len(pl.tracks)
        if self._shuffle:
            candidates = list(range(n))
            if n > 1:
                candidates.remove(self._track_idx)
            self._track_idx = random.choice(candidates)
        elif self._repeat == "one":
            pass
        elif self._repeat == "all":
            self._track_idx = (self._track_idx + 1) % n
        else:
            if self._track_idx < n - 1:
                self._track_idx += 1
            else:
                return
        self._play_current()

    # Playback control
    def _next_track(self):
        pl = self._current_playlist()
        if not pl or not pl.tracks:
            return
        n = len(pl.tracks)
        if self._shuffle:
            candidates = list(range(n))
            if n > 1:
                candidates.remove(self._track_idx)
            self._track_idx = random.choice(candidates)
        else:
            self._track_idx = (self._track_idx + 1) % n
        self._play_current()

    def _prev_track(self):
        if self.player.position > 3.0:
            self.player.seek(0, relative=False)
            return
        pl = self._current_playlist()
        if not pl or not pl.tracks:
            return
        n = len(pl.tracks)
        self._track_idx = (self._track_idx - 1) % n
        self._play_current()

    def _play_current(self):
        pl = self._current_playlist()
        if not pl or not pl.tracks:
            return
        idx = self._track_idx
        if idx < 0 or idx >= len(pl.tracks):
            return
        track = pl.tracks[idx]
        if not Path(track.path).exists():
            self._status(f"File not found: {track.path}")
            return
        self.player.load(track.path)
        self.player.set_volume(self.cfg.get("volume", 80))
        self.viz.notify_playing(True)
        self._now_playing_path = track.path
        # Clear idle art cache so it re-renders at full size for the new track
        self._art_cache = {k: v for k, v in self._art_cache.items()
                           if not k.startswith('idle:')}

    def _current_playlist(self):
        return self.plm.get(self._pl_idx)

    def _current_track(self) -> Track | None:
        """The track under the cursor — used for selection/UI highlight."""
        pl = self._current_playlist()
        if pl and pl.tracks and 0 <= self._track_idx < len(pl.tracks):
            return pl.tracks[self._track_idx]
        return None

    def _playing_track(self) -> Track | None:
        """The track actually loaded in mpv — used for idle screen art."""
        if not self._now_playing_path:
            return self._current_track()
        # Search all playlists for the playing path
        for pl in self.plm.playlists:
            for t in pl.tracks:
                if t.path == self._now_playing_path:
                    return t
        return self._current_track()

    # ══════════════════════════════════════════════════════════════════════════
    # Input
    # ══════════════════════════════════════════════════════════════════════════

    _NAV_KEYS = {curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT}

    # Key handling
    def _handle_keys(self, keys: list):
        """
        Process keys from one frame drain.
        Non-nav keys (space, letters, etc): process every one.
        Nav keys (arrows): only one per frame - terminal repeat gives steady speed.
        """
        if not keys:
            return

        self._last_key_time = time.time()

        nav_keys = [k for k in keys if k in self._NAV_KEYS]
        other_keys = [k for k in keys if k not in self._NAV_KEYS]

        if nav_keys:
            self._handle_key(nav_keys[0])

        for k in other_keys:
            self._handle_key(k)

    def _handle_key(self, key: int):
        if self._input_modal:
            self._handle_modal_key(key)
            return
        if self._searching:
            self._handle_search_key(key)
            return
        if self._view == VIEW_HELP:
            self._view = VIEW_PLAYER
            return

        if key == KEY_QUIT:
            self._running = False
        elif key == KEY_PAUSE:
            self.player.play_pause()
            self.viz.notify_playing(self.player.playing)
        elif key == KEY_NEXT:
            self._next_track()
        elif key == KEY_PREV:
            self._prev_track()
        elif key in (KEY_VOL_UP, KEY_VOL_UP2):
            v = min(100, self.cfg.get("volume", 80) + 5)
            self.cfg.set("volume", v)
            self.player.set_volume(v)
        elif key == KEY_VOL_DOWN:
            v = max(0, self.cfg.get("volume", 80) - 5)
            self.cfg.set("volume", v)
            self.player.set_volume(v)
        elif key == KEY_SEEK_BACK:
            self.player.seek(-5, relative=True)
        elif key == KEY_SEEK_FWD:
            self.player.seek(5, relative=True)
        elif key == KEY_SHUFFLE:
            self._shuffle = not self._shuffle
            self.cfg.set("shuffle", self._shuffle)
            self._status("Shuffle " + ("ON" if self._shuffle else "OFF"))
        elif key == KEY_REPEAT:
            cycle = {"none": "one", "one": "all", "all": "none"}
            self._repeat = cycle[self._repeat]
            self.cfg.set("repeat", self._repeat)
            self._status(f"Repeat: {self._repeat}")
        elif key == KEY_HELP:
            self._view = VIEW_HELP
        elif key == ord('e'):
            self._status("Tab to sidebar, ENTER with playlist selected to add songs")
        elif key == KEY_SEARCH:
            self._searching  = True
            self._search_buf = ""
        elif key == KEY_NEW_PLAYLIST:
            self._open_modal("New playlist name:", self._cb_create_playlist)
        elif key == KEY_TAB:
            self._focus = 1 - self._focus
        elif key == curses.KEY_UP:
            if self._focus == 0:
                self._pl_idx = max(0, self._pl_idx - 1)
                self._track_idx = 0; self._scroll_offset = 0
            else:
                pl = self._current_playlist()
                if pl and self._track_idx > 0:
                    self._track_idx -= 1
                    self._scroll_clamp()
        elif key == curses.KEY_DOWN:
            if self._focus == 0:
                self._pl_idx = min(len(self.plm.playlists) - 1, self._pl_idx + 1)
                self._track_idx = 0; self._scroll_offset = 0
            else:
                pl = self._current_playlist()
                if pl and self._track_idx < len(pl.tracks) - 1:
                    self._track_idx += 1
                    self._scroll_clamp()
        elif key == curses.KEY_LEFT:
            if self._focus == 1:
                self._prev_track()
        elif key == curses.KEY_RIGHT:
            if self._focus == 0:
                self._focus = 1
            else:
                self._next_track()
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            if self._focus == 0:
                self._focus = 1
            else:
                self._play_current()

        elif key == curses.KEY_PPAGE:
            if self._focus == 1:
                self._track_idx = max(0, self._track_idx - 10)
                self._scroll_clamp()
        elif key == curses.KEY_NPAGE:
            if self._focus == 1:
                pl = self._current_playlist()
                if pl:
                    self._track_idx = min(len(pl.tracks) - 1, self._track_idx + 10)
                    self._scroll_clamp()
        elif key == curses.KEY_HOME:
            if self._focus == 1:
                self._track_idx = 0; self._scroll_offset = 0
        elif key == curses.KEY_END:
            if self._focus == 1:
                pl = self._current_playlist()
                if pl and pl.tracks:
                    self._track_idx = len(pl.tracks) - 1
                    self._scroll_clamp()

    # ── Search ────────────────────────────────────────────────────────────────
    def _handle_search_key(self, key: int):
        if key == KEY_ESC:
            self._searching = False; self._search_buf = ""
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            self._searching = False
            self._do_search(self._search_buf)
        elif key in (curses.KEY_BACKSPACE, 127):
            self._search_buf = self._search_buf[:-1]
        elif 32 <= key < 127:
            self._search_buf += chr(key)

    def _do_search(self, query: str):
        q  = query.lower()
        pl = self._current_playlist()
        if not pl:
            return
        for i, t in enumerate(pl.tracks):
            if q in t.display_title.lower() or q in t.display_artist.lower():
                self._track_idx = i
                self._scroll_clamp()
                self._status(f"Found: {t.display_title}")
                self._focus = 1
                return
        self._status("Not found")

    # ── Modal ─────────────────────────────────────────────────────────────────
    def _handle_modal_key(self, key: int):
        m = self._input_modal
        mode = m.get("mode", "text")

        if mode == "confirm":
            if key in (ord('y'), ord('Y'), KEY_ENTER, curses.KEY_ENTER):
                cb = m["callback"]
                self._input_modal = None
                cb()
            elif key in (KEY_ESC, ord('n'), ord('N')):
                self._input_modal = None
            return

        if mode == "pl_settings":
            self._handle_pl_settings_key(key)
            return

        if mode == "pl_add_songs":
            self._handle_pl_add_key(key)
            return

        if mode == "pl_remove_songs":
            self._handle_pl_remove_key(key)
            return

        # Text-input modal
        if key == KEY_ESC:
            self._input_modal = None
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            cb = m["callback"]; val = m["buf"]
            self._input_modal = None
            if val.strip():
                cb(val.strip())
        elif key in (curses.KEY_BACKSPACE, 127):
            m["buf"] = m["buf"][:-1]
        elif 32 <= key < 127:
            m["buf"] += chr(key)

    # Modal UI
    def _open_modal(self, prompt: str, callback):
        self._input_modal = {"prompt": prompt, "buf": "", "callback": callback}

    def _cb_create_playlist(self, name: str):
        self.plm.create(name)
        self._pl_idx = len(self.plm.playlists) - 1
        self._track_idx = 0; self._scroll_offset = 0
        self._status(f"Created: {name}")

    def _open_confirm(self, title: str, subtitle: str, on_confirm):
        """Open a yes/no confirmation modal."""
        self._input_modal = {
            "mode":     "confirm",
            "title":    title,
            "subtitle": subtitle,
            "callback": on_confirm,
            "buf":      "",   # unused in confirm mode
        }

    def _cb_delete_playlist(self):
        if self._pl_idx > 0:
            self.plm.delete(self._pl_idx)
            self._pl_idx = max(0, self._pl_idx - 1)
            self._track_idx = 0; self._scroll_offset = 0
            self._status("Playlist deleted")

    # ── Playlist settings ─────────────────────────────────────────────────────

    def _open_pl_settings(self, pl):
        items = ["Add songs", "Remove songs", "Rename playlist"]
        if not pl.is_library:
            items.append("Delete playlist")
        self._input_modal = {
            "mode":     "pl_settings",
            "pl_id":    pl.id,
            "items":    items,
            "cursor":   0,
        }

    def _handle_pl_settings_key(self, key: int):
        m = self._input_modal
        items = m["items"]
        if key == KEY_ESC:
            self._input_modal = None
        elif key == curses.KEY_UP:
            m["cursor"] = max(0, m["cursor"] - 1)
        elif key == curses.KEY_DOWN:
            m["cursor"] = min(len(items) - 1, m["cursor"] + 1)
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            choice = items[m["cursor"]]
            pl     = next((p for p in self.plm.playlists if p.id == m["pl_id"]), None)
            self._input_modal = None
            if not pl:
                return
            if choice == "Add songs":
                self._open_pl_add(pl)
            elif choice == "Remove songs":
                self._open_pl_remove(pl)
            elif choice == "Rename playlist":
                self._open_modal(f"Rename '{pl.name}':", lambda name: self._cb_rename(pl, name))
            elif choice == "Delete playlist":
                self._open_confirm(
                    f"Delete '{pl.name}'?",
                    "This cannot be undone.",
                    self._cb_delete_playlist
                )

    def _open_pl_add(self, pl):
        """Show library tracks not already in this playlist."""
        library   = self.plm.playlists[0]
        pl_paths  = {t.path for t in pl.tracks}
        available = [t for t in library.tracks if t.path not in pl_paths]
        self._input_modal = {
            "mode":      "pl_add_songs",
            "pl_id":     pl.id,
            "tracks":    available,
            "cursor":    0,
            "scroll":    0,
            "selected":  set(),
        }

    def _handle_pl_add_key(self, key: int):
        m      = self._input_modal
        tracks = m["tracks"]
        n      = len(tracks)
        if key == KEY_ESC:
            self._input_modal = None
        elif key == curses.KEY_UP:
            m["cursor"] = max(0, m["cursor"] - 1)
        elif key == curses.KEY_DOWN:
            m["cursor"] = min(n - 1, m["cursor"] + 1)
        elif key == ord(' '):
            # Toggle selection
            if m["cursor"] in m["selected"]:
                m["selected"].discard(m["cursor"])
            else:
                m["selected"].add(m["cursor"])
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            pl = next((p for p in self.plm.playlists if p.id == m["pl_id"]), None)
            if pl:
                added = 0
                for i in sorted(m["selected"]):
                    if 0 <= i < len(tracks):
                        pl.add_track(tracks[i])
                        added += 1
                self._status(f"Added {added} track{'s' if added != 1 else ''} to {pl.name}")
            self._input_modal = None

    def _open_pl_remove(self, pl):
        self._input_modal = {
            "mode":     "pl_remove_songs",
            "pl_id":    pl.id,
            "tracks":   list(pl.tracks),
            "cursor":   0,
            "selected": set(),
        }

    def _handle_pl_remove_key(self, key: int):
        m      = self._input_modal
        tracks = m["tracks"]
        n      = len(tracks)
        if key == KEY_ESC:
            self._input_modal = None
        elif key == curses.KEY_UP:
            m["cursor"] = max(0, m["cursor"] - 1)
        elif key == curses.KEY_DOWN:
            m["cursor"] = min(n - 1, m["cursor"] + 1)
        elif key == ord(' '):
            if m["cursor"] in m["selected"]:
                m["selected"].discard(m["cursor"])
            else:
                m["selected"].add(m["cursor"])
        elif key in (KEY_ENTER, curses.KEY_ENTER):
            pl = next((p for p in self.plm.playlists if p.id == m["pl_id"]), None)
            if pl and m["selected"]:
                # Remove in reverse order so indices stay valid
                for i in sorted(m["selected"], reverse=True):
                    if 0 <= i < len(pl.tracks):
                        pl.remove_track(i)
                removed = len(m["selected"])
                self._status(f"Removed {removed} track{'s' if removed != 1 else ''}")
            self._input_modal = None

    def _cb_rename(self, pl, name: str):
        self.plm.rename(self.plm.playlists.index(pl), name)
        self._status(f"Renamed to '{name}'")

    # ══════════════════════════════════════════════════════════════════════════
    # PLAYLIST SETTINGS VIEW
    # ══════════════════════════════════════════════════════════════════════════

    _SETTINGS_MENU = ["Add songs", "Remove songs", "Rename", "Delete playlist"]

    def _handle_settings_view_key(self, key: int):
        pl = self._settings_pl
        if not pl:
            self._view = VIEW_PLAYER
            return

        sub = self._settings_sub

        if sub is None:
            # Main menu navigation
            if key in (KEY_ESC, ord('q'), ord('e')):
                self._view = VIEW_PLAYER
            elif key == curses.KEY_UP:
                self._settings_cur = max(0, self._settings_cur - 1)
            elif key == curses.KEY_DOWN:
                self._settings_cur = min(len(self._SETTINGS_MENU) - 1,
                                         self._settings_cur + 1)
            elif key in (KEY_ENTER, curses.KEY_ENTER, ord(' ')):
                choice = self._SETTINGS_MENU[self._settings_cur]
                if choice == "Add songs":
                    lib_paths = {t.path for t in pl.tracks}
                    self._picker_tracks = [t for t in self.plm.playlists[0].tracks
                                           if t.path not in lib_paths]
                    self._picker_cur  = 0
                    self._picker_sel  = set()
                    self._settings_sub = "add"
                elif choice == "Remove songs":
                    self._picker_tracks = list(pl.tracks)
                    self._picker_cur  = 0
                    self._picker_sel  = set()
                    self._settings_sub = "remove"
                elif choice == "Rename":
                    self._open_modal(f"Rename '{pl.name}':",
                                     lambda name: self._cb_rename_pl(pl, name))
                    self._view = VIEW_PLAYER
                elif choice == "Delete playlist":
                    self._open_confirm(
                        f"Delete '{pl.name}'?",
                        "This cannot be undone.",
                        self._cb_delete_playlist
                    )
                    self._view = VIEW_PLAYER
        else:
            # Picker (add or remove) navigation
            n = len(self._picker_tracks)
            if key in (KEY_ESC,):
                self._settings_sub = None
                self._picker_sel   = set()
            elif key == curses.KEY_UP:
                self._picker_cur = max(0, self._picker_cur - 1)
            elif key == curses.KEY_DOWN:
                self._picker_cur = min(max(0, n - 1), self._picker_cur + 1)
            elif key == ord(' '):
                if self._picker_cur in self._picker_sel:
                    self._picker_sel.discard(self._picker_cur)
                else:
                    self._picker_sel.add(self._picker_cur)
            elif key in (KEY_ENTER, curses.KEY_ENTER):
                if sub == "add":
                    added = 0
                    for i in sorted(self._picker_sel):
                        if 0 <= i < len(self._picker_tracks):
                            pl.add_track(self._picker_tracks[i])
                            added += 1
                    self._status(f"Added {added} track(s) to {pl.name}")
                elif sub == "remove":
                    for i in sorted(self._picker_sel, reverse=True):
                        if 0 <= i < len(pl.tracks):
                            pl.remove_track(i)
                    self._status(f"Removed {len(self._picker_sel)} track(s)")
                self._settings_sub = None
                self._picker_sel   = set()

    def _cb_rename_pl(self, pl, name: str):
        try:
            idx = self.plm.playlists.index(pl)
            self.plm.rename(idx, name)
            self._status(f"Renamed to '{name}'")
        except ValueError:
            pass

    def _draw_pl_settings_view(self, win, h: int, w: int):
        """Full-screen playlist settings — drawn like VIEW_HELP, no modal layer."""
        win.erase()
        pl  = self._settings_pl
        sub = self._settings_sub

        # Outer box
        ab = cattr(C_BORDER, dim=True)
        safe_addstr(win, 0,     0, "╔" + "═"*(w-2) + "╗", ab)
        safe_addstr(win, h - 1, 0, "╚" + "═"*(w-2) + "╝", ab)
        for y in range(1, h - 1):
            safe_addstr(win, y, 0, "║", ab)
            safe_addstr(win, y, w-1, "║", ab)

        # Title
        title = f"  ⚙  {pl.name}  " if pl else "  ⚙  Settings  "
        safe_addstr(win, 0, (w - len(title)) // 2, title,
                    cattr(C_THEME_TITLE, bold=True))

        if sub is None:
            # Main menu
            safe_addstr(win, 2, 4, f"Playlist:  {pl.name}", cattr(C_THEME_ARTIST))
            safe_addstr(win, 3, 4, f"Tracks:    {len(pl.tracks)}", cattr(C_DIM))
            hline(win, 4, 1, w-2, "─", cattr(C_BORDER, dim=True))

            for i, item in enumerate(self._SETTINGS_MENU):
                y    = 6 + i * 2
                sel  = (i == self._settings_cur)
                if sel:
                    safe_addstr(win, y, 2, " "*( w-4), cattr(C_SELECTED))
                    safe_addstr(win, y, 4, f"▶  {item}", cattr(C_SELECTED, bold=True))
                else:
                    safe_addstr(win, y, 4, f"   {item}", cattr(C_DIM))

            hline(win, h-3, 1, w-2, "─", cattr(C_BORDER, dim=True))
            hints = "  ↑↓ navigate    ENTER select    ESC / e  close  "
            safe_addstr(win, h-2, (w - len(hints)) // 2, hints, cattr(C_DIM))

        else:
            # Picker
            action = "Add to" if sub == "add" else "Remove from"
            hdr    = f"  {action}: {pl.name}"
            safe_addstr(win, 2, 2, hdr, cattr(C_THEME_TITLE, bold=True))
            hline(win, 3, 1, w-2, "─", cattr(C_BORDER, dim=True))

            tracks  = self._picker_tracks
            visible = h - 8
            scroll  = max(0, min(self._picker_cur - visible//2,
                                  max(0, len(tracks) - visible)))

            if not tracks:
                msg = "No tracks available"
                safe_addstr(win, h//2, (w-len(msg))//2, msg, cattr(C_DIM))
            else:
                for row in range(visible):
                    ti = row + scroll
                    if ti >= len(tracks):
                        break
                    t   = tracks[ti]
                    ry  = 4 + row
                    is_cur = (ti == self._picker_cur)
                    is_sel = (ti in self._picker_sel)
                    chk    = "◉" if is_sel else "○"
                    line   = _trunc(f" {chk}  {t.display_title}  ·  {t.display_artist}",
                                    w - 4)
                    if is_cur:
                        safe_addstr(win, ry, 2, " "*(w-4), cattr(C_SELECTED))
                        safe_addstr(win, ry, 2, line, cattr(C_SELECTED, bold=True))
                    elif is_sel:
                        safe_addstr(win, ry, 2, line, cattr(C_THEME_BAR, bold=True))
                    else:
                        safe_addstr(win, ry, 2, line, cattr(C_DIM))

            count = f"  {len(self._picker_sel)} selected  "
            safe_addstr(win, h-3, w - len(count) - 2, count,
                        cattr(C_THEME_ARTIST, bold=True))
            hline(win, h-3, 1, w-2, "─", cattr(C_BORDER, dim=True))
            hints = "  ↑↓ navigate    SPC toggle    ENTER confirm    ESC back  "
            safe_addstr(win, h-2, (w - len(hints)) // 2, hints, cattr(C_DIM))

    def _scroll_clamp(self, visible: int = 0):
        if visible <= 0:
            return
        pl = self._current_playlist()
        n  = len(pl.tracks) if pl else 0
        if self._track_idx - self._scroll_offset >= visible:
            self._scroll_offset = self._track_idx - visible + 1
        if self._track_idx < self._scroll_offset:
            self._scroll_offset = self._track_idx
        self._scroll_offset = max(0, min(self._scroll_offset, max(0, n - visible)))

    def _status(self, msg: str):
        self._status_msg = msg
        self._status_ts  = time.time()

    # ══════════════════════════════════════════════════════════════════════════
    # Drawing dispatcher
    # ══════════════════════════════════════════════════════════════════════════

    # Drawing
    def _draw(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        if w < 80 or h < 26:
            safe_addstr(stdscr, 0, 0,
                        "Terminal too small — resize to at least 80×26",
                        cattr(C_BAR_HIGH, bold=True))
            stdscr.refresh()
            return

        if self._view == VIEW_HELP:
            self._draw_help(stdscr, h, w)
        elif self._view == VIEW_IDLE:
            self._draw_idle(stdscr, h, w)
        else:
            self._draw_player(stdscr, h, w)

        stdscr.refresh()

    # ══════════════════════════════════════════════════════════════════════════
    # IDLE VIEW — full-screen art with song info overlay + bottom visualizer
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_idle(self, win, h: int, w: int):
        """
        Full-screen idle: large text-ramp ASCII art filling as much of the
        terminal as possible, then song info + progress + visualizer below.

        Layout:
          rows 0 .. art_h-1    :  ASCII art  (@#%*+=-.  chars, coloured)
          art_h                :  title centred
          art_h+1              :  artist · album centred
          art_h+2              :  full-width progress bar
          h-viz_h-1            :  thin divider line
          h-viz_h .. h-2       :  visualizer bars
          h-1                  :  "press any key" hint
        """
        # Use the PLAYING track for idle art, not the cursor selection
        track = self._playing_track()
        cover = track.cover_path if track else None

        viz_h     = 6
        info_rows = 3   # title + second line + progress

        # Rows available for art
        art_h_avail = h - info_rows - viz_h - 2

        # Braille cells are naturally square (2px wide * 4px tall, terminal
        # aspect ~2:1 → each cell is 4×4 display units).
        # So art_w columns × art_h rows gives a square-looking render when
        # art_w == art_h * 2  (same as normal ASCII, but 4× the pixel density).
        art_h = min(art_h_avail, w // 2)
        art_w = art_h * 2
        art_w = min(art_w, w)
        art_w = max(4, art_w)
        art_h = max(2, art_h)

        cache_key = f"idle:{cover}:{art_w}:{art_h}"
        if cache_key not in self._art_cache:
            self._art_cache[cache_key] = ascii_art_lines(cover, art_w, art_h)
        rows = self._art_cache[cache_key]

        art_x = max(0, (w - art_w) // 2)

        # Idle art colour pairs: slots 430-629 (completely separate from normal
        # art slots 30-229 so they never clobber each other).
        for row_i, row in enumerate(rows[:art_h]):
            ry = row_i
            if ry >= art_h:
                break
            for col_i, (ch, r, g, b) in enumerate(row[:art_w]):
                c256 = max(24, min(255, self._rgb_to_256(r, g, b)))
                safe_addstr(win, ry, art_x + col_i,
                            ch, curses.color_pair(c256))

        # Info strip
        info_y = art_h

        if track:
            title  = track.display_title
            artist = track.display_artist
            album  = track.album or ""
            second = f"{artist}  ·  {album}" if album else artist
        else:
            title  = "Nothing playing"
            second = ""

        safe_addstr(win, info_y,
                    max(0, (w - len(title)) // 2),
                    _trunc(title, w - 2),
                    cattr(C_THEME_TITLE, bold=True))
        safe_addstr(win, info_y + 1,
                    max(0, (w - len(second)) // 2),
                    _trunc(second, w - 2),
                    cattr(C_DIM))

        pos_s    = format_duration(self.player.position)
        dur_s    = format_duration(self.player.duration)
        time_str = f" {pos_s} / {dur_s} "
        draw_progress_bar(win, info_y + 2, 2, w - 4,
                          self.player.position, self.player.duration)
        safe_addstr(win, info_y + 2,
                    max(2, (w - len(time_str)) // 2),
                    time_str, cattr(C_DIM))

        # Visualizer
        viz_y = h - viz_h - 1
        hline(win, viz_y, 0, w, "─", cattr(C_BORDER))
        self._draw_visualizer(win, viz_y + 1, 0, viz_h - 1, w)

        # Wake hint
        safe_addstr(win, h - 1,
                    max(0, (w - 28) // 2),
                    "  press any key to return  ",
                    cattr(C_DIM, dim=True))

    # ══════════════════════════════════════════════════════════════════════════
    # PLAYER VIEW
    # ══════════════════════════════════════════════════════════════════════════

    def _draw_player(self, win, h: int, w: int):
        self._draw_chrome(win, h, w)
        self._draw_sidebar_divider(win, h)

        main_x = SIDEBAR_W + 1
        main_w = w - main_x - 1

        # Layout (rows, 1-indexed inside the outer box):
        #   1 .. art_h+1        now-playing block
        #   art_h+2             divider ─ NOW PLAYING ─
        #   art_h+3 .. +viz_h   visualizer
        #   art_h+3+viz_h       divider ─ PLAYLIST NAME ─
        #   ...                 track list
        art_h   = ART_HEIGHT
        viz_h   = 10
        np_div  = 1 + art_h + 1        # row of NOW PLAYING divider
        viz_div = np_div + viz_h + 1   # row of TRACKS divider
        list_y  = viz_div + 1

        self._draw_sidebar(win, h)
        self._draw_now_playing(win, 1, main_x, art_h, main_w)
        self._draw_divider_h(win, np_div,  main_x, main_w, " NOW PLAYING ")
        self._draw_visualizer(win, np_div + 1, main_x, viz_h, main_w)

        pl_label = self._current_playlist().name.upper() \
                   if self._current_playlist() else "TRACKS"
        self._draw_divider_h(win, viz_div, main_x, main_w, f"  {pl_label}  ")

        list_h = h - list_y - 2
        self._draw_tracklist(win, list_y, main_x, list_h, main_w)
        self._draw_statusbar(win, h, w)

        if self._input_modal:
            self._draw_modal(win, h, w)
        if self._searching:
            self._draw_search_bar(win, h, w)

    # ── Chrome ────────────────────────────────────────────────────────────────

    def _draw_chrome(self, win, h, w):
        """Outer box with a very dim border that recedes behind the content."""
        a = cattr(C_BORDER, dim=True)
        safe_addstr(win, 0,     0, "╔" + "═" * (w - 2) + "╗", a)
        safe_addstr(win, h - 2, 0, "╠" + "═" * (w - 2) + "╣", a)
        safe_addstr(win, h - 1, 0, "╚" + "═" * (w - 2) + "╝", a)
        for y in range(1, h - 1):
            safe_addstr(win, y, 0,     "║", a)
            safe_addstr(win, y, w - 1, "║", a)
        # Title centred in top bar — uses theme colour so it pops
        title = TITLE_STR
        safe_addstr(win, 0, (w - len(title)) // 2,
                    title, cattr(C_THEME_TITLE, bold=True))

    def _draw_sidebar_divider(self, win, h):
        a = cattr(C_BORDER, dim=True)
        safe_addstr(win, 0,     SIDEBAR_W, "╦", a)
        for y in range(1, h - 2):
            safe_addstr(win, y, SIDEBAR_W, "║", a)
        safe_addstr(win, h - 2, SIDEBAR_W, "╬", a)

    def _draw_divider_h(self, win, y: int, x: int, w: int, label: str = ""):
        safe_addstr(win, y, x, "╠" + "─" * (w - 1), cattr(C_BORDER, dim=True))
        if label:
            safe_addstr(win, y, x + 2, label, cattr(C_THEME_ARTIST, bold=True))

    # ── Sidebar ───────────────────────────────────────────────────────────────

    # Sidebar
    def _draw_sidebar(self, win, h: int):
        safe_addstr(win, 1, 1, " PLAYLISTS ", cattr(C_HEADER, bold=True))
        hline(win, 2, 1, SIDEBAR_W - 1, "─", cattr(C_BORDER, dim=True))

        max_rows = h - 8
        for i, pl in enumerate(self.plm.playlists[:max_rows]):
            y      = 3 + i
            active = (i == self._pl_idx)
            sel    = active and self._focus == 0

            if sel:
                # Fixed white-on-dark — unambiguous regardless of theme
                attr   = cattr(C_SELECTED, bold=True)
                prefix = " ▶ "
            elif active:
                attr   = cattr(C_THEME_TITLE, bold=True)
                prefix = " ♪ "
            else:
                attr   = cattr(C_DIM)
                prefix = "   "

            if pl.is_library:
                safe_addstr(win, y, 1, _trunc(prefix + pl.name, SIDEBAR_W - 6), attr)
                safe_addstr(win, y, SIDEBAR_W - 4,
                            f"{len(pl.tracks):>3}", cattr(C_DIM, dim=True))
            else:
                safe_addstr(win, y, 1, _trunc(prefix + pl.name, SIDEBAR_W - 6), attr)



    # ── Now-playing panel ─────────────────────────────────────────────────────

    def _draw_now_playing(self, win, y: int, x: int, h: int, w: int):
        # Show the PLAYING track in the now-playing panel.
        # Small cover follows the cursor so you can preview art while browsing.
        # Only the idle fullscreen view shows the playing track's art.
        track = self._current_track()
        cover = track.cover_path if track else None

        # Art
        cache_key = f"{cover}:{ART_WIDTH}:{ART_HEIGHT}"
        if cache_key not in self._art_cache:
            self._art_cache[cache_key] = ascii_art_lines(cover, ART_WIDTH, ART_HEIGHT)
        rows = self._art_cache[cache_key]

        art_x = x + 1
        # Render art as coloured space characters (bg = pixel colour).
        # This makes dark pixels visible — a dark char on dark bg is invisible,
        # but a space with a dark background is a solid coloured block.
        # We deduplicate by xterm-256 index: one curses pair per unique colour.
        # Pairs allocated in slots 30-229 (200 slots), keyed by xterm-256 index.
        # Pairs 24-255 pre-initialised: init_pair(n, n, -1).
        # Art = just color_pair(c256). No allocator, no overflow, no bleed.
        for row_i, row in enumerate(rows[:ART_HEIGHT]):
            for col_i, (ch, r, g, b) in enumerate(row[:ART_WIDTH]):
                c256 = max(24, min(255, self._rgb_to_256(r, g, b)))
                safe_addstr(win, y + row_i, art_x + col_i,
                            ch, curses.color_pair(c256))

        # Info column — right of art
        info_x = art_x + ART_WIDTH + 2
        info_w = w - ART_WIDTH - 4

        title  = track.display_title  if track else "─  no track  ─"
        artist = track.display_artist if track else ""
        album  = (track.album or "")  if track else ""

        safe_addstr(win, y + 0, info_x, _trunc(title,  info_w), cattr(C_THEME_TITLE, bold=True))
        safe_addstr(win, y + 1, info_x, _trunc(artist, info_w), cattr(C_THEME_ARTIST))
        safe_addstr(win, y + 2, info_x, _trunc(album,  info_w), cattr(C_DIM))

        # State badge + shuffle/repeat on same row
        badge = " ▶ PLAYING " if self.player.playing else " ⏸ PAUSED  "
        badge_attr = cattr(C_THEME_TITLE, bold=True) if self.player.playing \
                     else cattr(C_DIM, bold=True)
        safe_addstr(win, y + 3, info_x, badge, badge_attr)

        shuf_sym  = "⇌ SHF" if self._shuffle else "  SHF"
        shuf_attr = cattr(C_THEME_BAR, bold=True) if self._shuffle else cattr(C_DIM)
        rep_sym   = {"none": "─", "one": "↺", "all": "⟳"}[self._repeat]
        rep_attr  = cattr(C_THEME_BAR, bold=True) if self._repeat != "none" else cattr(C_DIM)
        safe_addstr(win, y + 3, info_x + 12, shuf_sym,       shuf_attr)
        safe_addstr(win, y + 3, info_x + 18, f" RPT:{rep_sym}", rep_attr)

        # ── Timeline (row 4) ───────────────────────────────────────────────
        pos_s     = format_duration(self.player.position)
        remaining = self.player.duration - self.player.position
        rem_s     = "-" + format_duration(remaining) if self.player.duration > 0 else "--:--"
        bar_w     = max(10, info_w - 2)
        draw_timeline(win, y + 4, info_x, bar_w,
                      self.player.position, self.player.duration,
                      pos_s, rem_s)

        # ── Volume (row 6 — blank row 5 separates them) ─────────────────────
        draw_volume_inline(win, y + 6, info_x, info_w,
                           self.cfg.get("volume", 80))

    # ── Visualizer ────────────────────────────────────────────────────────────

    def _draw_visualizer(self, win, y: int, x: int, h: int, w: int):
        """
        CAVA-quality renderer:
          - Each bar = 2 fill columns + 1 gap column (CAVA default style)
          - Sub-cell vertical resolution via ▁▂▃▄▅▆▇█
          - Per-row colour gradient: bottom rows = dim, top rows = bright
          - Gravity dot: ▀ rises instantly, holds 6 frames, falls 2 sub/frame
          - All empty cells explicitly cleared for clean redraws
        """
        if not self.cfg.get("visualizer", True):
            safe_addstr(win, y + h // 2, x + 1, "─" * (w - 2), cattr(C_BORDER))
            return

        bars = self.viz.bars if self.viz.active else self._fake_bars()
        if not bars:
            return

        n_bars = len(bars)
        avail  = w - 2   # drawable columns (inside border)

        # Bar layout: each bar = BAR_W fill columns + 1 gap column
        # Choose BAR_W so n_bars * (BAR_W + 1) <= avail
        # Try BAR_W=2 first, fall back to 1 if terminal is narrow
        BAR_W  = 2 if n_bars * 3 <= avail else 1
        GAP    = 1
        STEP   = BAR_W + GAP
        # How many bars actually fit
        fit    = min(n_bars, avail // STEP)
        # Left margin to centre the bar block
        total_used = fit * STEP - GAP   # last bar has no trailing gap
        margin = max(0, (avail - total_used) // 2)

        # Peak dot state
        if not hasattr(self, "_viz_peaks") or len(self._viz_peaks) != n_bars:
            self._viz_peaks = [0.0] * n_bars
            self._viz_holds = [0]   * n_bars

        # Sub-cell block chars (index 0 = empty, 8 = full)
        BLOCKS = " ▁▂▃▄▅▆▇█"

        # Per-row colour: bottom third = artist (dim), mid = bar, top = title
        def row_attr(row: int) -> int:
            frac = row / max(1, h - 1)
            if frac > 0.66:
                return cattr(C_THEME_TITLE, bold=True)
            elif frac > 0.33:
                return cattr(C_THEME_BAR, bold=True)
            else:
                return cattr(C_THEME_ARTIST, bold=False)

        peak_attr = cattr(C_THEME_TITLE, bold=True)

        # Clear entire visualizer region first for clean redraws
        for row in range(h):
            cy = y + row
            safe_addstr(win, cy, x + 1, " " * avail, 0)

        for i in range(fit):
            val   = max(0.0, min(1.0, bars[i]))
            sub_h = val * h * 8
            full  = int(sub_h) >> 3
            frac  = int(sub_h) & 7

            # Peak dot physics
            if sub_h >= self._viz_peaks[i]:
                self._viz_peaks[i] = sub_h
                self._viz_holds[i] = 6
            else:
                if self._viz_holds[i] > 0:
                    self._viz_holds[i] -= 1
                else:
                    self._viz_peaks[i] = max(0.0, self._viz_peaks[i] - 2.0)
            peak_row = min(int(self._viz_peaks[i]) >> 3, h - 1)

            # Column positions for this bar's fill cells
            col_start = x + 1 + margin + i * STEP

            for b in range(BAR_W):
                col = col_start + b
                if col >= x + w - 1:
                    break

                # Draw rows bottom-up
                for row in range(h):
                    cy   = y + (h - 1 - row)
                    attr = row_attr(row)
                    if row < full:
                        safe_addstr(win, cy, col, "█", attr)
                    elif row == full and frac > 0:
                        safe_addstr(win, cy, col, BLOCKS[frac], attr)

                # Peak dot one row above the bar
                if peak_row > full and peak_row > 0:
                    cy_peak = y + (h - 1 - peak_row)
                    safe_addstr(win, cy_peak, col, "▀", peak_attr)

    def _fake_bars(self) -> list[float]:
        """
        Animated fallback when no capture backend is available.
        Returns 2*FFT_BINS bars in the same mirrored layout the visualizer uses:
        [reversed_L | R] so the fake animation also looks symmetric.
        """
        if not self.player.playing:
            return [0.0] * (FFT_BINS * 2)
        t = time.time()
        half = [
            max(0.0, min(1.0,
                abs(math.sin(t * 2.3 + i * 0.29)) * 0.55 +
                abs(math.sin(t * 1.1 + i * 0.19)) * 0.30 +
                abs(math.sin(t * 3.7 + i * 0.47)) * 0.15
            ))
            for i in range(FFT_BINS)
        ]
        return half[::-1] + half

    # ── Track list ────────────────────────────────────────────────────────────

    def _draw_tracklist(self, win, y: int, x: int, h: int, w: int):
        pl = self._current_playlist()
        if not pl or h <= 0:
            return

        visible = h
        n       = len(pl.tracks)
        self._scroll_clamp(visible)

        # Reserve space: 1 char border left, 1 right, scrollbar takes col w-2
        # Duration sits at fixed right position: x + w - 6  (e.g. "3:47")
        dur_col = x + w - 7   # right-aligned duration

        for row in range(visible):
            ti    = row + self._scroll_offset
            if ti >= n:
                break
            track = pl.tracks[ti]
            ry    = y + row

            is_sel     = (ti == self._track_idx and self._focus == 1)
            is_playing = (self.player.playing and ti == self._track_idx)

            if is_sel:
                # Fixed high-contrast selection — white text on dark bg
                row_attr = cattr(C_SELECTED, bold=True)
            elif is_playing:
                row_attr = cattr(C_THEME_TITLE, bold=True)
            else:
                row_attr = cattr(C_DIM)

            now_sym = "♪ " if is_playing else "  "
            num_s   = f"{ti + 1:>3}."
            dur     = format_duration(track.duration)

            # Available width for title + artist (between num and duration)
            text_left  = x + 1 + len(now_sym) + len(num_s) + 1
            text_right = dur_col - 2
            text_w     = max(0, text_right - text_left)

            title_part  = track.display_title
            artist_part = track.display_artist
            # Build "Title · Artist" fitting the available space
            sep = "  ·  "
            combined = f"{title_part}{sep}{artist_part}"
            if len(combined) > text_w:
                # Try to fit title + truncated artist
                budget = text_w - len(sep)
                t_w = max(4, budget * 2 // 3)
                a_w = max(4, budget - t_w)
                combined = _trunc(title_part, t_w) + sep + _trunc(artist_part, a_w)

            # Clear row background
            safe_addstr(win, ry, x + 1, " " * (w - 3), row_attr if is_sel else cattr(C_DIM))

            safe_addstr(win, ry, x + 1,    now_sym, row_attr)
            safe_addstr(win, ry, x + 1 + len(now_sym), num_s, row_attr)
            safe_addstr(win, ry, text_left, combined, row_attr)
            safe_addstr(win, ry, dur_col,   dur,      row_attr)

        # Scrollbar
        if n > visible and visible > 1:
            sb_x  = x + w - 2
            ratio = self._scroll_offset / max(1, n - visible)
            thumb = int(ratio * (visible - 1))
            for r in range(visible):
                safe_addstr(win, y + r, sb_x,
                            "█" if r == thumb else "░",
                            cattr(C_THEME_BAR))

    # ── Status bar ────────────────────────────────────────────────────────────

    def _draw_statusbar(self, win, h: int, w: int):
        y = h - 2
        if self._status_msg and time.time() - self._status_ts < 4.0:
            safe_addstr(win, y, 1,
                        f"  ⚡  {self._status_msg}"[:w - 2],
                        cattr(C_THEME_BAR, bold=True))
            return

        # Render a grouped status bar:  key=bright  label=dim  ·=separator
        # Groups: PLAYBACK | NAVIGATE | LIBRARY | APP
        groups = [
            [("SPC", "pause"), ("←→", "skip"), ("[/]", "seek"), ("+/-", "vol")],
            [("s", "shuffle"), ("r", "repeat")],
            [("TAB", "focus"), ("/", "search"), ("c", "new pl"), ("e", "pl settings")],
            [("?", "help"), ("q", "quit")],
        ]
        sep_attr  = cattr(C_BORDER, dim=True)
        key_attr  = cattr(C_THEME_BAR, bold=True)
        lbl_attr  = cattr(C_DIM)

        col = 2
        for g_idx, group in enumerate(groups):
            if g_idx > 0:
                # Group separator
                if col + 3 < w - 2:
                    safe_addstr(win, y, col, " ┆ ", sep_attr)
                    col += 3
            for key_str, lbl_str in group:
                needed = len(key_str) + 1 + len(lbl_str) + 1
                if col + needed >= w - 2:
                    break
                safe_addstr(win, y, col,                  key_str, key_attr)
                safe_addstr(win, y, col + len(key_str),   " ",     lbl_attr)
                safe_addstr(win, y, col + len(key_str)+1, lbl_str, lbl_attr)
                col += needed

    # ── Help view ─────────────────────────────────────────────────────────────

    def _draw_help(self, win, h: int, w: int):
        self._draw_chrome(win, h, w)
        safe_addstr(win, 0, (w - 26) // 2,
                    "  ♪  T U N A  —  H E L P  ",
                    cattr(C_THEME_TITLE, bold=True))

        for i, line in enumerate(LOGO_LINES):
            safe_addstr(win, 2 + i, (w - len(line)) // 2,
                        line, cattr(C_THEME_TITLE, bold=True))

        # Two-column layout: left = PLAYBACK, right = INTERFACE
        items_left = [
            ("SPACE",    "Play / Pause"),
            ("← / →",   "Previous / Next track"),
            ("[ / ]",   "Seek  −5 / +5 s"),
            ("+ / =",   "Volume up"),
            ("−",       "Volume down"),
            ("s",       "Shuffle on/off"),
            ("r",       "Repeat  none → one → all"),
            ("ENTER",   "Play selected track"),
        ]
        items_right = [
            ("↑ / ↓",   "Navigate list"),
            ("TAB",     "Sidebar ↔ track list"),
            ("PgUp/Dn", "Jump 10 tracks"),
            ("Home/End","First / Last"),
            ("/",       "Search"),
            ("c",       "New playlist"),
            ("x",       "Delete playlist (sidebar)"),
            ("?",       "This help"),
            ("q",       "Quit"),
        ]
        col1 = 4; col2 = 20
        sy   = 2 + len(LOGO_LINES) + 2
        mid = w // 2
        # Left header
        safe_addstr(win, sy, col1, "PLAYBACK", cattr(C_HEADER, bold=True))
        hline(win, sy + 1, col1, mid - col1 - 2, "─", cattr(C_BORDER, dim=True))
        # Right header
        safe_addstr(win, sy, mid + col1, "INTERFACE", cattr(C_HEADER, bold=True))
        hline(win, sy + 1, mid + col1, w - mid - col1 - 4, "─", cattr(C_BORDER, dim=True))

        rows = max(len(items_left), len(items_right))
        for i in range(rows):
            yy = sy + 2 + i
            if yy >= h - 3:
                break
            if i < len(items_left):
                k, v = items_left[i]
                safe_addstr(win, yy, col1,      k, cattr(C_THEME_BAR, bold=True))
                safe_addstr(win, yy, col2,      v, cattr(C_DIM))
            if i < len(items_right):
                k, v = items_right[i]
                safe_addstr(win, yy, mid + col1, k, cattr(C_THEME_BAR, bold=True))
                safe_addstr(win, yy, mid + col2, v, cattr(C_DIM))
        safe_addstr(win, h - 2, (w - 24) // 2,
                    "  press any key to close  ", cattr(C_DIM))

    # ── Modal ─────────────────────────────────────────────────────────────────

    def _draw_modal(self, win, h: int, w: int):
        m    = self._input_modal
        mode = m.get("mode", "text")

        if mode == "pl_settings":
            self._draw_pl_settings(win, h, w)
            return
        if mode == "pl_add_songs":
            self._draw_pl_track_picker(win, h, w, adding=True)
            return
        if mode == "pl_remove_songs":
            self._draw_pl_track_picker(win, h, w, adding=False)
            return

        mw = 52; mh = 7
        mx = (w - mw) // 2; my = (h - mh) // 2
        ab = cattr(C_BORDER)
        safe_addstr(win, my,          mx, "╔" + "═" * (mw - 2) + "╗", ab)
        for r in range(1, mh - 1):
            safe_addstr(win, my + r,  mx, "║" + " " * (mw - 2) + "║", ab)
        safe_addstr(win, my + mh - 1, mx, "╚" + "═" * (mw - 2) + "╝", ab)

        if mode == "confirm":
            safe_addstr(win, my + 1, mx + 2,
                        _trunc(m["title"], mw - 4),
                        cattr(C_THEME_TITLE, bold=True))
            safe_addstr(win, my + 2, mx + 2,
                        _trunc(m["subtitle"], mw - 4),
                        cattr(C_DIM))
            hline(win, my + 3, mx + 1, mw - 2, "─", cattr(C_BORDER))
            safe_addstr(win, my + 4, mx + 2, "  Y  yes, delete it",
                        cattr(C_BAR_HIGH, bold=True))
            safe_addstr(win, my + 5, mx + 2, "  N  no, keep it", cattr(C_DIM))
        else:
            safe_addstr(win, my + 1, mx + 2, m["prompt"], cattr(C_THEME_TITLE))
            buf_line = m["buf"] + "▌"
            safe_addstr(win, my + 3, mx + 2, "  " + buf_line[:mw - 6],
                        cattr(C_SELECTED, bold=True))

    def _draw_pl_settings(self, win, h: int, w: int):
        m     = self._input_modal
        items = m["items"]
        mw    = 36
        mh    = len(items) + 5
        mx    = (w - mw) // 2
        my    = (h - mh) // 2
        ab    = cattr(C_BORDER)

        pl = next((p for p in self.plm.playlists if p.id == m["pl_id"]), None)
        title = f" ⚙  {pl.name} " if pl else " ⚙ Playlist "

        safe_addstr(win, my,          mx, "╔" + "═" * (mw - 2) + "╗", ab)
        safe_addstr(win, my + 1,      mx, "║" + _trunc(title, mw - 2).center(mw - 2) + "║",
                    cattr(C_THEME_TITLE, bold=True))
        hline(win, my + 2, mx + 1, mw - 2, "─", ab)
        for r in range(3, mh - 2):
            safe_addstr(win, my + r, mx, "║" + " " * (mw - 2) + "║", ab)
        hline(win, my + mh - 2, mx + 1, mw - 2, "─", ab)
        safe_addstr(win, my + mh - 1, mx, "╚" + "═" * (mw - 2) + "╝", ab)
        safe_addstr(win, my + mh - 1, mx + 2, " ESC cancel ", cattr(C_DIM))

        for i, item in enumerate(items):
            ry   = my + 3 + i
            sel  = (i == m["cursor"])
            if sel:
                safe_addstr(win, ry, mx + 1, " " * (mw - 2), cattr(C_SELECTED))
                safe_addstr(win, ry, mx + 3, f"▶  {item}", cattr(C_SELECTED, bold=True))
            else:
                safe_addstr(win, ry, mx + 3, f"   {item}", cattr(C_DIM))

    def _draw_pl_track_picker(self, win, h: int, w: int, adding: bool):
        """Shared draw for add/remove song pickers."""
        m       = self._input_modal
        tracks  = m["tracks"]
        cursor  = m["cursor"]
        sel_set = m["selected"]
        mw      = min(w - 4, 72)
        mh      = min(h - 4, 24)
        mx      = (w - mw) // 2
        my      = (h - mh) // 2
        ab      = cattr(C_BORDER)
        visible = mh - 6   # rows available for track list

        title = " + Add songs " if adding else " − Remove songs "

        # Scroll offset
        scroll = max(0, min(cursor - visible // 2, max(0, len(tracks) - visible)))
        m["scroll"] = scroll

        # Box
        safe_addstr(win, my, mx, "╔" + "═" * (mw - 2) + "╗", ab)
        safe_addstr(win, my + 1, mx,
                    "║" + title.center(mw - 2) + "║",
                    cattr(C_THEME_TITLE, bold=True))
        hline(win, my + 2, mx + 1, mw - 2, "─", ab)
        for r in range(3, mh - 3):
            safe_addstr(win, my + r, mx, "║" + " " * (mw - 2) + "║", ab)
        hline(win, my + mh - 3, mx + 1, mw - 2, "─", ab)
        safe_addstr(win, my + mh - 2, mx, "║" + " " * (mw - 2) + "║", ab)
        safe_addstr(win, my + mh - 1, mx, "╚" + "═" * (mw - 2) + "╝", ab)

        # Instructions
        safe_addstr(win, my + mh - 2, mx + 2,
                    " SPC toggle   ENTER confirm   ESC cancel ",
                    cattr(C_DIM))

        if not tracks:
            msg = "No tracks available"
            safe_addstr(win, my + mh // 2, mx + (mw - len(msg)) // 2, msg, cattr(C_DIM))
            return

        for row in range(visible):
            ti = row + scroll
            if ti >= len(tracks):
                break
            track = tracks[ti]
            ry    = my + 3 + row
            is_cursor   = (ti == cursor)
            is_selected = (ti in sel_set)

            check = "◉ " if is_selected else "○ "
            line  = _trunc(f"{check}{track.display_title}  ·  {track.display_artist}",
                           mw - 4)

            if is_cursor:
                safe_addstr(win, ry, mx + 1, " " * (mw - 2), cattr(C_SELECTED))
                safe_addstr(win, ry, mx + 2, line, cattr(C_SELECTED, bold=True))
            elif is_selected:
                safe_addstr(win, ry, mx + 2, line, cattr(C_THEME_BAR, bold=True))
            else:
                safe_addstr(win, ry, mx + 2, line, cattr(C_DIM))

        # Selection count
        count_str = f" {len(sel_set)} selected "
        safe_addstr(win, my + mh - 3, mx + mw - len(count_str) - 2,
                    count_str, cattr(C_THEME_ARTIST, bold=True))

    # ── Search bar ────────────────────────────────────────────────────────────

    def _draw_search_bar(self, win, h: int, w: int):
        y = h - 2
        bar = f"  /  {self._search_buf}▌"
        safe_addstr(win, y, 1, " " * (w - 2),  cattr(C_SELECTED))
        safe_addstr(win, y, 1, bar[:w - 2],     cattr(C_SELECTED, bold=True))

    # ── Colour helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _rgb_to_256(r: int, g: int, b: int) -> int:
        """Map 24-bit RGB to nearest xterm-256 colour index. No clamping."""
        gray = int(0.299 * r + 0.587 * g + 0.114 * b)
        if abs(r - gray) < 15 and abs(g - gray) < 15 and abs(b - gray) < 15:
            return 232 + round((gray / 255) * 23)
        ri = round(r / 255 * 5)
        gi = round(g / 255 * 5)
        bi = round(b / 255 * 5)
        return 16 + 36 * ri + 6 * gi + bi


# ─────────────────────────────────────────────────────────────────────────────

def _trunc(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"
