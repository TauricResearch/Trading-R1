# Trading-R1 manual

Each command takes `--help`, which also lists the less common commands and options.

## Commands

| Command | Does |
|---|---|
| `trading-r1 [QUESTION]` | Open the terminal. `--profile NAME`, `--mode MODE` |
| `trading-r1 serve` | Run the backend alone, in the foreground |
| `trading-r1 export ID` | Write a report. `--format pdf\|md\|html`, `-o FILE`, `--send CHANNELS` |
| `trading-r1 sessions list`, `delete ID` | Your sessions; delete one here and its copy in your account |
| `trading-r1 channels add KIND`, `list`, `test NAME`, `remove NAME` | Your channels |
| `trading-r1 jobs add`, `list`, `run ID`, `runs ID`, `remove ID` | Your scheduled reports, and what each run did |
| `trading-r1 secrets set NAME`, `list`, `delete NAME` | Stored keys, passwords and tokens |
| `trading-r1 daemon install`, `status`, `logs`, `uninstall` | The background service |
| `trading-r1 sync status`, `on`, `off`, `pull [ID]` | Session sync; `pull` gets a session back from your account |

`sessions`, `jobs` and most of `sync` need a running backend: the terminal's, the service, or
`trading-r1 serve`.

## Settings

Open **Settings** from the palette.

| Setting | Does | Default |
|---|---|---|
| Verification | Check the report's figures against the records and recompute the worked-out ones. Takes about twice the time and tokens. | off |
| Source review | Check the report against its sources. Ignored when Verification is on. | off |
| Default mode | The mode the app opens in. | auto |
| Auto-compact | Compact the context before it fills the model's window. | on |
| Show reasoning | The model's thinking in the transcript. | on |
| Tool details | `brief` shows a tool call's values; `full` shows every argument by name. | brief |
| Side panel | The plan, data records and sources beside the transcript. | on |
| Also save as Markdown, HTML page | Files saved beside a report's PDF. | Markdown on, page off |
| Session sync | Copy your sessions to your Tauric account. | on |
| Remembered notes… | View or delete what the agent has noted about you. | |

## Reports, channels and jobs

`TR1_REPORTS_DIR` changes the reports folder. Reports the PDF font cannot draw, such as Chinese or
Japanese, become an HTML page.

```bash
trading-r1 channels add telegram
trading-r1 channels add email --preset gmail --to me@example.com   # asks for the app password
trading-r1 channels add folder --path briefs
trading-r1 channels test telegram
trading-r1 export SESSION_ID --format pdf -o report.pdf            # or md, html; --turn N
trading-r1 export SESSION_ID --send telegram,email
```

For Gmail, turn on 2-Step Verification and make an app password in your Google account. Other
providers take `--host`, `--port` and `--username`.

```bash
trading-r1 jobs add --name "Morning brief" --every weekdays --time 08:00 --tz America/New_York \
    --prompt "What moved my watchlist overnight? NVDA, AAPL, MSFT." --deliver email,telegram
trading-r1 jobs add --name "Weekly review" --every week --weekday fri --time 17:00 \
    --prompt "Review my portfolio's week." --deliver email
trading-r1 jobs list
```

`--cron` takes any five-field expression, and `--at 2027-01-04T08:00:00-05:00` runs once.
`--profile` and `--mode` pin the model and mode. Jobs run unattended, so they cannot ask you a
question.

## Session sync

Sync is on by default with a Tauric key, whichever model you use: your sessions, with their
questions, model requests and responses and tool results, are copied to your account at
`api.tauric.ai`. Turn it off under *Settings > Session sync* or with `trading-r1 sync off`.

## Your data and keys

- `trading-r1 secrets set` and the key forms store keys in `~/.trading-r1/credentials.json`, and
  `trading-r1 daemon install` copies them to `~/.trading-r1/daemon.env`. Only you can read these
  files, but your own processes and any backup tool that copies the folder can too.
- Sessions, notes and settings are plain files under `~/.trading-r1/`.
- The backend listens on `127.0.0.1` by default, and every route but its liveness check needs a
  bearer token, kept in a file only you can read. Do not expose it to a network you do not trust.
- The agent can read web pages and call the data API on your behalf, and sends what it reads to
  the model you selected.
- Reports go to your channels through those services (your mail provider, Telegram). With `reply`
  on, the private chat paired with your Telegram bot can ask the agent questions.

## Configuration

Keys are read from the environment, then from `~/.trading-r1/credentials.json`. Settings, channels
and model profiles are in the same folder (`TR1_HOME` moves it).

| Variable | Does |
|---|---|
| `TR1_PROFILE` | The model to start on |
| `TR1_REPORTS_DIR` | The reports folder |
| `TR1_SKILLS_DIR` | One more folder of skills |
| `TR1_PORT` | The backend's port |
| `TR1_VLLM_BASE_URL` | Your vLLM server's address, such as `http://gpu-box:8000/v1` |
| `TR1_<NAME>_BASE_URL`, `TR1_<NAME>_MODEL`, `TR1_<NAME>_API_KEY` | One model's address, model and key |

To add a model, give it a profile in `~/.trading-r1/providers.json`:

```json
{"profiles": {"my-qwen": {"extends": "vllm", "base_url": "http://gpu-box:9000/v1",
                          "reader_profile": "my-qwen"}}}
```

Profiles with their own address send no key unless they name one (`"api_key_env": "MY_KEY"`). Those
that extend a hosted profile keep its key, so name the key they should send.

## Adding a skill

Skills are `SKILL.md` files that tell the agent how to do a kind of work with its tools. Put one in
`./skills/my-recipe/SKILL.md` under the folder you start from, or in `TR1_SKILLS_DIR`. Skills with a
built-in's name replace it.
