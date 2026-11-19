# jobbot - paste a link, tailor, approve, auto-fill

```powershell
pip install -r requirements.txt
playwright install chromium
claude login                # once; the tool uses your Claude subscription via Claude Code (backend: cli)
python main.py app          # opens http://127.0.0.1:5000
python main.py stats        # real token usage so far
# Prefer pay-per-token? set backend: api in config.yaml and $env:ANTHROPIC_API_KEY
```

## Daily use
1. Paste a job link in the app. Greenhouse / Lever / Ashby links and most company career pages work directly.
2. It tailors your resume + cover letter (rules in `jobbot/ai.py`) and puts it in the approval queue.
3. Click **Approve**. A visible browser opens the application and fills it in:
   - name/email/phone/links come from `profile/master_resume.yaml`
   - everything else comes from remembered answers (`profile/answers.json`, editable at /memory)
   - anything unknown shows up as a question in the app. Answer once; it is remembered (tick/untick "remember").
   - free-text questions get a draft from your resume that you edit; those aren't remembered.
4. Review the browser, press **Submit yourself**, then click "I submitted it" in the app.

LinkedIn / Indeed / Wellfound / Talboss links are not fetched or automated: paste the job description in the box,
get the tailored PDF, apply yourself, click "I applied".

Optional: `python main.py fetch` auto-discovers jobs from company boards listed in `config.yaml` (cheap Haiku filter first).

## Guardrails
Employer/title/dates/education come from the master resume, skills not in it are dropped, any number not in it is
flagged red in the queue. Resume PDF: one column, plain selectable text, under 2.5 MB. Optional unanswered fields are
left blank (`ask_optional: true` in config.yaml to be asked instead).
