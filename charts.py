"""
charts.py — visual building blocks for the dashboard.

Sparklines and icons are generated as inline SVG so they can sit inside the same
HTML block as the card that owns them; a Plotly figure would render as its own
element and break the card layout. The gauge is a real speedometer built from
polar traces rather than Plotly's Indicator, because Indicator cannot draw a
needle from the hub.
"""

from __future__ import annotations

import math

# Matches the CSS stack in app.py; Plotly cannot read a CSS variable.
FONT = 'Calibri, Carlito, "Segoe UI", system-ui, sans-serif'

# ---------------------------------------------------------------- icons
# 24x24 stroke icons, currentColor so the card controls the colour.
ICONS: dict[str, str] = {
    "documents": '<path d="M8 3h8l4 4v10a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
                 '<path d="M16 3v4h4"/><path d="M9 12h7M9 16h5"/>',
    "target": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4.5"/>'
              '<circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none"/>',
    "send": '<path d="M21 4L3 11l7 3 3 7 8-17z"/><path d="M10 14l4-4"/>',
    "speed": '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4.5-5.5"/>'
             '<circle cx="12" cy="18" r="1.4" fill="currentColor" stroke="none"/>',
    "hourglass": '<path d="M7 3h10M7 21h10"/><path d="M7 3c0 4 5 6 5 6s5-2 5-6"/>'
                 '<path d="M7 21c0-4 5-6 5-6s5 2 5 6"/>',
    "flag": '<path d="M5 21V4"/><path d="M5 5h11l-2 3.5L16 12H5z"/>',
    "gear": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.4M12 18.6V21M3 12h2.4M18.6 12H21'
            'M5.6 5.6l1.7 1.7M16.7 16.7l1.7 1.7M18.4 5.6l-1.7 1.7M7.3 16.7l-1.7 1.7"/>',
    "trophy": '<path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 6H4v1a3 3 0 0 0 3 3"/>'
              '<path d="M17 6h3v1a3 3 0 0 1-3 3"/><path d="M10 19h4M12 14v5"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "rupee": '<path d="M7 5h10M7 9h10M16 5c0 4-4 4-9 4l8 10"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/>',
}


def icon(name: str, color: str, size: int = 20, stroke: float = 1.7) -> str:
    body = ICONS.get(name, ICONS["documents"])
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>')


# ---------------------------------------------------------------- sparkline
def sparkline_svg(values: list[float], color: str, width: int = 132, height: int = 30) -> str:
    """A filled sparkline as inline SVG. Returns '' when there is nothing to draw."""
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'

    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    pad = 3
    step = (width - pad * 2) / (len(pts) - 1)

    coords = [(pad + i * step,
               height - pad - (v - lo) / span * (height - pad * 2))
              for i, v in enumerate(pts)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{coords[0][0]:.1f},{height} " + line + f" {coords[-1][0]:.1f},{height}"
    lx, ly = coords[-1]
    uid = f"sg{abs(hash((tuple(pts), color))) % 99999}"

    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="{uid}" x1="0" x2="0" y1="0" y2="1">'
        f'<stop offset="0%" stop-color="{color}" stop-opacity=".30"/>'
        f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
        f'</linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#{uid})"/>'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.9" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{color}"/>'
        f'</svg>')


# ---------------------------------------------------------------- ring
def ring_svg(pct: float, color: str, size: int = 92, thickness: int = 7,
             inner: str = "") -> str:
    """Progress ring used by the pipeline nodes."""
    r = (size - thickness) / 2
    circ = 2 * math.pi * r
    dash = max(0.0, min(1.0, pct / 100)) * circ
    c = size / 2
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="#EEF0F4" stroke-width="{thickness}"/>'
        f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{color}" stroke-width="{thickness}" '
        f'stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}" '
        f'transform="rotate(-90 {c} {c})"/>'
        f'<g transform="translate({c - 13} {c - 13})">{inner}</g>'
        f'</svg>')


# ---------------------------------------------------------------- gauge
def _blend(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    return "#%02X%02X%02X" % tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def gauge_arc_colors(n: int) -> list[str]:
    """Red → amber → green sweep, as on a speedometer dial."""
    red, amber, green = (214, 69, 69), (232, 168, 56), (14, 159, 110)
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append(_blend(red, amber, t / 0.5) if t < 0.5
                   else _blend(amber, green, (t - 0.5) / 0.5))
    return out


def gauge_figure(value: float, marker: float | None = None,
                 center_label: str = "", sub_label: str = "",
                 height: int = 250, segments: int = 40):
    """A speedometer: graduated arc, tick marks, and a needle from the hub.

    `value` and `marker` are percentages. `marker` draws the comparison pointer
    (the share of the year elapsed, for the target gauge).
    """
    import plotly.graph_objects as go

    value = max(0.0, min(100.0, float(value)))
    colors = gauge_arc_colors(segments)
    seg = 180 / segments
    # 0% sits at 180° (left) and 100% at 0° (right).
    centers = [180 - (i + 0.5) * seg for i in range(segments)]

    fig = go.Figure()
    fig.add_trace(go.Barpolar(
        r=[0.30] * segments, base=[0.62] * segments, theta=centers, width=[seg * 0.92] * segments,
        marker=dict(color=colors, line=dict(width=0)), hoverinfo="skip", showlegend=False))

    # Tick marks every 10%, longer at each 20%.
    for pct in range(0, 101, 5):
        ang = 180 - pct * 1.8
        long = pct % 20 == 0
        fig.add_trace(go.Scatterpolar(
            r=[0.94, 1.00] if long else [0.96, 1.00], theta=[ang, ang], mode="lines",
            line=dict(color="#98A2B3" if long else "#D0D5DD", width=1.4 if long else 1),
            hoverinfo="skip", showlegend=False))
        if long:
            fig.add_trace(go.Scatterpolar(
                r=[1.10], theta=[ang], mode="text", text=[str(pct)],
                textfont=dict(size=10.5, color="#98A2B3", family=FONT), hoverinfo="skip", showlegend=False))

    if marker is not None:
        ang = 180 - max(0.0, min(100.0, marker)) * 1.8
        fig.add_trace(go.Scatterpolar(
            r=[0.58, 0.94], theta=[ang, ang], mode="lines",
            line=dict(color="#101828", width=2, dash="dot"),
            hoverinfo="skip", showlegend=False))

    needle = 180 - value * 1.8
    fig.add_trace(go.Scatterpolar(
        r=[0, 0.80], theta=[needle, needle], mode="lines",
        line=dict(color="#101828", width=3.4), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatterpolar(
        r=[0], theta=[0], mode="markers",
        marker=dict(size=15, color="#101828", line=dict(color="white", width=3)),
        hoverinfo="skip", showlegend=False))

    fig.update_layout(
        height=height, margin=dict(l=14, r=14, t=6, b=14),
        paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
        polar=dict(
            bgcolor="rgba(0,0,0,0)", hole=0.0, sector=[0, 180],
            domain=dict(x=[0, 1], y=[0.30, 1]),
            radialaxis=dict(range=[0, 1.18], visible=False),
            angularaxis=dict(visible=False, direction="counterclockwise",
                             thetaunit="degrees")),
        annotations=[
            dict(text=f"<b>{center_label}</b>", x=0.5, y=0.155, showarrow=False,
                 font=dict(family=FONT, size=28, color="#101828")),
            dict(text=sub_label, x=0.5, y=0.045, showarrow=False,
                 font=dict(family=FONT, size=12, color="#667085")),
        ])
    return fig
