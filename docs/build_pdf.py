#!/usr/bin/env python3
"""Build EAEDK PDFs from markdown.

For each .md file in docs/ that starts with 'EAEDK-', this script:
  1. Converts markdown → HTML using the `markdown` package
  2. Inlines all referenced images as base64 data URIs
  3. Applies a professional A4-ready CSS stylesheet
  4. Saves the .html alongside the .md
  5. Uses puppeteer (via Node.js + mmdc's bundled puppeteer-core) to render PDF
     with no browser headers/footers and printBackground enabled

Run:  python3 docs/build_pdf.py
"""

import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).parent

# -- CSS for professional A4 PDF layout -----------------------------------------
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=Source+Code+Pro:wght@400;600&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --body-font: 'Source Sans 3', 'Segoe UI', Arial, sans-serif;
  --mono-font: 'Source Code Pro', 'Courier New', monospace;
  --text:   #1a1a1a;
  --muted:  #555;
  --code-bg:#f4f4f4;
  --rule:   #d0d0d0;
  --link:   #1a56db;
  --accent: #0f4c82;
}

html { font-size: 13pt; }

body {
  font-family: var(--body-font);
  color: var(--text);
  line-height: 1.65;
  background: #fff;
  padding: 0;
  margin: 0;
}

/* ---- Page layout ---- */
@page {
  size: A4;
  margin: 24mm 20mm 26mm 22mm;
}

@media print {
  body { margin: 0; padding: 0; }
  h1, h2 { page-break-before: always; }
  h1:first-of-type { page-break-before: avoid; }
  h3, h4, h5 { page-break-after: avoid; }
  pre, table, figure, img { page-break-inside: avoid; }
  .no-break { page-break-inside: avoid; }
  a { color: inherit; text-decoration: none; }
}

/* --- Screen wrapper (non-print only) --- */
@media screen {
  body {
    max-width: 780px;
    margin: 32px auto;
    padding: 20px 40px;
  }
}

/* ---- Headings ---- */
h1 {
  font-size: 2em;
  font-weight: 700;
  color: var(--accent);
  border-bottom: 3px solid var(--accent);
  padding-bottom: 6px;
  margin-top: 2em;
  margin-bottom: 0.6em;
}

h2 {
  font-size: 1.45em;
  font-weight: 700;
  color: #1a1a2e;
  border-bottom: 1.5px solid var(--rule);
  padding-bottom: 4px;
  margin-top: 1.8em;
  margin-bottom: 0.5em;
}

h3 {
  font-size: 1.15em;
  font-weight: 700;
  margin-top: 1.4em;
  margin-bottom: 0.3em;
}

h4, h5, h6 {
  font-size: 1em;
  font-weight: 600;
  margin-top: 1.2em;
  margin-bottom: 0.25em;
}

/* ---- Body text ---- */
p { margin: 0.6em 0; }

strong { font-weight: 600; }

em { font-style: italic; }

a { color: var(--link); }

hr {
  border: none;
  border-top: 1px solid var(--rule);
  margin: 2em 0;
}

/* ---- Code ---- */
code {
  font-family: var(--mono-font);
  font-size: 0.87em;
  background: var(--code-bg);
  border-radius: 3px;
  padding: 1px 4px;
}

pre {
  font-family: var(--mono-font);
  font-size: 0.78em;
  background: var(--code-bg);
  border: 1px solid #ddd;
  border-left: 4px solid var(--accent);
  border-radius: 4px;
  padding: 14px 16px;
  overflow-x: auto;
  white-space: pre;
  line-height: 1.45;
  margin: 1em 0;
}

pre code {
  background: none;
  padding: 0;
  border-radius: 0;
  font-size: inherit;
}

/* ---- Blockquotes ---- */
blockquote {
  border-left: 4px solid var(--accent);
  padding: 8px 16px;
  background: #f0f4ff;
  border-radius: 0 4px 4px 0;
  margin: 1em 0;
  font-style: normal;
  color: #2a2a4a;
}

blockquote p { margin: 0.25em 0; }

/* ---- Tables ---- */
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88em;
  margin: 1.2em 0;
}

th {
  background: var(--accent);
  color: #fff;
  font-weight: 600;
  text-align: left;
  padding: 8px 12px;
}

td {
  padding: 7px 12px;
  border-bottom: 1px solid var(--rule);
  vertical-align: top;
}

tr:nth-child(even) td { background: #f8f8fc; }

/* ---- Lists ---- */
ul, ol {
  margin: 0.5em 0 0.5em 1.6em;
  padding: 0;
}

li { margin: 0.25em 0; }

li > ul, li > ol { margin-top: 0.15em; }

/* ---- Images ---- */
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1.5em auto;
  border: 1px solid var(--rule);
  border-radius: 4px;
}

/* ---- Title page ---- */
.title-page {
  text-align: center;
  padding: 60px 0 40px;
  page-break-after: always;
}
.title-page h1 {
  border: none;
  font-size: 2.4em;
  color: var(--accent);
  margin-bottom: 0.3em;
}
.title-page .subtitle {
  font-size: 1.1em;
  color: var(--muted);
  margin-bottom: 2em;
}
.title-page .meta {
  font-size: 0.9em;
  color: var(--muted);
}

/* ---- TOC ---- */
.toc { margin: 2em 0; padding: 1.5em; background: #f8f8fc; border-radius: 6px; }
.toc ul { margin-left: 1em; }
.toc li { margin: 0.2em 0; }

/* ---- Admonitions / callout boxes ---- */
.admonition { border-left: 4px solid #888; padding: 8px 16px; margin: 1em 0; background: #fafafa; }
.admonition.note { border-color: var(--accent); background: #f0f4ff; }
.admonition.warning { border-color: #e07000; background: #fff7f0; }
"""

# -- Node.js puppeteer script ---------------------------------------------------
PUPPETEER_SCRIPT = r"""
const puppeteer = require('/usr/local/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer-core');
const fs = require('fs');

(async () => {
  const [,, htmlFile, pdfFile] = process.argv;
  const htmlContent = fs.readFileSync(htmlFile, 'utf8');

  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-web-security',
    ],
    headless: true,
  });

  const page = await browser.newPage();
  // Use setContent so that base64 images always load (no file:// restrictions)
  await page.setContent(htmlContent, { waitUntil: 'networkidle0' });

  await page.pdf({
    path: pdfFile,
    format: 'A4',
    margin: { top: '24mm', right: '20mm', bottom: '26mm', left: '22mm' },
    displayHeaderFooter: false,
    printBackground: true,
    preferCSSPageSize: false,
  });

  await browser.close();
  console.log('PDF written to', pdfFile);
})().catch(e => { console.error(e); process.exit(1); });
"""


def embed_images(html: str, base_dir: Path) -> str:
    """Replace all <img src="relative/path"> with base64 data URIs."""

    def replace(m: re.Match) -> str:
        before, src, after = m.group(1), m.group(2), m.group(3)
        # Skip already-embedded data URIs
        if src.startswith("data:"):
            return m.group(0)
        img_path = base_dir / src
        if img_path.exists():
            ext = img_path.suffix.lstrip(".").lower()
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                    "gif": "gif", "svg": "svg+xml", "webp": "webp"}.get(ext, ext)
            b64 = base64.b64encode(img_path.read_bytes()).decode()
            return f'<img{before}src="data:image/{mime};base64,{b64}"{after}'
        print(f"  WARNING: image not found: {img_path}", file=sys.stderr)
        return m.group(0)

    return re.sub(r'<img([^>]*?\s)src="([^"]+)"([^>]*?)>', replace, html)


def build_html(md_path: Path) -> Path:
    """Convert a markdown file to a self-contained HTML with embedded images."""
    import markdown as md_lib

    md_text = md_path.read_text(encoding="utf-8")

    # Convert
    html_body = md_lib.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "attr_list"],
        output_format="html",
    )

    # Embed images
    html_body = embed_images(html_body, md_path.parent)

    title = md_path.stem.replace("-", " ")
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
{CSS}
  </style>
</head>
<body>
{html_body}
</body>
</html>
"""
    html_path = md_path.with_suffix(".html")
    html_path.write_text(full_html, encoding="utf-8")
    print(f"  HTML: {html_path} ({html_path.stat().st_size // 1024} KB)")
    return html_path


def build_pdf(html_path: Path) -> Path:
    pdf_path = html_path.with_suffix(".pdf")
    script_file = Path(tempfile.mktemp(suffix=".js"))
    script_file.write_text(PUPPETEER_SCRIPT, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", str(script_file), str(html_path), str(pdf_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"  PDF: {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")
    finally:
        script_file.unlink(missing_ok=True)
    return pdf_path


def main():
    try:
        import markdown  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "markdown"], check=True)

    targets = sorted(DOCS.glob("EAEDK-*.md"))
    if not targets:
        print("No EAEDK-*.md files found in docs/")
        sys.exit(1)

    for md_path in targets:
        print(f"\nBuilding: {md_path.name}")
        html_path = build_html(md_path)
        build_pdf(html_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
