"""
TUNA visualizer — CAVA stereo mirrored layout.

CAVA stereo mode explained:
  - Audio is captured in stereo (L + R channels)
  - Each channel gets its own FFT → FFT_BINS bars each
  - The LEFT channel bars are REVERSED (high freq → low freq, left to right)
  - The RIGHT channel bars are NORMAL  (low freq → high freq, left to right)
  - They are placed side by side: [reversed_L | normal_R]
  - Result: bass frequencies meet at the CENTER, treble is at the EDGES
  - Because L and R are slightly different, it's not perfectly symmetric

We produce 2 * FFT_BINS values total and expose them in self.bars.
The renderer in app.py reads them as-is (already in display order).

DSP pipeline per channel:
  1. Capture stereo PCM (parec --channels=2 → interleaved L,R samples)
  2. De-interleave into L and R buffers
  3. Rolling 4096-sample Hann-windowed FFT per channel
  4. Log-spaced frequency bin aggregation (20Hz–20kHz)
  5. Power (magnitude²) with treble compensation
  6. Shared peak normalizer (tracks loudest bar across both channels)
  7. Asymmetric gravity smoothing: instant attack, gravity decay
  8. Arrange: reversed(L) + R → self.bars
"""
import threading
import time
import os
import subprocess
import numpy as np
from tuna.config import FFT_BINS

_RATE        = 44100
_CHUNK       = 512           # samples per channel per read
_FFT_SIZE    = 4096
_RATE_F      = float(_RATE)

_NOISE_FLOOR = 2e-4
_GRAVITY     = 0.88          # decay multiplier per compute cycle
_PEAK_FALL   = 0.994
_TREBLE_MULT = 2.8


class Visualizer:

    def __init__(self):
        # self.bars has 2*FFT_BINS entries: [reversed_L ... | ... R]
        self.bars:  list[float] = [0.0] * (FFT_BINS * 2)
        self.active: bool       = False
        self._running           = False
        self._lock   = threading.Lock()

        # Per-channel rolling buffers and smoothing state
        self._buf_l  = np.zeros(_FFT_SIZE, dtype=np.float64)
        self._buf_r  = np.zeros(_FFT_SIZE, dtype=np.float64)
        self._smooth_l = np.zeros(FFT_BINS, dtype=np.float64)
        self._smooth_r = np.zeros(FFT_BINS, dtype=np.float64)
        self._peak   = 1.0
        self._hann   = np.hanning(_FFT_SIZE).astype(np.float64)

        self._bin_lo, self._bin_hi = self._log_bins()

        self._thread: threading.Thread | None = None
        self._try_start()

    def _log_bins(self):
        """Logarithmic (musical) frequency spacing, 20Hz–20kHz."""
        n_fft  = _FFT_SIZE // 2
        f_min  = 20.0
        f_max  = min(20000.0, _RATE_F / 2.0)
        lo = np.zeros(FFT_BINS, dtype=int)
        hi = np.zeros(FFT_BINS, dtype=int)
        for i in range(FFT_BINS):
            f_lo   = f_min * (f_max / f_min) ** (i       / FFT_BINS)
            f_hi   = f_min * (f_max / f_min) ** ((i + 1) / FFT_BINS)
            bl     = max(1, int(f_lo / _RATE_F * _FFT_SIZE))
            bh     = max(bl + 1, int(f_hi / _RATE_F * _FFT_SIZE))
            lo[i]  = bl
            hi[i]  = min(bh, n_fft)
        return lo, hi

    # ── Public ────────────────────────────────────────────────────────────────

    def notify_playing(self, playing: bool):
        if not playing:
            with self._lock:
                self._smooth_l *= _GRAVITY ** 4
                self._smooth_r *= _GRAVITY ** 4
                combined = np.concatenate([self._smooth_l[::-1], self._smooth_r])
                self.bars = combined.tolist()

    def stop(self):
        self._running = False

    # ── Startup ───────────────────────────────────────────────────────────────

    def _try_start(self):
        if self._try_parec():
            return
        self._try_pyaudio()

    # ── Backend: parec (stereo) ───────────────────────────────────────────────

    def _try_parec(self) -> bool:
        try:
            proc = subprocess.Popen(
                ["parec",
                 "--format=s16le",
                 "--rate=44100",
                 "--channels=2",          # stereo
                 "--latency-msec=15",
                 "--device=@DEFAULT_MONITOR@"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            import select
            if not select.select([proc.stdout], [], [], 0.5)[0]:
                proc.kill()
                return False
            self.active = self._running = True
            self._thread = threading.Thread(
                target=self._parec_loop, args=(proc,), daemon=True)
            self._thread.start()
            return True
        except Exception:
            return False

    def _parec_loop(self, proc):
        # s16le stereo: 2 channels × 2 bytes = 4 bytes per frame
        bytes_per_read = _CHUNK * 4
        while self._running:
            try:
                raw = proc.stdout.read(bytes_per_read)
                if not raw or len(raw) < 4:
                    time.sleep(0.002)
                    continue
                # Interleaved stereo: LRLRLR...
                interleaved = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
                left  = interleaved[0::2]
                right = interleaved[1::2]
                self._ingest(left, right)
            except Exception:
                time.sleep(0.002)
        try:
            proc.kill()
        except Exception:
            pass

    # ── Backend: PyAudio (stereo) ─────────────────────────────────────────────

    def _try_pyaudio(self):
        try:
            import pyaudio  # noqa
            self.active = self._running = True
            self._thread = threading.Thread(target=self._pyaudio_loop, daemon=True)
            self._thread.start()
        except ImportError:
            self.active = False

    def _pyaudio_loop(self):
        import pyaudio
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_err = os.dup(2)
        os.dup2(devnull, 2); os.close(devnull)
        pa = pyaudio.PyAudio()
        os.dup2(old_err, 2); os.close(old_err)

        mon = self._find_monitor(pa)
        rate = _RATE
        if mon is not None:
            try:
                rate = int(pa.get_device_info_by_index(mon).get("defaultSampleRate", _RATE))
            except Exception:
                pass

        stream = None
        # Try stereo first, fall back to mono
        for ch in (2, 1):
            for dev in ([mon, None] if mon is not None else [None]):
                try:
                    stream = pa.open(
                        format=pyaudio.paFloat32, channels=ch,
                        rate=rate, input=True,
                        input_device_index=dev,
                        frames_per_buffer=_CHUNK)
                    self._pa_channels = ch
                    break
                except Exception:
                    continue
            if stream:
                break

        if not stream:
            self.active = False
            pa.terminate()
            return

        while self._running:
            try:
                raw = stream.read(_CHUNK, exception_on_overflow=False)
                s   = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
                if self._pa_channels == 2:
                    self._ingest(s[0::2], s[1::2])
                else:
                    self._ingest(s, s)   # mono: same signal both sides
            except Exception:
                time.sleep(0.002)
        stream.stop_stream()
        stream.close()
        pa.terminate()

    @staticmethod
    def _find_monitor(pa):
        try:
            r = subprocess.run(["pactl", "get-default-sink"],
                               capture_output=True, text=True, timeout=2)
            sink = r.stdout.strip()
            mon  = (sink + ".monitor") if sink else ""
            for i in range(pa.get_device_count()):
                try:
                    info = pa.get_device_info_by_index(i)
                    name = info.get("name", "")
                    if ((mon and mon in name) or "monitor" in name.lower()) \
                            and info.get("maxInputChannels", 0) > 0:
                        return i
                except Exception:
                    continue
        except Exception:
            pass
        return None

    # ── DSP ───────────────────────────────────────────────────────────────────

    def _ingest(self, left: np.ndarray, right: np.ndarray):
        n = min(len(left), len(right), _FFT_SIZE)
        with self._lock:
            self._buf_l = np.roll(self._buf_l, -n)
            self._buf_l[-n:] = left[:n]
            self._buf_r = np.roll(self._buf_r, -n)
            self._buf_r[-n:] = right[:n]
            wl = self._buf_l.copy()
            wr = self._buf_r.copy()
        self._compute(wl, wr)

    def _fft_bars(self, window: np.ndarray) -> np.ndarray:
        """FFT → log-spaced bars with treble compensation."""
        mag   = np.abs(np.fft.rfft(window * self._hann, n=_FFT_SIZE))
        power = mag[1:_FFT_SIZE // 2 + 1] ** 2
        bars  = np.zeros(FFT_BINS, dtype=np.float64)
        for i in range(FFT_BINS):
            band = power[self._bin_lo[i]: self._bin_hi[i]]
            if len(band) > 0:
                bars[i] = float(np.sqrt(np.mean(band)))
        # Linear treble boost (low freq → 1×, high freq → _TREBLE_MULT×)
        bars *= np.linspace(1.0, _TREBLE_MULT, FFT_BINS)
        return bars

    def _compute(self, wl: np.ndarray, wr: np.ndarray):
        # Noise gate — check combined RMS
        rms = float(np.sqrt((np.mean(wl**2) + np.mean(wr**2)) / 2.0))
        if rms < _NOISE_FLOOR:
            with self._lock:
                self._smooth_l *= _GRAVITY
                self._smooth_r *= _GRAVITY
                combined = np.concatenate([self._smooth_l[::-1], self._smooth_r])
                self.bars = combined.tolist()
            return

        raw_l = self._fft_bars(wl)
        raw_r = self._fft_bars(wr)

        # Shared peak normalizer across both channels
        peak = float(max(np.max(raw_l), np.max(raw_r)))
        if peak > self._peak:
            self._peak = peak
        else:
            self._peak = max(1e-6, self._peak * _PEAK_FALL)

        norm_l = np.clip(raw_l / self._peak, 0.0, 1.0)
        norm_r = np.clip(raw_r / self._peak, 0.0, 1.0)

        # Asymmetric gravity smoothing per channel
        with self._lock:
            rising_l = norm_l > self._smooth_l
            rising_r = norm_r > self._smooth_r
            self._smooth_l = np.where(rising_l, norm_l, self._smooth_l * _GRAVITY)
            self._smooth_r = np.where(rising_r, norm_r, self._smooth_r * _GRAVITY)

            # CAVA stereo mirror layout:
            #   left half  = L channel REVERSED (high freq in center, bass at left edge)
            #   right half = R channel NORMAL   (bass in center, high freq at right edge)
            # Result: bass meets at the center, treble at the outside edges
            combined = np.concatenate([self._smooth_l[::-1], self._smooth_r])
            self.bars = combined.tolist()
