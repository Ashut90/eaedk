#!/usr/bin/env python3
"""Render EAEDK-Technical-Documentation.md -> a paginated, print-ready HTML (for PDF).

Markdown -> HTML via the stdlib-friendly `markdown` package, with a GitHub-style slugify so the
in-document table of contents links resolve. Styling targets a ~50-page A4 document: a title
page, a contents page, each chapter starting on a fresh page, and readable code/tables.
Chrome (headless) turns the HTML into the PDF; see the sibling build command.
"""
import re
import markdown

SRC = "docs/EAEDK-Technical-Documentation.md"
OUT_HTML = "docs/EAEDK-Technical-Documentation.html"


def github_slugify(value, separator="-"):
    """Mimic GitHub's heading-anchor algorithm so the hand-written TOC links match."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value)        # drop punctuation (em dash, parens, etc.)
    value = re.sub(r"\s+", separator, value)       # spaces -> hyphens (keeps doubled hyphens)
    return value


md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "attr_list"],
                       extension_configs={"toc": {"slugify": github_slugify}})

with open(SRC, encoding="utf-8") as f:
    text = f.read()

# The first H1 becomes the title page; render the rest normally.
body = md.convert(text)

CSS = """
@page { size: A4; margin: 22mm 21mm 24mm 21mm; }
@page :first { margin: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: "Georgia", "Times New Roman", serif;
  font-size: 13pt; line-height: 1.85; color: #1a1a1a; margin: 0;
}
/* Title page */
.titlepage {
  height: 297mm; display: flex; flex-direction: column; justify-content: center;
  align-items: center; text-align: center; background: #0d2137; color: #fff;
  padding: 0 30mm; page-break-after: always;
}
.titlepage h1 { font-size: 34pt; line-height: 1.15; border: 0; color: #fff; margin: 0 0 10mm; }
.titlepage .sub { font-size: 15pt; color: #9ec5e8; margin-bottom: 30mm; font-style: italic; }
.titlepage .meta { font-size: 11pt; color: #c7d6e5; line-height: 1.8; }
.titlepage .rule { width: 60mm; border-top: 2px solid #4a90d9; margin: 8mm 0; }
/* Headings */
h1 { font-size: 23pt; color: #0d2137; border-bottom: 3px solid #0d2137;
     padding-bottom: 3mm; margin: 0 0 7mm; page-break-before: always; page-break-after: avoid; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 16.5pt; color: #14507a; margin: 10mm 0 3.5mm; page-break-after: avoid;
     border-bottom: 1px solid #cdd9e3; padding-bottom: 1.5mm; }
h3 { font-size: 13.5pt; color: #1a3a52; margin: 7mm 0 2.5mm; page-break-after: avoid; }
p { margin: 0 0 4mm; text-align: justify; }
strong { color: #0d2137; }
a { color: #14507a; text-decoration: none; }
ul, ol { margin: 0 0 3.5mm; padding-left: 7mm; }
li { margin-bottom: 1.6mm; }
blockquote {
  margin: 4mm 0; padding: 2.5mm 5mm; border-left: 4px solid #4a90d9;
  background: #eef4fa; color: #1a3a52; font-style: italic;
}
blockquote p { margin: 0; }
code {
  font-family: "DejaVu Sans Mono", "Consolas", monospace; font-size: 9.5pt;
  background: #eef1f4; padding: 0.5mm 1.5mm; border-radius: 2px; color: #b1144b;
}
pre {
  background: #f6f8fa; border: 1px solid #d6dde4; border-radius: 4px;
  padding: 3mm 4mm; overflow-x: hidden; page-break-inside: avoid; margin: 0 0 4mm;
}
pre code { background: none; color: #24292e; padding: 0; font-size: 9pt;
           line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
table {
  border-collapse: collapse; width: 100%; margin: 0 0 5mm; font-size: 9.8pt;
  page-break-inside: avoid;
}
th, td { border: 1px solid #c2ccd6; padding: 1.8mm 2.5mm; text-align: left; vertical-align: top; }
th { background: #14507a; color: #fff; font-family: "Helvetica", "Arial", sans-serif; }
tr:nth-child(even) td { background: #f1f5f9; }
hr { border: 0; border-top: 1px solid #cdd9e3; margin: 7mm 0; }
/* The hand-written TOC list (first list after the contents heading) */
.toc-page { page-break-after: always; }
.toc-page h1 { page-break-before: avoid; }
"""

# Pull the document title (first H1) out of the body to build the title page.
m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.DOTALL)
title_html = m.group(1) if m else "EAEDK — Technical Documentation"
# remove that first h1 + the immediate subtitle paragraph block from the flowing body
body = body[m.end():] if m else body

title_page = f"""
<section class="titlepage">
  <div class="sub">Embedded AI Engineering Development Kit</div>
  <h1>Technical Documentation</h1>
  <div class="rule"></div>
  <div class="meta">
    The master reference — architecture, every design decision, and how to use every feature.<br>
    Audience: firmware engineers (beginner to mid-level), technical writers, contributors.<br><br>
    Reason first. Build second. Verify always.
  </div>
</section>
"""

html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{title_html}</title><style>{CSS}</style></head>
<body>{title_page}<main>{body}</main></body></html>"""

with open(OUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)
print(f"wrote {OUT_HTML} ({len(html)} bytes)")
