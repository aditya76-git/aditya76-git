#!/usr/bin/env python3
"""Generate a Monkeytype activity heatmap SVG.

Usage:
    MONKEYTYPE_APE_KEY=xxxx python generate_svg.py
    python generate_svg.py --input sample.json     # offline test with saved API response
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_URL = "https://api.monkeytype.com/users/currentTestActivity"
FONT_FALLBACK_URL = (
    "https://cdn.jsdelivr.net/npm/@fontsource/roboto-mono@5.0.8/files/"
    "roboto-mono-latin-400-normal.woff2"
)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def fmt_date(d):
    """e.g. 'Friday 19 Jun 2026' (locale independent)."""
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def load_config():
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def fetch_activity(key):
    import requests

    resp = requests.get(
        API_URL,
        headers={"Authorization": f"ApeKey {key}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def load_font_b64(cfg):
    path = ROOT / cfg["font"]["file"]
    if not path.exists():
        try:
            import requests

            r = requests.get(FONT_FALLBACK_URL, timeout=30)
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        except Exception as e:  # noqa: BLE001
            print(f"warning: could not load font ({e}); using system monospace", file=sys.stderr)
            return None
    return base64.b64encode(path.read_bytes()).decode()


def level_for(value, thresholds):
    if not value:
        return 0
    lvl = 0
    for i, t in enumerate(thresholds, start=1):
        if value >= t:
            lvl = i
    return max(lvl, 1)


def build_svg(payload, cfg):
    data = payload["data"]
    tests = data["testsByDays"]
    last_day = datetime.fromtimestamp(data["lastDay"] / 1000, tz=timezone.utc).date()
    first_day = last_day - timedelta(days=len(tests) - 1)

    lay, col = cfg["layout"], cfg["colors"]
    weeks, size, step = lay["weeks"], lay["cell_size"], lay["cell_step"]

    # Grid is Sunday-first; last column is the week containing `last_day`.
    last_week_start = last_day - timedelta(days=(last_day.weekday() + 1) % 7)
    grid_start = last_week_start - timedelta(weeks=weeks - 1)

    cells, total = {}, 0
    for i, v in enumerate(tests):
        day = first_day + timedelta(days=i)
        if day < grid_start:
            continue
        v = v or 0
        total += v
        cells[((day.weekday() + 1) % 7, (day - grid_start).days // 7)] = v

    pad = lay["padding"]
    gx = lay["label_width"] + pad
    header_h = 42
    gy = pad + header_h
    grid_w = (weeks - 1) * step + size
    width = round(gx + grid_w + pad)
    height = round(gy + 6 * step + size + pad)
    right = gx + grid_w

    fsize = cfg["font"]["size"]
    char_w = fsize * 0.6  # Roboto Mono advance width
    fam = cfg["font"]["family"]
    font_b64 = load_font_b64(cfg)

    tt = cfg.get("tooltip", {})
    tt_on = tt.get("enabled", False)
    tfs = tt.get("font_size", 18)
    tpx, tpy = tt.get("padding_x", 14), tt.get("padding_y", 9)

    # ---- grid cells + tooltips (built first so CSS rules can be added to <style>) ----
    cell_els, tip_els, css_rules = [], [], []
    n = 0
    for c in range(weeks):
        for r in range(7):
            day = grid_start + timedelta(days=c * 7 + r)
            if day > last_day:
                continue
            count = cells.get((r, c), 0)
            lvl = level_for(count, cfg["thresholds"])
            x, y = gx + c * step, gy + r * step
            if not tt_on:
                cell_els.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
                    f'rx="{lay["cell_radius"]}" fill="{col["levels"][lvl]}">'
                    f'<title>{fmt_date(day)}: {count} tests</title></rect>')
                continue

            cell_els.append(
                f'<rect id="c{n}" class="cell" x="{x:.1f}" y="{y:.1f}" width="{size}" '
                f'height="{size}" rx="{lay["cell_radius"]}" fill="{col["levels"][lvl]}"/>')

            fmt = tt["format_zero"] if count == 0 else (tt["format_one"] if count == 1 else tt["format"])
            text = fmt.format(count=count, date=fmt_date(day))
            tw = len(text) * tfs * 0.6 + 2 * tpx
            th = tfs + 2 * tpy
            tx = min(max(x + size / 2 - tw / 2, 4), width - tw - 4)
            ty = y - th - 8 if r >= 2 else y + size + 8  # above, except for the top rows
            tip_els.append(
                f'<g id="t{n}" class="tip">'
                f'<rect x="{tx:.1f}" y="{ty:.1f}" width="{tw:.1f}" height="{th}" rx="8"/>'
                f'<text x="{tx + tw / 2:.1f}" y="{ty + th / 2:.1f}" text-anchor="middle" '
                f'dominant-baseline="central">{text}</text></g>')
            css_rules.append(f"#c{n}:hover~#t{n}{{opacity:1}}")
            n += 1

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'width="{width}" height="{height}">']
    out.append("<style>")
    if font_b64:
        out.append(f'@font-face{{font-family:"{fam}";src:url(data:font/woff2;base64,{font_b64}) '
                   f'format("woff2");font-weight:400;font-style:normal;}}')
    out.append(f'text{{font-family:"{fam}",ui-monospace,Menlo,Consolas,monospace;'
               f'font-size:{fsize}px;fill:{col["text"]};}}')
    if tt_on:
        out.append(f'.cell:hover{{stroke:{tt["hover_outline"]};stroke-width:2;}}')
        out.append(f'.tip{{opacity:0;pointer-events:none;transition:opacity .08s;}}')
        out.append(f'.tip rect{{fill:{tt["background"]};stroke:{tt["border"]};stroke-width:1;}}')
        out.append(f'.tip text{{fill:{tt["text"]};font-size:{tfs}px;}}')
        out.extend(css_rules)
    out.append("</style>")
    out.append(f'<rect width="{width}" height="{height}" fill="{col["background"]}"/>')

    # header: total (left) + legend (right)
    ty = pad + 18
    out.append(f'<text x="{pad}" y="{ty}">{total:,} tests</text>')

    sq, gap = 21, 4.5
    more_x = right
    legend_end = more_x - 4 * char_w - 12
    legend_start = legend_end - (5 * sq + 4 * gap)
    less_x = legend_start - 12 - 4 * char_w
    out.append(f'<text x="{less_x:.1f}" y="{ty}">less</text>')
    for i, c in enumerate(col["levels"]):
        out.append(f'<rect x="{legend_start + i * (sq + gap):.1f}" y="{pad - 1}" width="{sq}" '
                   f'height="{sq}" rx="{lay["cell_radius"]}" fill="{c}"/>')
    out.append(f'<text x="{more_x:.1f}" y="{ty}" text-anchor="end">more</text>')

    # weekday labels
    if lay.get("show_weekday_labels", True):
        for r, name in ((1, "monday"), (3, "wednesday"), (5, "friday")):
            out.append(f'<text x="{pad}" y="{gy + r * step + size / 2 + 6:.1f}">{name}</text>')

    # cells first, tooltips last (so tooltips paint on top of every cell)
    out.extend(cell_els)
    out.extend(tip_els)

    out.append("</svg>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="use a saved API response instead of calling the API")
    ap.add_argument("--output", help="override output file path")
    args = ap.parse_args()

    cfg = load_config()
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        key = os.environ.get("MONKEYTYPE_APE_KEY")
        if not key:
            sys.exit("error: set MONKEYTYPE_APE_KEY (env var / GitHub secret)")
        payload = fetch_activity(key)

    if "data" not in payload or "testsByDays" not in payload["data"]:
        sys.exit(f"error: unexpected API response: {str(payload)[:300]}")

    svg = build_svg(payload, cfg)
    out_path = ROOT / (args.output or cfg["output_file"])
    out_path.write_text(svg, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
