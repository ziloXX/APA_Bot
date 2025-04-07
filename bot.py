import discord
from discord.ext import commands
import requests
from bs4 import BeautifulSoup
import json
import os
import re
from pymongo import MongoClient
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

def run_health_check_server():
    server = HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

MONGO_URI = os.environ["MONGO_URI"]
client = MongoClient(MONGO_URI)
db = client["APA_Bot"]
teams_collection = db["Teams"]
cache_collection = db["Cache"]

TOKEN = os.environ["TOKEN"]
PREFIX = "!"
POKEMON_LIST_FILE = "pokemon_list.json"

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

with open(POKEMON_LIST_FILE, "r", encoding="utf-8") as f:
    pokemon_data = json.load(f)
    POKEMON_NAMES = set(pokemon.lower().replace("-", " ") for pokemon in pokemon_data["pokemon"])

def get_cached_pokemon(url):
    doc = cache_collection.find_one({"url": url})
    return doc["pokemon"] if doc else None

def cache_pokemon(url, pokemon_list):
    cache_collection.update_one(
        {"url": url},
        {"$set": {"pokemon": pokemon_list}},
        upsert=True
    )

def get_team_pokemon(url):
    cached = get_cached_pokemon(url)
    if cached:
        return cached
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Error al acceder a {url}: {response.status_code}")
            return ["Error al acceder"] * 6

        soup = BeautifulSoup(response.text, "html.parser")
        full_text = soup.get_text().lower().replace("-", " ")

        found_pokemon = []
        used_positions = set()
        for pokemon in POKEMON_NAMES:
            matches = [m.start() for m in re.finditer(rf'\b{re.escape(pokemon)}\b', full_text)]
            for pos in matches:
                if pos not in used_positions:
                    found_pokemon.append((pos, pokemon))
                    used_positions.add(pos)
                if len(found_pokemon) >= 6:
                    break
            if len(found_pokemon) >= 6:
                break

        found_pokemon.sort()
        pokemon_list = [name.title().replace(" ", "-") for _, name in found_pokemon]

        while len(pokemon_list) < 6:
            pokemon_list.append("No encontrado")

        cache_pokemon(url, pokemon_list)
        return pokemon_list

    except Exception as e:
        print(f"Error al scrapear {url}: {e}")
        return ["Error al scrapear"] * 6

def load_teams_from_db():
    return list(teams_collection.find({}, {"_id": 0}))

def save_team_to_db(team):
    teams_collection.insert_one(team)

def update_team_style(url, new_style):
    result = teams_collection.update_one({"url": url}, {"$set": {"style": new_style}})
    return result.modified_count > 0

def delete_team_from_db(url):
    result = teams_collection.delete_one({"url": url})
    return result.deleted_count > 0

def delete_teams_by_generation_and_pokemon(generation, pokemon_name):
    pokemon_name = pokemon_name.lower()
    teams = load_teams_from_db()
    deleted_count = 0
    for team in teams:
        if team.get("generation", "").lower() != generation.lower():
            continue
        team_pokemon = get_team_pokemon(team.get("url"))
        if pokemon_name in [p.lower() for p in team_pokemon]:
            result = teams_collection.delete_one({"url": team["url"]})
            if result.deleted_count > 0:
                deleted_count += 1
    return deleted_count

@bot.command()
async def addteam(ctx, generation, *args):
    if len(args) == 1:
        url = args[0]
        style = ""
    elif len(args) == 2:
        style, url = args
    else:
        await ctx.send("Uso: !addteam [gen] [url] o !addteam [gen] [estilo] [url]")
        return

    if not url.startswith("https://pokepast.es/"):
        await ctx.send("Error: La URL debe ser de PokePast (https://pokepast.es/).")
        return

    new_team = {"generation": generation, "url": url}
    if style:
        new_team["style"] = style
    save_team_to_db(new_team)
    await ctx.send(
        f"✅ Equipo agregado correctamente:\n"
        f"**Generación:** {generation}\n"
        f"**Link:** [Haz clic aquí]({url})"
    )

@bot.command()
async def modifystyle(ctx, url, *, new_style):
    success = update_team_style(url, new_style)
    if success:
        await ctx.send(f"✏️ Estilo actualizado correctamente a **{new_style}** para el equipo {url}.")
    else:
        await ctx.send("❌ No se encontró ningún equipo con esa URL.")

@bot.command()
@commands.has_permissions(administrator=True)
async def deleteteam(ctx, url):
    if not url.startswith("https://pokepast.es/"):
        await ctx.send("Error: La URL debe ser de PokePast (https://pokepast.es/).")
        return
    eliminado = delete_team_from_db(url)
    if eliminado:
        await ctx.send(f"✅ Equipo con URL `{url}` eliminado correctamente.")
    else:
        await ctx.send(f"❌ No se encontró ningún equipo con esa URL.")

@bot.command()
@commands.has_permissions(administrator=True)
async def deletebanned(ctx, generation, *, pokemon):
    cantidad = delete_teams_by_generation_and_pokemon(generation, pokemon)
    if cantidad > 0:
        await ctx.send(f"🗑️ Se eliminaron {cantidad} equipos de {generation} que contenían a **{pokemon}**.")
    else:
        await ctx.send(f"No se encontraron equipos con ese Pokémon en la generación especificada.")

@bot.command()
async def team(ctx, *args):
    if not args:
        await ctx.send("Uso: !team <generación> [Pokémon o estilo]")
        return

    args = [arg.lower() for arg in args]
    generation = args[0]
    teams = load_teams_from_db()
    filtered_teams = [team for team in teams if team.get("generation", "").lower() == generation]

    if not filtered_teams:
        await ctx.send("No se encontraron equipos para esa generacion.")
        return

    if len(args) > 1:
        filter_value = " ".join(args[1:]).lower()

        # Intentar primero por estilo
        style_filtered = [team for team in filtered_teams if team.get("style", "").lower() == filter_value]

        if style_filtered:
            filtered_teams = style_filtered
        else:
            # Si no hay coincidencia por estilo, buscar por Pokémon
            final_teams = []
            for team in filtered_teams:
                pokemon_list = get_team_pokemon(team.get("url"))
                if pokemon_list and filter_value in [p.lower().replace("-", " ") for p in pokemon_list]:
                    final_teams.append(team)
            filtered_teams = final_teams

    if not filtered_teams:
        await ctx.send("No se encontraron equipos con esos filtros.")
        return

    teams_per_page = 5
    pages = [filtered_teams[i:i + teams_per_page] for i in range(0, len(filtered_teams), teams_per_page)]
    current_page = 0

    color = 0x00ff00
    message = await ctx.send(embed=await create_embed(pages, current_page, filtered_teams, color))

    if len(pages) > 1:
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")

    author = ctx.author
    while True:
        def check(reaction, user):
            return user == author and str(reaction.emoji) in ["⬅️", "➡️"] and reaction.message.id == message.id

        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)
            if str(reaction.emoji) == "➡️" and current_page < len(pages) - 1:
                current_page += 1
                await message.edit(embed=await create_embed(pages, current_page, filtered_teams, color))
            elif str(reaction.emoji) == "⬅️" and current_page > 0:
                current_page -= 1
                await message.edit(embed=await create_embed(pages, current_page, filtered_teams, color))
        except asyncio.TimeoutError:
            await message.clear_reactions()
            break

async def create_embed(pages, page_num, all_teams, color):
    embed = discord.Embed(title=f"Equipos encontrados (Página {page_num + 1}/{len(pages)})", color=color)
    teams = pages[page_num]
    for i, team in enumerate(teams, 1 + page_num * 5):
        team_info = ""
        style = team.get("style")
        if style:
            team_info += f"**Estilo:** {style}\n"
        pokemon_list = get_team_pokemon(team.get("url"))
        if pokemon_list and all(p not in ["No encontrado", "Error al acceder", "Error al scrapear"] for p in pokemon_list):
            team_info += f"**Pokémon:** {', '.join(['**' + p + '**' for p in pokemon_list])}\n"
        else:
            team_info += "**Pokémon:** No disponibles (error al scrapear o Pokémon no encontrados)\n"
        team_info += f"**Link:** [Haz clic aquí]({team.get('url')})"
        embed.add_field(name=f"Equipo {i}", value=team_info, inline=False)
    return embed

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📘 Comandos disponibles", color=0x3498db)
    embed.add_field(name="!addteam [gen] [url] o !addteam [gen] [estilo] [url]", value="Agrega un equipo", inline=False)
    embed.add_field(name="!modifystyle [url] [nuevo estilo]", value="Modifica el estilo de un equipo existente", inline=False)
    embed.add_field(name="!deleteteam [url]", value="Elimina un equipo por URL (admin solamente)", inline=False)
    embed.add_field(name="!deletebanned [gen] [pokemon]", value="Elimina todos los equipos con ese Pokémon en esa gen (admin)", inline=False)
    embed.add_field(name="!team [gen] [opcional: pokemon]", value="Busca equipos por generación y opcionalmente por Pokémon", inline=False)
    embed.add_field(name="!help", value="Muestra esta lista de comandos", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"{bot.user} está en línea.")

bot.run(TOKEN)


