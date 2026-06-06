"""
FLUBS Drinking Bot 🍻
A tiny Discord bot for game-night chaos.

Features:
  !drink      -> picks a random player to drink (with a random punishment)
  !roulette   -> one survivor, everyone else drinks
  !teams      -> shuffles everyone into THE BOUNDLESS vs THE LIVE WIRES
  !match      -> random 1v1 matchups (odd player gets a bye + drinks)
  !round      -> a full round: random matchup + random rule + loser's stake
  !rule       -> drops a random drinking-game rule on the table
  !roster     -> show current players
  !add NAME   -> add a player  (tip: !add @SomeUser pings the real person)
  !remove NAME-> remove a player
  !commands   -> this help

Setup:
  1) pip install -U discord.py
  2) Create a bot at https://discord.com/developers/applications
     - Bot tab -> enable "MESSAGE CONTENT INTENT"
     - copy the token
  3) export DISCORD_TOKEN="your-token-here"   (Windows: set DISCORD_TOKEN=...)
  4) python flubs_drinking_bot.py
  5) Invite it with the "bot" scope + "Send Messages" permission.

Play responsibly — swap in water/soda for any sip and the game works exactly the same.
"""

import os
import random

import discord
from discord.ext import commands

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "!"

# Roster seeded straight from the lobby screenshot. Edit live with !add / !remove.
roster = [
    "Verticalphase",
    "BigSpender",
    "GrandGlobe",
    "BiggusIckus",
    "ShadowDivider",
    "PromptPro",
]

TEAM_NAMES = ["🟦 THE BOUNDLESS", "🟪 THE LIVE WIRES"]

PUNISHMENTS = [
    "takes 1 sip",
    "takes 2 sips",
    "takes 3 sips",
    "takes a big gulp",
    "finishes their drink 🥃",
    "downs a shot 💀",
    "drinks AND picks a buddy to drink with them",
    "waterfalls the whole table 🌊",
    "gets lucky — sits this one out 😇",
]

RULES = [
    "**Waterfall** — everyone starts drinking at once; you can't stop until the person before you stops.",
    "**Thumb Master** — last person to put a thumb on the table drinks.",
    "**Categories** — pick a category, go around naming things; first to blank or repeat drinks.",
    "**No Names** — say anyone's real name for the next round and you drink.",
    "**Wrong Hand** — drink with your non-dominant hand or it's a penalty sip.",
    "**Rhyme Time** — go around rhyming a word; whoever breaks the chain drinks.",
    "**Never Have I Ever** — losers run one quick round.",
    "**Heaven** — random callout: last person to raise a hand drinks.",
    "**Silent Round** — first person to talk before the next match drinks.",
]

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} — {len(roster)} players loaded.")


def _need_players(min_count=1):
    return len(roster) >= min_count


# ---- random victim --------------------------------------------------------
@bot.command(name="drink", aliases=["victim", "who"])
async def drink(ctx):
    if not _need_players():
        await ctx.send("Roster's empty — add someone with `!add Name`.")
        return
    victim = random.choice(roster)
    await ctx.send(f"🍻 **{victim}** {random.choice(PUNISHMENTS)}!")


@bot.command(name="roulette")
async def roulette(ctx):
    if not _need_players(2):
        await ctx.send("Need at least 2 players for roulette.")
        return
    survivor = random.choice(roster)
    drinkers = [p for p in roster if p != survivor]
    await ctx.send(
        "🔫 **DRINKING ROULETTE**\n"
        f"😇 Safe: **{survivor}**\n"
        f"🍺 Everyone else drinks: {', '.join(drinkers)}"
    )


# ---- match / team randomizers ---------------------------------------------
@bot.command(name="teams", aliases=["shuffle"])
async def teams(ctx):
    if not _need_players(2):
        await ctx.send("Need at least 2 players to make teams.")
        return
    pool = roster[:]
    random.shuffle(pool)
    mid = (len(pool) + 1) // 2
    team_a, team_b = pool[:mid], pool[mid:]
    msg = f"**{TEAM_NAMES[0]}**\n" + "\n".join(f"• {p}" for p in team_a)
    msg += f"\n\n**{TEAM_NAMES[1]}**\n" + "\n".join(f"• {p}" for p in team_b)
    await ctx.send(msg)


@bot.command(name="match", aliases=["matches", "1v1"])
async def match(ctx):
    if not _need_players(2):
        await ctx.send("Need at least 2 players for matchups.")
        return
    pool = roster[:]
    random.shuffle(pool)
    lines = []
    while len(pool) >= 2:
        lines.append(f"⚔️ **{pool.pop()}** vs **{pool.pop()}**")
    if pool:  # odd one out
        lines.append(f"🪑 **{pool[0]}** has a bye — drinks while they wait.")
    await ctx.send("\n".join(lines))


@bot.command(name="round")
async def round_(ctx):
    if not _need_players(2):
        await ctx.send("Need at least 2 players for a round.")
        return
    a, b = random.sample(roster, 2)
    await ctx.send(
        f"🎮 **ROUND** — {a} vs {b}\n"
        f"📜 {random.choice(RULES)}\n"
        f"💀 Loser {random.choice(PUNISHMENTS)}."
    )


@bot.command(name="rule")
async def rule(ctx):
    await ctx.send("📜 " + random.choice(RULES))


# ---- roster management ----------------------------------------------------
@bot.command(name="add")
async def add(ctx, *, name: str):
    name = name.strip()
    if name in roster:
        await ctx.send(f"**{name}** is already in the lobby.")
        return
    roster.append(name)
    await ctx.send(f"✅ Added **{name}**. ({len(roster)} players)")


@bot.command(name="remove", aliases=["kick"])
async def remove(ctx, *, name: str):
    name = name.strip()
    if name not in roster:
        await ctx.send(f"Couldn't find **{name}** in the lobby.")
        return
    roster.remove(name)
    await ctx.send(f"❌ Removed **{name}**. ({len(roster)} players)")


@bot.command(name="roster", aliases=["players", "list"])
async def show_roster(ctx):
    if not roster:
        await ctx.send("No players yet — add some with `!add Name`.")
        return
    await ctx.send("**Lobby:**\n" + "\n".join(f"• {p}" for p in roster))


@bot.command(name="commands", aliases=["help"])
async def help_cmd(ctx):
    await ctx.send(
        "**FLUBS Drinking Bot** 🍻\n"
        "`!drink` — random victim drinks\n"
        "`!roulette` — one survivor, rest drink\n"
        "`!teams` — shuffle into 2 teams\n"
        "`!match` — random 1v1s\n"
        "`!round` — matchup + rule + stake\n"
        "`!rule` — random rule\n"
        "`!roster` / `!add Name` / `!remove Name`"
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your environment first.")
    bot.run(TOKEN)
