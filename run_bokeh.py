"""
Viventis Ablation Dashboard — Bokeh server application
Run with:  bokeh serve --show ablation_dashboard.py
"""

import sys, os, time, math, threading
from datetime import datetime, timezone

import numpy as np

# ──────────────────────────────────────────────────────────────────────────────
# pymcs setup  (unchanged from original)
# ──────────────────────────────────────────────────────────────────────────────
base_outdir = ''

if os.path.isdir(r"C:\Viventis\PyMCS"):
    base_outdir = r'D:\Data\Temp'
    sys.path.insert(0, r"C:\Viventis\PyMCS\v2.0.0.2")
elif os.path.isdir('/Users/helsens/Software/Viventis'):
    sys.path.insert(0, "/Users/helsens/Software/Viventis/PyMCS/v2.0.0.2")

import pymcs

microscope = pymcs.Microscope()
try:
    microscope.connect()
except Exception:
    print("Could not connect to microscope — running in disconnected mode.")

time_lapse_controller  = pymcs.TimeLapseController(microscope)
acquisition_controller = pymcs.AcquisitionController(microscope, "ACQ")
camera                 = pymcs.Camera(microscope, "CAM")
stage_xyz              = pymcs.StageXYZ(microscope, "STAGE")

# ──────────────────────────────────────────────────────────────────────────────
# Bokeh imports
# ──────────────────────────────────────────────────────────────────────────────
from bokeh.plotting import figure, curdoc
from bokeh.models import (
    TextInput, Select, Button, Div, ColumnDataSource, Spacer, Slope
)
from bokeh.layouts import column, row

# ──────────────────────────────────────────────────────────────────────────────
# Application state  (mirrors original globals, collected in one dict)
# ──────────────────────────────────────────────────────────────────────────────
params = dict(
    pulse_count       = 10,
    point_count       = 10,
    position_name     = "Ablation",
    point_distance    = 0.7,
    cut_direction     = "X",
    laser_diameter    = 1.0,
    cut_type          = "Line",
    circle_diam       = 10.0,
    circle_sigma      = 1.0,
    line_angle        = 0.0,
    camera_view       = 1,
    camera_channel    = 1,
    camera_plane      = 1,
    camera_pixel_left  = -1,
    camera_pixel_top   = -1,
    camera_pixel_width = -1,
    camera_pixel_height= -1,
    laser_x           = 0,
    laser_y           = 0,
    experiment_name   = "test",
)

loop_running = False
time_lapse, metadata, time_stamps = [], [], []

# Animation state (mutable containers so closures can mutate them)
_anim_positions   = []
_anim_frame       = [0]
_anim_callback_id = [None]

# ──────────────────────────────────────────────────────────────────────────────
# Pattern functions  (identical to original)
# ──────────────────────────────────────────────────────────────────────────────
def ablation_pattern(radius, sigma, center=(0, 0), step_fraction=0.7):
    cx, cy    = center
    step_size = step_fraction * sigma
    positions = []
    x_min, x_max = cx - radius, cx + radius
    y_min, y_max = cy - radius, cy + radius
    x = np.arange(x_min, x_max + step_size, step_size)
    for i, xi in enumerate(x):
        offset = (step_size / 2) if i % 2 == 1 else 0
        y = np.arange(y_min + offset, y_max + step_size, step_size)
        if i % 2 != 0:
            y = y[::-1]
        for yi in y:
            if np.sqrt((xi - cx) ** 2 + (yi - cy) ** 2) <= radius:
                positions.append((xi, yi))
    return positions


def ablation_pattern_line(point_count, point_distance, line_angle):
    start_offset = -1 * (point_count - 1) * point_distance / 2.0
    positions = []
    for i in range(point_count):
        x = (start_offset + i * point_distance) * math.cos(line_angle * math.pi / 180.0)
        y = (start_offset + i * point_distance) * math.sin(line_angle * math.pi / 180.0)
        positions.append((x, y))
    return positions


def circle_positions(point_count, radius):
    return [
        (radius * math.cos(i * 2 * math.pi / point_count),
         radius * math.sin(i * 2 * math.pi / point_count))
        for i in range(point_count)
    ]

# ──────────────────────────────────────────────────────────────────────────────
# Bokeh data sources
# ──────────────────────────────────────────────────────────────────────────────
image_source   = ColumnDataSource(data=dict(image=[], x=[0], y=[0], dw=[2048], dh=[2048]))
scatter_source = ColumnDataSource(data=dict(x=[], y=[]))
circle_source  = ColumnDataSource(data=dict(x=[], y=[], radius=[]))

# ──────────────────────────────────────────────────────────────────────────────
# Main plot
# ──────────────────────────────────────────────────────────────────────────────
plot = figure(
    width=680, height=680,
    title="Ablation Preview",
    tools="pan,wheel_zoom,box_zoom,reset,save",
    x_range=(0, 2048), y_range=(0, 2048),
    background_fill_color="#1a1a2e",
    border_fill_color="#12122a",
    outline_line_color="#00d4ff",
    outline_line_width=1,
)
plot.title.text_color  = "#00d4ff"
plot.title.text_font_size = "14px"
plot.xaxis.axis_label_text_color = "#aaaacc"
plot.yaxis.axis_label_text_color = "#aaaacc"
plot.xgrid.grid_line_color = "#2a2a4a"
plot.ygrid.grid_line_color = "#2a2a4a"

plot.image(
    image='image', x='x', y='y', dw='dw', dh='dh',
    source=image_source, palette='Greys256',
)
plot.scatter(
    'x', 'y', source=scatter_source,
    color="#ff4444", size=8, alpha=0.85,
    legend_label="Laser Positions",
)
plot.circle(
    'x', 'y', radius='radius', source=circle_source,
    fill_color=None, line_color="#00d4ff",
    line_dash="dashed", line_width=1.5,
    legend_label="Disk Boundary",
)
plot.legend.background_fill_color = "#1a1a2e"
plot.legend.label_text_color = "#ccccff"

# ──────────────────────────────────────────────────────────────────────────────
# Status banner
# ──────────────────────────────────────────────────────────────────────────────
status_div = Div(
    text='<span style="color:#00d4ff;font-weight:600;">● Ready</span>',
    width=660,
    styles={"font-family": "monospace", "font-size": "13px",
            "padding": "6px 10px", "background": "#12122a",
            "border": "1px solid #2a2a4a", "border-radius": "4px"},
)

def set_status(msg, color="#00d4ff"):
    status_div.text = f'<span style="color:{color};font-weight:600;">● {msg}</span>'

# ──────────────────────────────────────────────────────────────────────────────
# Helpers: validated reads
# ──────────────────────────────────────────────────────────────────────────────
def _int(widget, name, allow_neg=False):
    try:
        v = int(widget.value.strip())
        if not allow_neg and v < 0:
            raise ValueError
        return v
    except ValueError:
        set_status(f"Invalid integer for {name}", "#ff6644")
        return None


def _float(widget, name):
    try:
        return float(widget.value.strip())
    except ValueError:
        set_status(f"Invalid number for {name}", "#ff6644")
        return None

# ──────────────────────────────────────────────────────────────────────────────
# Widget factory  (keeps layout code dry)
# ──────────────────────────────────────────────────────────────────────────────
_STYLE = {"font-family": "monospace", "font-size": "12px"}

def _input(title, default, w=110):
    return TextInput(title=title, value=str(default), width=w,
                     styles=_STYLE)

def _sep(label=""):
    html = (f'<div style="border-top:1px solid #2a3a5a;margin:6px 0;'
            f'color:#4466aa;font-size:11px;font-family:monospace;'
            f'padding-top:4px;">{label}</div>')
    return Div(text=html, width=320)

# ──────────────────────────────────────────────────────────────────────────────
# Widgets
# ──────────────────────────────────────────────────────────────────────────────
w_pulse_count    = _input("Pulse Count",    10)
w_point_count    = _input("Point Count",    10)
w_point_distance = _input("Point Distance", 1)
w_line_angle     = _input("Line Angle",     0)

w_cut_type = Select(
    title="Cut Type", value="Line",
    options=["Line", "Circle", "Disk"], width=150, styles=_STYLE,
)

w_circle_diam  = _input("Diam (µm)",  10, 95)
w_circle_sigma = _input("Sigma",      1,  95)

w_cut_dir = Select(
    title="Cut Direction", value="X",
    options=["X", "Y"], width=110, styles=_STYLE,
)
w_position_name = _input("Position Name", "Ablation", 150)
w_laser_diam    = _input("Laser Diam (µm)", 1)

w_camera_view    = _input("View",    1, 75)
w_camera_channel = _input("Channel", 1, 75)
w_camera_plane   = _input("Plane",   1, 75)

w_pixel_left   = _input("Left",   -1, 75)
w_pixel_top    = _input("Top",    -1, 75)
w_pixel_width  = _input("Width",  -1, 75)
w_pixel_height = _input("Height", -1, 75)

w_exp_name = _input("Experiment Name", "test", 200)

# Buttons
_BTN_W = 155
btn_set_params = Button(label="⚙  Set Parameters",    button_type="primary", width=_BTN_W)
btn_start_acq  = Button(label="▶  Start Acquisition",  button_type="success", width=_BTN_W)
btn_stop_acq   = Button(label="■  Stop Acquisition",   button_type="danger",  width=_BTN_W)
btn_preview    = Button(label="👁  Preview",            button_type="default", width=_BTN_W)
btn_ablate     = Button(label="⚡  Ablate",             button_type="warning", width=_BTN_W)

# ──────────────────────────────────────────────────────────────────────────────
# Callbacks
# ──────────────────────────────────────────────────────────────────────────────
def on_set_parameters(n_clicks=None):
    for attr, widget, name, kw in [
        ("camera_view",          w_camera_view,    "View",           {}),
        ("camera_channel",       w_camera_channel, "Channel",        {}),
        ("camera_plane",         w_camera_plane,   "Plane",          {}),
        ("camera_pixel_left",    w_pixel_left,     "Pixel Left",     {"allow_neg": True}),
        ("camera_pixel_top",     w_pixel_top,      "Pixel Top",      {"allow_neg": True}),
        ("camera_pixel_width",   w_pixel_width,    "Pixel Width",    {"allow_neg": True}),
        ("camera_pixel_height",  w_pixel_height,   "Pixel Height",   {"allow_neg": True}),
        ("pulse_count",          w_pulse_count,    "Pulse Count",    {}),
        ("point_count",          w_point_count,    "Point Count",    {}),
    ]:
        v = _int(widget, name, **kw)
        if v is not None:
            params[attr] = v

    for attr, widget, name in [
        ("point_distance", w_point_distance, "Point Distance"),
        ("line_angle",     w_line_angle,     "Line Angle"),
        ("circle_diam",    w_circle_diam,    "Circle Diam"),
        ("circle_sigma",   w_circle_sigma,   "Circle Sigma"),
        ("laser_diameter", w_laser_diam,     "Laser Diameter"),
    ]:
        v = _float(widget, name)
        if v is not None:
            params[attr] = v

    params["position_name"]  = w_position_name.value
    params["cut_direction"]  = w_cut_dir.value
    params["cut_type"]       = w_cut_type.value
    params["experiment_name"] = w_exp_name.value

    set_status("Parameters saved.", "#44ff88")
    print("Parameters:", params)


def on_start_acquisition(n_clicks=None):
    time_lapse_controller.start()
    set_status("Acquisition running…", "#44ff88")


def on_stop_acquisition(n_clicks=None):
    time_lapse_controller.stop()
    set_status("Acquisition stopped.", "#ffaa44")


def _snap_image():
    """Snap one frame and return as normalised float32 array."""
    time_lapse_controller.snap()
    pl, pt = params["camera_pixel_left"], params["camera_pixel_top"]
    pw, ph = params["camera_pixel_width"], params["camera_pixel_height"]
    if pl > 0 and pt > 0 and pw > 0 and ph > 0:
        img = camera.image_get(
            params["camera_view"], params["camera_channel"], params["camera_plane"],
            pl, pt, pw, ph,
        )
    else:
        img = camera.image_get(
            params["camera_view"], params["camera_channel"], params["camera_plane"],
        )
    img = np.flip(img, 0).astype(float)
    lo, hi = img.min(), img.max()
    return (img - lo) / (hi - lo) if hi > lo else img


def _get_positions():
    cut = params["cut_type"]
    if cut == "Line":
        return ablation_pattern_line(
            params["point_count"], params["point_distance"], params["line_angle"]
        )
    if cut == "Disk":
        return ablation_pattern(params["circle_diam"] / 2.0, params["circle_sigma"])
    if cut == "Circle":
        return circle_positions(params["point_count"], params["circle_diam"] / 2.0)
    return []


def on_preview(n_clicks=None):
    on_set_parameters()

    # ── snap & display image ──────────────────────────────────────────────────
    img = _snap_image()
    h, w = img.shape
    plot.x_range.start, plot.x_range.end = 0, w
    plot.y_range.start, plot.y_range.end = 0, h
    image_source.data = dict(image=[img], x=[0], y=[0], dw=[w], dh=[h])

    # ── compute laser positions in pixel space ────────────────────────────────
    positions = _get_positions()
    if not positions:
        set_status("No positions for current cut type.", "#ffaa44")
        return

    SCALE = 1.0 / 0.347          # µm → pixels
    lx, ly = params["laser_x"], params["laser_y"]
    px = [p[0] * SCALE + lx + w / 2 for p in positions]
    py = [p[1] * SCALE + ly + h / 2 for p in positions]

    # ── disk boundary circle ──────────────────────────────────────────────────
    if params["cut_type"] == "Disk":
        r_px = (params["circle_diam"] / 2.0) * SCALE
        circle_source.data = dict(x=[lx + w / 2], y=[ly + h / 2], radius=[r_px])
    else:
        circle_source.data = dict(x=[], y=[], radius=[])

    # ── animate scatter via periodic callback ─────────────────────────────────
    _anim_positions.clear()
    _anim_positions.extend(zip(px, py))
    _anim_frame[0] = 0
    scatter_source.data = dict(x=[], y=[])

    if _anim_callback_id[0] is not None:
        try:
            curdoc().remove_periodic_callback(_anim_callback_id[0])
        except Exception:
            pass

    def _step():
        f = _anim_frame[0]
        if f < len(_anim_positions):
            scatter_source.data = dict(
                x=[p[0] for p in _anim_positions[:f + 1]],
                y=[p[1] for p in _anim_positions[:f + 1]],
            )
            _anim_frame[0] += 1
        else:
            try:
                curdoc().remove_periodic_callback(_anim_callback_id[0])
            except Exception:
                pass
            _anim_callback_id[0] = None
            set_status(f"Preview complete — {len(_anim_positions)} positions.", "#44ff88")

    _anim_callback_id[0] = curdoc().add_periodic_callback(_step, 40)
    set_status("Preview loading…", "#00d4ff")


def _ablate_thread():
    """Run in a background thread; touch Bokeh state only via add_next_tick_callback."""
    position_name = params["position_name"]
    pulse_count   = params["pulse_count"]
    positions     = _get_positions()

    total = len(positions)
    for i, (x, y) in enumerate(positions):
        stage_xyz.move(position_name, None, None, (x, y, 0))
        acquisition_controller.laser_ablate_uv(pulse_count)
        msg = f"Ablating… {i + 1}/{total}"
        curdoc().add_next_tick_callback(lambda m=msg: set_status(m, "#ffaa44"))

    stage_xyz.move(position_name)
    curdoc().add_next_tick_callback(lambda: set_status("Ablation complete.", "#44ff88"))
    curdoc().add_next_tick_callback(lambda: time_lapse_controller.start())


def on_ablate(n_clicks=None):
    time_lapse_controller.stop()
    time.sleep(0.5)
    on_set_parameters()
    set_status("Starting ablation…", "#ffaa44")
    threading.Thread(target=_ablate_thread, daemon=True).start()


btn_set_params.on_click(on_set_parameters)
btn_start_acq.on_click(on_start_acquisition)
btn_stop_acq.on_click(on_stop_acquisition)
btn_preview.on_click(on_preview)
btn_ablate.on_click(on_ablate)

# ──────────────────────────────────────────────────────────────────────────────
# Layout
# ──────────────────────────────────────────────────────────────────────────────
header = Div(
    text=(
        '<div style="font-family:monospace;font-size:18px;font-weight:700;'
        'color:#00d4ff;letter-spacing:2px;padding:8px 0 4px 0;">'
        'VIVENTIS ABLATION DASHBOARD</div>'
    ),
    width=700,
)

controls = column(
    _sep("ACQUISITION PARAMETERS"),
    row(w_pulse_count, w_point_count, w_point_distance),
    row(w_line_angle),

    _sep("CUT TYPE"),
    w_cut_type,

    _sep("CIRCLE / DISK"),
    row(w_circle_diam, w_circle_sigma),

    _sep("CUT GEOMETRY"),
    row(w_cut_dir, w_position_name),
    row(w_laser_diam),

    _sep("CAMERA"),
    row(w_camera_view, w_camera_channel, w_camera_plane),
    row(w_pixel_left, w_pixel_top, w_pixel_width, w_pixel_height),

    _sep("EXPERIMENT"),
    w_exp_name,

    Spacer(height=10),
    _sep("ACTIONS"),
    btn_set_params,
    row(btn_start_acq, btn_stop_acq),
    btn_preview,
    btn_ablate,

    sizing_mode="fixed", width=340,
    styles={"background": "#12122a", "padding": "12px",
            "border": "1px solid #2a2a4a", "border-radius": "6px"},
)

main_layout = column(
    header,
    row(controls, Spacer(width=16), plot),
    status_div,
    styles={"background": "#0d0d1f", "padding": "16px"},
)

curdoc().add_root(main_layout)
curdoc().title = "Viventis Ablation Dashboard"