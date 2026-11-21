from __future__ import annotations

import datetime
import os

import discord
from discord import ui, app_commands
from discord.ext import commands

import config
from utils import storage

STORE_NAME = "criminal_applications"

LOGO_FILENAME = "medusa_logo.png"
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", LOGO_FILENAME)

# --- Emojis (μόνο εδώ) -------------------------------------------------------
# Άλλαξε τα ID με τα δικά σου custom emojis όποτε θες.
EMOJI_TITLE = "<:name:1573499489031946271>"
EMOJI_INFO = "<:name:1573453240052486277>"
EMOJI_APPLY = discord.PartialEmoji(name="name", id=1573499489031946271)
EMOJI_ACCEPT_TXT = "<:wl_accept:1548269957039525918>"
EMOJI_ACCEPT = discord.PartialEmoji(name="wl_accept", id=1548269957039525918)
EMOJI_DENY_TXT = "<:ban:>"
EMOJI_DENY = discord.PartialEmoji(name="ban", id=1548269903780511785)

# --- Χρώματα -----------------------------------------------------------------
COLOR_PENDING = 0x7014E1
COLOR_ACCEPTED = 0x57F287
COLOR_DENIED = 0xED4245

# --- Ερωτήσεις (key, label, placeholder, max_length) ---------------------------
FIELDS = [
    ("job", "Criminal Team Name & Type", "Όνομα Team & Τύπος (Cartel / Mafia / Gang)", 100),
    ("members", "Πόσοι είστε;", "Γράψε τον αριθμό των μελών", 20),
    ("server", "Από ποιον server έρχεστε;", "Γράψε το όνομα του server", 100),
    ("activity", "Daily Activity In-Game (Member)", "Πόσες ώρες την ημέρα είστε in-game;", 100),
]

ID_APPLY = "criminal_app_apply"
ID_ACCEPT = "criminal_app_accept"
ID_DENY = "criminal_app_deny"


def _sep() -> ui.Separator:
    return ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)


def _is_owner(member) -> bool:
    return any(r.id == config.OWNERSHIP_ROLE_ID for r in getattr(member, "roles", []))


def _get_data(message_id: int) -> dict | None:
    return storage.get_store(STORE_NAME).get(str(message_id))


def _save_data(message_id: int, data: dict) -> None:
    store = storage.get_store(STORE_NAME)
    store[str(message_id)] = data
    storage.save(STORE_NAME, store)


# =============================================================================
# Panel (το μήνυμα με το κουμπί Apply)
# =============================================================================

def _apply_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.secondary, label="〃 Apply",
                    emoji=EMOJI_APPLY, custom_id=ID_APPLY)

    async def _cb(interaction: discord.Interaction):
        await interaction.response.send_modal(ApplyModal())

    btn.callback = _cb
    return btn


def build_panel_view(with_logo: bool) -> ui.LayoutView:
    text = ui.TextDisplay(
        f"{EMOJI_INFO} Αν θες να αποκτήσεις ένα criminal team στον server μας, πάτα το κουμπί "
        "**\"Apply\"** παρακάτω και απάντα σε μερικές ερωτήσεις σχετικά με το team σου. "
        "__Παρακαλούμε απάντα ειλικρινά.__"
    )
    intro = ui.Section(text, accessory=ui.Thumbnail(f"attachment://{LOGO_FILENAME}")) if with_logo else text

    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Criminal Application__**"),
            _sep(),
            intro,
            _sep(),
            ui.ActionRow(_apply_button()),
            _sep(),
            accent_colour=COLOR_PENDING,
        )
    )
    return view


# =============================================================================
# Modal αίτησης
# =============================================================================

class ApplyModal(ui.Modal, title="Criminal Application"):
    def __init__(self):
        super().__init__(timeout=None)
        self.inputs: dict[str, ui.TextInput] = {}
        for key, label, placeholder, max_len in FIELDS:
            inp = ui.TextInput(label=label, placeholder=placeholder, required=True,
                               max_length=max_len, style=discord.TextStyle.short)
            self.inputs[key] = inp
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        channel_id = getattr(config, "APPLICATION_CHANNEL_ID", 0)
        channel = interaction.guild.get_channel(channel_id) if interaction.guild and channel_id else None
        if channel is None:
            await interaction.response.send_message(
                "Δεν έχει οριστεί το κανάλι αιτήσεων. Ενημέρωσε το Ownership.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        data = {
            "user_id": interaction.user.id,
            "answers": {key: inp.value.strip() for key, inp in self.inputs.items()},
            "created": datetime.datetime.now(datetime.timezone.utc).timestamp(),
            "status": "pending",
        }
        msg = await channel.send(view=build_review_view(data), allowed_mentions=discord.AllowedMentions.none())
        _save_data(msg.id, data)

        await interaction.followup.send(
            f"{EMOJI_ACCEPT_TXT} Η αίτησή σου στάλθηκε. Θα λάβεις απάντηση στα DMs σου, μην τα έχεις κλειστά.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        msg = "Δοκίμασε ξανά."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        raise error


# =============================================================================
# Μήνυμα αίτησης στο κανάλι review (Accept / Deny)
# =============================================================================

def _review_row() -> ui.ActionRow:
    accept = ui.Button(style=discord.ButtonStyle.success, label="Accept",
                       emoji=EMOJI_ACCEPT, custom_id=ID_ACCEPT)
    deny = ui.Button(style=discord.ButtonStyle.danger, label="Deny",
                     emoji=EMOJI_DENY, custom_id=ID_DENY)
    accept.callback = _on_accept
    deny.callback = _on_deny
    return ui.ActionRow(accept, deny)


def build_review_view(data: dict) -> ui.LayoutView:
    status = data.get("status", "pending")
    answers = data["answers"]
    ts = int(data["created"])

    answer_lines = [f"{EMOJI_INFO} **{label}**\n{answers.get(key, '—')}" for key, label, _, _ in FIELDS]

    items: list = [
        _sep(),
        ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Νέα Criminal Application__**"),
        _sep(),
        ui.TextDisplay(f"{EMOJI_INFO} **User:** <@{data['user_id']}> (`{data['user_id']}`)\n"
                       f"{EMOJI_INFO} **Ώρα:** <t:{ts}:F>"),
        _sep(),
        ui.TextDisplay("\n\n".join(answer_lines)),
        _sep(),
    ]

    if status == "pending":
        items.append(_review_row())
        color = COLOR_PENDING
    else:
        by = f"<@{data['decided_by']}>"
        if status == "accepted":
            text = f"{EMOJI_ACCEPT_TXT} **Αποδεκτή** από {by}"
            color = COLOR_ACCEPTED
        else:
            text = f"{EMOJI_DENY_TXT} **Απορρίφθηκε** από {by}"
            if data.get("reason"):
                text += f"\n{EMOJI_INFO} **Λόγος:** {data['reason']}"
            color = COLOR_DENIED
        if data.get("dm_failed"):
            text += f"\n{EMOJI_INFO} κλειστά dms."
        items.append(ui.TextDisplay(text))

    items.append(_sep())

    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=color))
    return view


def _build_dm_view(data: dict, accepted: bool, reason: str | None) -> ui.LayoutView:
    job = data["answers"].get("job", "—")
    if accepted:
        text = (f"{EMOJI_ACCEPT_TXT} Η αίτησή σου για το criminal team **{job}** έγινε **δεκτή**! "
                "Θα επικοινωνήσουμε σύντομα μαζί σου.")
        color = COLOR_ACCEPTED
    else:
        text = f"{EMOJI_DENY_TXT} Η αίτησή σου για το criminal team **{job}** **απορρίφθηκε**."
        if reason:
            text += f"\n\n{EMOJI_INFO} **Λόγος:** {reason}"
        color = COLOR_DENIED

    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Criminal Application__**"),
            _sep(),
            ui.TextDisplay(text),
            _sep(),
            accent_colour=color,
        )
    )
    return view


async def _finalize(interaction: discord.Interaction, message_id: int, *, accepted: bool, reason: str | None):
    """Καλείται αφού έχει γίνει defer(). Ενημερώνει αποθήκευση, DM και το μήνυμα."""
    data = _get_data(message_id)
    if not data or data.get("status") != "pending":
        await interaction.followup.send("Eχει απαντηθεί.", ephemeral=True)
        return

    data["status"] = "accepted" if accepted else "denied"
    data["decided_by"] = interaction.user.id
    data["reason"] = reason
    data["decided_at"] = datetime.datetime.now(datetime.timezone.utc).timestamp()

    try:
        user = interaction.client.get_user(data["user_id"]) or await interaction.client.fetch_user(data["user_id"])
        await user.send(view=_build_dm_view(data, accepted, reason))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        data["dm_failed"] = True

    _save_data(message_id, data)
    await interaction.edit_original_response(view=build_review_view(data))


async def _on_accept(interaction: discord.Interaction):
    if not _is_owner(interaction.user):
        await interaction.response.send_message("Μόνο το Ownership μπορεί να απαντήσει.", ephemeral=True)
        return
    data = _get_data(interaction.message.id)
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε η αίτηση.", ephemeral=True)
        return
    if data.get("status") != "pending":
        await interaction.response.send_message("Αυτή η αίτηση έχει απαντηθεί.", ephemeral=True)
        return
    await interaction.response.defer()
    await _finalize(interaction, interaction.message.id, accepted=True, reason=None)


async def _on_deny(interaction: discord.Interaction):
    if not _is_owner(interaction.user):
        await interaction.response.send_message("Μόνο το Ownership μπορεί να απαντήσει.", ephemeral=True)
        return
    data = _get_data(interaction.message.id)
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε η αίτηση.", ephemeral=True)
        return
    if data.get("status") != "pending":
        await interaction.response.send_message("Αυτή η αίτηση έχει απαντηθεί.", ephemeral=True)
        return
    await interaction.response.send_modal(DenyModal(interaction.message.id))


class DenyModal(ui.Modal, title="Deny Application"):
    reason = ui.TextInput(label="Λόγος (προαιρετικό)", placeholder="Γράψε τον λόγο απόρριψης...",
                          required=False, max_length=500, style=discord.TextStyle.paragraph)

    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_owner(interaction.user):
            await interaction.response.send_message("Μόνο το Ownership μπορεί να απαντήσει.", ephemeral=True)
            return
        await interaction.response.defer()
        reason = self.reason.value.strip() or None
        await _finalize(interaction, self.message_id, accepted=False, reason=reason)


# =============================================================================
# Cog
# =============================================================================

class Applications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Persistent views ώστε τα κουμπιά να δουλεύουν και μετά από restart.
        panel = ui.LayoutView(timeout=None)
        panel.add_item(ui.Container(ui.ActionRow(_apply_button()), accent_colour=COLOR_PENDING))
        self.bot.add_view(panel)

        review = ui.LayoutView(timeout=None)
        review.add_item(ui.Container(_review_row(), accent_colour=COLOR_PENDING))
        self.bot.add_view(review)

    @app_commands.command(name="panel-criminal-application")
    @app_commands.checks.has_role(config.OWNERSHIP_ROLE_ID)
    async def panel_criminal_application(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        has_logo = os.path.exists(LOGO_PATH)
        files = [discord.File(LOGO_PATH, filename=LOGO_FILENAME)] if has_logo else []
        await interaction.channel.send(view=build_panel_view(has_logo), files=files)
        note = "Στάλθηκε." if has_logo else f"Στάλθηκε χωρίς logo (δεν βρέθηκε το `assets/{LOGO_FILENAME}`)."
        await interaction.followup.send(note, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Applications(bot))
