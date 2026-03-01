#!/bin/bash
# ================================================
# DRISHTI - Pi Setup Script
# ================================================
# Run this on the Raspberry Pi to set up all
# required dependencies and configurations.
#
# Usage: bash setup_pi.sh
# ================================================

set -e  # Exit on error

echo "================================================"
echo "  DRISHTI - Raspberry Pi Setup"
echo "  Pi OS Bookworm 64-bit"
echo "================================================"
echo ""

# Update package list
echo "[1/7] Updating packages..."
sudo apt update -y

# Install required system packages
echo "[2/7] Installing system packages..."
sudo apt install -y \
    ffmpeg \
    mpv \
    mpg123 \
    alsa-utils \
    python3-pip \
    python3-rpi-lgpio \
    python3-gpiozero \
    libportaudio2

# Install Python GPIO library
echo "[3/7] Installing Python packages..."
pip3 install RPi.GPIO --break-system-packages 2>/dev/null || \
pip3 install RPi.GPIO

# ── Audio setup for Pi OS Bookworm ─────────────────────────
# Bookworm uses PipeWire (with a PulseAudio compatibility layer).
# Force 3.5 mm AUX jack through ALSA (numid=3 → headphones output).
echo "[4/7] Configuring audio output (3.5mm AUX jack)..."

# ALSA: route to analogue output (headphone jack)
if amixer cset numid=3 1 2>/dev/null; then
    echo "  ALSA: analogue output selected"
else
    echo "  ALSA numid=3 not available (PipeWire-only system) - skipping"
fi

# PulseAudio / PipeWire-Pulse: set default sink to analogue output
if command -v pactl &>/dev/null; then
    # Find the analogue stereo sink (usually ends with 'analog-stereo')
    ANALOG_SINK=$(pactl list short sinks 2>/dev/null | \
                  grep -i "analog" | awk '{print $2}' | head -n1)
    if [ -n "$ANALOG_SINK" ]; then
        pactl set-default-sink "$ANALOG_SINK" 2>/dev/null && \
            echo "  PulseAudio default sink → $ANALOG_SINK"
        pactl set-sink-volume "$ANALOG_SINK" 80% 2>/dev/null && \
            echo "  Volume set to 80%"
    else
        echo "  Could not find analogue sink via pactl"
    fi
else
    # Legacy ALSA volume controls
    amixer sset 'Headphone' 80% 2>/dev/null || true
    amixer sset 'Master' 80% 2>/dev/null || true
fi

# Create working directories
echo "[5/7] Creating directories..."
mkdir -p /tmp/drishti_audio
mkdir -p ~/drishti

# Copy scripts
echo "[6/7] Setting permissions..."
chmod +x ~/drishti/*.sh 2>/dev/null || true
chmod +x ~/drishti/*.py 2>/dev/null || true

# Quick audio smoke test
echo "[7/7] Audio test (you should hear a short tone)..."
if command -v speaker-test &>/dev/null; then
    timeout 2 speaker-test -t sine -f 880 -l1 2>/dev/null || true
fi

echo ""
echo "================================================"
echo "  ✓ Setup complete!"
echo "================================================"
echo ""
echo "Next steps on the Pi:"
echo "  1. Start camera stream:  bash ~/drishti/start_camera.sh"
echo "  2. Start button handler: python3 ~/drishti/pi_gpio_handler.py"
echo ""
echo "On the laptop:"
echo "  python3 main.py"
echo ""
