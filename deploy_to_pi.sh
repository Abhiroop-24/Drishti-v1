#!/bin/bash
# ================================================================
# DRISHTI - Full Deploy to Raspberry Pi
# ================================================================
# Deploys all Pi files, runs setup, and installs auto-start
# systemd services so camera + buttons start on every boot.
#
# Usage:
#   bash deploy_to_pi.sh            # deploy + install services
#   bash deploy_to_pi.sh --no-boot  # deploy only, skip services
# ================================================================

set -e

# ── Config ───────────────────────────────────────────────────
PI_IP="10.42.0.50"
PI_USER="abhiroop"
PI_PASS="12345678"
PI_DIR="/home/abhiroop/drishti"
INSTALL_SERVICES=true

if [[ "$1" == "--no-boot" ]]; then
    INSTALL_SERVICES=false
fi

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"
SCP_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

# Helper: run command on Pi
pi_ssh() { sshpass -p "$PI_PASS" ssh $SSH_OPTS "${PI_USER}@${PI_IP}" "$@"; }
pi_scp() { sshpass -p "$PI_PASS" scp $SCP_OPTS "$@"; }

# ── Banner ───────────────────────────────────────────────────
echo "================================================================"
echo "  DRISHTI - Deploy to Raspberry Pi"
echo "  Target : ${PI_USER}@${PI_IP}:${PI_DIR}"
echo "  Laptop : 10.42.0.1  (ethernet to Pi must be active)"
echo "================================================================"
echo ""

# ── 0. Ensure dependencies ───────────────────────────────────
echo "[0/7] Checking local tools..."
if ! command -v sshpass &>/dev/null; then
    echo "  sshpass not found — installing..."
    sudo apt-get install -y sshpass
fi
echo "  ✓ sshpass available"

# ── 1. Check Pi is reachable ─────────────────────────────────
echo "[1/7] Pinging Pi at ${PI_IP}..."
if ! ping -c 2 -W 3 "$PI_IP" &>/dev/null; then
    echo ""
    echo "  ✗ Cannot reach Pi at $PI_IP"
    echo "  Make sure:"
    echo "    • Ethernet cable is plugged in"
    echo "    • Connection sharing is enabled on laptop NIC"
    echo "    • Pi is powered on"
    exit 1
fi
echo "  ✓ Pi is reachable"

# ── 2. Create directory structure on Pi ─────────────────────
echo "[2/7] Creating directories on Pi..."
pi_ssh "mkdir -p ${PI_DIR}"
echo "  ✓ $PI_DIR"

# ── LAPTOP: Open firewall ports for Pi ← laptop traffic ──────
# NetworkManager's connection-sharing chain blocks everything except
# DHCP (67) and DNS (53).  We need UDP 8080 (camera stream) and
# TCP 9090 (command server from Pi buttons) to pass through.
echo "[2b/7] Opening firewall ports on laptop..."
_open_port() {
    local proto=$1 port=$2
    # Only insert if the rule doesn't already exist
    if ! iptables -C nm-sh-in-eno1 -p "$proto" --dport "$port" -j ACCEPT 2>/dev/null; then
        iptables -I nm-sh-in-eno1 1 -p "$proto" --dport "$port" -j ACCEPT
        echo "  ✓ Opened $proto/$port"
    else
        echo "  ✓ $proto/$port already open"
    fi
}
echo "20222006" | sudo -S bash -c "
    $(declare -f _open_port)
    _open_port udp 8080
    _open_port tcp 9090
" 2>/dev/null || echo "  ⚠ Could not update firewall (run manually if stream fails)"

# ── 3. Copy all Pi files ─────────────────────────────────────
echo "[3/7] Deploying files..."
FILES=(
    "pi_scripts/start_camera.sh"
    "pi_scripts/setup_pi.sh"
    "pi_scripts/drishti-camera.service"
    "pi_scripts/drishti-buttons.service"
    "pi_scripts/install_services.sh"
    "pi_gpio_handler.py"
)
for f in "${FILES[@]}"; do
    pi_scp "$f" "${PI_USER}@${PI_IP}:${PI_DIR}/"
    echo "  ✓ $(basename $f)"
done

# ── 4. Fix permissions ───────────────────────────────────────
echo "[4/7] Setting permissions..."
pi_ssh "chmod +x ${PI_DIR}/*.sh ${PI_DIR}/*.py"
echo "  ✓ Scripts are executable"

# ── 5. Run first-time setup (idempotent) ────────────────────
echo "[5/7] Running setup on Pi (first run installs packages)..."
pi_ssh "bash ${PI_DIR}/setup_pi.sh 2>&1" | tail -25
echo "  ✓ Setup complete"

# ── 6. Install / refresh systemd services ───────────────────
if $INSTALL_SERVICES; then
    echo "[6/7] Installing auto-start services..."
    pi_ssh "bash ${PI_DIR}/install_services.sh 2>&1"
    echo "  ✓ Services installed"
else
    echo "[6/7] Skipping service install (--no-boot passed)"
fi

# ── 7. Final status check ────────────────────────────────────
echo "[7/7] Pi service status:"
pi_ssh "
    cam=\$(systemctl is-active drishti-camera.service  2>/dev/null || echo 'inactive')
    btn=\$(systemctl is-active drishti-buttons.service 2>/dev/null || echo 'inactive')
    echo \"  camera  : \$cam\"
    echo \"  buttons : \$btn\"
"

echo ""
echo "================================================================"
echo "  ✓ Deployment complete!"
echo "================================================================"
echo ""
echo "  Pi services auto-start on every boot."
echo ""
echo "  On the LAPTOP — start the main app:"
echo "    source .venv/bin/activate && python3 main.py"
echo ""
echo "  Or open the debug dashboard:"
echo "    source .venv/bin/activate && python3 debug_server.py"
echo "    Then visit: http://localhost:5000"
echo ""
echo "  Manual Pi commands (SSH):"
echo "    ssh ${PI_USER}@${PI_IP}"
echo "    journalctl -fu drishti-camera    # live camera log"
echo "    journalctl -fu drishti-buttons   # live buttons log"
echo ""
