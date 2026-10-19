# Wi-Fi button: nearby AP scanner, AP info screen and the Wi-Fi speed test

import os
import re
import math
import time
import threading
import subprocess
import tkinter as tk
from datetime import datetime

from .config import WIFI_INTERFACE, BG, CARD_BG, BAR_BG, logger
from .graph import MetricsGraph
from .speed import get_ip, route_interface, run_ping_test, run_bandwidth_test, speed_test_lock


# --- WI-FI SCANNER HELPERS ---

# 2437 -> 6, 5805 -> 161, 5975 -> 5 (6 GHz)
def freq_to_channel(freq):
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    if 5925 <= freq <= 7125:
        return (freq - 5950) // 5
    return 0


# parses "iw dev wlan0 scan" output into a list of AP dicts
def parse_iw_scan(text):
    access_points = []
    ap = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # each AP starts with a line like: BSS 18:60:41:57:46:56(on wlan0) -- associated
        if raw_line.startswith("BSS "):
            ap = {
                "bssid": raw_line[4:21], "ssid": "<hidden>", "freq": 0, "channel": 0,
                "signal": -100, "security": "OPEN", "auth": "", "width": "20 MHz",
                "standard": "a/b/g", "clients": "--", "busy": "--",
                "connected": "associated" in raw_line,
            }
            access_points.append(ap)
            continue

        if ap is None:
            continue

        if line.startswith("freq:"):
            ap["freq"] = int(float(line.split(":")[1]))
            ap["channel"] = freq_to_channel(ap["freq"])
        elif line.startswith("signal:"):
            ap["signal"] = int(float(line.split(":")[1].split()[0]))
        elif line.startswith("SSID:"):
            name = line[5:].strip()
            if name != "" and name.replace("\\x00", "") != "":
                ap["ssid"] = name
        elif line.startswith("capability:") and "Privacy" in line:
            ap["security"] = "WEP"
        elif line.startswith("WPA:"):
            ap["security"] = "WPA"
        elif line.startswith("RSN:"):
            ap["security"] = "WPA2"
        elif "Authentication suites:" in line:
            ap["auth"] = ap["auth"] + line
        elif "station count:" in line:
            ap["clients"] = line.split(":")[1].strip()
        elif "channel utilisation:" in line:
            # comes as "6/255", turn it into a percent
            parts = line.split(":")[1].strip().split("/")
            ap["busy"] = str(round(int(parts[0]) * 100 / int(parts[1]))) + "%"
        elif "channel width:" in line and "MHz" in line and "STA" not in line:
            width = re.search(r"\((.*MHz)\)", line)
            if width:
                ap["width"] = width.group(1)
        elif line.startswith("HT capabilities") and ap["standard"] == "a/b/g":
            ap["standard"] = "Wi-Fi 4 (n)"
        elif line.startswith("VHT capabilities"):
            ap["standard"] = "Wi-Fi 5 (ac)"
        elif line.startswith("HE capabilities"):
            ap["standard"] = "Wi-Fi 6 (ax)"
        elif line.startswith("EHT capabilities"):
            ap["standard"] = "Wi-Fi 7 (be)"

    # SAE is WPA3, lots of APs offer both
    for ap in access_points:
        if "SAE" in ap["auth"] and "PSK" in ap["auth"]:
            ap["security"] = "WPA2/3"
        elif "SAE" in ap["auth"]:
            ap["security"] = "WPA3"
        elif "802.1X" in ap["auth"]:
            ap["security"] = "WPA2-ENT"

    return access_points


# False on a VM or a Pi with wifi turned off, no radio to use
def has_wifi():
    return os.path.exists(f"/sys/class/net/{WIFI_INTERFACE}/wireless")


# takes ~4s. if the radio is busy, retry then use its cached results
# returns APs strongest first
def scan_wifi():
    if not has_wifi():
        logger.warning("no wifi adapter (%s) - nothing to scan", WIFI_INTERFACE)
        return []

    output = ""
    for attempt in range(3):
        result = subprocess.run(["iw", "dev", WIFI_INTERFACE, "scan"], capture_output=True, text=True, timeout=20)
        output = result.stdout
        if "BSS " in output:
            break
        time.sleep(1)

    if "BSS " not in output:
        logger.warning("wifi scan failed, using cached results")
        result = subprocess.run(["iw", "dev", WIFI_INTERFACE, "scan", "dump"], capture_output=True, text=True, timeout=10)
        output = result.stdout

    access_points = parse_iw_scan(output)
    access_points.sort(key=lambda ap: ap["signal"], reverse=True)
    return access_points


# which network the Pi's wifi is on and how strong
# returns {"ssid": ..., "signal": ...} or None if not connected
def parse_iw_link(text):
    if not text.startswith("Connected"):
        return None
    link = {"ssid": "<hidden>", "signal": None}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SSID:"):
            link["ssid"] = line[5:].strip()
        elif line.startswith("signal:"):
            link["signal"] = int(float(line.split(":")[1].split()[0]))
    return link


def wifi_link():
    try:
        result = subprocess.run(["iw", "dev", WIFI_INTERFACE, "link"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return parse_iw_link(result.stdout)


# green strong, yellow ok, orange weak, red very weak
def signal_color(dbm):
    if dbm >= -60:
        return "#76FF03"
    if dbm >= -70:
        return "#FFD700"
    if dbm >= -80:
        return "#FFA500"
    return "#FF5252"


# very rough guess (assumes 20 dBm AP, indoor loss), can be off by 2x
# good enough to tell which AP is closer
def estimate_distance(dbm, freq):
    if freq <= 0:
        return "--"
    loss_at_one_metre = 20 * math.log10(freq) - 27.55
    metres = 10 ** ((20 - dbm - loss_at_one_metre) / 33)
    if metres < 10:
        return "~" + str(round(metres, 1)) + " m"
    return "~" + str(round(metres)) + " m"


def format_ap_detail(ap):
    if ap["freq"] < 3000:
        band = "2.4 GHz"
    elif ap["freq"] < 5925:
        band = "5 GHz"
    else:
        band = "6 GHz"

    quality = 2 * (ap["signal"] + 100)   # -50 dBm+ = 100%, -100 dBm = 0%
    if quality > 100:
        quality = 100
    if quality < 0:
        quality = 0

    lines = [
        f"SSID:     {ap['ssid']}",
        f"BSSID:    {ap['bssid']}",
        f"Signal:   {ap['signal']} dBm ({quality}%)",
        f"Distance: {estimate_distance(ap['signal'], ap['freq'])} (rough)",
        f"Channel:  {ap['channel']} ({band})",
        f"Freq:     {ap['freq']} MHz",
        f"Width:    {ap['width']}",
        f"Security: {ap['security']}",
        f"Standard: {ap['standard']}",
        f"Clients:  {ap['clients']}",
        f"Ch. busy: {ap['busy']}",
    ]
    if ap["connected"]:
        lines.append("")
        lines.append("(the Pi's own wifi is on this AP)")
    return "\n".join(lines)


# --- WI-FI SCREENS (mixed into FlukeApp, see app.py) ---

class WifiScreens:

    # one button per AP, strongest first
    def build_wifi_ui(self):
        self.wifi_scanning = False
        self.wifi_frame = tk.Frame(self.container, bg=BG)
        self.make_header(self.wifi_frame, "WI-FI SCANNER")

        bar = tk.Frame(self.wifi_frame, bg=BG)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=4)
        self.wifi_scan_button = self.make_button(bar, "Scan", self.start_wifi_scan)
        self.wifi_scan_button.pack(side=tk.LEFT, expand=True)
        self.make_button(bar, "Speed Test", self.open_wifi_speed).pack(side=tk.LEFT, expand=True)
        self.make_button(bar, "Back", self.go_back_to_main).pack(side=tk.LEFT, expand=True)

        self.wifi_status_lbl = tk.Label(
            self.wifi_frame, text="", font=("Helvetica", 10), bg=BG, fg="#888888"
        )
        self.wifi_status_lbl.pack(fill=tk.X)

        self.wifi_canvas, self.wifi_rows = self.make_scroll_area(self.wifi_frame)

    # shown when a row is tapped
    def build_ap_ui(self):
        self.ap_frame = tk.Frame(self.container, bg=BG)
        self.make_header(self.ap_frame, "ACCESS POINT")

        self.make_button(self.ap_frame, "Back", self.show_wifi_frame).pack(side=tk.BOTTOM, pady=4)

        self.ap_canvas, ap_inner = self.make_scroll_area(self.ap_frame)
        self.ap_lbl = tk.Label(
            ap_inner, text="", font=("Courier", 12, "bold"),
            bg=BG, fg="#00E5FF", justify=tk.LEFT, anchor="nw"
        )
        self.ap_lbl.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

    # same graph as the results screen but over wifi instead of the cable
    def build_wifi_speed_ui(self):
        self.wifi_speed_running = False
        self.wifi_speed_frame = tk.Frame(self.container, bg=BG)
        self.make_header(self.wifi_speed_frame, "WI-FI SPEED TEST")

        bar = tk.Frame(self.wifi_speed_frame, bg=BG)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=4)
        self.wifi_speed_button = self.make_button(bar, "Run Again", self.start_wifi_speed_test)
        self.wifi_speed_button.pack(side=tk.LEFT, expand=True)
        self.make_button(bar, "Back", self.show_wifi_frame).pack(side=tk.LEFT, expand=True)

        self.wifi_speed_status_lbl = tk.Label(
            self.wifi_speed_frame, text="", font=("Helvetica", 11), bg=BG, fg="#00E5FF"
        )
        self.wifi_speed_status_lbl.pack(fill=tk.X)

        self.wifi_speed_canvas, inner = self.make_scroll_area(self.wifi_speed_frame)
        card = tk.Frame(inner, bg=CARD_BG, bd=2, relief=tk.RIDGE)
        card.pack(fill=tk.X, padx=15, pady=8)
        tk.Label(card, text="Performance Metrics (Wi-Fi)", font=("Helvetica", 14, "bold"),
                 bg=CARD_BG, fg="#FFFFFF").pack(pady=4)
        self.wifi_graph = MetricsGraph(card, WIFI_INTERFACE)
        self.wifi_graph.canvas.pack(fill=tk.X, padx=8, pady=6)

    def open_wifi(self):
        if self.current_view == "scanner" or self.current_view == "results":
            self.return_view = self.current_view
        self.show_wifi_frame()
        self.start_wifi_scan()

    def show_wifi_frame(self):
        self.show_frame(self.wifi_frame, "wifi")

    # scan takes ~4s so it runs on a thread to keep the screen responsive
    def start_wifi_scan(self):
        if self.wifi_scanning:
            return
        if not has_wifi():
            self.wifi_status_lbl.config(text=f"No Wi-Fi adapter found ({WIFI_INTERFACE})")
            return
        self.wifi_scanning = True
        self.wifi_scan_button.config(text="Scanning...", fg="#FFFF00")
        self.wifi_status_lbl.config(text="Scanning for nearby access points...")
        threading.Thread(target=self.wifi_scan_thread, daemon=True).start()

    def wifi_scan_thread(self):
        try:
            access_points = scan_wifi()
        except Exception:
            logger.exception("wifi scan failed")
            access_points = []
        self.root.after(0, self.show_wifi_results, access_points)

    # each row: signal (coloured), name, channel, security
    def show_wifi_results(self, access_points):
        self.wifi_scanning = False
        self.wifi_scan_button.config(text="Scan", fg="#FFFFFF")

        for child in self.wifi_rows.winfo_children():
            child.destroy()
        self.wifi_canvas.yview_moveto(0)

        stamp = datetime.now().strftime("%I:%M:%S %p")
        self.wifi_status_lbl.config(text=f"{len(access_points)} access points  |  scanned {stamp}  |  tap one for info")

        if len(access_points) == 0:
            tk.Label(self.wifi_rows, text="No access points found", font=("Helvetica", 14),
                     bg=BG, fg="#888888").pack(pady=30)
            return

        for ap in access_points:
            name = ap["ssid"]
            if ap["connected"]:
                name = "*" + name     # * = the network the Pi is on
            text = f"{ap['signal']:>4} {name[:16]:<16} ch{ap['channel']:<4}{ap['security']}"
            tk.Button(
                self.wifi_rows, text=text, font=("Courier", 12, "bold"), anchor="w",
                bg=CARD_BG, fg=signal_color(ap["signal"]), activebackground=BAR_BG,
                activeforeground="#FFFFFF", relief=tk.RIDGE, pady=3,
                command=lambda a=ap: self.show_ap_info(a)
            ).pack(fill=tk.X, padx=6, pady=1)

    # ignore the tap if the finger was dragging, otherwise scrolling
    # would open whatever row you started on
    def show_ap_info(self, ap):
        if self.drag_moved:
            return
        self.ap_lbl.config(text=format_ap_detail(ap))
        self.show_frame(self.ap_frame, "ap")
        self.ap_canvas.yview_moveto(0)

    # --- WI-FI SPEED TEST ---

    def open_wifi_speed(self):
        self.show_frame(self.wifi_speed_frame, "wifi_speed")
        self.wifi_speed_canvas.yview_moveto(0)
        self.start_wifi_speed_test()

    def start_wifi_speed_test(self):
        if self.wifi_speed_running:
            return
        if not has_wifi():
            self.wifi_speed_status_lbl.config(text=f"No Wi-Fi adapter found ({WIFI_INTERFACE})", fg="#FF5252")
            return
        wifi_ip = get_ip(WIFI_INTERFACE)
        if wifi_ip == "":
            self.wifi_speed_status_lbl.config(text="Pi isn't connected to a Wi-Fi network", fg="#FF5252")
            return
        if route_interface(wifi_ip) not in ("", WIFI_INTERFACE):
            # traffic would go out the cable, so the numbers wouldn't be wifi
            self.wifi_speed_status_lbl.config(text="Internet goes out the cable - unplug it to test Wi-Fi",
                                              fg="#FF5252")
            return
        self.wifi_speed_running = True
        self.wifi_speed_button.config(text="Testing...", fg="#FFFF00")
        threading.Thread(target=self.wifi_speed_thread, daemon=True).start()

    def set_wifi_speed_status(self, text, color="#00E5FF"):
        self.root.after(0, lambda: self.wifi_speed_status_lbl.config(text=text, fg=color))

    # ping burst + bandwidth test over wifi, then draw the graph
    def wifi_speed_thread(self):
        results = {"speed_down": "Failed", "speed_up": "Failed", "ping_latency": "Failed",
                   "ping_jitter": "Failed", "ping_loss": "Failed"}
        curves = {"down": [], "up": []}
        try:
            link = wifi_link()
            name = link["ssid"] if link else WIFI_INTERFACE
            logger.info("wifi speed test started on %s", name)

            self.set_wifi_speed_status(f"{name}: testing latency / jitter / loss...")
            run_ping_test(results, WIFI_INTERFACE)

            if speed_test_lock.locked():
                self.set_wifi_speed_status("Waiting for the cable speed test to finish...")
            curves = run_bandwidth_test(
                results, WIFI_INTERFACE,
                on_status=lambda text: self.set_wifi_speed_status(f"{name}: {text}")
            )

            stamp = datetime.now().strftime("%I:%M:%S %p")
            signal = f"  ({link['signal']} dBm)" if link and link["signal"] is not None else ""
            self.set_wifi_speed_status(f"{name}{signal}  |  tested {stamp}", "#888888")
        except Exception:
            logger.exception("wifi speed test failed")
            self.set_wifi_speed_status("Wi-Fi speed test failed (see notafluke log)", "#FF5252")
        self.root.after(0, self.finish_wifi_speed_test, results, curves)

    def finish_wifi_speed_test(self, results, curves):
        self.wifi_speed_running = False
        self.wifi_speed_button.config(text="Run Again", fg="#FFFFFF")
        self.wifi_graph.show(results, curves)
