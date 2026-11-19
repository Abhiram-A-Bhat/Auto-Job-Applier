"""ATS-safe PDF: one column, standard headings, plain text, real selectable text, < 2.5 MB."""
import re
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

MAX_BYTES = int(2.5 * 1024 * 1024)
BASE = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13)
H1 = ParagraphStyle("h1", parent=BASE, fontName="Helvetica-Bold", fontSize=16, leading=19)
H2 = ParagraphStyle("h2", parent=BASE, fontName="Helvetica-Bold", fontSize=11, spaceBefore=9, spaceAfter=3)
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")


def _p(text, style=BASE):
    return Paragraph(escape(str(text)), style)


def _bullets(items):
    return ListFlowable([ListItem(_p(b), leftIndent=12) for b in items if b], bulletType="bullet",
                        bulletFontSize=7, leftIndent=12)


def build_cover_pdf(text, contact, out_path):
    doc = SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch, title=f"{contact['name']} - Cover letter",
                            author=contact["name"])
    s = [_p(contact["name"], H1), _p(" | ".join(str(contact[k]) for k in ("email", "phone", "location") if contact.get(k))),
         Spacer(1, 14)]
    for para in re.split(r"\n\s*\n", text.strip()):
        s += [_p(" ".join(para.split())), Spacer(1, 9)]
    doc.build(s)
    return out_path


def safe_name(*parts):
    return re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")[:90]


def build_pdf(resume, contact, out_path):
    doc = SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=f"{contact['name']} - Resume",
                            author=contact["name"])
    s = [_p(contact["name"], H1)]
    line = " | ".join(str(contact[k]) for k in ("email", "phone", "location", "linkedin", "github", "website")
                      if contact.get(k))
    s += [_p(line), Spacer(1, 4)]
    if resume.get("summary"):
        s += [_p("SUMMARY", H2), _p(resume["summary"])]
    if resume.get("skills"):
        s += [_p("SKILLS", H2), _p(", ".join(resume["skills"]))]
    if resume.get("experience"):
        s.append(_p("EXPERIENCE", H2))
        for e in resume["experience"]:
            s += [_p(f"{e['title']}, {e['company']}", BOLD),
                  _p(" | ".join(x for x in (e.get("dates"), e.get("location")) if x)), _bullets(e["bullets"]),
                  Spacer(1, 4)]
    if resume.get("projects"):
        s.append(_p("PROJECTS", H2))
        for p in resume["projects"]:
            s += [_p(p["name"] + (f" ({p['link']})" if p.get("link") else ""), BOLD), _bullets(p["bullets"]),
                  Spacer(1, 4)]
    if resume.get("education"):
        s.append(_p("EDUCATION", H2))
        for e in resume["education"]:
            s.append(_p(f"{e['degree']}, {e['school']} | {e.get('dates', '')}"))
    if resume.get("achievements"):
        s += [_p("ACHIEVEMENTS", H2), _bullets(resume["achievements"])]
    doc.build(s)
    size = out_path.stat().st_size
    if size > MAX_BYTES:
        raise RuntimeError(f"PDF is {size} bytes, over the 2.5 MB limit")
    return out_path
