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

WATCHLIST = {
    "Amen Thompson 150 silver PSA 9": 31.00,
    "tre johnson iii d-6 refractor": 5.00,
    "dylan harper d-2 refractor": 7.00,
    # Add more cards here...
}

EMAIL_CONFIG = {
    "enabled": True,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "sam.white1@gmail.com",
    "sender_password": "wezq rmns vsno xgrw",
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

    def build_search_url(self, query: str) -> str:
        encoded_query = query.replace(" ", "+")
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

    def scrape_listings(self, page, query: str) -> list[dict]:
        """Scrape eBay search results."""
        url = self.build_search_url(query)
        listings = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Wait for results to load
            page.wait_for_selector(".srp-results", timeout=15000)
            time.sleep(2)  # Let JS finish rendering

            items = page.query_selector_all("li.s-card")
            print(f"   Found {len(items)} raw listings")

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

                    # Get shipping from attribute rows
                    shipping_cost = 0.0
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
                            break

                    listings.append({
                        "item_id": item_id,
                        "title": title,
                        "price": price,
                        "shipping": shipping_cost,
                        "total_price": price + shipping_cost,
                        "link": link,
                    })

                except Exception:
                    continue

        except Exception as e:
            print(f"  ⚠️  Error fetching results for '{query}': {e}")

        return listings

    def title_matches_all_terms(self, title: str, query: str) -> bool:
        """Check if the title contains all search terms from the query."""
        title_lower = title.lower()
        terms = query.lower().split()
        return all(term in title_lower for term in terms)

    def find_deals(self, page, query: str, max_price: float) -> list[dict]:
        listings = self.scrape_listings(page, query)
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

    def send_email_alert(self, deals: list[dict], query: str, max_price: float):
        if not EMAIL_CONFIG["enabled"]:
            return
        
        subject = f"eBay Deal Alert: {len(deals)} card(s) found under ${max_price:.2f}"
        
        body = f"Deals found for: {query}\n"
        body += f"Your max price: ${max_price:.2f}\n\n"
        body += "=" * 50 + "\n\n"
        
        for deal in deals:
            body += f"{deal['title']}\n"
            body += f"   Price: ${deal['price']:.2f}"
            if deal['shipping'] > 0:
                body += f" + ${deal['shipping']:.2f} shipping"
            body += f"\n   Total: ${deal['total_price']:.2f}\n"
            body += f"   Link: {deal['link']}\n\n"
        
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
        
        with sync_playwright() as p:
            print("Starting browser...")
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()
            print("Browser ready.\n")
            
            for query, max_price in WATCHLIST.items():
                print(f"Searching: {query}")
                print(f"   Max price: ${max_price:.2f}")
                
                deals = self.find_deals(page, query, max_price)
                
                if deals:
                    print(f"   ✅ Found {len(deals)} deal(s)!\n")
                    for deal in deals:
                        shipping_str = f" + ${deal['shipping']:.2f} ship" if deal['shipping'] > 0 else " (free ship)"
                        print(f"      ${deal['price']:.2f}{shipping_str} - {deal['title'][:55]}...")
                        print(f"      {deal['link']}\n")
                    all_deals.extend(deals)
                    self.send_email_alert(deals, query, max_price)
                else:
                    print(f"   ❌ No deals under ${max_price:.2f}\n")
                
                time.sleep(random.uniform(3, 5))
            
            browser.close()
        
        self._save_seen_listings()
        
        print(f"\n{'='*60}")
        print(f"Scan Complete - Found {len(all_deals)} total deal(s)")
        print(f"{'='*60}\n")
        
        return all_deals


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
