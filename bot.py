import asyncio
import logging
import os
import shutil
import time
import uuid

from pyrogram import Client, filters, idle
from pyrogram.types import Message

import config
import web as dashboard
import aiohttp
from storage import E2, friendly_error
from state import START_TIME, STATS, ErrorCapture, add_error, human_size, human_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger().addHandler(ErrorCapture())
log = logging.getLogger("bot")

USER_PREFIX = {}  # user_id -> folder (key prefix) inside the bucket
e2 = E2()
sem = asyncio.Semaphore(config.MAX_PARALLEL)

app = Client(
    "e2_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workdir=".",
)


# ---------- helpers ----------
def bar(done, total, width=14):
    pct = done / total if total else 0
    filled = int(width * pct)
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct * 100:.1f}%"


class Progress:
    """Throttled progress updater that edits one status message."""

    def __init__(self, msg, title):
        self.msg, self.title = msg, title
        self.last = 0
        self.start = time.time()

    async def __call__(self, done, total):
        now = time.time()
        if now - self.last < 4 and done != total:
            return
        self.last = now
        speed = done / max(now - self.start, 0.1)
        text = (
            f"{self.title}\n{bar(done, total)}\n"
            f"{human_size(done)} / {human_size(total)}\n"
            f"Speed: {human_size(speed)}/s"
        )
        try:
            await self.msg.edit_text(text)
        except Exception:
            pass


def allowed(message: Message):
    if not config.ALLOWED_USERS:
        return True
    return message.from_user and message.from_user.id in config.ALLOWED_USERS


def get_media(message: Message):
    for kind in ("document", "video", "audio", "voice", "animation", "video_note"):
        media = getattr(message, kind, None)
        if media:
            return media
    if message.photo:
        return message.photo
    return None


# ---------- commands ----------
@app.on_message(filters.command(["start", "help"]) & filters.private)
async def start(_, m: Message):
    await m.reply_text(
        "👋 **IDrive e2 Bot**\n\n"
        "• Send me any file (up to 4 GB) → I upload it to IDrive e2 and give you "
        "a share link and a download link.\n"
        "• Send me a direct `http(s)://` link → I download it and send the file here.\n"
        "• `/get <file key>` → send a file from your bucket to this chat.\n\n"
        "/folder `<name>` – upload into a folder of your bucket\n"
        "/folder – show current folder, `/folder reset` to clear\n"
        "/status – bot status and uptime"
    )


@app.on_message(filters.command("folder") & filters.private)
async def folder(_, m: Message):
    uid = m.from_user.id
    if len(m.command) < 2:
        cur = USER_PREFIX.get(uid, config.E2_PREFIX) or "bucket root"
        return await m.reply_text(f"📁 Current folder: `{cur}`")
    arg = m.command[1].strip().strip("/")
    if arg.lower() == "reset":
        USER_PREFIX.pop(uid, None)
        return await m.reply_text("📁 Folder reset to the default.")
    USER_PREFIX[uid] = arg
    await m.reply_text(f"📁 Uploads will now go to folder `{arg}`.")


@app.on_message(filters.command("status") & filters.private)
async def status(_, m: Message):
    await m.reply_text(
        "🟢 **Bot is running**\n"
        f"⏱ Uptime: `{human_time(time.time() - START_TIME)}`\n"
        f"⚙️ Active jobs: `{STATS['active']}`\n"
        f"⬆️ Uploads to IDrive e2: `{STATS['uploads']}`\n"
        f"⬇️ Sent to Telegram: `{STATS['downloads']}`\n"
        f"🗄 IDrive e2: `{'configured: ' + config.E2_BUCKET if config.e2_configured() else 'NOT configured'}`"
    )


class ThreadProgress:
    """boto3 calls back from worker threads with bytes-just-sent. We only
    count bytes here; an async task shows them in the Telegram message."""

    def __init__(self, total):
        self.total, self.done = total, 0

    def __call__(self, n):
        self.done += n


async def show_progress(status_msg, title, tp):
    start = time.time()
    while True:
        await asyncio.sleep(4)
        speed = tp.done / max(time.time() - start, 0.1)
        try:
            await status_msg.edit_text(
                f"{title}\n{bar(tp.done, tp.total)}\n"
                f"{human_size(tp.done)} / {human_size(tp.total)}\n"
                f"Speed: {human_size(speed)}/s"
            )
        except Exception:
            pass


async def edit_no_preview(msg, text):
    """Edit a message without a link preview, on both old and new Kurigram/Pyrogram."""
    try:
        from pyrogram.types import LinkPreviewOptions
        await msg.edit_text(text, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except ImportError:
        await msg.edit_text(text, disable_web_page_preview=True)
    except TypeError:
        await msg.edit_text(text, disable_web_page_preview=True)


def need_e2():
    return None if config.e2_configured() else (
        "❌ IDrive e2 is not configured. Set E2_ENDPOINT, E2_ACCESS_KEY, E2_SECRET_KEY and E2_BUCKET."
    )


# ---------- Telegram -> IDrive e2 ----------
@app.on_message(
    (filters.document | filters.video | filters.audio | filters.voice
     | filters.photo | filters.animation | filters.video_note)
    & filters.private
)
async def tg_to_e2(_, m: Message):
    if not allowed(m):
        return await m.reply_text("⛔ You are not allowed to use this bot.")
    if need_e2():
        return await m.reply_text(need_e2())
    media = get_media(m)
    size = getattr(media, "file_size", 0) or 0
    if size > config.MAX_FILE_SIZE:
        return await m.reply_text(
            f"❌ File too large ({human_size(size)}). Limit: {human_size(config.MAX_FILE_SIZE)}."
        )

    status_msg = await m.reply_text("⏳ Queued...")
    job_dir = os.path.join(config.DOWNLOAD_DIR, uuid.uuid4().hex)
    os.makedirs(job_dir, exist_ok=True)
    async with sem:
        STATS["active"] += 1
        try:
            path = await m.download(
                file_name=job_dir + os.sep,
                progress=Progress(status_msg, "⬇️ Downloading from Telegram"),
            )
            total = os.path.getsize(path)
            tp = ThreadProgress(total)
            upd = asyncio.create_task(show_progress(status_msg, "⬆️ Uploading to IDrive e2", tp))
            try:
                res = await e2.upload(path, tp, USER_PREFIX.get(m.from_user.id))
            finally:
                upd.cancel()
            STATS["uploads"] += 1
            await edit_no_preview(
                status_msg,
                "✅ **Upload complete**\n\n"
                f"📄 `{res['name']}`\n"
                f"📦 {human_size(total)}\n\n"
                f"🔗 **Share link:** {res['share_link']}\n\n"
                f"⬇️ **Download link:** {res['download_link']}\n\n"
                f"🔑 Key: `{res['key']}`\n"
                f"⏳ Links valid for {config.LINK_EXPIRY_DAYS} day(s)",
            )
        except Exception as e:
            log.exception("upload failed")
            await status_msg.edit_text(f"❌ Failed: `{friendly_error(e)}`")
        finally:
            STATS["active"] -= 1
            shutil.rmtree(job_dir, ignore_errors=True)


# ---------- IDrive e2 key -> Telegram ----------
@app.on_message(filters.command("get") & filters.private)
async def e2_to_tg(_, m: Message):
    if not allowed(m):
        return await m.reply_text("⛔ You are not allowed to use this bot.")
    if need_e2():
        return await m.reply_text(need_e2())
    if len(m.command) < 2:
        return await m.reply_text("Usage: `/get <file key>` (the key is shown after each upload)")
    key = m.text.split(None, 1)[1].strip()
    status_msg = await m.reply_text("🔎 Looking for the file...")
    job_dir = os.path.join(config.DOWNLOAD_DIR, uuid.uuid4().hex)
    async with sem:
        STATS["active"] += 1
        try:
            total = await e2.size(key)
            tp = ThreadProgress(total)
            upd = asyncio.create_task(show_progress(status_msg, "⬇️ Downloading from IDrive e2", tp))
            try:
                path = await e2.download(key, os.path.join(job_dir, os.path.basename(key)), tp)
            finally:
                upd.cancel()
            await status_msg.edit_text("⬆️ Sending to Telegram...")
            await m.reply_document(
                path, caption=f"`{os.path.basename(key)}`",
                progress=Progress(status_msg, "⬆️ Sending to Telegram"),
            )
            STATS["downloads"] += 1
            await status_msg.delete()
        except Exception as e:
            log.exception("e2 get failed")
            await status_msg.edit_text(f"❌ Failed: `{friendly_error(e)}`")
        finally:
            STATS["active"] -= 1
            shutil.rmtree(job_dir, ignore_errors=True)


# ---------- direct link -> Telegram ----------
@app.on_message(filters.text & filters.regex(r"^https?://\S+$") & filters.private)
async def url_to_tg(_, m: Message):
    if not allowed(m):
        return await m.reply_text("⛔ You are not allowed to use this bot.")
    url = m.text.strip()
    status_msg = await m.reply_text("🔎 Checking link...")
    job_dir = os.path.join(config.DOWNLOAD_DIR, uuid.uuid4().hex)
    os.makedirs(job_dir, exist_ok=True)
    async with sem:
        STATS["active"] += 1
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=600)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(url) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", 0))
                    if total > config.MAX_FILE_SIZE:
                        raise RuntimeError(f"File too large ({human_size(total)})")
                    name = os.path.basename(r.url.path) or "file"
                    cd = r.headers.get("Content-Disposition", "")
                    if "filename=" in cd:
                        name = cd.split("filename=")[-1].strip('" ;')
                    name = E2.clean_name(name)
                    path = os.path.join(job_dir, name)
                    prog = Progress(status_msg, f"⬇️ Downloading {name}")
                    done = 0
                    with open(path, "wb") as f:
                        async for chunk in r.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                            done += len(chunk)
                            await prog(done, total or done)
            await status_msg.edit_text(f"⬆️ Sending {name} to Telegram...")
            await m.reply_document(
                path, caption=f"`{name}`",
                progress=Progress(status_msg, f"⬆️ Sending {name}"),
            )
            STATS["downloads"] += 1
            await status_msg.delete()
        except Exception as e:
            log.exception("url download failed")
            await status_msg.edit_text(f"❌ Failed: `{e}`")
        finally:
            STATS["active"] -= 1
            shutil.rmtree(job_dir, ignore_errors=True)


async def main():
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    runner = await dashboard.start_web(app, e2)  # dashboard starts first (Render needs the port)
    while True:  # keep trying so the bot never stays down
        try:
            await app.start()
            break
        except Exception as e:
            log.error("Bot failed to start: %r (retrying in 15s)", e)
            await asyncio.sleep(15)
    log.info("Bot started")
    await idle()
    await app.stop()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
