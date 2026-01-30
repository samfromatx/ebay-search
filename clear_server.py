#!/usr/bin/env python3
"""
Simple local server to clear seen listings from email links.
Run this in the background: python clear_server.py

Links in emails will point to: http://localhost:5050/clear?query=...
"""

import json
from pathlib import Path
from flask import Flask, request
from urllib.parse import unquote

app = Flask(__name__)
SEEN_LISTINGS_FILE = Path(__file__).parent / "seen_listings.json"

def load_seen():
    if SEEN_LISTINGS_FILE.exists():
        with open(SEEN_LISTINGS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_LISTINGS_FILE, "w") as f:
        json.dump(list(seen), f)

@app.route("/")
def index():
    seen = load_seen()
    return f"""
    <html>
    <head><title>eBay Monitor</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>eBay Card Monitor</h1>
        <p>Currently tracking <strong>{len(seen)}</strong> seen listings.</p>
        <p><a href="/clear-all">Clear all seen listings</a></p>
    </body>
    </html>
    """

@app.route("/clear")
def clear_query():
    query = unquote(request.args.get("query", ""))
    if not query:
        return "No query specified", 400

    # Clear all seen listings (we don't track which listing belongs to which query)
    # So this clears everything, allowing all results to appear again
    save_seen(set())

    return f"""
    <html>
    <head><title>Cleared</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>✅ History Cleared</h1>
        <p>Cleared seen listings. Results for "<strong>{query}</strong>" will appear in the next scan.</p>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    """

@app.route("/clear-all")
def clear_all():
    save_seen(set())
    return f"""
    <html>
    <head><title>Cleared</title></head>
    <body style="font-family: system-ui; max-width: 600px; margin: 50px auto; padding: 20px;">
        <h1>✅ All History Cleared</h1>
        <p>All seen listings have been cleared. All results will appear in the next scan.</p>
        <p><a href="/">Back to home</a></p>
    </body>
    </html>
    """

if __name__ == "__main__":
    print("Starting clear server on http://localhost:5050")
    print("Keep this running to use clear links from emails")
    app.run(host="127.0.0.1", port=5050, debug=False)
