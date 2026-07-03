import math

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize

from gcode_generator import create_contour_gcode, build_spiral_points
from gcode_parser import parse_header, parse_toolpath


FIELDS = [
    ("x_start", "Part origin X", "0"),
    ("y_start", "Part origin Y", "0"),
    ("total_width", "Stock width (X)", "205"),
    ("total_height", "Stock height (Y)", "205"),
    ("desired_width", "Part width (X)", "200"),
    ("desired_height", "Part height (Y)", "200"),
    ("cut_step", "Stepover (cut_step)", "0.5"),
    ("tool_diameter", "Tool diameter", "10.0"),
    ("cut_depth", "Total depth", "6.0"),
    ("pass_depth", "Depth per pass (blank = single pass)", "2.0"),
    ("feed", "Feed rate (per min)", "100"),
    ("plunge_feed", "Plunge feed (Z, per min)", "100"),
    ("rpm", "Spindle RPM", "1000"),
    ("safe_z", "Safe Z height", "5.0"),
    ("spindle_dwell", "Spindle dwell (s, 0 = none)", "0"),
]

# Number of waypoints the simulation path is resampled to (animation smoothness)
SIM_RESOLUTION = 1500


class GcodeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("CNC Contour G-code Generator")
        self.geometry("1150x720")
        self.minsize(950, 520)

        self.vars = {}
        self.units_var = tk.StringVar(value="mm")
        self.last_gcode = None

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        self.gen_tab = ttk.Frame(notebook)
        self.sim_tab = ttk.Frame(notebook)
        notebook.add(self.gen_tab, text="Generator")
        notebook.add(self.sim_tab, text="Simulator")
        self.notebook = notebook

        self._build_generator_tab()
        self._build_simulator_tab()

        # Simulation playback state
        self.sim_waypoints = []
        self.sim_index = 0
        self.sim_playing = False
        self._sim_job = None

        self.preview()

    # ------------------------------------------------------------------
    # Generator tab
    # ------------------------------------------------------------------
    def _build_generator_tab(self):
        # Button bar pinned to the top of the tab, always visible
        btn_frame = ttk.Frame(self.gen_tab, padding=10)
        btn_frame.grid(row=0, column=0, columnspan=2, sticky="ew")

        ttk.Button(btn_frame, text="Preview", command=self.preview).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Generate", command=self.generate).pack(side="left", padx=5)
        self.save_btn = ttk.Button(btn_frame, text="Save...", command=self.save_gcode, state="disabled")
        self.save_btn.pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Open...", command=self.open_file).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Reset", command=self.reset_form).pack(side="left", padx=5)

        self.error_var = tk.StringVar()
        ttk.Label(btn_frame, textvariable=self.error_var, foreground="red", wraplength=400).pack(
            side="left", padx=15
        )

        # Form
        form = ttk.Frame(self.gen_tab, padding=10)
        form.grid(row=1, column=0, sticky="ns")

        ttk.Label(form, text="Units").grid(row=0, column=0, sticky="w", pady=2)
        units_combo = ttk.Combobox(
            form, textvariable=self.units_var, values=["mm", "inch"],
            state="readonly", width=15
        )
        units_combo.grid(row=0, column=1, pady=2, padx=5)

        for i, (key, label, default) in enumerate(FIELDS, start=1):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=default)
            entry = ttk.Entry(form, textvariable=var, width=18)
            entry.grid(row=i, column=1, pady=2, padx=5)
            self.vars[key] = var

        # Plot
        plot_frame = ttk.Frame(self.gen_tab, padding=10)
        plot_frame.grid(row=1, column=1, sticky="nsew")
        self.gen_tab.columnconfigure(1, weight=1)
        self.gen_tab.rowconfigure(1, weight=1)

        self.figure = Figure(figsize=(5, 5))
        self.ax = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Output
        out_frame = ttk.Frame(self.gen_tab, padding=10)
        out_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")

        ttk.Label(out_frame, text="G-code preview (first lines):").pack(anchor="w")
        self.output_text = tk.Text(out_frame, height=8)
        self.output_text.pack(fill="both", expand=True)

    def _read_params(self):
        """Read and validate form fields, returning a kwargs dict."""
        v = self.vars

        field_labels = {key: label for key, label, _ in FIELDS}
        params = {}
        for key in v:
            raw = v[key].get().strip()
            if key == "pass_depth" and not raw:
                params[key] = None
                continue
            label = field_labels.get(key, key)
            try:
                val = float(raw)
            except ValueError:
                raise ValueError(f'"{label}" must be a number (got "{raw}")')
            if math.isinf(val) or math.isnan(val):
                raise ValueError(f'"{label}" must be a finite number')
            params[key] = val

        params["units"] = self.units_var.get()
        return params

    def preview(self):
        try:
            params = self._read_params()
        except ValueError as e:
            self.error_var.set(str(e))
            return

        try:
            points = build_spiral_points(
                x_start=params["x_start"], y_start=params["y_start"],
                total_width=params["total_width"], total_height=params["total_height"],
                desired_width=params["desired_width"], desired_height=params["desired_height"],
                cut_step=params["cut_step"], tool_diameter=params["tool_diameter"],
            )
        except ValueError as e:
            self.error_var.set(str(e))
            return

        self.error_var.set("")
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        self.ax.clear()
        self.ax.plot(xs, ys, marker="o", markersize=2, linewidth=1)
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_title("Toolpath preview (single pass)")
        self.ax.set_xlabel("X")
        self.ax.set_ylabel("Y")
        self.canvas.draw()

    def generate(self):
        try:
            params = self._read_params()
        except ValueError as e:
            self.error_var.set(str(e))
            return

        try:
            gcode = create_contour_gcode(**params)
        except ValueError as e:
            self.error_var.set(str(e))
            return

        self.error_var.set("")
        self.preview()

        self.output_text.delete("1.0", tk.END)
        preview_lines = "\n".join(gcode.splitlines()[:20])
        self.output_text.insert(tk.END, preview_lines + "\n...")

        self.last_gcode = gcode
        self.save_btn.config(state="normal")

        # Load the generated program straight into the simulator
        self.load_simulation(gcode)
        self.notebook.select(self.sim_tab)

    def save_gcode(self):
        if not self.last_gcode:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".ngc",
            filetypes=[("LinuxCNC G-code", "*.ngc"), ("G-code", "*.nc *.gcode *.txt"), ("All files", "*.*")],
            initialfile="contour.ngc",
        )
        if not path:
            return

        with open(path, "w") as f:
            f.write(self.last_gcode)
        messagebox.showinfo("Saved", f"G-code written to:\n{path}")

    def reset_form(self):
        for key, _label, default in FIELDS:
            self.vars[key].set(default)
        self.units_var.set("mm")
        self.error_var.set("")
        self.last_gcode = None
        self.save_btn.config(state="disabled")
        self.output_text.delete("1.0", tk.END)
        self.preview()

    def open_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("G-code", "*.ngc *.nc *.gcode *.txt"), ("All files", "*.*")]
        )
        if not path:
            return

        with open(path, "r") as f:
            text = f.read()

        header = parse_header(text)
        if "x_start" in header:
            self._apply_header(header)
            self.preview()
        else:
            messagebox.showinfo(
                "Loaded",
                "This file wasn't generated by this app, so the form "
                "fields weren't changed. Showing it in the Simulator."
            )

        try:
            self.load_simulation(text)
            self.notebook.select(self.sim_tab)
        except Exception as e:
            messagebox.showerror("Could not parse file", str(e))

    def _apply_header(self, header):
        for key in self.vars:
            if key in header:
                value = header[key]
                self.vars[key].set("" if value == "-" else value)
        if "units" in header:
            self.units_var.set(header["units"])

    # ------------------------------------------------------------------
    # Simulator tab
    # ------------------------------------------------------------------
    def _build_simulator_tab(self):
        controls = ttk.Frame(self.sim_tab, padding=10)
        controls.pack(side="top", fill="x")

        ttk.Button(controls, text="Load file...", command=self.open_file).pack(side="left", padx=5)
        self.play_btn = ttk.Button(controls, text="Play", command=self.toggle_play, state="disabled")
        self.play_btn.pack(side="left", padx=5)
        self.reset_btn = ttk.Button(controls, text="Reset", command=self.reset_sim, state="disabled")
        self.reset_btn.pack(side="left", padx=5)

        ttk.Label(controls, text="Speed").pack(side="left", padx=(20, 5))
        self.speed_var = tk.IntVar(value=10)
        ttk.Scale(controls, from_=1, to=50, variable=self.speed_var, orient="horizontal", length=150).pack(side="left")

        self.sim_progress = tk.DoubleVar(value=0)
        self.sim_scale = ttk.Scale(
            controls, from_=0, to=1, variable=self.sim_progress, orient="horizontal",
            length=300, command=self._on_scrub, state="disabled"
        )
        self.sim_scale.pack(side="left", padx=(20, 5), fill="x", expand=True)

        self.sim_status_var = tk.StringVar(value="Load a G-code file to simulate it.")
        ttk.Label(self.sim_tab, textvariable=self.sim_status_var, padding=(10, 0)).pack(side="top", anchor="w")

        plot_frame = ttk.Frame(self.sim_tab, padding=10)
        plot_frame.pack(side="top", fill="both", expand=True)

        self.sim_figure = Figure(figsize=(6, 6))
        self.sim_ax = self.sim_figure.add_subplot(111)
        self.sim_canvas = FigureCanvasTkAgg(self.sim_figure, master=plot_frame)
        self.sim_canvas.get_tk_widget().pack(fill="both", expand=True)

    def load_simulation(self, gcode_text):
        segments, units = parse_toolpath(gcode_text)
        if not segments:
            raise ValueError("No motion (G0/G1/G2/G3) found in this file.")

        self._stop_playback()
        self.sim_segments = segments
        self.sim_waypoints = _resample(segments, SIM_RESOLUTION)
        self.sim_index = 0

        self._draw_static_path(segments, units)
        self.sim_marker, = self.sim_ax.plot([], [], "ro", markersize=8, zorder=5)
        self.sim_canvas.draw()

        self.play_btn.config(state="normal", text="Play")
        self.reset_btn.config(state="normal")
        self.sim_scale.config(state="normal")
        self.sim_progress.set(0)
        self._update_marker()

    def _draw_static_path(self, segments, units):
        self.sim_ax.clear()

        cuts = [s for s in segments if not s["rapid"]]
        rapids = [s for s in segments if s["rapid"]]

        if cuts:
            cut_lines = [[(s["start"][0], s["start"][1]), (s["end"][0], s["end"][1])] for s in cuts]
            zs = [s["end"][2] for s in cuts]
            norm = Normalize(vmin=min(zs), vmax=max(zs)) if min(zs) != max(zs) else None
            cmap = matplotlib.colormaps["viridis"]
            colors = [cmap(norm(z)) if norm else cmap(0.5) for z in zs]
            self.sim_ax.add_collection(LineCollection(cut_lines, colors=colors, linewidths=1.2, label="Cutting"))

        if rapids:
            rapid_lines = [[(s["start"][0], s["start"][1]), (s["end"][0], s["end"][1])] for s in rapids]
            self.sim_ax.add_collection(LineCollection(rapid_lines, colors="red", linestyles="dashed",
                                                        linewidths=1, label="Rapid"))

        all_x = [p[0] for s in segments for p in (s["start"], s["end"])]
        all_y = [p[1] for s in segments for p in (s["start"], s["end"])]
        pad_x = (max(all_x) - min(all_x)) * 0.05 or 1
        pad_y = (max(all_y) - min(all_y)) * 0.05 or 1
        self.sim_ax.set_xlim(min(all_x) - pad_x, max(all_x) + pad_x)
        self.sim_ax.set_ylim(min(all_y) - pad_y, max(all_y) + pad_y)

        self.sim_ax.set_aspect("equal", adjustable="box")
        unit_label = units or "?"
        self.sim_ax.set_title(f"Toolpath simulation ({unit_label}) — color = depth (Z)")
        self.sim_ax.set_xlabel("X")
        self.sim_ax.set_ylabel("Y")
        self.sim_ax.legend(loc="upper right", fontsize="small")

    # -- Playback --
    def toggle_play(self):
        if self.sim_playing:
            self._stop_playback()
        else:
            if self.sim_index >= len(self.sim_waypoints) - 1:
                self.sim_index = 0
            self.sim_playing = True
            self.play_btn.config(text="Pause")
            self._tick()

    def reset_sim(self):
        self._stop_playback()
        self.sim_index = 0
        self.sim_progress.set(0)
        self._update_marker()

    def _stop_playback(self):
        self.sim_playing = False
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="Play")
        if self._sim_job is not None:
            self.after_cancel(self._sim_job)
            self._sim_job = None

    def _on_scrub(self, _value):
        if not self.sim_waypoints:
            return
        self._stop_playback()
        last = len(self.sim_waypoints) - 1
        self.sim_index = max(0, min(int(self.sim_progress.get() * last), last))
        self._update_marker(update_scale=False)

    def _tick(self):
        if not self.sim_playing:
            return
        step = max(1, self.speed_var.get())
        self.sim_index = min(self.sim_index + step, len(self.sim_waypoints) - 1)
        self._update_marker()
        if self.sim_index >= len(self.sim_waypoints) - 1:
            self._stop_playback()
            return
        self._sim_job = self.after(30, self._tick)

    def _update_marker(self, update_scale=True):
        if not self.sim_waypoints:
            return
        x, y, z, rapid = self.sim_waypoints[self.sim_index]
        self.sim_marker.set_data([x], [y])
        self.sim_canvas.draw_idle()

        if update_scale:
            total = len(self.sim_waypoints) - 1
            self.sim_progress.set(self.sim_index
             / total if total > 0 else 0)

        move = "Rapid" if rapid else "Cutting"
        self.sim_status_var.set(
            f"{move} — X={x:.2f} Y={y:.2f} Z={z:.2f}  "
            f"({self.sim_index + 1}/{len(self.sim_waypoints)})"
        )


def _resample(segments, n):
    """Resample motion segments into n evenly-spaced (x, y, z, rapid) waypoints."""
    lengths = []
    for s in segments:
        dx = s["end"][0] - s["start"][0]
        dy = s["end"][1] - s["start"][1]
        dz = s["end"][2] - s["start"][2]
        lengths.append((dx * dx + dy * dy + dz * dz) ** 0.5)

    total = sum(lengths) or 1.0
    waypoints = [(*segments[0]["start"], segments[0]["rapid"])]

    for s, length in zip(segments, lengths):
        steps = max(1, round(n * length / total))
        sx, sy, sz = s["start"]
        ex, ey, ez = s["end"]
        for k in range(1, steps + 1):
            t = k / steps
            waypoints.append((sx + (ex - sx) * t, sy + (ey - sy) * t, sz + (ez - sz) * t, s["rapid"]))

    return waypoints


if __name__ == "__main__":
    app = GcodeApp()
    app.mainloop()
