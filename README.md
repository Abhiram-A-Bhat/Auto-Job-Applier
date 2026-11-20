# jobbot - paste a link, tailor, approve, auto-fill

```powershell
pip install -r requirements.txt
playwright install chromium
claude login                # once; the tool uses your Claude subscription via Claude Code (backend: cli)
python main.py app          # opens http://127.0.0.1:5000
python main.py stats        # real token usage so far
# Prefer pay-per-token? set backend: api in config.yaml and $env:ANTHROPIC_API_KEY
```

## Login and phone access
`python main.py set-login --email you@example.com` sets the app login (stored only as a scrypt hash in the
git-ignored `profile/auth.json`). Every page needs that login; "keep me logged in" sets a 30-day cookie per device.
5 wrong passwords lock that device out for 15 minutes.
The console prints the phone URL (e.g. `http://192.168.1.5:5000`) - your phone must be on the same Wi-Fi and the
laptop must be running the app. Allow Python through Windows Firewall on **Private** networks when asked.
Away from home: install Tailscale on both laptop and phone and open `http://<laptop-tailscale-ip>:5000`.
Set `server.host: 127.0.0.1` in config.yaml to make it laptop-only again.

## Cloud inbox (Vercel) - use it from anywhere
`cloud/` is a small Vercel app: paste links, approve jobs and answer form questions from your phone anywhere.
It only stores the queue (Upstash Redis). The laptop does the real work (tailoring with your Claude login,
filling forms in the logged-in browser), so while it's off, links simply wait; when `python main.py app` runs
again it processes them in the order you added them, and approved jobs are applied oldest first.
Setup: deploy `cloud/` to Vercel, add the Upstash Redis integration, set env vars `APP_EMAIL`,
`APP_PASSWORD_HASH`, `SECRET_KEY`, `AGENT_TOKEN`; put the same token in `profile/cloud_token` and the URL in
`config.yaml` -> `cloud.url`. `cloud.auto_approve_pasted: true` makes phone links apply without an approval click;
`auto_click_submit: true` lets approved forms submit without you at the laptop.

## Telegram bot - send links from your phone's chat
Free, works with a normal Telegram account. Create a bot with @BotFather, then set Vercel env vars `TG_TOKEN`
(BotFather's token), `TG_SECRET` (random; Telegram sends it back on every webhook call) and `TG_PAIR` (random
link code), and register the webhook: `https://api.telegram.org/bot<TG_TOKEN>/setWebhook` with
`url=<cloud url>/api/telegram/webhook` and `secret_token=<TG_SECRET>`. Open `t.me/<bot>?start=<TG_PAIR>` once to
link your chat; everyone else is ignored. Commands: send a link, `/status`, `/approve <code>`, `/reject <code>`,
`delete my data`. You get a message when a resume is ready, a form needs an answer, or a job is applied.

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
