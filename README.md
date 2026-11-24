# Telegram ⇄ IDrive e2 Bot

A Telegram bot that uploads files you send it to IDrive e2 (S3 compatible storage) and
returns a share link and a download link.

## Features
- Telegram → IDrive e2 upload (multipart, 8 parallel threads) with live progress
- Share link + forced-download link (time limited, up to 7 days)
- Direct `http(s)` link → Telegram, and `/get <key>` bucket file → Telegram
- `/folder <name>` to choose a folder inside the bucket
- Web dashboard: Telegram connection, running time, job counters, recent errors,
  and a **Test IDrive e2 connection** button (open bucket, write, link, delete)
- Self keep-alive ping for Render, startup retries, Dockerfile + `render.yaml`

## Setup
1. `API_ID` / `API_HASH` from https://my.telegram.org, `BOT_TOKEN` from @BotFather.
2. In IDrive e2: create a bucket, create an access key (read/write), and copy the
   endpoint (looks like `https://xxxx.va.idrivee2-xx.com`).
3. Copy `.env.example` to `.env`, fill it in, then:
```bash
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)
python bot.py
```
Open http://localhost:10000 and press **Test IDrive e2 connection**.

## Deploy on Render
Push to GitHub → New → Blueprint (`render.yaml`) → fill the secret variables → deploy.
The bot pings its own `/health` every 4 minutes (`RENDER_EXTERNAL_URL`, set by Render).

## Notes
- Telegram limits: bots can send up to 2 GB per file (4 GB if the uploading account has
  Premium). Files from IDrive e2 / links are sent to Telegram up to 2 GB.
- Share/download links are presigned URLs and expire (max 7 days). The file stays in your bucket.
- Render free plan has small disk/RAM; use a paid plan or VPS for multi-GB files.
- The dashboard is public to anyone with the URL; set `ALLOWED_USERS` to restrict the bot.
