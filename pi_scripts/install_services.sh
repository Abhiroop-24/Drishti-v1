#!/bin/bash
# ================================================
# DRISHTI - Install Systemd Auto-start Services
# ================================================
# Run this on the Raspberry Pi ONCE after first deploy.
# After this, camera + GPIO handler start automatically
# every time the Pi boots.
#
# Usage: bash ~/drishti/install_services.sh
# ================================================

set -e

DRISHTI_DIR="/home/abhiroop/drishti"
SERVICE_DIR="/etc/systemd/system"

echo "================================================"
echo "  DRISHTI - Installing Auto-start Services"
echo "================================================"
echo ""

# ── 1. Verify scripts exist ───────────────────────────────
echo "[1/6] Checking files..."
for f in "$DRISHTI_DIR/start_camera.sh" \
         "$DRISHTI_DIR/pi_gpio_handler.py"; do
    if [ ! -f "$f" ]; then
        echo "  ✗ Missing: $f"
        echo "  Run deploy_to_pi.sh from the laptop first."
        exit 1
    fi
    echo "  ✓ $f"
done

# ── 2. Make scripts executable ───────────────────────────
echo "[2/6] Setting permissions..."
chmod +x "$DRISHTI_DIR/start_camera.sh"
chmod +x "$DRISHTI_DIR/pi_gpio_handler.py"

# ── 3. Install service files ─────────────────────────────
echo "[3/6] Installing systemd service files..."
sudo cp "$DRISHTI_DIR/drishti-camera.service"  "$SERVICE_DIR/"
sudo cp "$DRISHTI_DIR/drishti-buttons.service" "$SERVICE_DIR/"
echo "  ✓ Services copied to $SERVICE_DIR"

# ── 4. Add user to gpio + video groups (needed for hw access) ──
echo "[4/6] Ensuring group memberships..."
sudo usermod -aG gpio  abhiroop 2>/dev/null || true
sudo usermod -aG video abhiroop 2>/dev/null || true
echo "  ✓ Groups: gpio, video"

# ── 5. Enable and start services ─────────────────────────
echo "[5/6] Enabling services..."
sudo systemctl daemon-reload

sudo systemctl enable drishti-camera.service
sudo systemctl enable drishti-buttons.service
echo "  ✓ Services will start on every boot"

echo "[6/6] Starting services now..."
sudo systemctl restart drishti-camera.service
sleep 3
sudo systemctl restart drishti-buttons.service

# ── 6. Status report ─────────────────────────────────────
echo ""
echo "================================================"
echo "  Service Status"
echo "================================================"
systemctl is-active drishti-camera.service  && echo "  ✓ drishti-camera : RUNNING" \
    || echo "  ✗ drishti-camera : FAILED"
systemctl is-active drishti-buttons.service && echo "  ✓ drishti-buttons: RUNNING" \
    || echo "  ✗ drishti-buttons: FAILED"

echo ""
echo "================================================"
echo "  ✓ Auto-start installed!"
echo "================================================"
echo ""
echo "Useful commands:"
echo "  journalctl -fu drishti-camera   # live camera log"
echo "  journalctl -fu drishti-buttons  # live buttons log"
echo "  sudo systemctl restart drishti-camera"
echo "  sudo systemctl restart drishti-buttons"
echo "  sudo systemctl disable drishti-camera  # remove auto-start"
echo ""
