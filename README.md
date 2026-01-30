# eBay Card Monitor

Automated eBay sports card deal finder. Scans for underpriced Buy It Now listings and auctions, sends email alerts.

## Features

- **Buy It Now deals** - Finds listings under your max price
- **Auction monitoring** - Finds auctions ending within 24h with 0-2 bids at <50% of max price
- **Exclusion filters** - Use `-word` to exclude listings (e.g., `"dylan harper -ice"`)
- **Email alerts** - Sends deals to your email with clickable links
- **Clear history links** - Click links in emails to reset seen listings
- **Hourly scans** - Runs automatically via LaunchAgent
- **Wake detection** - Runs on Mac wake via sleepwatcher

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure watchlist

Edit `watchlist.json`:

```json
{
  "Amen Thompson 150 silver PSA 9 -ice": 38.00,
  "dylan harper d-2 refractor": 10.00
}
```

- **Key** = search query (use `-word` to exclude terms)
- **Value** = max BIN price (auctions target 50% of this)

### 3. Set email password

```bash
export EMAIL_PASSWORD="your-gmail-app-password"
```

Or edit `EMAIL_CONFIG` in `ebay_card_monitor.py`.

**For Gmail:** Requires an [App Password](https://myaccount.google.com/apppasswords) (2FA must be enabled).

### 4. Run manually

```bash
source venv/bin/activate
python ebay_card_monitor.py
```

## Automated Scheduling

### Hourly scans (LaunchAgent)

```bash
cp config/com.ebay.cardmonitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ebay.cardmonitor.plist
```

### Clear server (for email links)

```bash
cp config/com.ebay.clearserver.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ebay.clearserver.plist
```

Server runs at http://localhost:5050

### Wake detection (sleepwatcher)

```bash
brew install sleepwatcher
cp config/wakeup.sh ~/.wakeup
chmod +x ~/.wakeup
brew services start sleepwatcher
```

### LaunchAgent Commands

```bash
# Test run now
launchctl start com.ebay.cardmonitor

# Check status
launchctl list | grep ebay

# View logs
tail -f ~/Documents/ebay-search/monitor.log

# Stop/disable
launchctl unload ~/Library/LaunchAgents/com.ebay.cardmonitor.plist
```

## Files

| File | Description |
|------|-------------|
| `ebay_card_monitor.py` | Main scanner script |
| `clear_server.py` | Local server for clear history links |
| `watchlist.json` | Your search queries and max prices |
| `seen_listings.json` | Tracks seen listings (auto-generated) |
| `monitor.log` | Scan output log |
| `config/` | LaunchAgent and sleepwatcher configs |

## Email Format

Emails include:
- 📦 **BUY IT NOW** - Listings under your max price
- 🔨 **AUCTIONS** - Ending soon with low bids
- 🔄 Clear this search link
- 🗑️ Clear all history link

## How It Works

1. Scrapes eBay search results using Playwright (headless browser)
2. **BIN deals:** Filters to listings under your max price
3. **Auctions:** Finds auctions ending <24h with 0-2 bids and price <50% of max
4. Ensures listing titles contain **all** search terms (exclusions with `-word`)
5. Tracks seen listings to avoid duplicate alerts
6. Sends email with both BIN deals and auction opportunities

## Reset Seen Listings

```bash
# Clear all history
echo "[]" > seen_listings.json

# Or use the web interface
open http://localhost:5050/clear-all
```
