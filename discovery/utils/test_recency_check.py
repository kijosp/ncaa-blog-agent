"""Test script to debug the recency check algorithm.

Usage: uv run python utils/test_recency_check.py <url>
Default URL: https://acusports.com/sports/mens-basketball/archives
"""

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.recency_check import _extract_latest_date, _DATE_PATTERNS, _llm_extract_date

import requests

logging.basicConfig(level=logging.DEBUG, format="%(message)s")
logger = logging.getLogger(__name__)

TIMEOUT = 15
MAX_BYTES = 200_000

def test_recency(url: str):
    print(f"\n{'='*80}")
    print(f"TESTING RECENCY CHECK: {url}")
    print(f"{'='*80}\n")

    # Step 1: Fetch the page
    print("[STEP 1] Fetching page...")
    try:
        resp = requests.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BlogDiscoveryBot/1.0)"},
            stream=True,
        )
        resp.raise_for_status()
        content = resp.content[:MAX_BYTES].decode("utf-8", errors="ignore")
        print(f"  ✅ Fetched {len(content)} bytes (status {resp.status_code})")
    except requests.RequestException as e:
        print(f"  ❌ Fetch failed: {e}")
        return

    # Step 2: Show a sample of the text content (stripped of long CSS/JS)
    print(f"\n[STEP 2] First 2000 chars of content (for LLM fallback):")
    print("-" * 40)
    # Strip script and style tags for readability
    clean = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', '', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    print(clean[:2000])
    print("-" * 40)

    # Step 3: Run regex date extraction
    print(f"\n[STEP 3] Running regex date extraction on full HTML ({len(content)} bytes)...")
    now = datetime.now(tz=timezone.utc)
    all_dates = []

    for pattern, fmt in _DATE_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            print(f"\n  Pattern: {pattern}")
            print(f"  Format:  {fmt}")
            print(f"  Matches ({len(matches)} total): {matches[:10]}")
            
            valid_dates = []
            for match in matches:
                normalized = match.replace("/", "-").replace(",", "")
                try:
                    dt = datetime.strptime(normalized, fmt.replace("/", "-").replace(",", ""))
                    dt = dt.replace(tzinfo=timezone.utc)
                    if datetime(2000, 1, 1, tzinfo=timezone.utc) <= dt <= now:
                        valid_dates.append(dt)
                except ValueError:
                    continue
            
            if valid_dates:
                print(f"  Valid dates: {len(valid_dates)}")
                print(f"  Latest: {max(valid_dates).strftime('%Y-%m-%d')}")
                print(f"  Oldest: {min(valid_dates).strftime('%Y-%m-%d')}")
                all_dates.extend(valid_dates)
        else:
            print(f"  Pattern {pattern}: no matches")

    if all_dates:
        latest = max(all_dates)
        days_since = (now - latest).days
        status = "active" if days_since <= 365 else "outdated"
        print(f"\n  📅 REGEX RESULT: latest={latest.strftime('%Y-%m-%d')}, days_since={days_since}, status={status}")
    else:
        print(f"\n  ⚠️  REGEX RESULT: No dates found via regex")

    # Step 4: Run LLM fallback
    print(f"\n[STEP 4] Running LLM fallback on first 2000 chars of cleaned content...")
    llm_date = _llm_extract_date(clean[:2000])
    if llm_date:
        days_since = (now - llm_date).days
        status = "active" if days_since <= 365 else "outdated"
        print(f"  📅 LLM RESULT: latest={llm_date.strftime('%Y-%m-%d')}, days_since={days_since}, status={status}")
    else:
        print(f"  ⚠️  LLM RESULT: No dates found")

    # Step 5: Final determination
    print(f"\n[STEP 5] FINAL DETERMINATION:")
    regex_latest = max(all_dates) if all_dates else None
    final = regex_latest  # Regex takes priority in current algo
    if final is None:
        final = llm_date
    
    if final:
        days_since = (now - final).days
        status = "active" if days_since <= 365 else "outdated"
        print(f"  ✅ last_post_date={final.strftime('%Y-%m-%d')}, days_since={days_since}, status={status}")
    else:
        print(f"  ❌ No dates found at all — status=unknown")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://acusports.com/sports/mens-basketball/archives"
    test_recency(url)
