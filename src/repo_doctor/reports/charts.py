"""Tiny dependency-free SVG charts. Every chart carries a <title>/<desc> for screen readers and prints its
numbers as text, so colour is never the only way to read it. Charts are only drawn when there is data."""
from __future__ import annotations

from html import escape

SEVERITY_COLORS = {"critical": "#b42318", "high": "#d9480f", "medium": "#b7791f", "low": "#2f7d4f", "info": "#667085"}
VERIFICATION_COLORS = {"verified": "#1d6fb8", "supported": "#6f42c1", "unverified": "#b7791f", "speculative": "#667085"}
AREA_COLORS = {"security": "#b42318", "quality": "#1d6fb8", "testing": "#2f7d4f"}


def bar_chart(title: str, description: str, items: list[tuple[str, int, str]], standalone: bool = False,
              uid: str = "chart") -> str:
    """Horizontal bars. `items` = (label, value, colour). Returns '' when there is nothing to draw."""
    items = [(label, value, color) for label, value, color in items if value > 0]
    if not items:
        return ""
    row, left, width, pad = 34, 130, 520, 12
    peak = max(v for _, v, _ in items)
    height = pad * 2 + row * len(items)
    inner = []
    for i, (label, value, color) in enumerate(items):
        y = pad + i * row
        w = max(4, round((width - left - 60) * value / peak))
        inner.append(
            f'<text x="{left - 8}" y="{y + 19}" text-anchor="end" font-size="15" fill="{"#222" if standalone else "currentColor"}">'
            f"{escape(label)}</text>"
            f'<rect x="{left}" y="{y + 4}" width="{w}" height="20" rx="3" fill="{color}"/>'
            f'<text x="{left + w + 8}" y="{y + 19}" font-size="15" font-weight="600" '
            f'fill="{"#222" if standalone else "currentColor"}">{value}</text>')
    bg = f'<rect width="{width}" height="{height}" fill="#fff"/>' if standalone else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'aria-labelledby="{uid}-t {uid}-d" font-family="system-ui,-apple-system,Segoe UI,sans-serif">'
            f'<title id="{uid}-t">{escape(title)}</title><desc id="{uid}-d">{escape(description)}</desc>'
            f"{bg}{''.join(inner)}</svg>")


def severity_chart(counts: dict[str, int], standalone: bool = False) -> str:
    items = [(k.capitalize(), counts.get(k, 0), SEVERITY_COLORS[k]) for k in ("critical", "high", "medium", "low", "info")]
    desc = "; ".join(f"{k}: {v}" for k, v, _ in items if v)
    return bar_chart("Findings by severity", f"Number of findings per severity. {desc}.", items, standalone, "sev")


VERIFICATION_LABELS = {"verified": "Confirmed", "supported": "Likely", "unverified": "Unconfirmed",
                       "speculative": "Speculative"}  # the words used everywhere else in the report


def verification_chart(counts: dict[str, int], standalone: bool = False) -> str:
    items = [(VERIFICATION_LABELS[k], counts.get(k, 0), VERIFICATION_COLORS[k]) for k in VERIFICATION_COLORS]
    desc = "; ".join(f"{k}: {v}" for k, v, _ in items if v)
    return bar_chart("Findings by verification status", f"How well each finding is backed up. {desc}.", items,
                     standalone, "ver")


def area_chart(counts: dict[str, int], standalone: bool = False) -> str:
    items = [(k.capitalize() if k != "quality" else "Code quality", counts.get(k, 0), AREA_COLORS[k]) for k in AREA_COLORS]
    desc = "; ".join(f"{k}: {v}" for k, v, _ in items if v)
    return bar_chart("Findings by area", f"Number of findings per analysis area. {desc}.", items, standalone, "area")


def trend_chart(runs: list[dict], standalone: bool = False) -> str:
    """Stacked severity bars per run (oldest to newest). Needs at least two comparable runs."""
    if len(runs) < 2:
        return ""
    width, pad, label_h = 640, 24, 26
    bar_h = 150
    n = len(runs)
    slot = (width - 2 * pad) / n
    bw = max(10, min(46, slot * 0.6))
    peak = max(sum(r["counts"].get(k, 0) for k in ("critical", "high", "medium", "low")) for r in runs) or 1
    parts = []
    same_day = len({r["created_at"][:10] for r in runs}) < len(runs)
    for i, r in enumerate(runs):
        x = pad + i * slot + (slot - bw) / 2
        y = pad + bar_h
        for k in ("low", "medium", "high", "critical"):
            v = r["counts"].get(k, 0)
            if not v:
                continue
            h = bar_h * v / peak
            y -= h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{SEVERITY_COLORS[k]}"/>')
        total = sum(r["counts"].get(k, 0) for k in ("critical", "high", "medium", "low"))
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 5:.1f}" text-anchor="middle" font-size="13" font-weight="600" '
                     f'fill="currentColor">{total}</text>')
        label = r["created_at"][5:10] + (" " + r["created_at"][11:16] if same_day else "")
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{pad + bar_h + 18}" text-anchor="middle" font-size="12" '
                     f'fill="currentColor">{escape(label)}</text>')
    height = pad * 2 + bar_h + label_h
    desc = "; ".join(f"{r['created_at'][:10]}: " + ", ".join(f"{k} {r['counts'].get(k, 0)}" for k in ("critical", "high", "medium", "low")) for r in runs)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" role="img" '
            f'aria-labelledby="trend-t trend-d" font-family="system-ui,-apple-system,Segoe UI,sans-serif">'
            f'<title id="trend-t">Findings per analysis run</title>'
            f'<desc id="trend-d">Stacked counts of critical, high, medium and low findings per run. {escape(desc)}.</desc>'
            f"{''.join(parts)}</svg>")
