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

    def _load_seen_listings(self) -> set:
        if SEEN_LISTINGS_FILE.exists():
            with open(SEEN_LISTINGS_FILE, "r") as f:
                return set(json.load(f))
        return set()

    def _save_seen_listings(self):
        with open(SEEN_LISTINGS_FILE, "w") as f:
            json.dump(list(self.seen_listings), f)

    def build_search_url(self, query: str, auction: bool = False) -> str:
        # Remove exclusion terms from the eBay search (we filter locally)
        search_terms = [t for t in query.split() if not t.startswith("-")]
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

                    # Get shipping and auction info from attribute rows
                    shipping_cost = 0.0
                    bids = 0
                    time_left_hours = None
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
                            bids = self.parse_bid_count(row_text)
                            # Time is often in the same row as bids: "0 bids · Time left 23h 40m left"
                            if "left" in row_text:
                                time_left_hours = self.parse_time_remaining(row_text)

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

        Supports exclusions with minus prefix: "dylan harper -ice"
        will match titles with "dylan" and "harper" but NOT "ice"
        """
        title_lower = title.lower()
        terms = query.lower().split()

        for term in terms:
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

        for listing in listings:
            if listing["price"] <= max_price:
                if not self.title_matches_all_terms(listing["title"], query):
                    continue
                if listing["item_id"] and listing["item_id"] not in self.seen_listings:
                    deals.append(listing)
                    self.seen_listings.add(listing["item_id"])
                elif not listing["item_id"]:
                    deals.append(listing)

        return deals

    def find_auction_deals(self, page, query: str, max_price: float) -> list[dict]:
        """Find auctions ending within 8h with 0-2 bids and price < 50% of max."""
        listings = self.scrape_listings(page, query, auction=True)
        deals = []

        target_price = max_price * 0.5  # 50% of BIN max price

        for listing in listings:
            # Check criteria: price < 50% of max, 0-2 bids, ending within 8h
            if listing["price"] >= target_price:
                continue
            if listing.get("bids", 0) > 2:
                continue
            if listing.get("time_left_hours") is None or listing["time_left_hours"] > 8:
                continue
            if not self.title_matches_all_terms(listing["title"], query):
                continue
            if listing["item_id"] and listing["item_id"] not in self.seen_listings:
                deals.append(listing)
                self.seen_listings.add(listing["item_id"])
            elif not listing["item_id"]:
                deals.append(listing)

        return deals

    def send_email_alert(self, deals: list[dict], auctions: list[dict], query: str, max_price: float):
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
        body += f"Your max BIN price: ${max_price:.2f}\n"
        body += f"Auction target: under ${max_price * 0.5:.2f} (50%), ending <8h\n\n"
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
                body += f"\n   Total: ${deal['total_price']:.2f}\n"
                body += f"   Link: {deal['link']}\n\n"

        if auctions:
            body += f"🔨 AUCTIONS ({len(auctions)})\n\n"
            for auction in auctions:
                body += f"{auction['title']}\n"
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
                body += f"\n   Link: {auction['link']}\n\n"

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
            for query, max_price in watchlist.items():
                print(f"Searching: {query}")
                print(f"   Max BIN price: ${max_price:.2f}")
                print(f"   Auction target: <${max_price * 0.5:.2f} (50%)")

                # Create fresh context and page for each search
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = context.new_page()

                # Search BIN deals
                deals = self.find_deals(page, query, max_price)

                # Search auctions
                auctions = self.find_auction_deals(page, query, max_price)

                if deals:
                    print(f"   ✅ Found {len(deals)} BIN deal(s)!")
                    for deal in deals:
                        shipping_str = f" + ${deal['shipping']:.2f} ship" if deal['shipping'] > 0 else " (free ship)"
                        print(f"      ${deal['price']:.2f}{shipping_str} - {deal['title'][:50]}...")
                    all_deals.extend(deals)

                if auctions:
                    print(f"   🔨 Found {len(auctions)} auction(s)!")
                    for auction in auctions:
                        time_str = f"{auction['time_left_hours']:.1f}h" if auction.get('time_left_hours') else "?"
                        shipping_str = f" + ${auction['shipping']:.2f} ship" if auction['shipping'] > 0 else ""
                        print(f"      ${auction['price']:.2f}{shipping_str} ({auction.get('bids', 0)} bids, {time_str} left) - {auction['title'][:40]}...")
                    all_auctions.extend(auctions)

                if deals or auctions:
                    self.send_email_alert(deals, auctions, query, max_price)
                else:
                    print(f"   ❌ No deals found\n")

                if not deals and not auctions:
                    print()

                context.close()
                time.sleep(random.uniform(3, 5))

            browser.close()

        self._save_seen_listings()

        # Send summary email (temporary for testing)
        self.send_scan_summary(all_deals, all_auctions, watchlist)

        print(f"\n{'='*60}")
        print(f"Scan Complete - {len(all_deals)} BIN deal(s), {len(all_auctions)} auction(s)")
        print(f"{'='*60}\n")

        return all_deals + all_auctions

    def send_scan_summary(self, deals: list, auctions: list, watchlist: dict):
        """Send a summary email every scan (temporary for testing)."""
        if not EMAIL_CONFIG["enabled"]:
            return

        subject = f"eBay Scan Complete: {len(deals)} deals, {len(auctions)} auctions"

        body = f"Scan completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        body += f"Searches: {len(watchlist)}\n"
        body += f"BIN deals found: {len(deals)}\n"
        body += f"Auctions found: {len(auctions)}\n\n"
        body += "Watchlist:\n"
        for query, price in watchlist.items():
            body += f"  - {query}: ${price:.2f}\n"

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
            print("  📧 Scan summary email sent!")
        except Exception as e:
            print(f"  ⚠️  Failed to send summary email: {e}")


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
