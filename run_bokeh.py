"""
Viventis Ablation Dashboard v5
Run with:  bokeh serve --show ablation_dashboard.py

Shape modes
-----------
  Circle   - click centre on image, set radius
  Rect     - click centre on image, set width / height / rotation
  Polygon  - click vertices manually; "Close Polygon" to finalise;
             repeat for more regions; "Delete Last" to undo
  Freehand - drag to draw closed outline; repeat for more regions

Multiple regions are supported in Polygon and Freehand modes.
"Generate Points" fills ALL drawn regions simultaneously.
"""

import sys, os, time, math, threading
import numpy as np

# ---------------------------------------------------------------------------
# pymcs  (completely unchanged)
# ---------------------------------------------------------------------------
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
    print("Could not connect to microscope -- running disconnected.")

time_lapse_controller  = pymcs.TimeLapseController(microscope)
acquisition_controller = pymcs.AcquisitionController(microscope, "ACQ")
camera                 = pymcs.Camera(microscope, "CAM")
stage_xyz              = pymcs.StageXYZ(microscope, "STAGE")

# ---------------------------------------------------------------------------
# Bokeh imports
# ---------------------------------------------------------------------------
from bokeh.plotting import figure, curdoc
from bokeh.models import (
    TextInput, Button, Div, ColumnDataSource, Spacer,
    RadioButtonGroup, HoverTool, FreehandDrawTool,
)
from bokeh.layouts import column, row
from bokeh.events import Tap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
UM_PER_PX = 0.347

SH_CIRCLE = 0
SH_RECT   = 1
SH_POLY   = 2
SH_FREE   = 3

# ---------------------------------------------------------------------------
# Shared mutable state
# ---------------------------------------------------------------------------
img_hw = [2048, 2048]

# In-progress polygon vertices (list of (x, y) pixel coords)
_current_poly_verts = []

# All closed polygons: list of (xs_list, ys_list)
_closed_polys = []

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
_blank        = np.zeros((2048, 2048), dtype=np.float64)
image_source  = ColumnDataSource(data=dict(image=[_blank], x=[0], y=[0],
                                           dw=[2048], dh=[2048]))
region_source = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))   # circle/rect outline
points_source = ColumnDataSource(data=dict(x=[], y=[]))
center_source = ColumnDataSource(data=dict(x=[], y=[]))         # crosshair

# Polygon drawing sources
poly_closed_source  = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))   # filled closed polys
poly_open_source    = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))   # in-progress line
poly_vertex_source  = ColumnDataSource(data=dict(x=[], y=[]))         # vertex dots

# Freehand source
freehand_source = ColumnDataSource(data=dict(xs=[[]], ys=[[]]))

# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------
plot = figure(
    width=700, height=700,
    title="Snap image -> select shape -> draw / click -> Generate Points",
    tools="pan,wheel_zoom,box_zoom,reset,save,tap",
    x_range=(0, 2048), y_range=(0, 2048),
)

# image
plot.image(image="image", x="x", y="y", dw="dw", dh="dh",
           source=image_source, palette="Greys256")

# circle / rect outline
plot.patches("xs", "ys", source=region_source,
             fill_color="#1a73e8", fill_alpha=0.08,
             line_color="#1a73e8", line_width=1.8, line_dash="dashed",
             legend_label="Region outline")

# closed polygons (filled)
plot.patches("xs", "ys", source=poly_closed_source,
             fill_color="#ff9800", fill_alpha=0.12,
             line_color="#ff9800", line_width=2,
             legend_label="Polygons")

# in-progress polygon edge (open line)
plot.multi_line("xs", "ys", source=poly_open_source,
                line_color="#ff9800", line_width=1.5,
                line_dash="dotted", legend_label="In progress")

# vertex dots for in-progress polygon
plot.scatter("x", "y", source=poly_vertex_source,
             size=8, color="white", line_color="#ff9800",
             line_width=2, legend_label="Vertices")

# freehand outlines
freehand_renderer = plot.multi_line(
    "xs", "ys", source=freehand_source,
    line_color="#9c27b0", line_width=2,
    legend_label="Freehand regions",
)
freehand_draw_tool = FreehandDrawTool(renderers=[freehand_renderer])
plot.add_tools(freehand_draw_tool)

# ablation points
abl_rend = plot.scatter("x", "y", source=points_source,
                        color="#e53935", size=5, alpha=0.75,
                        legend_label="Ablation points")

# centre crosshair (circle/rect modes)
plot.scatter("x", "y", source=center_source,
             color="#1a73e8", size=14, marker="cross",
             line_width=2.5, legend_label="Centre")

tap_tool = plot.select_one("TapTool")
pan_tool = plot.select_one("PanTool")

plot.add_tools(HoverTool(renderers=[abl_rend],
                         tooltips=[("x", "@x{0.0} px"), ("y", "@y{0.0} px")]))

plot.legend.location      = "top_right"
plot.legend.click_policy  = "hide"
plot.title.text_font_size = "12px"
plot.title.text_color     = "#555"

# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
w_cam_view  = _inp("View",    1, 70)
w_cam_chan  = _inp("Channel", 1, 70)
w_cam_plane = _inp("Plane",   1, 70)
w_pos_name  = _inp("Ablation position name", "Ablation", 210)

w_shape = RadioButtonGroup(
    labels=["Circle", "Rect", "Polygon", "Freehand"],
    active=SH_CIRCLE, width=310,
)

w_cx = _inp("Centre X (px)", 1024)
w_cy = _inp("Centre Y (px)", 1024)

w_radius   = _inp("Radius (um)",    20)
w_rect_w   = _inp("Width (um)",     40)
w_rect_h   = _inp("Height (um)",    20)
w_rotation = _inp("Rotation (deg)",  0)

w_density     = _inp("Point spacing (um)", 1.0)
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
    status_div.text = f'<span style="color:{color};font-weight:600;">&#9679;</span> {msg}'

_BW = 148
btn_snap       = Button(label="Snap Image",          button_type="primary",  width=_BW)
btn_gen        = Button(label="Generate Points",      button_type="default",  width=_BW)
btn_ablate     = Button(label="Ablate",               button_type="danger",   width=_BW)
btn_start_acq  = Button(label="Start Acquisition",    button_type="success",  width=_BW)
btn_stop_acq   = Button(label="Stop Acquisition",     button_type="warning",  width=_BW)
btn_clear      = Button(label="Clear All",            button_type="light",    width=_BW)

# Polygon-specific buttons
btn_close_poly  = Button(label="Close Polygon",       button_type="success",  width=_BW)
btn_del_last    = Button(label="Delete Last Region",  button_type="warning",  width=_BW)
btn_del_free    = Button(label="Delete Last Region",  button_type="warning",  width=_BW)

region_count_div = Div(
    text='<span style="font-size:12px;color:#555;">0 region(s) drawn</span>',
    width=200,
)

# ---------------------------------------------------------------------------
# Polygon drawing helpers
# ---------------------------------------------------------------------------
def _refresh_poly_display():
    """Push current in-progress and closed polygon state to the plot sources."""
    # closed polygons
    if _closed_polys:
        c_xs = [p[0] for p in _closed_polys]
        c_ys = [p[1] for p in _closed_polys]
    else:
        c_xs, c_ys = [[]], [[]]
    poly_closed_source.data = dict(xs=c_xs, ys=c_ys)

    # in-progress open edge
    if len(_current_poly_verts) >= 2:
        vx = [v[0] for v in _current_poly_verts]
        vy = [v[1] for v in _current_poly_verts]
        poly_open_source.data = dict(xs=[vx], ys=[vy])
    else:
        poly_open_source.data = dict(xs=[[]], ys=[[]])

    # vertex dots
    if _current_poly_verts:
        poly_vertex_source.data = dict(
            x=[v[0] for v in _current_poly_verts],
            y=[v[1] for v in _current_poly_verts],
        )
    else:
        poly_vertex_source.data = dict(x=[], y=[])

    _update_region_count()


def _update_region_count():
    shape = w_shape.active
    if shape == SH_POLY:
        n = len(_closed_polys)
        extra = " (+ 1 open)" if _current_poly_verts else ""
    elif shape == SH_FREE:
        n = sum(1 for xs in freehand_source.data["xs"] if xs)
        extra = ""
    else:
        n = 0
        extra = ""
    region_count_div.text = (
        f'<span style="font-size:12px;color:#555;">'
        f'{n} region(s) drawn{extra}</span>'
    )


def _clear_poly_state():
    _current_poly_verts.clear()
    _closed_polys.clear()
    _refresh_poly_display()


# ---------------------------------------------------------------------------
# Tap handler  (routes by mode)
# ---------------------------------------------------------------------------
def _on_tap(event):
    shape = w_shape.active

    if shape == SH_CIRCLE:
        w_cx.value = f"{event.x:.1f}"
        w_cy.value = f"{event.y:.1f}"
        center_source.data = dict(x=[event.x], y=[event.y])
        _update_outline(None, None, None)
        set_status(f"Centre -> ({event.x:.1f}, {event.y:.1f}) px", "blue")

    elif shape == SH_RECT:
        w_cx.value = f"{event.x:.1f}"
        w_cy.value = f"{event.y:.1f}"
        center_source.data = dict(x=[event.x], y=[event.y])
        _update_outline(None, None, None)
        set_status(f"Centre -> ({event.x:.1f}, {event.y:.1f}) px", "blue")

    elif shape == SH_POLY:
        _current_poly_verts.append((event.x, event.y))
        _refresh_poly_display()
        n = len(_current_poly_verts)
        set_status(
            f"Vertex {n} placed at ({event.x:.1f}, {event.y:.1f})  "
            f"-- click to add more, or press 'Close Polygon'.",
            "#ff9800",
        )

    # SH_FREE: tap events are ignored; FreehandDrawTool handles drags

plot.on_event(Tap, _on_tap)

# ---------------------------------------------------------------------------
# Live outline refresh  (circle & rect only)
# ---------------------------------------------------------------------------
def _update_outline(attr, old, new):
    shape = w_shape.active
    if shape not in (SH_CIRCLE, SH_RECT):
        return
    cx, cy = _f(w_cx), _f(w_cy)
    if cx is None or cy is None:
        return
    if shape == SH_CIRCLE:
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

for _w in (w_cx, w_cy, w_radius, w_rect_w, w_rect_h, w_rotation):
    _w.on_change("value", _update_outline)

# ---------------------------------------------------------------------------
# Shape mode switch
# ---------------------------------------------------------------------------
def _on_shape_change(attr, old, new):
    circle_box.visible = (new == SH_CIRCLE)
    rect_box.visible   = (new == SH_RECT)
    poly_box.visible   = (new == SH_POLY)
    free_box.visible   = (new == SH_FREE)

    # clear everything when switching
    region_source.data = dict(xs=[[]], ys=[[]])
    points_source.data = dict(x=[], y=[])
    center_source.data = dict(x=[], y=[])
    _clear_poly_state()
    freehand_source.data = dict(xs=[[]], ys=[[]])
    _update_region_count()

    if new == SH_FREE:
        plot.toolbar.active_drag = freehand_draw_tool
        plot.toolbar.active_tap  = tap_tool   # keep tap so mode can still change
    else:
        plot.toolbar.active_drag = pan_tool
        plot.toolbar.active_tap  = tap_tool

    _update_outline(None, None, None)

w_shape.on_change("active", _on_shape_change)

# watch freehand source for count updates
freehand_source.on_change("data", lambda a, o, n: _update_region_count())

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _circle_outline(cx, cy, r_um, n=80):
    r_px = r_um / UM_PER_PX
    a    = np.linspace(0, 2 * np.pi, n, endpoint=True)
    return [list(cx + r_px * np.cos(a))], [list(cy + r_px * np.sin(a))]


def _rect_outline(cx, cy, w_um, h_um, angle_deg):
    hw, hh = (w_um / UM_PER_PX) / 2, (h_um / UM_PER_PX) / 2
    ang    = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh), (-hw, -hh)]
    xs = [cx + x * ca - y * sa for x, y in corners]
    ys = [cy + x * sa + y * ca for x, y in corners]
    return [xs], [ys]


def _points_circle(cx, cy, r_um, spacing_um):
    r_px = r_um / UM_PER_PX
    s    = (spacing_um / UM_PER_PX) * 0.7
    pts  = []
    for i, x in enumerate(np.arange(cx - r_px, cx + r_px + s, s)):
        off = s / 2 if i % 2 else 0.0
        col = np.arange(cy - r_px + off, cy + r_px + s, s)
        if i % 2:
            col = col[::-1]
        for y in col:
            if (x - cx) ** 2 + (y - cy) ** 2 <= r_px ** 2:
                pts.append((x, y))
    return pts


def _points_rect(cx, cy, w_um, h_um, angle_deg, spacing_um):
    hw  = (w_um / UM_PER_PX) / 2
    hh  = (h_um / UM_PER_PX) / 2
    s   = spacing_um / UM_PER_PX
    ang = math.radians(angle_deg)
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for i, lx in enumerate(np.arange(-hw, hw + s, s)):
        col = np.arange(-hh, hh + s, s)
        if i % 2:
            col = col[::-1]
        for ly in col:
            pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return pts


def _points_in_poly(poly_x, poly_y, spacing_um):
    """Vectorised ray-casting fill for an arbitrary closed polygon."""
    poly_x = np.asarray(poly_x, dtype=float)
    poly_y = np.asarray(poly_y, dtype=float)
    x_min, x_max = poly_x.min(), poly_x.max()
    y_min, y_max = poly_y.min(), poly_y.max()
    step = spacing_um / UM_PER_PX

    gx = np.arange(x_min, x_max + step, step)
    gy = np.arange(y_min, y_max + step, step)
    GX, GY = np.meshgrid(gx, gy)
    px_flat = GX.ravel()
    py_flat = GY.ravel()

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
    h, w = img_hw
    return (w / 2 - px)  * UM_PER_PX, (h / 2 - py) * UM_PER_PX

# ---------------------------------------------------------------------------
# Button callbacks
# ---------------------------------------------------------------------------
def on_snap(_=None):
    try:
        time_lapse_controller.snap()
        image = camera.image_get(
            _i(w_cam_view)  or 1,
            _i(w_cam_chan)  or 1,
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

    region_source.data   = dict(xs=[[]], ys=[[]])
    freehand_source.data = dict(xs=[[]], ys=[[]])
    points_source.data   = dict(x=[], y=[])
    center_source.data   = dict(x=[], y=[])
    _clear_poly_state()
    _update_region_count()
    set_status("Image snapped. Choose a shape mode and draw.", "blue")


def on_close_polygon(_=None):
    """Finalise the in-progress polygon and start a fresh one."""
    if len(_current_poly_verts) < 3:
        set_status("Need at least 3 vertices to close a polygon.", "orange")
        return
    xs = [v[0] for v in _current_poly_verts]
    ys = [v[1] for v in _current_poly_verts]
    _closed_polys.append((xs, ys))
    _current_poly_verts.clear()
    _refresh_poly_display()
    set_status(
        f"Polygon closed ({len(xs)} vertices). "
        f"{len(_closed_polys)} region(s) total. "
        f"Click to start another, or Generate Points.",
        "#ff9800",
    )


def on_del_last_poly(_=None):
    """Delete the most recently closed polygon, or clear the open one."""
    if _current_poly_verts:
        _current_poly_verts.clear()
        _refresh_poly_display()
        set_status("In-progress polygon cleared.", "#555")
    elif _closed_polys:
        _closed_polys.pop()
        _refresh_poly_display()
        points_source.data = dict(x=[], y=[])
        set_status(f"Last polygon deleted. {len(_closed_polys)} region(s) remain.", "#555")
    else:
        set_status("Nothing to delete.", "orange")


def on_del_last_free(_=None):
    """Delete the most recently drawn freehand stroke."""
    xs = list(freehand_source.data["xs"])
    ys = list(freehand_source.data["ys"])
    for i in range(len(xs) - 1, -1, -1):
        if xs[i]:
            xs.pop(i)
            ys.pop(i)
            break
    freehand_source.data = dict(xs=xs if xs else [[]], ys=ys if ys else [[]])
    points_source.data   = dict(x=[], y=[])
    n = sum(1 for x in freehand_source.data["xs"] if x)
    set_status(f"Last stroke deleted. {n} region(s) remain.", "#555")


def on_generate(_=None):
    spacing = _f(w_density)
    if not spacing or spacing <= 0:
        set_status("Point spacing must be > 0.", "red")
        return

    shape    = w_shape.active
    all_pts  = []
    n_regions = 0

    if shape == SH_CIRCLE:
        cx, cy = _f(w_cx), _f(w_cy)
        r = _f(w_radius)
        if None in (cx, cy, r) or r <= 0:
            set_status("Set a valid centre and radius.", "red"); return
        xs, ys = _circle_outline(cx, cy, r)
        region_source.data = dict(xs=xs, ys=ys)
        center_source.data = dict(x=[cx], y=[cy])
        all_pts   = _points_circle(cx, cy, r, spacing)
        n_regions = 1

    elif shape == SH_RECT:
        cx, cy = _f(w_cx), _f(w_cy)
        ww, hh = _f(w_rect_w), _f(w_rect_h)
        ro = _f(w_rotation) or 0.0
        if None in (cx, cy, ww, hh) or ww <= 0 or hh <= 0:
            set_status("Set a valid centre, width and height.", "red"); return
        xs, ys = _rect_outline(cx, cy, ww, hh, ro)
        region_source.data = dict(xs=xs, ys=ys)
        center_source.data = dict(x=[cx], y=[cy])
        all_pts   = _points_rect(cx, cy, ww, hh, ro, spacing)
        n_regions = 1

    elif shape == SH_POLY:
        # auto-close the open polygon if it has enough vertices
        if len(_current_poly_verts) >= 3:
            on_close_polygon()
        if not _closed_polys:
            set_status("Draw and close at least one polygon first.", "red"); return
        for xs, ys in _closed_polys:
            all_pts.extend(_points_in_poly(xs, ys, spacing))
        n_regions = len(_closed_polys)

    elif shape == SH_FREE:
        regions = [(xs, ys) for xs, ys in
                   zip(freehand_source.data["xs"], freehand_source.data["ys"]) if xs]
        if not regions:
            set_status("Draw at least one freehand region first.", "red"); return
        for xs, ys in regions:
            all_pts.extend(_points_in_poly(xs, ys, spacing))
        n_regions = len(regions)

    if all_pts:
        px_v, py_v = zip(*all_pts)
        points_source.data = dict(x=list(px_v), y=list(py_v))
        set_status(
            f"{len(all_pts)} ablation points across {n_regions} region(s)  "
            f"·  spacing {spacing} um  ·  ready to Ablate.",
            "#2e7d32",
        )
    else:
        points_source.data = dict(x=[], y=[])
        set_status("No points generated -- region too small for this spacing.", "orange")


def _ablate_thread(doc):
    pos_name    = w_pos_name.value.strip() or "Ablation"
    pulse_count = _i(w_pulse_count) or 10
    pts         = list(zip(points_source.data["x"], points_source.data["y"]))
    total       = len(pts)

    for i, (px, py) in enumerate(pts):
        dx, dy = _px_to_stage_um(px, py)
        print(f"Ablating point {i + 1} / {total} at ({px:.1f}, {py:.1f})")
        stage_xyz.move(pos_name, None, None, (dx, dy, 0))
        acquisition_controller.laser_ablate_uv(pulse_count)
        pct = int(100 * (i + 1) / total)
        msg = f"Ablating ... {i + 1} / {total}  ({pct}%)"
        doc.add_next_tick_callback(lambda m=msg: set_status(m, "#e65100"))

    stage_xyz.move(pos_name)
    doc.add_next_tick_callback(lambda: set_status("Ablation complete!", "#2e7d32"))
    doc.add_next_tick_callback(lambda: time_lapse_controller.start())


def on_ablate(_=None):
    if not points_source.data["x"]:
        set_status("Generate points first.", "red"); return
    time_lapse_controller.stop()
    time.sleep(0.5)
    set_status("Ablating ...", "#e65100")
    doc = curdoc()
    threading.Thread(target=_ablate_thread, args=(doc,), daemon=True).start()


def on_clear(_=None):
    region_source.data   = dict(xs=[[]], ys=[[]])
    freehand_source.data = dict(xs=[[]], ys=[[]])
    points_source.data   = dict(x=[], y=[])
    center_source.data   = dict(x=[], y=[])
    _clear_poly_state()
    _update_region_count()
    set_status("Cleared.", "#555")


btn_snap.on_click(on_snap)
btn_close_poly.on_click(on_close_polygon)
btn_del_last.on_click(on_del_last_poly)
btn_del_free.on_click(on_del_last_free)
btn_gen.on_click(on_generate)
btn_ablate.on_click(on_ablate)
btn_clear.on_click(on_clear)
btn_start_acq.on_click(
    lambda _: (time_lapse_controller.start(),
               set_status("Acquisition running ...", "blue"))
)
btn_stop_acq.on_click(
    lambda _: (time_lapse_controller.stop(),
               set_status("Acquisition stopped.", "#555"))
)

# ---------------------------------------------------------------------------
# Shape sub-panels
# ---------------------------------------------------------------------------
circle_box = column(
    row(w_cx, w_cy),
    w_radius,
    Div(text='<span style="font-size:11px;color:#888;">Click image to place centre.</span>',
        width=240),
)

rect_box = column(
    row(w_cx, w_cy),
    row(w_rect_w, w_rect_h),
    w_rotation,
    Div(text='<span style="font-size:11px;color:#888;">Click image to place centre.</span>',
        width=240),
    visible=False,
)

poly_box = column(
    Div(text=(
        '<b style="color:#ff9800;">Polygon mode</b><br>'
        '<span style="font-size:11px;color:#555;">'
        'Click on the image to place vertices.<br>'
        'Press <b>Close Polygon</b> when done.<br>'
        'Repeat to add more regions.</span>'
    ), width=300),
    region_count_div,
    row(btn_close_poly, btn_del_last),
    visible=False,
)

free_box = column(
    Div(text=(
        '<b style="color:#9c27b0;">Freehand mode</b><br>'
        '<span style="font-size:11px;color:#555;">'
        'Click and drag to draw a closed outline.<br>'
        'Release to finish. Repeat for more regions.</span>'
    ), width=300),
    region_count_div,
    btn_del_free,
    visible=False,
)

# ---------------------------------------------------------------------------
# Control panel
# ---------------------------------------------------------------------------
controls = column(
    _sep("Camera / Stage"),
    row(w_cam_view, w_cam_chan, w_cam_plane),
    w_pos_name,
    btn_snap,

    _sep("Region"),
    w_shape,
    circle_box,
    rect_box,
    poly_box,
    free_box,

    _sep("Ablation"),
    row(w_density, w_pulse_count),
    row(btn_gen, btn_clear),
    btn_ablate,

    _sep("Acquisition"),
    row(btn_start_acq, btn_stop_acq),

    width=330,
)

# ---------------------------------------------------------------------------
# Root layout
# ---------------------------------------------------------------------------
root = column(
    Div(text='<h2 style="margin:0 0 8px 0;font-size:20px;">Viventis Ablation Dashboard</h2>',
        width=1060),
    row(controls, Spacer(width=12), plot),
    status_div,
    Spacer(height=8),
)

curdoc().add_root(root)
curdoc().title = "Viventis Ablation Dashboard"