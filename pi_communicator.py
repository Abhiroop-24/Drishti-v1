"""
DRISHTI - Raspberry Pi Communication Module
Handles SSH connection, audio transfer, and remote commands.
"""

import logging
import time
import paramiko
from scp import SCPClient
from pathlib import Path
from config import PiConfig

logger = logging.getLogger("drishti.pi_comm")


class PiCommunicator:
    """Handles communication with Raspberry Pi over SSH."""

    def __init__(self):
        self.host = PiConfig.IP
        self.user = PiConfig.USER
        self.password = PiConfig.PASSWORD
        self.port = PiConfig.SSH_PORT
        self.ssh = None
        self._connected = False

    def connect(self):
        """Establish SSH connection to Pi."""
        if self._connected and self.ssh:
            try:
                # Quick connectivity check
                transport = self.ssh.get_transport()
                if transport and transport.is_active():
                    return True
                self._connected = False
            except Exception:
                self._connected = False

        try:
            logger.info(f"Connecting to Pi at {self.host}...")
            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                password=self.password,
                timeout=10,
            )
            self._connected = True
            logger.info("Connected to Pi successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Pi: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Close SSH connection."""
        if self.ssh:
            try:
                self.ssh.close()
            except Exception:
                pass
            self.ssh = None
            self._connected = False
            logger.info("Disconnected from Pi")

    def send_audio_and_play(self, local_audio_path, volume=80):
        """
        Send audio file to Pi and play it through AUX jack.

        Args:
            local_audio_path: Path to local audio file
            volume: Playback volume (0-100)

        Returns:
            bool: True if successful
        """
        if not self.connect():
            logger.error("Cannot send audio - not connected to Pi")
            return False

        local_path = Path(local_audio_path)
        if not local_path.exists():
            logger.error(f"Audio file not found: {local_path}")
            return False

        remote_dir = "/tmp/drishti_audio"
        remote_path = f"{remote_dir}/{local_path.name}"

        try:
            # Create remote directory
            self._exec(f"mkdir -p {remote_dir}")
            time.sleep(0.1)

            # Transfer audio file via SCP
            logger.info(f"Transferring audio to Pi: {local_path.name}")
            with SCPClient(self.ssh.get_transport()) as scp:
                scp.put(str(local_path), remote_path)

            logger.info("Audio transferred, playing...")

            # Set audio output to AUX jack and volume
            self._exec(
                f"amixer cset numid=3 1 2>/dev/null; "
                f"amixer sset 'Headphone' {volume}% 2>/dev/null; "
                f"amixer sset 'Master' {volume}% 2>/dev/null"
            )
            time.sleep(0.1)

            # Kill any currently playing audio
            self._exec(
                "pkill -f 'mpv.*drishti_audio' 2>/dev/null; "
                "pkill -f 'ffplay.*drishti_audio' 2>/dev/null; "
                "pkill -f 'mpg123.*drishti_audio' 2>/dev/null; "
                "true"
            )
            time.sleep(0.2)

            # Play audio file (mpv → ffplay → mpg123 fallback chain).
            # Do NOT force --audio-device=alsa on Bookworm: PipeWire
            # handles routing to the default sink (set up by setup_pi.sh).
            play_cmd = (
                f"nohup sh -c '"
                f"mpv --no-video {remote_path} 2>/dev/null || "
                f"ffplay -nodisp -autoexit {remote_path} 2>/dev/null || "
                f"mpg123 {remote_path} 2>/dev/null"
                f"' > /dev/null 2>&1 &"
            )

            self._exec(play_cmd)
            logger.info("Audio playback started on Pi")
            return True

        except Exception as e:
            logger.error(f"Failed to send/play audio: {e}")
            return False

    def set_led(self, red, green, blue, gpio_red=5, gpio_green=6, gpio_blue=13):
        """
        Set RGB LED color on Pi using pinctrl (Bookworm compatible).
        This does NOT conflict with gpiozero running in the GPIO handler.

        Args:
            red, green, blue: True/False for each color
            gpio_red, gpio_green, gpio_blue: GPIO pin numbers

        Returns:
            bool: True if successful
        """
        if not self.connect():
            return False

        try:
            # Use pinctrl which is available on Pi OS Bookworm
            # and doesn't conflict with gpiozero
            cmds = []
            for pin, val in [(gpio_red, red), (gpio_green, green), (gpio_blue, blue)]:
                cmds.append(f"pinctrl set {pin} op")     # set as output
                cmds.append(f"pinctrl set {pin} {'dh' if val else 'dl'}")  # high or low

            cmd = " && ".join(cmds)
            self._exec(cmd)
            return True
        except Exception as e:
            logger.error(f"Failed to set LED: {e}")
            return False

    def set_led_color(self, color_tuple):
        """
        Set LED using a color tuple from LEDColors.

        Args:
            color_tuple: (red, green, blue) booleans
        """
        r, g, b = color_tuple
        return self.set_led(r, g, b)

    def check_connection(self):
        """Check if Pi is reachable."""
        try:
            if self.connect():
                out, _err = self.execute_command("hostname")
                if out:
                    logger.info(f"Pi hostname: {out}")
                    return True
        except Exception:
            pass
        return False

    def execute_command(self, command, timeout=10):
        """
        Execute a command on Pi and return output.

        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds

        Returns:
            tuple: (stdout, stderr) strings
        """
        if not self.connect():
            return None, "Not connected"

        try:
            stdin, stdout, stderr = self.ssh.exec_command(
                command, timeout=timeout
            )
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            return out, err
        except Exception as e:
            return None, str(e)

    def _exec(self, command, timeout=10):
        """Execute command without waiting for output (fire-and-forget)."""
        if not self._connected or not self.ssh:
            return
        try:
            self.ssh.exec_command(command, timeout=timeout)
        except Exception as e:
            logger.debug(f"Command exec error: {e}")

    def stop_audio(self):
        """Stop any currently playing audio on Pi."""
        if not self.connect():
            return False
        try:
            self._exec(
                "pkill -f 'mpv.*drishti_audio' 2>/dev/null; "
                "pkill -f 'ffplay.*drishti_audio' 2>/dev/null; "
                "pkill -f 'mpg123.*drishti_audio' 2>/dev/null; "
                "true"
            )
            return True
        except Exception:
            return False

    def __del__(self):
        """Cleanup on destruction."""
        self.disconnect()
