import discord
import requests
import asyncio
import json
import config
import os
from datetime import datetime

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# =======================================
# GESPEICHERTE IDs LADEN
# =======================================
if os.path.exists("posted.json"):
    with open("posted.json", "r") as f:
        posted_ids = set(json.load(f))
else:
    posted_ids = set()

def save_posted_ids():
    with open("posted.json", "w") as f:
        json.dump(list(posted_ids), f, indent=4)


# =======================================
# CHECK OB SCRIPT FUNKTIONIERT
# =======================================
def script_is_broken(script_data):
    script = script_data.get("script", "")
    patched = script_data.get("isPatched", False)

    if patched:
        return True

    if not script or len(script) < 5:
        return True

    bad_words = ["error", "nil", "invalid", "fail", "patched"]
    if any(word.lower() in script.lower() for word in bad_words):
        return True

    return False


# =======================================
# FETCH SCRIPTS
# =======================================
async def fetch_scripts():
    url = "https://scriptblox.com/api/script/search?q="
    response = requests.get(url)
    return response.json().get("result", [])


# =======================================
# DISCORD BUTTONS
# =======================================
class ScriptButtons(discord.ui.View):
    def __init__(self, script_text):
        super().__init__(timeout=None)
        self.script_text = script_text

    @discord.ui.button(label="📋 Script kopieren", style=discord.ButtonStyle.green)
    async def copy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"```lua\n{self.script_text[:1900]}\n```",
            ephemeral=True
        )


# =======================================
# EMBEDS SENDEN
# =======================================
async def send_embed(channel, s):

    title = s.get("title", "Unbekannt")
    desc = s.get("description", "Keine Beschreibung")
    game = s.get("game", {}).get("name", "Unbekannt")
    uploader = s.get("owner", {}).get("username", "Unbekannt")
    script = s.get("script", "")
    created = s.get("createdAt", None)

    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            created_str = dt.strftime("%d.%m.%Y • %H:%M")
        except:
            created_str = "Unbekannt"
    else:
        created_str = "Unbekannt"

    embed = discord.Embed(
        title=f"📜 {title}",
        description=desc,
        color=0x2ECC71
    )

    embed.add_field(name="🎮 Spiel", value=game, inline=False)
    embed.add_field(name="👤 Hochgeladen von", value=uploader, inline=True)
    embed.add_field(name="⏰ Erstellt am", value=created_str, inline=True)

    embed.add_field(
        name="📦 Script",
        value="Klicke auf **Script kopieren** unten ⬇️",
        inline=False
    )

    embed.set_footer(text="ScriptBlox Auto-Poster Bot")

    view = ScriptButtons(script)
    await channel.send(embed=embed, view=view)


# =======================================
# MAIN LOOP
# =======================================
async def send_scripts():
    await client.wait_until_ready()
    channel = client.get_channel(config.CHANNEL_ID)

    while True:
        scripts = await fetch_scripts()

        for s in scripts:
            script_id = s.get("_id")

            # bereits gespeichert → skip
            if script_id in posted_ids:
                continue

            # ist Script kaputt? → löschen & nicht posten
            if script_is_broken(s):
                print(f"[INFO] Script {script_id} wurde gelöscht (kaputt)")
                posted_ids.add(script_id)
                save_posted_ids()
                continue

            # neues funktionierendes Script → posten
            await send_embed(channel, s)

            posted_ids.add(script_id)
            save_posted_ids()

        await asyncio.sleep(config.CHECK_DELAY)


@client.event
async def on_ready():
    print(f"Bot ist online: {client.user}")


client.loop.create_task(send_scripts())
client.run(config.TOKEN)