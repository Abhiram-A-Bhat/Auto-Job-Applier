# Resume tailor

Paste a job link. Claude Code reads the job and writes a tailored LaTeX resume. The program compiles it to a PDF.

1. Put your details in `profile/master_resume.yaml`. Claude only selects and rewords what is in this file.
2. Double-click **Start JobBot.bat** (or run `python main.py`). The page opens at http://127.0.0.1:5000.
3. Paste the link and press **Make PDF**. If the link needs a login, paste the job description instead.

The PDF and `.tex` are saved in `output/`.

Needs: Python, a logged-in Claude Code, and the Tectonic LaTeX compiler at `tools/tectonic.exe`.
