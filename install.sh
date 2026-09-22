#!/usr/bin/env bash
# install.sh — sets up the Pi Fluke commands. Run once:  ./install.sh
# Commands are linked (not copied), so editing files in this folder takes effect right away.
cd "$(dirname "$0")"
sudo apt-get install -y python3-pil python3-numpy fonts-dejavu-core ethtool fbset lldpd python3-evdev >/dev/null

# lldpd = listens for the switch name/port (Cisco CDP + LLDP). Listen-only, eth0 only.
echo 'DAEMON_ARGS="-r -c -I eth0"' | sudo tee /etc/default/lldpd >/dev/null
sudo systemctl enable -q lldpd && sudo systemctl restart lldpd

for f in bin/*; do
  chmod +x "$f"
  sudo ln -sf "$PWD/$f" /usr/local/bin/$(basename "$f")
done

sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable -q pi-fluke-console      # terminal on the small screen at boot
echo "Installed. Type:  fluke"
