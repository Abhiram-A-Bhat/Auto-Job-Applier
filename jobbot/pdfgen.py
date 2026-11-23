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
_C = ParagraphStyle("bc", fontName="Helvetica", fontSize=9.5, leading=11.8)
COMPACT = (_C, ParagraphStyle("h1c", parent=_C, fontName="Helvetica-Bold", fontSize=15, leading=18),
           ParagraphStyle("h2c", parent=_C, fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=5, spaceAfter=2),
           ParagraphStyle("boldc", parent=_C, fontName="Helvetica-Bold"))


def _p(text, style=BASE):
    return Paragraph(escape(str(text)), style)


def _bullets(items, style=BASE):
    return ListFlowable([ListItem(_p(b, style), leftIndent=12) for b in items if b], bulletType="bullet",
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


def _cert_line(c, with_about):
    head = ", ".join(x for x in (c["name"], c.get("issuer"), c.get("date")) if x)
    return f"{head}: {c['about']}" if with_about and c.get("about") else head


def build_pdf(resume, contact, out_path):
    """Keeps the resume to one page: slightly tighter type first, then fewer certificate descriptions, then entries."""
    certs = resume.get("certifications") or []
    n = len(certs)
    tries = [(False, n, n)] + [(True, n, d) for d in range(n, -1, -1)] + [(True, k, 0) for k in range(n - 1, 1, -1)]
    for compact, shown, described in tries:
        side, edge = (0.6, 0.5) if compact else (0.7, 0.6)
        doc = SimpleDocTemplate(str(out_path), pagesize=letter, leftMargin=side * inch, rightMargin=side * inch,
                                topMargin=edge * inch, bottomMargin=edge * inch, title=f"{contact['name']} - Resume",
                                author=contact["name"])
        lines = [_cert_line(c, i < described) for i, c in enumerate(certs[:shown])]
        doc.build(_story(resume, contact, lines, COMPACT if compact else (BASE, H1, H2, BOLD)))
        if doc.page <= 1:
            break
    size = out_path.stat().st_size
    if size > MAX_BYTES:
        raise RuntimeError(f"PDF is {size} bytes, over the 2.5 MB limit")
    return out_path


def _story(resume, contact, cert_lines, styles):
    base, h1, h2, bold = styles
    s = [_p(contact["name"], h1)]
    line = " | ".join(str(contact[k]) for k in ("email", "phone", "location", "linkedin", "github", "website")
                      if contact.get(k))
    s += [_p(line, base), Spacer(1, 4)]
    if resume.get("summary"):
        s += [_p("SUMMARY", h2), _p(resume["summary"], base)]
    if resume.get("skills"):
        s += [_p("SKILLS", h2), _p(", ".join(resume["skills"]), base)]
    if resume.get("experience"):
        s.append(_p("EXPERIENCE", h2))
        for e in resume["experience"]:
            s += [_p(f"{e['title']}, {e['company']}", bold),
                  _p(" | ".join(x for x in (e.get("dates"), e.get("location")) if x), base),
                  _bullets(e["bullets"], base), Spacer(1, 3)]
    if resume.get("projects"):
        s.append(_p("PROJECTS", h2))
        for p in resume["projects"]:
            s += [_p(p["name"] + (f" ({p['link']})" if p.get("link") else ""), bold), _bullets(p["bullets"], base),
                  Spacer(1, 3)]
    if resume.get("education"):
        s.append(_p("EDUCATION", h2))
        for e in resume["education"]:
            s.append(_p(f"{e['degree']}, {e['school']} | {e.get('dates', '')}", base))
    if resume.get("achievements"):
        s += [_p("ACHIEVEMENTS", h2), _bullets(resume["achievements"], base)]
    if cert_lines:
        s += [_p("CERTIFICATIONS", h2), _bullets(cert_lines, base)]
    return s
