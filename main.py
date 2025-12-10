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
DEFAULT_IMAGE = "https://cdn.discordapp.com/attachments/920731720645500978/1350138518608937081/6794d187-3c79-4a3c-83bb-c9d08e768fa1.webp?ex=693aec7b&is=69399afb&hm=6209616adb80c216a075321117f96dedb73d57d2cae30846c04defe1d42873ae&"
API_URL = "https://scriptblox.com/api/script/fetch"
CHECK_INTERVAL = 10
MAX_RETRIES = 3

is_checking = False
posted_ids = set()
http_session = None


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def load_posted_ids():
    global posted_ids
    if not os.path.exists(POSTED_FILE):
        posted_ids = set()
        return

    try:
        with open(POSTED_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                posted_ids = set(data)
            else:
                posted_ids = set()
    except (json.JSONDecodeError, IOError, ValueError):
        log("WARN: posted.json corrupted, creating new file")
        posted_ids = set()
        save_posted_ids()


def save_posted_ids():
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=".json", dir=".")
        with os.fdopen(fd, "w") as f:
            json.dump(list(posted_ids), f, indent=4)
        shutil.move(temp_path, POSTED_FILE)
    except Exception as e:
        log(f"ERROR: Failed to save posted.json: {e}")
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def script_is_broken(script_data):
    script = script_data.get("script", "")
    patched = script_data.get("isPatched", False)

    if patched:
        return True

    if not script or len(script) < 5:
        return True

    bad_words = ["error", "nil", "invalid", "fail", "patched"]
    script_lower = script.lower()
    if any(word in script_lower for word in bad_words):
        return True

    return False


def get_image_url(script_data):
    game = script_data.get("game", {})
    image_url = game.get("imageUrl", "")

    if image_url and not image_url.endswith("no-script.webp"):
        if image_url.startswith("/"):
            return f"https://scriptblox.com{image_url}"
        return image_url

    return DEFAULT_IMAGE


def format_date(created):
    if not created:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y • %I:%M %p")
    except:
        return "Unknown"


async def create_session():
    global http_session
    timeout = aiohttp.ClientTimeout(total=15)
    http_session = aiohttp.ClientSession(timeout=timeout)
    log("HTTP session created")


async def close_session():
    global http_session
    if http_session:
        await http_session.close()
        http_session = None
        log("HTTP session closed")


async def fetch_scripts():
    global http_session

    if not http_session:
        await create_session()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with http_session.get(API_URL) as response:
                if response.status != 200:
                    log(f"ERROR: API returned status {response.status}")
                    if attempt < MAX_RETRIES:
                        await asyncio.sleep(attempt)
                        continue
                    return []

                data = await response.json()
                result = data.get("result", {})

                if isinstance(result, dict):
                    return result.get("scripts", [])
                return []

        except asyncio.TimeoutError:
            log(f"ERROR: API request timed out (attempt {attempt}/{MAX_RETRIES})"
                )
        except aiohttp.ClientError as e:
            log(f"ERROR: API connection error: {e} (attempt {attempt}/{MAX_RETRIES})"
                )
        except json.JSONDecodeError:
            log(f"ERROR: Invalid JSON from API (attempt {attempt}/{MAX_RETRIES})"
                )
        except Exception as e:
            log(f"ERROR: Unexpected error: {e} (attempt {attempt}/{MAX_RETRIES})"
                )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(attempt)

    log("ERROR: All API retry attempts failed")
    return []


class ScriptButtons(discord.ui.View):

    def __init__(self, script_text):
        super().__init__(timeout=None)
        self.script_text = script_text

    @discord.ui.button(label="📋 Copy Script", style=discord.ButtonStyle.green)
    async def copy_button(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        if len(self.script_text) > 1900:
            await interaction.response.send_message(
                f"```lua\n{self.script_text[:1900]}\n```\n*(Script truncated, too long for Discord)*",
                ephemeral=True)
        else:
            await interaction.response.send_message(
                f"```lua\n{self.script_text}\n```", ephemeral=True)


async def send_embed(channel, script_data):
    script_title = script_data.get("title", "Unknown")
    game_name = script_data.get("game", {}).get("name", "")
    script = script_data.get("script", "")
    created = script_data.get("createdAt", None)

    if not game_name or game_name.strip() == "":
        game_name = "Unknown Game"

    created_str = format_date(created)
    image_url = get_image_url(script_data)

    embed = discord.Embed(title=f"🎮{game_name}🎮",
                          description=f"{script_title}",
                          color=0xFF0000)

    embed.add_field(name="⌛Created⌛", value=created_str, inline=True)
    embed.add_field(name="📜Script📜",
                    value="⬇️Click on **Copy Script** below⬇️",
                    inline=False)

    embed.set_thumbnail(url=image_url)
    embed.set_footer(text="", icon_url=image_url)

    view = ScriptButtons(script)

    try:
        await channel.send(embed=embed, view=view)
        return True
    except discord.DiscordException as e:
        log(f"ERROR: Failed to send embed: {e}")
        return False


async def process_scripts():
    global is_checking, posted_ids

    if is_checking:
        return

    is_checking = True

    try:
        channel = client.get_channel(config.CHANNEL_ID)
        if not channel:
            log("ERROR: Channel not found")
            return

        scripts = await fetch_scripts()

        for s in scripts:
            script_id = s.get("_id")

            if not script_id:
                continue

            if script_id in posted_ids:
                continue

            if script_is_broken(s):
                log(f"INFO: Script {script_id} skipped (broken/patched) - not saved"
                    )
                continue

            success = await send_embed(channel, s)

            if success:
                log(f"INFO: Posted script: {s.get('title', 'Unknown')}")
                posted_ids.add(script_id)
                save_posted_ids()

            await asyncio.sleep(1)

    except Exception as e:
        log(f"ERROR: Error in process_scripts: {e}")

    finally:
        is_checking = False


async def main_loop():
    await client.wait_until_ready()
    await create_session()
    log(f"INFO: Starting main loop (checking every {CHECK_INTERVAL}s)")

    while not client.is_closed():
        await process_scripts()
        await asyncio.sleep(CHECK_INTERVAL)


@client.event
async def on_ready():
    log(f"Bot is online: {client.user}")
    load_posted_ids()
    log(f"INFO: Loaded {len(posted_ids)} posted script IDs")
    client.loop.create_task(main_loop())


@client.event
async def on_disconnect():
    await close_session()


if __name__ == "__main__":
    if not config.TOKEN:
        log("ERROR: TOKEN not set in environment")
    elif not config.CHANNEL_ID:
        log("ERROR: CHANNEL_ID not set in environment")
    else:
        try:
            client.run(config.TOKEN)
        finally:
            if http_session:
                asyncio.get_event_loop().run_until_complete(close_session())
