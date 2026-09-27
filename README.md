# CBT Exam System

A lightweight computer-based testing prototype built with Python, Streamlit and SQLite, designed with institutions that have limited computing infrastructure in mind. Each visitor can create a private demo workspace, try the student and administrator flows, create tests, add questions, sit timed examinations, and view released results and topic-based reports. No database server or paid service is required for the core app.

## Run locally

1. Use Python 3.12 and install `requirements.txt` in a virtual environment.
2. Run `streamlit run app.py` from this folder.
3. Select **Create private demo** and save the workspace code and administrator key shown on screen. Register an administrator with that key, then share only the workspace code with people joining as students. Returning visitors can enter the code under **Join demo**.

The entry point is `app.py`. Each private demo gets a separate SQLite file under `data/workspaces/`. Existing records in the former shared `data/demo.sqlite3` file are left untouched but are not shown in new public demo workspaces. You do not need to fill in `.env` for the basic demo.

The interface uses a light theme. Navigation opens beside the content on desktop; on narrow screens, use the top-left arrow to open the sidebar when needed so the exam stays readable.

To attach a diagram to a question, sign in as an administrator, open **Add Questions**, select a test, choose a PNG, JPEG, GIF, WebP, BMP, or TIFF in **Upload Diagram/Image (Optional)**, and save the question. Images are limited to 5 MB; animated GIFs use their first frame. The app creates a separate folder under `uploads/` for each workspace and displays saved images during its exams. Uploaded images are ignored by Git and, like the SQLite database, are temporary on Streamlit Community Cloud; they can disappear after a restart or redeployment. Keep original copies outside this demo.

## Deploy on Streamlit Community Cloud

Point the app at `app.py` in this repository. No MySQL server, database credentials, or AI key is needed for basic use. A new workspace receives a random join code and a separate administrator key. Its accounts, tests, results and images are isolated from other workspaces. Keep the administrator key private; share only the workspace code with students. A workspace becomes inaccessible seven days after creation, and the app removes expired files when it next runs. The SQLite files and uploaded images live on Streamlit's local filesystem and **may disappear before seven days if Streamlit restarts or rebuilds the app**. They are ignored by Git. Seven days is a maximum lifetime, not guaranteed retention.

This remains a public prototype, not a production isolation or access-control system. Use **fictional names, IDs, passwords, and security-question answers only**. Do not use this deployment for real examinations or student data. Do not share a signed-in browser URL: it contains a session token as well as the workspace code.

### Optional availability check

The [keep-alive workflow](.github/workflows/keep_alive.yml) checks the public app at `https://cbt-exam-system.streamlit.app/` every four hours and can also be started manually from **Actions**. If the app URL changes, set the GitHub repository variable `STREAMLIT_APP_URL` under **Settings → Secrets and variables → Actions → Variables** to override that default. The workflow opens the app in Chromium, requests wake-up if Streamlit shows a sleeping-app screen, checks that the CBT interface appears, and uploads a screenshot if verification fails. The URL is restricted to HTTPS `*.streamlit.app` hosts. No credentials are required for this public demo.

This is a best-effort availability check, not a guarantee that the app will never sleep: Streamlit can still suspend or redeploy the app, and scheduled GitHub Actions can run late, be missed, or be disabled after repository inactivity. Check the workflow result and the live URL after deployment; visitors can wake a sleeping public app themselves.

## Built-in AI assistance

When keys are configured, the app tries Gemini 3.5 Flash-Lite, Gemini 3.1 Flash-Lite, then Groq's GPT-OSS 20B. An invalid Gemini key or a Gemini rate-limit response skips the second Gemini attempt. If only one provider is configured, it uses that provider directly. There is no per-user AI switch. The core question parser, keyword-based classifier, and deterministic reports remain available when AI is absent, unavailable, or over the demo allowance. Set `GEMINI_API_KEY` and/or `GROQ_API_KEY` in your ignored local `.env` or as **root-level Streamlit secrets**. Do not commit either key.

- **Smart Paste:** pressing **Prepare questions** sends up to 12,000 characters for formatting into an editable draft only when the input is not already in the supported format. An explicit answer label is required for each question. The app rejects AI output if the question count or answer-label sequence differs from the input. The administrator must still review the wording, options and answers before saving. If any block fails validation, none of the blocks are saved. Without AI, the original text becomes the draft for the local parser.
- **Topic classification:** each saved bulk import sends at most 20 question texts in one request, then uses local classification for any remaining questions or if AI fails. A manually entered topic always takes precedence.
- **Reports and tracking:** released scores appear in a local chronological trend chart. The existing student study-summary request also includes up to eight recent released score percentages, in chronological order, so AI can comment cautiously on the trend without another call. Unreleased scores, names, IDs, dates and test titles are not sent in that history. Class-level observations still use completed-attempt aggregates. Summaries are cached in each session until the underlying results change, and local summaries remain available if AI fails. Score percentages and topic totals sent to AI leave the app; use fictional demo data only.

The app limits requests to four per minute and 60 per day per running process, counting each provider attempt separately. Short tasks use 6-, 4-, and 6-second network timeouts; larger Smart Paste requests use 8-, 5-, and 7-second timeouts. These are per-socket limits rather than a hard end-to-end deadline. It caps input and output sizes and does not otherwise retry failed calls. Streamlit restarts reset the local allowance; provider quotas still apply. Keep billing disabled on the Gemini project and remain on Groq's Free Plan if you require a no-cost boundary. Question text sent to AI leaves the app. Strict JSON output and answer-label checks improve reliability but cannot prove that an AI copied every word or option correctly; the review step remains essential. Arbitrary text without explicit answers cannot be converted into a verified exam question. Free-tier availability and quotas can change; check your accounts before deployment.

For a private demonstration, set `DEMO_MODE=false` and a strong `ADMIN_SECRET_KEY` through `.env` or Streamlit secrets. This hides the public demo key but **does not** make the application production-ready.

## Limits and production path

SQLite local storage is fine for trying the prototype, but it is not durable on Streamlit Community Cloud and has limited concurrent-write capacity. A real multi-user deployment needs a persistent hosted database, durable image storage, stronger authentication and authorisation, safer security-question handling, tested migrations, backups, accessibility review, and load testing. The browser-tab malpractice signal is only a demonstration and should not be treated as proof of misconduct.

The exam flow now stores the start of each security-question challenge in SQLite so refreshing the browser does not reset its 60-second deadline. New security-question answers are hashed; older local demo records with plain-text answers remain readable until replaced. Results from unfinished attempts are excluded from class analytics, and unreleased scores do not appear in student trend charts or topic reports. With `after_limit`, scores and reports are released when the configured maximum number of distinct students have completed the test; a limit above zero is required. Choose `do_not_release` to keep them hidden. This prototype is not suitable for high-stakes invigilated examinations.

When an administrator enables **Show Correct Answers**, students can review their submitted answers and the correct answer labels after their scores are released. The demo's login token appears in the browser URL to restore a session; do not share that URL or use real account details. A production deployment needs a secure session-cookie design and an explicit session-expiry policy.
