from __future__ import annotations

import datetime

import discord
from discord import ui, app_commands
from discord.ext import commands

from discord.ext import tasks

import config
from utils import storage

STORE_NAME = "staff_activity"

# --- Emojis (μόνο εδώ, όχι στο emojis.py) ---
EMOJI_ON_DUTY = "<a:on_duty:1548269590197575701>"
EMOJI_OFF_DUTY = "<a:off_duty:1548269566659002479>"
EMOJI_LEADERBOARD = "<:leaderboard:1548270973285957662>"

# --- Panel emojis ---
PANEL_TITLE_EMOJI = "<a:name:1573488190977876179>"
PANEL_INFO_EMOJI = "<:name:1573467024846225560>"
PANEL_REFRESH_EMOJI = discord.PartialEmoji(name="name", id=1573490238955724840)

active_sessions: dict[int, datetime.datetime] = {}


DEAFEN_MUTE_KICK_SECONDS = 20 * 60 
_deaf_mute_since: dict[int, datetime.datetime] = {}


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if not h and not m:
        parts.append(f"{s}s")
    return " ".join(parts)


def _is_deaf_mute(state: discord.VoiceState) -> bool:
    return bool((state.deaf or state.self_deaf) and (state.mute or state.self_mute))


class StaffActivity(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.deaf_mute_check_loop.start()
        persistent_view = ui.LayoutView(timeout=None)
        persistent_container = ui.Container(accent_colour=discord.Colour.from_str("#A600FF"))
        persistent_container.add_item(ui.ActionRow())
        persistent_container.children[-1].add_item(self._make_refresh_button())
        persistent_view.add_item(persistent_container)
        self.bot.add_view(persistent_view)

    def cog_unload(self):
        self.deaf_mute_check_loop.cancel()

    async def _log(self, guild: discord.Guild, member: discord.Member, *, joined: bool, duration_seconds: int | None = None):
        channel = guild.get_channel(config.STAFF_ACTIVITY_LOG_CHANNEL_ID)
        if not channel:
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        embed = discord.Embed(color=config.EMBED_COLOR, timestamp=now)
        if joined:
            embed.title = f"{EMOJI_ON_DUTY} On Duty - Join"
            embed.description = f"{member.mention} μπήκε on duty."
        else:
            embed.title = f"{EMOJI_OFF_DUTY} On Duty - Leave"
            embed.description = f"{member.mention} βγήκε από on duty.\nΔιάρκεια: **{_fmt_duration(duration_seconds or 0)}**"
        embed.add_field(name="Ώρα", value=discord.utils.format_dt(now, style="F"))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):

        target_ids = set(config.ACTIVITY_CHANNELS_IDS.values())
        role = member.guild.get_role(config.ON_DUTY_ROLE_ID)

        before_in = bool(before.channel and before.channel.id in target_ids)
        after_in = bool(after.channel and after.channel.id in target_ids)

        if after_in and not before_in:
            active_sessions[member.id] = datetime.datetime.now(datetime.timezone.utc)
            if role:
                await member.add_roles(role, reason="Staff Activity - on duty")
            await self._log(member.guild, member, joined=True)

        elif before_in and not after_in:
            start = active_sessions.pop(member.id, None)
            duration = 0
            if start:
                duration = (datetime.datetime.now(datetime.timezone.utc) - start).total_seconds()
            store = storage.get_store(STORE_NAME)
            store[str(member.id)] = store.get(str(member.id), 0) + duration
            storage.save(STORE_NAME, store)
            if role:
                await member.remove_roles(role, reason="Staff Activity - off duty")
            await self._log(member.guild, member, joined=False, duration_seconds=int(duration))

        if after_in and _is_deaf_mute(after):
            _deaf_mute_since.setdefault(member.id, datetime.datetime.now(datetime.timezone.utc))
        else:
            _deaf_mute_since.pop(member.id, None)

    @tasks.loop(seconds=60)
    async def deaf_mute_check_loop(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        target_ids = set(config.ACTIVITY_CHANNELS_IDS.values())
        for member_id, since in list(_deaf_mute_since.items()):
            if (now - since).total_seconds() < DEAFEN_MUTE_KICK_SECONDS:
                continue
            for guild in self.bot.guilds:
                member = guild.get_member(member_id)
                if not member or not member.voice or not member.voice.channel:
                    continue
                if member.voice.channel.id not in target_ids:
                    continue

                if not _is_deaf_mute(member.voice):
                    continue
                try:
                    await member.move_to(None, reason="On Duty - deaf+mute πάνω από 20 λεπτά")
                except discord.HTTPException:
                    pass
                _deaf_mute_since.pop(member_id, None)
                break

    @deaf_mute_check_loop.before_loop
    async def before_deaf_mute_check_loop(self):
        await self.bot.wait_until_ready()


    def _make_refresh_button(self) -> ui.Button:
        refresh_btn = ui.Button(label=" 〃 Refresh", style=discord.ButtonStyle.secondary,
                                 emoji=PANEL_REFRESH_EMOJI, custom_id="staff_activity_refresh")

        async def _on_refresh(interaction: discord.Interaction):
            member = interaction.user
            has_access = any(r.id == config.OWNERSHIP_ROLE_ID for r in getattr(member, "roles", []))
            if not has_access:
                await interaction.response.send_message("Δεν έχεις δικαίωμα να κάνεις refresh.", ephemeral=True)
                return
            new_view = self._build_panel_view(interaction.guild)
            await interaction.response.edit_message(view=new_view)

        refresh_btn.callback = _on_refresh
        return refresh_btn

    def _build_panel_view(self, guild: discord.Guild) -> ui.LayoutView:
        store = storage.get_store(STORE_NAME)
        totals = dict(store)
        now = datetime.datetime.now(datetime.timezone.utc)
        for uid, start in active_sessions.items():
            totals[str(uid)] = totals.get(str(uid), 0) + (now - start).total_seconds()

        ranking = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:10]
        lines = []
        for uid, secs in ranking:
            member = guild.get_member(int(uid)) if guild else None
            name = member.mention if member else f"`{uid}`"
            lines.append(f"{name} — {_fmt_duration(secs)}")
        staff_text = "\n".join(lines) if lines else "Δεν υπάρχουν δεδομένα ακόμα."

        sep = lambda: ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)

        view = ui.LayoutView(timeout=None)
        view.add_item(
            ui.Container(
                sep(),
                ui.TextDisplay(f"## {PANEL_TITLE_EMOJI} 〃 **__Medusa Arena - Staff Status__**"),
                sep(),
                ui.TextDisplay(f"{PANEL_INFO_EMOJI} ***Για να ξεκινήσει να μετράει ο χρόνος σου μπες σε ένα On Duty Channel. ***"),
                sep(),
                ui.TextDisplay(staff_text),
                sep(),
                ui.ActionRow(self._make_refresh_button()),
                sep(),
                accent_colour=0xA600FF,
            )
        )
        return view


    @app_commands.command(name="panel-staff-activity", description="Στέλνει το Staff Activity leaderboard panel")
    @app_commands.checks.has_role(config.OWNERSHIP_ROLE_ID)
    async def panel_staff_activity(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        view = self._build_panel_view(interaction.guild)
        await interaction.channel.send(view=view)
        await interaction.followup.send("Στάλθηκε.", ephemeral=True)

    @app_commands.command(name="resettime", description="Κάνει reset τον χρόνο on duty (όλο το panel ή έναν συγκεκριμένο χρήστη)")
    @app_commands.describe(user="Αν δοθεί, γίνεται reset μόνο ο χρόνος αυτού του χρήστη")
    @app_commands.checks.has_role(config.OWNERSHIP_ROLE_ID)
    async def resettime(self, interaction: discord.Interaction, user: discord.Member | None = None):
        if user is None:
            storage.save(STORE_NAME, {})
            active_sessions.clear()
            await interaction.response.send_message(
                f"{EMOJI_LEADERBOARD} Έγινε reset **όλο** το Staff Activity panel.",
                ephemeral=True,
            )
        else:
            store = storage.get_store(STORE_NAME)
            store.pop(str(user.id), None)
            storage.save(STORE_NAME, store)
            active_sessions.pop(user.id, None)
            await interaction.response.send_message(
                f"{EMOJI_LEADERBOARD} Έγινε reset ο χρόνος του {user.mention}.",
                ephemeral=True,
            )

    @resettime.error
    async def resettime_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message("Μόνο το Ownership μπορεί να κάνει reset.", ephemeral=True)
        else:
            raise error

async def setup(bot: commands.Bot):
    await bot.add_cog(StaffActivity(bot))
