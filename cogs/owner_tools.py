from __future__ import annotations

import asyncio
import re

import aiohttp
import discord
from discord.ext import commands

from emojis import emoji
from utils.permissions import is_ownership_only

EMOJI_RE = re.compile(r"<(a?):(\w{2,32}):(\d{15,25})>")
URL_RE = re.compile(
    r"https?://cdn\.discordapp\.com/emojis/(\d{15,25})\.(png|webp|gif|jpg|jpeg)(\?[^\s>]*)?", re.I
)
NAME_PARAM_RE = re.compile(r"[?&]name=(\w+)")

MAX_EMOJIS_PER_COMMAND = 30

OK = emoji("applications", "accept") or "✅"
FAIL = emoji("applications", "deny") or "❌"


def _clean_name(name: str) -> str:
    name = re.sub(r"\W", "_", name)[:32]
    return name if len(name) >= 2 else (name + "_e")[:32]


def parse_emojis(content: str) -> tuple[list[tuple[bool, str, int]], str]:
    """Βρίσκει custom emojis (<:n:id>, <a:n:id>) και CDN links. Επιστρέφει (λίστα, υπόλοιπο κείμενο)."""
    found: list[tuple[bool, str, int]] = []

    for m in EMOJI_RE.finditer(content):
        found.append((m.group(1) == "a", m.group(2), int(m.group(3))))
    rest = EMOJI_RE.sub("", content)

    for m in URL_RE.finditer(rest):
        query = m.group(3) or ""
        animated = m.group(2).lower() == "gif" or "animated=true" in query.lower()
        nm = NAME_PARAM_RE.search(query)
        found.append((animated, nm.group(1) if nm else "emoji", int(m.group(1))))
    rest = URL_RE.sub("", rest)

    # αφαίρεση διπλότυπων (ίδιο id)
    seen, unique = set(), []
    for item in found:
        if item[2] not in seen:
            seen.add(item[2])
            unique.append(item)
    return unique, rest.strip()


class OwnerTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # !e -> @everyone και διαγραφή μετά από 2 δευτερόλεπτα
    @commands.command(name="e")
    @commands.guild_only()
    @is_ownership_only()
    async def everyone_ping(self, ctx: commands.Context):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        msg = await ctx.send("@everyone", allowed_mentions=discord.AllowedMentions(everyone=True))
        await asyncio.sleep(2)
        try:
            await msg.delete()
        except discord.HTTPException:
            pass

    # !adde <emoji> [όνομα]  -> προσθέτει emoji (και animated) από οποιονδήποτε server
    @commands.command(name="adde")
    @commands.guild_only()
    @is_ownership_only()
    async def add_emoji(self, ctx: commands.Context, *, text: str = ""):
        found, rest = parse_emojis(text)

        custom_name = None
        if len(found) == 1 and rest and len(rest.split()) == 1:
            custom_name = _clean_name(rest)

        # 1) Αν δεν έγραψες emoji αλλά έκανες reply σε μήνυμα: παίρνει τα emojis από το κείμενο και τα reactions του
        if not found and ctx.message.reference:
            ref = ctx.message.reference.resolved
            if not isinstance(ref, discord.Message) and ctx.message.reference.message_id:
                try:
                    ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                except discord.HTTPException:
                    ref = None
            if isinstance(ref, discord.Message):
                found, _ = parse_emojis(ref.content)
                seen = {f[2] for f in found}
                for reaction in ref.reactions:
                    e = reaction.emoji
                    eid = getattr(e, "id", None)
                    if eid and eid not in seen:
                        seen.add(eid)
                        found.append((bool(getattr(e, "animated", False)), e.name, eid))

        # 2) Αν γράψεις μόνο όνομα (π.χ. !adde party): ψάχνει σε όλους τους servers που είναι το bot
        if not found and rest:
            for word in rest.split():
                match = discord.utils.find(lambda em: em.name.lower() == word.lower(), self.bot.emojis)
                if match:
                    found.append((match.animated, match.name, match.id))

        if not found:
            await ctx.send(
                delete_after=10,
            )
            return

        skipped = max(0, len(found) - MAX_EMOJIS_PER_COMMAND)
        found = found[:MAX_EMOJIS_PER_COMMAND]

        lines: list[str] = []
        timeout = aiohttp.ClientTimeout(total=30)
        async with ctx.typing():
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for animated, name, eid in found:
                    final_name = custom_name or _clean_name(name)
                    ext = "gif" if animated else "png"
                    url = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
                    try:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                lines.append(f"{FAIL} `{final_name}`: δεν βρέθηκε το emoji (ID `{eid}`).")
                                continue
                            image = await resp.read()
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        lines.append(f"{FAIL} `{final_name}`: αποτυχία")
                        continue

                    try:
                        created = await ctx.guild.create_custom_emoji(
                            name=final_name, image=image, reason=f"!adde από {ctx.author}"
                        )
                        lines.append(f"{OK} {created} `{created.name}`")
                    except discord.Forbidden:
                        lines.append(f"{FAIL} `{final_name}`: το bot δεν έχει δικαίωμα Manage Expressions.")
                    except discord.HTTPException as e:
                        lines.append(f"{FAIL} `{final_name}`: {e.text}")

        if skipped:
            lines.append(f"Παραπανω {skipped} emojis απο {MAX_EMOJIS_PER_COMMAND}.")
        await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerTools(bot))
