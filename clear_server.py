#!/usr/bin/env python3
"""
Simple local server to clear seen listings from email links.
Run this in the background: python clear_server.py

Links in emails will point to: http://localhost:5050/hide?player=...&id=...
"""

import json
from pathlib import Path
from flask import Flask, request
from urllib.parse import unquote

app = Flask(__name__)
SEEN_LISTINGS_FILE = Path(__file__).parent / "seen_listings.json"

def load_seen():
    """Load seen listings as dict: player_name -> list of item_ids."""
    if SEEN_LISTINGS_FILE.exists():
        with open(SEEN_LISTINGS_FILE, "r") as f:
            data = json.load(f)
            # Handle old format (list) - migrate to new format
            if isinstance(data, list):
                return {"_legacy": data}
            return data
    return {}

def save_seen(seen):
    with open(SEEN_LISTINGS_FILE, "w") as f:
        json.dump(seen, f)

@app.route("/")
def index():
    seen = load_seen()
    total = sum(len(ids) for ids in seen.values())
    player_list = "<br>".join(f"  {player}: {len(ids)} items" for player, ids in seen.items())
    return f"""
    <html>
    <head><title>eBay Monitor</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>eBay Card Monitor</h1>
        <p>Currently tracking <strong>{total}</strong> hidden listings:</p>
        <p style="font-family: monospace; margin-left: 20px;">{player_list}</p>
        <p><a href="/clear-all">Clear all hidden listings</a></p>
    </body>
    </html>
    """

@app.route("/hide")
def hide_item():
    player = unquote(request.args.get("player", ""))
    item_id = request.args.get("id", "")

    if not player or not item_id:
        return "Missing player or id parameter", 400

    seen = load_seen()
    if player not in seen:
        seen[player] = []

    if item_id not in seen[player]:
        seen[player].append(item_id)
        save_seen(seen)
        status = "Hidden"
    else:
        status = "Already hidden"

    return f"""
    <html>
    <head><title>{status}</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>✅ {status}</h1>
        <p>Item <strong>{item_id}</strong> will no longer appear in <strong>{player}</strong> emails.</p>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    """

@app.route("/clear")
def clear_query():
    player = unquote(request.args.get("player", "") or request.args.get("query", ""))
    if not player:
        return "No player specified", 400

    seen = load_seen()
    count = len(seen.get(player, []))
    if player in seen:
        del seen[player]
        save_seen(seen)

    return f"""
    <html>
    <head><title>Cleared</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>✅ History Cleared</h1>
        <p>Cleared {count} hidden listings for "<strong>{player}</strong>". All results will appear in the next scan.</p>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    """

@app.route("/clear-all")
def clear_all():
    save_seen({})
    return f"""
    <html>
    <head><title>Cleared</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>✅ All History Cleared</h1>
        <p>All hidden listings have been cleared. All results will appear in the next scan.</p>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    """

if __name__ == "__main__":
    print("Starting clear server on http://localhost:5050")
    print("Keep this running to use clear links from emails")
    app.run(host="127.0.0.1", port=5050, debug=False)
