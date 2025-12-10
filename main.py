import discord
import aiohttp
import asyncio
import json
import config
import os
import tempfile
import shutil
from datetime import datetime

intents = discord.Intents.default()
client = discord.Client(intents=intents)

POSTED_FILE = "posted.json"
DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/920731720645500978/1350138518608937081/6794d187-3c79-4a3c-83bb-c9d08e768fa1.webp"

API_URL = "https://scriptblox.com/api/script/fetch"
CHECK_INTERVAL = 10
MAX_RETRIES = 3

WEBHOOK_URL = config.WEBHOOK_URL
http_session = None

posted_ids = set()
is_checking = False


# ======================================================
# LOGGER
# ======================================================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ======================================================
# SAFE LOAD / SAVE posted.json
# ======================================================
def load_posted_ids():
    global posted_ids
    if not os.path.exists(POSTED_FILE):
        posted_ids = set()
        return

    try:
        with open(POSTED_FILE, "r") as f:
            posted_ids = set(json.load(f))
    except:
        log("posted.json damaged → recreating")
        posted_ids = set()
        save_posted_ids()


def save_posted_ids():
    fd, temp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(list(posted_ids), f, indent=4)
        shutil.move(temp_path, POSTED_FILE)
    except:
        log("Error saving posted.json")
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ======================================================
# CLEANUP
# ======================================================
def cleanup_removed_scripts(live_ids):
    removed = [sid for sid in posted_ids if sid not in live_ids]
    if not removed:
        return

    for sid in removed:
        posted_ids.discard(sid)

    save_posted_ids()
    log(f"Cleanup removed {len(removed)} deleted scripts")


# ======================================================
# SCRIPT VALIDATION
# ======================================================
def script_is_broken(script_data):
    script = script_data.get("script", "") or ""
    if len(script) < 5:
        return True

    banned = ["error", "nil", "invalid", "fail", "patched"]
    low = script.lower()
    return any(bad in low for bad in banned)


# ======================================================
# IMAGE
# ======================================================
def get_image_url(script):
    game = script.get("game", {})
    image = game.get("imageUrl", "")

    if image:
        if image.startswith("/"):
            return "https://scriptblox.com" + image
        return image
    return DEFAULT_IMAGE


def format_date(ts):
    if not ts:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y • %I:%M %p")
    except:
        return "Unknown"


# ======================================================
# SESSION
# ======================================================
async def create_session():
    global http_session
    if http_session is None:
        http_session = aiohttp.ClientSession()
        log("HTTP session opened")


async def close_session():
    global http_session
    if http_session:
        await http_session.close()
        http_session = None
        log("HTTP session closed")


# ======================================================
# FETCH SCRIPTS
# ======================================================
async def fetch_scripts():
    await create_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with http_session.get(API_URL) as r:
                if r.status != 200:
                    await asyncio.sleep(attempt)
                    continue
                data = await r.json()
                result = data.get("result", {})
                return result.get("scripts", [])
        except Exception as e:
            log(f"API error {attempt}/{MAX_RETRIES}: {e}")
            await asyncio.sleep(attempt)

    log("API FAILED after retries")
    return []


# ======================================================
# WEBHOOK POST
# ======================================================
async def webhook_send(script):
    code_raw = script.get("script", "")
    code_short = code_raw[:1900]  # Discord limit safe

    embed = {
        "title": f"🎮 {script.get('game', {}).get('name', 'Unknown Game')}",
        "description": script.get("title", "Unknown Script"),
        "color": 0xFF0000,
        "thumbnail": {"url": get_image_url(script)},
        "fields": [
            {"name": "⌛ Created", "value": format_date(script.get("createdAt")), "inline": True},
            {"name": "📜 Script", "value": f"```lua\n{code_short}\n```", "inline": False}
        ],
        "footer": {
            "text": script.get("game", {}).get("name", ""),
            "icon_url": get_image_url(script)
        }
    }

    await create_session()

    try:
        async with http_session.post(WEBHOOK_URL, json={"embeds": [embed]}) as res:
            return res.status in (200, 204)
    except Exception as e:
        log(f"Webhook send error: {e}")
        return False


# ======================================================
# PROCESSOR
# ======================================================
async def process_scripts():
    global is_checking
    if is_checking:
        return

    is_checking = True

    try:
        scripts = await fetch_scripts()
        live_ids = {s.get("_id") for s in scripts}

        cleanup_removed_scripts(live_ids)

        for s in scripts:
            sid = s.get("_id")
            if not sid or sid in posted_ids:
                continue

            if script_is_broken(s):
                continue

            ok = await webhook_send(s)
            if ok:
                posted_ids.add(sid)
                save_posted_ids()
                log(f"Posted: {s.get('title')}")

            await asyncio.sleep(0.4)

    except Exception as e:
        log(f"PROCESS ERROR: {e}")

    is_checking = False


# ======================================================
# LOOP
# ======================================================
async def main_loop():
    await client.wait_until_ready()
    await create_session()

    log("System running...")

    while True:
        await process_scripts()
        await asyncio.sleep(CHECK_INTERVAL)


# ======================================================
# DISCORD EVENTS
# ======================================================
@client.event
async def on_ready():
    log(f"Bot Online → {client.user}")

    await client.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="ScriptBlox 🔍")
    )

    load_posted_ids()
    log(f"Loaded {len(posted_ids)} scripts")

    asyncio.create_task(main_loop())


if __name__ == "__main__":
    client.run(config.TOKEN)
