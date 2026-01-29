# eBay Card Monitor

Monitor eBay for underpriced sports cards. No API key required.

## Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install browser for Playwright
playwright install chromium
```

## Run

```bash
cd ~/Documents/ebay-search
source venv/bin/activate
python ebay_card_monitor.py
```

Or as a one-liner:

```bash
cd ~/Documents/ebay-search && source venv/bin/activate && python ebay_card_monitor.py
```

## Reset Seen Listings

To see all current deals again (clears history):

```bash
echo "[]" > seen_listings.json
```

## Configuration

Edit the `WATCHLIST` dictionary in `ebay_card_monitor.py`:

```python
WATCHLIST = {
    "victor wembanyama prizm silver": 150.00,
    "anthony edwards select concourse": 25.00,
    "ja morant mosaic base psa 10": 40.00,
    # Add your cards here...
}
```

**Format:** `"search query": max_price_you_want_to_pay`

### Search Tips

- Be specific: `"lamelo ball select courtside blue psa 10"` 
- Use card-specific terms: `prizm`, `select`, `mosaic`, `optic`
- Include year if needed: `"2023-24 wembanyama"` 
- Include grade for graded cards: `"psa 10"`, `"bgs 9.5"`

## Automated Hourly Scans (Mac/Linux)

### Option 1: Cron Job

```bash
# Open crontab editor
crontab -e

# Add this line (runs every hour at :00)
0 * * * * /usr/bin/python3 /path/to/ebay_card_monitor.py >> /path/to/monitor.log 2>&1
```

### Option 2: macOS LaunchAgent (Recommended for Mac)

1. Create `~/Library/LaunchAgents/com.sambricksquad.ebaymonitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sambricksquad.ebaymonitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/ebay_card_monitor.py</string>
    </array>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/ebay_monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/ebay_monitor_error.log</string>
</dict>
</plist>
```

2. Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.sambricksquad.ebaymonitor.plist
```

## Scheduled Runs

The script is configured to run automatically at **7:00 AM** and **7:00 PM** daily via cron.

To view or edit the schedule:

```bash
crontab -e
```

To disable scheduled runs:

```bash
crontab -l | grep -v ebay_card_monitor | crontab -
```

## Email Alerts

Email alerts are enabled and will be sent to the configured address when deals are found.

To change email settings, edit `EMAIL_CONFIG` in `ebay_card_monitor.py`.

**For Gmail:** Requires an [App Password](https://myaccount.google.com/apppasswords) (2FA must be enabled).

## How It Works

1. Scrapes eBay search results using Playwright (headless browser)
2. Filters to Buy It Now listings only
3. Ensures listing titles contain **all** search terms (no partial matches)
4. Compares BIN price against your max price (shipping shown separately)
5. Alerts you to any listings under your target price
6. Tracks seen listings to avoid duplicate alerts

## Notes

- Results are sorted by "newly listed" to catch fresh deals first
- The script adds random delays between searches to be respectful to eBay
- Seen listings are stored in `seen_listings.json` to prevent duplicate alerts
- Run `echo "[]" > seen_listings.json` to reset and see all current listings
- Price comparison uses BIN price only; shipping is displayed separately

## Limitations

- Web scraping can break if eBay changes their HTML structure
- Not real-time - best used for hourly/periodic checks
- May occasionally miss listings due to eBay's dynamic loading
- Be careful not to run too frequently (could get IP blocked)
