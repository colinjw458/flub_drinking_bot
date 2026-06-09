"""
FLUBS Drinking Bot 🍻
A tiny Discord bot for game-night chaos.

Match / casual commands:
  !drink      -> random player drinks (random punishment)
  !roulette   -> one survivor, everyone else drinks
  !teams      -> shuffle into THE BOUNDLESS vs THE LIVE WIRES
  !match      -> random 1v1 matchups (odd player gets a bye + drinks)
  !round      -> a full round: random matchup + rule + loser's stake
  !rule       -> drops a random drinking-game rule

Tournament commands:
  !tournament -> start a single-elimination bracket from the roster
  !bracket    -> reprint the current bracket
  !win NAME   -> report a match winner; loser drinks the stake; bracket advances
  !tend       -> end / cancel the current tournament

Roster:
  !roster     -> show current players
  !add NAME   -> add a player  (tip: !add @SomeUser pings the real person)
  !remove NAME-> remove a player
  !commands   -> help

Setup:
  1) pip install -U discord.py python-dotenv
  2) Developer Portal -> Bot tab -> enable "MESSAGE CONTENT INTENT"
  3) Put your token in a file named  .env  next to this script:
         DISCORD_TOKEN=your-token-here
     (or set it as an env var). Then:  python flubs_drinking_bot.py
  Play responsibly — swap in water/soda for any sip and it all still works.
"""

import os
import math
import random

import discord
from discord.ext import commands

# Load DISCORD_TOKEN from a .env file if python-dotenv is installed.
# (pip install python-dotenv)  Falls back to the system env var otherwise.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "!"

roster = [
    "Colin",
    "Spender",
    "Jake",
    "Kaden",
    "Wyatt",
    "Nick",
    "Paul",
    "Garrett",
    "Ayden",
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
]

RULES = [
    "🔇 **No callouts** — stay silent the whole match or drink.",
    "🔫 **Hipfire only** — no aiming down sights or drink.",
    "🚶 **Walk only** — no sprinting the entire match or drink.",
    "🙅 **No real names** — in-game names only; say a real name and you drink.",
    "💣 **No grenades or abilities** this match or drink.",
    "🐢 **No crouching** the whole match or drink.",
    "🔪 **First blood drinks** — whoever dies first this match takes the stake too.",
    "🤐 **Don't say 'kill'** — say 'tag' instead, or drink.",
    "↩️ **No early reloads** — empty the mag before reloading or drink.",
    "🎤 **Trash talk in an accent only** — break character and you drink.",
    "🧍 **No camping** — keep moving; group calls it, you drink.",
    "🎯 **One life, one drink** — first death on each team drinks immediately.",
]

BYE = "BYE"

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


# ===========================================================================
# CASUAL / MATCH COMMANDS
# ===========================================================================
@bot.command(name="drink", aliases=["victim", "who"])
async def drink(ctx):
    if not _need_players():
        await ctx.send("Roster's empty — add someone with `!add Name`.")
        return
    await ctx.send(f"🍻 **{random.choice(roster)}** {random.choice(PUNISHMENTS)}!")


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


@bot.command(name="teams", aliases=["shuffle"])
async def teams(ctx):
    if not _need_players(2):
        await ctx.send("Need at least 2 players to make teams.")
        return
    pool = roster[:]
    random.shuffle(pool)
    mid = (len(pool) + 1) // 2
    a, b = pool[:mid], pool[mid:]
    msg = f"**{TEAM_NAMES[0]}**\n" + "\n".join(f"• {p}" for p in a)
    msg += f"\n\n**{TEAM_NAMES[1]}**\n" + "\n".join(f"• {p}" for p in b)
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
    if pool:
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


# ===========================================================================
# TOURNAMENT
# ===========================================================================
tournament = {
    "active": False,
    "cols": [],     # cols[0] = seeds (incl BYE); later cols = winners or "?"
    "cur": 0,       # index of the round currently being played (children col)
    "matches": {},  # match_id -> {a, b, stake, rule, i}
    "champion": None,
}

NAME_W = 14   # bracket name field width
GAP = 5       # space for connectors between columns


def _next_pow2(n):
    return 1 << (n - 1).bit_length()


def _round_label(slot_count):
    return {2: "FINAL", 4: "SEMIFINALS", 8: "QUARTERFINALS",
            16: "ROUND OF 16", 32: "ROUND OF 32"}.get(slot_count, f"ROUND ({slot_count})")


def _build_round(cur):
    """Create matches for the children column `cur`, auto-advancing byes."""
    children = tournament["cols"][cur]
    parents = tournament["cols"][cur + 1]
    matches = {}
    mi = 1
    for i in range(len(parents)):
        a, b = children[2 * i], children[2 * i + 1]
        if a == BYE and b != BYE:
            parents[i] = b
        elif b == BYE and a != BYE:
            parents[i] = a
        elif a == BYE and b == BYE:
            parents[i] = BYE
        else:
            mid = f"M{mi}"
            mi += 1
            matches[mid] = {
                "a": a, "b": b, "i": i,
                "stake": random.choice(PUNISHMENTS),
                "rule": random.choice(RULES),
            }
    tournament["matches"] = matches


def _render_bracket(cols):
    """Draw a single-elimination bracket as monospace ASCII."""
    def fmt(s):
        s = "(bye)" if s == BYE else s
        return s[:NAME_W].ljust(NAME_W)

    ncols = len(cols)
    size = len(cols[0])
    height = 2 * size - 1
    width = ncols * (NAME_W + GAP)
    grid = [[" "] * width for _ in range(height)]

    def put(r, c, text):
        for k, ch in enumerate(text):
            if 0 <= r < height and 0 <= c + k < width:
                grid[r][c + k] = ch

    def row_of(col, i):
        return (2 ** col) * (2 * i + 1) - 1

    # labels
    for c in range(ncols):
        for i in range(len(cols[c])):
            put(row_of(c, i), c * (NAME_W + GAP), fmt(cols[c][i]))

    # connectors
    for c in range(ncols - 1):
        for i in range(len(cols[c + 1])):
            r_top, r_bot, r_par = row_of(c, 2 * i), row_of(c, 2 * i + 1), row_of(c + 1, i)
            x_end = c * (NAME_W + GAP) + NAME_W
            x_bar = x_end + 1
            x_par = (c + 1) * (NAME_W + GAP)
            put(r_top, x_end, "─"); put(r_top, x_bar, "┐")
            put(r_bot, x_end, "─"); put(r_bot, x_bar, "┘")
            for rr in range(r_top + 1, r_bot):
                if grid[rr][x_bar] == " ":
                    grid[rr][x_bar] = "│"
            put(r_par, x_bar, "├")
            for xx in range(x_bar + 1, x_par):
                put(r_par, xx, "─")

    return "\n".join("".join(row).rstrip() for row in grid)


def _matches_block():
    lines = []
    for mid, m in tournament["matches"].items():
        lines.append(
            f"**{mid}** — {m['a']} vs {m['b']}\n"
            f"   📜 {m['rule']}\n"
            f"   💀 Loser {m['stake']}."
        )
    return "\n".join(lines)


async def _show_bracket(ctx):
    art = _render_bracket(tournament["cols"])
    if len(art) > 1900:
        await ctx.send("⚠️ Bracket's too wide to draw cleanly — try fewer players.")
    else:
        await ctx.send(f"```\n{art}\n```")
    block = _matches_block()
    if block:
        await ctx.send("**Play these matches, then report with `!win NAME`:**\n" + block)


@bot.command(name="tournament", aliases=["tourney", "cup"])
async def tournament_start(ctx):
    if tournament["active"]:
        await ctx.send("A tournament's already running. Use `!bracket` to see it or `!tend` to cancel.")
        return
    if not _need_players(2):
        await ctx.send("Need at least 2 players. Add some with `!add Name`.")
        return

    reals = roster[:]
    random.shuffle(reals)
    n = len(reals)
    size = _next_pow2(n)
    byes = size - n

    # seed: at most one BYE per match; byes land in the first `byes` matches
    seeds = []
    it = iter(reals)
    for m in range(size // 2):
        seeds.append(next(it))                       # slot a
        seeds.append(BYE if m < byes else next(it))  # slot b

    ncols = int(math.log2(size)) + 1
    cols = [["?"] * (size >> c) for c in range(ncols)]
    cols[0] = seeds

    tournament.update(active=True, cols=cols, cur=0, matches={}, champion=None)
    _build_round(0)

    bye_txt = f" ({byes} bye{'s' if byes != 1 else ''})" if byes else ""
    plural = "s" if n != 1 else ""
    await ctx.send(
        f"🏆 **THE FLUBS CUP** — {n} player{plural}{bye_txt}\n"
        f"**{_round_label(size)}** begins!"
    )
    await _show_bracket(ctx)


@bot.command(name="bracket")
async def bracket_show(ctx):
    if not tournament["active"]:
        await ctx.send("No tournament running. Start one with `!tournament`.")
        return
    await _show_bracket(ctx)


@bot.command(name="win", aliases=["winner", "advance"])
async def win(ctx, *, name: str):
    if not tournament["active"]:
        await ctx.send("No tournament running. Start one with `!tournament`.")
        return

    low = name.strip().lower()
    found = None
    for mid, m in tournament["matches"].items():
        for who in (m["a"], m["b"]):
            if who.lower() == low or who.lower().startswith(low):
                found = (mid, m, who)
                break
        if found:
            break

    if not found:
        live = ", ".join(f"{m['a']} vs {m['b']}" for m in tournament["matches"].values())
        await ctx.send(f"Couldn't find **{name}** in an open match.\nOpen now: {live}")
        return

    mid, m, winner = found
    loser = m["b"] if winner == m["a"] else m["a"]
    cur = tournament["cur"]
    tournament["cols"][cur + 1][m["i"]] = winner
    del tournament["matches"][mid]

    await ctx.send(f"✅ **{winner}** beats **{loser}** — {loser} {m['stake']}! 🍺")

    if tournament["matches"]:
        await _show_bracket(ctx)
        return

    # round complete -> advance
    tournament["cur"] += 1
    cur = tournament["cur"]
    if len(tournament["cols"][cur]) == 1:
        champ = tournament["cols"][cur][0]
        tournament["champion"] = champ
        tournament["active"] = False
        art = _render_bracket(tournament["cols"])
        await ctx.send(f"```\n{art}\n```")
        await ctx.send(
            f"👑 **{champ}** WINS THE FLUBS CUP! 🏆\n"
            "Everyone else: raise a glass to the champ. 🍻"
        )
        return

    _build_round(cur)
    await ctx.send(f"➡️ **{_round_label(len(tournament['cols'][cur]))}**")
    await _show_bracket(ctx)


@bot.command(name="tend", aliases=["cancel"])
async def tournament_end(ctx):
    if not tournament["active"]:
        await ctx.send("No tournament to cancel.")
        return
    tournament.update(active=False, cols=[], cur=0, matches={}, champion=None)
    await ctx.send("🛑 Tournament cancelled.")


# ===========================================================================
# ROSTER
# ===========================================================================
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
        "__Casual__\n"
        "`!drink` · `!roulette` · `!teams` · `!match` · `!round` · `!rule`\n"
        "__Tournament__\n"
        "`!tournament` start · `!win NAME` report result · `!bracket` reprint · `!tend` cancel\n"
        "__Roster__\n"
        "`!roster` · `!add Name` · `!remove Name`"
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your environment first.")
    bot.run(TOKEN)