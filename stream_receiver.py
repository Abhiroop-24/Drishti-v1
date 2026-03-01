"""
DRISHTI - Video Stream Receiver
Receives H264/MPEG-TS video stream from Raspberry Pi via UDP.

Key design:
- ffmpeg subprocess decodes MPEG-TS → raw BGR24 pixels
- Separate stderr-drain thread prevents ffmpeg from blocking on stderr buffer
- UDP socket has a large fifo_size to absorb burst packets
- Exponential back-off on reconnect (max 10 s)
- Thread-safe frame access via Lock
"""

import logging
import threading
import subprocess
import time
import numpy as np
from config import StreamConfig

logger = logging.getLogger("drishti.stream")


class StreamReceiver:
    """Receives and decodes video stream from Raspberry Pi."""

    def __init__(self):
        self.width  = StreamConfig.WIDTH
        self.height = StreamConfig.HEIGHT
        self.fps    = StreamConfig.FPS
        # UDP URL with large FIFO (10 MB) + overrun_nonfatal so we never drop
        # the whole stream on a momentary spike. timeout = 15 s in µs.
        self.stream_url = (
            f"udp://0.0.0.0:{StreamConfig.PORT}"
            f"?fifo_size=10000000&overrun_nonfatal=1&timeout=15000000"
        )

        self._running         = False
        self._frame           = None
        self._frame_lock      = threading.Lock()
        self._thread          = None
        self._frame_count     = 0
        self._last_frame_time = 0.0
        self._actual_fps      = 0.0
        self._process         = None
        self._reconnect_delay = 2.0   # seconds; doubles on failure (cap 10 s)

        logger.info(f"Stream receiver configured: {self.stream_url}")

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        """Start receiving stream in a background thread."""
        if self._running:
            return True
        self._running = True
        self._thread  = threading.Thread(
            target=self._receive_loop, daemon=True, name="stream-rx"
        )
        self._thread.start()
        logger.info("Stream receiver started (waiting for camera UDP packets…)")
        return True

    def get_frame(self):
        """Return a copy of the latest decoded frame, or None."""
        with self._frame_lock:
            if self._frame is not None:
                return self._frame.copy()
        return None

    def capture_frame(self):
        """Alias for get_frame – used when saving a still."""
        frame = self.get_frame()
        if frame is not None:
            logger.info(f"Frame captured: {frame.shape}")
        else:
            logger.warning("No frame available for capture")
        return frame

    def get_fps(self):
        return round(self._actual_fps, 1)

    def get_frame_count(self):
        return self._frame_count

    def is_receiving(self):
        if not self._running:
            return False
        if self._last_frame_time > 0:
            return (time.time() - self._last_frame_time) < 3.0
        return False

    def stop(self):
        self._running = False
        self._kill_process()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Stream receiver stopped")

    # ── Internal ──────────────────────────────────────────────────────

    def _build_ffmpeg_cmd(self):
        return [
            "ffmpeg",
            # ── Input options ──────────────────────────────────────
            "-fflags",           "+nobuffer+discardcorrupt",
            "-flags",            "low_delay",
            "-rtbufsize",        "32M",          # receive-side ring buffer
            "-analyzeduration",  "1000000",      # 1 s probe (MPEG-TS needs it)
            "-probesize",        "1000000",
            "-i",                self.stream_url,
            # ── Output options ─────────────────────────────────────
            "-f",     "rawvideo",
            "-pix_fmt", "bgr24",
            "-vf",    f"scale={self.width}:{self.height}",
            "-an",                              # no audio
            "-sn",                              # no subtitles
            "-vsync", "passthrough",            # don't duplicate/drop frames
            "-v",     "error",                  # only real errors to stderr
            "pipe:1",
        ]

    def _receive_loop(self):
        """Main loop: spawn ffmpeg, read frames, reconnect on failure."""
        frame_size      = self.width * self.height * 3   # bytes per BGR frame
        reconnect_delay = self._reconnect_delay

        while self._running:
            ffmpeg_cmd = self._build_ffmpeg_cmd()
            logger.info("ffmpeg starting: " + " ".join(ffmpeg_cmd))

            try:
                proc = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=frame_size * 4,    # ~4 frame ring buffer
                )
                self._process = proc

                # Drain stderr in a background thread so it never fills the OS
                # pipe buffer and causes ffmpeg to block/deadlock.
                stderr_thread = threading.Thread(
                    target=self._drain_stderr,
                    args=(proc,),
                    daemon=True,
                    name="stream-stderr",
                )
                stderr_thread.start()

                logger.info("ffmpeg process started — waiting for first frame…")
                frames_this_session = 0

                while self._running:
                    # Read exactly one raw BGR frame
                    raw = self._read_exactly(proc.stdout, frame_size)
                    if raw is None:
                        # ffmpeg exited (EOF on stdout)
                        break

                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(
                        (self.height, self.width, 3)
                    )
                    with self._frame_lock:
                        self._frame = frame
                        self._frame_count += 1

                    now = time.time()
                    if self._last_frame_time > 0:
                        dt = now - self._last_frame_time
                        if 0 < dt < 1.0:
                            self._actual_fps = (
                                0.9 * self._actual_fps + 0.1 / dt
                            )
                    self._last_frame_time = now
                    frames_this_session  += 1

                    if frames_this_session == 1:
                        logger.info(
                            "✓ First frame received — stream is live! "
                            f"({self.width}×{self.height})"
                        )
                        reconnect_delay = 2.0   # reset on success

            except Exception as exc:
                logger.error(f"Stream error: {exc}", exc_info=True)

            self._kill_process()

            if self._running:
                logger.warning(
                    f"Stream lost. Reconnecting in {reconnect_delay:.0f} s…"
                )
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 10.0)

    @staticmethod
    def _read_exactly(stream, n_bytes: int):
        """
        Read exactly *n_bytes* from *stream*.
        Returns the bytes, or None if EOF / stream closed.
        """
        buf = bytearray()
        while len(buf) < n_bytes:
            chunk = stream.read(n_bytes - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _drain_stderr(proc):
        """Silently consume ffmpeg's stderr so the pipe never fills up."""
        try:
            for line in proc.stderr:
                decoded = line.decode(errors="replace").strip()
                if decoded:
                    logger.debug(f"[ffmpeg] {decoded}")
        except Exception:
            pass

    def _kill_process(self):
        proc, self._process = self._process, None
        if proc is None:
            return
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def __del__(self):
        self.stop()

