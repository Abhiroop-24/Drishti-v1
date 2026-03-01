#!/bin/bash
# ================================================
# DRISHTI - Camera Stream Script (Raspberry Pi)
# ================================================
# Streams Pi Camera video to laptop via UDP
# Uses rpicam-vid (Pi OS Bookworm 64-bit)
#
# Usage: ./start_camera.sh
# ================================================

# Settings
WIDTH=800
HEIGHT=600
FPS=30
PORT=8080
LAPTOP_IP="10.42.0.1"

echo "================================================"
echo "  DRISHTI - Camera Stream"
echo "  Raspberry Pi 3B+ → Laptop"
echo "================================================"
echo "  Resolution: ${WIDTH}x${HEIGHT} @ ${FPS}fps"
echo "  Target: udp://${LAPTOP_IP}:${PORT}"
echo "================================================"
echo ""

# Check if rpicam-vid is available
if ! command -v rpicam-vid &> /dev/null; then
    echo "❌ rpicam-vid not found!"
    echo "   Make sure you have Pi OS Bookworm with rpicam-apps installed"
    echo "   Try: sudo apt install rpicam-apps"
    exit 1
fi

# Check if ffmpeg is available
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ ffmpeg not found!"
    echo "   Try: sudo apt install ffmpeg"
    exit 1
fi

# Kill any existing camera processes
echo "Stopping any existing camera streams..."
pkill -f rpicam-vid 2>/dev/null
pkill -f "ffmpeg.*mpegts" 2>/dev/null
sleep 1

echo "Starting camera stream..."
echo "Press Ctrl+C to stop"
echo ""

# Start the camera stream
# - rpicam-vid captures H264 video from Pi Camera
# - ffmpeg wraps it in MPEG-TS for UDP streaming
# - inline: SPS/PPS headers in every keyframe
# - intra: keyframe every FPS frames (1 second)
# - baseline profile: lowest latency
# - flush: immediate output
rpicam-vid -t 0 \
    --width $WIDTH \
    --height $HEIGHT \
    --framerate $FPS \
    --inline \
    --intra $FPS \
    --profile baseline \
    --codec h264 \
    --flush \
    -o - | \
ffmpeg -f h264 -i - \
    -c:v copy \
    -f mpegts \
    -flush_packets 1 \
    "udp://${LAPTOP_IP}:${PORT}?pkt_size=1316"
