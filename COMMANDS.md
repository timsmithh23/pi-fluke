# Commands

Type these in the Pi's terminal, either on the small screen or over SSH.

## Connect
| What | How |
|---|---|
| SSH into the Pi | Join the GL.iNet wifi (`GL-SFT1200-81b-5G`), then `ssh admin@raspberrypi.local` (or the IP shown on the small screen) |

## Testing
| Command | What it does |
|---|---|
| `fluke test` | Test the jack once, live on the small screen |
| `fluke speed` | Speed test (download / upload / ping) through the jack |
| `netcheck` | Same test as `fluke test`, but text only |

## Modes
| Command | What it does |
|---|---|
| `fluke start` | Fluke mode: tests every cable you plug in, automatically |
| `fluke term` | Stop fluke mode, small screen goes back to the terminal |
| `fluke boot fluke` | Start in fluke mode when the Pi turns on |
| `fluke boot term` | Start with the terminal on the small screen (default) |
| `fluke status` | Show the current mode, boot mode and wifi IP |

## Screen
| Command / button | What it does |
|---|---|
| `xinput-calibrator` | Calibrate the touchscreen (tap the 4 crosses) |
| **SPEED** button | Run a speed test |
| **END** button | Leave the tester, back to the terminal |

## Other
| Command | What it does |
|---|---|
| `fluke` | Show the list of fluke commands |
| `./install.sh` | Set up everything on a new Pi (run it inside the repo folder) |
| `killclaude` | Remove Claude Code and the Pi's GitHub login (only exists on this Pi, not in the repo) |
