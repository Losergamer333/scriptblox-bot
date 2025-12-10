# == FULLY OPTIMIZED SCRIPTBLOX DISCORD BOT ==
# aiohttp • safe JSON saves • game thumbnails • stable API handling

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
DEFAULT_IMAGE = (
    "https://cdn.discordapp.com/attachments/920731720645500978/1350138518608937081/6794d187-3c79-4a3c-83bb-c9d08e768fa1.webp"
)

API_URL = "https://scriptblox.com/api/script/fetch"
CHECK_INTERVAL = 10
MAX_RETRIES = 3

is_checking = False
posted_ids = set()
http_session = None


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
# SCRIPT VALIDATION
# ==========================================================

def script_is_broken(script_data):
    script = script_data.get("script", "") or ""
    patched = script_data.get("isPatched", False)

    if patched:
        return True

    if len(script) < 5:
        return True

    banned = ["error", "nil", "invalid", "fail", "patched"]
    script_low = script.lower()

    return any(bad in script_low for bad in banned)


# ==========================================================
# GAME THUMBNAIL & FOOTER ICON
# ==========================================================

def get_image_url(script_data):
    game = script_data.get("game", {})
    image = game.get("imageUrl", "")

    if image:
        if image.startswith("/"):
            return f"https://scriptblox.com{image}"
        return image

    return DEFAULT_IMAGE


# Timestamp Formatter
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
# FETCH SCRIPTS (with retry system)
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
# DISCORD BUTTONS FOR COPY SCRIPT
# ==========================================================

class ScriptButtons(discord.ui.View):
    def __init__(self, script_code):
        super().__init__(timeout=None)
        self.script_code = script_code

    @discord.ui.button(label="📋 Copy Script", style=discord.ButtonStyle.green)
    async def copy(self, interaction, button):
        if len(self.script_code) > 1950:
            await interaction.response.send_message(
                f"```lua\n{self.script_code[:1950]}\n```\n*(Script truncated)*",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"```lua\n{self.script_code}\n```",
                ephemeral=True
            )


# ==========================================================
# SEND EMBED
# ==========================================================

async def send_embed(channel, script):
    title = script.get("title", "Unknown Script")
    game = script.get("game", {}).get("name", "Unknown Game")
    code = script.get("script", "")
    created = format_date(script.get("createdAt"))
    image = get_image_url(script)

    embed = discord.Embed(
        title=f"🎮 {game} 🎮",
        description=title,
        color=0xFF0000
    )

    embed.add_field(name="⌛ Created", value=created, inline=True)
    embed.add_field(
        name="📜 Script",
        value="⬇️ Click **Copy Script** below ⬇️",
        inline=False
    )

    embed.set_thumbnail(url=image)
    embed.set_footer(text=game, icon_url=image)

    try:
        await channel.send(embed=embed, view=ScriptButtons(code))
        return True
    except Exception as e:
        log(f"ERROR sending embed: {e}")
        return False


# ==========================================================
# MAIN SCRIPT PROCESSING
# ==========================================================

async def process_scripts():
    global is_checking

    if is_checking:
        return
    is_checking = True

    try:
        channel = client.get_channel(config.CHANNEL_ID)
        if not channel:
            log("CHANNEL NOT FOUND")
            return

        scripts = await fetch_scripts()

        for s in scripts:
            sid = s.get("_id")
            if not sid:
                continue

            if sid in posted_ids:
                continue

            if script_is_broken(s):
                log(f"Skipping broken script {sid}")
                continue

            if await send_embed(channel, s):
                posted_ids.add(sid)
                save_posted_ids()
                log(f"Posted {s.get('title','')}")
            
            await asyncio.sleep(1)

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

    log(f"Bot started — checking every {CHECK_INTERVAL}s")

    while not client.is_closed():
        await process_scripts()
        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log(f"ONLINE → {client.user}")
    load_posted_ids()
    log(f"Loaded {len(posted_ids)} stored script IDs")
    client.loop.create_task(main_loop())


@client.event
async def on_disconnect():
    await close_session()


if __name__ == "__main__":
    if not config.TOKEN:
        log("TOKEN missing")
    elif not config.CHANNEL_ID:
        log("CHANNEL_ID missing")
    else:
        try:
            client.run(config.TOKEN)
        finally:
            if http_session:
                asyncio.get_event_loop().run_until_complete(close_session())
