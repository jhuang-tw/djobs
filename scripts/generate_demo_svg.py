#!/usr/bin/env python3
"""Generate an animated terminal SVG from the migration demo.

Runs ``examples/legacy_queue/run_migration_demo.py``, captures every line with a
timestamp, then renders a dark-themed terminal SVG with CSS animations
(line-by-line reveal + auto-scroll).  Pure Python, no external tools.

Usage (from project root):
    python scripts/generate_demo_svg.py

Outputs:
    docs/demo.svg   — animated SVG for the README
    docs/demo.cast  — asciicast v2 recording (optional reuse)
"""

from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ─── SVG design tokens ──────────────────────────────────────────────
FONT_SIZE = 13
LINE_HEIGHT = 18
PAD_X = 12
TITLE_BAR_H = 36
CONTENT_PAD_TOP = 8
VISIBLE_LINES = 24
SVG_WIDTH = 700
CONTENT_H = VISIBLE_LINES * LINE_HEIGHT
SVG_HEIGHT = TITLE_BAR_H + CONTENT_H + CONTENT_PAD_TOP + 8

# Catppuccin Mocha palette
BG = "#1e1e2e"
TITLE_BG = "#181825"
TEXT_CLR = "#cdd6f4"
GREEN = "#a6e3a1"
RED = "#f38ba8"
YELLOW = "#f9e2af"
CYAN = "#89b4fa"
DIM = "#585b70"
BORDER = "#313244"
PROMPT_CLR = "#f5c2e7"


# ─── Step 1: run the demo ───────────────────────────────────────────

def capture_demo() -> list[tuple[float, str]]:
    """Run the migration demo and return (elapsed_sec, text) per line."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [sys.executable, str(Path("examples/legacy_queue/run_migration_demo.py"))]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        encoding="utf-8",
        errors="replace",
    )

    start = time.monotonic()
    lines: list[tuple[float, str]] = []
    for raw in proc.stdout:  # type: ignore[union-attr]
        elapsed = time.monotonic() - start
        lines.append((elapsed, raw.rstrip("\n")))
    proc.wait()
    return lines


# ─── Step 2: clean up & retime ──────────────────────────────────────

def clean_paths(lines: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Replace machine-specific absolute paths and normalise separators."""
    abs_path = str(Path("examples/demo_workspace").resolve())
    result: list[tuple[float, str]] = []
    for t, text in lines:
        text = text.replace(abs_path, "examples/demo_workspace")
        text = text.replace("\\", "/")  # normalise Windows backslashes
        result.append((t, text))
    return result


def retime(lines: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """Re-assign timestamps so the animation is pleasant to watch."""
    result: list[tuple[float, str]] = []
    t = 0.0

    for i, (_, text) in enumerate(lines):
        s = text.strip()

        if i == 0:
            delay = 1.2                           # hold command prompt
        elif "\U0001f4cb" in s:                    # 📋 enqueue — fast batch
            delay = 0.06
        elif s.startswith("\u2705"):                # ✅ complete — steady
            delay = 0.20
        elif s.startswith("\u23f3"):                # ⏳ pending
            delay = 0.14
        elif "\U0001f4a5" in s or "CRASHED" in s:   # 💥 crash — dramatic
            delay = 1.8
        elif s.startswith("STEP") or s.startswith("RESULT"):
            delay = 0.7
        elif "\u2500" * 10 in s or "=" * 10 in s:
            delay = 0.3
        elif "ALL FILES COMPLETED" in s:
            delay = 0.8
        elif "Queue:" in s or "\u2192" in s:       # → arrow
            delay = 0.4
        elif "This is what" in s:
            delay = 0.5
        elif not s:
            delay = 0.15
        else:
            delay = 0.18

        t += delay
        result.append((round(t, 2), text))

    return result


# ─── Step 3: SVG rendering ──────────────────────────────────────────

def line_color(text: str) -> str:
    s = text.strip()
    if s.startswith("$"):
        return PROMPT_CLR
    if "\u2705" in s:       # ✅
        return GREEN
    if "\U0001f4a5" in s or "CRASHED" in s:
        return RED
    if "\u23f3" in s:       # ⏳
        return YELLOW
    if "\U0001f4cb" in s:   # 📋
        return DIM
    if s.startswith("STEP") or s.startswith("RESULT") or s.startswith("\u2192"):
        return CYAN
    if "=" * 10 in s or "\u2500" * 10 in s:
        return DIM
    if "djobs" in s and "\u2014" in s:
        return GREEN
    return TEXT_CLR


def build_svg(lines: list[tuple[float, str]]) -> str:
    n = len(lines)
    hold_end = 5.0                             # hold final frame
    duration = lines[-1][0] + hold_end

    # ── scroll keyframes ──
    scroll_kf: list[tuple[float, int]] = []
    for i in range(VISIBLE_LINES, n):
        pct = lines[i][0] / duration * 100
        offset = (i - VISIBLE_LINES + 1) * LINE_HEIGHT
        scroll_kf.append((round(pct, 1), offset))
    if scroll_kf:
        scroll_kf.append((100.0, scroll_kf[-1][1]))

    o: list[str] = []  # output lines

    # SVG root
    o.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" width="{SVG_WIDTH}" '
        f"font-family=\"'Cascadia Code','SF Mono','Fira Code','Consolas',monospace\" "
        f'font-size="{FONT_SIZE}">'
    )

    # ── CSS ──
    o.append("<style>")
    o.append("  .ln{opacity:0;animation:a .05s forwards}")
    for i, (t, _) in enumerate(lines):
        o.append(f"  .l{i}{{animation-delay:{t:.2f}s}}")
    o.append("  @keyframes a{to{opacity:1}}")

    if scroll_kf:
        o.append(f"  .ct{{animation:s {duration:.1f}s forwards}}")
        o.append("  @keyframes s{")
        o.append("    0%{transform:translateY(0)}")
        seen: set[float] = set()
        for pct, offset in scroll_kf:
            if pct in seen:
                continue
            seen.add(pct)
            o.append(f"    {pct}%{{transform:translateY(-{offset}px)}}")
        o.append("  }")
    o.append("</style>")

    # ── terminal chrome ──
    o.append(
        f'<rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" rx="10"'
        f' fill="{BG}" stroke="{BORDER}" stroke-width="1"/>'
    )
    o.append(f'<rect width="{SVG_WIDTH}" height="{TITLE_BAR_H}" rx="10" fill="{TITLE_BG}"/>')
    o.append(f'<rect y="{TITLE_BAR_H - 8}" width="{SVG_WIDTH}" height="8" fill="{TITLE_BG}"/>')

    cy = TITLE_BAR_H // 2
    o.append(f'<circle cx="20" cy="{cy}" r="6" fill="#f38ba8" opacity=".8"/>')
    o.append(f'<circle cx="40" cy="{cy}" r="6" fill="#f9e2af" opacity=".8"/>')
    o.append(f'<circle cx="60" cy="{cy}" r="6" fill="#a6e3a1" opacity=".8"/>')
    o.append(
        f'<text x="{SVG_WIDTH // 2}" y="{cy + 4}" text-anchor="middle" '
        f'fill="{DIM}" font-size="12">djobs \u2014 crash-proof demo</text>'
    )

    # ── clipped content area ──
    clip_y = TITLE_BAR_H
    clip_h = SVG_HEIGHT - TITLE_BAR_H
    o.append("<defs>")
    o.append(
        f'  <clipPath id="t">'
        f'<rect x="0" y="{clip_y}" width="{SVG_WIDTH}" height="{clip_h}"/>'
        f'</clipPath>'
    )
    o.append("</defs>")

    o.append('<g clip-path="url(#t)">')
    o.append('  <g class="ct">')

    for i, (_, text) in enumerate(lines):
        color = line_color(text)
        y = TITLE_BAR_H + CONTENT_PAD_TOP + i * LINE_HEIGHT + FONT_SIZE
        escaped = html.escape(text) if text.strip() else "&#160;"
        o.append(f'  <text x="{PAD_X}" y="{y}" fill="{color}" class="ln l{i}">{escaped}</text>')

    o.append("  </g>")
    o.append("</g>")
    o.append("</svg>")

    return "\n".join(o)


# ─── Step 4: optional asciicast export ──────────────────────────────

def save_cast(lines: list[tuple[float, str]], path: Path) -> None:
    header = {"version": 2, "width": 80, "height": 24}
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for t, text in lines:
            f.write(json.dumps([round(t, 3), "o", text + "\n"]) + "\n")


# ─── main ───────────────────────────────────────────────────────────

def main() -> None:
    print("Running migration demo…")
    raw = capture_demo()
    print(f"Captured {len(raw)} lines")

    # Prepend a shell prompt for realism
    raw.insert(0, (0.0, "$ python examples/legacy_queue/run_migration_demo.py"))

    lines = clean_paths(raw)
    lines = retime(lines)

    out_dir = Path("docs")
    out_dir.mkdir(parents=True, exist_ok=True)

    svg_path = out_dir / "demo.svg"
    svg_path.write_text(build_svg(lines), encoding="utf-8")
    print(f"\u2713 {svg_path}  ({len(lines)} lines, {lines[-1][0]:.0f}s animation)")

    cast_path = out_dir / "demo.cast"
    save_cast(lines, cast_path)
    print(f"\u2713 {cast_path}")


if __name__ == "__main__":
    main()
