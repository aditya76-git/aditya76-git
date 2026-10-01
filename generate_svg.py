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

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'width="{width}" height="{height}">']
    out.append("<style>")
    if font_b64:
        out.append(f'@font-face{{font-family:"{fam}";src:url(data:font/woff2;base64,{font_b64}) '
                   f'format("woff2");font-weight:400;font-style:normal;}}')
    out.append(f'text{{font-family:"{fam}",ui-monospace,Menlo,Consolas,monospace;'
               f'font-size:{fsize}px;fill:{col["text"]};}}')
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

    # grid (cells after `last_day` are not drawn)
    for c in range(weeks):
        for r in range(7):
            day = grid_start + timedelta(days=c * 7 + r)
            if day > last_day:
                continue
            lvl = level_for(cells.get((r, c), 0), cfg["thresholds"])
            out.append(f'<rect x="{gx + c * step:.1f}" y="{gy + r * step:.1f}" width="{size}" '
                       f'height="{size}" rx="{lay["cell_radius"]}" fill="{col["levels"][lvl]}">'
                       f'<title>{day.isoformat()}: {cells.get((r, c), 0)} tests</title></rect>')

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
