# main window: top bar, scroll arrows, scanner/results screens and the scan loop
# history and wifi screens come from history.py and wifi.py as mixins

import os
import time
import threading
import tkinter as tk
from tkinter import ttk

from .config import (VERSION, INTERFACE, WIFI_INTERFACE, CARRIER_PATH, SWITCH_DISCOVERY_TIMEOUT,
                     BG, CARD_BG, BAR_BG, ACCENT, logger)
from .history import HistoryScreens, save_scan_record, now_stamp, is_clock_synced, format_stamp
from .wifi import WifiScreens
from .graph import MetricsGraph
from .speed import get_ip, run_ping_test, run_bandwidth_test
from .switch import query_lldpd, parse_switch_topology, start_cdp_listener

# same size as the Pi's TFT so a VM window looks like the real screen
WINDOW_SIZE = "480x320"


# blank scan record. "--" while waiting, then each test starts as "Failed" until it passes
def empty_scan_results(started=False):
    failed = "Failed" if started else "--"
    return {
        "started_at": now_stamp() if started else "--",
        "finished_at": "--",
        "clock_synced": is_clock_synced() if started else "unknown",
        "complete": False,
        "switch_name": "Not Found" if started else "--",
        "switch_ip": "Not Advertised" if started else "--",
        "switch_port": "Not Found" if started else "--",
        "switch_vlan": "None / Untagged" if started else "--",
        "switch_voice": "None" if started else "--",
        "switch_proto": "--",
        "speed_down": failed,
        "speed_up": failed,
        "speed_ping": failed,
        "ping_latency": failed,
        "ping_jitter": failed,
        "ping_loss": failed,
        "dns_queries": 0,
        "dns_last_domain": "None" if started else "--",
        "dns_server": "None" if started else "--",
        "dns_time": failed,
        "switch_poe": "Unknown (no CDP/LLDP)" if started else "--",
        "speed_rating": "--"
    }


class FlukeApp(HistoryScreens, WifiScreens):
    # windowed = 480x320 window for VM/desktop, start_workers=False is for tests
    def __init__(self, root, windowed=False, start_workers=True):
        self.root = root
        self.root.title("Not-A-Fluke")
        if windowed:
            self.root.geometry(WINDOW_SIZE)
        else:
            self.root.attributes("-fullscreen", True)
        self.root.configure(bg=BG)
        self.root.bind("<Escape>", self.close_window)

        # which screen is up, and where Back on History/Wi-Fi goes
        self.current_view = "scanner"
        self.return_view = "scanner"
        self.history_records = []
        self.history_page = 0
        self.exit_armed = False
        self.scan_results = empty_scan_results()
        self.speed_curves = {"down": [], "up": []}   # samples for the speed graph

        # if the finger moved enough it's a drag, so don't also press the button under it
        self.drag_start_y = 0
        self.drag_moved = False

        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor=CARD_BG,
            background=ACCENT,
            thickness=14
        )

        # title on the left, version on the right
        top_bar = tk.Frame(root, bg=BAR_BG)
        top_bar.pack(side=tk.TOP, fill=tk.X)
        tk.Label(top_bar, text="NOT-A-FLUKE", font=("Helvetica", 11, "bold"), bg=BAR_BG, fg=ACCENT).pack(side=tk.LEFT, padx=6)
        tk.Label(top_bar, text="v" + VERSION, font=("Helvetica", 11, "bold"), bg=BAR_BG, fg="#888888").pack(side=tk.RIGHT, padx=6)
        # show the IP (cable first, then wifi) so you know where to SSH
        self.ip_lbl = tk.Label(top_bar, text="", font=("Helvetica", 10), bg=BAR_BG, fg="#BBBBBB")
        self.ip_lbl.pack(side=tk.RIGHT, padx=6)

        # up/down arrows on the right side
        arrow_strip = tk.Frame(root, bg=BG, width=54)
        arrow_strip.pack(side=tk.RIGHT, fill=tk.Y)
        arrow_strip.pack_propagate(False)
        self.make_arrow(arrow_strip, "▲", -1)
        self.make_arrow(arrow_strip, "▼", 1)

        self.container = tk.Frame(root, bg=BG)
        self.container.pack(expand=True, fill=tk.BOTH)

        # keyboard and mouse wheel scrolling for VM/desktop
        self.root.bind("<Up>", lambda event: self.scroll_by(-1))
        self.root.bind("<Down>", lambda event: self.scroll_by(1))
        self.root.bind("<Prior>", lambda event: self.scroll_by(-5))     # Page Up
        self.root.bind("<Next>", lambda event: self.scroll_by(5))       # Page Down
        self.root.bind("<MouseWheel>", self.scroll_with_mouse_wheel)
        self.root.bind("<Button-4>", lambda event: self.scroll_by(-1))
        self.root.bind("<Button-5>", lambda event: self.scroll_by(1))

        # drag a finger anywhere on a page to scroll it
        self.root.bind_all("<ButtonPress-1>", self.drag_start, add="+")
        self.root.bind_all("<B1-Motion>", self.drag_move, add="+")

        self.build_scanner_ui()
        self.build_results_ui()
        self.build_history_ui()
        self.build_detail_ui()
        self.build_wifi_ui()
        self.build_ap_ui()
        self.build_wifi_speed_ui()
        self.frames = [self.scanner_frame, self.results_frame, self.history_frame, self.detail_frame,
                       self.wifi_frame, self.ap_frame, self.wifi_speed_frame]
        self.scroll_canvases = {"results": self.scroll_canvas, "detail": self.detail_canvas,
                                "wifi": self.wifi_canvas, "ap": self.ap_canvas,
                                "wifi_speed": self.wifi_speed_canvas}
        self.show_scanner_ui()

        self.running = True
        self.sniffing = False
        # listen for CDP the whole time for PoE info (see switch.py)
        self.cdp_poe = ""
        if start_workers:
            start_cdp_listener(self.set_cdp_poe)
            self.worker_thread = threading.Thread(target=self.main_workflow, daemon=True)
            self.worker_thread.start()
            self.refresh_ip()

    # refresh the top bar IP every 5s in case the cable goes in later
    def refresh_ip(self):
        for interface in (INTERFACE, WIFI_INTERFACE):
            ip = get_ip(interface)
            if ip != "":
                self.ip_lbl.config(text=f"{interface} {ip}")
                break
        else:
            self.ip_lbl.config(text="no IP")
        self.root.after(5000, self.refresh_ip)

    # --- UI BUILDERS ---

    # big dark touch button, caller packs it
    def make_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command, font=("Helvetica", 14, "bold"),
            bg=BAR_BG, fg="#FFFFFF", activebackground="#444444", activeforeground="#FFFFFF",
            padx=12, pady=6
        )

    # cyan title strip at the top of a screen
    def make_header(self, parent, text):
        header = tk.Label(parent, text=text, font=("Helvetica", 16, "bold"), bg=ACCENT, fg="#000000", pady=3)
        header.pack(fill=tk.X)
        return header

    # big arrow button on the right strip, hold it to keep scrolling
    def make_arrow(self, parent, symbol, direction):
        arrow = tk.Label(
            parent, text=symbol, font=("DejaVu Sans", 22, "bold"),
            bg=BAR_BG, fg=ACCENT, relief=tk.RAISED, bd=2
        )
        arrow.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=2, pady=2)
        arrow.bind("<ButtonPress-1>", lambda event: self.arrow_pressed(arrow, direction))
        arrow.bind("<ButtonRelease-1>", lambda event: self.arrow_released(arrow))

    # History / Wi-Fi / Exit buttons along the bottom
    def make_bottom_bar(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(side=tk.BOTTOM, fill=tk.X, pady=4)
        self.make_button(bar, "History", self.open_history).pack(side=tk.LEFT, expand=True)
        self.make_button(bar, "Wi-Fi", self.open_wifi).pack(side=tk.LEFT, expand=True)
        exit_button = self.make_button(bar, "Exit", self.exit_pressed)
        exit_button.config(fg="#FF5252")
        exit_button.pack(side=tk.LEFT, expand=True)
        return exit_button

    # scrollable box, returns (canvas, inner frame) to put widgets in
    def make_scroll_area(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfig(window, width=event.width))
        return canvas, inner

    def build_scanner_ui(self):
        self.scanner_frame = tk.Frame(self.container, bg=BG)

        self.status_label = tk.Label(
            self.scanner_frame, text="WAITING FOR CABLE...", font=("Helvetica", 20, "bold"),
            bg=BAR_BG, fg="#FFA500", pady=8
        )
        self.status_label.pack(fill=tk.X)

        self.mode_label = tk.Label(
            self.scanner_frame, text="Ready", font=("Helvetica", 14, "italic"),
            bg=BG, fg="#888888"
        )
        self.mode_label.pack(pady=4)

        self.timer_label = tk.Label(
            self.scanner_frame, text="--:--", font=("Helvetica", 40, "bold"),
            bg=BG, fg="#FFFFFF"
        )
        self.timer_label.pack(pady=2)

        # loading screen for the speed test
        self.loading_frame = tk.Frame(self.scanner_frame, bg=BG)
        self.loading_text = tk.Label(
            self.loading_frame, text="", font=("Helvetica", 14),
            bg=BG, fg=ACCENT
        )
        self.loading_text.pack(pady=5)
        self.progress_bar = ttk.Progressbar(
            self.loading_frame, style="Custom.Horizontal.TProgressbar",
            mode="indeterminate", length=360
        )
        self.progress_bar.pack(pady=4)

        self.scanner_exit_button = self.make_bottom_bar(self.scanner_frame)

    def make_card(self, title):
        card = tk.Frame(self.scroll_content, bg=CARD_BG, bd=2, relief=tk.RIDGE)
        card.pack(fill=tk.X, padx=15, pady=8)
        tk.Label(card, text=title, font=("Helvetica", 14, "bold"), bg=CARD_BG, fg="#FFFFFF").pack(pady=4)
        return card

    def make_topo_line(self, card, text):
        label = tk.Label(card, text=text, font=("Courier", 13, "bold"), bg=CARD_BG, fg="#76FF03",
                         anchor="w", justify=tk.LEFT, wraplength=360)
        label.pack(fill=tk.X, padx=15, pady=1)
        return label

    def build_results_ui(self):
        self.results_frame = tk.Frame(self.container, bg=BG)
        self.make_header(self.results_frame, "DIAGNOSTIC COMPLETE")

        # pack the button bar before the canvas so it stays on screen while cards scroll
        self.results_exit_button = self.make_bottom_bar(self.results_frame)
        self.scroll_canvas, self.scroll_content = self.make_scroll_area(self.results_frame)

        # --- timestamp ---
        self.lbl_tested = tk.Label(
            self.scroll_content, text="Tested: --", font=("Helvetica", 12),
            bg=BG, fg="#888888"
        )
        self.lbl_tested.pack(pady=(8, 0))

        # --- switch card ---
        topo = self.make_card("Switch Topology")
        self.lbl_sw = self.make_topo_line(topo, "SW: --")
        # own line so a long switch name doesn't push it off screen
        self.lbl_via = self.make_topo_line(topo, "VIA: --")
        self.lbl_ip = self.make_topo_line(topo, "IP: --")
        self.lbl_port = self.make_topo_line(topo, "PORT: --")
        self.lbl_vlan = self.make_topo_line(topo, "VLAN: --")
        self.lbl_voice = self.make_topo_line(topo, "VOICE: --")
        # PoE info from the switch, the Pi can't measure voltage itself
        self.lbl_poe = self.make_topo_line(topo, "POE: --")

        # --- bandwidth card ---
        speed = self.make_card("Bandwidth")
        self.res_speed_lbl = tk.Label(speed, text="Down: -- Mbps | Up: -- Mbps | Ping: -- ms", font=("Helvetica", 12), bg=CARD_BG, fg="#FFD700")
        self.res_speed_lbl.pack(pady=(4, 0))
        self.res_rating_lbl = tk.Label(speed, text="Rating: --", font=("Helvetica", 12, "bold"), bg=CARD_BG, fg="#FFD700")
        self.res_rating_lbl.pack(pady=(0, 4))

        # --- DNS card ---
        dns = self.make_card("DNS Activity")
        self.res_dns_server_lbl = tk.Label(dns, text="Server: --", font=("Helvetica", 12), bg=CARD_BG, fg="#FF00FF")
        self.res_dns_server_lbl.pack(pady=(0, 2))
        self.res_dns_lbl = tk.Label(dns, text="Queries captured: 0 | Last: --", font=("Helvetica", 12), bg=CARD_BG, fg="#FF00FF")
        self.res_dns_lbl.pack(pady=(0, 4))

        # --- graph (graph.py) ---
        metrics = self.make_card("Performance Metrics")
        self.metrics_graph = MetricsGraph(metrics, INTERFACE)
        self.metrics_graph.canvas.pack(fill=tk.X, padx=8, pady=6)

        tk.Label(
            self.scroll_content, text="Arrows / drag to scroll  |  Unplug cable to reset",
            font=("Helvetica", 10), bg=BG, fg="#888888", pady=10
        ).pack(fill=tk.X)

    # --- EVENT HANDLERS ---

    def close_window(self, event):
        self.root.destroy()

    # tap Exit once for "Sure?", again within 3s to quit
    # (the notafluke launcher puts the terminal back after)
    def exit_pressed(self):
        if self.exit_armed:
            logger.info("exit button pressed")
            self.root.destroy()
            return
        self.exit_armed = True
        self.scanner_exit_button.config(text="Sure?", bg="#FF5252", fg="#FFFFFF")
        self.results_exit_button.config(text="Sure?", bg="#FF5252", fg="#FFFFFF")
        self.root.after(3000, self.disarm_exit)

    def disarm_exit(self):
        self.exit_armed = False
        self.scanner_exit_button.config(text="Exit", bg=BAR_BG, fg="#FF5252")
        self.results_exit_button.config(text="Exit", bg=BAR_BG, fg="#FF5252")

    # scroll up (negative) or down (positive). History flips pages instead
    def scroll_by(self, steps):
        if self.current_view == "history":
            if steps < 0:
                self.history_newer()
            else:
                self.history_older()
            return
        canvas = self.scroll_canvases.get(self.current_view)
        if canvas is not None:
            canvas.yview_scroll(steps, "units")

    def scroll_with_mouse_wheel(self, event):
        # delta > 0 means wheel up
        self.scroll_by(-1 if event.delta > 0 else 1)

    # scroll once, then keep going while held
    def arrow_pressed(self, arrow, direction):
        arrow.config(bg="#444444", relief=tk.SUNKEN)
        self.arrow_direction = direction
        if self.current_view == "history":
            self.scroll_by(direction)      # history is paged, so no repeat
            return
        self.scroll_by(direction * 2)
        self.arrow_job = self.root.after(400, self.arrow_repeat)

    def arrow_repeat(self):
        self.scroll_by(self.arrow_direction * 2)
        self.arrow_job = self.root.after(100, self.arrow_repeat)

    def arrow_released(self, arrow):
        arrow.config(bg=BAR_BG, relief=tk.RAISED)
        if getattr(self, "arrow_job", None) is not None:
            self.root.after_cancel(self.arrow_job)
            self.arrow_job = None

    def drag_start(self, event):
        self.drag_start_y = event.y_root
        self.drag_moved = False

    def drag_move(self, event):
        canvas = self.scroll_canvases.get(self.current_view)
        if canvas is None:
            return
        # small wobbles on the resistive screen still count as a tap
        if not self.drag_moved:
            if abs(event.y_root - self.drag_start_y) > 10:
                self.drag_moved = True
                canvas.scan_mark(0, event.y_root)
            return
        canvas.scan_dragto(0, event.y_root, gain=1)

    # --- UI STATE MANAGERS ---

    # swap whatever screen is showing for `frame`, view is the name used by scroll/Back
    def show_frame(self, frame, view):
        for other in self.frames:
            other.pack_forget()
        frame.pack(expand=True, fill=tk.BOTH)
        self.current_view = view

    # True while History or Wi-Fi is open. the test keeps running but won't
    # pull you off those pages, Back takes you to it
    def is_browsing(self):
        return self.current_view in ("history", "detail", "wifi", "ap", "wifi_speed")

    def show_scanner_ui(self):
        self.stop_loading_animation()
        if self.is_browsing():
            self.return_view = "scanner"
            return
        self.show_frame(self.scanner_frame, "scanner")

    def show_results_ui(self):
        self.stop_loading_animation()
        results = self.scan_results

        tested_text = f"Tested: {format_stamp(results['finished_at'])}"
        if results["clock_synced"] != "yes":
            tested_text += "  (clock not verified)"
        self.lbl_tested.config(text=tested_text)

        self.lbl_sw.config(text=f"SW:    {results['switch_name']}")
        self.lbl_via.config(text=f"VIA:   {results['switch_proto']}")
        self.lbl_ip.config(text=f"IP:    {results['switch_ip']}")
        self.lbl_port.config(text=f"PORT:  {results['switch_port']}")
        self.lbl_vlan.config(text=f"VLAN:  {results['switch_vlan']}")
        self.lbl_voice.config(text=f"VOICE: {results['switch_voice']}")
        self.lbl_poe.config(text=f"POE:   {results['switch_poe']}")

        self.res_speed_lbl.config(text=f"Down: {results['speed_down']} | Up: {results['speed_up']} | Ping: {results['speed_ping']}")
        self.res_rating_lbl.config(text=f"Rating: {results['speed_rating']}")
        self.res_dns_lbl.config(text=f"Queries: {results['dns_queries']} | Domain: {results['dns_last_domain']}")
        self.res_dns_server_lbl.config(text=f"Server: {results['dns_server']} | Answer: {results['dns_time']}")
        self.metrics_graph.show(results, self.speed_curves)

        if self.is_browsing():
            self.return_view = "results"
            return
        self.show_frame(self.results_frame, "results")
        self.scroll_canvas.yview_moveto(0)

    # go back to scanner or results, whichever the test is on. packs the frame
    # directly so a running speed test animation keeps going
    def go_back_to_main(self):
        if self.return_view == "results":
            self.show_frame(self.results_frame, "results")
        else:
            self.show_frame(self.scanner_frame, "scanner")

    def start_loading_animation(self, message):
        self.timer_label.pack_forget()
        self.loading_text.config(text=message)
        self.loading_frame.pack(pady=15)
        self.progress_bar.start(10)

    def set_loading_message(self, message):
        self.loading_text.config(text=message)

    def stop_loading_animation(self):
        self.progress_bar.stop()
        self.loading_frame.pack_forget()
        self.timer_label.pack(pady=10)

    def update_scanner(self, status, status_color, mode, timer_text):
        self.status_label.config(text=status, fg=status_color)
        self.mode_label.config(text=mode)
        self.timer_label.config(text=timer_text)

    # stamp the finish time and log the scan. True when results are ready,
    # False if the cable got pulled mid-scan
    def finish_scan(self, complete):
        self.scan_results["finished_at"] = now_stamp()
        self.scan_results["complete"] = complete
        save_scan_record(dict(self.scan_results))

        if complete:
            logger.info("scan complete: switch=%s port=%s down=%s",
                        self.scan_results["switch_name"], self.scan_results["switch_port"],
                        self.scan_results["speed_down"])
        else:
            logger.warning("scan cut short: cable removed before results")

    # --- THE SCAN ---

    def set_cdp_poe(self, text):
        self.cdp_poe = text

    def is_cable_connected(self):
        try:
            with open(CARRIER_PATH, "r") as f:
                return f.read().strip() == "1"
        except OSError:
            return False

    def countdown_timer(self, seconds):
        seconds_left = seconds
        while seconds_left >= 0:
            if not self.sniffing:
                break
            self.root.after(0, self.timer_label.config, {'text': f"00:{seconds_left:02d}"})
            time.sleep(1)
            seconds_left = seconds_left - 1

    # check lldpd once a second (switch.py). it's always running so usually the
    # first check already has data. returns False on timeout or cable pulled
    def discover_switch_topology(self):
        seconds_left = SWITCH_DISCOVERY_TIMEOUT
        while seconds_left >= 0:
            if not self.is_cable_connected():
                return False

            minutes = seconds_left // 60
            seconds = seconds_left % 60
            self.root.after(0, self.timer_label.config, {'text': f"{minutes:02d}:{seconds:02d}"})

            topology = parse_switch_topology(query_lldpd())
            if topology is not None:
                self.scan_results.update(topology)
                return True

            time.sleep(1)
            seconds_left = seconds_left - 1

        return False

    def main_workflow(self):
        # import scapy here on the worker thread since it's slow, so the screen shows up right away
        from .dns import run_dns_test

        while self.running:
            # 1. wait for a cable
            self.root.after(0, self.show_scanner_ui)
            if not self.is_cable_connected():
                self.root.after(0, self.update_scanner, "WAITING FOR CABLE...", "#FFA500", "Standby", "--:--")
                while not self.is_cable_connected() and self.running:
                    time.sleep(1)

            if not self.running:
                break

            self.scan_results = empty_scan_results(started=True)
            self.cdp_poe = ""    # forget the last cable's PoE
            self.speed_curves = {"down": [], "up": []}
            logger.info("cable connected, scan started (clock synced: %s)", self.scan_results["clock_synced"])

            # 2. find the switch (up to SWITCH_DISCOVERY_TIMEOUT)
            start_minutes = SWITCH_DISCOVERY_TIMEOUT // 60
            start_seconds = SWITCH_DISCOVERY_TIMEOUT % 60
            self.root.after(
                0, self.update_scanner, "TESTING NETWORK...", "#FFFF00",
                "Mode: Switch Discovery (CDP/LLDP)", f"{start_minutes:02d}:{start_seconds:02d}"
            )
            switch_found = self.discover_switch_topology()
            if not self.is_cable_connected():
                self.finish_scan(False)
                continue
            if not switch_found:
                logger.warning("no CDP/LLDP announcement heard within %d seconds", SWITCH_DISCOVERY_TIMEOUT)

            # PoE from LLDP (lldpd) or our own CDP listener
            if self.cdp_poe != "" and self.scan_results["switch_poe"] in ("Not advertised", "Unknown (no CDP/LLDP)"):
                self.scan_results["switch_poe"] = self.cdp_poe

            # 3. DNS lookup out the test port + 3s of sniffing
            self.root.after(0, self.update_scanner, "TESTING NETWORK...", "#FFFF00", "Mode: DNS Test", "00:03")
            self.sniffing = True
            timer_thread = threading.Thread(target=self.countdown_timer, args=(3,))
            timer_thread.start()

            try:
                run_dns_test(self.scan_results)
            except Exception:
                logger.exception("DNS test failed")
                self.scan_results["dns_time"] = "Failed"
            finally:
                self.sniffing = False
            timer_thread.join()
            if not self.is_cable_connected():
                self.finish_scan(False)
                continue

            # 3b. ~4s of pings for latency, jitter and loss
            self.root.after(0, self.update_scanner, "TESTING NETWORK...", "#FFFF00", "Mode: Latency / Jitter / Loss", "")
            run_ping_test(self.scan_results)
            if not self.is_cable_connected():
                self.finish_scan(False)
                continue

            # 4. speed test with the loading animation
            self.root.after(0, self.update_scanner, "TESTING NETWORK...", "#FFFF00", "Mode: Bandwidth Speed Test", "")
            self.root.after(0, self.start_loading_animation, "Connecting to closest server...")
            self.speed_curves = run_bandwidth_test(
                self.scan_results, INTERFACE,
                on_status=lambda text: self.root.after(0, self.set_loading_message, text)
            )

            # 5. show results
            if not self.is_cable_connected():
                self.finish_scan(False)
                continue

            self.finish_scan(True)
            self.root.after(0, self.show_results_ui)

            while self.is_cable_connected() and self.running:
                time.sleep(1)
            logger.info("cable removed")
