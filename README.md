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
| `xinput-calibrator` (or `fluke calibrate`) | Calibrate the touchscreen: tap the 4 red crosses, then tap around to check the dots land under your finger |

`netcheck` also runs the test directly (`netcheck eth1` tests a different port).

## The screen (made to look like PiScout)
While the test runs, the small screen lists each check live. When it finishes, it switches to a
summary card laid out like PiScout's display (PiScout is the Pi Zero e-paper project this was based on):

```
PI FLUKE                    CDPv2      <- which protocol told us the switch info
──────────────────────────────────
SW:    SWITCH-01                        <- switch name
IP:    10.10.1.2                        <- switch's management IP
PORT:  Gi1/0/24                         <- switch port this jack is wired to
VLAN:  120                              <- data VLAN
VOICE: 130                              <- voice VLAN (if the switch sends it)
LINK:  1G FD                            <- speed + full/half duplex
INFO DHCP 10.10.120.55  GW yes  NET yes
INFO ssh admin@192.168.8.160
```

In fluke mode, with no cable plugged in, it shows PiScout's "Waiting for link..." screen.

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
9. **Switch + port**: the switch's name and the exact port (e.g. `GigabitEthernet1/0/12`), plus VLAN and the switch's IP if it sends them. Cisco switches announce this with **CDP** every 60s and other brands use **LLDP** every 30s. The `lldpd` service listens for it in the background (listen-only, it never sends anything), so the test can take up to a minute on a freshly plugged cable

PASS = good, FAIL = problem, INFO = just information.

## How it works (the files)

Every script in this repo has a plain-English `#` comment above each line of code explaining what it does,
so you can read any file top to bottom.

| File | Job |
|------|-----|
| `bin/fluke` | The main command. Reads the word after `fluke` and runs the matching step |
| `bin/netcheck` | The test itself: a list of checks, each printing PASS/FAIL/INFO |
| `bin/fluke-screen` | Python. Draws the result lines as a picture and writes it straight into the screen's memory (`/dev/fb2`) |
| `bin/fluke-daemon` | Fluke mode. Checks every second whether a cable is plugged in, and runs `netcheck` when one is |
| `systemd/pi-fluke-console.service` | On every boot, puts the terminal on the small screen |
| `systemd/pi-fluke.service` | Runs `fluke-daemon` in the background. `fluke boot fluke` turns this on at boot |
| `bin/fluke-calibrate` | Python. Touch calibration: compares where you tapped (raw touch numbers) with where the crosses were, and saves the conversion to `/etc/pi-fluke-touch.json` and the desktop's touch settings |
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
