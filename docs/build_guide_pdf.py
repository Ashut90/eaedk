#!/usr/bin/env python3
"""Render EAEDK-Complete-Guide.md -> a polished, paginated PDF via WeasyPrint.

WeasyPrint (not Chrome) because it implements CSS Paged Media: a contents page with real
page numbers and dot leaders (target-counter + leader), running headers/footers in @page
margin boxes, and named pages so the cover and back cover carry no running furniture.

Heading convention in the Markdown:
  #  Part   -> part divider (new page, big)
  ## Chapter-> chapter (new page); the main contents entry
  ### Section-> section; indented contents entry
"""
import re
import html as _html
import markdown
from weasyprint import HTML

SRC = "docs/EAEDK-Complete-Guide.md"
OUT = "docs/EAEDK-Complete-Guide.pdf"

md = markdown.Markdown(extensions=["tables", "fenced_code"])
with open(SRC, encoding="utf-8") as f:
    body = md.convert(f.read())


def slug(text):
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"\s+", "-", s)


# Assign ids to h1/h2/h3 and collect the table-of-contents entries.
toc = []
seen = {}


def tag(m):
    level, attrs, inner = m.group(1), m.group(2), m.group(3)
    base = slug(inner)
    n = seen.get(base, 0)
    seen[base] = n + 1
    hid = base if n == 0 else f"{base}-{n}"
    cls = {"1": "toc-part", "2": "toc-chapter", "3": "toc-section"}[level]
    toc.append((cls, hid, re.sub(r"<[^>]+>", "", inner)))
    return f'<h{level} id="{hid}"{attrs}>{inner}</h{level}>'


body = re.sub(r"<h([123])([^>]*)>(.*?)</h\1>", tag, body, flags=re.DOTALL)

toc_html = ['<section class="toc"><h1 class="toc-title">Contents</h1>']
for cls, hid, text in toc:
    toc_html.append(f'<a class="{cls}" href="#{hid}">'
                    f'<span class="t">{_html.escape(text)}</span></a>')
toc_html.append("</section>")
toc_html = "\n".join(toc_html)

COVER = """
<section class="cover">
  <div class="brand">EAEDK</div>
  <div class="full">Embedded AI Engineering Development Kit</div>
  <div class="rule"></div>
  <div class="title">The Complete Technical Guide</div>
  <div class="tag">From a board, or a datasheet, to a validated project —
       without an AI ever inventing a hardware fact.</div>
  <div class="meta">
    June 2026 Edition &nbsp;•&nbsp; github.com/Ashut90/eaedk<br>
    The LLM may explain. The engines hold the truth.
  </div>
</section>
"""

BACK = """
<section class="backcover">
  <div class="bbrand">EAEDK</div>
  <div class="bline">The LLM may explain. The engines hold the truth.</div>
  <div class="bmeta">github.com/Ashut90/eaedk &nbsp;•&nbsp; June 2026 Edition<br>
       Reason first. Build second. Verify always.</div>
</section>
"""

CSS = """
@page {
  size: A4; margin: 24mm 20mm 22mm 20mm;
  @top-right  { content: "EAEDK — The Complete Technical Guide";
                font: 8pt "Helvetica", sans-serif; color: #8aa0b4; }
  @top-left   { content: string(chapter); font: 8pt "Helvetica", sans-serif; color: #8aa0b4; }
  @bottom-center { content: counter(page) " / " counter(pages);
                   font: 8.5pt "Helvetica", sans-serif; color: #8aa0b4; }
  @bottom-left { content: ""; }
}
@page cover { margin: 0; @top-right{content:""} @top-left{content:""} @bottom-center{content:""} }
@page back  { margin: 0; @top-right{content:""} @top-left{content:""} @bottom-center{content:""} }
@page toc   { @top-left{content:""} }

html { -weasy-hyphens: auto; }
body { font-family: "Georgia","Times New Roman",serif; font-size: 11pt; line-height: 1.55;
       color: #1b1b1b; }

/* cover */
.cover { page: cover; height: 297mm; background: #0d2137; color: #fff;
         padding: 70mm 28mm 0; }
.cover .brand { font: 700 56pt "Helvetica",sans-serif; letter-spacing: 2px; }
.cover .full  { font: 15pt "Helvetica",sans-serif; color: #9ec5e8; margin-top: 4mm; letter-spacing:1px; }
.cover .rule  { width: 70mm; border-top: 2px solid #4a90d9; margin: 12mm 0 8mm; }
.cover .title { font: 700 26pt "Helvetica",sans-serif; color: #eaf2fb; }
.cover .tag   { font: italic 13pt "Georgia",serif; color: #c7d6e5; margin-top: 8mm; max-width: 120mm; }
.cover .meta  { position: absolute; bottom: 32mm; font: 10pt "Helvetica",sans-serif;
                color: #9ec5e8; line-height: 1.9; }

/* back cover */
.backcover { page: back; height: 297mm; background: #0d2137; color: #fff;
             display: flex; flex-direction: column; justify-content: center;
             align-items: center; text-align: center; }
.backcover .bbrand { font: 700 40pt "Helvetica",sans-serif; letter-spacing: 2px; }
.backcover .bline  { font: 13pt "Georgia",serif; color: #c7d6e5; margin-top: 6mm; }
.backcover .bmeta  { font: 9.5pt "Helvetica",sans-serif; color: #8aa6c4; margin-top: 26mm; line-height: 1.9; }

/* contents */
.toc { page: toc; page-break-before: always; }
.toc-title { font: 700 22pt "Helvetica",sans-serif; color: #0d2137 !important;
             background: transparent !important; margin: 0 0 8mm !important;
             padding: 0 0 3mm !important; border-bottom: 3px solid #0d2137;
             page-break-before: avoid !important; }
.toc a { display: block; text-decoration: none; color: #1b1b1b; }
.toc a .t { }
.toc a::after { content: leader(". ") target-counter(attr(href), page);
               font: 9.5pt "Helvetica",sans-serif; color: #5b7governi; }
.toc a::after { color: #4a6a86; }
.toc-part    { font: 700 12pt "Helvetica",sans-serif; color: #0d2137; margin: 6mm 0 2mm; }
.toc-chapter { font: 10.5pt "Helvetica",sans-serif; color: #14507a; margin: 1.6mm 0 0 6mm; }
.toc-section { font: 9.5pt "Helvetica",sans-serif; color: #44617a; margin: 0.6mm 0 0 14mm; }

/* headings */
h1 { string-set: chapter ""; font: 700 22pt "Helvetica",sans-serif; color: #102a44;
     page-break-before: always; page-break-after: avoid;
     background: #0d2137; color: #fff; padding: 10mm 8mm; margin: 0 -20mm 8mm; }
h2 { string-set: chapter content(); font: 700 16pt "Helvetica",sans-serif; color: #102a44;
     page-break-before: always; page-break-after: avoid;
     border-bottom: 2px solid #0d2137; padding-bottom: 2.5mm; margin: 0 0 5mm; }
h2:first-of-type { }
h3 { font: 700 12.5pt "Helvetica",sans-serif; color: #14507a; margin: 7mm 0 2mm;
     page-break-after: avoid; }
p { margin: 0 0 3mm; text-align: justify; }
strong { color: #0d2137; }
ul,ol { margin: 0 0 3.5mm; padding-left: 7mm; }
li { margin-bottom: 1.5mm; }
blockquote { margin: 4mm 0; padding: 2.5mm 5mm; border-left: 4px solid #4a90d9;
             background: #eef4fa; color: #1a3a52; font-style: italic; }
blockquote p { margin: 0; }
code { font-family: "DejaVu Sans Mono",monospace; font-size: 9pt; background: #eef1f4;
       padding: 0.4mm 1.2mm; border-radius: 2px; color: #b1144b; }
pre { background: #f6f8fa; border: 1px solid #d6dde4; border-radius: 4px; padding: 3mm 4mm;
      page-break-inside: avoid; margin: 0 0 4mm; }
pre code { background: none; color: #24292e; padding: 0; font-size: 8.4pt; line-height: 1.45;
           white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 0 0 5mm; font-size: 9pt;
        page-break-inside: avoid; }
th,td { border: 1px solid #c2ccd6; padding: 1.5mm 2.2mm; text-align: left; vertical-align: top; }
th { background: #14507a; color: #fff; font-family: "Helvetica",sans-serif; }
tr:nth-child(even) td { background: #f1f5f9; }
hr { border: 0; border-top: 1px solid #cdd9e3; margin: 7mm 0; }
"""
# repair an accidental typo token in the leader color (keep CSS valid)
CSS = CSS.replace("#5b7governi", "#4a6a86")

doc = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<style>{CSS}</style></head><body>{COVER}{toc_html}<main>{body}</main>{BACK}</body></html>"""

with open("docs/EAEDK-Complete-Guide.html", "w", encoding="utf-8") as f:
    f.write(doc)

HTML(string=doc, base_url="docs/").write_pdf(OUT)
print(f"wrote {OUT}")
