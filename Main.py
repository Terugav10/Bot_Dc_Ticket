import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import datetime
import asyncio
import aiosqlite
from keep_alive import keep_alive

def load_config():
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            return json.load(f)
    return {}

configs = load_config()

def get_guild_config(guild_id):
    if str(guild_id) not in configs:
        configs[str(guild_id)] = {
            "category_id": None,
            "role_id": None,
            "embed": {
                "title": "Suporte",
                "description": "Selecione uma opção abaixo para abrir um ticket.",
                "color": 0x00ff00,
                "thumbnail": None,
                "footer": None,
                "menu_placeholder": "Selecione uma opção..."
            },
            "options": []
        }
    return configs[str(guild_id)]

def save_all_configs():
    with open("config.json", "w") as f:
        json.dump(configs, f, indent=4)

async def init_db():
    async with aiosqlite.connect("tickets.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        channel_id INTEGER,
        user_id INTEGER,
        created_at TEXT,
        closed_at TEXT,
        transcript TEXT
        )
        """)
        await db.commit()

async def save_ticket(guild_id, channel_id, user_id, created_at, closed_at, transcript):
    async with aiosqlite.connect("tickets.db") as db:
        await db.execute("""
        INSERT INTO tickets (guild_id, channel_id, user_id, created_at, closed_at, transcript)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (guild_id, channel_id, user_id, created_at, closed_at, transcript))
        await db.commit()

async def generate_transcript(channel: discord.TextChannel):
    messages = []
    async for msg in channel.history(limit=None, oldest_first=True):
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        messages.append(f"[{timestamp}] {msg.author.display_name}: {msg.content}")
    transcript_text = "\n".join(messages)
    filename = f"transcript_{channel.id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(transcript_text)
    return filename, transcript_text

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

class CloseButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        filename, transcript_text = await generate_transcript(channel)
        await channel.send(file=discord.File(filename))
        await save_ticket(
            guild_id=interaction.guild.id,
            channel_id=channel.id,
            user_id=interaction.user.id,
            created_at=channel.created_at.isoformat(),
            closed_at=datetime.datetime.utcnow().isoformat(),
            transcript=transcript_text
        )
        await channel.delete()

class TicketSelect(discord.ui.Select):
    def __init__(self, guild_id):
        config = get_guild_config(guild_id)
        options = [discord.SelectOption(label=o["label"], description=o["description"], value=o["value"]) for o in config["options"]]
        super().__init__(placeholder=config["embed"].get("menu_placeholder", "Selecione uma opção..."), min_values=1, max_values=1, options=options)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        config = get_guild_config(self.guild_id)
        guild = interaction.guild
        category = guild.get_channel(config["category_id"])
        role = guild.get_role(config["role_id"])
        if category is None or role is None:
            await interaction.response.send_message("Categoria ou cargo não configurado.", ephemeral=True)
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True),
            role: discord.PermissionOverwrite(read_messages=True)
        }
        channel = await guild.create_text_channel(f"ticket-{interaction.user.name}", category=category, overwrites=overwrites)
        await channel.send(f"{interaction.user.mention}, seu ticket foi criado.", view=CloseButton())
        await interaction.response.send_message(f"Ticket criado: {channel.mention}", ephemeral=True)

class TicketView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(guild_id))

@bot.event
async def on_ready():
    print(f"Conectado como {bot.user}")
    await bot.tree.sync()
    print("Comandos sincronizados globalmente.")

@bot.tree.command(name="painel", description="Envia o painel de tickets.")
async def painel(interaction: discord.Interaction):
    config = get_guild_config(interaction.guild.id)
    embed = discord.Embed(
        title=config["embed"]["title"],
        description=config["embed"]["description"],
        color=config["embed"]["color"]
    )
    if config["embed"]["thumbnail"]:
        embed.set_thumbnail(url=config["embed"]["thumbnail"])
    if config["embed"]["footer"]:
        embed.set_footer(text=config["embed"]["footer"])
    await interaction.response.send_message(embed=embed, view=TicketView(interaction.guild.id))

@bot.tree.command(name="add", description="Adiciona uma opção ao menu de seleção.")
@app_commands.describe(label="Rótulo", description="Descrição", value="Valor")
async def add(interaction: discord.Interaction, label: str, description: str, value: str):
    config = get_guild_config(interaction.guild.id)
    config["options"].append({"label": label, "description": description, "value": value})
    save_all_configs()
    await interaction.response.send_message(f"Opção '{label}' adicionada.", ephemeral=True)

@bot.tree.command(name="rm", description="Remove uma opção do menu.")
@app_commands.describe(value="Valor da opção")
async def rm(interaction: discord.Interaction, value: str):
    config = get_guild_config(interaction.guild.id)
    before = len(config["options"])
    config["options"] = [o for o in config["options"] if o["value"] != value]
    if len(config["options"]) < before:
        save_all_configs()
        await interaction.response.send_message("Opção removida.", ephemeral=True)
    else:
        await interaction.response.send_message("Opção não encontrada.", ephemeral=True)

@bot.tree.command(name="personalizar", description="Personaliza o painel.")
@app_commands.describe(title="Título", description="Descrição", color="Cor (#hex)", thumbnail="URL thumbnail", footer="Rodapé", menu_placeholder="Nome do menu")
async def personalizar(interaction: discord.Interaction, title: str = None, description: str = None, color: str = None, thumbnail: str = None, footer: str = None, menu_placeholder: str = None):
    config = get_guild_config(interaction.guild.id)
    if title:
        config["embed"]["title"] = title
    if description:
        config["embed"]["description"] = description
    if color:
        try:
            config["embed"]["color"] = int(color.strip("#"), 16)
        except:
            await interaction.response.send_message("Cor inválida, use formato #hex.", ephemeral=True)
            return
    if thumbnail:
        config["embed"]["thumbnail"] = thumbnail
    if footer:
        config["embed"]["footer"] = footer
    if menu_placeholder:
        config["embed"]["menu_placeholder"] = menu_placeholder
    save_all_configs()
    await interaction.response.send_message("Painel personalizado.", ephemeral=True)

@bot.tree.command(name="config", description="Define categoria e cargo.")
@app_commands.describe(category="Categoria dos tickets", role="Cargo responsável")
async def config_cmd(interaction: discord.Interaction, category: discord.CategoryChannel, role: discord.Role):
    config = get_guild_config(interaction.guild.id)
    config["category_id"] = category.id
    config["role_id"] = role.id
    save_all_configs()
    await interaction.response.send_message("Configuração salva.", ephemeral=True)

async def main():
    keep_alive()
    await init_db()
    await bot.start("MTM2MzU1MDM3MTY3MDI2NTg4Nw.GiLthE.iEXo3CCFSbb8ur_YHfAZJV0tpMICeOE-LdO9gI")

asyncio.run(main())
