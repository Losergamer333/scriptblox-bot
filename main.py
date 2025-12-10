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
intents.message_content = True

client = discord.Client(intents=intents)

POSTED_FILE = "posted.json"
DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/920731720645500978/1350138518608937081/6794d187-3c79-4a3c-83bb-c9d08e768fa1.webp"

API_URL = "https://scriptblox.com/api/script/fetch"
CHECK_INTERVAL = 10
MAX_RETRIES = 3

WEBHOOK_URL = config.WEBHOOK_URL  # NEW: ultra fast webhook output

is_checking = False
posted_ids = set()
http_session = None


# ==========================================================
# LOGGING
# ==========================================================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ==========================================================
# SAFE LOAD / SAVE posted.json
# ==========================================================
def load_posted_ids():
    global posted_ids
    if not os.path.exists(POSTED_FILE):
        posted_ids = set()
        return

    try:
        with open(POSTED_FILE, "r") as f:
            data = json.load(f)
        posted_ids = set(data if isinstance(data, list) else [])
    except Exception:
        log("WARN: posted.json corrupted → recreating")
        posted_ids = set()
        save_posted_ids()


def save_posted_ids():
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(list(posted_ids), f, indent=4)

        shutil.move(temp_path, POSTED_FILE)
    except Exception as e:
        log(f"ERROR saving posted.json: {e}")
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


# ==========================================================
# CLEANUP FOR REMOVED SCRIPTS
# ==========================================================
def cleanup_removed_scripts(live_ids):
    removed = [sid for sid in posted_ids if sid not in live_ids]
    if not removed:
        return

    for sid in removed:
        posted_ids.discard(sid)

    save_posted_ids()
    log(f"Cleanup: Removed {len(removed)} deleted/old scripts")


# ==========================================================
# SCRIPT VALIDATION
# ==========================================================
def script_is_broken(script_data):
    script = script_data.get("script", "") or ""
    patched = script_data.get("isPatched", False)

    if patched or len(script) < 5:
        return True

    banned = ["error", "nil", "invalid", "fail", "patched"]
    script_low = script.lower()

    return any(bad in script_low for bad in banned)


# ==========================================================
# GAME THUMBNAIL + FOOTER ICON
# ==========================================================
def get_image_url(script_data):
    game = script_data.get("game", {})
    image = game.get("imageUrl", "")

    if image:
        if image.startswith("/"):
            return f"https://scriptblox.com{image}"
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


# ==========================================================
# HTTP SESSION
# ==========================================================
async def create_session():
    global http_session
    http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    log("HTTP session created")


async def close_session():
    global http_session
    if http_session:
        await http_session.close()
        http_session = None
        log("HTTP session closed")


# ==========================================================
# FETCH SCRIPTS (WITH RETRY)
# ==========================================================
async def fetch_scripts():
    if not http_session:
        await create_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with http_session.get(API_URL) as res:
                if res.status != 200:
                    log(f"API status {res.status}")
                    await asyncio.sleep(attempt)
                    continue

                data = await res.json()
                result = data.get("result", {})
                return result.get("scripts", [])

        except Exception as e:
            log(f"API error attempt {attempt}/{MAX_RETRIES}: {e}")
            await asyncio.sleep(attempt)

    log("API FAILED after retries")
    return []


# ==========================================================
# WEBHOOK ULTRAFAST POSTING
# ==========================================================
async def webhook_send(script):
    if not WEBHOOK_URL:
        log("Webhook URL missing!")
        return False

    title = script.get("title", "Unknown Script")
    game = script.get("game", {}).get("name", "Unknown Game")
    code = script.get("script", "")
    created = format_date(script.get("createdAt"))
    image = get_image_url(script)

    data = {
        "embeds": [
            {
                "title": f"🎮 {game} 🎮",
                "description": title,
                "color": 0xFF0000,
                "thumbnail": {"url": image},
                "fields": [
                    {"name": "⌛ Created", "value": created, "inline": True},
                    {
                        "name": "📜 Script",
                        "value": "⬇️ Click **Copy Script** in Discord ⬇️",
                        "inline": False
                    }
                ],
                "footer": {"text": game, "icon_url": image}
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": "📋 Copy Script",
                        "custom_id": f"copy:{script['_id']}"
                    }
                ]
            }
        ]
    }

    try:
        async with http_session.post(WEBHOOK_URL, json=data) as res:
            return res.status in (200, 204)
    except Exception as e:
        log(f"Webhook error: {e}")
        return False


# ==========================================================
# DISCORD INTERACTION HANDLER FOR COPY SCRIPT
# ==========================================================
@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type.value != 3:  # Component press
        return

    custom = interaction.data.get("custom_id", "")
    if not custom.startswith("copy:"):
        return

    script_id = custom.split(":")[1]

    try:
        scripts = await fetch_scripts()
        for s in scripts:
            if s.get("_id") == script_id:
                code = s.get("script", "")
                await interaction.response.send_message(
                    f"```lua\n{code[:1950]}\n```",
                    ephemeral=True
                )
                return
    except:
        pass

    await interaction.response.send_message("Script not found.", ephemeral=True)


# ==========================================================
# MAIN PROCESSING
# ==========================================================
async def process_scripts():
    global is_checking

    if is_checking:
        return
    is_checking = True

    try:
        scripts = await fetch_scripts()

        live_ids = {s.get("_id") for s in scripts if s.get("_id")}

        # remove deleted
        cleanup_removed_scripts(live_ids)

        for s in scripts:
            sid = s.get("_id")
            if not sid or sid in posted_ids:
                continue

            if script_is_broken(s):
                continue

            if await webhook_send(s):
                posted_ids.add(sid)
                save_posted_ids()
                log(f"Posted {s.get('title', '')}")

            await asyncio.sleep(0.5)

    except Exception as e:
        log(f"PROCESS ERROR: {e}")

    finally:
        is_checking = False


# ==========================================================
# LOOP
# ==========================================================
async def main_loop():
    await client.wait_until_ready()
    await create_session()

    log(f"SYSTEM ACTIVE — checking every {CHECK_INTERVAL}s")

    while not client.is_closed():
        await process_scripts()
        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log(f"ONLINE → {client.user}")

    # ------------------------------------------------------
    # WATCHING STATUS
    # ------------------------------------------------------
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="ScriptBlox 🔍"
    )
    await client.change_presence(activity=activity)

    load_posted_ids()
    log(f"Loaded {len(posted_ids)} stored scripts")
    client.loop.create_task(main_loop())


@client.event
async def on_disconnect():
    await close_session()


if __name__ == "__main__":
    if not config.TOKEN:
        log("TOKEN missing")
    else:
        try:
            client.run(config.TOKEN)
        finally:
            if http_session:
                asyncio.get_event_loop().run_until_complete(close_session())
