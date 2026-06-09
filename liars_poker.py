"""
FLUBS Liar's Poker 🪙 — the bond-trading bluffing game, drinking edition.

Each player gets a secret 8-digit serial (DM'd, like a dollar bill). Players
take turns bidding on the TOTAL count of a digit across everyone's serials —
e.g. "three 7s" means "I claim there are at least three 7s among all bills."

A bid must raise the previous one: a higher quantity, or the same quantity on a
higher digit. On your turn you either out-bid or call `!liar`. When challenged,
all serials are revealed and the digit is counted:
  • count >= the bid  -> the bid was good, the CHALLENGER drinks
  • count <  the bid   -> it was a bluff, the BIDDER drinks
The loser drinks the spread (how far off the bid was), on the usual scale, and
starts the next round.

LiarsTable is pure Python (no Discord) so the rules are unit-testable (__main__).
register_liars() adds the Discord command layer.
"""

import random

import discord

from poker import drink_call, frac   # reuse the drink scale

SERIAL_LEN = 8


def make_serial(rng):
    return "".join(str(rng.randint(0, 9)) for _ in range(SERIAL_LEN))


# ---------------------------------------------------------------------------
# Pure game engine
# ---------------------------------------------------------------------------
class LiarsTable:
    def __init__(self):
        self.reset()

    def reset(self):
        self.players = []          # ids in seat order
        self.names = {}
        self.serials = {}
        self.active = False
        self.lobby = False
        self.turn = 0              # index into players
        self.bid = None            # (qty, digit, bidder_id) or None
        self.start_id = 0          # who bids first this round

    # ---- lobby ----
    def open_lobby(self, host_id, name):
        self.reset()
        self.lobby = True
        self.players = [host_id]
        self.names = {host_id: name}
        return True

    def join(self, uid, name):
        if not self.lobby or uid in self.players:
            return False
        self.players.append(uid)
        self.names[uid] = name
        return True

    def begin(self, rng):
        if not self.lobby or len(self.players) < 2:
            return False
        self.serials = {p: make_serial(rng) for p in self.players}
        self.active = True
        self.lobby = False
        self.bid = None
        self.turn = self.start_id % len(self.players)
        return True

    # ---- play ----
    def current(self):
        return self.players[self.turn]

    def count_digit(self, digit):
        return sum(s.count(str(digit)) for s in self.serials.values())

    def max_qty(self):
        return SERIAL_LEN * len(self.players)

    def valid_raise(self, qty, digit):
        if not (0 <= digit <= 9) or qty < 1:
            return False
        if self.bid is None:
            return True
        pq, pd, _ = self.bid
        return qty > pq or (qty == pq and digit > pd)

    def make_bid(self, uid, qty, digit):
        if not self.active:
            return False, "No Liar's Poker hand running. `!liars` to start one."
        if uid != self.current():
            return False, f"Not your turn — it's **{self.names[self.current()]}**'s bid."
        if not self.valid_raise(qty, digit):
            if self.bid:
                pq, pd, _ = self.bid
                return False, f"Must raise **{pq}×{pd}s** — bid a higher quantity or the same on a higher digit."
            return False, "Bid like `!bid 3 7` (three 7s)."
        self.bid = (qty, digit, uid)
        self.turn = (self.turn + 1) % len(self.players)
        return True, f"📣 **{self.names[uid]}** bids **{qty} × {digit}s**. " \
                     f"➡️ **{self.names[self.current()]}**: `!bid` higher or `!liar`."

    def challenge(self, uid):
        """Returns (ok, lines, loser_id, drink_teenies)."""
        if not self.active:
            return False, ["No hand running."], None, 0
        if self.bid is None:
            return False, ["Nothing to challenge yet — someone has to bid first."], None, 0
        qty, digit, bidder = self.bid
        actual = self.count_digit(digit)
        lines = [f"🗣️ **{self.names[uid]}** calls **LIAR** on {self.names[bidder]}'s **{qty} × {digit}s**!"]
        reveal = ", ".join(f"{self.names[p]} `{self.serials[p]}`" for p in self.players)
        lines.append("🔓 Serials: " + reveal)
        lines.append(f"🔢 Actual **{digit}s** in play: **{actual}** (bid was {qty}).")

        if actual >= qty:
            loser = uid
            lines.append(f"✅ The bid was good — **{self.names[bidder]}** had the goods. "
                         f"**{self.names[uid]}** drinks.")
        else:
            loser = bidder
            lines.append(f"❌ Bluff called — only {actual}. **{self.names[bidder]}** drinks.")

        spread = max(1, abs(actual - qty))          # how far off -> how much you drink
        lines.append(f"🍺 **{self.names[loser]}** drinks {frac(spread)} — {drink_call(spread)}.")

        # next round: loser bids first
        self.active = False
        self.bid = None
        self.start_id = self.players.index(loser)
        return True, lines, loser, spread


# ---------------------------------------------------------------------------
# Discord command layer
# ---------------------------------------------------------------------------
def register_liars(bot):
    table = LiarsTable()

    async def _dm_serials(ctx):
        for p in table.players:
            user = bot.get_user(p)
            if user is not None:
                try:
                    await user.send(f"🪙 Your Liar's Poker serial: **{table.serials[p]}**  "
                                    f"(table in #{ctx.channel.name})")
                    continue
                except discord.Forbidden:
                    pass
            await ctx.send(f"⚠️ Couldn't DM **{table.names[p]}** — turn on DMs and `!serial` me privately.")

    @bot.command(name="liars", aliases=["liarspoker", "lp"])
    async def liars(ctx):
        if table.active:
            await ctx.send("A Liar's Poker hand is already running. `!lpend` to cancel.")
            return
        table.open_lobby(ctx.author.id, ctx.author.display_name)
        await ctx.send(
            f"🪙 **LIAR'S POKER** — {ctx.author.display_name} opened a table.\n"
            "`!lpjoin` to get in, then `!lpgo` to deal serials and start. (2+ players)"
        )

    @bot.command(name="lpjoin", aliases=["lpsit"])
    async def lpjoin(ctx):
        if not table.lobby:
            await ctx.send("No open table. `!liars` to start one.")
            return
        if table.join(ctx.author.id, ctx.author.display_name):
            who = ", ".join(table.names[p] for p in table.players)
            await ctx.send(f"✅ **{ctx.author.display_name}** joins. In: {who}")
        else:
            await ctx.send("You're already in (or no lobby).")

    @bot.command(name="lpgo", aliases=["lpdeal"])
    async def lpgo(ctx):
        if not table.lobby:
            await ctx.send("Open a table first with `!liars`.")
            return
        if not table.begin(random):
            await ctx.send("Need at least 2 players. `!lpjoin` up!")
            return
        await ctx.send(
            f"🪙 **Serials dealt** to {len(table.players)} players. "
            f"➡️ **{table.names[table.current()]}** opens — `!bid 3 7` style (qty, digit)."
        )
        await _dm_serials(ctx)

    @bot.command(name="serial")
    async def serial(ctx):
        uid = ctx.author.id
        if not table.active or uid not in table.serials:
            await ctx.send("You're not in the current hand.")
            return
        try:
            await ctx.author.send(f"🪙 Your serial: **{table.serials[uid]}**")
            if ctx.guild is not None:
                await ctx.message.add_reaction("📬")
        except discord.Forbidden:
            await ctx.send("Open your DMs so I can send your serial privately.")

    @bot.command(name="bid")
    async def bid(ctx, qty: int = None, digit: int = None):
        if qty is None or digit is None:
            await ctx.send("Bid like `!bid 3 7` — three 7s.")
            return
        ok, msg = table.make_bid(ctx.author.id, qty, digit)
        await ctx.send(msg)

    @bot.command(name="liar", aliases=["challenge", "call_liar", "bs"])
    async def liar(ctx):
        ok, lines, loser, spread = table.challenge(ctx.author.id)
        await ctx.send("\n".join(lines))
        if ok:
            await ctx.send(f"🔁 New round — **{table.names[loser]}** opens. `!lpgo` to re-deal serials.")

    @bot.command(name="lpend", aliases=["liarsend"])
    async def lpend(ctx):
        table.reset()
        await ctx.send("🛑 Liar's Poker table cleared.")

    return table


# ---------------------------------------------------------------------------
# Self-test — `python liars_poker.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    rng = random.Random(0)
    t = LiarsTable()
    t.open_lobby(1, "Colin")
    assert t.join(2, "Jake")
    assert t.join(3, "Nick")
    assert not t.join(2, "Jake")          # no double-join
    assert t.begin(rng)
    assert len(t.serials[1]) == SERIAL_LEN

    # turn + raise validation
    assert t.current() == 1
    ok, _ = t.make_bid(2, 3, 7)
    assert not ok                          # not Jake's turn
    ok, _ = t.make_bid(1, 2, 5)            # Colin opens
    assert ok and t.current() == 2
    ok, _ = t.make_bid(2, 2, 4)            # same qty, lower digit -> invalid
    assert not ok
    ok, _ = t.make_bid(2, 2, 6)            # Jake: same qty higher digit -> ok
    assert ok and t.current() == 3
    ok, _ = t.make_bid(3, 2, 9)            # Nick: same qty higher digit -> ok
    assert ok

    # counting + challenge resolution against known serials
    t.serials = {1: "77700000", 2: "70000000", 3: "00000000"}
    t.active = True
    t.turn = 0
    t.bid = (4, 7, 1)                       # claim four 7s; actual = 4 -> bid good
    ok, lines, loser, spread = t.challenge(2)
    assert ok and loser == 2               # challenger was wrong
    assert t.count_digit(7) == 4

    t.active = True
    t.bid = (5, 7, 1)                       # claim five 7s; actual = 4 -> bluff
    ok, lines, loser, spread = t.challenge(2)
    assert ok and loser == 1 and spread == 1
    assert t.start_id == t.players.index(1)
    print("ALL LIAR'S POKER SELF-TESTS PASSED ✅")
