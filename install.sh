#!/usr/bin/env bash
# install.sh — sets up the Pi Fluke commands. Run once:  ./install.sh
# Commands are linked (not copied), so editing files in this folder takes effect right away.
cd "$(dirname "$0")"
sudo apt-get install -y python3-pil python3-numpy fonts-dejavu-core ethtool fbset >/dev/null

for f in bin/*; do
  chmod +x "$f"
  sudo ln -sf "$PWD/$f" /usr/local/bin/$(basename "$f")
done

sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable -q pi-fluke-console      # terminal on the small screen at boot
echo "Installed. Type:  fluke"
