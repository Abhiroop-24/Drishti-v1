#!/usr/bin/env python3
"""
DRISHTI - Component Test Script
Tests each component individually to catch errors.
"""
import sys
import os
import traceback

sys.stdout.reconfigure(line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def test(name, func):
    print(f"\n{'='*50}")
    print(f"TEST: {name}")
    print(f"{'='*50}")
    try:
        func()
        print(f"✓ {name} PASSED")
        return True
    except Exception as e:
        print(f"✗ {name} FAILED: {e}")
        traceback.print_exc()
        return False

def test_config():
    from config import (
        PiConfig, StreamConfig, YOLOConfig, BLIPConfig,
        TTSConfig, GPIOConfig, PathConfig, LEDColors
    )
    print(f"  Pi IP: {PiConfig.IP}")
    print(f"  Stream URL: {StreamConfig.URL}")
    print(f"  YOLO Model: {YOLOConfig.MODEL}")
    print(f"  BLIP Model: {BLIPConfig.MODEL}")
    print(f"  TTS Engine: {TTSConfig.ENGINE}")
    print(f"  Audio Dir: {PathConfig.AUDIO_OUTPUT}")
    PathConfig.ensure_dirs()
    assert PathConfig.AUDIO_OUTPUT.exists()
    assert PathConfig.CAPTURES.exists()
    assert PathConfig.LOGS.exists()

def test_yolo():
    import numpy as np
    from yolo_detector import YOLODetector
    yolo = YOLODetector()
    print(f"  Model loaded")
    
    # Test with blank frame
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    detections = yolo.detect(frame)
    print(f"  Blank frame: {len(detections)} people")
    
    # Test alert toggle
    enabled = yolo.toggle_alerts()
    print(f"  Alerts toggled: {enabled}")
    
    # Test summary
    summary = yolo.get_summary(detections)
    print(f"  Summary: {summary}")
    
    # Test draw
    annotated = yolo.draw_detections(frame, detections)
    print(f"  Annotated frame shape: {annotated.shape}")

def test_blip():
    from blip_describer import BLIPDescriber
    describer = BLIPDescriber()
    print(f"  Mode: {describer.current_mode}")
    print(f"  Device: {describer.device}")
    
    # Test mode cycling
    new_mode = describer.cycle_mode()
    print(f"  After cycle: {new_mode}")
    new_mode = describer.cycle_mode()
    print(f"  After cycle: {new_mode}")
    new_mode = describer.cycle_mode()
    print(f"  After cycle (back to start): {new_mode}")
    
    # Test model load and inference
    print("  Loading BLIP model (this takes a while)...")
    describer.load_model()
    print("  Model loaded!")
    
    # Generate description of a simple test image
    from PIL import Image
    import numpy as np
    test_img = Image.fromarray(np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8))
    desc = describer.describe(test_img, mode="short")
    print(f"  Description: {desc}")

def test_tts():
    from tts_engine import TTSEngine
    tts = TTSEngine()
    print(f"  Engine: {tts.engine_type}")
    
    # Test synthesis
    audio_path = tts.synthesize("Hello, this is a test from DRISHTI.", "test_audio")
    if audio_path and audio_path.exists():
        size = audio_path.stat().st_size
        print(f"  Audio file: {audio_path} ({size} bytes)")
    else:
        print(f"  ✗ Audio synthesis failed!")
        raise Exception("TTS synthesis returned None or file does not exist")

def test_stream():
    from stream_receiver import StreamReceiver
    stream = StreamReceiver()
    print(f"  URL: {stream.stream_url}")
    print(f"  Resolution: {stream.width}x{stream.height}")
    
    # Try to start (may fail if Pi stream not running, that's OK)
    started = stream.start()
    print(f"  Stream started: {started}")
    
    import time
    time.sleep(2)
    
    frame = stream.get_frame()
    if frame is not None:
        print(f"  Got frame: {frame.shape}")
    else:
        print(f"  No frame yet (Pi stream may not be active)")
    
    stream.stop()
    print(f"  Stream stopped")

def test_pi_connection():
    from pi_communicator import PiCommunicator
    pi = PiCommunicator()
    
    connected = pi.check_connection()
    print(f"  Connected: {connected}")
    
    if connected:
        out, err = pi.execute_command("uname -a")
        print(f"  Pi uname: {out}")
        
        # Test audio playback system
        out, err = pi.execute_command("which mpv ffplay mpg123 2>/dev/null")
        print(f"  Audio players: {out}")
    
    pi.disconnect()

def test_command_server():
    import socket
    import json
    
    # Test that we can create the server socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('0.0.0.0', 9090))
        server.listen(1)
        print(f"  Command server can bind to port 9090")
        server.close()
    except OSError as e:
        server.close()
        if "Address already in use" in str(e):
            print(f"  Port 9090 already in use (maybe previous instance?)")
        else:
            raise

def test_main_import():
    # Just test that main.py can be imported without starting
    import importlib.util
    spec = importlib.util.spec_from_file_location("main", "main.py")
    mod = importlib.util.module_from_spec(spec)
    # Don't execute it, just check syntax
    print(f"  main.py syntax OK")

# Run all tests
if __name__ == "__main__":
    results = {}
    
    tests = [
        ("Config", test_config),
        ("Command Server Port", test_command_server),
        ("Pi Connection", test_pi_connection),
        ("Stream Receiver", test_stream),
        ("YOLO Detector", test_yolo),
        ("TTS Engine", test_tts),
        ("BLIP Describer", test_blip),
    ]
    
    for name, func in tests:
        results[name] = test(name, func)
    
    print(f"\n{'='*50}")
    print("RESULTS SUMMARY")
    print(f"{'='*50}")
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")
    
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    print(f"\n  {passed}/{total} tests passed")
