"""Convert the analysis package REPORT.md into a paginated PDF.

Reads `docs/performance/<source>-package/REPORT.md` and the sibling
`charts/` directory; produces a single PDF with the full text, all
tables, and every chart embedded inline.

Pure reportlab — no external binaries (pandoc, wkhtmltopdf) required.

Usage
-----
  ./venv/bin/python scripts/analysis/build_pdf_report.py
  ./venv/bin/python scripts/analysis/build_pdf_report.py \
      --source-dir docs/performance/2026-05-10-package \
      --out docs/performance/2026-05-11-package/REPORT.pdf
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("pdf_report")


# -----------------------------------------------------------------------------
# Minimal markdown lexer — handles the subset that appears in REPORT.md
# -----------------------------------------------------------------------------

def parse_markdown(text: str) -> Iterator[Tuple[str, object]]:
    """Yield (kind, payload) tuples. kind ∈ {h1, h2, h3, p, img, table, code,
    hr, ul, blockquote}."""
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.rstrip()

        if stripped.startswith("# "):
            yield "h1", stripped[2:].strip(); i += 1; continue
        if stripped.startswith("## "):
            yield "h2", stripped[3:].strip(); i += 1; continue
        if stripped.startswith("### "):
            yield "h3", stripped[4:].strip(); i += 1; continue

        m_img = re.match(r"^!\[(.*?)\]\((.+?)\)\s*$", stripped)
        if m_img:
            yield "img", (m_img.group(1), m_img.group(2))
            i += 1; continue

        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1]):
            tbl_lines: List[str] = []
            while i < n and lines[i].startswith("|"):
                tbl_lines.append(lines[i])
                i += 1
            yield "table", tbl_lines
            continue

        if stripped.startswith("```"):
            code_lines: List[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # consume closing ```
            yield "code", "\n".join(code_lines)
            continue

        if stripped.strip() in ("---", "***"):
            yield "hr", None; i += 1; continue

        if stripped.startswith("> "):
            quote_lines = []
            while i < n and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:].strip())
                i += 1
            yield "blockquote", " ".join(quote_lines)
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            items: List[str] = []
            while i < n and (lines[i].startswith("- ") or lines[i].startswith("* ")):
                items.append(lines[i][2:].strip())
                # gather continuation lines (indented)
                i += 1
                while i < n and lines[i].startswith("  ") and not (
                    lines[i].startswith("- ") or lines[i].startswith("* ")
                ):
                    items[-1] += " " + lines[i].strip()
                    i += 1
            yield "ul", items
            continue

        if not stripped:
            i += 1; continue

        # Paragraph: gather until blank or special line
        para_lines = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                break
            if (nxt.startswith("#") or nxt.startswith("![") or nxt.startswith("|")
                    or nxt.startswith("```") or nxt.startswith("- ")
                    or nxt.startswith("* ") or nxt.startswith("> ")):
                break
            para_lines.append(nxt.rstrip())
            i += 1
        yield "p", " ".join(para_lines)


def inline_md(text: str) -> str:
    """Convert markdown inline elements to reportlab paragraph markup."""
    # Escape XML first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold + italic + code (after escaping so we don't double-escape)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+?)`", r"<font face='Courier' size='9'>\1</font>", text)
    # Links — keep text only, drop URL (we're in a PDF anyway)
    text = re.sub(r"\[(.*?)\]\((.+?)\)", r"\1", text)
    return text


def parse_table(lines: List[str]) -> Tuple[List[str], List[List[str]]]:
    """Parse a pipe-table into (headers, rows). Skips the separator row."""
    def split_row(s: str) -> List[str]:
        cells = s.split("|")[1:-1]
        return [c.strip() for c in cells]

    if not lines:
        return [], []
    headers = split_row(lines[0])
    rows = [split_row(l) for l in lines[2:] if l.startswith("|")]
    return headers, rows


# -----------------------------------------------------------------------------
# reportlab style sheet
# -----------------------------------------------------------------------------

def make_styles():
    styles = getSampleStyleSheet()
    custom = {
        "title":    ParagraphStyle("title", parent=styles["Title"],
                                    fontSize=22, leading=26, alignment=1,
                                    textColor=colors.HexColor("#1e293b")),
        "subtitle": ParagraphStyle("subtitle", parent=styles["Normal"],
                                    fontSize=12, leading=14, alignment=1,
                                    textColor=colors.HexColor("#475569")),
        "h1":       ParagraphStyle("h1", parent=styles["Heading1"],
                                    fontSize=18, leading=22, spaceBefore=16,
                                    spaceAfter=8,
                                    textColor=colors.HexColor("#1e293b")),
        "h2":       ParagraphStyle("h2", parent=styles["Heading2"],
                                    fontSize=14, leading=18, spaceBefore=12,
                                    spaceAfter=6,
                                    textColor=colors.HexColor("#1e3a8a")),
        "h3":       ParagraphStyle("h3", parent=styles["Heading3"],
                                    fontSize=11, leading=14, spaceBefore=8,
                                    spaceAfter=4,
                                    textColor=colors.HexColor("#334155")),
        "body":     ParagraphStyle("body", parent=styles["BodyText"],
                                    fontSize=9.5, leading=13,
                                    spaceBefore=2, spaceAfter=4,
                                    textColor=colors.HexColor("#0f172a")),
        "bullet":   ParagraphStyle("bullet", parent=styles["BodyText"],
                                    fontSize=9.5, leading=13,
                                    leftIndent=14, bulletIndent=2,
                                    spaceBefore=1, spaceAfter=1),
        "quote":    ParagraphStyle("quote", parent=styles["BodyText"],
                                    fontSize=10, leading=14, leftIndent=14,
                                    rightIndent=14, italic=True,
                                    textColor=colors.HexColor("#475569"),
                                    borderLeftColor=colors.HexColor("#cbd5e1"),
                                    borderLeftWidth=2, borderPadding=6),
        "caption":  ParagraphStyle("caption", parent=styles["Normal"],
                                    fontSize=8, leading=10, alignment=1,
                                    textColor=colors.HexColor("#64748b"),
                                    spaceAfter=8),
        "code":     ParagraphStyle("code", parent=styles["Code"],
                                    fontSize=8, leading=10,
                                    leftIndent=10, rightIndent=10,
                                    spaceBefore=4, spaceAfter=4,
                                    backColor=colors.HexColor("#f1f5f9")),
    }
    return custom


def build_pdf(md_path: Path, pdf_path: Path, base_dir: Path,
              cover_title: str, cover_subtitle: str):
    md_text = md_path.read_text()
    styles = make_styles()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=cover_title, author="StockDrop Analytics",
    )
    page_w, page_h = doc.width, doc.height

    story = []
    # ---------------- Cover ----------------
    story.append(Spacer(1, page_h * 0.25))
    story.append(Paragraph(cover_title, styles["title"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph(cover_subtitle, styles["subtitle"]))
    story.append(Spacer(1, 36))
    story.append(Paragraph(
        f"Generated {datetime.now():%Y-%m-%d %H:%M}",
        styles["subtitle"]))
    story.append(PageBreak())

    img_count = 0
    table_count = 0
    for kind, payload in parse_markdown(md_text):
        if kind == "h1":
            story.append(Paragraph(inline_md(payload), styles["h1"]))
        elif kind == "h2":
            story.append(Paragraph(inline_md(payload), styles["h2"]))
        elif kind == "h3":
            story.append(Paragraph(inline_md(payload), styles["h3"]))
        elif kind == "p":
            story.append(Paragraph(inline_md(payload), styles["body"]))
        elif kind == "blockquote":
            story.append(Paragraph(inline_md(payload), styles["quote"]))
        elif kind == "ul":
            for item in payload:
                story.append(Paragraph("• " + inline_md(item), styles["bullet"]))
        elif kind == "code":
            story.append(Preformatted(payload, styles["code"]))
        elif kind == "hr":
            story.append(Spacer(1, 12))
        elif kind == "table":
            headers, rows = parse_table(payload)
            if not rows:
                continue
            table_count += 1
            data = [[Paragraph("<b>" + inline_md(h) + "</b>",
                                styles["body"]) for h in headers]]
            for row in rows:
                data.append([Paragraph(inline_md(c), styles["body"])
                             for c in row])
            tbl = Table(data, repeatRows=1, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(Spacer(1, 4))
            story.append(tbl)
            story.append(Spacer(1, 6))
        elif kind == "img":
            alt, rel_path = payload
            img_path = (base_dir / rel_path).resolve()
            if not img_path.exists():
                logger.warning("Image not found: %s", img_path)
                continue
            img_count += 1
            # Scale to fit width while preserving aspect
            from PIL import Image as PILImage
            with PILImage.open(img_path) as im:
                ar = im.width / im.height
            max_w = page_w
            max_h = page_h * 0.50  # take at most half a page in height
            target_w = max_w
            target_h = target_w / ar
            if target_h > max_h:
                target_h = max_h
                target_w = target_h * ar
            story.append(KeepTogether([
                Image(str(img_path), width=target_w, height=target_h),
                Paragraph(f"<i>{inline_md(alt) or img_path.name}</i>",
                           styles["caption"]),
            ]))
            story.append(Spacer(1, 4))

    logger.info("Rendering PDF: %d charts, %d tables...", img_count, table_count)
    doc.build(story)
    logger.info("Wrote %s (%.0f KB)", pdf_path,
                pdf_path.stat().st_size / 1024)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir", default=None,
        help="Package directory containing REPORT.md and charts/. "
             "Defaults to the most recent <date>-package/ under docs/performance/",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs" / "performance" / f"{today}-package" / "REPORT.pdf"),
    )
    args = parser.parse_args()

    perf_dir = REPO_ROOT / "docs" / "performance"
    if args.source_dir:
        source = Path(args.source_dir)
    else:
        # Pick the most recent package dir that has REPORT.md
        candidates = sorted(
            d for d in perf_dir.glob("*-package")
            if (d / "REPORT.md").exists()
        )
        if not candidates:
            logger.error("No package directory with REPORT.md found")
            sys.exit(1)
        source = candidates[-1]
    logger.info("Source: %s", source)

    md_path = source / "REPORT.md"
    if not md_path.exists():
        logger.error("REPORT.md not found at %s", md_path); sys.exit(1)

    out_pdf = Path(args.out)
    build_pdf(
        md_path, out_pdf, source,
        cover_title="StockDrop Performance Analysis",
        cover_subtitle=f"Source cohort: {source.name}",
    )


if __name__ == "__main__":
    main()
