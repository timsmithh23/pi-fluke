#!/usr/bin/env bash
# install.sh — sets up the Pi Fluke commands. Run once:  ./install.sh
# Commands are linked (not copied), so editing files in this folder takes effect right away.
#
# HOW TO READ THIS FILE: every line of code has a "#" comment above it explaining it.

# Go into the folder this script is in (so "bin/..." paths work from anywhere).
cd "$(dirname "$0")"
# Install the programs we need: Pillow + numpy (drawing), a font, ethtool (link speed), fbset (screens),
# lldpd (switch name/port), python3-evdev (reading the touchscreen).
sudo apt-get install -y python3-pil python3-numpy fonts-dejavu-core ethtool fbset lldpd python3-evdev >/dev/null

# lldpd = listens for the switch name/port (Cisco CDP + LLDP). Listen-only, eth0 only.
# -r = receive only (never send), -c = understand Cisco CDP, -I eth0 = only listen on the ethernet port.
echo 'DAEMON_ARGS="-r -c -I eth0"' | sudo tee /etc/default/lldpd >/dev/null
# Turn lldpd on at boot, and restart it now so the settings above take effect.
sudo systemctl enable -q lldpd && sudo systemctl restart lldpd

# For every script in the bin folder...
for f in bin/*; do
  # ...make it runnable...
  chmod +x "$f"
  # ...and put a shortcut (link) to it in /usr/local/bin, so you can type its name from anywhere.
  sudo ln -sf "$PWD/$f" /usr/local/bin/$(basename "$f")
done

# Copy the background service files to where the system looks for them.
sudo cp systemd/*.service /etc/systemd/system/
# Tell the system to re-read its service files.
sudo systemctl daemon-reload
# Turn on the service that puts the terminal on the small screen at every boot.
sudo systemctl enable -q pi-fluke-console      # terminal on the small screen at boot
# Done.
echo "Installed. Type:  fluke"
