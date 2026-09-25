# CBT Exam System

A public, disposable computer-based testing prototype built with Streamlit and SQLite. Visitors can try the student and administrator flows, create tests, add questions, sit timed examinations, and view released results and topic-based reports. No database server or paid service is required for the core app.

## Run locally

1. Use Python 3.12 and install `requirements.txt` in a virtual environment.
2. Run `streamlit run app.py` from this folder.
3. Select **Register** to create a demo account. The administrator registration screen displays the demo key, `DEMO-ADMIN`, so visitors can try that role too.

The entry point is `app.py`. The database is created automatically at `data/demo.sqlite3`. You do not need to fill in `.env` for the basic demo.

## Deploy on Streamlit Community Cloud

Point the app at `app.py` in this repository. No MySQL server, database credentials, or AI key is needed for basic use. The SQLite database and uploaded question images are stored on the app's local filesystem and **may disappear when Streamlit restarts or rebuilds the app**. They are ignored by Git. This is intentional for a throwaway demonstration, not suitable for a real school's records.

The demo key is deliberately public. Any visitor can create an administrator account, change demo content, and potentially inspect other demo records. Use **fictional names, IDs, passwords, and security-question answers only**. Do not use this deployment for real examinations or student data.

### Optional availability check

After the app is deployed, set the GitHub repository variable `STREAMLIT_APP_URL` under **Settings → Secrets and variables → Actions → Variables** to its exact public `https://<app-name>.streamlit.app/` address. Until that variable is set, the [keep-alive workflow](.github/workflows/keep_alive.yml) skips its job. It runs every four hours and can also be started manually from **Actions**. It opens the app in Chromium, requests wake-up if Streamlit shows a sleeping-app screen, checks that the CBT interface appears, and uploads a screenshot if verification fails. The URL is restricted to HTTPS `*.streamlit.app` hosts. No credentials are required for this public demo.

This is a best-effort availability check, not a guarantee that the app will never sleep: Streamlit can still suspend or redeploy the app, and scheduled GitHub Actions can run late, be missed, or be disabled after repository inactivity. Check the workflow result and the live URL after deployment; visitors can wake a sleeping public app themselves.

## Built-in AI assistance

When keys are configured, the app tries Gemini 3.5 Flash-Lite, Gemini 3.1 Flash-Lite, then Groq's GPT-OSS 20B. An invalid Gemini key or a Gemini rate-limit response skips the second Gemini attempt. If only one provider is configured, it uses that provider directly. There is no per-user AI switch. The core question parser, keyword-based classifier, and deterministic reports remain available when AI is absent, unavailable, or over the demo allowance. Set `GEMINI_API_KEY` and/or `GROQ_API_KEY` in your ignored local `.env` or as **root-level Streamlit secrets**. Do not commit either key.

- **Smart Paste:** pressing **Prepare questions** sends up to 12,000 characters for formatting into an editable draft only when the input is not already in the supported format. An explicit answer label is required for each question. The app rejects AI output if the question count or answer-label sequence differs from the input. The administrator must still review the wording, options and answers before saving. If any block fails validation, none of the blocks are saved. Without AI, the original text becomes the draft for the local parser.
- **Topic classification:** each saved bulk import sends at most 20 question texts in one request, then uses local classification for any remaining questions or if AI fails. A manually entered topic always takes precedence.
- **Reports and tracking:** released scores appear in a local chronological trend chart. Student study summaries use topic totals, and class-level observations use completed-attempt aggregates, once per result or set of aggregate figures in a session. The trend chart adds no AI calls and sends no individual score history to a provider. Local summaries remain available if AI fails. Names and IDs are not sent for these reports.

The app limits requests to four per minute and 60 per day per running process, counting each provider attempt separately. Short tasks use 6-, 4-, and 6-second network timeouts; larger Smart Paste requests use 8-, 5-, and 7-second timeouts. These are per-socket limits rather than a hard end-to-end deadline. It caps input and output sizes and does not otherwise retry failed calls. Streamlit restarts reset the local allowance; provider quotas still apply. Keep billing disabled on the Gemini project and remain on Groq's Free Plan if you require a no-cost boundary. Question text sent to AI leaves the app. Strict JSON output and answer-label checks improve reliability but cannot prove that an AI copied every word or option correctly; the review step remains essential. Arbitrary text without explicit answers cannot be converted into a verified exam question. Free-tier availability and quotas can change; check your accounts before deployment.

For a private demonstration, set `DEMO_MODE=false` and a strong `ADMIN_SECRET_KEY` through `.env` or Streamlit secrets. This hides the public demo key but **does not** make the application production-ready.

## Limits and production path

SQLite local storage is fine for trying the prototype, but it is not durable on Streamlit Community Cloud and has limited concurrent-write capacity. A real multi-user deployment needs a persistent hosted database, durable image storage, stronger authentication and authorisation, safer security-question handling, tested migrations, backups, accessibility review, and load testing. The browser-tab malpractice signal is only a demonstration and should not be treated as proof of misconduct.

The exam flow now stores the start of each security-question challenge in SQLite so refreshing the browser does not reset its 60-second deadline. New security-question answers are hashed; older local demo records with plain-text answers remain readable until replaced. Results from unfinished attempts are excluded from class analytics, and unreleased scores do not appear in student trend charts or topic reports. The `after_limit` release setting does not yet implement an automatic release condition; use `immediate` or `do_not_release` when you need predictable visibility. This prototype is not suitable for high-stakes invigilated examinations.

When an administrator enables **Show Correct Answers**, students can review their submitted answers and the correct answer labels after an immediate score release. The demo's login token appears in the browser URL to restore a session; do not share that URL or use real account details. A production deployment needs a secure session-cookie design and an explicit session-expiry policy.
