"""Discord webhook notifications. No-op if DISCORD_WEBHOOK_URL is unset."""
import logging
import os

import requests

log = logging.getLogger("scalper.notify")

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

COLORS = {
    "entry_long": 0x2ECC71,   # green
    "entry_short": 0xE67E22,  # orange
    "exit_win": 0x3498DB,     # blue
    "exit_loss": 0xE74C3C,    # red
    "halt": 0x9B59B6,         # purple
    "info": 0x95A5A6,         # gray
}


def send(title: str, description: str, kind: str = "info", fields: dict | None = None):
    """Fire-and-forget Discord embed. Never raises — alerts must not kill the bot."""
    if not WEBHOOK_URL:
        return
    embed = {
        "title": title,
        "description": description,
        "color": COLORS.get(kind, COLORS["info"]),
    }
    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True} for k, v in fields.items()
        ]
    try:
        requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
    except Exception as e:
        log.warning("discord webhook failed: %s", e)
