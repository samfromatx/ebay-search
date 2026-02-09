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
SOLD_PRICES_CACHE_FILE = Path("sold_prices_cache.json")
SOLD_CACHE_DAYS = 7  # Refresh cache weekly

# ============================================


class EbayCardMonitor:
    def __init__(self):
        self.seen_listings = self._load_seen_listings()  # dict: player_name -> set of item_ids
        self.seen_this_run = {}  # item_id -> list of queries that matched
        self.current_player = None  # Set during run_scan for each player

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

    def _load_seen_listings(self) -> dict:
        """Load seen listings as dict: player_name -> set of item_ids."""
        if SEEN_LISTINGS_FILE.exists():
            with open(SEEN_LISTINGS_FILE, "r") as f:
                data = json.load(f)
                # Handle old format (list) - migrate to new format
                if isinstance(data, list):
                    return {"_legacy": set(data)}
                # New format: dict of player -> list of ids
                return {player: set(ids) for player, ids in data.items()}
        return {}

    def _save_seen_listings(self):
        with open(SEEN_LISTINGS_FILE, "w") as f:
            # Convert sets to lists for JSON serialization
            data = {player: list(ids) for player, ids in self.seen_listings.items()}
            json.dump(data, f)

    def _get_player_seen(self) -> set:
        """Get seen item IDs for current player."""
        if self.current_player is None:
            return set()
        return self.seen_listings.get(self.current_player, set())

    def _mark_seen(self, item_id: str):
        """Mark an item as seen for current player."""
        if self.current_player is None or not item_id:
            return
        if self.current_player not in self.seen_listings:
            self.seen_listings[self.current_player] = set()
        self.seen_listings[self.current_player].add(item_id)

    def clear_player_history(self, player_name: str) -> bool:
        """Clear seen listings history for a specific player."""
        if player_name in self.seen_listings:
            count = len(self.seen_listings[player_name])
            del self.seen_listings[player_name]
            self._save_seen_listings()
            print(f"Cleared {count} items from {player_name}'s history.")
            return True
        else:
            print(f"No history found for {player_name}.")
            return False

    # ============== SOLD PRICES CACHE ==============

    def _load_sold_cache(self) -> dict:
        """Load sold prices cache: query -> {avg_price, num_sold, updated}."""
        if SOLD_PRICES_CACHE_FILE.exists():
            with open(SOLD_PRICES_CACHE_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save_sold_cache(self, cache: dict):
        with open(SOLD_PRICES_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)

    def _get_cache_key(self, title: str) -> str:
        """Generate precise cache key from listing title for sold price lookup.

        Keeps: player name, card number (#XX), parallel/insert names, set names
        Excludes graded terms if not in original title
        """
        original_title = title
        title = title.lower()

        # Check if this is a graded card
        is_graded = any(g in title for g in ['psa', 'bgs', 'sgc', 'cgc'])

        # Important terms to always keep (parallels, inserts, sets)
        important_patterns = [
            r'#\d+',  # Card numbers like #20, #136
            r'/\d+',  # Numbered cards like /99, /199
        ]

        # Extract card numbers before normalization
        card_nums = re.findall(r'#\d+', title)
        numbered = re.findall(r'/\d+', title)

        # Words that are noise
        noise = {'the', 'a', 'an', 'and', 'or', 'of', 'for', 'to', 'in', 'on',
                 'card', 'cards', 'new', 'brand', 'hot', 'rare', 'sp', 'ssp',
                 'lot', 'rc', 'nba', 'panini', 'topps', 'opens', 'tab', 'window'}

        # Keep these important parallel/insert/set names
        keep_words = {'prizm', 'select', 'mosaic', 'optic', 'hoops', 'donruss',
                      'silver', 'gold', 'holo', 'refractor', 'shimmer', 'mojo',
                      'kaleidoscopic', 'fast', 'break', 'courtside', 'scope',
                      'pink', 'blue', 'red', 'green', 'orange', 'purple', 'ice',
                      'cracked', 'wave', 'hyper', 'disco', 'speckle', 'camo',
                      'rookie', 'base', 'variation', 'parallel', 'insert',
                      'psa', 'bgs', 'sgc', 'cgc', '10', '9', '8'}

        # Extract words, keeping alphanumeric
        words = re.findall(r'[a-z0-9]+', title)

        # Build key: keep player name words + important terms
        key_words = []
        for word in words:
            if word in noise:
                continue
            if len(word) < 2:
                continue
            # Keep the word if it's important or looks like a name
            if word in keep_words or len(word) > 3:
                key_words.append(word)

        # Add card numbers back
        for num in card_nums:
            key_words.append(num.replace('#', ''))

        # Limit to reasonable length
        result = ' '.join(key_words[:10])

        # If not graded, append exclusions
        if not is_graded:
            result += ' -psa -bgs -sgc -cgc -graded'

        return result

    def _is_cache_valid(self, cache_entry: dict) -> bool:
        """Check if cache entry is still valid (within SOLD_CACHE_DAYS)."""
        if not cache_entry or 'updated' not in cache_entry:
            return False
        updated = datetime.fromisoformat(cache_entry['updated'])
        age_days = (datetime.now() - updated).days
        return age_days < SOLD_CACHE_DAYS

    COMMON_PRINT_RUNS = [10, 25, 49, 75, 99, 149, 175, 199, 249, 299, 399, 499]

    def _get_common_print_run(self, tier: dict) -> int:
        """Return the first common print run that falls within the tier's min/max range."""
        for pr in self.COMMON_PRINT_RUNS:
            if tier["min"] <= pr <= tier["max"]:
                return pr
        return tier["max"]

    def _get_tier_sold_key(self, numbered_query: str, tier: dict) -> str:
        """Generate a sold cache key for a specific tier of a numbered search."""
        # Remove 'numbered' from the query
        base = re.sub(r'\bnumbered\b', '', numbered_query, flags=re.IGNORECASE).strip()
        # Clean up extra spaces
        base = re.sub(r'\s+', ' ', base)
        print_run = self._get_common_print_run(tier)
        # Split into include and exclude terms
        include = [t for t in base.split() if not t.startswith('-')]
        exclude = [t for t in base.split() if t.startswith('-')]
        key = ' '.join(include) + f' /{print_run}'
        # Add grading exclusions if not already present
        has_grading_exclude = any(t.lstrip('-') in ('psa', 'bgs', 'sgc', 'cgc', 'graded') for t in exclude)
        if not has_grading_exclude:
            exclude.extend(['-psa', '-bgs', '-sgc', '-cgc', '-graded'])
        if exclude:
            key += ' ' + ' '.join(exclude)
        return key.lower()

    def build_sold_search_url(self, query: str) -> str:
        """Build eBay URL for sold/completed listings."""
        # Split into include and exclude terms
        terms = query.split()
        include_terms = [t for t in terms if not t.startswith("-")]
        exclude_terms = [t[1:] for t in terms if t.startswith("-")]  # Remove the -

        # Build the query with exclusions
        encoded_query = "+".join(include_terms)
        for exc in exclude_terms:
            encoded_query += f"+-{exc}"

        # LH_Sold=1 and LH_Complete=1 for sold listings, _sop=13 for most recent
        return f"https://www.ebay.com/sch/i.html?_nkw={encoded_query}&LH_Sold=1&LH_Complete=1&_sop=13&LH_PrefLoc=1"

    def scrape_sold_prices(self, page, query: str) -> dict | None:
        """Scrape sold listings and return average price info."""
        url = self.build_sold_search_url(query)
        prices = []

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".srp-results", timeout=10000)
            time.sleep(1.5)

            items = page.query_selector_all("li.s-card")

            for item in items[:30]:  # Check up to 30 items to get 20 valid sold
                try:
                    # Skip sponsored/ad items - real sold items have "Sold" in text
                    item_text = item.inner_text()
                    if "Sold" not in item_text or "Shop on eBay" in item_text:
                        continue

                    # Use the correct price selector for sold listings
                    price_el = item.query_selector(".s-card__price")
                    if not price_el:
                        continue

                    price_text = price_el.inner_text()
                    # Skip price ranges
                    if " to " in price_text.lower():
                        continue

                    price = self.parse_price(price_text)
                    if price and price > 0:
                        prices.append(price)
                        if len(prices) >= 20:
                            break
                except:
                    continue

            if prices:
                return {
                    "avg_price": round(sum(prices) / len(prices), 2),
                    "num_sold": len(prices),
                    "updated": datetime.now().isoformat()
                }
        except Exception as e:
            print(f"      [Sold] Error fetching: {e}")

        return None

    def get_sold_price(self, page, title: str, force_refresh: bool = False) -> dict | None:
        """Get sold price from cache or fetch fresh."""
        cache_key = self._get_cache_key(title)
        if not cache_key:
            return None

        cache = self._load_sold_cache()

        # Check cache first
        if not force_refresh and cache_key in cache:
            entry = cache[cache_key]
            if self._is_cache_valid(entry):
                return entry

        # Fetch fresh data
        result = self.scrape_sold_prices(page, cache_key)
        if result:
            cache[cache_key] = result
            self._save_sold_cache(cache)
            return result

        return None

    # ============================================

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

                # Dedupe by item_id or link within this search
                dedupe_key = listing["item_id"] or listing.get("link", "")
                if dedupe_key in seen_in_search:
                    continue
                seen_in_search.add(dedupe_key)

                # Check persistent seen_listings (across runs) for BIN deals
                # Items stay visible until manually hidden via link in email
                player_seen = self._get_player_seen()
                if listing["item_id"] and listing["item_id"] not in player_seen:
                    deals.append(listing)
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

            # Dedupe by item_id or link within this search
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            if listing["price"] <= tier_price:
                listing["numbered"] = numbered
                listing["tier_price"] = tier_price
                # Store matched tier for sold price lookups
                matched_tier = next(t for t in tiers if t["min"] <= numbered <= t["max"])
                listing["tier_max"] = matched_tier["max"]
                listing["numbered_query"] = query
                # Check persistent seen_listings (across runs) for BIN deals
                # Items stay visible until manually hidden via link in email
                player_seen = self._get_player_seen()
                if listing["item_id"] and listing["item_id"] not in player_seen:
                    deals.append(listing)
                elif not listing["item_id"]:
                    deals.append(listing)

        return deals

    def find_auction_deals(self, page, query: str, max_price: float) -> list[dict]:
        """Find auctions ending within 12h with price < max."""
        listings = self.scrape_listings(page, query, auction=True)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        target_price = max_price  # Any auction under max price

        # Debug counters
        filtered_price = 0
        filtered_time = 0
        filtered_title = 0

        for listing in listings:
            # Check criteria: price < max, ending within 12h
            if listing["price"] >= target_price:
                filtered_price += 1
                continue
            if listing.get("time_left_hours") is None or listing["time_left_hours"] > 12:
                filtered_time += 1
                continue
            if not self.title_matches_all_terms(listing["title"], query):
                filtered_title += 1
                continue

            # Dedupe by item_id or link within this search
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            # Mark as DEAL if 0-2 bids and price < 50% of max
            listing["is_deal"] = listing.get("bids", 0) <= 2 and listing["price"] < (max_price * 0.5)

            # Always show auctions (don't mark as seen persistently) so user can track countdown
            deals.append(listing)

        if len(listings) > 0 and len(deals) == 0:
            print(f"      [Auction debug] Filtered: {filtered_price} price>${target_price:.0f}, {filtered_time} time>12h, {filtered_title} title mismatch")

        return deals

    def find_tiered_auction_deals(self, page, query: str, tiers: list[dict]) -> list[dict]:
        """Find auction deals for numbered cards using price tiers (ending within 12h)."""
        listings = self.scrape_listings(page, query, auction=True)
        deals = []
        seen_in_search = set()  # Dedupe within this search

        for listing in listings:
            if listing.get("time_left_hours") is None or listing["time_left_hours"] > 12:
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

            # Dedupe by item_id or link within this search
            dedupe_key = listing["item_id"] or listing.get("link", "")
            if dedupe_key in seen_in_search:
                continue
            seen_in_search.add(dedupe_key)

            target_price = tier_price  # Any auction under tier price

            if listing["price"] < target_price:
                listing["numbered"] = numbered
                listing["tier_price"] = tier_price
                # Store matched tier for sold price lookups
                matched_tier = next(t for t in tiers if t["min"] <= numbered <= t["max"])
                listing["tier_max"] = matched_tier["max"]
                listing["numbered_query"] = query
                # Mark as DEAL if 0-2 bids and price < 50% of tier
                listing["is_deal"] = listing.get("bids", 0) <= 2 and listing["price"] < (tier_price * 0.5)
                deals.append(listing)

        return deals

    def send_player_email(self, player: str, numbered_deals: list, numbered_auctions: list,
                          other_deals: list, other_auctions: list):
        """Send one email per player with all their deals organized by category."""
        if not EMAIL_CONFIG["enabled"]:
            return

        total = len(numbered_deals) + len(numbered_auctions) + len(other_deals) + len(other_auctions)
        if total == 0:
            return

        subject = f"🏀 {player}: {total} deal(s) found"

        body = f"{'='*50}\n"
        body += f"🏀 {player}\n"
        body += f"{'='*50}\n\n"

        # Numbered cards section (if any)
        if numbered_deals or numbered_auctions:
            body += "📊 NUMBERED CARDS\n"
            body += "-" * 30 + "\n\n"

            if numbered_deals:
                body += f"📦 BUY IT NOW ({len(numbered_deals)})\n\n"
                for deal in numbered_deals:
                    body += f"[/{deal.get('numbered', '?')}] {deal['title']}\n"
                    body += f"   ${deal['price']:.2f}"
                    if deal['shipping'] > 0:
                        body += f" + ${deal['shipping']:.2f} ship"
                    body += f" (max ${deal.get('tier_price', 0):.2f})"
                    # Add sold price comparison
                    if deal.get('sold_info'):
                        avg = deal['sold_info']['avg_price']
                        diff = deal['price'] - avg
                        if diff < 0:
                            body += f" | Avg sold: ${avg:.2f} (${abs(diff):.2f} below)"
                        else:
                            body += f" | Avg sold: ${avg:.2f} (${diff:.2f} above)"
                    body += "\n"
                    body += f"   {deal['link']}\n"
                    if deal.get('item_id'):
                        from urllib.parse import quote
                        body += f"   [Hide] http://localhost:5050/hide?player={quote(player)}&id={deal['item_id']}\n"
                    body += "\n"

            if numbered_auctions:
                body += f"🔨 AUCTIONS ({len(numbered_auctions)})\n\n"
                for auction in numbered_auctions:
                    deal_tag = "🔥 DEAL! " if auction.get("is_deal") else ""
                    body += f"{deal_tag}[/{auction.get('numbered', '?')}] {auction['title']}\n"
                    body += f"   ${auction['price']:.2f}"
                    if auction['shipping'] > 0:
                        body += f" + ${auction['shipping']:.2f} ship"
                    body += f" ({auction.get('bids', 0)} bids"
                    if auction.get('time_left_hours'):
                        hours = auction['time_left_hours']
                        if hours < 1:
                            body += f", {int(hours * 60)}m left"
                        else:
                            body += f", {hours:.1f}h left"
                    body += f")\n   {auction['link']}\n\n"

        # Other searches section (if any)
        if other_deals or other_auctions:
            body += "🔍 OTHER SEARCHES\n"
            body += "-" * 30 + "\n\n"

            if other_deals:
                body += f"📦 BUY IT NOW ({len(other_deals)})\n\n"
                for deal in other_deals:
                    search_name = deal.get('search_query', '')[:30]
                    body += f"[{search_name}] {deal['title']}\n"
                    body += f"   ${deal['price']:.2f}"
                    if deal['shipping'] > 0:
                        body += f" + ${deal['shipping']:.2f} ship"
                    # Add sold price comparison
                    if deal.get('sold_info'):
                        avg = deal['sold_info']['avg_price']
                        diff = deal['price'] - avg
                        if diff < 0:
                            body += f" | Avg sold: ${avg:.2f} (${abs(diff):.2f} below)"
                        else:
                            body += f" | Avg sold: ${avg:.2f} (${diff:.2f} above)"
                    body += f"\n   {deal['link']}\n"
                    if deal.get('item_id'):
                        from urllib.parse import quote
                        body += f"   [Hide] http://localhost:5050/hide?player={quote(player)}&id={deal['item_id']}\n"
                    body += "\n"

            if other_auctions:
                body += f"🔨 AUCTIONS ({len(other_auctions)})\n\n"
                for auction in other_auctions:
                    deal_tag = "🔥 DEAL! " if auction.get("is_deal") else ""
                    search_name = auction.get('search_query', '')[:30]
                    body += f"{deal_tag}[{search_name}] {auction['title']}\n"
                    body += f"   ${auction['price']:.2f}"
                    if auction['shipping'] > 0:
                        body += f" + ${auction['shipping']:.2f} ship"
                    body += f" ({auction.get('bids', 0)} bids"
                    if auction.get('time_left_hours'):
                        hours = auction['time_left_hours']
                        if hours < 1:
                            body += f", {int(hours * 60)}m left"
                        else:
                            body += f", {hours:.1f}h left"
                    body += f")\n   {auction['link']}\n\n"

        body += f"\n{'='*50}\n"
        body += f"🗑️ Clear {player} history:\n"
        body += f"   python ebay_card_monitor.py --clear \"{player}\"\n"
        body += f"🗑️ Clear all history: http://localhost:5050/clear-all\n"

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
            print(f"  📧 Email sent for {player}!")
        except Exception as e:
            print(f"  ⚠️  Failed to send email: {e}")

    def run_scan(self):
        print(f"\n{'='*60}")
        print(f"eBay Card Monitor - Scan Started")
        print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        # Reset cross-search deduplication for this run
        self.seen_this_run = {}

        total_deals = 0
        total_auctions = 0

        with sync_playwright() as p:
            print("Starting browser...")
            browser = p.chromium.launch(headless=True)
            print("Browser ready.\n")

            watchlist = load_watchlist()

            for player, config in watchlist.items():
                print(f"\n{'='*50}")
                print(f"🏀 {player}")
                print(f"{'='*50}")

                # Set current player for seen_listings tracking
                self.current_player = player

                # Reset per-player deduplication
                player_seen = set()

                # Collect all deals for this player
                numbered_deals = []
                numbered_auctions = []
                other_deals = []
                other_auctions = []

                # Create browser context for this player
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = context.new_page()

                # Run numbered search first (if exists)
                if "numbered" in config:
                    numbered_config = config["numbered"]
                    query = numbered_config["query"]
                    tiers = numbered_config["tiers"]
                    numbered_search_sold = numbered_config.get("search_sold", True)

                    print(f"\n   📊 Numbered search: {query}")
                    min_price = min(t["price"] for t in tiers)
                    max_price = max(t["price"] for t in tiers)
                    print(f"      Tiers: ${min_price:.2f} - ${max_price:.2f}")

                    # BIN deals
                    deals = self.find_tiered_deals(page, query, tiers)
                    for deal in deals:
                        dedupe_key = deal["item_id"] or deal.get("link", "")
                        if dedupe_key not in player_seen:
                            player_seen.add(dedupe_key)
                            deal["search_sold"] = numbered_search_sold
                            numbered_deals.append(deal)

                    # Auctions
                    auctions = self.find_tiered_auction_deals(page, query, tiers)
                    for auction in auctions:
                        dedupe_key = auction["item_id"] or auction.get("link", "")
                        if dedupe_key not in player_seen:
                            player_seen.add(dedupe_key)
                            numbered_auctions.append(auction)

                    if numbered_deals or numbered_auctions:
                        print(f"      ✅ {len(numbered_deals)} BIN, {len(numbered_auctions)} auctions")
                    else:
                        print(f"      ❌ No numbered deals")

                    time.sleep(random.uniform(2, 4))

                # Run other searches
                searches = config.get("searches", [])
                for search in searches:
                    query = search["query"]
                    max_price = search["price"]

                    print(f"\n   🔍 Search: {query}")
                    print(f"      Max: ${max_price:.2f}")

                    search_sold = search.get("search_sold", True)

                    # BIN deals
                    deals = self.find_deals(page, query, max_price)
                    for deal in deals:
                        dedupe_key = deal["item_id"] or deal.get("link", "")
                        if dedupe_key not in player_seen:
                            player_seen.add(dedupe_key)
                            deal["search_query"] = query
                            deal["search_sold"] = search_sold
                            other_deals.append(deal)

                    # Auctions
                    auctions = self.find_auction_deals(page, query, max_price)
                    for auction in auctions:
                        dedupe_key = auction["item_id"] or auction.get("link", "")
                        if dedupe_key not in player_seen:
                            player_seen.add(dedupe_key)
                            auction["search_query"] = query
                            other_auctions.append(auction)

                    if deals or auctions:
                        new_deals = len([d for d in deals if d.get("search_query")])
                        new_auctions = len([a for a in auctions if a.get("search_query")])
                        print(f"      ✅ {new_deals} BIN, {new_auctions} auctions (after dedupe)")
                    else:
                        print(f"      ❌ No deals")

                    time.sleep(random.uniform(2, 4))

                # Fetch sold prices for BIN deals (cached weekly)
                all_bin_deals = numbered_deals + other_deals
                sold_eligible = [d for d in all_bin_deals if d.get("search_sold", True)]
                if sold_eligible:
                    print(f"\n   💰 Fetching sold prices for {len(sold_eligible)} deals...")
                    cached_count = 0
                    fetched_count = 0
                    for deal in sold_eligible:
                        # Use tier-specific key for numbered deals, title-based for others
                        if deal.get("tier_max") and deal.get("numbered_query"):
                            tier = {"min": 0, "max": deal["tier_max"]}  # Only max matters for key
                            cache_key = self._get_tier_sold_key(deal["numbered_query"], tier)
                        else:
                            cache_key = self._get_cache_key(deal['title'])
                        cache = self._load_sold_cache()
                        if cache_key in cache and self._is_cache_valid(cache[cache_key]):
                            deal['sold_info'] = cache[cache_key]
                            cached_count += 1
                        else:
                            result = self.scrape_sold_prices(page, cache_key)
                            if result:
                                cache[cache_key] = result
                                self._save_sold_cache(cache)
                                deal['sold_info'] = result
                                fetched_count += 1
                            time.sleep(random.uniform(1, 2))
                    print(f"      ✅ {cached_count} cached, {fetched_count} fetched")

                context.close()

                # Summary for player
                player_total = len(numbered_deals) + len(numbered_auctions) + len(other_deals) + len(other_auctions)
                total_deals += len(numbered_deals) + len(other_deals)
                total_auctions += len(numbered_auctions) + len(other_auctions)

                if player_total > 0:
                    print(f"\n   📧 {player}: {player_total} total deals")
                    self.send_player_email(player, numbered_deals, numbered_auctions, other_deals, other_auctions)
                else:
                    print(f"\n   ❌ No deals for {player}")

            browser.close()

        self._save_seen_listings()

        # Send any queued emails from quiet hours
        self._send_queued_emails()

        print(f"\n{'='*60}")
        print(f"Scan Complete - {total_deals} BIN deal(s), {total_auctions} auction(s)")
        print(f"{'='*60}\n")

        return total_deals + total_auctions

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

    def refresh_sold_cache(self):
        """Pre-populate sold prices cache for all watchlist items."""
        print("============================================================")
        print("Sold Price Cache Refresh")
        print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("============================================================\n")

        if not PLAYWRIGHT_AVAILABLE:
            print("❌ Playwright not installed.")
            return

        watchlist = load_watchlist()
        cache = self._load_sold_cache()
        queries_to_refresh = set()

        # Collect all unique cache keys from watchlist
        for player, config in watchlist.items():
            # Add player name as base search
            queries_to_refresh.add(player.lower())

            # Add per-tier queries for numbered searches
            if "numbered" in config:
                numbered_config = config["numbered"]
                if numbered_config.get("search_sold", True):
                    for tier in numbered_config["tiers"]:
                        key = self._get_tier_sold_key(numbered_config["query"], tier)
                        if key:
                            queries_to_refresh.add(key)

            # Add other searches (respecting search_sold flag)
            for search in config.get("searches", []):
                if not search.get("search_sold", True):
                    continue
                key = self._get_cache_key(search["query"])
                if key:
                    queries_to_refresh.add(key)

        # Filter to only queries needing refresh
        stale_queries = []
        for query in queries_to_refresh:
            if query not in cache or not self._is_cache_valid(cache[query]):
                stale_queries.append(query)

        print(f"Found {len(queries_to_refresh)} unique queries")
        print(f"Need to refresh {len(stale_queries)} stale queries\n")

        if not stale_queries:
            print("✅ All cache entries are fresh!")
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()

            for i, query in enumerate(stale_queries, 1):
                print(f"[{i}/{len(stale_queries)}] Fetching: {query[:50]}...")
                result = self.scrape_sold_prices(page, query)
                if result:
                    cache[query] = result
                    print(f"   ✅ Avg: ${result['avg_price']:.2f} ({result['num_sold']} sold)")
                else:
                    print(f"   ❌ No data found")

                self._save_sold_cache(cache)
                time.sleep(random.uniform(2, 4))

            browser.close()

        print(f"\n✅ Cache refresh complete!")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="eBay Card Monitor")
    parser.add_argument("--clear", metavar="PLAYER", help="Clear history for a specific player")
    parser.add_argument("--clear-all", action="store_true", help="Clear all history")
    parser.add_argument("--hide", nargs=2, metavar=("PLAYER", "ITEM_ID"), help="Hide a specific item for a player")
    parser.add_argument("--refresh-sold", action="store_true", help="Refresh sold prices cache (run weekly)")
    args = parser.parse_args()

    monitor = EbayCardMonitor()

    # Handle clear/hide commands
    if args.hide:
        player, item_id = args.hide
        monitor.current_player = player
        monitor._mark_seen(item_id)
        monitor._save_seen_listings()
        print(f"Hidden item {item_id} for {player}.")
        return
    if args.clear:
        monitor.clear_player_history(args.clear)
        return
    if args.clear_all:
        monitor.seen_listings = {}
        monitor._save_seen_listings()
        print("Cleared all history.")
        return
    if args.refresh_sold:
        monitor.refresh_sold_cache()
        return

    if not PLAYWRIGHT_AVAILABLE:
        print("\n❌ Playwright not installed.")
        print("   Run:")
        print("     pip install playwright")
        print("     playwright install chromium\n")
        return

    monitor.run_scan()


if __name__ == "__main__":
    main()
