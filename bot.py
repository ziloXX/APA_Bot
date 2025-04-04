import discord
from discord.ext import commands
import requests
from bs4 import BeautifulSoup
import json
import os
import re

# Configuración del bot
TOKEN = os.environ["TOKEN"]
PREFIX = "!"
CACHE_FILE = "pokemon_cache.json"
POKEMON_LIST_FILE = "pokemon_list.json"
TEAMS_FILE = "teams.json"

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True  # Necesario para detectar reacciones
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Cargar la lista de Pokémon al iniciar el bot
with open(POKEMON_LIST_FILE, "r", encoding="utf-8") as f:
    pokemon_data = json.load(f)
    POKEMON_NAMES = set(pokemon.lower() for pokemon in pokemon_data["pokemon"])

# Funciones auxiliares

def load_teams_from_json():
    """Carga los equipos desde un archivo teams.json."""
    try:
        with open(TEAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []  # Retorna una lista vacía si el archivo no existe o está corrupto

def save_teams_to_json(teams):
    """Guarda los equipos en el archivo teams.json."""
    with open(TEAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(teams, f, ensure_ascii=False, indent=2)

def load_cache():
    """Carga el caché de Pokémon desde pokemon_cache.json."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    """Guarda el caché actualizado en pokemon_cache.json."""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def get_team_pokemon(url):
    """Extrae los 6 nombres de Pokémon de una URL de PokePast desde la primera línea de cada <pre>."""
    cache = load_cache()
    if url in cache:
        return cache[url]
    try:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Error al acceder a {url}: {response.status_code}")
            return ["Error al acceder"] * 6
        soup = BeautifulSoup(response.text, "html.parser")
        pokemon_blocks = soup.find_all("article")  # Contenedor de cada Pokémon
        pokemon_list = []

        for block in pokemon_blocks[:6]:  # Limitar a 6 bloques (un equipo estándar)
            pre_tag = block.find("pre")
            if pre_tag:
                pre_text = pre_tag.get_text(strip=True)
                print(f"Texto completo del <pre>: {pre_text}")  # Depuración
                first_line = pre_text.split("\n")[0].strip()
                print(f"Primera línea extraída: {first_line}")  # Depuración
                pokemon_name = first_line.split("@")[0].strip()
                print(f"Nombre candidato: {pokemon_name}")  # Depuración
                pokemon_name_clean = re.sub(r'\s*\(.*?\)|\s*Shiny:.*', '', pokemon_name).strip()
                if pokemon_name_clean.lower() in POKEMON_NAMES:
                    pokemon_list.append(pokemon_name_clean)
                else:
                    print(f"Texto no válido encontrado: {first_line} en {url}")
                    pokemon_list.append("No encontrado")
            else:
                print(f"No se encontró <pre> en el bloque: {block}")
                pokemon_list.append("No encontrado")

        while len(pokemon_list) < 6:
            pokemon_list.append("No encontrado")

        cache[url] = pokemon_list
        save_cache(cache)
        return pokemon_list
    except Exception as e:
        print(f"Error al scrapear {url}: {e}")
        return ["Error al scrapear"] * 6

# Comando del bot
@bot.command()
@commands.has_permissions(administrator=True)  # Restringe el comando a administradores
async def addteam(ctx, generation, style, url):
    """Permite a administradores agregar un equipo al archivo teams.json.
    Uso: !addteam [generation] [style] [url pokepast]"""
    # Validar que la URL sea de PokePast (opcional, pero recomendado)
    if not url.startswith("https://pokepast.es/"):
        await ctx.send("Error: La URL debe ser de PokePast (https://pokepast.es/).")
        return

    # Cargar equipos existentes
    teams = load_teams_from_json()
    # Crear nuevo equipo con los valores originales
    new_team = {
        "generation": generation,  # Sin .lower()
        "style": style,           # Sin .lower()
        "url": url
    }
    teams.append(new_team)
    # Guardar los equipos actualizados
    save_teams_to_json(teams)
    await ctx.send(f"Equipo agregado correctamente: {new_team}")

@bot.command()
async def team(ctx, *args):
    """Busca equipos en teams.json por generación, estilo o Pokémon con paginación."""
    if not args:
        await ctx.send("Uso: !team <generación> [estilo o Pokémon]")
        return

    # Convertir argumentos a minúsculas para búsqueda insensible
    args = [arg.lower() for arg in args]
    generation = args[0]

    # Cargar equipos y filtrar por generación (insensible a mayúsculas/minúsculas)
    teams = load_teams_from_json()
    filtered_teams = [team for team in teams if team.get("generation", "").lower() == generation]

    if not filtered_teams:
        await ctx.send(f"No se encontraron equipos para esa generacion.")
        return

    if len(args) > 1:
        filter_value = " ".join(args[1:])
        # Filtrar por estilo (insensible a mayúsculas/minúsculas)
        style_teams = [team for team in filtered_teams if team.get("style", "").lower() == filter_value]
        if style_teams:
            filtered_teams = style_teams
        else:
            # Filtrar por Pokémon (insensible a mayúsculas/minúsculas)
            final_teams = []
            for team in filtered_teams:
                pokemon_list = get_team_pokemon(team.get("url"))
                if pokemon_list and filter_value in [p.lower() for p in pokemon_list]:
                    final_teams.append(team)
            filtered_teams = final_teams

    if not filtered_teams:
        await ctx.send("No se encontraron equipos con esos filtros.")
        return

    # Configuración de paginación (5 equipos por página)
    teams_per_page = 5
    pages = [filtered_teams[i:i + teams_per_page] for i in range(0, len(filtered_teams), teams_per_page)]
    current_page = 0

    # Crear el primer embed
    color = 0x00ff00  # Color verde para el embed
    message = await ctx.send(embed=await create_embed(pages, current_page, filtered_teams, color))

    # Añadir reacciones para navegar
    if len(pages) > 1:
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")

    # Bucle para manejar la navegación (solo para el autor)
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
    """Crea un embed para una página específica."""
    embed = discord.Embed(title=f"Equipos encontrados (Página {page_num + 1}/{len(pages)})", color=color)
    teams = pages[page_num]
    for i, team in enumerate(teams, 1 + page_num * 5):
        team_info = f"**Estilo:** {team.get('style', 'Desconocido')}\n"
        pokemon_list = get_team_pokemon(team.get("url"))
        if pokemon_list and all(p != "No encontrado" and p != "Error al acceder" and p != "Error al scrapear" for p in pokemon_list):
            team_info += f"**Pokémon:** {', '.join(['**' + p + '**' for p in pokemon_list])}\n"
        else:
            team_info += "**Pokémon:** No disponibles (error al scrapear o Pokémon no encontrados)\n"
        team_info += f"**Link:** [Haz clic aquí]({team.get('url')})"
        embed.add_field(name=f"Equipo {i}", value=team_info, inline=False)
    return embed

import asyncio  # Añadido para usar async/await

@bot.event
async def on_ready():
    """Evento que se ejecuta cuando el bot está en línea."""
    print(f"{bot.user} está en línea.")

# Iniciar el bot
bot.run(TOKEN)