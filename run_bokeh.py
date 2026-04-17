"""
Viventis Ablation Dashboard v3 — Region-based laser ablation
─────────────────────────────────────────────────────────────
Run with:  bokeh serve --show ablation_dashboard.py

Shape modes
───────────
  ● Circle      — click centre, set radius
  ■ Rectangle   — click centre, set width / height / rotation
  ◆ Polygon     — click vertices on image, double-click to close
  ✏ Freehand    — drag freely on image to draw a closed outline

After drawing / configuring, set point spacing and click Generate Points,
then Ablate.
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
    PolyDrawTool, FreehandDrawTool,
)
from bokeh.layouts import column, row
from bokeh.events import Tap

# ── Constants ─────────────────────────────────────────────────────────────
UM_PER_PX = 0.347

# Shape indices
SH_CIRCLE = 0
SH_RECT   = 1
SH_POLY   = 2
SH_FREE   = 3

# ── Mutable state ─────────────────────────────────────────────────────────
img_hw = [2048, 2048]        # [height, width], updated on snap

# ── Data sources ──────────────────────────────────────────────────────────
_blank        = np.zeros((2048, 2048), dtype=np.float64)
image_source  = ColumnDataSource(data=dict(image=[_blank], x=[0], y=[0], dw=[2048], dh=[2048]))
region_source = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))   # circle / rect outline
points_source = ColumnDataSource(data=dict(x=[], y=[]))          # ablation dots
center_source = ColumnDataSource(data=dict(x=[], y=[]))          # crosshair marker

# Draw-tool sources (one per tool — different glyph types)
poly_source     = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))  # PolyDrawTool  → patches
freehand_source = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))  # FreehandDrawTool → multi_line

# ── Plot ──────────────────────────────────────────────────────────────────
plot = figure(
    width=700, height=700,
    title="Snap image → select shape mode → draw / click → Generate Points",
    tools="pan,wheel_zoom,box_zoom,reset,save,tap",
    x_range=(0, 2048), y_range=(0, 2048),
)

# ---- image ---------------------------------------------------------------
plot.image(image="image", x="x", y="y", dw="dw", dh="dh",
           source=image_source, palette="Greys256")

# ---- circle / rect outline -----------------------------------------------
plot.patches("xs", "ys", source=region_source,
             fill_color="#1a73e8", fill_alpha=0.08,
             line_color="#1a73e8", line_width=1.8, line_dash="dashed",
             legend_label="Region outline")

# ---- polygon draw renderer (PolyDrawTool attaches here) ------------------
poly_renderer = plot.patches(
    "xs", "ys", source=poly_source,
    fill_color="#ff9800", fill_alpha=0.10,
    line_color="#ff9800", line_width=2,
    legend_label="Polygon region",
)

# ---- freehand draw renderer (FreehandDrawTool attaches here) -------------
freehand_renderer = plot.multi_line(
    "xs", "ys", source=freehand_source,
    line_color="#9c27b0", line_width=2,
    legend_label="Freehand region",
)

# ---- ablation points -----------------------------------------------------
abl_rend = plot.scatter("x", "y", source=points_source,
                         color="#e53935", size=5, alpha=0.75,
                         legend_label="Ablation points")

# ---- centre crosshair ----------------------------------------------------
plot.scatter("x", "y", source=center_source,
             color="#1a73e8", size=12, marker="cross",
             line_width=2.5, legend_label="Centre")

# ---- draw tools ----------------------------------------------------------
poly_draw_tool     = PolyDrawTool(renderers=[poly_renderer])
freehand_draw_tool = FreehandDrawTool(renderers=[freehand_renderer])
plot.add_tools(poly_draw_tool, freehand_draw_tool)

# keep a reference to the original TapTool so we can restore it
tap_tool = plot.select_one("TapTool")

# ---- hover ---------------------------------------------------------------
plot.add_tools(HoverTool(renderers=[abl_rend],
                          tooltips=[("x", "@x{0.0} px"), ("y", "@y{0.0} px")]))

plot.legend.location     = "top_right"
plot.legend.click_policy = "hide"
plot.title.text_font_size = "12px"
plot.title.text_color    = "#555"

# ── Tap → set centre (only active in Circle / Rectangle modes) ────────────
def _on_tap(event):
    w_cx.value = f"{event.x:.1f}"
    w_cy.value = f"{event.y:.1f}"
    center_source.data = dict(x=[event.x], y=[event.y])
    _update_outline(None, None, None)
    set_status(f"Centre → ({event.x:.1f}, {event.y:.1f}) px", "blue")

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

w_cam_view  = _inp("View",    1, 70)
w_cam_chan  = _inp("Channel", 1, 70)
w_cam_plane = _inp("Plane",   1, 70)
w_pos_name  = _inp("Ablation position name", "Ablation", 210)

w_shape = RadioButtonGroup(
    labels=["● Circle", "■ Rect", "◆ Polygon", "✏ Freehand"],
    active=SH_CIRCLE, width=310,
)

w_cx = _inp("Centre X (px)", 1024)
w_cy = _inp("Centre Y (px)", 1024)

w_radius   = _inp("Radius (µm)",  20)
w_rect_w   = _inp("Width (µm)",   40)
w_rect_h   = _inp("Height (µm)",  20)
w_rotation = _inp("Rotation (°)",  0)

w_density     = _inp("Point spacing (µm)", 1.0)
w_pulse_count = _inp("Pulse count",        10)

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

_BW = 148
btn_snap      = Button(label="Snap Image",          button_type="primary",  width=_BW)
btn_gen       = Button(label="Generate Points",      button_type="default",  width=_BW)
btn_ablate    = Button(label="⚡ Ablate",            button_type="danger",   width=_BW)
btn_start_acq = Button(label="▶ Start Acquisition",  button_type="success",  width=_BW)
btn_stop_acq  = Button(label="■ Stop Acquisition",   button_type="warning",  width=_BW)
btn_clear     = Button(label="✕ Clear",              button_type="light",    width=_BW)

# ── Geometry helpers ──────────────────────────────────────────────────────

def _circle_outline(cx, cy, r_um, n=80):
    r_px = r_um / UM_PER_PX
    a = np.linspace(0, 2 * np.pi, n, endpoint=True)
    return [list(cx + r_px * np.cos(a))], [list(cy + r_px * np.sin(a))]


def _rect_outline(cx, cy, w_um, h_um, angle_deg):
    hw, hh = (w_um / UM_PER_PX) / 2, (h_um / UM_PER_PX) / 2
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]
    xs = [cx + x * ca - y * sa for x, y in corners]
    ys = [cy + x * sa + y * ca for x, y in corners]
    return [xs], [ys]


def _points_circle(cx, cy, r_um, spacing_um):
    r_px = r_um / UM_PER_PX
    s = (spacing_um / UM_PER_PX) * 0.7     # hexagonal packing
    pts = []
    xs = np.arange(cx - r_px, cx + r_px + s, s)
    for i, x in enumerate(xs):
        off = s / 2 if i % 2 else 0.0
        ys = np.arange(cy - r_px + off, cy + r_px + s, s)
        if i % 2:
            ys = ys[::-1]
        for y in ys:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r_px ** 2:
                pts.append((x, y))
    return pts


def _points_rect(cx, cy, w_um, h_um, angle_deg, spacing_um):
    hw = (w_um / UM_PER_PX) / 2
    hh = (h_um / UM_PER_PX) / 2
    s  = spacing_um / UM_PER_PX
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for i, lx in enumerate(np.arange(-hw, hw + s, s)):
        for ly in (np.arange(-hh, hh + s, s)[::-1] if i % 2 else np.arange(-hh, hh + s, s)):
            pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return pts


def _points_in_poly(poly_x, poly_y, spacing_um):
    """
    Vectorised ray-casting point-in-polygon fill.
    Works with any closed polygon given in pixel coordinates.
    Returns list of (px, py).
    """
    poly_x = np.asarray(poly_x, dtype=float)
    poly_y = np.asarray(poly_y, dtype=float)

    x_min, x_max = poly_x.min(), poly_x.max()
    y_min, y_max = poly_y.min(), poly_y.max()

    step = spacing_um / UM_PER_PX

    # candidate grid (regular, no hex offset — plenty fine for arbitrary polys)
    gx = np.arange(x_min, x_max + step, step)
    gy = np.arange(y_min, y_max + step, step)
    GX, GY = np.meshgrid(gx, gy)
    px_flat = GX.ravel()
    py_flat = GY.ravel()

    # vectorised ray casting
    n      = len(poly_x)
    inside = np.zeros(len(px_flat), dtype=bool)
    j      = n - 1
    for i in range(n):
        xi, yi = poly_x[i], poly_y[i]
        xj, yj = poly_x[j], poly_y[j]
        cond  = (yi > py_flat) != (yj > py_flat)
        x_int = (xj - xi) * (py_flat - yi) / ((yj - yi) + 1e-12) + xi
        inside ^= cond & (px_flat < x_int)
        j = i

    return list(zip(px_flat[inside].tolist(), py_flat[inside].tolist()))


def _px_to_stage_um(px, py):
    """Pixel coord → µm offset relative to ablation position (= image centre)."""
    h, w = img_hw
    return (px - w / 2) * UM_PER_PX, (py - h / 2) * UM_PER_PX

# ── Live outline refresh (circle & rect only) ─────────────────────────────

def _update_outline(attr, old, new):
    shape = w_shape.active
    cx, cy = _f(w_cx), _f(w_cy)
    if cx is None or cy is None or shape not in (SH_CIRCLE, SH_RECT):
        return
    if shape == SH_CIRCLE:
        r = _f(w_radius)
        if r and r > 0:
            xs, ys = _circle_outline(cx, cy, r)
            region_source.data = dict(xs=xs, ys=ys)
    else:
        ww, hh, ro = _f(w_rect_w), _f(w_rect_h), _f(w_rotation) or 0.0
        if ww and hh and ww > 0 and hh > 0:
            xs, ys = _rect_outline(cx, cy, ww, hh, ro)
            region_source.data = dict(xs=xs, ys=ys)

for _w in (w_cx, w_cy, w_radius, w_rect_w, w_rect_h, w_rotation):
    _w.on_change("value", _update_outline)

# ── Shape mode switch ─────────────────────────────────────────────────────

def _on_shape_change(attr, old, new):
    circle_box.visible = (new == SH_CIRCLE)
    rect_box.visible   = (new == SH_RECT)
    draw_box.visible   = (new in (SH_POLY, SH_FREE))

    # clear all overlays when switching
    region_source.data  = dict(xs=[[]], ys=[[]])
    poly_source.data    = dict(xs=[[]], ys=[[]])
    freehand_source.data = dict(xs=[[]], ys=[[]])
    points_source.data  = dict(x=[], y=[])
    center_source.data  = dict(x=[], y=[])

    # activate the right tool
    if new == SH_POLY:
        plot.toolbar.active_tap  = poly_draw_tool
        plot.toolbar.active_drag = None           # disable pan while drawing
        draw_hint.text = (
            '<span style="color:#ff9800;font-weight:600;">◆ Polygon mode active</span><br>'
            '<span style="font-size:11px;color:#555;">'
            'Select the ◆ tool in the toolbar.<br>'
            'Click to place vertices · double-click to close.</span>'
        )
    elif new == SH_FREE:
        plot.toolbar.active_drag = freehand_draw_tool
        plot.toolbar.active_tap  = None
        draw_hint.text = (
            '<span style="color:#9c27b0;font-weight:600;">✏ Freehand mode active</span><br>'
            '<span style="font-size:11px;color:#555;">'
            'Select the ✏ tool in the toolbar.<br>'
            'Click-drag to draw a closed outline.</span>'
        )
    else:
        plot.toolbar.active_tap  = tap_tool
        plot.toolbar.active_drag = plot.select_one("PanTool")

    _update_outline(None, None, None)

w_shape.on_change("active", _on_shape_change)

# ── Button callbacks ──────────────────────────────────────────────────────

def on_snap(_=None):
    try:
        time_lapse_controller.snap()
        image = camera.image_get(_i(w_cam_view) or 1,
                                 _i(w_cam_chan)  or 1,
                                 _i(w_cam_plane) or 1)
    except Exception as exc:
        set_status(f"Snap failed: {exc}", "red"); return

    image = np.flip(image, 0).astype(np.float64)
    lo, hi = image.min(), image.max()
    if hi > lo:
        image = (image - lo) / (hi - lo)
    h, w = image.shape
    img_hw[0], img_hw[1] = h, w
    plot.x_range.start, plot.x_range.end = 0, w
    plot.y_range.start, plot.y_range.end = 0, h
    image_source.data = dict(image=[image], x=[0], y=[0], dw=[w], dh=[h])

    region_source.data   = dict(xs=[[]], ys=[[]])
    poly_source.data     = dict(xs=[[]], ys=[[]])
    freehand_source.data = dict(xs=[[]], ys=[[]])
    points_source.data   = dict(x=[], y=[])
    center_source.data   = dict(x=[], y=[])
    set_status("Image snapped. Now choose a shape mode.", "blue")


def _get_drawn_polygon():
    """Return (poly_x, poly_y) from whichever draw source has data, or (None, None)."""
    shape = w_shape.active
    if shape == SH_POLY:
        for xs, ys in zip(poly_source.data["xs"], poly_source.data["ys"]):
            if xs:
                return list(xs), list(ys)
    elif shape == SH_FREE:
        # FreehandDrawTool appends a new row per stroke; use the last non-empty one
        all_xs = freehand_source.data["xs"]
        all_ys = freehand_source.data["ys"]
        for xs, ys in zip(reversed(all_xs), reversed(all_ys)):
            if xs:
                return list(xs), list(ys)
    return None, None


def on_generate(_=None):
    spacing = _f(w_density)
    if not spacing or spacing <= 0:
        set_status("Point spacing must be > 0.", "red"); return

    shape = w_shape.active
    pts   = []

    if shape == SH_CIRCLE:
        cx, cy = _f(w_cx), _f(w_cy)
        r = _f(w_radius)
        if None in (cx, cy, r) or r <= 0:
            set_status("Set a valid centre and radius.", "red"); return
        xs, ys = _circle_outline(cx, cy, r)
        region_source.data = dict(xs=xs, ys=ys)
        center_source.data = dict(x=[cx], y=[cy])
        pts = _points_circle(cx, cy, r, spacing)

    elif shape == SH_RECT:
        cx, cy = _f(w_cx), _f(w_cy)
        ww, hh = _f(w_rect_w), _f(w_rect_h)
        ro = _f(w_rotation) or 0.0
        if None in (cx, cy, ww, hh) or ww <= 0 or hh <= 0:
            set_status("Set a valid centre, width and height.", "red"); return
        xs, ys = _rect_outline(cx, cy, ww, hh, ro)
        region_source.data = dict(xs=xs, ys=ys)
        center_source.data = dict(x=[cx], y=[cy])
        pts = _points_rect(cx, cy, ww, hh, ro, spacing)

    elif shape in (SH_POLY, SH_FREE):
        poly_x, poly_y = _get_drawn_polygon()
        if poly_x is None:
            set_status("Draw a region on the image first.", "red"); return
        pts = _points_in_poly(poly_x, poly_y, spacing)

    if pts:
        px_v, py_v = zip(*pts)
        points_source.data = dict(x=list(px_v), y=list(py_v))
        set_status(
            f"{len(pts)} ablation points  ·  spacing {spacing} µm  ·  ready to Ablate.",
            "#2e7d32",
        )
    else:
        points_source.data = dict(x=[], y=[])
        set_status("No points generated — region too small for this spacing.", "orange")


def _ablate_thread():
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

    stage_xyz.move(pos_name)
    curdoc().add_next_tick_callback(
        lambda: set_status("Ablation complete ✓", "#2e7d32")
    )
    curdoc().add_next_tick_callback(lambda: time_lapse_controller.start())


def on_ablate(_=None):
    if not points_source.data["x"]:
        set_status("Generate points first.", "red"); return
    time_lapse_controller.stop()
    time.sleep(0.5)
    set_status("Ablating …", "#e65100")
    threading.Thread(target=_ablate_thread, daemon=True).start()


def on_clear(_=None):
    region_source.data   = dict(xs=[[]], ys=[[]])
    poly_source.data     = dict(xs=[[]], ys=[[]])
    freehand_source.data = dict(xs=[[]], ys=[[]])
    points_source.data   = dict(x=[], y=[])
    center_source.data   = dict(x=[], y=[])
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

# ── Shape sub-panels ──────────────────────────────────────────────────────

circle_box = column(
    row(w_cx, w_cy),
    w_radius,
    Div(text='<span style="font-size:11px;color:#888;">Click image to place centre.</span>',
        width=230),
)

rect_box = column(
    row(w_cx, w_cy),
    row(w_rect_w, w_rect_h),
    w_rotation,
    Div(text='<span style="font-size:11px;color:#888;">Click image to place centre.</span>',
        width=230),
    visible=False,
)

draw_hint = Div(
    text=(
        '<span style="color:#ff9800;font-weight:600;">◆ Polygon mode</span><br>'
        '<span style="font-size:11px;color:#555;">'
        'Select the ◆ tool in the toolbar.<br>'
        'Click vertices · double-click to close.</span>'
    ),
    width=290,
)

draw_box = column(
    draw_hint,
    Div(text='<span style="font-size:11px;color:#888;">Then click Generate Points.</span>',
        width=290),
    visible=False,
)

# ── Control panel ─────────────────────────────────────────────────────────
controls = column(
    _sep("Camera / Stage"),
    row(w_cam_view, w_cam_chan, w_cam_plane),
    w_pos_name,
    btn_snap,

    _sep("Region"),
    w_shape,
    circle_box,
    rect_box,
    draw_box,

    _sep("Ablation"),
    row(w_density, w_pulse_count),
    row(btn_gen, btn_clear),
    btn_ablate,

    _sep("Acquisition"),
    row(btn_start_acq, btn_stop_acq),

    width=330,
)

# ── Root layout ───────────────────────────────────────────────────────────
root = column(
    Div(text='<h2 style="margin:0 0 8px 0;font-size:20px;">Viventis Ablation Dashboard</h2>',
        width=1060),
    row(controls, Spacer(width=12), plot),
    status_div,
    Spacer(height=8),
)

curdoc().add_root(root)
curdoc().title = "Viventis Ablation Dashboard"