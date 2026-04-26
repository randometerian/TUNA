"""TUNA audio player — wraps mpv via JSON IPC socket"""
import os
import json
import time
import socket
import subprocess
import threading
from pathlib import Path
from tuna.config import TUNA_DIR

SOCKET_PATH = str(TUNA_DIR / "mpv.sock")


class Player:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket    | None = None
        self._buf   = b""
        self._lock  = threading.Lock()

        self.playing   = False
        self.position  = 0.0
        self.duration  = 0.0
        self.volume    = 80
        self.finished  = False   # main thread reads+clears this

        # Timestamp of when the current track started loading.
        # We ignore end-file events that arrive within 2 s of a load() call
        # because mpv fires end-file(stop) for the previous track when
        # loadfile replace is issued.
        self._load_time: float = 0.0

    def start(self):
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    # ── public API ─────────────────────────────────────────────────────────────

    def load(self, path: str):
        with self._lock:
            self.finished   = False
            self._load_time = time.time()

        if self._proc and self._proc.poll() is None:
            if not self._sock:
                self._connect_socket()
            self._cmd("loadfile", [path, "replace"])
            self._send({"command": ["set_property", "pause", False]})
        else:
            self._launch(path)

        self.playing  = True
        self._paused  = False

    def play_pause(self):
        if not (self._proc and self._proc.poll() is None):
            return
        self._paused = not self._paused
        self._send({"command": ["set_property", "pause", self._paused]})
        self.playing = not self._paused

    def seek(self, seconds: float, relative: bool = False):
        self._cmd("seek", [seconds, "relative" if relative else "absolute"])

    def set_volume(self, vol: int):
        vol = max(0, min(100, vol))
        self.volume = vol
        self._send({"command": ["set_property", "volume", vol]})

    def quit(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._cmd("quit", [])
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()
        self._close_socket()

    # ── internals ─────────────────────────────────────────────────────────────

    def _launch(self, path: str):
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass
        self._proc = subprocess.Popen(
            ["mpv", "--no-video", "--no-terminal", "--really-quiet",
             "--idle=yes", f"--input-ipc-server={SOCKET_PATH}",
             f"--volume={self.volume}", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(30):
            if os.path.exists(SOCKET_PATH):
                break
            time.sleep(0.1)
        self._connect_socket()

    def _connect_socket(self):
        self._close_socket()
        for _ in range(10):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(SOCKET_PATH)
                s.setblocking(False)
                self._sock = s
                return
            except Exception:
                time.sleep(0.05)

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send(self, data: dict):
        if not self._sock:
            self._connect_socket()
        if not self._sock:
            return
        msg = (json.dumps(data) + "\n").encode()
        try:
            self._sock.sendall(msg)
        except Exception:
            self._connect_socket()
            if self._sock:
                try:
                    self._sock.sendall(msg)
                except Exception:
                    pass

    def _cmd(self, cmd: str, args: list):
        self._send({"command": [cmd] + args})

    # ── poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while True:
            try:
                # Read any pending IPC events
                if self._sock:
                    try:
                        chunk = self._sock.recv(4096)
                        if chunk:
                            self._buf += chunk
                            while b"\n" in self._buf:
                                line, self._buf = self._buf.split(b"\n", 1)
                                self._handle_event(line)
                        elif chunk == b"":
                            self._close_socket()
                    except BlockingIOError:
                        pass
                    except Exception:
                        self._connect_socket()

                # Poll position + duration while playing
                if self.playing and self._sock:
                    self._send({"command": ["get_property", "time-pos"], "request_id": 10})
                    self._send({"command": ["get_property", "duration"],  "request_id": 11})

                # Position-based fallback: if we're within 0.3 s of the end,
                # mark finished.  This catches cases where end-file never fires.
                if (self.playing and not self._paused
                        and self.duration > 1.0
                        and self.position > 0.0
                        and self.position >= self.duration - 0.3
                        and time.time() - self._load_time > 3.0):
                    self.finished = True
                    self.playing  = False

                # Process death fallback
                if self._proc and self._proc.poll() is not None and self.playing:
                    self.finished = True
                    self.playing  = False

            except Exception:
                pass
            time.sleep(0.1)

    def _handle_event(self, raw: bytes):
        try:
            msg = json.loads(raw.decode())
        except Exception:
            return

        rid  = msg.get("request_id")
        data = msg.get("data")
        evt  = msg.get("event")

        if rid == 10 and isinstance(data, (int, float)):
            self.position = float(data)

        elif rid == 11 and isinstance(data, (int, float)):
            self.duration = float(data)

        elif evt == "end-file":
            reason = msg.get("reason", "")
            # Accept eof OR idle as natural track end.
            # Guard: must be at least 2 s since last load() to avoid catching
            # the end-file(stop) that mpv fires when loadfile replace is issued.
            if reason in ("eof", "stop") and time.time() - self._load_time > 2.0:
                self.finished = True
                self.playing  = False
                self._paused  = False

        elif evt == "idle":
            # mpv entered idle mode = track finished and nothing queued
            if time.time() - self._load_time > 2.0:
                self.finished = True
                self.playing  = False

        elif evt == "pause":
            self.playing = False
            self._paused = True

        elif evt == "unpause":
            self.playing = True
            self._paused = False
