#!/usr/bin/env python3
"""
eBay Card Monitor - Scans for underpriced sports cards
Uses Playwright (bundles its own browser - no version conflicts)

Setup:
    pip install playwright
    playwright install chromium

Run manually or set up as a cron job for hourly checks
"""

import json
import time
import random
import re
import os
from datetime import datetime
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ============== CONFIGURATION ==============

WATCHLIST_FILE = Path("watchlist.json")

def load_watchlist():
    if WATCHLIST_FILE.exists():
        with open(WATCHLIST_FILE, "r") as f:
            return json.load(f)
    return {}

EMAIL_CONFIG = {
    "enabled": True,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "sam.white1@gmail.com",
    "sender_password": os.environ.get("EMAIL_PASSWORD", "wezq rmns vsno xgrw"),
    "recipient_email": "sam.white1@gmail.com",
}

SEEN_LISTINGS_FILE = Path("seen_listings.json")

# ============================================


class EbayCardMonitor:
    def __init__(self):
        self.seen_listings = self._load_seen_listings()

    def extract_numbered_value(self, title: str) -> int | None:
        """Extract the numbered value from a card title like '/75' or '/299'.

        Looks for patterns like /75, /299, #/50, etc. and returns the number.
        Returns the smallest number found (most valuable).
        """
        # Match patterns like /75, /299, #/50, numbered /25
        matches = re.findall(r'/(\d+)', title)
        if matches:
            # Return the smallest number (most valuable/rare)
            return min(int(m) for m in matches)
        return None

    def get_tier_price(self, tiers: list[dict], number: int) -> float | None:
        """Get the max price for a numbered card based on tiers.

        Tiers format: [{"min": 1, "max": 24, "price": 150.00}, ...]
        Returns the price if number falls within a tier, None otherwise.
        """
        for tier in tiers:
            if tier["min"] <= number <= tier["max"]:
                return tier["price"]
        return None

    def _load_seen_listings(self) -> set:
        if SEEN_LISTINGS_FILE.exists():
            with open(SEEN_LISTINGS_FILE, "r") as f:
                return set(json.load(f))
        return set()

    def _save_seen_listings(self):
        with open(SEEN_LISTINGS_FILE, "w") as f:
            json.dump(list(self.seen_listings), f)

    def build_search_url(self, query: str, auction: bool = False) -> str:
        # Remove exclusion terms and OR groups from eBay search (we filter locally)
        # OR groups look like: ('/275','/399','/299')
        clean_query = re.sub(r"\(['\"][^)]+['\"]\)", "", query)  # Remove OR groups
        search_terms = [t for t in clean_query.split() if t and not t.startswith("-")]
        encoded_query = "+".join(search_terms)
        if auction:
            # _sop=1 = ending soonest, LH_Auction=1 = auctions only
            return f"https://www.ebay.com/sch/i.html?_nkw={encoded_query}&_sop=1&LH_Auction=1"
        else:
            # _sop=10 = newly listed, LH_BIN=1 = Buy It Now only
            return f"https://www.ebay.com/sch/i.html?_nkw={encoded_query}&_sop=10&LH_BIN=1"

    def parse_price(self, price_text: str) -> float | None:
        if not price_text:
            return None
        price_text = price_text.replace(",", "").strip()
        if " to " in price_text.lower():
            price_text = price_text.lower().split(" to ")[0]
        match = re.search(r'\$?([\d.]+)', price_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
        return None

    def parse_time_remaining(self, time_text: str) -> int | None:
        """Parse time remaining text and return hours left, or None if can't parse."""
        if not time_text:
            return None
        time_text = time_text.lower()

        hours = 0
        # Match patterns like "1d 2h", "5h 30m", "2d", "12h"
        day_match = re.search(r'(\d+)\s*d', time_text)
        hour_match = re.search(r'(\d+)\s*h', time_text)
        min_match = re.search(r'(\d+)\s*m', time_text)

        if day_match:
            hours += int(day_match.group(1)) * 24
        if hour_match:
            hours += int(hour_match.group(1))
        if min_match:
            hours += int(min_match.group(1)) / 60

        return hours if hours > 0 else None

    def parse_bid_count(self, bid_text: str) -> int:
        """Parse bid count from text like '0 bids' or '3 bids'."""
        if not bid_text:
            return 0
        match = re.search(r'(\d+)\s*bid', bid_text.lower())
        return int(match.group(1)) if match else 0

    def scrape_listings(self, page, query: str, auction: bool = False) -> list[dict]:
        """Scrape eBay search results."""
        url = self.build_search_url(query, auction=auction)
        listings = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for results to load
            page.wait_for_selector(".srp-results", timeout=15000)
            time.sleep(2)  # Let JS finish rendering

            items = page.query_selector_all("li.s-card")
            print(f"   Found {len(items)} raw {'auctions' if auction else 'listings'}")

            for item in items:
                try:
                    # Skip placeholder "Shop on eBay" cards (they don't have a real item id attribute)
                    item_id_attr = item.get_attribute("id")
                    if not item_id_attr or not item_id_attr.startswith("item"):
                        continue

                    # Get title
                    title_elem = item.query_selector(".s-card__title")
                    if not title_elem:
                        continue
                    title = title_elem.inner_text().strip()

                    if not title or "Shop on eBay" in title:
                        continue

                    # Get price
                    price_elem = item.query_selector(".s-card__price")
                    if not price_elem:
                        continue
                    price = self.parse_price(price_elem.inner_text())
                    if price is None:
                        continue

                    # Get link
                    link_elem = item.query_selector("a.s-card__link")
                    link = link_elem.get_attribute("href") if link_elem else None

                    # Extract item ID from id attribute or link
                    item_id = item_id_attr.replace("item", "") if item_id_attr else None
                    if not item_id and link and "/itm/" in link:
                        match = re.search(r'/itm/(\d+)', link)
                        if match:
                            item_id = match.group(1)

                    # Get shipping, location, and auction info from attribute rows
                    shipping_cost = 0.0
                    bids = 0
                    time_left_hours = None
                    location = ""
                    has_bid_info = False
                    attr_rows = item.query_selector_all(".s-card__attribute-row")
                    for row in attr_rows:
                        row_text = row.inner_text().strip().lower()
                        if "delivery" in row_text or "shipping" in row_text:
                            if "free" in row_text:
                                shipping_cost = 0.0
                            else:
                                shipping_price = self.parse_price(row_text)
                                if shipping_price:
                                    shipping_cost = shipping_price
                        if "bid" in row_text:
                            has_bid_info = True
                            bids = self.parse_bid_count(row_text)
                            # Time is often in the same row as bids: "0 bids · Time left 23h 40m left"
                            if "left" in row_text:
                                time_left_hours = self.parse_time_remaining(row_text)
                        if "located in" in row_text:
                            location = row_text

                    # Skip listings not from United States
                    if "united states" not in location:
                        continue
                    # Skip China specifically
                    if "china" in location:
                        continue

                    # For BIN searches, skip listings that show bid info (they're auctions with BIN option)
                    if not auction and has_bid_info:
                        continue

                    listing_data = {
                        "item_id": item_id,
                        "title": title,
                        "price": price,
                        "shipping": shipping_cost,
                        "total_price": price + shipping_cost,
                        "link": link,
                        "is_auction": auction,
                    }

                    if auction:
                        listing_data["bids"] = bids
                        listing_data["time_left_hours"] = time_left_hours

                    listings.append(listing_data)

                except Exception:
                    continue

        except Exception as e:
            print(f"  ⚠️  Error fetching results for '{query}': {e}")

        return listings

    def title_matches_all_terms(self, title: str, query: str) -> bool:
        """Check if the title contains all search terms from the query.

        Supports:
        - Exclusions with minus prefix: "dylan harper -ice"
        - OR groups in parentheses: "Victor Wembanyama ('/275','/99','/50')"
          matches if ANY of those values are in the title
        """
        title_lower = title.lower()

        # Extract and check OR groups like ('/275','/399','/299')
        or_groups = re.findall(r"\((['\"][^)]+['\"])\)", query)
        for group in or_groups:
            # Parse the values from the group: '/275','/399' -> ['/275', '/399']
            values = re.findall(r"['\"]([^'\"]+)['\"]", group)
            # At least one value must be in the title
            if values and not any(v.lower() in title_lower for v in values):
                return False

        # Remove OR groups from query for regular term matching
        clean_query = re.sub(r"\(['\"][^)]+['\"]\)", "", query)
        terms = clean_query.lower().split()

        for term in terms:
            if not term:
                continue
            if term.startswith("-"):
                # Exclusion term - must NOT be in title
                exclude = term[1:]
                if exclude in title_lower:
                    return False
            else:
                # Required term - must be in title
                if term not in title_lower:
                    return False

        return True

    def find_deals(self, page, query: str, max_price: float) -> list[dict]:
        listings = self.scrape_listings(page, query, auction=False)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        for listing in listings:
            if listing["price"] <= max_price:
                if not self.title_matches_all_terms(listing["title"], query):
                    continue

                # Dedupe by item_id or link
                dedupe_key = listing["item_id"] or listing.get("link", "")
                if dedupe_key in seen_in_search:
                    continue
                seen_in_search.add(dedupe_key)

                if listing["item_id"] and listing["item_id"] not in self.seen_listings:
                    deals.append(listing)
                    self.seen_listings.add(listing["item_id"])
                elif not listing["item_id"]:
                    deals.append(listing)

        return deals

    def find_tiered_deals(self, page, query: str, tiers: list[dict]) -> list[dict]:
        """Find BIN deals for numbered cards using price tiers."""
        listings = self.scrape_listings(page, query, auction=False)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        for listing in listings:
            if not self.title_matches_all_terms(listing["title"], query):
                continue

            # Extract the numbered value from title
            numbered = self.extract_numbered_value(listing["title"])
            if numbered is None:
                continue  # Skip if no number found

            # Get the max price for this tier
            tier_price = self.get_tier_price(tiers, numbered)
            if tier_price is None:
                continue  # Number doesn't fall in any tier

            # Dedupe by item_id or link
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            if listing["price"] <= tier_price:
                listing["numbered"] = numbered
                listing["tier_price"] = tier_price
                if listing["item_id"] and listing["item_id"] not in self.seen_listings:
                    deals.append(listing)
                    self.seen_listings.add(listing["item_id"])
                elif not listing["item_id"]:
                    deals.append(listing)

        return deals

    def find_auction_deals(self, page, query: str, max_price: float) -> list[dict]:
        """Find auctions ending within 8h with price < 90% of max (10% discount)."""
        listings = self.scrape_listings(page, query, auction=True)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        target_price = max_price * 0.9  # 90% of BIN max price (10% discount)

        for listing in listings:
            # Check criteria: price < 90% of max, ending within 8h
            if listing["price"] >= target_price:
                continue
            if listing.get("time_left_hours") is None or listing["time_left_hours"] > 8:
                continue
            if not self.title_matches_all_terms(listing["title"], query):
                continue

            # Dedupe by item_id or link
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            # Mark as DEAL if 0-2 bids and price < 50% of max
            listing["is_deal"] = listing.get("bids", 0) <= 2 and listing["price"] < (max_price * 0.5)

            # Always show auctions (don't mark as seen) so user can track countdown
            deals.append(listing)

        return deals

    def find_tiered_auction_deals(self, page, query: str, tiers: list[dict]) -> list[dict]:
        """Find auction deals for numbered cards using price tiers."""
        listings = self.scrape_listings(page, query, auction=True)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        for listing in listings:
            if listing.get("time_left_hours") is None or listing["time_left_hours"] > 8:
                continue
            if not self.title_matches_all_terms(listing["title"], query):
                continue

            # Extract the numbered value from title
            numbered = self.extract_numbered_value(listing["title"])
            if numbered is None:
                continue

            # Get the max price for this tier
            tier_price = self.get_tier_price(tiers, numbered)
            if tier_price is None:
                continue

            # Dedupe by item_id or link
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            target_price = tier_price * 0.9  # 90% of tier price

            if listing["price"] < target_price:
                listing["numbered"] = numbered
                listing["tier_price"] = tier_price
                # Mark as DEAL if 0-2 bids and price < 50% of tier
                listing["is_deal"] = listing.get("bids", 0) <= 2 and listing["price"] < (tier_price * 0.5)
                deals.append(listing)

        return deals

    def send_email_alert(self, deals: list[dict], auctions: list[dict], query: str, price_config):
        if not EMAIL_CONFIG["enabled"]:
            return

        total_items = len(deals) + len(auctions)
        if total_items == 0:
            return

        # URL encode the query for the clear link
        from urllib.parse import quote
        clear_link = f"http://localhost:5050/clear?query={quote(query)}"

        subject = f"eBay Deal Alert: {total_items} item(s) for {query}"

        body = f"Deals found for: {query}\n"

        # Handle tiered vs simple pricing
        is_tiered = isinstance(price_config, dict) and "tiers" in price_config
        if is_tiered:
            tiers = price_config["tiers"]
            body += "Tiered pricing:\n"
            for tier in tiers:
                body += f"   /{tier['min']}-/{tier['max']}: ${tier['price']:.2f}\n"
            body += "\n"
        else:
            max_price = price_config
            body += f"Your max BIN price: ${max_price:.2f}\n"
            body += f"Auction target: under ${max_price * 0.9:.2f} (10% off), ending <8h\n\n"
        body += f"🔄 Clear this search: {clear_link}\n"
        body += f"🗑️ Clear all history: http://localhost:5050/clear-all\n\n"
        body += "=" * 50 + "\n\n"

        if deals:
            body += f"📦 BUY IT NOW ({len(deals)})\n\n"
            for deal in deals:
                body += f"{deal['title']}\n"
                body += f"   Price: ${deal['price']:.2f}"
                if deal['shipping'] > 0:
                    body += f" + ${deal['shipping']:.2f} shipping"
                body += f"\n   Total: ${deal['total_price']:.2f}"
                if deal.get('numbered'):
                    body += f" (/{deal['numbered']} - max ${deal['tier_price']:.2f})"
                body += f"\n   Link: {deal['link']}\n\n"

        if auctions:
            body += f"🔨 AUCTIONS ({len(auctions)})\n\n"
            for auction in auctions:
                deal_tag = "🔥 DEAL! " if auction.get("is_deal") else ""
                body += f"{deal_tag}{auction['title']}\n"
                body += f"   Current: ${auction['price']:.2f}"
                if auction['shipping'] > 0:
                    body += f" + ${auction['shipping']:.2f} shipping"
                body += f" ({auction.get('bids', 0)} bids)"
                if auction.get('time_left_hours'):
                    hours = auction['time_left_hours']
                    if hours < 1:
                        body += f" - {int(hours * 60)}m left"
                    else:
                        body += f" - {hours:.1f}h left"
                if auction.get('numbered'):
                    body += f" [/{auction['numbered']} - max ${auction['tier_price']:.2f}]"
                body += f"\n   Link: {auction['link']}\n\n"

        # Queue email during quiet hours (12am-6am)
        if self._is_quiet_hours():
            self._queue_email(subject, body)
            return

        msg = MIMEMultipart()
        msg["From"] = EMAIL_CONFIG["sender_email"]
        msg["To"] = EMAIL_CONFIG["recipient_email"]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
                server.starttls()
                server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
                server.send_message(msg)
            print("  📧 Email alert sent!")
        except Exception as e:
            print(f"  ⚠️  Failed to send email: {e}")

    def run_scan(self):
        print(f"\n{'='*60}")
        print(f"eBay Card Monitor - Scan Started")
        print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        all_deals = []
        all_auctions = []

        with sync_playwright() as p:
            print("Starting browser...")
            browser = p.chromium.launch(headless=True)
            print("Browser ready.\n")

            watchlist = load_watchlist()
            for query, price_config in watchlist.items():
                # Check if this is a tiered search or simple search
                is_tiered = isinstance(price_config, dict) and "tiers" in price_config

                print(f"Searching: {query}")

                if is_tiered:
                    tiers = price_config["tiers"]
                    min_price = min(t["price"] for t in tiers)
                    max_price_display = max(t["price"] for t in tiers)
                    print(f"   Tiered pricing: ${min_price:.2f} - ${max_price_display:.2f}")
                    for tier in tiers:
                        print(f"      /{tier['min']}-/{tier['max']}: ${tier['price']:.2f}")
                else:
                    max_price = price_config
                    print(f"   Max BIN price: ${max_price:.2f}")
                    print(f"   Auction target: <${max_price * 0.9:.2f} (10% off)")

                # Create fresh context and page for each search
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = context.new_page()

                # Search BIN deals
                if is_tiered:
                    deals = self.find_tiered_deals(page, query, tiers)
                    auctions = self.find_tiered_auction_deals(page, query, tiers)
                else:
                    deals = self.find_deals(page, query, max_price)
                    auctions = self.find_auction_deals(page, query, max_price)

                if deals:
                    print(f"   ✅ Found {len(deals)} BIN deal(s)!")
                    for deal in deals:
                        shipping_str = f" + ${deal['shipping']:.2f} ship" if deal['shipping'] > 0 else " (free ship)"
                        numbered_str = f" [/{deal['numbered']}]" if deal.get('numbered') else ""
                        print(f"      ${deal['price']:.2f}{shipping_str}{numbered_str} - {deal['title'][:50]}...")
                    all_deals.extend(deals)

                if auctions:
                    print(f"   🔨 Found {len(auctions)} auction(s)!")
                    for auction in auctions:
                        time_str = f"{auction['time_left_hours']:.1f}h" if auction.get('time_left_hours') else "?"
                        shipping_str = f" + ${auction['shipping']:.2f} ship" if auction['shipping'] > 0 else ""
                        deal_str = "🔥DEAL " if auction.get('is_deal') else ""
                        numbered_str = f" [/{auction['numbered']}]" if auction.get('numbered') else ""
                        print(f"      {deal_str}${auction['price']:.2f}{shipping_str} ({auction.get('bids', 0)} bids, {time_str} left){numbered_str} - {auction['title'][:40]}...")
                    all_auctions.extend(auctions)

                if deals or auctions:
                    self.send_email_alert(deals, auctions, query, price_config)
                else:
                    print(f"   ❌ No deals found\n")

                if not deals and not auctions:
                    print()

                context.close()
                time.sleep(random.uniform(3, 5))

            browser.close()

        self._save_seen_listings()

        # Send any queued emails from quiet hours
        self._send_queued_emails()

        print(f"\n{'='*60}")
        print(f"Scan Complete - {len(all_deals)} BIN deal(s), {len(all_auctions)} auction(s)")
        print(f"{'='*60}\n")

        return all_deals + all_auctions

    def _is_quiet_hours(self) -> bool:
        """Check if current time is between 12am and 6am."""
        current_hour = datetime.now().hour
        return 0 <= current_hour < 6

    def _queue_email(self, subject: str, body: str):
        """Queue an email to be sent after quiet hours."""
        queue_file = Path("email_queue.json")
        queue = []
        if queue_file.exists():
            with open(queue_file, "r") as f:
                queue = json.load(f)

        queue.append({
            "subject": subject,
            "body": body,
            "queued_at": datetime.now().isoformat()
        })

        with open(queue_file, "w") as f:
            json.dump(queue, f, indent=2)
        print("  📬 Email queued (quiet hours)")

    def _send_queued_emails(self):
        """Send any emails that were queued during quiet hours."""
        if self._is_quiet_hours():
            return

        queue_file = Path("email_queue.json")
        if not queue_file.exists():
            return

        with open(queue_file, "r") as f:
            queue = json.load(f)

        if not queue:
            return

        print(f"📬 Sending {len(queue)} queued email(s)...")

        for email in queue:
            msg = MIMEMultipart()
            msg["From"] = EMAIL_CONFIG["sender_email"]
            msg["To"] = EMAIL_CONFIG["recipient_email"]
            msg["Subject"] = email["subject"]
            msg.attach(MIMEText(email["body"], "plain"))

            try:
                with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
                    server.starttls()
                    server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
                    server.send_message(msg)
                print(f"  📧 Sent: {email['subject']}")
            except Exception as e:
                print(f"  ⚠️  Failed to send queued email: {e}")

        # Clear the queue
        queue_file.unlink()
        print("📬 Queue cleared")


def main():
    if not PLAYWRIGHT_AVAILABLE:
        print("\n❌ Playwright not installed.")
        print("   Run:")
        print("     pip install playwright")
        print("     playwright install chromium\n")
        return
    
    monitor = EbayCardMonitor()
    monitor.run_scan()


if __name__ == "__main__":
    main()
