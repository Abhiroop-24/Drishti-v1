"""
DRISHTI Configuration Module
Loads settings from .env file and provides centralized config.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")


class PiConfig:
    """Raspberry Pi connection settings."""
    IP = os.getenv("PI_IP", "10.42.0.50")
    USER = os.getenv("PI_USER", "abhiroop")
    PASSWORD = os.getenv("PI_PASSWORD", "12345678")
    SSH_PORT = int(os.getenv("PI_SSH_PORT", 22))


class StreamConfig:
    """Video stream settings."""
    PORT = int(os.getenv("STREAM_PORT", 8080))
    WIDTH = int(os.getenv("STREAM_WIDTH", 800))
    HEIGHT = int(os.getenv("STREAM_HEIGHT", 600))
    FPS = int(os.getenv("STREAM_FPS", 30))
    # Human-readable display string (StreamReceiver builds the real URL)
    URL = f"udp://0.0.0.0:{PORT}"


class YOLOConfig:
    """YOLO detection settings."""
    MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", 0.5))
    MAX_PEOPLE = int(os.getenv("YOLO_MAX_PEOPLE", 10))
    # Minimum seconds between spoken proximity alerts (audio cooldown)
    ALERT_COOLDOWN = float(os.getenv("YOLO_ALERT_COOLDOWN", 5.0))
    # Run YOLO every N frames (1 = every frame; 2 = alternate frames saves CPU)
    EVERY_N_FRAMES = int(os.getenv("YOLO_EVERY_N_FRAMES", 2))
    # Person class ID in COCO dataset
    PERSON_CLASS_ID = 0
    # Approximate person height in real world (meters)
    REAL_PERSON_HEIGHT = 1.7
    # Camera focal length (calibrated for 5MP Pi Camera at 800x600)
    # Re-calibrate if distance readings are off: F = (D * H_px) / H_real
    FOCAL_LENGTH = float(os.getenv("YOLO_FOCAL_LENGTH", 615.0))


class BLIPConfig:
    """Vision captioning model settings."""
    MODEL = os.getenv("BLIP_MODEL", "Salesforce/blip-image-captioning-large")
    DEFAULT_MODE = os.getenv("BLIP_DEFAULT_MODE", "default")
    MODES = ["default", "short", "story"]
    PROMPTS = {
        "default": "a photograph showing",
        "short":   "a photo of",
        "story":   None,
    }


class TTSConfig:
    """Text-to-Speech settings."""
    ENGINE = os.getenv("TTS_ENGINE", "gtts")
    LANGUAGE = os.getenv("TTS_LANGUAGE", "en")
    SLOW = os.getenv("TTS_SLOW", "false").lower() == "true"


class GPIOConfig:
    """GPIO pin assignments."""
    BTN_CAPTURE = int(os.getenv("BTN_CAPTURE", 17))
    BTN_MODE = int(os.getenv("BTN_MODE", 27))
    BTN_YOLO_ALERT = int(os.getenv("BTN_YOLO_ALERT", 22))
    LED_RED = int(os.getenv("LED_RED", 5))
    LED_GREEN = int(os.getenv("LED_GREEN", 6))
    LED_BLUE = int(os.getenv("LED_BLUE", 13))


class PathConfig:
    """File paths."""
    AUDIO_OUTPUT = BASE_DIR / os.getenv("AUDIO_OUTPUT_DIR", "./audio_output")
    CAPTURES = BASE_DIR / os.getenv("CAPTURE_DIR", "./captures")
    LOGS = BASE_DIR / os.getenv("LOG_DIR", "./logs")
    
    @classmethod
    def ensure_dirs(cls):
        """Create directories if they don't exist."""
        cls.AUDIO_OUTPUT.mkdir(parents=True, exist_ok=True)
        cls.CAPTURES.mkdir(parents=True, exist_ok=True)
        cls.LOGS.mkdir(parents=True, exist_ok=True)


# LED Color definitions (R, G, B) - True = ON
class LEDColors:
    """RGB LED color presets."""
    OFF = (False, False, False)
    RED = (True, False, False)        # Alert/Error
    GREEN = (False, True, False)      # Processing
    BLUE = (False, False, True)       # Idle/Ready
    YELLOW = (True, True, False)      # Capturing
    PURPLE = (True, False, True)      # TTS generating
    WHITE = (True, True, True)        # Audio playing
    CYAN = (False, True, True)        # Mode change
