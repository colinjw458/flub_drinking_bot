"""
FLUBS Hold'em 🃏 — pot-limit *drinking* Texas Hold'em.

The whole economy is drinks, quoted bond-style in teenies (1/16):
    blinds 1/16 (sb) and 1/8 (bb); bets sized in eighths and teenies.
At showdown the loser(s) DRINK THE POT, on this scale:
    1/16 = a sip · 1/8 = a drink · 1/4 = 2 drinks · 1/2 = half your drink · 1 = a shot

Chips are tracked internally in teenies (integers) so the engine math is exact;
everything shown to players is rendered as a reduced fraction.

The Table class is pure Python (no Discord) so the engine — hand evaluation,
pot-limit math, side pots — is unit-testable (see __main__). register_poker()
adds the Discord command layer.
"""

import random
from math import gcd
from collections import Counter

import discord

# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
RANKS = "23456789TJQKA"
RV = {r: i for i, r in enumerate(RANKS, start=2)}   # '2'->2 ... 'A'->14
SUITS = ["♠", "♥", "♦", "♣"]


def make_deck():
    return [r + s for r in RANKS for s in SUITS]


def _val(c):
    return RV[c[0]]


def _suit(c):
    return c[1]


def pretty(c):
    r = "10" if c[0] == "T" else c[0]
    return f"{r}{c[1]}"


def show_cards(cards):
    return " ".join(pretty(c) for c in cards) if cards else "—"


# ---------------------------------------------------------------------------
# Drink / fraction rendering (teenie = 1/16)
# ---------------------------------------------------------------------------
def frac(teenies):
    """Render a teenie count as a reduced fraction string."""
    if teenies <= 0:
        return "0"
    g = gcd(teenies, 16)
    num, den = teenies // g, 16 // g
    return str(num) if den == 1 else f"{num}/{den}"


# greedy milestones, biggest first: (teenies, phrase)
_MILE = [(16, "a shot 🥃"), (8, "half your drink"), (4, "2 drinks"),
         (2, "a drink"), (1, "a sip")]


def drink_call(teenies):
    """Translate a teenie count into a physical drinking instruction."""
    parts = []
    t = teenies
    for v, label in _MILE:
        while t >= v:
            parts.append(label)
            t -= v
    return " + ".join(parts) if parts else "nothing"


# ---------------------------------------------------------------------------
# 7-card hand evaluation -> comparable tuple (bigger is better)
# ---------------------------------------------------------------------------
HAND_NAMES = {
    8: "straight flush", 7: "four of a kind", 6: "full house", 5: "flush",
    4: "straight", 3: "three of a kind", 2: "two pair", 1: "pair", 0: "high card",
}


def _straight_high(vals):
    s = set(vals)
    if 14 in s:
        s = s | {1}          # wheel: ace plays low
    for hi in range(14, 4, -1):
        if all((hi - k) in s for k in range(5)):
            return hi
    return None


def hand_rank(cards):
    """cards: list of card strings (5-7). Returns a comparable tuple."""
    vals = [_val(c) for c in cards]
    cnt = Counter(vals)
    scnt = Counter(_suit(c) for c in cards)

    flush_suit = next((s for s, n in scnt.items() if n >= 5), None)
    if flush_suit:
        fvals = [_val(c) for c in cards if _suit(c) == flush_suit]
        sf = _straight_high(fvals)
        if sf:
            return (8, sf)

    quad = [v for v, n in cnt.items() if n == 4]
    if quad:
        q = quad[0]
        return (7, q, max(v for v in vals if v != q))

    trips = sorted((v for v, n in cnt.items() if n == 3), reverse=True)
    pairs = sorted((v for v, n in cnt.items() if n == 2), reverse=True)
    if trips and (len(trips) > 1 or pairs):
        pair = trips[1] if len(trips) > 1 else pairs[0]
        return (6, trips[0], pair)

    if flush_suit:
        top = sorted((_val(c) for c in cards if _suit(c) == flush_suit), reverse=True)
        return (5, *top[:5])

    sh = _straight_high(vals)
    if sh:
        return (4, sh)

    if trips:
        kick = sorted((v for v in vals if v != trips[0]), reverse=True)[:2]
        return (3, trips[0], *kick)

    if len(pairs) >= 2:
        kick = max(v for v in vals if v not in pairs[:2])
        return (2, pairs[0], pairs[1], kick)

    if pairs:
        kick = sorted((v for v in vals if v != pairs[0]), reverse=True)[:3]
        return (1, pairs[0], *kick)

    return (0, *sorted(vals, reverse=True)[:5])


# ---------------------------------------------------------------------------
# Bet sizing (bond math) — sizes are fractions of the relevant pot
# ---------------------------------------------------------------------------
FRAC_FLAVOR = {
    (1, 16): "a teenie", (1, 8): "an eighth", (3, 16): "three teenies",
    (1, 4): "a quarter", (5, 16): "five teenies", (3, 8): "three eighths",
    (1, 2): "half pot", (5, 8): "five eighths", (3, 4): "three quarters",
    (7, 8): "seven eighths", (1, 1): "pot",
}


def parse_size(arg, unit):
    """Return (teenies, flavor) for a size given as a fraction of `unit`, or raw
    teenies. Returns (None, 'pot') for 'pot'/'max' so the caller applies the cap."""
    a = arg.strip().lower()
    if a in ("pot", "max", "p"):
        return None, "pot"
    if "/" in a:
        num, den = (int(x) for x in a.split("/"))
        teenies = max(1, round(unit * num / den))
        g = gcd(num, den)
        flavor = FRAC_FLAVOR.get((num // g, den // g), f"{num}/{den} pot")
        return teenies, flavor
    return max(1, int(round(float(a)))), None


# ---------------------------------------------------------------------------
# Table — pure game engine (all amounts in teenies)
# ---------------------------------------------------------------------------
class Table:
    def __init__(self, start_stack=16, sb=1, bb=2):
        self.start_stack = start_stack   # 16 teenies = 1.0 ("a shot")
        self.sb = sb                     # 1/16
        self.bb = bb                     # 1/8
        self.reset_all()
        self.losers_drink_bet = False    # !drinkmode toggle (persists across hands)

    def reset_all(self):
        self.seats = []
        self.names = {}
        self.stacks = {}
        self.button = -1
        self.channel_id = None
        self.in_hand = False
        self._clear_hand()

    def _clear_hand(self):
        self.players = []
        self.deck = []
        self.hole = {}
        self.board = []
        self.pot = 0
        self.committed = {}
        self.paid = {}
        self.folded = set()
        self.allin = set()
        self.current_bet = 0
        self.min_raise = self.bb
        self.street = None
        self.need = set()
        self.to_act = None
        self.last_result = None      # set when a hand ends; drives drinking

    # ---- seating ----
    def seat(self, uid, name, chips=None):
        if uid in self.seats:
            return False
        self.seats.append(uid)
        self.names[uid] = name
        self.stacks[uid] = self.start_stack if chips is None else chips
        return True

    def unseat(self, uid):
        if uid not in self.seats:
            return False
        self.seats.remove(uid)
        self.names.pop(uid, None)
        self.stacks.pop(uid, None)
        return True

    # ---- helpers ----
    def pot_now(self):
        return self.pot + sum(self.committed.values())

    def to_call(self, p):
        return self.current_bet - self.committed[p]

    def contenders(self):
        return [p for p in self.players if p not in self.folded]

    def _put(self, p, amt):
        amt = min(amt, self.stacks[p])
        self.stacks[p] -= amt
        self.committed[p] += amt
        self.paid[p] += amt
        if self.stacks[p] == 0:
            self.allin.add(p)
        return amt

    def _next_actor(self, start):
        n = len(self.players)
        i = start
        for _ in range(n):
            i = (i + 1) % n
            p = self.players[i]
            if p not in self.folded and p not in self.allin:
                return i
        return None

    def max_raise_to(self, p):
        """Pot-limit: highest total bet p may make this street (capped by stack)."""
        call = self.to_call(p)
        pot_after_call = self.pot_now() + call
        cap = self.committed[p] + self.stacks[p]
        return min(self.current_bet + pot_after_call, cap)

    # ---- hand lifecycle ----
    def start_hand(self):
        live = [s for s in self.seats if self.stacks[s] > 0]
        if len(live) < 2:
            return False
        self._clear_hand()
        self.players = live
        n = len(live)
        self.button = (self.button + 1) % n
        self.committed = {p: 0 for p in live}
        self.paid = {p: 0 for p in live}

        self.deck = make_deck()
        random.shuffle(self.deck)
        for p in live:
            self.hole[p] = [self.deck.pop(), self.deck.pop()]

        if n == 2:
            sb_i, bb_i = self.button, (self.button + 1) % 2
        else:
            sb_i, bb_i = (self.button + 1) % n, (self.button + 2) % n
        self._put(live[sb_i], self.sb)
        self._put(live[bb_i], self.bb)
        self.current_bet = self.bb
        self.min_raise = self.bb
        self.street = "preflop"
        self.need = set(p for p in live if p not in self.allin)
        self.to_act = self._next_actor(bb_i)
        self.in_hand = True
        return True

    def _deal_street(self):
        if self.street == "preflop":
            self.board += [self.deck.pop() for _ in range(3)]
            self.street = "flop"
        elif self.street == "flop":
            self.board.append(self.deck.pop())
            self.street = "turn"
        elif self.street == "turn":
            self.board.append(self.deck.pop())
            self.street = "river"

    def _begin_betting(self):
        self.current_bet = 0
        self.min_raise = self.bb
        self.committed = {p: 0 for p in self.players}
        self.need = set(p for p in self.contenders() if p not in self.allin)
        self.to_act = self._next_actor(self.button)

    # ---- actions: return (ok, log) ----
    def act(self, pid, kind, arg=None):
        if not self.in_hand:
            return False, "No hand in progress — `!deal` to start one."
        if self.players[self.to_act] != pid:
            return False, f"Not your turn — waiting on **{self.names[self.players[self.to_act]]}**."
        n = self.names[pid]
        opening = self.current_bet == 0

        if kind == "fold":
            self.folded.add(pid)
            self.need.discard(pid)
            return True, f"🃏 **{n}** folds."

        if kind == "check":
            if self.to_call(pid) != 0:
                return False, f"Can't check — {frac(self.to_call(pid))} to call."
            self.need.discard(pid)
            return True, f"✋ **{n}** checks."

        if kind == "call":
            tc = self.to_call(pid)
            if tc == 0:
                return False, "Nothing to call — `!check` instead."
            amt = self._put(pid, tc)
            self.need.discard(pid)
            tag = " — **all in** 🔥" if pid in self.allin else ""
            return True, f"💰 **{n}** calls {frac(amt)}{tag}."

        if kind in ("bet", "raise", "pot", "allin"):
            return self._aggress(pid, kind, arg, opening)

        return False, "Unknown action."

    def _aggress(self, pid, kind, arg, opening):
        n = self.names[pid]
        call = self.to_call(pid)
        flavor = None

        if kind == "allin":
            target = self.committed[pid] + self.stacks[pid]
        elif kind == "pot":
            target = self.max_raise_to(pid)
            flavor = "pot"
        else:
            if arg is None:
                return False, f"Size it: `!{kind} 1/8`, `!{kind} 1/16`, or `!pot`."
            if opening:
                teenies, flavor = parse_size(arg, self.pot_now())
                target = self.max_raise_to(pid) if teenies is None else teenies
            else:
                teenies, flavor = parse_size(arg, self.pot_now() + call)
                target = self.max_raise_to(pid) if teenies is None else self.current_bet + teenies

        target = min(target, self.max_raise_to(pid))
        add = target - self.committed[pid]
        if add <= 0:
            return False, "That isn't a raise."
        if add > self.stacks[pid]:
            add = self.stacks[pid]
            target = self.committed[pid] + add
        is_allin = add == self.stacks[pid]

        min_to = self.bb if opening else self.current_bet + self.min_raise
        if not is_allin and target < min_to:
            return False, f"Min {'bet' if opening else 'raise'} is to {frac(min_to)}. (or `!allin`)"
        if target <= self.current_bet and not is_allin:
            return False, "Has to beat the current bet."

        raise_amt = target - self.current_bet
        reopens = opening or raise_amt >= self.min_raise
        self._put(pid, add)
        if target > self.current_bet:
            if reopens:
                self.min_raise = raise_amt
                self.need = set(p for p in self.contenders() if p not in self.allin)
            self.current_bet = target
        self.need.discard(pid)

        bond = f" ({flavor})" if flavor else ""
        tag = " — **all in** 🔥" if is_allin else ""
        if opening:
            return True, f"📈 **{n}** bets {frac(target)}{bond}{tag}. (pot {frac(self.pot_now())})"
        return True, f"📈 **{n}** raises to {frac(target)}{bond}{tag}. (pot {frac(self.pot_now())})"

    # ---- progression -> list of message lines ----
    def progress(self):
        cont = self.contenders()
        if len(cont) == 1:
            return self._award_uncontested(cont[0])

        if self.need:
            self.to_act = self._next_actor(self.to_act)
            return [self.prompt_line()]

        # betting round closed — sweep into pot
        self.pot += sum(self.committed.values())
        self.committed = {p: 0 for p in self.players}

        actable = [p for p in cont if p not in self.allin]
        if len(actable) <= 1 and self.street != "river":
            out = []
            while len(self.board) < 5:
                self._deal_street()
            out.append(self.board_line("RUN IT OUT"))
            out += self._showdown()
            return out

        if self.street == "river":
            return self._showdown()

        self._deal_street()
        self._begin_betting()
        return [self.board_line(self.street.upper()), self.prompt_line()]

    # ---- payout ----
    def _settle_busts(self):
        lines = []
        for s in self.seats:
            if self.stacks[s] == 0:
                self.stacks[s] = self.start_stack
                lines.append(f"💀 **{self.names[s]}** stacked out — finish your drink 🥃 and rebuy.")
        return lines

    def _award_uncontested(self, winner):
        self.pot += sum(self.committed.values())
        self.stacks[winner] += self.pot
        self.in_hand = False
        # uncontested = no showdown = no drinks (just push the pot)
        self.last_result = None
        out = [f"🏆 **{self.names[winner]}** takes {frac(self.pot)} uncontested — no cards, no drinks."]
        return out + self._settle_busts()

    def _side_pots(self):
        contrib = dict(self.paid)
        pots = []
        while any(v > 0 for v in contrib.values()):
            positive = [p for p, v in contrib.items() if v > 0]
            m = min(contrib[p] for p in positive)
            pots.append((m * len(positive), [p for p in positive if p not in self.folded]))
            for p in positive:
                contrib[p] -= m
        return pots

    def _showdown(self):
        out = ["**— SHOWDOWN —**"]
        ranks = {}
        for p in self.contenders():
            r = hand_rank(self.hole[p] + self.board)
            ranks[p] = r
            out.append(f"   **{self.names[p]}**: {show_cards(self.hole[p])}  → _{HAND_NAMES[r[0]]}_")

        pot_total = self.pot
        winners_all = set()
        for amount, elig in self._side_pots():
            if not elig:
                continue
            best = max(ranks[p] for p in elig)
            winners = [p for p in elig if ranks[p] == best]
            share, rem = divmod(amount, len(winners))
            for i, w in enumerate(sorted(winners, key=self.players.index)):
                self.stacks[w] += share + (1 if i < rem else 0)
                winners_all.add(w)
            wn = " & ".join(self.names[w] for w in winners)
            out.append(f"💸 {wn} win{'s' if len(winners) == 1 else ''} {frac(amount)}.")

        self.in_hand = False
        # hand result drives the drinking (handled by the Discord layer)
        self.last_result = {
            "winners": winners_all,
            "pot": pot_total,
            "bets": dict(self.paid),          # every player's contribution this hand
            "players": list(self.players),
        }
        return out + self._settle_busts()

    # ---- rendering ----
    def board_line(self, label):
        return f"**— {label} —**  {show_cards(self.board)}   ·  pot {frac(self.pot_now())}"

    def prompt_line(self):
        p = self.players[self.to_act]
        tc = self.to_call(p)
        opts = "`!check` `!bet 1/8`" if tc == 0 else f"`!call` ({frac(tc)}) `!raise 1/8`"
        return (f"➡️ **{self.names[p]}** — {opts} `!pot` `!allin` `!fold`"
                f"   ·  pot {frac(self.pot_now())}, stack {frac(self.stacks[p])}")

    def table_view(self):
        if not self.seats:
            return "No one seated. `!sit` to take a seat. 🪑"
        rows = []
        for s in self.seats:
            btn = " 🔘" if (self.in_hand and self.players and self.players[self.button] == s) else ""
            rows.append(f"• **{self.names[s]}** — {frac(self.stacks[s])}{btn}")
        mode = "ON" if self.losers_drink_bet else "OFF"
        return ("**🃏 FLUBS Hold'em** — blinds 1/16 / 1/8 · winner pours the pot · "
                f"losers-drink-bet **{mode}**\n" + "\n".join(rows))


# ---------------------------------------------------------------------------
# Interactive "pour the pot" menu — DM'd to the hand's winner
# ---------------------------------------------------------------------------
class PourView(discord.ui.View):
    """Winner picks who drinks the pot, fraction by fraction. Posts to channel."""

    def __init__(self, winner_id, winner_name, recipients, total, on_done):
        super().__init__(timeout=180)
        self.winner_id = winner_id
        self.winner_name = winner_name
        self.recipients = recipients          # {id: name}
        self.total = total
        self.remaining = total
        self.assign = {}                      # id -> teenies
        self.on_done = on_done                # async fn(assign)
        self.target = None
        self.note = ""
        self.message = None
        self._finalized = False

        self.select = discord.ui.Select(
            placeholder="Who drinks next?",
            options=[discord.SelectOption(label=nm, value=str(i))
                     for i, nm in recipients.items()][:25],
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        for label, amt in [("sip 1/16", 1), ("drink 1/8", 2), ("1/4", 4), ("1/2", 8)]:
            b = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            b.callback = self._mk_assign(amt)
            self.add_item(b)
        rest = discord.ui.Button(label="Rest →", style=discord.ButtonStyle.success)
        rest.callback = self._on_rest
        self.add_item(rest)
        done = discord.ui.Button(label="Done", style=discord.ButtonStyle.secondary)
        done.callback = self._on_done
        self.add_item(done)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.winner_id:
            return False
        return True

    def _status(self):
        lines = [f"🍺 You won **{frac(self.total)}** — pour it out. "
                 f"Remaining: **{frac(self.remaining)}**"]
        if self.target:
            lines.append(f"Pouring for: **{self.recipients[self.target]}** — pick a size")
        else:
            lines.append("Pick a player from the menu, then a size.")
        if self.assign:
            lines.append("So far: " + ", ".join(
                f"{self.recipients[i]} {frac(a)}" for i, a in self.assign.items()))
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)

    async def _refresh(self, interaction):
        await interaction.response.edit_message(content=self._status(), view=self)

    async def _on_select(self, interaction):
        self.target = int(self.select.values[0])
        self.note = ""
        await self._refresh(interaction)

    def _mk_assign(self, amt):
        async def cb(interaction):
            if not self.target:
                self.note = "⚠️ Pick who drinks first (menu above)."
                await self._refresh(interaction)
                return
            give = min(amt, self.remaining)
            self.assign[self.target] = self.assign.get(self.target, 0) + give
            self.remaining -= give
            self.note = ""
            if self.remaining <= 0:
                await self._finalize(interaction)
            else:
                await self._refresh(interaction)
        return cb

    async def _on_rest(self, interaction):
        if not self.target:
            self.note = "⚠️ Pick who drinks first."
            await self._refresh(interaction)
            return
        self.assign[self.target] = self.assign.get(self.target, 0) + self.remaining
        self.remaining = 0
        await self._finalize(interaction)

    async def _on_done(self, interaction):
        await self._finalize(interaction)

    async def _finalize(self, interaction):
        if self._finalized:
            return
        self._finalized = True
        for c in self.children:
            c.disabled = True
        await interaction.response.edit_message(
            content=self._status() + "\n✅ Sent to the table.", view=self)
        await self.on_done(self.assign)
        self.stop()

    async def on_timeout(self):
        if self._finalized:
            return
        self._finalized = True
        for c in self.children:
            c.disabled = True
        if self.message:
            try:
                await self.message.edit(content=self._status() + "\n⏱️ Timed out.", view=self)
            except discord.HTTPException:
                pass
        await self.on_done(self.assign)


# ---------------------------------------------------------------------------
# Discord command layer
# ---------------------------------------------------------------------------
def register_poker(bot):
    table = Table()

    async def _post(ctx, lines):
        for line in lines:
            await ctx.send(line)

    async def _dm_holes(ctx):
        for p in table.players:
            user = bot.get_user(p)
            cards = show_cards(table.hole[p])
            if user is not None:
                try:
                    await user.send(f"🂠 Your hole cards: **{cards}**  (table in #{ctx.channel.name})")
                    continue
                except discord.Forbidden:
                    pass
            await ctx.send(f"⚠️ Couldn't DM **{table.names[p]}** — turn on DMs and `!cards` me privately.")

    @bot.command(name="sit")
    async def sit(ctx, chips: int = None):
        if table.in_hand:
            await ctx.send("A hand's in progress — `!sit` once it's done.")
            return
        name = ctx.author.display_name
        if table.seat(ctx.author.id, name, chips):
            await ctx.send(f"🪑 **{name}** sits with {frac(table.stacks[ctx.author.id])}.")
        else:
            await ctx.send(f"**{name}**, you're already seated.")

    @bot.command(name="stand", aliases=["leave"])
    async def stand(ctx):
        if table.in_hand and ctx.author.id in table.players and ctx.author.id not in table.folded:
            await ctx.send("Finish (or `!fold`) the current hand before standing.")
            return
        await ctx.send(f"👋 **{ctx.author.display_name}** stands up." if table.unseat(ctx.author.id)
                       else "You're not seated.")

    @bot.command(name="table", aliases=["seats"])
    async def table_cmd(ctx):
        await ctx.send(table.table_view())

    @bot.command(name="deal", aliases=["holdem", "newhand"])
    async def deal(ctx):
        if table.in_hand:
            await ctx.send("A hand's already running.")
            return
        if not table.start_hand():
            await ctx.send("Need at least 2 seated players with chips. `!sit` up!")
            return
        table.channel_id = ctx.channel.id
        await ctx.send(
            f"🃏 **NEW HAND** — blinds 1/16 / 1/8\n"
            f"Dealer 🔘 **{table.names[table.players[table.button]]}**  ·  "
            f"{len(table.players)} players  ·  pot {frac(table.pot_now())}"
        )
        await _dm_holes(ctx)
        await ctx.send(table.prompt_line())

    @bot.command(name="cards", aliases=["peek"])
    async def cards(ctx):
        uid = ctx.author.id
        if not table.in_hand or uid not in table.hole:
            await ctx.send("You're not in the current hand.")
            return
        try:
            await ctx.author.send(f"🂠 Your hole cards: **{show_cards(table.hole[uid])}**")
            if ctx.guild is not None:
                await ctx.message.add_reaction("📬")
        except discord.Forbidden:
            await ctx.send("Open your DMs so I can send cards privately.")

    async def _start_pour(ctx, winner_id, pot):
        """DM the winner an interactive menu to hand out the pot as drinks."""
        recipients = {i: table.names[i] for i in table.last_result["players"] if i != winner_id}
        if not recipients:
            return
        winner_name = table.names[winner_id]

        async def on_done(assign):
            if not assign:
                await ctx.send(f"🍺 **{winner_name}** poured nothing out. Mercy. 🙏")
                return
            lines = [f"🍺 **{winner_name}** hands out the {frac(pot)} pot:"]
            for i, a in assign.items():
                if a > 0:
                    lines.append(f"   **{recipients[i]}** drinks {frac(a)} — {drink_call(a)}")
            await ctx.send("\n".join(lines))

        view = PourView(winner_id, winner_name, recipients, pot, on_done)
        user = bot.get_user(winner_id)
        try:
            view.message = await user.send(
                f"You won a {frac(pot)} pot at the table in #{ctx.channel.name}. "
                "Decide who drinks 👇", view=view)
            await ctx.send(f"📬 **{winner_name}** is deciding who drinks the {frac(pot)} pot…")
        except (discord.Forbidden, AttributeError):
            await ctx.send(f"⚠️ Couldn't DM **{winner_name}** to pour — open your DMs! "
                           "Pot goes undrunk this hand.")

    async def _resolve_drinks(ctx):
        res = table.last_result
        if not res:
            return
        table.last_result = None  # consume so we don't double-fire

        # toggle: each non-winner also drinks what they bet this hand
        if table.losers_drink_bet:
            lines = [f"   **{table.names[p]}** drinks {frac(b)} — {drink_call(b)}"
                     for p, b in res["bets"].items()
                     if p not in res["winners"] and b > 0]
            if lines:
                await ctx.send("**Losers drink their bets:**\n" + "\n".join(lines))

        # winner always pours the pot
        if res["pot"] > 0 and len(res["winners"]) == 1:
            await _start_pour(ctx, next(iter(res["winners"])), res["pot"])
        elif res["pot"] > 0 and len(res["winners"]) > 1:
            names = " & ".join(table.names[w] for w in res["winners"])
            await ctx.send(f"🤝 Split pot — **{names}**, sort the {frac(res['pot'])} of drinks "
                           "out among yourselves.")

    async def _do(ctx, kind, arg=None):
        ok, log = table.act(ctx.author.id, kind, arg)
        await ctx.send(log)
        if ok:
            await _post(ctx, table.progress())
            if not table.in_hand:
                await _resolve_drinks(ctx)

    @bot.command(name="drinkmode")
    async def drinkmode(ctx, mode: str = None):
        m = (mode or "").lower()
        if m in ("on", "losers", "bet"):
            table.losers_drink_bet = True
        elif m in ("off", "pot"):
            table.losers_drink_bet = False
        else:
            table.losers_drink_bet = not table.losers_drink_bet
        state = "ON" if table.losers_drink_bet else "OFF"
        await ctx.send(f"🍻 **Losers-drink-their-bet: {state}.** "
                       "(Winner always pours the pot either way.)")

    @bot.command(name="check")
    async def check(ctx):
        await _do(ctx, "check")

    @bot.command(name="call")
    async def call(ctx):
        await _do(ctx, "call")

    @bot.command(name="fold")
    async def fold(ctx):
        await _do(ctx, "fold")

    @bot.command(name="bet")
    async def bet(ctx, size: str = None):
        await _do(ctx, "bet", size)

    @bot.command(name="raise", aliases=["r"])
    async def raise_(ctx, size: str = None):
        await _do(ctx, "raise", size)

    @bot.command(name="pot")
    async def pot(ctx):
        await _do(ctx, "pot")

    @bot.command(name="allin", aliases=["shove", "jam"])
    async def allin(ctx):
        await _do(ctx, "allin")

    @bot.command(name="endpoker", aliases=["pend"])
    async def endpoker(ctx):
        table.reset_all()
        await ctx.send("🛑 Poker table cleared.")

    return table


# ---------------------------------------------------------------------------
# Self-test (no Discord) — `python poker.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # hand evaluation sanity
    assert hand_rank(["A♠", "K♠", "Q♠", "J♠", "T♠"])[0] == 8      # royal/straight flush
    assert hand_rank(["A♠", "A♥", "A♦", "A♣", "K♠"])[0] == 7      # quads
    assert hand_rank(["A♠", "A♥", "A♦", "K♣", "K♠"])[0] == 6      # full house
    assert hand_rank(["2♠", "3♠", "4♠", "5♠", "7♠"])[0] == 5      # flush
    assert hand_rank(["A♠", "2♥", "3♦", "4♣", "5♠"])[0] == 4      # wheel straight
    assert hand_rank(["A♠", "A♥", "A♦", "5♣", "7♠"])[0] == 3      # trips
    assert hand_rank(["A♠", "A♥", "K♦", "K♣", "7♠"])[0] == 2      # two pair
    assert hand_rank(["A♠", "A♥", "K♦", "Q♣", "7♠"])[0] == 1      # pair
    assert hand_rank(["A♠", "K♥", "Q♦", "J♣", "9♠"])[0] == 0      # high card
    assert hand_rank(["A♠", "A♥", "A♦", "K♠", "Q♠", "J♠", "T♠"])[0] == 8  # 7-card straight flush
    print("hand_rank: OK")

    # fraction + drink rendering
    assert frac(1) == "1/16" and frac(2) == "1/8" and frac(4) == "1/4"
    assert frac(8) == "1/2" and frac(16) == "1" and frac(6) == "3/8"
    assert drink_call(1) == "a sip"
    assert drink_call(16) == "a shot 🥃"
    assert drink_call(5) == "2 drinks + a sip"
    print("frac/drink_call: OK")

    # drive a scripted heads-up hand to showdown without exceptions
    random.seed(0)
    t = Table()
    t.seat(1, "Colin")
    t.seat(2, "Jake")
    assert t.start_hand()
    log = []
    # preflop: button(SB) acts first heads-up
    for _ in range(60):
        if not t.in_hand:
            break
        p = t.players[t.to_act]
        tc = t.to_call(p)
        ok, msg = t.act(p, "call" if tc > 0 else "check")
        assert ok, msg
        log += t.progress()
    assert not t.in_hand, "hand should have completed"
    assert any("SHOWDOWN" in x for x in log)
    assert t.last_result and t.last_result["pot"] > 0 and t.last_result["winners"]
    # pot is conserved: winners' winnings == everyone's contributions
    assert sum(t.last_result["bets"].values()) == t.last_result["pot"]
    print("heads-up hand: OK")

    # 3-way with a fold and a raise
    random.seed(3)
    t = Table()
    for i, nm in enumerate(["Colin", "Jake", "Nick"], start=1):
        t.seat(i, nm)
    assert t.start_hand()
    steps = 0
    first = True
    while t.in_hand and steps < 80:
        steps += 1
        p = t.players[t.to_act]
        tc = t.to_call(p)
        if first and tc > 0:
            ok, _ = t.act(p, "fold")          # first to act folds
            first = False
        else:
            ok, _ = t.act(p, "call" if tc > 0 else "check")
        assert ok
        t.progress()
    assert not t.in_hand
    print("3-way hand: OK")

    # short-stacked all-in (within the pot limit) runs out the board to showdown
    random.seed(7)
    t = Table(start_stack=4)          # tiny stacks so a shove is legal pot-limit
    t.seat(1, "Colin")
    t.seat(2, "Jake")
    assert t.start_hand()
    p = t.players[t.to_act]
    ok, _ = t.act(p, "allin")
    assert ok and p in t.allin, "short shove should be all-in"
    t.progress()
    p = t.players[t.to_act]
    t.act(p, "call")
    t.progress()
    assert not t.in_hand, "all-in run-out should reach showdown"
    assert len(t.board) == 5, "board must be fully dealt on run-out"
    # pot conservation at result level (before auto-rebuy): contributions == pot
    assert sum(t.last_result["bets"].values()) == t.last_result["pot"]
    print("all-in hand: OK")

    # pot-limit cap: an 'allin' for more than the pot is capped, NOT all-in
    random.seed(1)
    t = Table(start_stack=16)
    t.seat(1, "Colin")
    t.seat(2, "Jake")
    t.start_hand()
    p = t.players[t.to_act]
    t.act(p, "allin")                 # only 6 (pot) is legal, so not really all-in
    assert p not in t.allin and t.current_bet == 6
    print("pot-limit cap: OK")
    print("ALL POKER SELF-TESTS PASSED ✅")
