from __future__ import annotations

import asyncio
import datetime
import re
import time

import discord
from discord import ui, app_commands
from discord.ext import commands

import config
from emojis import emoji
from utils import storage

STORE_OPEN = "staff_app_open"      # ανοιχτά application channels  (key = channel id)
STORE_REVIEW = "staff_app_review"  # αιτήσεις στο review channel   (key = message id)

# --- Emojis (μόνο εδώ) -------------------------------------------------------
EMOJI_TITLE = "<:name:1573454068372873298>"
EMOJI_INFO = "<:name:1573453240052486277>"
EMOJI_NOTE = "<:name:1573453842878828684>"
EMOJI_APPLY = discord.PartialEmoji(name="name", id=1573457240701276180)


def _partial(text: str) -> discord.PartialEmoji | None:
    return discord.PartialEmoji.from_str(text) if text else None


# Accept / Deny: emojis.py -> "applications"  |  Start / Close / Ping / Send: emojis.py -> "staff_applications"
EMOJI_ACCEPT_TXT = emoji("applications", "accept")
EMOJI_DENY_TXT = emoji("applications", "deny")
EMOJI_ACCEPT = _partial(EMOJI_ACCEPT_TXT)
EMOJI_DENY = _partial(EMOJI_DENY_TXT)
EMOJI_START = _partial(emoji("staff_applications", "start"))
EMOJI_CLOSE = _partial(emoji("staff_applications", "close"))
EMOJI_PING = _partial(emoji("staff_applications", "ping"))
EMOJI_SEND = _partial(emoji("staff_applications", "send"))

# --- Χρώματα -----------------------------------------------------------------
COLOR_PANEL = 0x8000FF
COLOR_ACCEPTED = 0x57F287
COLOR_DENIED = 0xED4245

# --- Ερωτήσεις (key, label, max_length) -------------------------------------
# ΠΡΟΣΩΡΙΝΕΣ: άλλαξέ τες όπως θες. Κράτα το άθροισμα των max_length κάτω από ~2200.
QUESTIONS = [
    ("name_age", "Όνομα & ηλικία", 60),
    ("activity", "Πόσες ώρες την ημέρα είσαι in-game;", 100),
    ("experience", "Έχεις εμπειρία ως staff; Πού και πόσο καιρό;", 300),
    ("why", "Γιατί θες να γίνεις staff στον server μας;", 500),
    ("scenario", "Τι θα έκανες αν έβλεπες παίκτη να σπάει τους κανόνες;", 500),
    ("availability", "Ποιες μέρες και ώρες είσαι διαθέσιμος;", 200),
    ("offer", "Τι μπορείς να προσφέρεις στον server;", 300),
]

ID_APPLY = "staff_app_apply"
ID_START = "staff_app_start"
ID_CLOSE = "staff_app_close"
ID_PING = "staff_app_ping"
ID_SEND = "staff_app_send"
ID_ACCEPT = "staff_app_accept"
ID_DENY = "staff_app_deny"

CLOSE_DELAY = 5
PING_COOLDOWN = 30
_ping_cooldowns: dict[int, float] = {}


# =============================================================================
# Helpers
# =============================================================================

def _cfg(name: str, default=None):
    return getattr(config, name, default)


def _sep() -> ui.Separator:
    return ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)


def _now() -> float:
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _is_owner(member) -> bool:
    return any(r.id == config.OWNERSHIP_ROLE_ID for r in getattr(member, "roles", []))


# --- Ανοιχτά applications ---
# Cache με τα IDs των ανοιχτών channels, ώστε το on_message να μην διαβάζει τη βάση σε κάθε μήνυμα.
_OPEN_IDS: set[int] = set()


def _get_open(channel_id: int) -> dict | None:
    return storage.get_store(STORE_OPEN).get(str(channel_id))


def _save_open(channel_id: int, data: dict) -> None:
    store = storage.get_store(STORE_OPEN)
    store[str(channel_id)] = data
    storage.save(STORE_OPEN, store)
    _OPEN_IDS.add(channel_id)


def _delete_open(channel_id: int) -> None:
    _OPEN_IDS.discard(channel_id)
    store = storage.get_store(STORE_OPEN)
    if store.pop(str(channel_id), None) is not None:
        storage.save(STORE_OPEN, store)


def _find_open_by_user(user_id: int) -> tuple[int, dict] | None:
    for cid, data in storage.get_store(STORE_OPEN).items():
        if data.get("user_id") == user_id:
            return int(cid), data
    return None


# --- Αιτήσεις στο review ---
def _get_review(message_id: int) -> dict | None:
    return storage.get_store(STORE_REVIEW).get(str(message_id))


def _save_review(message_id: int, data: dict) -> None:
    store = storage.get_store(STORE_REVIEW)
    store[str(message_id)] = data
    storage.save(STORE_REVIEW, store)


# =============================================================================
# Panel (το μήνυμα με το κουμπί Apply For Staff)
# =============================================================================

def _apply_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.secondary, label="〃 Apply For Staff",
                    emoji=EMOJI_APPLY, custom_id=ID_APPLY)
    btn.callback = _on_apply
    return btn


def build_panel_view() -> ui.LayoutView:
    banner = _cfg("STAFF_APP_BANNER_URL", "")
    logo = _cfg("STAFF_APP_LOGO_URL", "")

    intro_text = ui.TextDisplay(
        f"-  {EMOJI_INFO} Για να γίνεις μέλος του staff team και να βοηθήσεις τον server "
        "__πάτα το κουμπί παρακάτω και απάντα στις ερωτήσεις.__ "
        "***Φρόντισε να απαντήσεις ειλικρινά και χωρίς την χρήση του AI.***"
    )
    intro = ui.Section(intro_text, accessory=ui.Thumbnail(logo)) if logo else intro_text

    items: list = [_sep()]
    if banner:
        items += [ui.MediaGallery(discord.MediaGalleryItem(banner)), _sep()]
    items += [
        ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Application__**"),
        _sep(),
        intro,
        _sep(),
        ui.ActionRow(_apply_button()),
        _sep(),
        ui.TextDisplay(
            f"> -# {EMOJI_NOTE} 〃 __*Αν δεν ολοκληρωθεί το application ή δεν το στείλεις "
            "το channel σου θα κλείσει.*__"
        ),
        _sep(),
    ]
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=COLOR_PANEL))
    return view


async def _on_apply(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Μόνο μέσα σε server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    user = interaction.user

    # Ήδη ανοιχτό application;
    existing = _find_open_by_user(user.id)
    if existing:
        cid, _ = existing
        ch = guild.get_channel(cid)
        if ch is not None:
            await interaction.followup.send(f"Έχεις ήδη ανοιχτό application: {ch.mention}", ephemeral=True)
            return
        _delete_open(cid)  # το channel έχει σβηστεί, καθάρισμα

    category = guild.get_channel(_cfg("STAFF_APP_CATEGORY_ID", 0))
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send("Δεν έχει οριστεί category για τα applications. Ενημέρωσε το Ownership.",
                                        ephemeral=True)
        return

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                              manage_channels=True, manage_messages=True),
    }
    for rid in _cfg("STAFF_APP_VIEWER_ROLE_IDS", [config.OWNERSHIP_ROLE_ID]):
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                           read_message_history=True)

    base = re.sub(r"[^a-z0-9_-]", "", user.name.lower())[:80] or str(user.id)
    try:
        channel = await category.create_text_channel(
            name=f"application-{base}", overwrites=overwrites, reason=f"Staff application από {user}")
    except discord.HTTPException as e:
        await interaction.followup.send(f"Δεν μπόρεσα να ανοίξω το channel: {e.text}", ephemeral=True)
        return

    data = {
        "user_id": user.id,
        "created": _now(),
        "last_activity": _now(),
        "started": False,
        "index": 0,
        "answers": {},
    }
    try:
        await channel.send(view=build_channel_view(data),
                           allowed_mentions=discord.AllowedMentions(users=[user]))
    except discord.HTTPException:
        await channel.delete(reason="Αποτυχία αποστολής panel")
        await interaction.followup.send("Κάτι πήγε στραβά. Δοκίμασε ξανά.", ephemeral=True)
        return
    _save_open(channel.id, data)

    await interaction.followup.send(f"Άνοιξε το application σου: {channel.mention}", ephemeral=True)


# =============================================================================
# Panel μέσα στο application channel (Start / Close Application / Ping User)
# =============================================================================

def _start_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.secondary, label="〃 Start", emoji=EMOJI_START, custom_id=ID_START)
    btn.callback = _on_start
    return btn


def _close_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.secondary, label="〃 Close Application",
                    emoji=EMOJI_CLOSE, custom_id=ID_CLOSE)
    btn.callback = _on_close
    return btn


def _ping_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.secondary, label="〃 Ping User",
                    emoji=EMOJI_PING, custom_id=ID_PING)
    btn.callback = _on_ping
    return btn


def build_channel_view(data: dict) -> ui.LayoutView:
    if data.get("started"):
        text = (f"{EMOJI_INFO} Το application ξεκίνησε. Απάντα στις ερωτήσεις με μήνυμα σε αυτό το channel.")
        buttons = [_close_button(), _ping_button()]
    else:
        text = (f"{EMOJI_INFO} Καλώς όρισες <@{data['user_id']}>! Πάτα **Start** για να ξεκινήσεις. "
                f"Θα σου κάνω {len(QUESTIONS)} ερωτήσεις και θα απαντάς με μήνυμα σε αυτό το channel.\n"
                f"-# {EMOJI_NOTE} Μόνο εσύ μπορείς να πατήσεις Start.")
        buttons = [_start_button(), _close_button(), _ping_button()]

    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Staff Application__**"),
            _sep(),
            ui.TextDisplay(text),
            _sep(),
            ui.ActionRow(*buttons),
            _sep(),
            accent_colour=COLOR_PANEL,
        )
    )
    return view


def _send_button() -> ui.Button:
    btn = ui.Button(style=discord.ButtonStyle.success, label="Send", emoji=EMOJI_SEND, custom_id=ID_SEND)
    btn.callback = _on_send
    return btn


def build_send_view() -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"{EMOJI_INFO} Απάντησες σε όλες τις ερωτήσεις. Πάτα **Send** για να σταλεί η αίτησή σου.\n"
                           f"-# {EMOJI_NOTE} Μόνο εσύ μπορείς να πατήσεις Send."),
            _sep(),
            ui.ActionRow(_send_button()),
            _sep(),
            accent_colour=COLOR_PANEL,
        )
    )
    return view


def build_sent_view() -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"{EMOJI_ACCEPT_TXT} Η αίτησή σου στάλθηκε! Θα λάβεις απάντηση στα DMs σου, "
                           "μην τα έχεις κλειστά."),
            _sep(),
            accent_colour=COLOR_ACCEPTED,
        )
    )
    return view


def _question_text(index: int) -> str:
    _, label, max_len = QUESTIONS[index]
    return f"{EMOJI_INFO} **Ερώτηση {index + 1}/{len(QUESTIONS)}:** {label}\n-# Μέχρι {max_len} χαρακτήρες."


async def _on_start(interaction: discord.Interaction):
    data = _get_open(interaction.channel.id) if interaction.channel else None
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε αυτό το application.", ephemeral=True)
        return
    if interaction.user.id != data["user_id"]:
        await interaction.response.send_message("Μόνο αυτός που άνοιξε το application μπορεί να πατήσει Start.",
                                                ephemeral=True)
        return
    if data.get("started"):
        await interaction.response.send_message("Το application έχει ξεκινήσει.", ephemeral=True)
        return

    await interaction.response.defer()
    data["started"] = True
    data["index"] = 0
    data["last_activity"] = _now()
    _save_open(interaction.channel.id, data)

    await interaction.edit_original_response(view=build_channel_view(data))
    await interaction.channel.send(_question_text(0))


async def _on_send(interaction: discord.Interaction):
    data = _get_open(interaction.channel.id) if interaction.channel else None
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε αυτό το application.", ephemeral=True)
        return
    if interaction.user.id != data["user_id"]:
        await interaction.response.send_message("Μόνο αυτός που άνοιξε το application μπορεί να πατήσει Send.",
                                                ephemeral=True)
        return
    if data.get("submitted"):
        await interaction.response.send_message("Η αίτησή σου έχει σταλεί.", ephemeral=True)
        return
    if not data.get("completed"):
        await interaction.response.send_message("Πρέπει πρώτα να απαντήσεις σε όλες τις ερωτήσεις.", ephemeral=True)
        return

    await interaction.response.defer()
    error = await _submit(interaction.guild, data)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return

    data["submitted"] = True
    data["last_activity"] = _now()
    _save_open(interaction.channel.id, data)
    await interaction.edit_original_response(view=build_sent_view())


async def _on_close(interaction: discord.Interaction):
    data = _get_open(interaction.channel.id) if interaction.channel else None
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε αυτό το application.", ephemeral=True)
        return
    if interaction.user.id == data["user_id"]:
        await interaction.response.send_message("Δεν μπορείς να το κλείσεις εσύ, πες το σε κάποιον άλλο.",
                                                ephemeral=True)
        return

    channel = interaction.channel
    _delete_open(channel.id)
    await interaction.response.send_message(
        f"{EMOJI_DENY_TXT} Το application κλείνει σε {CLOSE_DELAY} δευτερόλεπτα από {interaction.user.mention}.",
        allowed_mentions=discord.AllowedMentions.none())
    await asyncio.sleep(CLOSE_DELAY)
    try:
        await channel.delete(reason=f"Staff application εκλεισε από {interaction.user}")
    except discord.HTTPException:
        pass


async def _on_ping(interaction: discord.Interaction):
    data = _get_open(interaction.channel.id) if interaction.channel else None
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε αυτό το application.", ephemeral=True)
        return
    if interaction.user.id == data["user_id"]:
        await interaction.response.send_message("Δεν μπορείς να κάνεις ping εσενα....",
                                                ephemeral=True)
        return

    if data.get("submitted"):
        await interaction.response.send_message("Το application έχει σταλεί.", ephemeral=True)
        return

    channel = interaction.channel
    wait = _ping_cooldowns.get(channel.id, 0) + PING_COOLDOWN - time.monotonic()
    if wait > 0:
        await interaction.response.send_message(f"Περίμενε {int(wait) + 1} δευτερόλεπτα για νέο ping.",
                                                ephemeral=True)
        return
    _ping_cooldowns[channel.id] = time.monotonic()

    await interaction.response.defer(ephemeral=True)
    user = interaction.client.get_user(data["user_id"])
    if user is None:
        try:
            user = await interaction.client.fetch_user(data["user_id"])
        except discord.HTTPException:
            await interaction.followup.send("Δεν βρέθηκε ο χρήστης.", ephemeral=True)
            return

    dm = ui.LayoutView(timeout=None)
    dm.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Staff Application__**"),
            _sep(),
            ui.TextDisplay(f"{EMOJI_INFO} Σε περιμένουμε να ολοκληρώσεις το staff application σου: "
                           f"{channel.mention}"),
            _sep(),
            accent_colour=COLOR_PANEL,
        )
    )
    try:
        await user.send(view=dm)
        await interaction.followup.send(f"{EMOJI_ACCEPT_TXT} Στάλθηκε DM στον χρήστη.", ephemeral=True)
    except (discord.Forbidden, discord.HTTPException):
        # κλειστά DMs: ping μέσα στο channel
        await channel.send(f"<@{data['user_id']}> σε περιμένουμε να ολοκληρώσεις το application σου.",
                           allowed_mentions=discord.AllowedMentions(users=True))
        await interaction.followup.send("Ο χρήστης έχει κλειστά DMs εγινε ping στο channel.",
                                        ephemeral=True)


# =============================================================================
# Αποστολή στο review channel
# =============================================================================

async def _submit(guild: discord.Guild, data: dict) -> str | None:
    """Στέλνει την αίτηση στο review channel. Επιστρέφει μήνυμα σφάλματος ή None αν πήγε καλά."""
    review_channel = guild.get_channel(_cfg("STAFF_APP_REVIEW_CHANNEL_ID", 0))
    if review_channel is None:
        return "Δεν έχει οριστεί το κανάλι review. Ενημέρωσε το Ownership."

    review_data = {
        "user_id": data["user_id"],
        "answers": data["answers"],
        "created": _now(),
        "status": "pending",
    }
    try:
        msg = await review_channel.send(view=build_review_view(review_data),
                                        allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return "Δεν μπόρεσα να στείλω την αίτηση. Δοκίμασε ξανά ή ενημέρωσε το Ownership."
    _save_review(msg.id, review_data)
    return None


def _review_row() -> ui.ActionRow:
    accept = ui.Button(style=discord.ButtonStyle.success, label="Accept", emoji=EMOJI_ACCEPT, custom_id=ID_ACCEPT)
    deny = ui.Button(style=discord.ButtonStyle.danger, label="Deny", emoji=EMOJI_DENY, custom_id=ID_DENY)
    accept.callback = _on_accept
    deny.callback = _on_deny
    return ui.ActionRow(accept, deny)


def build_review_view(data: dict) -> ui.LayoutView:
    status = data.get("status", "pending")
    answers = data["answers"]
    ts = int(data["created"])

    answer_lines = [f"{EMOJI_INFO} **{label}**\n{answers.get(key, '—')}" for key, label, _ in QUESTIONS]

    items: list = [
        _sep(),
        ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Νέo Staff Application__**"),
        _sep(),
        ui.TextDisplay(f"{EMOJI_INFO} **User:** <@{data['user_id']}> (`{data['user_id']}`)\n"
                       f"{EMOJI_INFO} **Ώρα:** <t:{ts}:F>"),
        _sep(),
        ui.TextDisplay("\n\n".join(answer_lines)),
        _sep(),
    ]

    if status == "pending":
        items.append(_review_row())
        color = COLOR_PANEL
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
            text += f"\n{EMOJI_INFO} Κλειστά DMs."
        items.append(ui.TextDisplay(text))

    items.append(_sep())

    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=color))
    return view


def _build_dm_view(accepted: bool, reason: str | None) -> ui.LayoutView:
    if accepted:
        text = (f"{EMOJI_ACCEPT_TXT} Η αίτησή σου για staff έγινε **δεκτή**! "
                "Θα επικοινωνήσουμε σύντομα μαζί σου.")
        color = COLOR_ACCEPTED
    else:
        text = f"{EMOJI_DENY_TXT} Η αίτησή σου για staff **απορρίφθηκε**."
        if reason:
            text += f"\n\n{EMOJI_INFO} **Λόγος:** {reason}"
        color = COLOR_DENIED

    view = ui.LayoutView(timeout=None)
    view.add_item(
        ui.Container(
            _sep(),
            ui.TextDisplay(f"## {EMOJI_TITLE} 〃 **__Medusa Arena - Staff Application__**"),
            _sep(),
            ui.TextDisplay(text),
            _sep(),
            accent_colour=color,
        )
    )
    return view


async def _finalize(interaction: discord.Interaction, message_id: int, *, accepted: bool, reason: str | None):
    """Καλείται αφού έχει γίνει defer()."""
    data = _get_review(message_id)
    if not data or data.get("status") != "pending":
        await interaction.followup.send("Έχει απαντηθεί.", ephemeral=True)
        return

    data["status"] = "accepted" if accepted else "denied"
    data["decided_by"] = interaction.user.id
    data["reason"] = reason
    data["decided_at"] = _now()

    try:
        user = interaction.client.get_user(data["user_id"]) or await interaction.client.fetch_user(data["user_id"])
        await user.send(view=_build_dm_view(accepted, reason))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        data["dm_failed"] = True

    _save_review(message_id, data)
    await interaction.edit_original_response(view=build_review_view(data))


async def _on_accept(interaction: discord.Interaction):
    if not _is_owner(interaction.user):
        await interaction.response.send_message("Μόνο το Ownership μπορεί να απαντήσει.", ephemeral=True)
        return
    data = _get_review(interaction.message.id)
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
    data = _get_review(interaction.message.id)
    if not data:
        await interaction.response.send_message("Δεν βρέθηκε η αίτηση.", ephemeral=True)
        return
    if data.get("status") != "pending":
        await interaction.response.send_message("Αυτή η αίτηση έχει απαντηθεί.", ephemeral=True)
        return
    await interaction.response.send_modal(DenyModal(interaction.message.id))


class DenyModal(ui.Modal, title="Deny Staff Application"):
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

class StaffApplications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

        # Persistent views ώστε τα κουμπιά να δουλεύουν και μετά από restart.
        panel = ui.LayoutView(timeout=None)
        panel.add_item(ui.Container(ui.ActionRow(_apply_button()), accent_colour=COLOR_PANEL))
        self.bot.add_view(panel)

        channel_view = ui.LayoutView(timeout=None)
        channel_view.add_item(
            ui.Container(ui.ActionRow(_start_button(), _close_button(), _ping_button()), accent_colour=COLOR_PANEL))
        self.bot.add_view(channel_view)

        send = ui.LayoutView(timeout=None)
        send.add_item(ui.Container(ui.ActionRow(_send_button()), accent_colour=COLOR_PANEL))
        self.bot.add_view(send)

        review = ui.LayoutView(timeout=None)
        review.add_item(ui.Container(_review_row(), accent_colour=COLOR_PANEL))
        self.bot.add_view(review)

    async def cog_load(self):
        _OPEN_IDS.update(int(cid) for cid in storage.get_store(STORE_OPEN))

    # --- Slash command για το panel ---
    @app_commands.command(name="panel-staff-application")
    @app_commands.checks.has_role(config.OWNERSHIP_ROLE_ID)
    async def panel_staff_application(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(view=build_panel_view())
        await interaction.followup.send("Στάλθηκε.", ephemeral=True)

    # --- Απαντήσεις του χρήστη μέσα στο channel ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or message.channel.id not in _OPEN_IDS:
            return
        data = _get_open(message.channel.id)
        if not data or not data.get("started") or message.author.id != data["user_id"]:
            return

        lock = self._locks.setdefault(message.channel.id, asyncio.Lock())
        async with lock:
            data = _get_open(message.channel.id)
            if not data or data["index"] >= len(QUESTIONS):
                return

            text = message.content.strip()
            if not text:
                await message.reply("Απάντα με κείμενο.", delete_after=8, mention_author=False)
                return

            key, _, max_len = QUESTIONS[data["index"]]
            if len(text) > max_len:
                await message.reply(f"Η απάντηση είναι πολύ μεγάλη ({len(text)}/{max_len}). Γράψε την πιο σύντομα.",
                                    delete_after=10, mention_author=False)
                return

            answers = dict(data["answers"])
            answers[key] = text
            next_index = data["index"] + 1

            if next_index >= len(QUESTIONS):
                data["answers"] = answers
                data["index"] = next_index
                data["completed"] = True
                data["last_activity"] = _now()
                _save_open(message.channel.id, data)
                await message.channel.send(view=build_send_view())
                return

            data["answers"] = answers
            data["index"] = next_index
            data["last_activity"] = _now()
            _save_open(message.channel.id, data)
            await message.channel.send(_question_text(next_index))

    # --- Αν σβηστεί το channel με το χέρι ---
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if channel.id in _OPEN_IDS:
            _delete_open(channel.id)
        self._locks.pop(channel.id, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffApplications(bot))
