"""Scores a resume against one job out of 100. A keyword only counts if the master resume proves it."""
import re

TARGET = 85
SECTIONS = ("Summary", "Skills", "Experience", "Education")
SAME = [{"rest api", "rest apis", "restful api", "restful apis"}, {"node", "node.js", "nodejs"},
        {"postgres", "postgresql"}, {"ai", "artificial intelligence"}, {"ml", "machine learning"},
        {"nlp", "natural language processing"}, {"genai", "gen ai", "generative ai"},{".net", "vb.net"}, {"js", "javascript"}, {"ts", "typescript"}]
FIRST_PERSON = re.compile(r"\b(i|my|me)\b", re.I)


def plain(body):
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}", " ", body)
    text = re.sub(r"\\(?:section|job|project|textbf|textit|item)\b", " ", text).replace("\\\\", " ")
    return re.sub(r"\s+", " ", re.sub(r"\\([&%$#_])", r"\1", text).replace("{", " ").replace("}", " ")).strip()


def _has(term, text):
    term = term.lower().strip()
    names = next((g for g in SAME if term in g), {term})
    names = names | {n + "s" for n in names} | {n[:-1] for n in names if n.endswith("s") and len(n) > 3}
    return any(re.search(r"(?<![a-z0-9+#.])" + re.escape(n) + r"(?![a-z0-9+#]|\.[a-z])", text) for n in names)


def _evidence(master_txt):
    return "\n".join(l for l in master_txt.lower().splitlines() if not l.lstrip().startswith("#"))


def score(body, pages, contact, keywords, role, master_txt):
    text, proof = plain(body).lower(), _evidence(master_txt)
    earned = sum(w for k, w in keywords.items() if _has(k, text) and _has(k, proof))
    keyword_pts = 55 * earned / sum(keywords.values()) if keywords else 0
    title_pts = 5 if role.lower() in text else 0
    found = [s for s in SECTIONS if re.search(r"\\section\{" + s + r"\}", body)]
    section_pts = 10 * len(found) / len(SECTIONS)
    contact_pts = 2.5 * sum(bool(contact.get(k)) for k in ("email", "phone", "location")) + \
        (2.5 if contact.get("linkedin") or contact.get("github") else 0)
    bullets = [plain(b) for b in re.findall(r"\\item\s+(.*?)(?=\\item|\\end\{itemize\})", body, re.S)]
    bullets = [b for b in bullets if not b.startswith("Technologies")]
    good = [b for b in bullets if 6 <= len(b.split()) <= 34 and not FIRST_PERSON.search(b)]
    bullet_pts = 10 * len(good) / len(bullets) if bullets else 0
    words = len(text.split())
    length_pts = 10 if pages == 1 and 300 <= words <= 800 else 5 if pages == 1 else 0
    return {
        "score": round(keyword_pts + title_pts + section_pts + contact_pts + bullet_pts + length_pts),
        "add": [k for k in keywords if not _has(k, text) and _has(k, proof)],
        "lacking": [k for k in keywords if not _has(k, proof)],
        "unproven": [k for k in keywords if _has(k, text) and not _has(k, proof)],
        "title": bool(title_pts), "sections": [s for s in SECTIONS if s not in found],
        "weak_bullets": len(bullets) - len(good), "words": words,
    }


def problem(r, role):
    """What Claude should fix, or None when there's nothing more it can honestly improve."""
    if r["unproven"]:
        return "Remove these terms, which the master resume does not prove: " + ", ".join(r["unproven"])
    if r["score"] >= TARGET:
        return None
    fixes = []
    if r["add"]:
        fixes.append("Work in these terms, spelled exactly like this, where the master proves them: " + ", ".join(r["add"]))
    if not r["title"]:
        fixes.append(f'State the exact job title in the Summary as the role being sought: "{role}".')
    if r["sections"]:
        fixes.append("Add the missing sections: " + ", ".join(r["sections"]))
    if r["weak_bullets"]:
        fixes.append("Keep every bullet between 10 and 28 words, with no first person.")
    if not 300 <= r["words"] <= 800:
        fixes.append("Keep the resume between 350 and 750 words.")
    return " ".join(fixes) or None


def note(r):
    if r["unproven"]:
        return f"ATS score {r['score']}/100. Please check these terms, which your master resume doesn't prove: " + \
            ", ".join(r["unproven"])
    if r["score"] >= TARGET:
        return f"ATS score {r['score']}/100."
    if r["lacking"]:
        return f"ATS score {r['score']}/100, below {TARGET}. The job asks for these, which aren't in your master " \
            "resume, so I left them out: " + ", ".join(r["lacking"][:8])
    return f"ATS score {r['score']}/100, below {TARGET}."
