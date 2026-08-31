# Tweegen GitHub Actions pipeline

A lightweight, database-free automation pipeline for:

1. Watching the Barca Universal RSS feed every 30 minutes.
2. Generating a tweet with Gemini, falling back to Groq when Gemini's daily quota is exhausted.
3. Maintaining a FIFO tweet queue in daily CSV files.
4. Posting at most one tweet at a time through Buffer, with at least 30 minutes between successful dispatches.

## Architecture

```text
Barca Universal RSS
        |
        | every 30 min
        v
RSS Watcher
        |
        v
DISCOVERED
        |
        | workflow_dispatch
        v
Tweet Generator
        |
        | Gemini -> daily quota -> Groq
        v
READY
        |
        | immediate trigger + 5 min heartbeat
        v
Tweet Dispatcher
        |
        | last post >= 30 min?
        v
Buffer shareNow
        |
        v
POSTED
```

## State

No database is used.

State is stored in the `automation-state` Git branch:

```text
logs/
  tweet_log_2026-08-31.csv
  tweet_log_2026-09-01.csv
```

CSV columns:

```text
id
title
url
published_datetime
status
tweet
llm_provider
discovered_datetime
tweet_generated_datetime
tweet_posted_datetime
```

The full article content is never saved. It exists only in memory while the generator runs.

## State machine

```text
DISCOVERED -> READY -> POSTED
```

If LLM generation fails, the item remains `DISCOVERED`.

If Buffer posting fails, the item remains `READY`.

## FIFO queue

READY rows are ordered by:

1. `published_datetime`
2. `discovered_datetime`

A dispatcher run posts at most one READY tweet.

## 30-minute rule

The dispatcher finds the latest non-empty `tweet_posted_datetime` across every CSV log.

It only posts when:

```text
now >= last_posted_datetime + 30 minutes
```

The dispatcher also runs every 5 minutes, so normal spacing will be roughly 30–35 minutes plus any GitHub scheduling delay.

## Important Buffer behavior

The dispatcher uses Buffer `createPost` with:

```text
mode = shareNow
schedulingType = automatic
```

The CSV is marked POSTED once Buffer accepts the `createPost` request.

There is deliberately no automatic retry around Buffer `createPost`: retrying an ambiguous network timeout could create a duplicate post because Buffer does not currently document a create-post idempotency key.

## Gemini / Groq routing

Default primary:

```text
gemini-3.6-flash
```

Default fallback:

```text
openai/gpt-oss-20b
```

Gemini temporary RPM/TPM 429 errors wait and retry.

A daily-quota error switches the remaining articles in that generator execution directly to Groq.

## Local setup

```bash
python -m venv .venv
```

Git Bash:

```bash
source .venv/Scripts/activate
```

Install:

```bash
pip install -r requirements.txt
```

Copy:

```text
.env.example -> .env
```

Fill in:

```text
GEMINI_API_KEY
GROQ_API_KEY
BUFFER_API_KEY
BUFFER_CHANNEL_ID
```

For local development, either put the prompt in `prompts/system_prompt.txt` or set `TWEET_SYSTEM_PROMPT` in `.env`.

For the public GitHub repository, keep only the placeholder prompt file and store the real prompt in the `TWEET_SYSTEM_PROMPT` GitHub Secret.

## GitHub repository setup

### 1. Push the application to `main`

The repo must contain the three workflow files under:

```text
.github/workflows/
```

### 2. Create the `automation-state` branch

Create a branch named:

```text
automation-state
```

from `main`.

It is fine if this branch initially contains the same files as main. The workflows only modify its `logs/` directory.

### 3. Add GitHub Actions secrets

Repository:

```text
Settings
-> Secrets and variables
-> Actions
-> New repository secret
```

Create:

```text
GEMINI_API_KEY
GROQ_API_KEY
BUFFER_API_KEY
BUFFER_CHANNEL_ID
TWEET_SYSTEM_PROMPT
```

`TWEET_SYSTEM_PROMPT` should contain your complete private Tweegen writing-style system prompt.

Never put API keys or the real system prompt in the public repository.

### 4. Allow workflow writes

Repository:

```text
Settings
-> Actions
-> General
-> Workflow permissions
```

Allow read/write workflow permissions if your repository/account settings require it.

The workflows also request least-required permissions explicitly.

### 5. Bootstrap BEFORE normal posting

The code includes a safety marker at:

```text
logs/.initialized
```

Until bootstrap creates this marker, scheduled RSS runs exit without discovering articles. This prevents the current 10 feed items from accidentally becoming tweets before setup is complete.

Go to:

```text
Actions
-> RSS Watcher
-> Run workflow
```

Set:

```text
bootstrap = true
```

Run it once.

This writes the current RSS items to the state CSV as already seen:

```text
status = POSTED
tweet = blank
tweet_posted_datetime = blank
```

These baseline rows do not affect the 30-minute posting clock.

### 6. Verify the state branch

After bootstrap, `automation-state` should contain something like:

```text
logs/tweet_log_YYYY-MM-DD.csv
```

### 7. Test RSS discovery

Manually run `RSS Watcher` with bootstrap=false.

If there is a new article, it will:

```text
add DISCOVERED
commit state
trigger Tweet Generator
```

### 8. Test generation

You can manually run `Tweet Generator`.

Any DISCOVERED rows will be processed.

Successful generation becomes:

```text
status = READY
tweet = generated text
llm_provider = gemini or groq
```

### 9. Test Buffer carefully

Before enabling real posting, consider connecting Buffer to a test/private account or temporarily modifying `posting_service.py` to a dry-run mode.

Once enabled, manually run `Tweet Dispatcher`.

It will post at most one READY tweet.

## Workflow schedules

RSS:

```cron
*/30 * * * *
```

Dispatcher:

```cron
*/5 * * * *
```

Tweet Generator has no cron schedule.

## GitHub workflow chaining

The RSS Watcher uses GitHub CLI with the repository `GITHUB_TOKEN` to send a `workflow_dispatch` to Tweet Generator.

Tweet Generator similarly dispatches Tweet Dispatcher.

`workflow_dispatch` is intentionally used because GitHub allows workflows triggered through `GITHUB_TOKEN` to create workflow runs for `workflow_dispatch` / `repository_dispatch`.

## Concurrency

All three workflows share:

```text
tweegen-state-writer
```

and queue rather than cancel each other.

This serializes state mutations and prevents two workflows from pushing conflicting CSV changes at the same time.

## Public repo

Keep:

```text
.env
API keys
tokens
credentials
```

out of Git.

The provided `.gitignore` excludes `.env`.

The system prompt is public if committed to a public repository. If you want the prompt private, store it as a GitHub Secret instead and write it to a temporary file during the generator workflow.
