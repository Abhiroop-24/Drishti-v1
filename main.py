#!/usr/bin/env python3
"""
DRISHTI - Main Application (runs on Laptop)
═══════════════════════════════════════════════════════════

An assistive device for blind people that provides:
- Real-time people detection with distance estimation (YOLOv8)
- Rich image descriptions in multiple modes (BLIP)
- Text-to-speech audio output (gTTS)
- GPIO button control from Raspberry Pi
- Audio playback on Pi via AUX jack

Architecture:
    Pi Camera → UDP Stream → Laptop (YOLO + BLIP + TTS) → Audio → Pi AUX

Author: Team DRISHTI
"""

import sys
import os
import time
import json
import socket
import logging
import threading
import numpy as np
from datetime import datetime
from pathlib import Path

# Suppress Qt font-directory warnings — must be set BEFORE importing cv2
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;*.warning=false;qt.qpa.*=false")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts")
os.environ.setdefault("FONTCONFIG_PATH", "/etc/fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
# Prevent "Ignoring XDG_SESSION_TYPE=wayland on Gnome" warning from libqxcb
os.environ["XDG_SESSION_TYPE"] = "x11"

import cv2

# DRISHTI modules
from config import (
    PathConfig, StreamConfig, YOLOConfig, BLIPConfig,
    GPIOConfig, LEDColors, PiConfig
)
from stream_receiver import StreamReceiver
from yolo_detector import YOLODetector
from blip_describer import BLIPDescriber
from tts_engine import TTSEngine
from pi_communicator import PiCommunicator


# ── Logging Setup ──────────────────────────────────────────
def setup_logging():
    """Configure logging for the application."""
    PathConfig.ensure_dirs()

    log_file = PathConfig.LOGS / f"drishti_{datetime.now():%Y%m%d_%H%M%S}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file),
        ],
    )

    # Reduce noise from other libraries
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)

    return logging.getLogger("drishti.main")


# ── Command Server ─────────────────────────────────────────
class CommandServer:
    """
    TCP server that receives commands from Pi's GPIO handler.
    Runs on port 9090.
    """

    def __init__(self, app):
        self.app = app
        self.port = 9090
        self.server = None
        self._running = False
        self._thread = None
        self.logger = logging.getLogger("drishti.cmd_server")

    def start(self):
        """Start the command server."""
        # Free port if a stale process is holding it
        try:
            import subprocess as _sp
            _sp.run(["fuser", "-k", f"{self.port}/tcp"],
                    capture_output=True, timeout=3)
            time.sleep(0.2)
        except Exception:
            pass

        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(("0.0.0.0", self.port))
            self.server.listen(5)
            self.server.settimeout(1.0)

            self._running = True
            self._thread = threading.Thread(
                target=self._listen_loop, daemon=True
            )
            self._thread.start()
            self.logger.info(f"Command server listening on port {self.port}")
        except OSError as e:
            self.logger.error(f"Cannot start command server: {e}")
            self.logger.info("Port 9090 may be in use. Kill old processes.")

    def _listen_loop(self):
        """Listen for incoming commands."""
        while self._running:
            try:
                client, addr = self.server.accept()
                self.logger.info(f"Connection from {addr}")

                data = client.recv(4096).decode("utf-8")
                if data:
                    try:
                        msg = json.loads(data)
                        command = msg.get("command", "")
                        response = self.app.handle_command(command)
                    except json.JSONDecodeError:
                        response = {"status": "invalid_json"}
                    client.sendall(json.dumps(response).encode("utf-8"))

                client.close()

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.logger.error(f"Command server error: {e}")

    def stop(self):
        """Stop the command server."""
        self._running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass


# ── Main Application ───────────────────────────────────────
class DrishtiApp:
    """Main DRISHTI application controller."""

    def __init__(self):
        self.logger = setup_logging()
        self.logger.info("=" * 60)
        self.logger.info("  DRISHTI - Visual Assistance System")
        self.logger.info("  Starting up...")
        self.logger.info("=" * 60)

        PathConfig.ensure_dirs()

        # Initialize components
        self.logger.info("Initializing components...")

        self.stream = StreamReceiver()
        self.yolo = YOLODetector()
        self.blip = BLIPDescriber()
        self.tts = TTSEngine()
        self.pi = PiCommunicator()
        self.cmd_server = CommandServer(self)

        # State
        self.running = False
        # YOLO starts OFF — B3 toggles it on.  Both flags must be in sync.
        self.yolo_active = False
        self._detections_lock = threading.Lock()
        self.current_detections = []
        self.processing_capture = False
        self.last_alert_audio_time = 0
        self.alert_audio_cooldown = YOLOConfig.ALERT_COOLDOWN
        self._playing_alert = False   # guard: only one alert at a time

        # Display window
        self.window_name = "DRISHTI - Visual Assistance"
        self._has_display = "DISPLAY" in os.environ or "WAYLAND_DISPLAY" in os.environ

        # YOLO throttle: run detection every N frames (configurable)
        self._yolo_every_n = YOLOConfig.EVERY_N_FRAMES
        self._frame_idx = 0

    def start(self):
        """Start all components and main loop."""
        self.running = True

        # Connect to Pi
        self.logger.info("Checking Pi connection...")
        if self.pi.check_connection():
            self.logger.info("✓ Pi connected")
            self.pi.set_led_color(LEDColors.BLUE)
        else:
            self.logger.warning("⚠ Pi not connected - running in local mode")

        # Start video stream
        self.logger.info("Starting video stream receiver...")
        stream_ok = self.stream.start()
        if not stream_ok:
            self.logger.warning(
                "⚠ Stream not available yet. "
                "Make sure the Pi camera stream is running!"
            )

        # Preload BLIP model in background
        self.logger.info("Preloading BLIP model in background...")
        blip_thread = threading.Thread(
            target=self._preload_blip, daemon=True
        )
        blip_thread.start()

        # Start command server for Pi GPIO
        self.cmd_server.start()

        self.logger.info("✓ DRISHTI is ready!")
        self.logger.info("─" * 40)
        self.logger.info("Controls (keyboard):")
        self.logger.info("  1 / Space : Capture & Describe")
        self.logger.info("  2 / M     : Cycle BLIP mode")
        self.logger.info("  3 / A     : Toggle YOLO alerts")
        self.logger.info("  Q / ESC   : Quit")
        self.logger.info("─" * 40)

        # Main loop
        self._main_loop()

    def _preload_blip(self):
        """Preload BLIP model in background."""
        try:
            self.blip.load_model()
            self.logger.info("✓ BLIP model preloaded")
        except Exception as e:
            self.logger.error(f"Failed to preload BLIP: {e}")

    def _main_loop(self):
        """Main processing loop."""
        frame_interval = 1.0 / 30

        while self.running:
            loop_start = time.time()

            # Get latest frame
            frame = self.stream.get_frame()

            if frame is not None:
                display_frame = frame.copy()
                self._frame_idx += 1

                # Run YOLO detection (throttled for performance)
                if self.yolo_active and (self._frame_idx % self._yolo_every_n == 0):
                    try:
                        detections = self.yolo.detect(frame)
                        with self._detections_lock:
                            self.current_detections = detections
                    except Exception as e:
                        self.logger.error(f"YOLO detection error: {e}")
                        with self._detections_lock:
                            self.current_detections = []

                # Always draw detections (even on non-detection frames)
                with self._detections_lock:
                    detections_snapshot = list(self.current_detections)
                display_frame = self.yolo.draw_detections(
                    display_frame, detections_snapshot
                )

                # Check for alerts
                if self.yolo.alerts_enabled:
                    self._check_alerts(detections_snapshot)

                # Draw status info
                display_frame = self._draw_status(display_frame)

                # Show frame
                if self._has_display:
                    cv2.imshow(self.window_name, display_frame)
            else:
                # No frame - show waiting screen
                if self._has_display:
                    waiting = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(
                        waiting,
                        "Waiting for Pi camera stream...",
                        (50, 180),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (100, 100, 255),
                        2,
                    )
                    cv2.putText(
                        waiting,
                        f"Listening: udp://0.0.0.0:{StreamConfig.PORT}",
                        (50, 220),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (150, 150, 150),
                        1,
                    )
                    cv2.putText(
                        waiting,
                        "Pi should send:  rpicam-vid | ffmpeg -> udp://10.42.0.1:8080",
                        (50, 255),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (100, 180, 100),
                        1,
                    )
                    cv2.putText(
                        waiting,
                        "Camera svc: sudo systemctl status drishti-camera",
                        (50, 285),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        (100, 150, 100),
                        1,
                    )
                    cv2.putText(
                        waiting,
                        "[1] Capture  [2] Mode  [3] Alerts  [Q] Quit",
                        (50, 330),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (100, 200, 100),
                        1,
                    )
                    cv2.imshow(self.window_name, waiting)

            # Handle keyboard input
            if self._has_display:
                key = cv2.waitKey(1) & 0xFF
                self._handle_key(key)
            else:
                time.sleep(frame_interval)

            # Frame rate control
            elapsed = time.time() - loop_start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _draw_status(self, frame):
        """Draw status information overlay on frame."""
        h, w = frame.shape[:2]

        # Bottom status bar
        bar_h = 60
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        # Mode info
        mode_text = f"Mode: {self.blip.current_mode.upper()}"
        cv2.putText(
            frame,
            mode_text,
            (10, h - 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 255),
            2,
        )

        # FPS
        fps_text = f"FPS: {self.stream.get_fps()}"
        cv2.putText(
            frame,
            fps_text,
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (150, 150, 150),
            1,
        )

        # People count
        people_text = f"People: {len(self.current_detections)}"
        cv2.putText(
            frame,
            people_text,
            (200, h - 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 200, 255),
            2,
        )

        # Alert status
        alert_text = "ALERTS: ON" if self.yolo.alerts_enabled else "ALERTS: OFF"
        alert_color = (0, 255, 0) if self.yolo.alerts_enabled else (100, 100, 100)
        cv2.putText(
            frame,
            alert_text,
            (200, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            alert_color,
            1,
        )

        # Stream status
        if self.stream.is_receiving():
            status = "LIVE"
            color = (0, 255, 0)
        else:
            status = "NO SIGNAL"
            color = (0, 0, 255)

        cv2.putText(
            frame,
            status,
            (w - 140, h - 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )

        # Processing indicator
        if self.processing_capture:
            # Flash effect
            alpha = abs(int(time.time() * 4) % 2)
            if alpha:
                cv2.putText(
                    frame,
                    "PROCESSING...",
                    (w // 2 - 100, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 255),
                    2,
                )

        return frame

    def _handle_key(self, key):
        """Handle keyboard input."""
        if key == 255:  # No key pressed
            return
        if key == ord("q") or key == 27:  # Q or ESC
            self.logger.info("Quit requested")
            self.shutdown()
        elif key == ord("1") or key == ord(" "):  # Capture
            self.handle_command("capture")
        elif key == ord("2") or key == ord("m"):  # Mode
            self.handle_command("cycle_mode")
        elif key == ord("3") or key == ord("a"):  # Alerts
            self.handle_command("toggle_yolo")

    def handle_command(self, command):
        """
        Handle a command from Pi GPIO or keyboard.

        Args:
            command: "capture", "cycle_mode", or "toggle_yolo"

        Returns:
            dict: Response with status and LED color
        """
        self.logger.info(f"Command received: {command}")

        if command == "capture":
            return self._cmd_capture()
        elif command == "cycle_mode":
            return self._cmd_cycle_mode()
        elif command == "toggle_yolo":
            return self._cmd_toggle_yolo()
        else:
            return {
                "status": "unknown_command",
                "led": {"r": True, "g": False, "b": False},
            }

    def _cmd_capture(self):
        """Handle capture & describe command."""
        if self.processing_capture:
            self.logger.warning("Already processing a capture")
            return {
                "status": "busy",
                "led": {"r": True, "g": True, "b": False},
            }

        # Run in background thread to avoid blocking
        thread = threading.Thread(
            target=self._do_capture_and_describe, daemon=True
        )
        thread.start()

        return {
            "status": "capturing",
            "led": {"r": True, "g": True, "b": False},
        }

    def _do_capture_and_describe(self):
        """Capture frame, describe with BLIP, convert to speech, send to Pi."""
        self.processing_capture = True

        try:
            # 1. Set LED to yellow (capturing)
            self.pi.set_led_color(LEDColors.YELLOW)

            # 2. Capture frame
            self.logger.info("📸 Capturing frame...")
            frame = self.stream.capture_frame()
            if frame is None:
                self.logger.error("No frame available!")
                self.pi.set_led_color(LEDColors.RED)
                time.sleep(1)
                self.pi.set_led_color(LEDColors.BLUE)
                return

            # Save capture
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            capture_path = PathConfig.CAPTURES / f"capture_{timestamp}.jpg"
            cv2.imwrite(str(capture_path), frame)
            self.logger.info(f"Frame saved: {capture_path}")

            # 3. Set LED to green (processing BLIP)
            self.pi.set_led_color(LEDColors.GREEN)

            # 4. Get YOLO context (thread-safe snapshot)
            yolo_context = None
            with self._detections_lock:
                detections_snap = list(self.current_detections)
            if detections_snap:
                yolo_context = self.yolo.describe_all_people(detections_snap)

            # 5. Generate description with best model (B1 always uses 'default'
            #    for maximum detail regardless of the current cycling mode).
            self.logger.info("🧠 Generating detailed scene description...")
            description = self.blip.describe(
                frame,
                mode="default",
                yolo_context=yolo_context,
            )
            self.logger.info(f"Description: {description}")

            # 6. Set LED to purple (TTS)
            self.pi.set_led_color(LEDColors.PURPLE)

            # 7. Convert to speech
            self.logger.info("🔊 Converting to speech...")
            audio_path = self.tts.synthesize(
                description, f"describe_{timestamp}"
            )

            if audio_path and audio_path.exists():
                # 8. Set LED to white (playing)
                self.pi.set_led_color(LEDColors.WHITE)

                # 9. Send audio to Pi and play
                self.logger.info("📤 Sending audio to Pi...")
                success = self.pi.send_audio_and_play(audio_path)

                if success:
                    self.logger.info("✓ Audio playing on Pi")
                    # Estimate playback duration (~150 words/min = 2.5 words/sec)
                    word_count = len(description.split())
                    wait_time = max(3, word_count / 2.5)
                    time.sleep(wait_time)
                else:
                    self.logger.warning(
                        "Failed to play on Pi, playing locally"
                    )
                    self._play_local(audio_path)
            else:
                self.logger.error("TTS synthesis failed!")
                self.pi.set_led_color(LEDColors.RED)
                time.sleep(1)

            # 10. Return to blue (idle)
            self.pi.set_led_color(LEDColors.BLUE)

        except Exception as e:
            self.logger.error(
                f"Capture & describe error: {e}", exc_info=True
            )
            try:
                self.pi.set_led_color(LEDColors.RED)
                time.sleep(1)
                self.pi.set_led_color(LEDColors.BLUE)
            except Exception:
                pass

        finally:
            self.processing_capture = False

    def _cmd_cycle_mode(self):
        """Handle mode cycle command."""
        new_mode = self.blip.cycle_mode()
        self.logger.info(f"🔄 Mode changed to: {new_mode}")

        # Announce mode change in background
        thread = threading.Thread(
            target=self._announce_mode_change, args=(new_mode,), daemon=True
        )
        thread.start()

        return {
            "status": f"mode_{new_mode}",
            "mode": new_mode,
            "led": {"r": False, "g": True, "b": True},
        }

    def _announce_mode_change(self, mode):
        """Announce mode change via TTS."""
        try:
            self.pi.set_led_color(LEDColors.CYAN)

            mode_names = {
                "default": "default detailed description",
                "short": "short description",
                "story": "immersive story mode",
            }
            text = f"Mode changed to {mode_names.get(mode, mode)}"

            audio_path = self.tts.synthesize(text, "mode_change")
            if audio_path and audio_path.exists():
                self.pi.send_audio_and_play(audio_path)
                time.sleep(3)

        except Exception as e:
            self.logger.error(f"Mode announcement error: {e}")
        finally:
            try:
                self.pi.set_led_color(LEDColors.BLUE)
            except Exception:
                pass

    def _cmd_toggle_yolo(self):
        """
        B3: Toggle YOLO people-detection mode entirely.
        When ON  — runs YOLO every N frames AND periodically dictates all
                    people with distances.
        When OFF — stops detection, clears overlays, stops dictation.
        """
        enabled = self.yolo.toggle_alerts()   # flips alerts_enabled
        self.yolo_active = enabled            # also gate the detection loop

        if not enabled:
            # Clear stale detections so overlays disappear immediately
            with self._detections_lock:
                self.current_detections = []

        state = "activated" if enabled else "deactivated"
        self.logger.info(f"🎯 YOLO people-detection {state}")

        # Snapshot of current people for immediate announcement on enable
        with self._detections_lock:
            snap = list(self.current_detections)

        thread = threading.Thread(
            target=self._announce_yolo_toggle, args=(enabled, snap), daemon=True
        )
        thread.start()

        return {
            "status": f"yolo_{'on' if enabled else 'off'}",
            "alerts_enabled": enabled,
            "led": {"r": enabled, "g": enabled, "b": not enabled},
        }

    def _announce_yolo_toggle(self, enabled, current_detections=None):
        """Announce YOLO toggle and immediately describe people if any."""
        try:
            if enabled:
                # Immediate scan result
                immediate = self.yolo.describe_all_people(current_detections or [])
                if immediate:
                    text = f"People detection on. {immediate}"
                else:
                    text = "People detection on. No people in view right now."
            else:
                text = "People detection off."

            audio_path = self.tts.synthesize(text, "yolo_toggle")
            if audio_path and audio_path.exists():
                self.pi.send_audio_and_play(audio_path)
                word_count = len(text.split())
                time.sleep(max(2, word_count / 2.5))

        except Exception as e:
            self.logger.error(f"YOLO toggle announcement error: {e}")
        finally:
            try:
                self.pi.set_led_color(LEDColors.BLUE)
            except Exception:
                pass

    def _check_alerts(self, detections):
        """
        When YOLO mode is active, periodically dictate ALL people with
        distances.  Urgent close-person alerts get priority.
        """
        if not self.yolo.alerts_enabled:
            return

        # Skip if a previous alert is still playing
        if self._playing_alert:
            return

        now = time.time()
        if now - self.last_alert_audio_time < self.alert_audio_cooldown:
            return  # Still in cooldown

        # Prefer urgent message if someone is very close/close
        urgent = self.yolo.generate_alert_message(detections)
        full   = self.yolo.describe_all_people(detections)

        msg = urgent if urgent else full
        if msg:
            self.logger.info(f"👤 YOLO: {msg}")
            thread = threading.Thread(
                target=self._play_alert, args=(msg,), daemon=True
            )
            thread.start()

    def _play_alert(self, alert_text):
        """Generate and play an alert audio — waits for full playback to finish."""
        self._playing_alert = True
        try:
            self.pi.set_led_color(LEDColors.RED)

            audio_path = self.tts.synthesize_alert(alert_text)
            if audio_path and audio_path.exists():
                self.pi.send_audio_and_play(audio_path)
                # Wait long enough for the whole sentence to finish:
                # ~2.0 words/sec for TTS, plus 0.8s network/start buffer
                word_count = len(alert_text.split())
                wait_time = max(3.5, word_count / 2.0 + 0.8)
                time.sleep(wait_time)

        except Exception as e:
            self.logger.error(f"Alert playback error: {e}")
        finally:
            self._playing_alert = False
            # Cooldown starts AFTER playback ends so next alert never fires mid-sentence
            self.last_alert_audio_time = time.time()
            try:
                self.pi.set_led_color(LEDColors.BLUE)
            except Exception:
                pass

    def _play_local(self, audio_path):
        """Play audio locally as fallback."""
        try:
            import subprocess

            subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", str(audio_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self.logger.error(f"Local playback failed: {e}")

    def shutdown(self):
        """Gracefully shut down all components."""
        self.logger.info("Shutting down DRISHTI...")
        self.running = False

        # Stop components
        try:
            self.cmd_server.stop()
        except Exception:
            pass
        try:
            self.stream.stop()
        except Exception:
            pass

        # Turn off LED
        try:
            self.pi.set_led_color(LEDColors.OFF)
        except Exception:
            pass

        try:
            self.pi.disconnect()
        except Exception:
            pass

        try:
            cv2.destroyAllWindows()
        except Exception:
            pass

        self.logger.info("DRISHTI shutdown complete")


# ── Entry Point ────────────────────────────────────────────
def main():
    """Main entry point."""
    app = DrishtiApp()

    try:
        app.start()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        app.shutdown()
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback

        traceback.print_exc()
        app.shutdown()


if __name__ == "__main__":
    main()
