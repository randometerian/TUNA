"""TUNA playlist management"""
import json
import uuid
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional
from tuna.config import PLAYLIST_DIR, AUDIO_EXTS


@dataclass
class Track:
    path:       str
    title:      str   = ""
    artist:     str   = ""
    album:      str   = ""
    duration:   float = 0.0
    cover_path: str   = ""

    @property
    def display_title(self):
        return self.title if self.title else Path(self.path).stem

    @property
    def display_artist(self):
        return self.artist if self.artist else "Unknown Artist"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


@dataclass
class Playlist:
    name:       str
    id:         str  = field(default_factory=lambda: str(uuid.uuid4())[:8])
    tracks:     List[dict] = field(default_factory=list)
    is_library: bool = False   # protected — cannot be deleted

    def save(self):
        path = PLAYLIST_DIR / f"{self.id}.json"
        data = {
            "name":       self.name,
            "id":         self.id,
            "is_library": self.is_library,
            "tracks":     [t.to_dict() for t in self.tracks],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Playlist":
        with open(path) as f:
            data = json.load(f)
        pl = cls(name=data["name"], id=data["id"],
                 is_library=data.get("is_library", False))
        pl.tracks = [Track.from_dict(t) for t in data.get("tracks", [])]
        return pl

    def delete_file(self):
        p = PLAYLIST_DIR / f"{self.id}.json"
        if p.exists():
            p.unlink()

    def add_track(self, track: Track):
        paths = {t.path for t in self.tracks}
        if track.path not in paths:
            self.tracks.append(track)
            self.save()

    def remove_track(self, index: int):
        if 0 <= index < len(self.tracks):
            self.tracks.pop(index)
            self.save()

    def move_track(self, src: int, dst: int):
        if src == dst:
            return
        t = self.tracks.pop(src)
        self.tracks.insert(dst, t)
        self.save()

    def __len__(self):
        return len(self.tracks)


class PlaylistManager:
    def __init__(self):
        self.playlists: List[Playlist] = []
        self._load_all()

    def _load_all(self):
        loaded = []
        for f in PLAYLIST_DIR.glob("*.json"):
            try:
                loaded.append(Playlist.load(f))
            except Exception:
                pass

        # Ensure library is always first and always marked correctly
        libs  = [p for p in loaded if p.is_library]
        others = [p for p in loaded if not p.is_library]
        others.sort(key=lambda p: p.name.lower())

        if not libs:
            lib = Playlist(name="Library", is_library=True)
            lib.save()
            libs = [lib]

        self.playlists = libs[:1] + others

    def create(self, name: str) -> Playlist:
        pl = Playlist(name=name)
        pl.save()
        self.playlists.append(pl)
        return pl

    def delete(self, index: int):
        if 0 <= index < len(self.playlists):
            pl = self.playlists[index]
            if pl.is_library:
                return   # Library is indestructible
            pl.delete_file()
            self.playlists.pop(index)

    def rename(self, index: int, name: str):
        if 0 <= index < len(self.playlists):
            self.playlists[index].name = name
            self.playlists[index].save()

    def get(self, index: int) -> Optional[Playlist]:
        if 0 <= index < len(self.playlists):
            return self.playlists[index]
        return None

    def scan_library(self, music_dir: str) -> int:
        """
        Scan music_dir for new audio files and add them to the Library playlist.
        Returns the number of new tracks added.
        """
        from tuna.metadata import read_metadata
        library  = self.playlists[0]
        existing = {t.path for t in library.tracks}
        root     = Path(music_dir)
        added    = 0
        if not root.exists():
            return 0
        for f in sorted(root.rglob("*")):
            if f.suffix.lower() in AUDIO_EXTS and str(f) not in existing:
                track = read_metadata(str(f))
                library.add_track(track)
                added += 1
        return added

    def start_watcher(self, music_dir: str, interval: float = 15.0):
        """
        Start a background thread that polls music_dir every `interval` seconds
        and automatically adds any new audio files to the Library.
        Uses watchdog if available for instant detection, otherwise polls.
        """
        import threading

        def _poll_loop():
            import time
            # Try watchdog first
            try:
                from watchdog.observers import Observer
                from watchdog.events import FileSystemEventHandler

                plm_ref = self

                class _Handler(FileSystemEventHandler):
                    def on_created(self, event):
                        if not event.is_directory:
                            p = Path(event.src_path)
                            if p.suffix.lower() in AUDIO_EXTS:
                                plm_ref.scan_library(music_dir)

                observer = Observer()
                observer.schedule(_Handler(), music_dir, recursive=True)
                observer.start()
                # Keep the thread alive as long as the observer runs
                while observer.is_alive():
                    time.sleep(1)
                return
            except ImportError:
                pass

            # Fallback: poll every interval seconds
            while True:
                time.sleep(interval)
                try:
                    self.scan_library(music_dir)
                except Exception:
                    pass

        t = threading.Thread(target=_poll_loop, daemon=True)
        t.start()
