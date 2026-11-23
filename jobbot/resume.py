"""Link -> job description -> Claude writes LaTeX -> compiled PDF."""
import re

from . import ai, fetchjob, latex
from .config import ROOT, load_master

OUTPUT = ROOT / "output"
ATTEMPTS = 3


def _name(*parts):
    return re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")[:90]


def make(url, pasted=""):
    """Returns {company, role, pdf, tex, note}: file names inside output/."""
    master, master_txt = load_master()
    job = fetchjob.read(url, pasted)
    draft = ai.write_resume(job, master_txt)
    pdf, tex, note = None, None, ""
    for attempt in range(ATTEMPTS):
        problem = latex.unsafe(draft["body"])
        extra = latex.invented_numbers(draft["body"], master_txt)
        if not problem:
            tex = latex.document(draft["body"], master["contact"])
            try:
                pdf, pages = latex.compile_pdf(tex)
                if pages > 1:
                    problem = f"It is {pages} pages. Shorten it to exactly one page."
                elif extra:
                    problem = "Remove these numbers, which are not in the master resume: " + ", ".join(extra)
                else:
                    break
            except ValueError as e:
                pdf, problem = None, str(e)
        if attempt == ATTEMPTS - 1:
            if pdf is None:
                raise RuntimeError(problem)
            note = "Please check this before sending: " + problem
            break
        draft = ai.fix_resume(job, master_txt, draft, problem)
    OUTPUT.mkdir(exist_ok=True)
    base = _name(master["contact"]["name"], draft["company"], draft["role"])
    (OUTPUT / f"{base}.pdf").write_bytes(pdf)
    (OUTPUT / f"{base}.tex").write_text(tex, encoding="utf-8")
    return {"company": draft["company"], "role": draft["role"], "pdf": f"{base}.pdf", "tex": f"{base}.tex", "note": note}
