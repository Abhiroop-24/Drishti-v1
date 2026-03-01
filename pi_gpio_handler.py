#!/usr/bin/env python3
"""
DRISHTI - GPIO Button & LED Handler (runs on Raspberry Pi)
Listens for button presses and communicates with the laptop.

Uses gpiozero (compatible with Pi OS Bookworm 64-bit)

Buttons:
  B1 (GPIO17) - Capture and describe
  B2 (GPIO27) - Cycle BLIP mode (default → short → story)
  B3 (GPIO22) - Toggle YOLO alerts

RGB LED:
  R (GPIO5), G (GPIO6), B (GPIO13) - connected with common GND
"""

import socket
import json
import time
import sys
import signal
import threading

try:
    from gpiozero import Button, LED
    GPIO_AVAILABLE = True
except ImportError:
    print("gpiozero not available - running in simulation mode")
    GPIO_AVAILABLE = False


# ── Pin Configuration ──────────────────────────────────────
# Buttons (common GND, pulled up internally)
BTN_CAPTURE_PIN = 17   # B1 – Capture & Describe
BTN_MODE_PIN    = 27   # B2 – Cycle BLIP mode
BTN_YOLO_PIN    = 22   # B3 – Toggle YOLO people-detection

# RGB LED  (GND, R, G, B  with current-limiting resistors)
LED_RED_PIN   = 5
LED_GREEN_PIN = 6
LED_BLUE_PIN  = 13

# ── Network Configuration ─────────────────────────────────
LAPTOP_IP   = "10.42.0.1"
LAPTOP_PORT = 9090

# ── Debounce ──────────────────────────────────────────────
DEBOUNCE_SEC = 0.3    # seconds – passed to gpiozero bounce_time

# ── Global LED references ─────────────────────────────────
led_red = None
led_green = None
led_blue = None


def setup_gpio():
    """Initialize GPIO pins using gpiozero."""
    global led_red, led_green, led_blue

    if not GPIO_AVAILABLE:
        return None, None, None

    try:
        # Setup LEDs
        led_red   = LED(LED_RED_PIN)
        led_green = LED(LED_GREEN_PIN)
        led_blue  = LED(LED_BLUE_PIN)

        # Start with blue LED (idle)
        set_led(False, False, True)

        # Setup buttons — pull_up=True means button shorts the pin to GND
        btn_capture = Button(BTN_CAPTURE_PIN, pull_up=True, bounce_time=DEBOUNCE_SEC)
        btn_mode    = Button(BTN_MODE_PIN,    pull_up=True, bounce_time=DEBOUNCE_SEC)
        btn_yolo    = Button(BTN_YOLO_PIN,    pull_up=True, bounce_time=DEBOUNCE_SEC)

        print("GPIO initialized (gpiozero)")
        print(f"  B1 Capture  : GPIO{BTN_CAPTURE_PIN}")
        print(f"  B2 Mode     : GPIO{BTN_MODE_PIN}")
        print(f"  B3 YOLO     : GPIO{BTN_YOLO_PIN}")
        print(f"  LED R/G/B   : GPIO{LED_RED_PIN} / {LED_GREEN_PIN} / {LED_BLUE_PIN}")

        return btn_capture, btn_mode, btn_yolo

    except Exception as e:
        print(f"ERROR: GPIO setup failed: {e}")
        import traceback; traceback.print_exc()
        return None, None, None


def set_led(red, green, blue):
    """Set RGB LED color."""
    if not GPIO_AVAILABLE:
        color = []
        if red: color.append("RED")
        if green: color.append("GREEN")
        if blue: color.append("BLUE")
        print(f"  LED → {'+'.join(color) if color else 'OFF'}")
        return
    
    if led_red:
        led_red.on() if red else led_red.off()
    if led_green:
        led_green.on() if green else led_green.off()
    if led_blue:
        led_blue.on() if blue else led_blue.off()


def send_command(command):
    """Send a command to the laptop."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((LAPTOP_IP, LAPTOP_PORT))
        
        message = json.dumps({"command": command, "timestamp": time.time()})
        sock.sendall(message.encode('utf-8'))
        
        # Wait for response
        response = sock.recv(4096).decode('utf-8')
        sock.close()
        
        resp_data = json.loads(response)
        print(f"  Response: {resp_data.get('status', 'unknown')}")
        
        # Handle LED color from response
        if 'led' in resp_data:
            led = resp_data['led']
            set_led(led.get('r', False), led.get('g', False), led.get('b', False))
        
        return resp_data
        
    except socket.timeout:
        print(f"  ⚠ Timeout connecting to laptop ({LAPTOP_IP}:{LAPTOP_PORT})")
        set_led(True, False, False)  # Red = error
        time.sleep(1)
        set_led(False, False, True)  # Back to blue
        return None
    except ConnectionRefusedError:
        print(f"  ⚠ Connection refused - is the laptop program running?")
        set_led(True, False, False)  # Red = error
        time.sleep(1)
        set_led(False, False, True)  # Back to blue
        return None
    except Exception as e:
        print(f"  ⚠ Error sending command: {e}")
        set_led(True, False, False)  # Red = error
        time.sleep(1)
        set_led(False, False, True)  # Back to blue
        return None


def on_capture_pressed():
    """Handle B1 (Capture) button press."""
    print("\n\U0001f535 B1 pressed: CAPTURE & DESCRIBE")
    set_led(True, True, False)  # Yellow = capturing
    # Send in background thread to avoid blocking
    threading.Thread(target=send_command, args=("capture",), daemon=True).start()


def on_mode_pressed():
    """Handle B2 (Mode) button press."""
    print("\n\U0001f7e2 B2 pressed: CYCLE MODE")
    set_led(False, True, True)  # Cyan = mode change
    threading.Thread(target=send_command, args=("cycle_mode",), daemon=True).start()


def on_yolo_pressed():
    """Handle B3 (YOLO) button press."""
    print("\n\U0001f534 B3 pressed: TOGGLE YOLO ALERTS")
    set_led(True, False, True)  # Purple = toggling
    threading.Thread(target=send_command, args=("toggle_yolo",), daemon=True).start()


def keyboard_mode():
    """Fallback mode using keyboard input (for testing)."""
    print("\n╔══════════════════════════════════════╗")
    print("║  DRISHTI GPIO Simulator (Keyboard)   ║")
    print("╠══════════════════════════════════════╣")
    print("║  Press 1: Capture & Describe (B1)    ║")
    print("║  Press 2: Cycle Mode (B2)            ║")
    print("║  Press 3: Toggle YOLO Alerts (B3)    ║")
    print("║  Press q: Quit                       ║")
    print("╚══════════════════════════════════════╝\n")
    
    while True:
        try:
            key = input("→ Button: ").strip()
            if key == '1':
                print("\n🔵 Simulating B1: CAPTURE & DESCRIBE")
                send_command("capture")
            elif key == '2':
                print("\n🟢 Simulating B2: CYCLE MODE")
                send_command("cycle_mode")
            elif key == '3':
                print("\n🔴 Simulating B3: TOGGLE YOLO ALERTS")
                send_command("toggle_yolo")
            elif key.lower() == 'q':
                break
            else:
                print("  Invalid input. Press 1, 2, 3, or q")
        except (KeyboardInterrupt, EOFError):
            break


def cleanup(signum=None, frame=None):
    """Clean up GPIO on exit."""
    print("\nCleaning up GPIO...")
    if GPIO_AVAILABLE:
        set_led(False, False, False)
        if led_red: led_red.close()
        if led_green: led_green.close()
        if led_blue: led_blue.close()
    sys.exit(0)


def main():
    """Main entry point."""
    print("=" * 50)
    print("  DRISHTI - GPIO Button Handler")
    print("  Raspberry Pi 3B+ Controller")
    print("=" * 50)
    print(f"  Laptop IP: {LAPTOP_IP}:{LAPTOP_PORT}")
    print()
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    btn_capture, btn_mode, btn_yolo = setup_gpio()
    
    if GPIO_AVAILABLE and btn_capture is not None:
        # Register button callbacks
        btn_capture.when_pressed = on_capture_pressed
        btn_mode.when_pressed = on_mode_pressed
        btn_yolo.when_pressed = on_yolo_pressed
        
        print("\n✓ Listening for button presses...")
        print("  Press Ctrl+C to exit\n")
        
        try:
            # Use sleep loop instead of signal.pause() for nohup compatibility
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        keyboard_mode()
    
    cleanup()


if __name__ == "__main__":
    main()
