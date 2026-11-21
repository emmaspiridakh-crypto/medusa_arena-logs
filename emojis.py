EMOJIS = {
    "tickets": {
        "ticket": "<:ticket:1572728296993726585>",
    },
    "moderation": {
        "ban": "<:ban:1573464177719115956>",
        "unban": "<:unban:1573464177719115956>",
        "kick": "<a:kick:1572723491734556722>",
        "timeout": "<:timeout:1573362872363385003>",
        "untimeout": "<:untimeout:1573362872363385003>",
        "clear": "<:clear:1573362885181177926>",
    },
    "applications": {
        "apply": "<:apply:1548269878438531184>",
        "accept": "<:accept:1573458107097423973>",  # βάλε εδώ το νέο emoji, π.χ. "<:accept:ID>"
        "deny": "<:deny:1573458135375675483>",    # βάλε εδώ το νέο emoji, π.χ. "<:deny:ID>"
    },
    "notifier": {
        "bell": "<:notif_bell:1573455807058542682>",
        "clock": "<:notif_clock:1573362872363385003>",
    },
    "panel": {
        "scan": "<:scan:1573362885181177926>",
    },
}


def emoji(category: str, name: str) -> str:
    try:
        return EMOJIS[category][name]
    except KeyError:
        return ""
