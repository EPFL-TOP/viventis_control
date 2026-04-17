"""
Viventis Ablation Dashboard v2 — Region-based laser ablation
─────────────────────────────────────────────────────────────
Run with:  bokeh serve --show ablation_dashboard.py

Workflow
────────
1. Snap Image          → live camera frame displayed on plot
2. Click image         → sets region centre (crosshair marker)
3. Choose shape        → Circle or Rectangle (with rotation)
4. Set parameters      → radius / width-height-angle, point spacing
5. Generate Points     → fills region, previews all ablation spots
6. Ablate              → stops acquisition, drives stage, fires UV laser,
                         returns to named position, restarts acquisition
"""

import sys, os, time, math, threading
import numpy as np

# ── pymcs (completely unchanged) ──────────────────────────────────────────
base_outdir = ""

if os.path.isdir(r"C:\Viventis\PyMCS"):
    base_outdir = r"D:\Data\Temp"
    sys.path.insert(0, r"C:\Viventis\PyMCS\v2.0.0.2")
elif os.path.isdir("/Users/helsens/Software/Viventis"):
    sys.path.insert(0, "/Users/helsens/Software/Viventis/PyMCS/v2.0.0.2")

import pymcs

microscope = pymcs.Microscope()
try:
    microscope.connect()
except Exception:
    print("Could not connect to microscope — running disconnected.")

time_lapse_controller  = pymcs.TimeLapseController(microscope)
acquisition_controller = pymcs.AcquisitionController(microscope, "ACQ")
camera                 = pymcs.Camera(microscope, "CAM")
stage_xyz              = pymcs.StageXYZ(microscope, "STAGE")

# ── Bokeh ─────────────────────────────────────────────────────────────────
from bokeh.plotting import figure, curdoc
from bokeh.models import (
    TextInput, Button, Div, ColumnDataSource, Spacer,
    RadioButtonGroup, HoverTool,
)
from bokeh.layouts import column, row
from bokeh.events import Tap

# ── Constants ─────────────────────────────────────────────────────────────
UM_PER_PX = 0.347          # µm per pixel (calibration)
HEX_FRAC  = 0.7            # hexagonal packing fraction (same as original)

# ── Mutable state ─────────────────────────────────────────────────────────
img_hw = [2048, 2048]      # [height, width] — updated on every snap

# ── Data sources ──────────────────────────────────────────────────────────
_blank = np.zeros((2048, 2048), dtype=np.float64)
image_source  = ColumnDataSource(
    data=dict(image=[_blank], x=[0], y=[0], dw=[2048], dh=[2048])
)
region_source = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))  # outline polygon
points_source = ColumnDataSource(data=dict(x=[], y=[]))        # ablation dots
center_source = ColumnDataSource(data=dict(x=[], y=[]))        # + crosshair

# ── Plot ──────────────────────────────────────────────────────────────────
plot = figure(
    width=700, height=700,
    title="Click image to place region centre  ·  then Generate Points",
    tools="pan,wheel_zoom,box_zoom,reset,save,tap",
    x_range=(0, 2048), y_range=(0, 2048),
)
plot.toolbar.active_tap = plot.select_one("TapTool")

plot.image(
    image="image", x="x", y="y", dw="dw", dh="dh",
    source=image_source, palette="Greys256",
)
plot.patches(
    "xs", "ys", source=region_source,
    fill_color="#1a73e8", fill_alpha=0.08,
    line_color="#1a73e8", line_width=1.8, line_dash="dashed",
)
abl_renderer = plot.scatter(
    "x", "y", source=points_source,
    color="#e53935", size=5, alpha=0.75,
    legend_label="Ablation points",
)
plot.scatter(
    "x", "y", source=center_source,
    color="#1a73e8", size=12, marker="cross",
    line_width=2.5, legend_label="Centre",
)
plot.add_tools(HoverTool(
    renderers=[abl_renderer],
    tooltips=[("x", "@x{0.0} px"), ("y", "@y{0.0} px")],
))
plot.legend.location     = "top_right"
plot.legend.click_policy = "hide"
plot.title.text_font_size = "12px"
plot.title.text_color    = "#555"

# ── Tap → set centre ──────────────────────────────────────────────────────
def _on_tap(event):
    w_cx.value = f"{event.x:.1f}"
    w_cy.value = f"{event.y:.1f}"
    center_source.data = dict(x=[event.x], y=[event.y])
    _update_outline()
    set_status(f"Centre set → ({event.x:.1f}, {event.y:.1f}) px", "blue")

plot.on_event(Tap, _on_tap)

# ── Widget helpers ─────────────────────────────────────────────────────────
def _inp(title, default, w=115):
    return TextInput(title=title, value=str(default), width=w)

def _sep(label):
    return Div(
        text=(
            f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;'
            f'color:#888;text-transform:uppercase;margin-top:12px;'
            f'padding-bottom:3px;border-bottom:1px solid #e0e0e0;">'
            f'{label}</div>'
        ),
        width=310,
    )

def _f(w):
    try:    return float(w.value.strip())
    except: return None

def _i(w):
    try:    return int(w.value.strip())
    except: return None

# ── Widgets ───────────────────────────────────────────────────────────────

# Camera / stage
w_cam_view  = _inp("View",    1, 70)
w_cam_chan  = _inp("Channel", 1, 70)
w_cam_plane = _inp("Plane",   1, 70)
w_pos_name  = _inp("Ablation position name", "Ablation", 210)

# Region shape selector
w_shape = RadioButtonGroup(
    labels=["● Circle", "■ Rectangle"], active=0, width=230,
)

# Centre (auto-filled by click, also manually editable)
w_cx = _inp("Centre X (px)", 1024)
w_cy = _inp("Centre Y (px)", 1024)

# Circle-specific
w_radius = _inp("Radius (µm)", 20)

# Rectangle-specific
w_rect_w   = _inp("Width (µm)",   40)
w_rect_h   = _inp("Height (µm)",  20)
w_rotation = _inp("Rotation (°)",  0)

# Ablation
w_density     = _inp("Point spacing (µm)", 1.0)
w_pulse_count = _inp("Pulse count",        10)

# Status bar
status_div = Div(
    text="<b>Ready.</b>",
    width=700,
    styles={
        "font-size": "13px", "padding": "6px 10px",
        "background": "#f8f9fa", "border": "1px solid #dee2e6",
        "border-radius": "4px", "margin-top": "4px",
    },
)

def set_status(msg, color="#212529"):
    status_div.text = (
        f'<span style="color:{color};font-weight:600;">●</span> {msg}'
    )

# Buttons
_BW = 148
btn_snap      = Button(label="Snap Image",          button_type="primary",  width=_BW)
btn_gen       = Button(label="Generate Points",      button_type="default",  width=_BW)
btn_ablate    = Button(label="⚡ Ablate",            button_type="danger",   width=_BW)
btn_start_acq = Button(label="▶ Start Acquisition",  button_type="success",  width=_BW)
btn_stop_acq  = Button(label="■ Stop Acquisition",   button_type="warning",  width=_BW)
btn_clear     = Button(label="✕ Clear",              button_type="light",    width=_BW)

# ── Geometry helpers ──────────────────────────────────────────────────────

def _circle_outline(cx, cy, r_um, n=80):
    """Outline polygon of a circle (pixel coords)."""
    r_px = r_um / UM_PER_PX
    a    = np.linspace(0, 2 * np.pi, n, endpoint=True)
    return [list(cx + r_px * np.cos(a))], [list(cy + r_px * np.sin(a))]


def _rect_outline(cx, cy, w_um, h_um, angle_deg):
    """Outline polygon of a rotated rectangle (pixel coords)."""
    hw, hh = (w_um / UM_PER_PX) / 2, (h_um / UM_PER_PX) / 2
    ang    = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]
    xs = [cx + x * ca - y * sa for x, y in corners]
    ys = [cy + x * sa + y * ca for x, y in corners]
    return [xs], [ys]


def _points_circle(cx, cy, r_um, spacing_um):
    """
    Hexagonal-packed grid clipped to a circle.
    Returns list of (px, py) in image pixel coordinates.
    """
    r_px = r_um / UM_PER_PX
    s    = (spacing_um / UM_PER_PX) * HEX_FRAC
    pts  = []
    xs   = np.arange(cx - r_px, cx + r_px + s, s)
    for i, x in enumerate(xs):
        off = s / 2 if i % 2 else 0.0
        ys  = np.arange(cy - r_px + off, cy + r_px + s, s)
        if i % 2:
            ys = ys[::-1]
        for y in ys:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r_px ** 2:
                pts.append((x, y))
    return pts


def _points_rect(cx, cy, w_um, h_um, angle_deg, spacing_um):
    """
    Regular grid inside a rotated rectangle.
    Returns list of (px, py) in image pixel coordinates.
    """
    hw  = (w_um / UM_PER_PX) / 2
    hh  = (h_um / UM_PER_PX) / 2
    s   = spacing_um / UM_PER_PX
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    xs  = np.arange(-hw, hw + s, s)
    for i, lx in enumerate(xs):
        ys = np.arange(-hh, hh + s, s)
        if i % 2:
            ys = ys[::-1]
        for ly in ys:
            pts.append((cx + lx * ca - ly * sa,
                        cy + lx * sa + ly * ca))
    return pts


def _px_to_stage_um(px, py):
    """
    Image pixel  →  µm offset relative to the ablation position.
    Convention: image centre  =  stage position 'position_name'  =  (0, 0) µm.
    """
    h, w = img_hw
    return (px - w / 2) * UM_PER_PX, (py - h / 2) * UM_PER_PX

# ── Live outline refresh ───────────────────────────────────────────────────

def _update_outline(attr=None, old=None, new=None):
    """Redraws the region outline whenever any geometry widget changes."""
    cx = _f(w_cx)
    cy = _f(w_cy)
    if cx is None or cy is None:
        return
    shape = w_shape.active
    if shape == 0:
        r = _f(w_radius)
        if r and r > 0:
            xs, ys = _circle_outline(cx, cy, r)
            region_source.data = dict(xs=xs, ys=ys)
    else:
        ww = _f(w_rect_w)
        hh = _f(w_rect_h)
        ro = _f(w_rotation) or 0.0
        if ww and hh and ww > 0 and hh > 0:
            xs, ys = _rect_outline(cx, cy, ww, hh, ro)
            region_source.data = dict(xs=xs, ys=ys)

# Wire all geometry widgets → live outline
for _w in (w_cx, w_cy, w_radius, w_rect_w, w_rect_h, w_rotation):
    _w.on_change("value", _update_outline)


def _on_shape_change(attr, old, new):
    circle_box.visible = (new == 0)
    rect_box.visible   = (new == 1)
    region_source.data = dict(xs=[[]], ys=[[]])
    points_source.data = dict(x=[], y=[])
    _update_outline()

w_shape.on_change("active", _on_shape_change)

# ── Button callbacks ──────────────────────────────────────────────────────

def on_snap(_=None):
    try:
        time_lapse_controller.snap()
        image = camera.image_get(
            _i(w_cam_view) or 1,
            _i(w_cam_chan) or 1,
            _i(w_cam_plane) or 1,
        )
    except Exception as exc:
        set_status(f"Snap failed: {exc}", "red")
        return

    image = np.flip(image, 0).astype(np.float64)
    lo, hi = image.min(), image.max()
    if hi > lo:
        image = (image - lo) / (hi - lo)
    h, w = image.shape
    img_hw[0], img_hw[1] = h, w

    plot.x_range.start, plot.x_range.end = 0, w
    plot.y_range.start, plot.y_range.end = 0, h
    image_source.data = dict(image=[image], x=[0], y=[0], dw=[w], dh=[h])

    region_source.data = dict(xs=[[]], ys=[[]])
    points_source.data = dict(x=[], y=[])
    center_source.data = dict(x=[], y=[])
    set_status("Image snapped — click image to place region centre.", "blue")


def on_generate(_=None):
    cx = _f(w_cx)
    cy = _f(w_cy)
    spacing = _f(w_density)

    if cx is None or cy is None:
        set_status("Set a centre point by clicking the image.", "red"); return
    if not spacing or spacing <= 0:
        set_status("Point spacing must be > 0.", "red"); return

    shape = w_shape.active
    if shape == 0:                                        # circle
        r = _f(w_radius)
        if not r or r <= 0:
            set_status("Radius must be > 0.", "red"); return
        xs, ys = _circle_outline(cx, cy, r)
        pts    = _points_circle(cx, cy, r, spacing)
    else:                                                 # rectangle
        ww = _f(w_rect_w)
        hh = _f(w_rect_h)
        ro = _f(w_rotation) or 0.0
        if not ww or not hh or ww <= 0 or hh <= 0:
            set_status("Width and height must be > 0.", "red"); return
        xs, ys = _rect_outline(cx, cy, ww, hh, ro)
        pts    = _points_rect(cx, cy, ww, hh, ro, spacing)

    region_source.data = dict(xs=xs, ys=ys)
    center_source.data = dict(x=[cx], y=[cy])

    if pts:
        px_v, py_v = zip(*pts)
        points_source.data = dict(x=list(px_v), y=list(py_v))
        set_status(
            f"{len(pts)} ablation points generated  ·  spacing {spacing} µm  ·  "
            f"ready to Ablate.",
            "#2e7d32",
        )
    else:
        points_source.data = dict(x=[], y=[])
        set_status("No points generated — region may be too small for this spacing.", "orange")


def _ablate_thread():
    """
    Runs in a background thread.
    All Bokeh state updates go through add_next_tick_callback.
    pymcs calls are identical to the original script.
    """
    pos_name    = w_pos_name.value.strip() or "Ablation"
    pulse_count = _i(w_pulse_count) or 10
    pts         = list(zip(points_source.data["x"], points_source.data["y"]))
    total       = len(pts)

    for i, (px, py) in enumerate(pts):
        dx, dy = _px_to_stage_um(px, py)
        stage_xyz.move(pos_name, None, None, (dx, dy, 0))
        acquisition_controller.laser_ablate_uv(pulse_count)
        pct = int(100 * (i + 1) / total)
        msg = f"Ablating … {i + 1} / {total}  ({pct} %)"
        curdoc().add_next_tick_callback(lambda m=msg: set_status(m, "#e65100"))

    stage_xyz.move(pos_name)           # return to named position

    curdoc().add_next_tick_callback(
        lambda: set_status("Ablation complete ✓", "#2e7d32")
    )
    curdoc().add_next_tick_callback(lambda: time_lapse_controller.start())


def on_ablate(_=None):
    if not points_source.data["x"]:
        set_status("Generate points first before ablating.", "red"); return
    time_lapse_controller.stop()
    time.sleep(0.5)
    set_status("Ablating …", "#e65100")
    threading.Thread(target=_ablate_thread, daemon=True).start()


def on_clear(_=None):
    region_source.data = dict(xs=[[]], ys=[[]])
    points_source.data = dict(x=[], y=[])
    center_source.data = dict(x=[], y=[])
    set_status("Cleared.", "#555")


btn_snap.on_click(on_snap)
btn_gen.on_click(on_generate)
btn_ablate.on_click(on_ablate)
btn_clear.on_click(on_clear)
btn_start_acq.on_click(
    lambda _: (time_lapse_controller.start(),
               set_status("Acquisition running …", "blue"))
)
btn_stop_acq.on_click(
    lambda _: (time_lapse_controller.stop(),
               set_status("Acquisition stopped.", "#555"))
)

# ── Sub-panels toggled by shape selector ──────────────────────────────────
circle_box = column(
    w_radius,
    Div(
        text='<span style="font-size:11px;color:#888;">Hexagonal packing inside circle.</span>',
        width=220,
    ),
)
rect_box = column(
    row(w_rect_w, w_rect_h),
    w_rotation,
    Div(
        text='<span style="font-size:11px;color:#888;">Regular grid inside rotated rectangle.</span>',
        width=240,
    ),
    visible=False,
)

# ── Control panel ─────────────────────────────────────────────────────────
controls = column(

    _sep("Camera / Stage"),
    row(w_cam_view, w_cam_chan, w_cam_plane),
    w_pos_name,
    btn_snap,

    _sep("Region"),
    Div(
        text=(
            '<span style="font-size:11px;color:#555;">'
            'Snap image → click to set centre → choose shape → set parameters.</span>'
        ),
        width=300,
    ),
    w_shape,
    row(w_cx, w_cy),
    circle_box,
    rect_box,

    _sep("Ablation"),
    row(w_density, w_pulse_count),
    row(btn_gen, btn_clear),
    btn_ablate,

    _sep("Acquisition"),
    row(btn_start_acq, btn_stop_acq),

    width=320,
)

# ── Root layout ───────────────────────────────────────────────────────────
root = column(
    Div(
        text='<h2 style="margin:0 0 8px 0;font-size:20px;">Viventis Ablation Dashboard</h2>',
        width=1040,
    ),
    row(controls, Spacer(width=12), plot),
    status_div,
    Spacer(height=8),
)

curdoc().add_root(root)
curdoc().title = "Viventis Ablation Dashboard"