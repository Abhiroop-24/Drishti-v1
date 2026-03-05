# DRISHTI - Visual Assistance System for the Visually Impaired

> "Drishti" means "Vision" in Sanskrit

An AI-powered assistive device that helps blind people understand their surroundings through:
- **Real-time people detection** with distance estimation (YOLOv8)
- **Rich scene descriptions** in multiple modes (BLIP)
- **Natural speech output** delivered through earphones (gTTS)

## System Architecture

```
┌─────────────────────────┐     UDP Stream       ┌──────────────────────────┐
│   Raspberry Pi 3B+      │ ──────────────────-  │       Laptop             │
│                         │     (H264/MPEGTS)    │                          │
│  ┌──────────┐           │                      │  ┌──────────────────┐    │
│  │ Pi Camera│───--------- rpicam-vid + ffmpeg  │  │ Stream Receiver  │    │
│  │  (5MP)   │           │                      │  └────────┬─────────┘    │
│  └──────────┘           │                      │           │              │
│                         │                      │  ┌────────▼─────────┐    │
│  ┌──────────────────┐   │  TCP Commands        │  │  YOLOv8 Detector │    │
│  │ GPIO Buttons     │───│ ──────────────────   │  │  (People + Dist) │    │
│  │ B1: Capture      │   │                      │  └────────┬─────────┘    │
│  │ B2: Mode         │   │                      │           │              │
│  │ B3: YOLO Alert   │   │                      │  ┌────────▼─────────┐    │
│  └──────────────────┘   │                      │  │ BLIP Describer   │    │
│                         │                      │  │ (3 modes)        │    │
│  ┌──────────────────┐   │    Audio (SCP)       │  └────────┬─────────┘    │
│  │ RGB LED          │ ──│ ──────────────────── │           │              │
│  │ Status Indicator │   │                      │  ┌────────▼─────────┐    │
│  └──────────────────┘   │                      │  │  TTS Engine      │    │
│                         │                      │  │  (gTTS)          │    │
│  ┌──────────────────┐   │                      │  └──────────────────┘    │
│  │ AUX Jack Output  │ ──│──── Audio File ────  │                          │
│  │ (Earphones)      │   │     via SCP          │      LLM(local)          │
│  └──────────────────┘   │                      │                          │
└─────────────────────────┘                      └──────────────────────────┘
```

## Project Structure

```
DRISHTI/
├── main.py              # Main app (runs on laptop)
├── config.py            # Configuration module
├── yolo_detector.py     # YOLOv8 people detection
├── blip_describer.py    # BLIP image captioning
├── tts_engine.py        # Text-to-Speech
├── stream_receiver.py   # Video stream receiver
├── pi_communicator.py   # SSH/SCP Pi communication
├── pi_gpio_handler.py   # GPIO button handler (runs on Pi)
├── .env                 # Environment configuration
├── requirements.txt     # Python dependencies
├── deploy_to_pi.sh      # Deploy scripts to Pi
├── pi_scripts/
│   ├── start_camera.sh  # Camera stream script (Pi)
│   └── setup_pi.sh      # Pi setup script
└── README.md            # This file
```

## Quick Start

### 1. Laptop Setup

```bash
# Install Python dependencies
pip install -r requirements.txt

# Verify YOLO model downloads
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
```

### 2. Deploy to Raspberry Pi

```bash
# Install sshpass if not available
sudo apt install sshpass

# Deploy files to Pi
bash deploy_to_pi.sh

# SSH into Pi for first-time setup
ssh abhiroop@10.42.0.50
bash ~/drishti/setup_pi.sh
```

### 3. Start the System

**On the Raspberry Pi (2 terminals):**

```bash
# Terminal 1: Start camera stream
bash ~/drishti/start_camera.sh

# Terminal 2: Start button handler
python3 ~/drishti/pi_gpio_handler.py
```

**On the Laptop:**
```bash
python3 main.py
```

## Button Controls

| Button | GPIO | Action | LED Color |
|--------|------|--------|-----------|
| **B1** | GPIO17 | Capture & Describe | Yellow → Green → Purple → White |
| **B2** | GPIO27 | Cycle BLIP Mode | Cyan |
| **B3** | GPIO22 | Toggle YOLO Alerts | Purple |

### Keyboard Shortcuts (Laptop)

| Key | Action |
|-----|--------|
| `1` / `Space` | Capture & Describe |
| `2` / `M` | Cycle BLIP Mode |
| `3` / `A` | Toggle YOLO Alerts |
| `Q` / `ESC` | Quit |

## LED Color Guide

| Color | Meaning |
|-------|---------|
| Blue | Idle / Ready |
| Yellow | Capturing image |
| Green | AI Processing |
| Purple | Generating speech |
| White | Playing audio |
| Red | Alert / Error |
| Cyan | Mode change |

## BLIP Description Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Default** | Detailed scene description with objects, positions, colors | General use |
| **Short** | Brief 1-2 sentence summary | Quick check |
| **Story** | Immersive narrative with atmosphere, emotions, sounds | Rich experience |

## YOLO Detection Features

- Detects up to 10 people simultaneously
- Distance estimation using pinhole camera model
- Position tracking: left, center, right
- Proximity alerts:
  - Very close (< 1.5m) - Urgent warning
  - Close (< 3.0m) - Nearby notice
  - Medium (< 6.0m) - Detected
  - Far (> 6.0m) - Background

## Hardware Connections

### GPIO Wiring (Pi 3B+)

```
┌─── Pi GPIO Header ── ─┐
│                       │
│  B1 ──── GPIO17       │ ← Button 1 (Capture)
│  B2 ──── GPIO27       │ ← Button 2 (Mode)
│  B3 ──── GPIO22       │ ← Button 3 (YOLO)
│  GND ─── Common GND   │ ← All buttons share GND
│                       │
│  LED_R ── GPIO5  ─[R]─│ ← Red (with resistor)
│  LED_G ── GPIO6  ─[R]─│ ← Green (with resistor)
│  LED_B ── GPIO13 ─[R]─│ ← Blue (with resistor)
│  LED_GND ─ GND        │ ← LED common ground
│                       │
│  3.5mm AUX Jack ──────│ ← Audio output (earphones)
│  Ethernet ─────────── │ ← Connected to laptop (10.42.0.50)
│  Camera (CSI) ────────│ ← 5MP Pi Camera
└───────────────────────┘
```

### Network Configuration

- **Pi IP**: 10.42.0.50 (static)
- **Laptop IP**: 10.42.0.1
- **Video Stream**: UDP port 8080
- **Command Channel**: TCP port 9090

## Troubleshooting

### Camera stream not working
```bash
# On Pi: Check if camera is detected
rpicam-hello --list-cameras

# Check if rpicam-vid is available
which rpicam-vid
```

### No audio on Pi
```bash
# Force output to 3.5mm jack
amixer cset numid=3 1

# Test audio
speaker-test -t wav -l 1

# Check volume
amixer sset 'Headphone' 80%
```

### Pi not connecting
```bash
# From laptop, test connection
ping 10.42.0.50

# Test SSH
ssh abhiroop@10.42.0.50
```

## Performance Notes

- YOLO detection runs at ~15-25 FPS on laptop with GPU
- BLIP description takes 2-5 seconds per image
- TTS synthesis takes 1-3 seconds
- Total capture-to-speech pipeline: ~5-10 seconds

---

**Project Drishti** - Empowering the visually impaired with AI vision
