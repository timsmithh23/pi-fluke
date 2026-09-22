# Pi Fluke

A cheap DIY version of a Fluke network tester, built on a Raspberry Pi 5 with a 3.5" touchscreen.
Plug the Pi's ethernet port into a wall jack and it tells you whether the jack works: link, speed,
DHCP address, gateway, DNS and internet access.

## Hardware
- Raspberry Pi 5
- 3.5" SPI TFT screen, 480x320 (ILI9486 driver, installed with goodtft `LCD35-show`)
- GL.iNet GL-SFT1200 travel router, used for SSH access (see below)

## Commands

Type `fluke` on its own to see this list.

| Command            | What it does |
|--------------------|--------------|
| `fluke test`       | Run the network test once, live on the small screen (and in the terminal). Results stay up until `fluke term` |
| `fluke start`      | **Fluke mode.** The small screen shows "ready", and every cable you plug in gets tested automatically |
| `fluke term`       | Stop fluke mode and give the small screen back to the terminal |
| `fluke boot fluke` | When the Pi turns on, go straight into fluke mode |
| `fluke boot term`  | When the Pi turns on, show the terminal on the small screen (default) |
| `fluke status`     | Show the current mode, the boot mode and the wifi IP |

`netcheck` also runs the test directly (`netcheck eth1` tests a different port).

## What the test checks

**Part 1, the Pi's own connection** (wifi, used for SSH)
1. Wifi connected, and to which network
2. The Pi's IP, shown as the exact `ssh` command to use
3. SSH server running
4. Internet over wifi

**Part 2, the jack being tested** (eth0)
1. **Link**: is there a live cable/port?
2. **Speed/duplex**: e.g. 1000Mb/s Full (a slow speed can mean a bad cable)
3. **DHCP**: did the network give us an IP address?
4. **Gateway**: the router for that network, and whether it answers a ping
5. **DNS servers** the network handed out
6. **Internet ping** through that jack
7. **Web (HTTP)** through that jack. A code other than 200 usually means a login page (captive portal)
8. **DNS lookup**: can names like example.com be turned into IP addresses?

PASS = good, FAIL = problem, INFO = just information.

## How it works (the files)

| File | Job |
|------|-----|
| `bin/fluke` | The main command. Reads the word after `fluke` and runs the matching step |
| `bin/netcheck` | The test itself: a list of checks, each printing PASS/FAIL/INFO |
| `bin/fluke-screen` | Python. Draws the result lines as a picture and writes it straight into the screen's memory (`/dev/fb2`) |
| `bin/fluke-daemon` | Fluke mode. Checks every second whether a cable is plugged in, and runs `netcheck` when one is |
| `systemd/pi-fluke-console.service` | On every boot, puts the terminal on the small screen |
| `systemd/pi-fluke.service` | Runs `fluke-daemon` in the background. `fluke boot fluke` turns this on at boot |
| `install.sh` | One-time setup: links the commands into `/usr/local/bin` and installs the services |

**Screens:** Linux treats each screen as a "framebuffer" (`/dev/fb0` = HDMI, `/dev/fb2` = the small
TFT). The terminal can be moved between them with `con2fbmap`. Fluke mode moves the terminal to HDMI
so it doesn't draw over the test results, and `fluke term` moves it back.

## SSH access at school
School wifi blocks SSH and Tailscale, so the Pi and the laptop both join the **GL.iNet's own wifi**.
SSH then only travels inside the travel router's network and never touches the school's.
For internet, the GL.iNet uses the school wifi in Repeater mode (or a phone hotspot/USB tether).

    ssh admin@raspberrypi.local      (or the IP shown on the screen)

The ethernet port is set to a lower routing priority (metric 700), so plugging it into a test jack
doesn't steal the Pi's wifi connection.

## Install on a fresh Pi
    git clone <this repo> ~/pi-fluke
    cd ~/pi-fluke && ./install.sh
