# PRLens

PRLens is a Python service that uses Gemini to help developers review GitHub pull requests. FastAPI accepts review requests, and Celery workers fetch review inputs from GitHub and run the analysis in the background. Redis queues jobs and stores manual review results.

| Review mode | How to trigger it | Where to see the results |
| --- | --- | --- |
| Manual API | Submit a GitHub repository URL and PR number | Poll the API for per-file findings on bugs, style, performance, and best practices |
| GitHub App pilot | Install the app on a repository and open, reopen, update, or retarget a PR | GitHub receives an advisory review with up to two inline comments when findings qualify. PostgreSQL records the outcome for every processed delivery. |

- [Install and start PRLens](#install-and-start-prlens)
- [Review a PR with the manual API](#review-a-pr-with-the-manual-api)
- [Set up automatic GitHub reviews](#set-up-automatic-github-reviews)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)

## Install and start PRLens

You need Git, Python (use 3.13 for the commands below), Docker for local Redis, and a Gemini API key. The automatic review workflow also needs PostgreSQL and a GitHub App. [Create a Gemini API key](https://ai.google.dev/gemini-api/docs/api-key)

### 1. Get the code and install dependencies

Run all Python commands from the repository root. If you already have a checkout, start from that directory.

```bash
git clone https://github.com/aarnasingh812/PRLens---AI_Powered_PR_Analysis_Service.git
cd PRLens---AI_Powered_PR_Analysis_Service
```

Create and activate the virtual environment for your shell.

**macOS or Linux**

```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows PowerShell**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install the project dependencies.

```bash
python -m pip install -r requirements.txt
```

### 2. Configure the service

Create a file named `.env` in the repository root. Replace the API key placeholder with your own key and keep the file out of Git.

```dotenv
GEMINI_API_KEY=replace-with-your-gemini-api-key
PRLENS_REDIS_URL=redis://127.0.0.1:6379/0
```

PRLens defaults to `gemini-3.1-flash-lite`. The model receives repository code during a review, so use a PR whose code you are authorized to send to Gemini.

### 3. Start Redis

Start Docker, then create a local Redis container. If you already have Redis running, set `PRLENS_REDIS_URL` to its address instead.

```bash
docker run -d --name prlens-redis -p 127.0.0.1:6379:6379 redis:7
docker exec prlens-redis redis-cli ping
```

Expect `PONG`. To restart this container later, run `docker start prlens-redis`.

### 4. Start the worker and API

Use two terminals. In each one, change to the repository root and activate the virtual environment from step 1. Keep both processes running.

**Terminal 1 — Celery worker**

```bash
python -m celery -A app.celery_app:app worker --loglevel=info --pool=solo
```

The solo pool runs tasks sequentially and is suitable for this local walkthrough.

**Terminal 2 — FastAPI**

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to verify the API is running.

## Review a PR with the manual API

1. Choose a small PR that modifies a text file already present on the repository's default branch. For example, for `https://github.com/OWNER/REPO/pull/123`, use `https://github.com/OWNER/REPO` and PR number `123`.
2. In [Swagger UI](http://127.0.0.1:8000/docs), expand **POST /start_task/**, select **Try it out**, enter the request body below, and select **Execute**. For a private repository, add a `github_token` field containing a GitHub token with read access to the repository's pull requests and contents.
3. Copy the `task_id` from the response. Expand **GET /task_status/{task_id}/**, select **Try it out**, paste the ID, and select **Execute**. Repeat while the status is `PENDING`.
4. On `SUCCESS`, read `result.results`. Each entry contains a `filename` and a `results` string with Gemini's JSON findings. Each issue can include a type, line number, description, and suggestion. Check `result.error` for a GitHub or Gemini error even when the Celery status is `SUCCESS`. If Celery reports `FAILURE`, check the top-level `error` field and the worker logs.

```json
{
  "repo_url": "https://github.com/OWNER/REPO",
  "pr_number": 123
}
```

The manual API fetches file contents from the default branch. Findings can refer to an older version of a file, and fetching a newly added or renamed file can fail. Use the GitHub App pilot for a review anchored to the PR diff. Manual task results expire from Redis after 24 hours.

If you set `PRLENS_MANUAL_API_KEY` in `.env`, supply it as the `X-PRLens-API-Key` header on both API requests. With a GitHub webhook secret configured, the manual routes require a manual API key to be enabled.

## Set up automatic GitHub reviews

Complete the base installation above and use a checkout that contains `app/github_app/`. The pilot validates signed webhooks, stores deliveries in PostgreSQL, and uses Celery Beat to dispatch review jobs. The worker screens changed lines, checks usage limits, and asks Gemini for findings. A qualifying finding must match a line in the current diff before the worker can post it to GitHub.

### 1. Start PostgreSQL and initialize the schema

These credentials are for a local development database. If using an existing PostgreSQL instance, use its connection URL instead.

```bash
docker run -d --name prlens-postgres -p 127.0.0.1:5432:5432 -e POSTGRES_USER=prlens -e POSTGRES_PASSWORD=prlens-dev -e POSTGRES_DB=prlens postgres:16
docker exec prlens-postgres pg_isready -U prlens -d prlens
```

Wait for PostgreSQL to accept connections. Add this line to the same `.env` file and run the schema setup from your virtual environment.

```dotenv
PRLENS_DATABASE_URL=postgresql://prlens:prlens-dev@127.0.0.1:5432/prlens
```

```bash
python -m app.github_app.store
```

### 2. Register and install the GitHub App

1. Set up a public HTTPS endpoint that forwards to the local API on port `8000`. For example, install `cloudflared` and run `cloudflared tunnel --url http://127.0.0.1:8000` in a separate terminal. Use the HTTPS URL it prints. If you ran the manual walkthrough, stop the API before opening the tunnel and restart it in step 3 after configuring the webhook secret. [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
2. In GitHub, open **Settings → Developer settings → GitHub Apps → New GitHub App**. Enter an app name and homepage URL. Enable webhooks, set the webhook URL to your public HTTPS endpoint followed by `/webhooks/github`, and set a webhook secret.
3. Under **Repository permissions**, give **Pull requests** **Read and write** access. Under **Subscribe to events**, select **Pull request**, then create the app.
4. Copy the numeric **App ID** and generate a private key. Store the downloaded PEM file outside the repository. On the app's settings page, use **Install App** to install it on a test repository you control. [Register a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) · [Generate a private key](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps#generating-private-keys) · [Install your app](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)

### 3. Configure and start the review processes

Add the following to `.env`, replacing the examples with your app's values. Use the same webhook secret you entered in GitHub. For the private key, use an absolute path such as `C:/keys/prlens.private-key.pem` on Windows or `/home/dev/keys/prlens.private-key.pem` on Linux.

```dotenv
PRLENS_GITHUB_APP_ID=123456
PRLENS_GITHUB_PRIVATE_KEY_FILE=/path/to/prlens.private-key.pem
PRLENS_GITHUB_WEBHOOK_SECRET=replace-with-your-webhook-secret
PRLENS_GITHUB_BOT_LOGIN=your-app-slug[bot]
```

Use the app's URL slug followed by `[bot]` as the bot login. PRLens uses it to find an existing review after an uncertain publication attempt. To allow the pilot to send a private repository's diffs to Gemini, add its numeric repository ID to `PRLENS_GEMINI_PRIVATE_REPOSITORY_IDS`. Both this setting and `PRLENS_DISABLED_REPOSITORY_IDS` accept comma-separated IDs. The disabled list takes precedence.

Restart the API and worker from step 4 of the base setup so they load the new configuration. In a third terminal, activate the same virtual environment and start one Celery Beat scheduler.

```bash
python -m celery -A app.celery_app:app beat --loglevel=info
```

Beat checks for pending deliveries every five seconds. The default estimated model budget is USD 2 per installation per month (`PRLENS_MONTHLY_BUDGET_MICRODOLLARS=2000000`), with up to 12 model calls across revisions of a PR (`PRLENS_MAX_CALLS_PER_PR=12`). To use another model, set `PRLENS_REVIEW_MODEL` and provide that model's input and output prices in USD per million tokens through `PRLENS_INPUT_USD_PER_MILLION` and `PRLENS_OUTPUT_USD_PER_MILLION`. The pilot expects a model that supports structured output. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Structured output](https://ai.google.dev/gemini-api/docs/structured-output)

### 4. Verify a review

1. Open a Markdown-only PR in the test repository. In the GitHub App's **Recent deliveries**, look for a `pull_request` delivery with HTTP `202`. The worker should record `skipped_scope` and leave the PR without a bot review.
2. Open a PR with a small code change that introduces a clear bug, such as reversing the condition that rejects a negative quantity. Check the PR's **Conversation** or **Files changed** tab for an inline bot comment. Publication depends on Gemini returning a finding that passes validation.
3. If there is no comment, inspect the recorded outcome. `published` means GitHub accepted the review, and `quiet` means no qualifying comment remained after validation. Outcomes such as `skipped_budget` and `repository_disabled` explain why the worker stopped early.

```bash
docker exec prlens-postgres psql -U prlens -d prlens -c "SELECT pr_number, attempts, processed_at, outcome FROM github_delivery ORDER BY next_dispatch_at DESC LIMIT 10;"
```

`github_review_run` records the revision, coverage, and GitHub review ID when available. `github_model_usage` records estimated spend and token usage. See the [architecture notes](ARCHITECTURE.md) for the broader hosted design.

#### Example inline review

This screenshot from testing shows PRLens commenting on a changed FastAPI route and suggesting that the developer restore `ChatResponse` as the response model.

![PRLens bot posting an inline comment and suggested fix on a GitHub pull request](docs/images/github-app-review.png)

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The API cannot connect to Redis | Run `docker exec prlens-redis redis-cli ping`. Make sure the worker and API use the same `PRLENS_REDIS_URL`. |
| A manual task stays `PENDING` | Check the worker is running and connected to Redis, verify the task ID, and check whether the 24-hour result window has elapsed. |
| The manual API reports a GitHub error | Verify the repo URL and PR number. For private access or rate limits, supply a token with pull request and contents read permissions. For a file lookup failure, try a PR that modifies a file already on the default branch. |
| The manual API returns `401` or `404` | Supply the configured `X-PRLens-API-Key`. With a webhook secret set and no manual API key, the routes return `404`. |
| A GitHub delivery returns `401` or `503` | For `401`, check that the webhook secrets match. For `503`, check PostgreSQL and run the schema setup. After fixing the issue, redeliver the event from GitHub's **Recent deliveries**. |
| GitHub gets `202` but the delivery stays unprocessed | Check Beat, the worker, and their PostgreSQL and Redis configuration. A `202` acknowledges storage of the delivery. |
| Gemini requests fail | Check `GEMINI_API_KEY`, model availability, and the project's quota. Inspect the manual task result or the GitHub review worker logs. |

## Project layout

| Path | Role |
| --- | --- |
| `app/main.py` | FastAPI entry point and manual review endpoints |
| `app/celery_app.py` | Celery configuration, Redis connection, and Beat schedule |
| `app/tasks.py` and `app/utils/` | Manual review task, GitHub file fetching, and Gemini client |
| `app/github_app/` | Webhook handler, GitHub App authentication, diff analysis, review publication, and database schema |
| `requirements.txt` | Python dependencies |
