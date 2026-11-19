from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def load_master():
    """Returns (parsed master resume, raw yaml text). Refuses to run on the template."""
    txt = (ROOT / "profile" / "master_resume.yaml").read_text(encoding="utf-8")
    if "TODO" in txt:
        raise SystemExit("Fill in profile/master_resume.yaml first (it still contains TODO).")
    return yaml.safe_load(txt), txt
