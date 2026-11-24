import asyncio
import html
import logging
import os
import time

import aiohttp
from aiohttp import web

import config
from state import ERRORS, add_error, START_TIME, STATS, human_time

log = logging.getLogger("web")
TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
TEST_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "test.html")
bot_client = None
storage_client = None
_last_test = {"t": 0}
bot_info = {"username": None}


def render(**ctx):
    template = TEST_TEMPLATE if "body" in ctx else TEMPLATE
    with open(template, encoding="utf-8") as f:
        page = f.read()
    for k, v in ctx.items():
        page = page.replace("{{" + k + "}}", str(v))
    return page


def snapshot():
    connected = bool(bot_client and bot_client.is_connected)
    return {
        "status": "running",
        "bot_connected": connected,
        "bot_username": bot_info["username"],
        "uptime": human_time(time.time() - START_TIME),
        "uptime_seconds": int(time.time() - START_TIME),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(START_TIME)),
        "active_jobs": STATS["active"],
        "uploads": STATS["uploads"],
        "downloads": STATS["downloads"],
        "error_count": STATS["errors"],
        "storage": f"IDrive e2 / {config.E2_BUCKET}" if config.e2_configured() else "not configured",
        "errors": list(ERRORS)[::-1],
    }


async def dashboard(_):
    s = snapshot()
    if s["errors"]:
        rows = "".join(
            f"<tr><td>{html.escape(e['time'])}</td><td>{html.escape(e['error'])}</td></tr>"
            for e in s["errors"]
        )
    else:
        rows = '<tr><td colspan="2" class="ok">No errors 🎉</td></tr>'
    page = render(
        conn_class="on" if s["bot_connected"] else "off",
        conn_text="Connected" if s["bot_connected"] else "Disconnected",
        username=html.escape(f"@{s['bot_username']}" if s["bot_username"] else "-"),
        uptime=s["uptime"],
        started=s["started_at"],
        active=s["active_jobs"],
        uploads=s["uploads"],
        downloads=s["downloads"],
        error_count=s["error_count"],
        storage=s["storage"],
        error_rows=rows,
    )
    return web.Response(text=page, content_type="text/html")


async def api_status(_):
    return web.json_response(snapshot())


async def api_errors(_):
    return web.json_response(list(ERRORS)[::-1])


async def test_storage(_):
    """Checks IDrive e2 credentials: open bucket, write, create link, delete."""
    if time.time() - _last_test["t"] < 10:
        return web.json_response({"ok": False, "message": "Please wait 10 seconds between tests."})
    _last_test["t"] = time.time()
    t0 = time.time()
    if not config.e2_configured():
        return web.json_response({
            "ok": False,
            "message": "❌ Not configured: set E2_ENDPOINT, E2_ACCESS_KEY, E2_SECRET_KEY and E2_BUCKET.",
        })
    try:
        steps = await storage_client.test()
    except Exception as e:
        steps = [{"step": "Connect", "ok": False, "detail": str(e)}]
    ok = all(s["ok"] for s in steps)
    if not ok:
        add_error("IDrive e2 test: " + "; ".join(s["detail"] for s in steps if not s["ok"]))
    return web.json_response({"ok": ok, "account": config.E2_BUCKET, "seconds": round(time.time() - t0, 2), "steps": steps})


async def test_page(request):
    """Plain link version of the connection test: open /test and read the result."""
    resp = await test_storage(request)
    import json as _json
    d = _json.loads(resp.text)
    lines = []
    head = d.get("message") or (("✅ IDrive e2 works" if d["ok"] else "❌ IDrive e2 test failed") + f" · bucket: {d.get('account')} · {d.get('seconds')}s")
    lines.append(f'<div class="step {"pass" if d["ok"] else "fail"}">{html.escape(head)}</div>')
    for s in d.get("steps", []):
        lines.append(
            f'<div class="step {"pass" if s["ok"] else "fail"}">{"✅" if s["ok"] else "❌"} '
            f'{html.escape(s["step"])}: {html.escape(str(s["detail"]))}</div>'
        )
    page = render(body="".join(lines))
    return web.Response(text=page, content_type="text/html")


async def health(_):
    return web.Response(text="ok")


async def keep_alive():
    """Pings our own public URL so Render's free plan does not put us to sleep."""
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if not url:
        log.info("No RENDER_EXTERNAL_URL/APP_URL set; keep-alive ping disabled")
        return
    url = url.rstrip("/") + "/health"
    while True:
        await asyncio.sleep(240)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    log.debug("keep-alive %s", r.status)
        except Exception as e:
            log.warning("keep-alive failed: %r", e)


async def watch_bot():
    """Fetch the bot username once connected, retry if it fails."""
    while not bot_info["username"]:
        try:
            if bot_client and bot_client.is_connected:
                bot_info["username"] = (await bot_client.get_me()).username
                break
        except Exception:
            pass
        await asyncio.sleep(5)


async def start_web(client, storage):
    global bot_client, storage_client
    bot_client = client
    storage_client = storage
    app = web.Application()
    app.router.add_get("/", dashboard)
    app.router.add_get("/health", health)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/errors", api_errors)
    app.router.add_post("/api/test-storage", test_storage)
    app.router.add_get("/test", test_page)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("Dashboard on port %s", config.PORT)
    asyncio.create_task(keep_alive())
    asyncio.create_task(watch_bot())
    return runner
