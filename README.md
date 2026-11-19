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

## Auto job search
While the app runs it searches every `auto_fetch_hours` (or click "Find matching jobs now", or `python main.py fetch`):
company boards (Greenhouse/Lever/Ashby, list in `config.yaml`), Remotive, and Adzuna India
(free keys from developer.adzuna.com -> `$env:ADZUNA_APP_ID`, `$env:ADZUNA_APP_KEY`).
Filters: tech titles, India/remote-from-India locations, salary >= `min_salary_lpa` (or "salary not listed",
see `unknown_salary`), then a free local skill-overlap check. Only the best `max_score_per_run` are scored by AI and
at most `max_tailor_per_run` are tailored - those two numbers control token usage.
`auto_approve_min_score` lets high-fit jobs start filling without a click; you still press Submit unless
`auto_click_submit: true`. Only Bengaluru or remote jobs (remote-in-another-country is dropped).

## LinkedIn / Naukri / Indeed / Wellfound
Click "Open jobbot browser to log in" and log in to each site once in that window (it remembers you).
Searches then also read those sites in that window, and pasted links from them get filled like any other form
(LinkedIn Easy Apply / Indeed multi-step flows are stepped through; on Naukri you press Apply yourself, because
that button submits instantly). Login walls and "verify you are human" checks are never bypassed - it pauses
until you clear them. These sites' terms forbid automation and can restrict accounts: keep
`platforms.max_jobs_per_platform` modest, or set `platforms.enabled: []` to switch it off.

## Guardrails
Employer/title/dates/education come from the master resume, skills not in it are dropped, any number not in it is
flagged red in the queue. Resume PDF: one column, plain selectable text, under 2.5 MB. Optional unanswered fields are
left blank (`ask_optional: true` in config.yaml to be asked instead).
