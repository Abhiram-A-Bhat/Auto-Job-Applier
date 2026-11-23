"""Puts Claude's LaTeX into a fixed ATS-safe template and compiles it to PDF with Tectonic."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import ROOT

ALLOWED = {"section", "job", "project", "textbf", "textit", "begin", "end", "item"}
PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=0.55in]{geometry}
\usepackage{fontspec}
\setmainfont{texgyreheros}[Extension=.otf, UprightFont=*-regular, BoldFont=*-bold, ItalicFont=*-italic,
  BoldItalicFont=*-bolditalic, Ligatures={TeX,NoCommon}]
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\hyphenpenalty=10000 \exhyphenpenalty=10000 \emergencystretch=2em
\setlist[itemize]{leftmargin=1.2em,itemsep=0.5pt,topsep=1pt,parsep=0pt}
\titleformat{\section}{\bfseries\large}{}{0pt}{\MakeUppercase}[\vspace{-3pt}\titlerule]
\titlespacing*{\section}{0pt}{6pt}{3pt}
\newcommand{\job}[3]{\par\vspace{2pt}\textbf{#1}\hfill #2\par #3\par}
\newcommand{\project}[1]{\par\vspace{2pt}\textbf{#1}\par}
"""


def compiler():
    local = ROOT / "tools" / "tectonic.exe"
    found = os.environ.get("TECTONIC") or (str(local) if local.exists() else shutil.which("tectonic"))
    if not found:
        raise RuntimeError("The LaTeX compiler (Tectonic) isn't installed in jobbot/tools.")
    return found


def _escape(text):
    return re.sub(r"([&%$#_{}])", r"\\\1", str(text))


def document(body, contact):
    items = [contact.get(k) for k in ("email", "phone", "location", "linkedin", "github", "website")]
    line = " $|$ ".join(_escape(x) for x in items if x)
    return (PREAMBLE + "\\begin{document}\n\\begin{center}{\\LARGE\\bfseries " + _escape(contact["name"]) +
            "}\\\\[2pt]\n" + line + "\n\\end{center}\n\\vspace{-6pt}\n" + body + "\n\\end{document}\n")


def unsafe(body):
    """Job pages are untrusted, so the body may only use the few formatting commands the template expects."""
    if "^^" in body:
        return "Remove the ^^ sequence."
    text = body.replace("\\\\", " ")
    bad = sorted(set(re.findall(r"\\([a-zA-Z]+)", text)) - ALLOWED)
    envs = set(re.findall(r"\\(?:begin|end)\{([^}]*)\}", text)) - {"itemize"}
    if bad or envs:
        return "Use only the allowed commands. Remove: " + ", ".join(bad + sorted(envs))
    return None


def _numbers(text):
    return {n.rstrip(".,").replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def invented_numbers(body, master_txt):
    return sorted(_numbers(body) - _numbers(master_txt))


def compile_pdf(tex):
    """Returns (pdf bytes, page count). Raises ValueError with the LaTeX error so Claude can fix it."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "resume.tex"
        src.write_text(tex, encoding="utf-8")
        r = subprocess.run([compiler(), "-X", "compile", "--untrusted", "--keep-logs", "--outdir", d, str(src)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, cwd=d)
        pdf, log = Path(d) / "resume.pdf", Path(d) / "resume.log"
        log_text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if r.returncode or not pdf.exists():
            errors = [l for l in log_text.splitlines() if l.startswith("!")] or (r.stderr or r.stdout).splitlines()[-6:]
            raise ValueError("LaTeX failed to compile: " + " ".join(errors)[:600])
        pages = re.search(r"Output written on .*?\((\d+) pages?", log_text)
        return pdf.read_bytes(), int(pages[1]) if pages else len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes()))
