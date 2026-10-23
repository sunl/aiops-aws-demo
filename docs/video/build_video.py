#!/usr/bin/env python3
"""Build the solution walkthrough video from docs/video/slides.py.

Pipeline (all steps cache their output under build/video/):
  1. render every slide step to HTML and screenshot it at 1920x1080 with playwright-cli
     (file:// and localhost are blocked for the browser here, so pages go in as data: URLs);
  2. synthesise the narration sentence by sentence with Amazon Polly (neural, Mandarin);
  3. lay the sentences out on one timeline, write narration.wav plus .srt / .ass subtitles;
  4. encode the frames and narration with ffmpeg (imageio-ffmpeg), subtitles burned in.

Output: dist/video/方案讲解.mp4 and dist/video/方案讲解.srt

Usage (from the project root):
  python3.12 -m venv .venv-video && .venv-video/bin/pip install imageio-ffmpeg==0.6.0 pygments==2.19.2
  AWS_PROFILE=<profile> .venv-video/bin/python docs/video/build_video.py           # full build
  .venv-video/bin/python docs/video/build_video.py --frames-only --only 12         # preview one slide's frames

Needs: playwright-cli (with ~/.local/browser-libs on hosts without system fonts/libs), the AWS CLI
with polly:SynthesizeSpeech, and the Noto Sans CJK fonts.
"""
import argparse
import base64
import concurrent.futures
import hashlib
import html
import importlib.util
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
BUILD = ROOT / "build" / "video"
DIST = ROOT / "dist" / "video"
OUT_NAME = "方案讲解"
W, H = 1920, 1080
FPS = 30
SAMPLE_RATE = 24000
BROWSER_LIBS = Path(os.environ.get("BROWSER_LIBS", Path.home() / ".local" / "browser-libs"))
CJK_FONT_DIR = BROWSER_LIBS / "root" / "usr" / "share" / "fonts" / "google-noto-cjk"

# viewBox crops: drop each diagram's own title line and footnote (the slide has its own title)
DIAGRAM_CROP = {
    "architecture.svg": (10, 50, 1380, 740),
    "demo-deployment.svg": (10, 50, 1380, 758),
    "sequence-flows.svg": (10, 44, 1380, 866),
}

# Speech-only rewrites: subtitles keep the original wording.
TTS_REPLACE = [
    ("aiops-aws-demo", "AIOps AWS demo"), ("aws.aidevops", "AWS AI DevOps"), ("AIOps", "AI Ops"),
    ("es-mock", "ES mock"), ("nexus-mock", "Nexus mock"), ("feishu-notifier", "飞书 notifier"),
    ("feishu-msg", "飞书 message"), ("chat-worker", "chat worker"), ("alert-forwarder", "alert forwarder"),
    ("alert_forwarder", "alert forwarder"), ("chat_worker", "chat worker"), ("feishu_ws", "飞书 WS"),
    ("es_mcp", "ES MCP"), ("nexus_mcp", "Nexus MCP"), ("order_service", "order service"),
    ("HMAC-SHA256", "H Mac SHA 256"), ("HMAC", "H Mac"), ("SigV4", "Sig V4"), ("A2A", "A to A"),
    ("OAuth", "O Auth"), ("JSON-RPC", "JSON RPC"), ("FastMCP", "Fast MCP"), ("ListJournalRecords", "List Journal Records"),
    ("CloudFormation", "Cloud Formation"), ("CloudWatch", "Cloud Watch"), ("CloudTrail", "Cloud Trail"),
    ("EventBridge", "Event Bridge"), ("DynamoDB", "Dynamo DB"), ("AgentCore", "Agent Core"), ("DevOps", "Dev Ops"),
    ("Alertmanager", "Alert manager"), ("X-Ray", "X Ray"), ("aws-cli", "AWS CLI"), ("cfn-lint", "CFN lint"),
    ("botocore", "boto core"), ("readOnlyHint", "read only hint"), ("incidentId", "incident ID"),
    ("taskId", "task ID"), ("message_id", "message ID"), ("event_id", "event ID"), ("AWS_IAM", "AWS IAM"),
    ("tools/list", "tools list"), ("tools/call", "tools call"), ("ws.Client", "WS Client"), ("frontmatter", "front matter"),
    ("template.yaml", "template 点 yaml"), ("deploy.sh", "deploy 点 sh"), ("Base64", "Base 64"),
    ("PoC", "P O C"), ("PR", "P R"), ("5xx", "5 X X"), ("4xx", "4 X X"), ("conv", "conversation"),
    ("@", "艾特"), ("“", ""), ("”", ""),
]


def load_slides():
    spec = importlib.util.spec_from_file_location("slides", HERE / "slides.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SLIDES


def esc(text) -> str:
    """HTML-escape, then render `code` spans."""
    out = html.escape(str(text))
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", out)


# ----------------------------------------------------------------------------- styles
CSS = """
*{box-sizing:border-box}
html,body{margin:0;width:1920px;height:1080px;overflow:hidden}
body{font-family:'Noto Sans CJK SC','Noto Sans SC',sans-serif;color:#0F172A;background:#FFFFFF}
code{font-family:'DejaVu Sans Mono','Noto Sans Mono CJK SC',monospace;font-size:.86em;background:#EEF2F7;
  border-radius:6px;padding:1px 8px;color:#0F172A}
.frame{position:relative;width:1920px;height:1080px;overflow:hidden}
.top{position:absolute;left:96px;right:96px;top:40px;display:flex;justify-content:space-between;font-size:22px;color:#64748B}
.top .sec{color:#D97706;font-weight:700;letter-spacing:1px}
h1{position:absolute;left:96px;top:78px;margin:0;font-size:50px;line-height:1.2;font-weight:700}
.bar{position:absolute;left:96px;top:150px;width:84px;height:6px;border-radius:3px;background:#FF9900}
.body{position:absolute;left:96px;right:96px;top:192px;bottom:150px}
.dim{opacity:.3}
.card{background:#F8FAFC;border:2px solid #E2E8F0;border-radius:18px;padding:26px 32px}
.card.on{background:#FFF7EB;border-color:#FF9900;box-shadow:0 0 0 4px rgba(255,153,0,.18)}
.card h3{margin:0 0 14px;font-size:35px;line-height:1.3}
.card p{margin:0;font-size:28px;line-height:1.6;color:#334155}
.grid{display:grid;gap:26px;align-content:stretch}
.col-flex{display:flex;flex-direction:column;gap:24px;height:100%}
.aside{background:#0F172A;color:#E2E8F0;border-radius:14px;padding:20px 30px;font-size:24px;line-height:1.5}
.aside.on{box-shadow:0 0 0 4px #FF9900}
.lead{background:#EEF4FF;border-left:8px solid #3370FF;border-radius:10px;padding:20px 30px;font-size:28px;line-height:1.5}
.lead.on{background:#FFF7EB;border-left-color:#FF9900}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:25px;line-height:1.45}
th{background:#0F172A;color:#FFFFFF;text-align:left;padding:17px 24px;font-weight:700}
th:first-child{border-top-left-radius:12px}th:last-child{border-top-right-radius:12px}
td{padding:15px 24px;border-bottom:1.5px solid #E2E8F0;vertical-align:top;color:#1E293B}
tr.on td{background:#FFF7EB}
tr.on td:first-child{box-shadow:inset 7px 0 0 #FF9900}
td.mono{font-family:'DejaVu Sans Mono','Noto Sans Mono CJK SC',monospace;font-size:.9em}
.note{margin-top:24px;font-size:26px;color:#475569;background:#F1F5F9;border-radius:12px;padding:18px 28px}
.note.on{background:#FFF7EB;box-shadow:0 0 0 3px #FF9900;color:#0F172A}
.items{display:flex;flex-direction:column;gap:20px}
.item{border-left:6px solid #CBD5E1;padding:8px 0 8px 24px}
.item.on{border-left-color:#FF9900;background:linear-gradient(90deg,#FFF7EB,rgba(255,247,235,0))}
.item b{display:block;font-size:32px;margin-bottom:6px}
.item span{font-size:26px;line-height:1.55;color:#334155}
.panel-label{font-size:22px;color:#64748B;margin:0 0 10px 4px;font-family:'DejaVu Sans Mono','Noto Sans Mono CJK SC',monospace}
.code{background:#0D1117;color:#E6EDF3;border-radius:16px;padding:20px 0;font-family:'DejaVu Sans Mono','Noto Sans Mono CJK SC',monospace;overflow:hidden}
.code .ln{display:flex}
.code .no{flex:none;width:3.4em;text-align:right;padding-right:1.1em;color:#6E7681;user-select:none}
.code .src{flex:1;white-space:pre;padding-right:28px}
.code.wrap .src{white-space:pre-wrap;word-break:break-all}
.code .ln.on{background:rgba(255,166,0,.17);box-shadow:inset 6px 0 0 #FF9900}
.code .ln.dim .src,.code .ln.dim .no{opacity:.36}
.code .gap .src{color:#6E7681}
.chip{position:absolute;right:96px;top:70px;background:#FF9900;color:#111827;font-weight:700;font-size:26px;
  border-radius:999px;padding:8px 26px}
.frow{display:flex;align-items:stretch}
.fcard{flex:none;background:#F8FAFC;border:2px solid #E2E8F0;border-radius:16px;padding:18px 22px;height:244px;overflow:hidden}
.fcard.on{background:#FFF7EB;border-color:#FF9900;box-shadow:0 0 0 4px rgba(255,153,0,.18)}
.fhead{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.fhead b{font-size:30px;line-height:1.25}
.fnum{flex:none;width:44px;height:44px;border-radius:50%;background:#0F172A;color:#FFFFFF;font-size:24px;font-weight:700;
  display:flex;align-items:center;justify-content:center}
.fcard.on .fnum{background:#FF9900;color:#111827}
.fwho{font-size:21px;color:#2F5BD3;margin-bottom:8px;font-family:'DejaVu Sans Mono','Noto Sans Mono CJK SC',monospace}
.fdet{font-size:23px;line-height:1.5;color:#334155}
.farr{flex:none;display:flex;align-items:center;justify-content:center}
.ftime{display:flex;align-items:stretch;margin-top:20px;border:2px solid #E2E8F0;border-radius:12px;overflow:hidden}
.ftime.on{border-color:#FF9900;box-shadow:0 0 0 4px rgba(255,153,0,.18)}
.ftime .tl{flex:none;background:#0F172A;color:#FFFFFF;font-size:22px;font-weight:700;padding:12px 22px;display:flex;align-items:center}
.ftime .seg{flex:1;background:#F1F5F9;padding:12px 22px;font-size:24px;color:#334155;border-left:2px solid #E2E8F0}
.ftime.on .seg{background:#FFF7EB}
.ftime .seg b{color:#D97706;margin-left:12px}
.dark{background:radial-gradient(1200px 700px at 78% 18%,#1E3A66 0%,#0B1730 60%,#070F20 100%);color:#F8FAFC}
"""


def frame(inner: str, dark=False) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body><div class='frame{' dark' if dark else ''}'>{inner}</div></body></html>")


def header(slide, page, total, compact=False):
    top = (f"<div class='top' style='top:{22 if compact else 40}px'><span class='sec'>{esc(slide.get('section', ''))}</span>"
           f"<span>AIOps 方案讲解 · {page:02d} / {total:02d}</span></div>")
    if compact:
        return top + f"<h1 style='top:58px;font-size:40px'>{esc(slide['title'])}</h1>"
    return top + f"<h1>{esc(slide['title'])}</h1><div class='bar'></div>"


def on_cls(key, hl):
    if hl is None:
        return ""
    return " on" if key in hl else " dim"


# ----------------------------------------------------------------------------- code
def wide(ch) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def text_units(line: str) -> float:
    return sum(1.0 if wide(c) else 0.6 for c in line.expandtabs(4))


def find_line(lines, marker, start=0):
    for i in range(start, len(lines)):
        if marker in lines[i]:
            return i
    raise ValueError(f"marker not found: {marker!r}")


def extract(path: Path, ranges):
    """Return [(line_no or None, text)] for the requested ranges; None marks a gap / elision."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for n, r in enumerate(ranges):
        i = max(find_line(lines, r["start"]) - r["before"], 0)
        j = min(find_line(lines, r["end"], i + r["before"]) + r["after"], len(lines) - 1)
        block = [(k + 1, lines[k]) for k in range(i, j + 1)]
        for es, ee in r["elide"]:
            texts = [t for _, t in block]
            a = find_line(texts, es)
            b = find_line(texts, ee, a)
            indent = re.match(r"\s*", texts[a]).group(0)
            block = block[:a] + [(None, indent + "…")] + block[b + 1:]
        if n:
            out.append((None, ""))
            out.append((None, "⋯"))
            out.append((None, ""))
        out.extend(block)
    # strip the indentation shared by every shown line
    indents = [len(t) - len(t.lstrip(" ")) for no, t in out if no is not None and t.strip()]
    cut = min(indents) if indents else 0
    return [(no, t[cut:] if t[:cut].strip() == "" else t.lstrip(" ")) for no, t in out]


def highlight_lines(entries, lang):
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name

    code = "\n".join(t for _, t in entries)
    try:
        style = "github-dark"
        fmt = HtmlFormatter(nowrap=True, noclasses=True, style=style)
    except Exception:  # older Pygments
        fmt = HtmlFormatter(nowrap=True, noclasses=True, style="monokai")
    lexer = get_lexer_by_name(lang, stripnl=False, ensurenl=False)
    out = highlight(code, lexer, fmt).split("\n")
    out += [""] * (len(entries) - len(out))
    return out[: len(entries)]


def hl_indices(entries, hl):
    if hl is None:
        return None
    texts = [t for _, t in entries]
    picked = set()
    for h in hl:
        if isinstance(h, (tuple, list)):
            a = find_line(texts, h[0])
            b = find_line(texts, h[1], a)
            picked.update(range(a, b + 1))
        else:
            hits = [i for i, t in enumerate(texts) if h in t]
            if not hits:
                raise ValueError(f"highlight marker not found: {h!r}")
            picked.update(hits)
    return picked


def clip(text: str, max_units: float) -> str:
    """Cut a line at max_units display columns, marking the cut with an ellipsis."""
    used = 0.0
    for i, c in enumerate(text):
        used += 1.0 if wide(c) else 0.6
        if used > max_units:
            return text[:i] + "…"
    return text


def code_block(entries, lang, hl, width, height, wrap=False, max_size=28, max_units=64):
    """Code panel sized to fit width x height (px). Unwrapped lines longer than max_units em are clipped."""
    picked = hl_indices(entries, hl)
    if not wrap:
        entries = [(no, clip(t, max_units)) for no, t in entries]
    rendered = highlight_lines(entries, lang)
    units = [text_units(t) for _, t in entries] or [1]
    gutter_em = 4.5
    pad_px = 28 + 40  # right padding + vertical padding budget
    size = max_size
    while size > 12:
        line_h = size * 1.45
        avail_w = width - gutter_em * size - 28
        if wrap:
            rows = sum(max(1, math.ceil(u * size / avail_w)) for u in units)
            fits = rows * line_h <= height - pad_px
        else:
            fits = max(units) * size <= avail_w and len(entries) * line_h <= height - pad_px
        if fits:
            break
        size -= 0.5
    rows = []
    for idx, ((no, text), body) in enumerate(zip(entries, rendered)):
        cls = "ln"
        if no is None:
            cls += " gap"
            body = html.escape(text)
        if picked is not None:
            cls += " on" if idx in picked else " dim"
        rows.append(f"<div class='{cls}'><span class='no'>{no or ''}</span><span class='src'>{body or ' '}</span></div>")
    return (f"<div class='code{' wrap' if wrap else ''}' style='font-size:{size}px;line-height:1.45;"
            f"max-height:{height}px'>{''.join(rows)}</div>")


# ----------------------------------------------------------------------------- renderers
def render_cover(s, st, page, total):
    return frame(
        "<div style='position:absolute;left:150px;top:300px;right:150px'>"
        "<div style='width:110px;height:10px;background:#FF9900;border-radius:5px;margin-bottom:46px'></div>"
        f"<div style='font-size:80px;font-weight:700;line-height:1.2'>{esc(s['title'])}</div>"
        f"<div style='font-size:46px;color:#FFB84D;margin-top:30px;font-weight:700'>{esc(s['subtitle'])}</div>"
        f"<div style='font-size:32px;color:#CBD5E1;margin-top:26px;letter-spacing:6px'>{esc(s['desc'])}</div></div>"
        f"<div style='position:absolute;left:150px;bottom:170px;font-size:26px;color:#94A3B8'>{esc(s['foot'])}</div>",
        dark=True)


def render_section(s, st, page, total):
    return frame(
        f"<div style='position:absolute;left:150px;top:250px;font-size:240px;font-weight:700;color:#FF9900;"
        f"opacity:.9;line-height:1'>{esc(s['num'])}</div>"
        f"<div style='position:absolute;left:150px;top:540px;font-size:84px;font-weight:700'>{esc(s['title'])}</div>"
        f"<div style='position:absolute;left:150px;top:670px;font-size:34px;color:#CBD5E1'>{esc(s['sub'])}</div>",
        dark=True)


def render_agenda(s, st, page, total):
    cards = "".join(
        f"<div class='card' style='padding:52px 40px;min-height:430px'><div style='font-size:100px;font-weight:700;"
        f"color:#FF9900;line-height:1'>{esc(n)}</div><h3 style='margin-top:40px;font-size:46px'>{esc(t)}</h3>"
        f"<p style='font-size:29px'>{esc(d)}</p></div>"
        for n, t, d in s["items"])
    return frame(
        header(s, page, total) + "<div class='body'>"
        f"<div class='grid' style='grid-template-columns:repeat(4,1fr);margin-top:36px'>{cards}</div>"
        f"<div class='note' style='margin-top:44px;font-size:28px'>ⓘ {esc(s['note'])}</div></div>")


def render_bullets(s, st, page, total):
    hl = st["hl"]
    cols = s.get("cols", 2)
    cards = "".join(f"<div class='card{on_cls(i, hl)}'><h3>{esc(t)}</h3><p>{esc(d)}</p></div>"
                    for i, (t, d) in enumerate(s["items"]))
    aside = f"<div class='aside{on_cls('aside', hl)}'>{esc(s['aside'])}</div>" if s.get("aside") else ""
    return frame(header(s, page, total) + "<div class='body'><div class='col-flex'>"
                 f"<div class='grid' style='grid-template-columns:repeat({cols},1fr);flex:1'>{cards}</div>{aside}</div></div>")


def render_columns(s, st, page, total):
    hl = st["hl"]
    cols = []
    for ci, (head, items) in enumerate(s["columns"]):
        rows = "".join(f"<div class='item'><b>{esc(a)}</b><span>{esc(b)}</span></div>" for a, b in items)
        cols.append(f"<div class='card{on_cls(f'c{ci}', hl)}' style='padding:30px 36px'>"
                    f"<h3 style='font-size:36px;color:#D97706;margin-bottom:20px'>{esc(head)}</h3>"
                    f"<div class='items'>{rows}</div></div>")
    return frame(header(s, page, total) + "<div class='body'>"
                 f"<div class='grid' style='grid-template-columns:1fr 1fr;height:100%'>{''.join(cols)}</div></div>")


def table_html(head, rows, hl=None, widths=None, mono_first=False, font=25):
    ths = "".join(f"<th style='width:{widths[i]}%'>{esc(h)}</th>" if widths else f"<th>{esc(h)}</th>"
                  for i, h in enumerate(head))
    trs = []
    for ri, row in enumerate(rows):
        tds = "".join(f"<td class='{'mono' if (mono_first and ci == 0) else ''}'>{esc(c)}</td>" for ci, c in enumerate(row))
        trs.append(f"<tr class='{on_cls(ri, hl).strip()}'>{tds}</tr>")
    return f"<table style='font-size:{font}px'><tr>{ths}</tr>{''.join(trs)}</table>"


def render_table(s, st, page, total):
    hl = st["hl"]
    lead = f"<div class='lead{on_cls('lead', hl)}' style='margin-bottom:24px'>{esc(s['lead'])}</div>" if s.get("lead") else ""
    note = f"<div class='note{on_cls('note', hl)}'>{esc(s['note'])}</div>" if s.get("note") else ""
    rows_hl = None if hl is None else [h for h in hl if isinstance(h, int)]
    if hl is not None and not rows_hl:
        rows_hl = []
    font = 28 if len(s["rows"]) <= 6 else 26
    return frame(header(s, page, total) + "<div class='body'>" + lead
                 + table_html(s["head"], s["rows"], rows_hl, s.get("widths"), s.get("mono_first"), font) + note + "</div>")


def render_stats(s, st, page, total):
    hl = st["hl"]
    label, text = s["scene"]
    scene = f"<div class='lead{on_cls('scene', hl)}'><b>{esc(label)}：</b>{esc(text)}</div>"
    stats = "".join(f"<div class='card' style='text-align:center;padding:22px'><div style='font-size:54px;font-weight:700;"
                    f"color:#D97706'>{esc(v)}</div><div style='font-size:24px;color:#334155;margin-top:6px'>{esc(k)}</div></div>"
                    for v, k in s["stats"])
    stats = f"<div class='grid{on_cls('stats', hl)}' style='grid-template-columns:repeat(4,1fr);margin:24px 0'>{stats}</div>"
    runs_cls = on_cls("runs", hl)
    runs = f"<div class='{runs_cls.strip()}'>" + table_html(s["head"], s["rows"], None, [12, 18, 18, 12, 40], False, 26) + "</div>"
    return frame(header(s, page, total) + "<div class='body'>" + scene + stats + runs + "</div>")


def render_split(s, st, page, total):
    hl = st["hl"]
    lw, rw = s.get("ratio", [44, 56])
    gap = 48
    full = W - 2 * 96 - gap
    left_px, right_px = full * lw / 100, full * rw / 100
    body_h = H - 192 - 150
    items = "".join(
        f"<div class='item{on_cls(f'L{i}', hl) if hl is None or 'L' not in hl else ' on'}'><b>{esc(a)}</b><span>{esc(b)}</span></div>"
        for i, (a, b) in enumerate(s["left"]))
    r = s["right"]
    r_cls = on_cls("R", hl)
    if r["type"] == "code":
        entries = [(None, t) for t in r["text"].splitlines()]
        panel = code_block([(i + 1, t) for i, (_, t) in enumerate(entries)], r.get("lang", "text"), None,
                           right_px, body_h - 40, max_size=28)
    else:
        panel = table_html(r["head"], r["rows"], None, None, False, 26)
    right = (f"<div class='{r_cls.strip()}' style='width:{right_px}px'><div class='panel-label'>{esc(r['label'])}</div>"
             f"{panel}</div>")
    return frame(header(s, page, total) + "<div class='body' style='display:flex;gap:48px'>"
                 f"<div class='items' style='width:{left_px}px'>{items}</div>{right}</div>")


def render_current(s, st, page, total):
    hl = st["hl"] or []
    on = "agent" in hl

    def box(x, y, w, h, text, key=None, fill="#F8FAFC", stroke="#94A3B8", size=28):
        op = "1"
        if on:
            if key == "agent":
                fill, stroke = "#FFF7EB", "#FF9900"
            else:
                op = ".35"
        return (f"<g opacity='{op}'><rect x='{x}' y='{y}' width='{w}' height='{h}' rx='14' fill='{fill}' stroke='{stroke}' "
                f"stroke-width='{4 if (on and key == 'agent') else 2}'/><text x='{x + w / 2}' y='{y + h / 2 + size * 0.36}' "
                f"text-anchor='middle' font-size='{size}' font-weight='700' fill='#0F172A'>{esc(text)}</text></g>")

    right = ["Elasticsearch MCP Server", "Prometheus MCP Server", "Nexus MCP Server",
             "运维编排平台（全域观测与根因分析）", "本地部署的开源大模型"]
    keys = [None, None, None, "agent", "agent"]
    parts = [box(0, 140, 320, 110, "现有告警平台"), box(0, 380, 320, 110, "开发 / 运维提问"),
             box(440, 260, 300, 110, "飞书机器人", fill="#EEF4FF", stroke="#3370FF"),
             box(860, 260, 320, 110, "自建 SRE Agent", key="agent")]
    for i, t in enumerate(right):
        parts.append(box(1290, i * 130, 438, 110, t, key=keys[i], size=24))
    arrows = ["M320,195 L436,300", "M320,435 L436,330", "M740,315 L856,315"]
    arrows += [f"M1180,315 L1286,{i * 130 + 55}" for i in range(5)]
    arrow_svg = "".join(f"<path d='{d}' stroke='#475569' stroke-width='3' fill='none' marker-end='url(#m)'/>" for d in arrows)
    svg = ("<svg width='1728' height='640' viewBox='0 0 1728 640' font-family=\"'Noto Sans CJK SC',sans-serif\">"
           "<defs><marker id='m' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'>"
           "<path d='M0,0L10,5L0,10z' fill='#475569'/></marker></defs>" + arrow_svg + "".join(parts) + "</svg>")
    caption = ("<div class='note on' style='margin-top:16px;font-size:26px'>需要自建与维护：Agent 编排 · 模型推理集群 · 拓扑关联与根因分析</div>"
               if on else "")
    return frame(header(s, page, total) + f"<div class='body'>{svg}{caption}</div>")


def zoom_view(rects, crop, aspect, max_zoom=1.8, area_w=W - 2 * 96, pad=50):
    """A viewBox around the highlighted rects, matched to the screen aspect, at most max_zoom x."""
    cx, cy, cw, ch = crop
    x0 = min(x for x, _, _, _ in rects) - pad
    y0 = min(y for _, y, _, _ in rects) - pad
    x1 = max(x + w for x, _, w, _ in rects) + pad
    y1 = max(y + h for _, y, _, h in rects) + pad
    bw, bh = x1 - x0, y1 - y0
    w = min(cw, max(bw, bh * aspect, area_w / max_zoom))
    h = min(ch, max(w / aspect, bh))
    w = min(cw, max(w, h * aspect)) if h * aspect <= cw else cw
    x = min(max((x0 + x1) / 2 - w / 2, cx), cx + cw - w)
    y = min(max((y0 + y1) / 2 - h / 2, cy), cy + ch - h)
    return x, y, w, h


def render_diagram(s, st, page, total):
    src = (DIAGRAMS / s["svg"]).read_text(encoding="utf-8")
    crop = DIAGRAM_CROP[s["svg"]]
    max_w, max_h = W - 2 * 96, H - 128 - 140
    rects = st.get("rects") or []
    if st.get("view"):
        view = st["view"]
    elif rects and s.get("zoom"):
        view = zoom_view(rects, crop, max_w / max_h)
    else:
        view = crop
    vx, vy, vw, vh = view
    scale = min(max_w / vw, max_h / vh)
    w, h = round(vw * scale), round(vh * scale)
    src = re.sub(r'width="\d+" height="\d+" viewBox="[^"]+"',
                 f'width="{w}" height="{h}" viewBox="{vx:.1f} {vy:.1f} {vw:.1f} {vh:.1f}"', src, 1)
    if rects:
        pad = 6
        cx, cy, cw, ch = crop
        d = f"M{cx - 20},{cy - 20} H{cx + cw + 20} V{cy + ch + 20} H{cx - 20} Z"
        for x, y, rw, rh in rects:
            d += f" M{x - pad},{y - pad} H{x + rw + pad} V{y + rh + pad} H{x - pad} Z"
        overlay = f"<path d='{d}' fill='#FFFFFF' fill-opacity='.68' fill-rule='evenodd'/>"
        overlay += "".join(f"<rect x='{x - pad}' y='{y - pad}' width='{rw + 2 * pad}' height='{rh + 2 * pad}' rx='12' "
                           f"fill='none' stroke='#FF9900' stroke-width='{4 / scale:.2f}'/>" for x, y, rw, rh in rects)
        src = src.replace("</svg>", overlay + "</svg>")
    n = len(s["steps"])
    idx = s["steps"].index(st) + 1
    chip = f"<div class='chip' style='top:56px'>{esc(st.get('label') or '')}  ·  {idx}/{n}</div>" if n > 1 else ""
    left = (W - w) // 2
    return frame(header(s, page, total, compact=True) + chip
                 + f"<div style='position:absolute;left:{left}px;top:128px;width:{w}px;height:{h}px;border-radius:12px;"
                   f"box-shadow:0 0 0 2px #E2E8F0'>{src}</div>")


def render_code(s, st, page, total):
    if s.get("file"):
        entries = extract(ROOT / s["file"], s["ranges"])
        label = s["file"]
    else:
        entries = [(i + 1, t) for i, t in enumerate(s["text"].splitlines())]
        label = s.get("label", "")
    lang = s.get("lang", "python")
    body_h = H - 192 - 150 - 44
    block = code_block(entries, lang, st["hl"], W - 2 * 96, body_h, wrap=s.get("wrap", False))
    return frame(header(s, page, total) + f"<div class='body'><div class='panel-label'>▸ {esc(label)}</div>{block}</div>")


def render_flow(s, st, page, total):
    """Process steps as two rows of cards (row 2 continues row 1), then a timing strip and a note."""
    hl = st["hl"]
    nodes = s["nodes"]
    per_row = math.ceil(len(nodes) / 2)
    gap, link_h = 44, 44
    card_w = (W - 2 * 96 - gap * (per_row - 1)) / per_row
    arrow = (f"<div class='farr' style='width:{gap}px'><svg width='{gap}' height='24'>"
             "<path d='M4,12 H32' stroke='#94A3B8' stroke-width='3'/><path d='M30,5 L41,12 L30,19z' fill='#94A3B8'/></svg></div>")

    def row(items, offset):
        cards = []
        for i, (num, title, who, detail) in enumerate(items, offset):
            cards.append(f"<div class='fcard{on_cls(i, hl)}' style='width:{card_w:.1f}px'>"
                         f"<div class='fhead'><span class='fnum'>{esc(num)}</span><b>{esc(title)}</b></div>"
                         f"<div class='fwho'>{esc(who)}</div><div class='fdet'>{esc(detail)}</div></div>")
        return f"<div class='frow'>{arrow.join(cards)}</div>"

    # elbow from the last card of row 1 down to the first card of row 2
    x_end, x_start = (per_row - 1) * (card_w + gap) + card_w / 2, card_w / 2
    link = (f"<svg width='{W - 2 * 96}' height='{link_h}' style='display:block'>"
            f"<path d='M{x_end:.1f},0 V{link_h / 2} H{x_start:.1f} V{link_h - 10}' stroke='#94A3B8' stroke-width='3' fill='none'/>"
            f"<path d='M{x_start - 7:.1f},{link_h - 12} L{x_start:.1f},{link_h} L{x_start + 7:.1f},{link_h - 12}z' fill='#94A3B8'/></svg>")
    segs = "".join(f"<div class='seg'>{esc(k)}<b>{esc(v)}</b></div>" for k, v in s["timing"])
    timing = f"<div class='ftime{on_cls('time', hl)}'><div class='tl'>{esc(s['timing_label'])}</div>{segs}</div>"
    note = f"<div class='note{on_cls('note', hl)}' style='margin-top:20px;font-size:25px;line-height:1.5'>{esc(s['note'])}</div>"
    return frame(header(s, page, total) + "<div class='body'>"
                 + row(nodes[:per_row], 0) + link + row(nodes[per_row:], per_row) + timing + note + "</div>")


def render_end(s, st, page, total):
    points = "".join(f"<div style='font-size:36px;margin:0 0 26px;display:flex;gap:22px'><span style='color:#FF9900'>✓</span>"
                     f"<span>{esc(p)}</span></div>" for p in s["points"])
    links = "".join(f"<div style='font-size:28px;margin-bottom:14px'><span style='color:#94A3B8;display:inline-block;"
                    f"width:200px'>{esc(a)}</span>{esc(b)}</div>" for a, b in s["links"])
    return frame(
        "<div style='position:absolute;left:150px;top:150px;right:150px'>"
        f"<div style='font-size:72px;font-weight:700;margin-bottom:56px'>{esc(s['title'])}</div>{points}"
        f"<div style='margin-top:60px;border-top:2px solid #334155;padding-top:40px'>{links}</div></div>",
        dark=True)


RENDER = {"cover": render_cover, "section": render_section, "agenda": render_agenda, "bullets": render_bullets,
          "columns": render_columns, "table": render_table, "stats": render_stats, "split": render_split,
          "current": render_current, "diagram": render_diagram, "code": render_code, "flow": render_flow,
          "end": render_end}


# ----------------------------------------------------------------------------- browser
def browser_env():
    env = dict(os.environ)
    if BROWSER_LIBS.exists():
        lib = str(BROWSER_LIBS / "root" / "usr" / "lib64")
        env["LD_LIBRARY_PATH"] = lib + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        env["FONTCONFIG_FILE"] = str(BROWSER_LIBS / "fonts.conf")
    return env


def pw(*args, env):
    res = subprocess.run(["playwright-cli", *args], capture_output=True, text=True, env=env)
    return res.returncode, res.stdout + res.stderr


def screenshot_all(pages, frames_dir: Path):
    env = browser_env()
    out_dir = Path(env.get("PLAYWRIGHT_MCP_OUTPUT_DIR") or ".")
    code, out = pw("goto", "about:blank", env=env)
    if code or "is not open" in out:
        pw("open", "about:blank", env=env)
    pw("resize", str(W), str(H), env=env)
    for name, doc in pages:
        target = frames_dir / f"{name}.png"
        digest = hashlib.sha1(doc.encode("utf-8")).hexdigest()
        stamp = frames_dir / f"{name}.sha1"
        if target.exists() and stamp.exists() and stamp.read_text() == digest:
            continue
        url = "data:text/html;charset=utf-8;base64," + base64.b64encode(doc.encode("utf-8")).decode("ascii")
        if len(url) > 120_000:
            raise SystemExit(f"{name}: page too large for a data: URL ({len(url)} bytes)")
        code, out = pw("goto", url, env=env)
        if code:
            raise SystemExit(f"{name}: goto failed: {out[-400:]}")
        code, out = pw("screenshot", env=env)
        m = re.search(r"page-[0-9TZ:.-]+\.png", out)
        if code or not m:
            raise SystemExit(f"{name}: screenshot failed: {out[-400:]}")
        shutil.move(str(out_dir / m.group(0)), target)
        with open(target, "rb") as f:
            f.seek(16)
            pw_w, pw_h = struct.unpack(">II", f.read(8))
        if (pw_w, pw_h) != (W, H):
            raise SystemExit(f"{name}: screenshot is {pw_w}x{pw_h}, expected {W}x{H}")
        stamp.write_text(digest)
        print(f"  frame {name}")


# ----------------------------------------------------------------------------- speech
def sentences(text: str):
    parts = re.findall(r"[^。！？；]+[。！？；]?", text)
    return [p.strip() for p in parts if p.strip()]


def tts_text(text: str) -> str:
    out = text
    for a, b in TTS_REPLACE:
        if a.isascii() and a[0].isalnum():
            out = re.sub(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", b, out)
        else:
            out = out.replace(a, b)
    out = out.replace("_", " ")
    return out


def ffmpeg_exe():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def synth(text, cache: Path, args):
    spoken = tts_text(text)
    ssml = f"<speak><prosody rate='{args.rate}'>{html.escape(spoken, quote=False)}</prosody></speak>"
    key = hashlib.sha1(f"{args.voice}|neural|{ssml}".encode("utf-8")).hexdigest()
    pcm = cache / f"{key}.pcm"
    if pcm.exists():
        return pcm.read_bytes()
    mp3 = cache / f"{key}.mp3"
    cmd = ["aws", "polly", "synthesize-speech", "--engine", "neural", "--voice-id", args.voice,
           "--output-format", "mp3", "--sample-rate", str(SAMPLE_RATE), "--text-type", "ssml", "--text", ssml,
           "--region", args.region, str(mp3)]
    if args.profile:
        cmd[1:1] = ["--profile", args.profile]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(f"polly failed for {text[:30]}…: {res.stderr.strip()[-300:]}")
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1",
                    "-ar", str(SAMPLE_RATE), str(pcm)], check=True)
    mp3.unlink()
    return pcm.read_bytes()


def silence(seconds):
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


def cue_chunks(sentence: str, limit=22):
    text = sentence.rstrip("。；！？")
    pieces = re.findall(r"[^，、：,]+[，、：,]?", text) or [text]
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + len(p) > limit:
            chunks.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        chunks.append(cur)
    return [c.rstrip("，、：,") for c in chunks]


def fmt_srt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def fmt_ass(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK SC,42,&H00FFFFFF,&H00FFFFFF,&H5A101010,&H5A101010,0,0,0,0,100,100,0,0,3,14,0,2,80,80,44,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--region", default=os.environ.get("POLLY_REGION", "us-east-1"))
    ap.add_argument("--voice", default="Zhiyu")
    ap.add_argument("--rate", default="100%", help="Polly prosody rate, e.g. 105%%")
    ap.add_argument("--only", type=int, help="render only this slide number (1-based); implies --frames-only")
    ap.add_argument("--frames-only", action="store_true")
    ap.add_argument("--no-burn", action="store_true", help="do not burn subtitles into the video")
    args = ap.parse_args()

    slides = load_slides()
    total = len(slides)
    html_dir, frames_dir, tts_dir = BUILD / "html", BUILD / "frames", BUILD / "tts"
    for d in (html_dir, frames_dir, tts_dir, DIST):
        d.mkdir(parents=True, exist_ok=True)

    pages, segments = [], []
    for si, s in enumerate(slides, 1):
        for ti, st in enumerate(s["steps"], 1):
            name = f"{si:02d}-{ti}"
            if args.only and si != args.only:
                continue
            doc = RENDER[s["kind"]](s, st, si, total)
            (html_dir / f"{name}.html").write_text(doc, encoding="utf-8")
            pages.append((name, doc))
            segments.append((name, st["say"], s["kind"]))
    print(f"{total} slides, {len(pages)} frames")
    screenshot_all(pages, frames_dir)
    if args.only or args.frames_only:
        print(f"frames in {frames_dir}")
        return

    all_sentences = sorted({x for _, say, _ in segments for x in sentences(say)})
    print(f"synthesising {len(all_sentences)} sentences ({sum(len(x) for x in all_sentences)} characters)")
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        audio = dict(zip(all_sentences, pool.map(lambda x: synth(x, tts_dir, args), all_sentences)))

    pcm, t = bytearray(), 0.0
    frames_list, srt, ass = [], [], []
    cue_no = 0
    for k, (name, say, kind) in enumerate(segments):
        start = t
        lead, gap, tail = (0.8 if k == 0 else 0.35), 0.28, (1.0 if kind in ("section", "cover", "end") else 0.7)
        pcm += silence(lead)
        t += lead
        for sent in sentences(say):
            data = audio[sent]
            dur = len(data) / 2 / SAMPLE_RATE
            chunks = cue_chunks(sent)
            weights = [max(len(c), 1) for c in chunks]
            ct = t
            for c, wgt in zip(chunks, weights):
                cd = dur * wgt / sum(weights)
                cue_no += 1
                srt.append(f"{cue_no}\n{fmt_srt(ct)} --> {fmt_srt(ct + cd)}\n{c}\n")
                ass.append(f"Dialogue: 0,{fmt_ass(ct)},{fmt_ass(ct + cd)},Default,,0,0,0,,{c}")
                ct += cd
            pcm += data
            t += dur
            pcm += silence(gap)
            t += gap
        pcm += silence(tail)
        t += tail
        frames_list.append((frames_dir / f"{name}.png", t - start))

    wav = BUILD / "narration.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(pcm))
    (DIST / f"{OUT_NAME}.srt").write_text("\n".join(srt), encoding="utf-8")
    ass_path = BUILD / "subtitles.ass"
    ass_path.write_text(ASS_HEAD + "\n".join(ass) + "\n", encoding="utf-8")

    concat = BUILD / "frames.txt"
    lines = ["ffconcat version 1.0"]
    for path, dur in frames_list:
        lines += [f"file '{path}'", f"duration {dur:.3f}"]
    lines.append(f"file '{frames_list[-1][0]}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

    vf = f"fps={FPS},format=yuv420p"
    env = browser_env()
    if not args.no_burn:
        vf += f",ass={ass_path}" + (f":fontsdir={CJK_FONT_DIR}" if CJK_FONT_DIR.exists() else "")
    out = DIST / f"{OUT_NAME}.mp4"
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-stats", "-f", "concat", "-safe", "0", "-i", str(concat),
           "-i", str(wav), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out)]
    print(f"encoding {t / 60:.1f} minutes -> {out}")
    subprocess.run(cmd, check=True, env=env)
    print(f"done: {out} ({out.stat().st_size / 1e6:.1f} MB, {t / 60:.1f} min), subtitles: {DIST / (OUT_NAME + '.srt')}")


if __name__ == "__main__":
    sys.exit(main())
