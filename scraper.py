import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://speedhome.com"
CACHE_FILE = Path("data") / "speedhome_cache.json"
DEFAULT_DELAY_SECONDS = 1.5

DETAIL_ENRICH_DELAY_SECONDS = 0.75
DETAIL_ENRICH_MAX_LISTINGS = 60


PLAYWRIGHT_CHROMIUM_INSTALL_ATTEMPTED = False


def _looks_like_missing_playwright_browser(exc: Exception) -> bool:
    """Detect the common Streamlit Cloud error where Playwright is installed but Chromium is not."""
    error_text = str(exc or "")
    markers = [
        "Executable doesn't exist",
        "Please run the following command to download new browsers",
        "playwright install",
        "Looks like Playwright was just installed or updated",
        "ms-playwright/chromium",
    ]
    return any(marker in error_text for marker in markers)


def _install_playwright_chromium_once() -> Dict[str, Any]:
    """Install the Playwright Chromium browser binary once per app process.

    Streamlit Cloud installs Python packages from requirements.txt, but the
    browser binary may not exist in /home/appuser/.cache/ms-playwright. When a
    live scrape needs browser rendering, this lightweight fallback downloads
    Chromium on demand instead of failing immediately.
    """
    global PLAYWRIGHT_CHROMIUM_INSTALL_ATTEMPTED

    if PLAYWRIGHT_CHROMIUM_INSTALL_ATTEMPTED:
        return {
            "attempted": False,
            "success": False,
            "note": "Playwright Chromium install was already attempted in this app process.",
        }

    PLAYWRIGHT_CHROMIUM_INSTALL_ATTEMPTED = True

    started = time.perf_counter()

    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=180,
        )

        duration = round(time.perf_counter() - started, 2)

        return {
            "attempted": True,
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "duration_seconds": duration,
            "stdout_tail": (result.stdout or "")[-1200:],
            "stderr_tail": (result.stderr or "")[-1200:],
        }

    except Exception as install_exc:
        duration = round(time.perf_counter() - started, 2)
        return {
            "attempted": True,
            "success": False,
            "duration_seconds": duration,
            "error": str(install_exc),
        }


def _launch_playwright_chromium(playwright):
    """Launch Chromium, installing the browser binary on demand if needed."""
    launch_kwargs = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    }

    try:
        return playwright.chromium.launch(**launch_kwargs)
    except Exception as exc:
        if not _looks_like_missing_playwright_browser(exc):
            raise

        install_result = _install_playwright_chromium_once()

        if install_result.get("success"):
            return playwright.chromium.launch(**launch_kwargs)

        raise RuntimeError(
            "Playwright Chromium browser is missing and automatic installation failed. "
            f"Install result: {install_result}"
        ) from exc


# UI suggestions only.
# This list is NOT a whitelist and must NOT limit scraping.
AREA_SUGGESTIONS = [
    "Ampang",
    "Ara Damansara",
    "Bangsar",
    "Bangsar South",
    "Brickfields",
    "Bukit Bintang",
    "Bukit Jalil",
    "Cheras",
    "City Centre",
    "Cyberjaya",
    "Damansara",
    "Damansara Heights",
    "Desa ParkCity",
    "Dutamas",
    "Gombak",
    "Jalan Ipoh",
    "Kepong",
    "KLCC",
    "KL Sentral",
    "Kota Damansara",
    "Mont Kiara",
    "Mont Kiara Aman",
    "Mont Kiara Bayu",
    "Mont Kiara Palma",
    "Mont Kiara Pines",
    "Old Klang Road",
    "OUG",
    "Petaling Jaya",
    "Puchong",
    "Rawang",
    "Segambut",
    "Sentul",
    "Setapak",
    "Shah Alam",
    "Sri Hartamas",
    "Subang Jaya",
    "Sunway",
    "Taman Desa",
    "Taman Melawati",
    "Wangsa Maju",
]


class SpeedhomeFetchError(RuntimeError):
    def __init__(self, message: str, metadata: Optional[Dict] = None):
        super().__init__(message)
        self.metadata = metadata or {}


def _ensure_data_dir() -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


def clear_speedhome_cache() -> None:
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def _slugify_area(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _is_speedhome_host(hostname: str) -> bool:
    hostname = str(hostname or "").strip().lower().split(":")[0]
    return hostname == "speedhome.com" or hostname.endswith(".speedhome.com")


def is_speedhome_url_like(value: str) -> bool:
    value = str(value or "").strip().lower()
    return value.startswith("http://") or value.startswith("https://") or value.startswith("/rent/")


def get_supported_rent_slugs() -> set:
    """Return built-in UI suggestion slugs.

    The suggestion list is a UX helper, not a scraping whitelist. Direct
    SPEEDHOME /rent/<slug> URLs may be valid even when the slug is not listed.
    """
    return {_slugify_area(area) for area in AREA_SUGGESTIONS}


def get_speedhome_rent_slug(value: str) -> str:
    normalized_url = normalize_input_to_url(value)
    parsed = urlparse(normalized_url)

    if not _is_speedhome_host(parsed.netloc):
        raise ValueError("Only public SPEEDHOME URLs are supported for scraping.")

    path = (parsed.path or "").strip("/")
    parts = [part for part in path.split("/") if part]

    if len(parts) < 2 or parts[0].lower() != "rent":
        raise ValueError("Only SPEEDHOME rent pages are supported. Please use a /rent/<area-or-apartment> URL.")

    slug = _slugify_area(parts[1])

    if not slug:
        raise ValueError("Could not detect a valid SPEEDHOME rent area slug.")

    return slug


def validate_direct_speedhome_rent_url(value: str) -> str:
    """Validate URL shape without limiting it to AREA_SUGGESTIONS.

    This allows newly added SPEEDHOME rent pages outside the built-in
    autocomplete list, while still rejecting non-SPEEDHOME URLs and non-rent
    pages such as /details/...
    """
    normalized_url = normalize_input_to_url(value)
    parsed = urlparse(normalized_url)
    slug = get_speedhome_rent_slug(normalized_url)

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or urlparse(BASE_URL).netloc
    return f"{scheme}://{netloc}/rent/{slug}"


def is_direct_speedhome_rent_url_outside_suggestions(value: str) -> bool:
    try:
        slug = get_speedhome_rent_slug(value)
        return slug not in get_supported_rent_slugs()
    except Exception:
        return False


def detect_likely_concatenated_suggestion_typo(value: str) -> Optional[str]:
    """Detect obvious typo URLs such as /rent/mont-kiaraasdasd.

    A custom URL like /rent/mont-kiara-new remains allowed because the extra
    part is separated by a hyphen. This guard only catches strings where a
    known suggestion slug is directly concatenated with extra characters.
    """
    try:
        slug = get_speedhome_rent_slug(value)
    except Exception:
        return None

    for known_slug in sorted(get_supported_rent_slugs(), key=len, reverse=True):
        if slug == known_slug:
            return None

        if slug.startswith(known_slug):
            remainder = slug[len(known_slug):]
            if remainder and not remainder.startswith("-"):
                return known_slug.replace("-", " ").title()

    return None




def _custom_url_broad_fallback_guard(url: str, listings: List[Dict], metadata: Optional[Dict] = None) -> Tuple[bool, Dict[str, Any]]:
    """Reject broad fallback results for unlisted custom SPEEDHOME rent URLs.

    AREA_SUGGESTIONS is only a UX helper, not a scraping whitelist. Custom
    SPEEDHOME /rent/<slug> URLs are allowed. However, SPEEDHOME can render a
    broad fallback result set for typo/non-existent slugs, for example showing
    hundreds of unrelated direct listing cards while the page headline reports
    only a very small target count. That should be treated as an invalid target,
    not as market data.
    """
    metadata = metadata or {}

    try:
        custom_outside_suggestions = is_direct_speedhome_rent_url_outside_suggestions(url)
    except Exception:
        custom_outside_suggestions = False

    rendered_count = len(listings or [])

    try:
        reported_total = metadata.get("source_reported_total_count")
        reported_total = int(reported_total) if reported_total is not None else None
    except Exception:
        reported_total = None

    threshold = None
    if reported_total is not None:
        # A true target with 1-10 reported results should not render hundreds of
        # unrelated cards. Use a generous threshold so valid custom pages remain
        # allowed, while obvious fallback pages are rejected.
        threshold = max(25, reported_total * 10)

    suspicious = bool(
        custom_outside_suggestions
        and reported_total is not None
        and reported_total <= 10
        and rendered_count > threshold
    )

    return suspicious, {
        "custom_url_outside_suggestions": bool(custom_outside_suggestions),
        "custom_url_reported_total": reported_total,
        "custom_url_rendered_count": rendered_count,
        "custom_url_broad_fallback_threshold": threshold,
        "custom_url_broad_fallback_suspected": bool(suspicious),
    }


def _same_rent_target(requested_url: str, final_url: str) -> bool:
    try:
        return get_speedhome_rent_slug(requested_url) == get_speedhome_rent_slug(final_url)
    except Exception:
        return False


def normalize_input_to_url(user_input: str) -> str:
    if not user_input or not str(user_input).strip():
        raise ValueError("Please enter a SPEEDHOME URL, area name, or apartment name.")

    value = str(user_input).strip()

    if value.startswith("/rent/"):
        return urljoin(BASE_URL, value.split("?", 1)[0].split("#", 1)[0]).rstrip("/")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)

        if not _is_speedhome_host(parsed.netloc):
            raise ValueError("Only public SPEEDHOME URLs are supported for scraping.")

        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    slug = _slugify_area(value)

    if not slug:
        raise ValueError("Could not convert the input into a SPEEDHOME area URL.")

    return f"{BASE_URL}/rent/{slug}"


def get_source_area_from_url(url: str) -> str:
    try:
        normalized_url = normalize_input_to_url(url)
        slug = normalized_url.split("/rent/")[-1].split("/")[0]
        return slug.replace("-", " ").title()
    except Exception:
        return str(url).strip().title()


def _load_cache() -> Dict:
    if not CACHE_FILE.exists():
        return {}

    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: Dict) -> None:
    _ensure_data_dir()
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _robots_allowed(url: str) -> Tuple[bool, str, str]:
    robots_url = urljoin(BASE_URL, "/robots.txt")
    parsed = urlparse(url)
    path = parsed.path or ""

    # Assessment scope: public SPEEDHOME rent pages.
    if path.startswith("/rent/"):
        return True, robots_url, "assessment_allowed_rent_path"

    parser = RobotFileParser()
    parser.set_url(robots_url)

    try:
        parser.read()
        allowed_custom_agent = parser.can_fetch("SPEEDHOME-Price-Intelligence-Bot", url)
        allowed_wildcard_agent = parser.can_fetch("*", url)
        allowed = bool(allowed_custom_agent or allowed_wildcard_agent)
        return allowed, robots_url, "robotparser"
    except Exception:
        return True, robots_url, "robotparser_unavailable_allowed_with_note"


def _fetch_with_requests(url: str) -> Tuple[str, Dict]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_URL,
    }

    time.sleep(DEFAULT_DELAY_SECONDS)

    response = requests.get(url, headers=headers, timeout=30)

    metadata = {
        "requests_status_code": response.status_code,
        "requests_final_url": response.url,
        "requests_response_length": len(response.text or ""),
    }

    if response.status_code != 200:
        raise RuntimeError(
            f"Requests failed with HTTP {response.status_code}. "
            f"Final URL: {response.url}"
        )

    return response.text, metadata



def _extract_reported_result_count_from_text(text: str):
    """Extract SPEEDHOME's reported total count from page heading text.

    Example: "268 Zero Deposit Houses for Rent in Ampang" -> 268.
    This count is the website-reported inventory count, while the scraper's
    listing count is the number of valid direct listing cards detected during
    the current browser session.
    """
    if not text:
        return None

    patterns = [
        r"\b([\d,]+)\s+Zero\s+Deposit\s+Houses\s+for\s+Rent\b",
        r"\b([\d,]+)\s+zero[-\s]+deposit\s+houses\b",
        r"\b([\d,]+)\s+Homes\s+for\s+Rent\b",
        r"\b([\d,]+)\s+houses\s+in\s+",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if not match:
            continue

        try:
            return int(str(match.group(1)).replace(",", ""))
        except Exception:
            return None

    return None


def _extract_reported_result_count_from_html(html: str):
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = html

    return _extract_reported_result_count_from_text(text)


def _fetch_with_playwright(url: str, crawl_mode: str = "quick") -> Tuple[str, Dict]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright package is not available. Run: pip install playwright && python -m playwright install chromium"
        ) from exc

    time.sleep(DEFAULT_DELAY_SECONDS)

    captured_json_payloads: List[str] = []
    captured_json_urls: List[str] = []
    candidate_response_urls: List[str] = []
    debug_json_records: List[Dict[str, Any]] = []

    crawl_mode = str(crawl_mode or "quick").strip().lower()
    is_deeper_crawl = crawl_mode in ["deeper", "full", "extended"]

    # For large areas SPEEDHOME may expose public pagination through rel=next,
    # but some pages stop advertising the next URL even when /rent/<area>?page=N
    # still exists. Deeper mode therefore follows rel=next when available and
    # then falls back to polite numeric public pagination.
    max_pages = 18 if is_deeper_crawl else 1
    max_scroll_rounds = 18 if is_deeper_crawl else 8
    stagnant_limit = 4 if is_deeper_crawl else 2
    scroll_wait_ms = 1300 if is_deeper_crawl else 1000

    with sync_playwright() as playwright:
        browser = _launch_playwright_chromium(playwright)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1100},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": BASE_URL,
            },
        )

        page = context.new_page()

        response_status = None
        response_url = None
        final_url = url
        page_htmls: List[str] = []
        page_urls_fetched: List[str] = []
        page_detail_counts: List[int] = []
        unique_detail_links_seen: set = set()
        scroll_rounds_completed = 0
        clicked_load_more_count = 0

        def _summarize_response_payload(response_url: str, status: Any, content_type: str, text: str) -> Dict[str, Any]:
            text = text or ""
            sample = text[:12000]
            detail_urls = sorted(set(re.findall(r"https?://speedhome\.com/details/[^\"'<>\\\s]+|/details/[^\"'<>\\\s]+", text)))

            reported_total_candidates = []
            for match in re.finditer(r"\b([\d,]+)\s+Zero\s+Deposit\s+Houses\s+for\s+Rent\b", text, flags=re.IGNORECASE):
                try:
                    reported_total_candidates.append(int(match.group(1).replace(",", "")))
                except Exception:
                    pass

            lower = text.lower()
            contains_tokens = {
                "listing": "listing" in lower,
                "property": "property" in lower,
                "price": "price" in lower or "rm " in lower,
                "rent": "rent" in lower,
                "bedroom": "bedroom" in lower or "bed" in lower,
                "sqft": "sqft" in lower,
                "pagination": any(token in lower for token in ["pagination", "page=", "next", "cursor", "offset", "limit"]),
            }

            score = 0
            if response_url.endswith("/rent/ampang") or "/rent/" in response_url:
                score += 8
            if detail_urls:
                score += min(40, len(detail_urls))
            if reported_total_candidates:
                score += 10
            if contains_tokens["property"] and contains_tokens["price"] and contains_tokens["rent"]:
                score += 10
            if contains_tokens["pagination"]:
                score += 8

            record = {
                "url": response_url,
                "status": status,
                "content_type": content_type,
                "size_bytes": len(text.encode("utf-8", errors="ignore")),
                "detail_url_count_in_payload": len(detail_urls),
                "reported_total_candidates": reported_total_candidates[:10],
                "contains_tokens": contains_tokens,
                "likely_listing_endpoint_score": score,
                "sample": sample,
            }

            # Best-effort JSON shape extraction. Many useful Next.js responses are HTML/JS, not JSON.
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    record["top_level_type"] = "dict"
                    record["top_level_keys"] = list(parsed.keys())[:30]
                elif isinstance(parsed, list):
                    record["top_level_type"] = "list"
                    record["top_level_keys"] = []
                else:
                    record["top_level_type"] = type(parsed).__name__
                    record["top_level_keys"] = []
            except Exception:
                record["parse_error"] = "Could not parse response body as JSON."

            return record

        def capture_response(response):
            try:
                response_url_lower = response.url.lower()
                content_type = (response.headers.get("content-type") or "").lower()

                if any(token in response_url_lower for token in [
                    "/rent/",
                    "/api/",
                    "graphql",
                    "property",
                    "listing",
                    "search",
                    "page=",
                    "_next/data",
                    "_next/static/chunks/pages/rent",
                ]):
                    if response.url not in candidate_response_urls:
                        candidate_response_urls.append(response.url)

                looks_interesting = (
                    "application/json" in content_type
                    or "text/html" in content_type
                    or "/api/" in response_url_lower
                    or "graphql" in response_url_lower
                    or "property" in response_url_lower
                    or "listing" in response_url_lower
                    or "search" in response_url_lower
                    or "/rent/" in response_url_lower
                    or "_next/data" in response_url_lower
                    or "_next/static/chunks/pages/rent" in response_url_lower
                )

                if not looks_interesting:
                    return

                if len(debug_json_records) >= 60:
                    return

                text = response.text()

                if not text:
                    return

                if len(text) > 2_500_000:
                    text = text[:2_500_000]

                lower = text.lower()
                if not any(token in lower for token in ["price", "rent", "bedroom", "bathroom", "furnished", "property", "listing", "sqft", "/details/", "page="]):
                    return

                if len(captured_json_payloads) < 20:
                    captured_json_payloads.append(text)
                    captured_json_urls.append(response.url)

                debug_json_records.append(
                    _summarize_response_payload(
                        response.url,
                        response.status,
                        content_type,
                        text,
                    )
                )
            except Exception:
                return

        page.on("response", capture_response)

        def count_detail_links_on_page() -> int:
            try:
                return int(
                    page.evaluate(
                        """
                        () => new Set(
                            Array.from(document.querySelectorAll('a[href*="/details/"]'))
                                .map(a => a.href || a.getAttribute('href') || '')
                                .filter(Boolean)
                        ).size
                        """
                    )
                )
            except Exception:
                return 0

        def get_detail_links_on_page() -> List[str]:
            try:
                return page.evaluate(
                    """
                    () => Array.from(new Set(
                        Array.from(document.querySelectorAll('a[href*="/details/"]'))
                            .map(a => a.href || a.getAttribute('href') || '')
                            .filter(Boolean)
                    ))
                    """
                ) or []
            except Exception:
                return []

        def get_next_page_url() -> Optional[str]:
            try:
                next_href = page.evaluate(
                    """
                    () => {
                        const link = document.querySelector('link[rel="next"]');
                        if (link && link.href) return link.href;

                        const anchors = Array.from(document.querySelectorAll('a[href]'));
                        const nextAnchor = anchors.find((a) => {
                            const text = (a.innerText || a.textContent || '').trim().toLowerCase();
                            const rel = (a.getAttribute('rel') || '').toLowerCase();
                            const aria = (a.getAttribute('aria-label') || '').toLowerCase();
                            if (rel.includes('next') || aria.includes('next')) return true;
                            return ['next', '>', '›', 'load more', 'show more'].includes(text);
                        });
                        return nextAnchor ? nextAnchor.href : null;
                    }
                    """
                )
            except Exception:
                next_href = None

            if not next_href:
                return None

            try:
                parsed = urlparse(next_href)
                if "speedhome.com" not in parsed.netloc.lower():
                    return None
                if not parsed.path.lower().startswith("/rent/"):
                    return None
                return next_href.split("#")[0]
            except Exception:
                return None

        def build_numeric_page_url(page_number: int) -> Optional[str]:
            try:
                parsed = urlparse(url)
                if "speedhome.com" not in parsed.netloc.lower():
                    return None

                base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

                if page_number <= 1:
                    return base

                return f"{base}?page={page_number}"
            except Exception:
                return None

        def try_click_load_more():
            try:
                return page.evaluate(
                    """
                    () => {
                        const tokens = ['load more', 'show more', 'view more', 'more listings'];
                        const elements = Array.from(document.querySelectorAll('button, a'));
                        const candidate = elements.find((el) => {
                            const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                            if (!text) return false;
                            if (el.disabled) return false;
                            return tokens.some((token) => text.includes(token));
                        });
                        if (!candidate) return null;
                        candidate.click();
                        return (candidate.innerText || candidate.textContent || '').trim();
                    }
                    """
                )
            except Exception:
                return None

        def scroll_current_page() -> None:
            nonlocal scroll_rounds_completed, clicked_load_more_count
            stable_scroll_count = 0
            stagnant_detail_count = 0
            previous_height = 0
            best_detail_count = count_detail_links_on_page()

            for _ in range(max_scroll_rounds):
                scroll_rounds_completed += 1

                try:
                    current_height = page.evaluate("document.body.scrollHeight")
                except Exception:
                    current_height = previous_height

                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass

                page.wait_for_timeout(scroll_wait_ms)
                current_detail_count = count_detail_links_on_page()

                if current_detail_count > best_detail_count:
                    best_detail_count = current_detail_count
                    stagnant_detail_count = 0
                else:
                    stagnant_detail_count += 1

                if current_height == previous_height:
                    stable_scroll_count += 1
                else:
                    stable_scroll_count = 0

                previous_height = current_height

                if is_deeper_crawl and stagnant_detail_count >= 2:
                    clicked_label = try_click_load_more()
                    if clicked_label:
                        clicked_load_more_count += 1
                        page.wait_for_timeout(2200)
                        after_click_detail_count = count_detail_links_on_page()
                        if after_click_detail_count > best_detail_count:
                            best_detail_count = after_click_detail_count
                            stagnant_detail_count = 0

                if stable_scroll_count >= stagnant_limit and stagnant_detail_count >= stagnant_limit:
                    break

        visited_page_urls = set()
        current_url = url
        consecutive_no_new_pages = 0
        pagination_stop_reason = "max_pages_reached"

        try:
            for page_number in range(1, max_pages + 1):
                if not current_url:
                    pagination_stop_reason = "no_current_url"
                    break

                normalized_current = current_url.split("#")[0].rstrip("/")
                if normalized_current in visited_page_urls:
                    pagination_stop_reason = "duplicate_page_url"
                    break

                before_unique_count = len(unique_detail_links_seen)
                visited_page_urls.add(normalized_current)

                response = page.goto(current_url, wait_until="domcontentloaded", timeout=90000)

                if page_number == 1 and response:
                    response_status = response.status
                    response_url = response.url

                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    pass

                page.wait_for_timeout(1800)
                scroll_current_page()

                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                page.wait_for_timeout(800)

                current_html = page.content()
                page_htmls.append(current_html)
                page_urls_fetched.append(page.url)

                current_links = get_detail_links_on_page()
                page_detail_counts.append(len(set(current_links)))
                for link in current_links:
                    if link:
                        unique_detail_links_seen.add(str(link).split("?")[0].split("#")[0].rstrip("/"))

                new_links_added = len(unique_detail_links_seen) - before_unique_count

                if new_links_added <= 0:
                    consecutive_no_new_pages += 1
                else:
                    consecutive_no_new_pages = 0

                final_url = page.url

                if not is_deeper_crawl:
                    pagination_stop_reason = "quick_mode_single_page"
                    break

                # Stop after several consecutive public pages add no new listing links.
                # This prevents wasting time when SPEEDHOME starts repeating/empty pages,
                # while still allowing page 2/3 overlap before later pages add new cards.
                if page_number >= 4 and consecutive_no_new_pages >= 3:
                    pagination_stop_reason = "consecutive_pages_without_new_detail_links"
                    break

                next_url = get_next_page_url()

                if not next_url:
                    next_url = build_numeric_page_url(page_number + 1)

                if not next_url:
                    pagination_stop_reason = "no_next_or_numeric_page_url"
                    break

                # Be polite between paginated public pages.
                page.wait_for_timeout(1000)
                current_url = next_url

        finally:
            context.close()
            browser.close()

    html = "\n<!-- SPEEDHOME_PAGE_BREAK -->\n".join(page_htmls)

    debug_json_records_sorted = sorted(
        debug_json_records,
        key=lambda record: record.get("likely_listing_endpoint_score", 0),
        reverse=True,
    )

    metadata = {
        "playwright_final_url": final_url,
        "playwright_response_status": response_status,
        "playwright_response_url": response_url,
        "playwright_html_length": len(html or ""),
        "playwright_captured_json_payload_count": len(captured_json_payloads),
        "playwright_captured_json_urls": captured_json_urls[:20],
        "playwright_candidate_response_urls": candidate_response_urls[:120],
        "playwright_debug_json_summary_count": len(debug_json_records),
        "playwright_top_debug_json_urls": [record.get("url") for record in debug_json_records_sorted[:10]],
        "playwright_crawl_mode": crawl_mode,
        "playwright_scroll_rounds_completed": scroll_rounds_completed,
        "playwright_best_detail_link_count_seen": len(unique_detail_links_seen),
        "playwright_clicked_load_more_count": clicked_load_more_count,
        "playwright_paginated_pages_fetched": len(page_htmls),
        "playwright_page_urls_fetched": page_urls_fetched[:20],
        "playwright_page_detail_counts": page_detail_counts[:20],
        "playwright_numeric_pagination_enabled": bool(is_deeper_crawl),
        "playwright_pagination_stop_reason": pagination_stop_reason,
        "_captured_json_payloads": captured_json_payloads,
        "_debug_json_records": debug_json_records_sorted,
        "_candidate_response_urls": candidate_response_urls,
    }

    if not html or len(html) < 1000:
        raise RuntimeError(
            f"Playwright loaded too little HTML. Status: {response_status}, Final URL: {final_url}"
        )

    return html, metadata

def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()



def _format_duration(seconds: Any) -> str:
    """Format scrape duration for UI diagnostics and exported reports."""
    try:
        seconds = float(seconds)
    except Exception:
        return "-"

    if seconds < 0:
        return "-"

    if seconds < 1:
        return f"{seconds:.2f} sec"

    if seconds < 60:
        return f"{seconds:.1f} sec"

    minutes = int(seconds // 60)
    remaining_seconds = int(round(seconds % 60))

    if remaining_seconds == 60:
        minutes += 1
        remaining_seconds = 0

    return f"{minutes} min {remaining_seconds:02d} sec"


def _extract_number(value: Any):
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)

        if number.is_integer():
            return int(number)

        return number

    value = str(value).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", value)

    if not match:
        return None

    try:
        number = float(match.group(0))

        if number.is_integer():
            return int(number)

        return number
    except Exception:
        return None


def _parse_price_rm(text: str):
    if not text:
        return None

    patterns = [
        r"RM\s*([\d,]+)\s*(?:\/|\s+per\s+)?(?:month|monthly|mo|mth)",
        r"(?:monthly|month|rental|rent)\s*[:\-]?\s*RM\s*([\d,]+)",
        r"RM\s*([\d,]+)",
    ]

    candidates = []

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            number = _extract_number(match.group(1))

            if number and 300 <= number <= 100000:
                candidates.append(number)

    if not candidates:
        return None

    return candidates[0]


def _parse_daily_price_rm(text: str):
    if not text:
        return None

    patterns = [
        r"RM\s*([\d,]+)\s*(?:\/|\s+per\s+)?(?:day|daily|night)",
        r"(?:daily|day|night)\s*[:\-]?\s*RM\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number and 20 <= number <= 5000:
                return number

    return None


def _parse_explicit_yearly_price_rm(text: str):
    if not text:
        return None

    patterns = [
        r"RM\s*([\d,]+)\s*(?:\/|\s+per\s+)?(?:year|yearly|annum)",
        r"(?:yearly|year|annual|annum)\s*[:\-]?\s*RM\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number and 1000 <= number <= 1_000_000:
                return number

    return None


def _parse_size_sqft(text: str):
    if not text:
        return None

    patterns = [
        r"([\d,]+)\s*(?:sqft|sq\s*ft|sf|square\s*feet)",
        r"Built[-\s]?up\s*:?\s*([\d,]+)",
        r"Size\s*:?\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number and 100 <= number <= 20000:
                return number

    return None


def _parse_detail_icon_metrics(text: str) -> Dict[str, Any]:
    """Parse the top detail-page metric row: sqft, bedrooms, bathrooms, car parks.

    SPEEDHOME detail pages render a compact icon row, e.g.
    "502 sqft 1 1 1 RM 1500". Generic bedroom regex can be misled by
    unit names such as "Y-13-08", so this row is treated as a stronger source
    when detail enrichment is enabled.
    """
    if not text:
        return {}

    clean = _clean_text(str(text))

    pattern = (
        r"([\d,]+)\s*sqft\s+"
        r"(\d{1,2})\s+"
        r"(\d{1,2})\s+"
        r"(\d{1,2})"
        r"(?=\s+(?:RM|Zero|Verified|Accept|Near|Walk|WFH|Easy|Parking|Move-in|Min|"
        r"Not|Fully|Partially|Partly|Unfurnished|Furnished|Amenities|Map|A\s+Closer|$))"
    )

    match = re.search(pattern, clean, flags=re.IGNORECASE)
    if not match:
        return {}

    size_sqft = _extract_number(match.group(1))
    bedrooms = _extract_number(match.group(2))
    bathrooms = _extract_number(match.group(3))
    carparks = _extract_number(match.group(4))

    if not size_sqft or not (100 <= float(size_sqft) <= 20000):
        return {}

    for number in [bedrooms, bathrooms, carparks]:
        if number is None or not (0 <= int(number) <= 20):
            return {}

    return {
        "size_sqft": size_sqft,
        "bedrooms": bedrooms,
        "bedroom_count": bedrooms,
        "bathrooms": bathrooms,
        "bathroom_count": bathrooms,
        "carparks": carparks,
        "carpark_count": carparks,
        "detail_metrics_source": "detail_top_icon_metrics",
    }


def _parse_bedroom_count(text: str):
    if not text:
        return None

    patterns = [
        r"(\d+)\s*(?:bedroom|bedrooms|beds|bed|br)\b",
        r"(\d+)\s*R\s*(?:\d+\s*B)?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number is not None and 0 <= number <= 20:
                return number

    studio_match = re.search(r"\bstudio\b", text, flags=re.IGNORECASE)

    if studio_match:
        return 0

    return None


def _parse_bathroom_count(text: str):
    if not text:
        return None

    patterns = [
        r"(\d+)\s*(?:bathroom|bathrooms|baths|bath|ba)\b",
        r"\d+\s*R\s*(\d+)\s*B",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number is not None and 0 <= number <= 20:
                return number

    return None


def _parse_bathroom_type_label(text: str):
    """Parse SPEEDHOME room-rental bathroom privacy labels.

    On room-rental cards SPEEDHOME may show compact metrics such as
    ``MEDIUM | SHARED | 0``. In that context MEDIUM is the room type,
    SHARED/PRIVATE describes the bathroom arrangement, and the last value is
    usually car park count. SHARED must not be interpreted as a room type.
    """
    if not text:
        return None

    normalized = " " + re.sub(r"[^a-z0-9]+", " ", str(text).lower()) + " "

    if re.search(r"\bprivate\b", normalized, flags=re.IGNORECASE):
        return "Private Bathroom"

    if re.search(r"\bshared\b", normalized, flags=re.IGNORECASE):
        return "Shared Bathroom"

    return None


def _parse_carpark_count(text: str):
    if not text:
        return None

    patterns = [
        r"(\d+)\s*(?:carpark|carparks|parking|parkings|car\s*park)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if match:
            number = _extract_number(match.group(1))

            if number is not None and 0 <= number <= 20:
                return number

    return None


def _normalize_furnishing_label(value: Any):
    """Normalize SPEEDHOME furnishing text into one of the app labels."""
    if not value:
        return None

    text_lower = str(value).strip().lower()

    # Check negative/explicit statuses before generic "furnished" so phrases
    # such as "Not Furnished" never become the generic Furnished label.
    if "not furnished" in text_lower or "unfurnished" in text_lower:
        return "Unfurnished"

    if "partially furnished" in text_lower or "partly furnished" in text_lower:
        return "Partially Furnished"

    if "fully furnished" in text_lower:
        return "Fully Furnished"

    if text_lower == "furnished" or re.search(r"(?<![a-z0-9])furnished(?![a-z0-9])", text_lower):
        return "Furnished"

    return None


def _parse_furnishing(text: str):
    if not text:
        return None

    return _normalize_furnishing_label(text)


def _parse_detail_official_furnishing(text: str):
    """Prefer the official furnishing label shown beside the Amenities heading.

    SPEEDHOME detail pages can contain marketing copy under "A Closer Look" that
    says things like "fully furnished" even when the structured detail section
    above says "Not Furnished • Amenities". For detail-page enrichment, the
    furnishing status beside the Amenities heading is the safer source of truth.
    """
    if not text:
        return None

    clean = _clean_text(str(text))

    # Examples observed:
    #   "Fully Furnished • Amenities"
    #   "Not Furnished • Amenities"
    #   "Partially Furnished • Amenities"
    # Accept a few separators because rendered text can vary.
    status_pattern = r"(Not\s+Furnished|Unfurnished|Partially\s+Furnished|Partly\s+Furnished|Fully\s+Furnished|Furnished)"
    separator_pattern = r"(?:\s*[•|\-–—:]\s*|\s+)"

    patterns = [
        status_pattern + separator_pattern + r"Amenities\b",
        r"Amenities" + separator_pattern + status_pattern,
    ]

    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if match:
            # In the second pattern the status is group 2.
            raw_status = match.group(1) if match.lastindex == 1 else match.group(match.lastindex)
            normalized = _normalize_furnishing_label(raw_status)
            if normalized:
                return normalized

    return None


def _parse_room_unit_type(text: str):
    """Parse SPEEDHOME room-rental size/type labels.

    SPEEDHOME room-rental cards can show compact metrics like
    ``MASTER | PRIVATE | 0`` or ``MEDIUM | SHARED | 0``. MASTER/MEDIUM/SMALL
    are treated as unit type labels. PRIVATE/SHARED describe bathroom arrangement and
    are intentionally handled by ``_parse_bathroom_type_label`` instead.
    """
    if not text:
        return None

    normalized = " " + re.sub(r"[^a-z0-9]+", " ", str(text).lower()) + " "

    room_type_patterns = [
        (r"\bmaster\b", "Master"),
        (r"\bmedium\b", "Medium"),
        (r"\bsmall\b", "Small"),
    ]

    for pattern, label in room_type_patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return label

    # Do not create a generic "Room" unit type. SPEEDHOME room labels used in
    # this app are limited to Master, Medium, and Small.
    return None

def _bedroom_type_from_count(bedrooms, raw_text: str = ""):
    # Room-rental labels are more specific than numeric bedroom counts.
    # Example: ``MEDIUM | SHARED | 0`` means Medium + Shared Bathroom,
    # not Studio. Detail-page text can also contain a top icon metric of 0
    # bedrooms while the official room label remains MEDIUM/MASTER/SMALL.
    room_unit_type = _parse_room_unit_type(raw_text)
    if room_unit_type:
        return room_unit_type

    if bedrooms is None:
        return "Unknown"

    try:
        bedrooms = int(bedrooms)
    except Exception:
        return "Unknown"

    if bedrooms == 0:
        return "Studio"

    if bedrooms == 1:
        return "1 Bedroom"

    return f"{bedrooms} Bedrooms"



def _is_direct_speedhome_listing_url(url: Any) -> bool:
    """Return True only for a real SPEEDHOME listing detail URL.

    This intentionally rejects area/search pages such as /rent/bukit-bintang.
    The assessment asks for a direct listing link that can be opened for verification,
    so search/result URLs are not treated as valid listing URLs.
    """
    if not url:
        return False

    try:
        parsed = urlparse(str(url).strip())
    except Exception:
        return False

    if "speedhome.com" not in parsed.netloc.lower():
        return False

    path = parsed.path.lower().rstrip("/")

    if not path:
        return False

    return "/details/" in path


def _looks_like_non_listing_text(title: Any, raw_text: Any = "") -> bool:
    title_text = _clean_text(str(title or ""))
    raw = _clean_text(str(raw_text or ""))
    combined = f"{title_text} {raw}".lower()
    title_lower = title_text.lower()

    if not title_text:
        return True

    # FAQ / schema / SEO article noise. These are common inside JSON payloads and
    # previously leaked into Unit Listings as if they were properties.
    question_starts = (
        "how ",
        "what ",
        "why ",
        "when ",
        "where ",
        "which ",
        "who ",
        "can ",
        "do ",
        "does ",
        "is ",
        "are ",
        "should ",
    )

    if "?" in title_text and title_lower.startswith(question_starts):
        return True

    noise_tokens = [
        "faq",
        "frequently asked",
        "average rental price",
        "how do i apply",
        "how to apply",
        "terms and conditions",
        "privacy policy",
        "cookie policy",
        "login",
        "signup",
        "sign up",
        "whatsapp",
        "contact us",
        "about speedhome",
        "tenant guide",
        "landlord guide",
        "blog",
        "article",
        "breadcrumb",
        "search result",
    ]

    if any(token in combined for token in noise_tokens):
        return True

    # Very long titles are usually SEO text, not listing card titles.
    if len(title_text) > 180:
        return True

    return False


def _json_object_is_schema_or_seo_noise(obj: Dict) -> bool:
    """Reject JSON-LD/schema/meta objects that are not rental units."""
    if not isinstance(obj, dict):
        return True

    type_value = obj.get("@type") or obj.get("type") or obj.get("__typename")

    if isinstance(type_value, list):
        type_text = " ".join([str(item) for item in type_value]).lower()
    else:
        type_text = str(type_value or "").lower()

    noise_types = [
        "faqpage",
        "question",
        "answer",
        "breadcrumb",
        "breadcrumblist",
        "webpage",
        "website",
        "organization",
        "person",
        "imageobject",
        "searchaction",
        "aggregateoffer",
    ]

    return any(token in type_text for token in noise_types)


def _valid_listing_evidence_count(listing: Dict) -> int:
    evidence_fields = [
        "monthly_price_rm",
        "size_sqft",
        "bedrooms",
        "room_type_label",
        "bathrooms",
        "carparks",
        "furnishing",
    ]

    count = 0

    for field in evidence_fields:
        value = listing.get(field)
        if value not in [None, "", "Unknown", "Not specified"]:
            count += 1

    return count


def _is_valid_listing_candidate(listing: Dict) -> bool:
    """Final safety gate before anything becomes a listing row."""
    if not listing:
        return False

    listing_url = listing.get("listing_url")

    if not _is_direct_speedhome_listing_url(listing_url):
        return False

    if _looks_like_non_listing_text(listing.get("title"), listing.get("raw_text")):
        return False

    monthly_price = listing.get("monthly_price_rm")

    if monthly_price is None:
        return False

    try:
        if not (300 <= float(monthly_price) <= 100000):
            return False
    except Exception:
        return False

    # Price alone is too weak because FAQ/SEO blocks often contain rental numbers.
    # Require at least one real unit attribute in addition to monthly rent.
    return _valid_listing_evidence_count(listing) >= 2



def _normalize_listing_url(value: Any, source_url: Optional[str] = None) -> Optional[str]:
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

    # Do not try to interpret JSON blobs, schema.org scripts, or full text blocks
    # as URLs. Earlier versions accidentally accepted a whole JSON-LD object
    # because the text contained the token "/details/" somewhere inside it.
    if len(value) > 500 or any(token in value for token in ["{", "}", "[", "]", '"', "'", "\n", "\r", "\t"]):
        return None

    value = value.replace("\\/", "/")

    # Some embedded data may contain a malformed but recoverable SPEEDHOME URL.
    if value.startswith("https:/speedhome.com/") and not value.startswith("https://speedhome.com/"):
        value = value.replace("https:/speedhome.com/", "https://speedhome.com/", 1)

    if value.startswith("//"):
        value = "https:" + value

    full_url = urljoin(BASE_URL, value).split("?")[0].split("#")[0].rstrip("/")

    parsed = urlparse(full_url)

    if "speedhome.com" not in parsed.netloc.lower():
        return None

    path = parsed.path.lower().rstrip("/")

    if not path or path == "/":
        return None

    source_path = urlparse(source_url or "").path.rstrip("/").lower()

    if path == source_path:
        return None

    # Unit Listings must be individual SPEEDHOME detail pages, not /rent search
    # pages, FAQ pages, breadcrumbs, or arbitrary schema text.
    if "/details/" not in path:
        return None

    return full_url

def _is_candidate_listing_href(href: Any, source_url: str) -> bool:
    return _normalize_listing_url(href, source_url=source_url) is not None


def _flatten_scalar_values(value: Any, max_items: int = 200) -> List[Any]:
    output = []
    stack = [value]

    while stack and len(output) < max_items:
        item = stack.pop()

        if isinstance(item, dict):
            for child in item.values():
                stack.append(child)
        elif isinstance(item, list):
            for child in item:
                stack.append(child)
        elif isinstance(item, (str, int, float)) and not isinstance(item, bool):
            output.append(item)

    return output


def _collect_key_values(value: Any, max_items: int = 600) -> List[Tuple[str, Any]]:
    output = []
    stack = [("", value)]

    while stack and len(output) < max_items:
        path, item = stack.pop()

        if isinstance(item, dict):
            for key, child in item.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if path else key_text

                if isinstance(child, (dict, list)):
                    stack.append((child_path, child))
                elif isinstance(child, (str, int, float)) and not isinstance(child, bool):
                    output.append((child_path, child))

        elif isinstance(item, list):
            for index, child in enumerate(item):
                child_path = f"{path}[{index}]"

                if isinstance(child, (dict, list)):
                    stack.append((child_path, child))
                elif isinstance(child, (str, int, float)) and not isinstance(child, bool):
                    output.append((child_path, child))

    return output


def _key_contains(key: str, include_tokens: List[str], exclude_tokens: Optional[List[str]] = None) -> bool:
    normalized_key = re.sub(r"[^a-z0-9]+", "", key.lower())

    if exclude_tokens:
        for token in exclude_tokens:
            if token in normalized_key:
                return False

    return any(token in normalized_key for token in include_tokens)


def _find_number_by_key(
    obj: Any,
    include_tokens: List[str],
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    exclude_tokens: Optional[List[str]] = None,
):
    for key, value in _collect_key_values(obj):
        if not _key_contains(key, include_tokens, exclude_tokens=exclude_tokens):
            continue

        number = _extract_number(value)

        if number is None:
            continue

        if min_value is not None and number < min_value:
            continue

        if max_value is not None and number > max_value:
            continue

        return number

    return None


def _find_text_by_key(
    obj: Any,
    include_tokens: List[str],
    exclude_tokens: Optional[List[str]] = None,
    min_len: int = 2,
    max_len: int = 180,
):
    for key, value in _collect_key_values(obj):
        if not _key_contains(key, include_tokens, exclude_tokens=exclude_tokens):
            continue

        if not isinstance(value, str):
            continue

        text = _clean_text(value)

        if min_len <= len(text) <= max_len:
            return text

    return None


def _extract_title_from_json_object(obj: Dict, raw_text: str, source_area: str) -> str:
    title = _find_text_by_key(
        obj,
        include_tokens=[
            "title",
            "name",
            "propertyname",
            "buildingname",
            "projectname",
            "displayname",
        ],
        exclude_tokens=[
            "username",
            "agentname",
            "ownername",
            "typename",
        ],
        min_len=4,
        max_len=160,
    )

    if title:
        return title

    for value in _flatten_scalar_values(obj, max_items=80):
        if not isinstance(value, str):
            continue

        text = _clean_text(value)

        if len(text) < 8 or len(text) > 160:
            continue

        text_lower = text.lower()

        if any(token in text_lower for token in ["rm ", "http", "whatsapp", "login", "signup"]):
            continue

        if any(token in text_lower for token in ["rent", "apartment", "condo", "residence", source_area.lower()]):
            return text

    return f"Rental listing in {source_area}"


def _extract_url_from_json_object(obj: Dict, source_url: str) -> Optional[str]:
    for key, value in _collect_key_values(obj):
        if not isinstance(value, str):
            continue

        if not _key_contains(
            key,
            include_tokens=["url", "href", "link", "slug", "path"],
            exclude_tokens=["image", "photo", "avatar", "logo"],
        ):
            continue

        candidate = _normalize_listing_url(value, source_url=source_url)

        if candidate:
            return candidate

    for value in _flatten_scalar_values(obj, max_items=120):
        if not isinstance(value, str):
            continue

        candidate = _normalize_listing_url(value, source_url=source_url)

        if candidate:
            return candidate

    return None



def _extract_listing_from_json_object(obj: Dict, source_url: str, source_area: str) -> Optional[Dict]:
    if _json_object_is_schema_or_seo_noise(obj):
        return None

    raw_text = _clean_text(" ".join([str(value) for value in _flatten_scalar_values(obj, max_items=160)]))

    if not raw_text:
        return None

    listing_url = _extract_url_from_json_object(obj, source_url)

    # Critical: never fallback to the source /rent/<area> page.
    # A row without a direct /details/ URL cannot satisfy the assessment's
    # "clickable direct listing link" requirement.
    if not _is_direct_speedhome_listing_url(listing_url):
        return None

    monthly_price = _find_number_by_key(
        obj,
        include_tokens=[
            "monthlyrent",
            "monthlyprice",
            "monthlyrental",
            "rentalprice",
            "rentprice",
            "rent",
            "price",
        ],
        min_value=300,
        max_value=100000,
        exclude_tokens=[
            "deposit",
            "fee",
            "agent",
            "min",
            "max",
            "sale",
            "selling",
            "purchase",
        ],
    )

    if monthly_price is None:
        monthly_price = _parse_price_rm(raw_text)

    daily_price = _find_number_by_key(
        obj,
        include_tokens=["daily", "dayprice", "nightprice"],
        min_value=20,
        max_value=5000,
        exclude_tokens=["monthly", "yearly", "annual"],
    )

    if daily_price is None:
        daily_price = _parse_daily_price_rm(raw_text)

    explicit_yearly_price = _find_number_by_key(
        obj,
        include_tokens=["yearly", "annual", "annum"],
        min_value=1000,
        max_value=1_000_000,
        exclude_tokens=["monthly", "daily"],
    )

    if explicit_yearly_price is None:
        explicit_yearly_price = _parse_explicit_yearly_price_rm(raw_text)

    size_sqft = _find_number_by_key(
        obj,
        include_tokens=[
            "sqft",
            "builtup",
            "buildup",
            "builtarea",
            "areasize",
            "size",
        ],
        min_value=100,
        max_value=20000,
        exclude_tokens=["price", "rent", "deposit"],
    )

    if size_sqft is None:
        size_sqft = _parse_size_sqft(raw_text)

    bedrooms = _find_number_by_key(
        obj,
        include_tokens=["bedroom", "bedrooms", "bedcount", "roomcount"],
        min_value=0,
        max_value=20,
        exclude_tokens=["bath", "bathroom"],
    )

    if bedrooms is None:
        bedrooms = _parse_bedroom_count(raw_text)

    bathrooms = _find_number_by_key(
        obj,
        include_tokens=["bathroom", "bathrooms", "bathcount"],
        min_value=0,
        max_value=20,
    )

    if bathrooms is None:
        bathrooms = _parse_bathroom_count(raw_text)

    carparks = _find_number_by_key(
        obj,
        include_tokens=["carpark", "carparks", "parking"],
        min_value=0,
        max_value=20,
    )

    if carparks is None:
        carparks = _parse_carpark_count(raw_text)

    furnishing = _find_text_by_key(
        obj,
        include_tokens=["furnish", "furnishing"],
        min_len=4,
        max_len=80,
    )

    if furnishing:
        furnishing = _parse_furnishing(furnishing) or furnishing
    else:
        furnishing = _parse_furnishing(raw_text)

    title = _extract_title_from_json_object(obj, raw_text, source_area)

    property_area = _find_text_by_key(
        obj,
        include_tokens=["area", "locality", "district", "city", "address"],
        exclude_tokens=["size", "builtarea", "areasize"],
        min_len=3,
        max_len=120,
    ) or source_area

    listing = {
        "title": title,
        "property_area": property_area,
        "source_area": source_area,
        "bedrooms": bedrooms,
        "bedroom_count": bedrooms,
        "bedroom_type": _bedroom_type_from_count(bedrooms, raw_text),
        "room_type_label": _parse_room_unit_type(raw_text),
        "bathrooms": bathrooms,
        "bathroom_count": bathrooms,
        "bathroom_type_label": _parse_bathroom_type_label(raw_text),
        "carparks": carparks,
        "carpark_count": carparks,
        "monthly_price_rm": monthly_price,
        "yearly_price_rm": explicit_yearly_price,
        "explicit_yearly_price_rm": explicit_yearly_price,
        "estimated_yearly_price_rm": monthly_price * 12 if monthly_price else None,
        "daily_price_rm": daily_price,
        "size_sqft": size_sqft,
        "furnishing": furnishing,
        "detected_rental_type": "Monthly" if monthly_price else "Unknown",
        "listing_url": listing_url,
        "source_url": source_url,
        "rental_type_monthly_available": bool(monthly_price),
        "rental_type_yearly_available": bool(explicit_yearly_price),
        "rental_type_daily_available": bool(daily_price),
        "raw_text": raw_text[:1500],
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }

    if not _is_valid_listing_candidate(listing):
        return None

    return listing

def _iter_dicts(data: Any, max_items: int = 15000):
    stack = [data]
    visited = 0

    while stack and visited < max_items:
        item = stack.pop()
        visited += 1

        if isinstance(item, dict):
            yield item

            for child in item.values():
                if isinstance(child, (dict, list)):
                    stack.append(child)

        elif isinstance(item, list):
            for child in item:
                if isinstance(child, (dict, list)):
                    stack.append(child)


def _safe_json_loads(text: str):
    if not text:
        return None

    text = text.strip()

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_json_payloads_from_html(soup: BeautifulSoup) -> List[Any]:
    payloads = []

    for script in soup.find_all("script"):
        content = script.string or script.get_text() or ""
        content = content.strip()

        if not content:
            continue

        script_type = (script.get("type") or "").lower()
        script_id = (script.get("id") or "").lower()

        should_try = (
            script_type in ["application/json", "application/ld+json"]
            or script_id in ["__next_data__", "__nuxt_data__"]
            or content.startswith("{")
            or content.startswith("[")
        )

        if not should_try:
            continue

        data = _safe_json_loads(content)

        if data is not None:
            payloads.append(data)

    return payloads


def _parse_json_payloads(payloads: List[Any], source_url: str, source_area: str) -> List[Dict]:
    listings = []

    for payload in payloads:
        for obj in _iter_dicts(payload):
            listing = _extract_listing_from_json_object(obj, source_url, source_area)

            if listing:
                listings.append(listing)

    return listings


def _title_from_listing_url(listing_url: Any, source_area: str) -> str:
    """Build a readable fallback title from a SPEEDHOME /details/ slug.

    This is only a fallback for cards whose visible title is not available in
    the captured DOM text. It is much safer than reusing a title from a parent
    container that may contain many cards.
    """
    try:
        slug = urlparse(str(listing_url or "")).path.rstrip("/").split("/")[-1]
    except Exception:
        slug = ""

    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", slug).strip("-")

    if not slug:
        return f"Rental listing in {source_area}"

    parts = [part for part in slug.split("-") if part]

    # SPEEDHOME detail slugs usually end with a short random id.
    # Remove that token so the fallback title remains human readable.
    if len(parts) >= 2 and re.fullmatch(r"[a-z0-9]{6,12}", parts[-1], flags=re.IGNORECASE):
        parts = parts[:-1]

    if not parts:
        return f"Rental listing in {source_area}"

    return " ".join(parts).title()[:160]


def _extract_title_from_card(card, anchor, card_text: str, source_area: str, listing_url: Any = None):
    heading = card.find(["h1", "h2", "h3", "h4"]) if card else None

    if heading:
        title = _clean_text(heading.get_text(" ", strip=True))

        if title and len(title) > 4 and not _looks_like_non_listing_text(title):
            return title[:160]

    anchor_text = _clean_text(anchor.get_text(" ", strip=True)) if anchor else ""

    # Anchor text can include the full card. Pick the first sensible title-like
    # fragment before metrics/prices, instead of returning the whole text blob.
    if anchor_text:
        candidate_lines = [
            _clean_text(line)
            for line in re.split(r"\s{2,}|\|", anchor_text)
            if _clean_text(line)
        ]

        for line in candidate_lines:
            line_lower = line.lower()

            if len(line) < 5 or len(line) > 160:
                continue

            if any(token in line_lower for token in ["rm ", "sqft", "verified", "zero deposit", "chat with owner", "move-in", "video call"]):
                continue

            if not _looks_like_non_listing_text(line):
                return line[:160]

    lines = [
        _clean_text(line)
        for line in re.split(r"\s{2,}|\|", card_text)
        if _clean_text(line)
    ]

    for line in lines:
        line_lower = line.lower()

        if len(line) < 5 or len(line) > 160:
            continue

        if any(token in line_lower for token in ["rm ", "sqft", "verified", "zero deposit", "chat with owner", "move-in", "video call", "login", "signup", "whatsapp"]):
            continue

        if not _looks_like_non_listing_text(line):
            return line[:160]

    return _title_from_listing_url(listing_url, source_area)


def _score_card_text(text: str) -> int:
    score = 0

    if _parse_price_rm(text):
        score += 5

    if _parse_size_sqft(text):
        score += 3

    if _parse_bedroom_count(text) is not None:
        score += 2

    if _parse_bathroom_count(text) is not None:
        score += 1

    if _parse_furnishing(text):
        score += 1

    length = len(text)

    if 80 <= length <= 2500:
        score += 3
    elif 2500 < length <= 6000:
        score += 1

    return score


def _detail_links_inside(element, source_url: str) -> set:
    links = set()

    if element is None:
        return links

    try:
        if getattr(element, "name", None) == "a" and element.get("href"):
            url = _normalize_listing_url(element.get("href"), source_url=source_url)
            if _is_direct_speedhome_listing_url(url):
                links.add(url)
    except Exception:
        pass

    try:
        anchors = element.find_all("a", href=True)
    except Exception:
        anchors = []

    for child_anchor in anchors:
        url = _normalize_listing_url(child_anchor.get("href"), source_url=source_url)
        if _is_direct_speedhome_listing_url(url):
            links.add(url)

    return links


def _find_best_card_for_anchor(anchor, source_url: str):
    """Find the smallest useful card container for one listing anchor.

    The previous implementation climbed too far up the DOM and sometimes chose
    the whole result grid. That made every listing reuse the first card's title,
    price, sqft, and furnishing. This version refuses ancestors that contain
    multiple distinct /details/ links.
    """
    best_card = anchor
    best_score = -1
    current = anchor

    for _ in range(10):
        if current is None:
            break

        detail_links = _detail_links_inside(current, source_url)

        # Once an ancestor contains multiple listing detail URLs, it is a grid/list
        # container, not an individual card. Stop before contaminating this row
        # with neighbouring listings.
        if len(detail_links) > 1:
            break

        text = _clean_text(current.get_text(" ", strip=True))

        if text:
            score = _score_card_text(text)

            # Prefer compact card containers. Very large text blobs are likely
            # page sections even if they still contain only one link.
            if len(text) > 2600:
                score -= 3

            if score > best_score:
                best_card = current
                best_score = score

        current = current.parent

    return best_card


def _extract_candidate_anchors(soup: BeautifulSoup, source_url: str):
    anchors = []

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")

        if _is_candidate_listing_href(href, source_url):
            anchors.append(anchor)

    return anchors





def _is_missing_listing_value(value: Any) -> bool:
    if value is None:
        return True

    try:
        if isinstance(value, float) and value != value:
            return True
    except Exception:
        pass

    text = str(value).strip().lower()

    return text in [
        "",
        "none",
        "nan",
        "unknown",
        "not specified",
        "not detected",
        "not detected on result card",
        "not detected on public page",
    ]


def _listing_needs_detail_enrichment(listing: Dict) -> bool:
    """Return True when a valid card is missing fields that are often present on detail pages."""
    if not _is_direct_speedhome_listing_url(listing.get("listing_url")):
        return False

    if _is_missing_listing_value(listing.get("furnishing")):
        return True

    if _is_missing_listing_value(listing.get("bedrooms")) and _is_missing_listing_value(listing.get("room_type_label")):
        return True

    if _is_missing_listing_value(listing.get("bathrooms")):
        return True

    if _is_missing_listing_value(listing.get("carparks")):
        return True

    if _is_missing_listing_value(listing.get("size_sqft")):
        return True

    return False


def _extract_title_from_detail_soup(soup: BeautifulSoup):
    for selector in ["h1", "h2"]:
        node = soup.find(selector)
        if node:
            text = _clean_text(node.get_text(" ", strip=True))
            if text and len(text) <= 180 and not _looks_like_non_listing_text(text):
                return text
    return None


def _parse_detail_listing_fields(html: str) -> Dict[str, Any]:
    """Extract missing fields from a SPEEDHOME /details/ page.

    This is used only as optional enrichment. Result-card data remains the main
    source of truth for whether a listing belongs to the selected public result page.
    """
    if not html:
        return {}

    try:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
    except Exception:
        soup = None
        text = html

    text = _clean_text(text)

    fields: Dict[str, Any] = {}

    official_metrics = _parse_detail_icon_metrics(text)
    if official_metrics:
        fields.update(official_metrics)

    title = _extract_title_from_detail_soup(soup) if soup is not None else None
    if title:
        fields["title"] = title

    monthly_price = _parse_price_rm(text)
    if monthly_price:
        fields["monthly_price_rm"] = monthly_price

    daily_price = _parse_daily_price_rm(text)
    if daily_price:
        fields["daily_price_rm"] = daily_price

    yearly_price = _parse_explicit_yearly_price_rm(text)
    if yearly_price:
        fields["explicit_yearly_price_rm"] = yearly_price
        fields["yearly_price_rm"] = yearly_price

    size_sqft = _parse_size_sqft(text)
    if size_sqft and "size_sqft" not in fields:
        fields["size_sqft"] = size_sqft

    bedrooms = _parse_bedroom_count(text)
    if bedrooms is not None and "bedrooms" not in fields:
        fields["bedrooms"] = bedrooms
        fields["bedroom_count"] = bedrooms

    bathrooms = _parse_bathroom_count(text)
    if bathrooms is not None and "bathrooms" not in fields:
        fields["bathrooms"] = bathrooms
        fields["bathroom_count"] = bathrooms

    bathroom_type_label = _parse_bathroom_type_label(text)
    if bathroom_type_label:
        fields["bathroom_type_label"] = bathroom_type_label

    carparks = _parse_carpark_count(text)
    if carparks is not None and "carparks" not in fields:
        fields["carparks"] = carparks
        fields["carpark_count"] = carparks

    official_furnishing = _parse_detail_official_furnishing(text)
    fallback_furnishing = _parse_furnishing(text)
    furnishing = official_furnishing or fallback_furnishing

    if furnishing:
        fields["furnishing"] = furnishing
        fields["furnishing_source"] = (
            "detail_official_amenities_heading"
            if official_furnishing
            else "detail_page_text_fallback"
        )

    room_type_label = _parse_room_unit_type(text)
    if room_type_label:
        fields["room_type_label"] = room_type_label

    fields["detail_raw_text"] = text[:1500]

    return fields


def _merge_detail_fields_into_listing(listing: Dict, detail_fields: Dict[str, Any]) -> Tuple[Dict, bool]:
    """Merge detail-page fields without overwriting good result-card values."""
    if not detail_fields:
        return listing, False

    changed = False
    enriched = dict(listing)

    merge_if_missing = [
        "title",
        "monthly_price_rm",
        "daily_price_rm",
        "explicit_yearly_price_rm",
        "yearly_price_rm",
        "size_sqft",
        "bedrooms",
        "bedroom_count",
        "bathrooms",
        "bathroom_count",
        "bathroom_type_label",
        "carparks",
        "carpark_count",
        "furnishing",
        "room_type_label",
    ]

    for field in merge_if_missing:
        new_value = detail_fields.get(field)

        if _is_missing_listing_value(new_value):
            continue

        # The official detail-page top metric row is more reliable than numbers
        # accidentally parsed from unit names such as "Y-13-08". Allow it to
        # correct bedroom/bathroom/carpark/size values.
        if (
            detail_fields.get("detail_metrics_source") == "detail_top_icon_metrics"
            and field in [
                "size_sqft",
                "bedrooms",
                "bedroom_count",
                "bathrooms",
                "bathroom_count",
                "carparks",
                "carpark_count",
            ]
            and enriched.get(field) != new_value
        ):
            enriched[field] = new_value
            enriched["detail_metrics_source"] = detail_fields.get("detail_metrics_source")
            changed = True
            continue

        # The official detail-page Amenities heading is more reliable than
        # marketing copy and more reliable than a missing/ambiguous result-card
        # furnishing value. Allow it to correct existing furnishing values.
        if (
            field == "furnishing"
            and detail_fields.get("furnishing_source") == "detail_official_amenities_heading"
            and enriched.get(field) != new_value
        ):
            enriched[field] = new_value
            enriched["furnishing_source"] = detail_fields.get("furnishing_source")
            changed = True
            continue

        if _is_missing_listing_value(enriched.get(field)):
            enriched[field] = new_value
            if field == "furnishing" and detail_fields.get("furnishing_source"):
                enriched["furnishing_source"] = detail_fields.get("furnishing_source")
            changed = True

    if detail_fields.get("detail_raw_text"):
        previous_raw = str(enriched.get("raw_text") or "")
        detail_raw = str(detail_fields.get("detail_raw_text") or "")
        if detail_raw and detail_raw not in previous_raw:
            enriched["raw_text"] = _clean_text(f"{previous_raw} {detail_raw}")[:1500]
            changed = True

    bedrooms = enriched.get("bedrooms")
    raw_context = enriched.get("raw_text", "")

    room_type_label = _parse_room_unit_type(raw_context)
    bathroom_type_label = _parse_bathroom_type_label(raw_context)

    if room_type_label:
        enriched["room_type_label"] = room_type_label

    if bathroom_type_label:
        enriched["bathroom_type_label"] = bathroom_type_label

    enriched["bedroom_type"] = _bedroom_type_from_count(bedrooms, raw_context)
    enriched["detected_rental_type"] = "Monthly" if enriched.get("monthly_price_rm") else enriched.get("detected_rental_type", "Unknown")
    enriched["rental_type_monthly_available"] = bool(enriched.get("monthly_price_rm"))
    enriched["rental_type_yearly_available"] = bool(enriched.get("explicit_yearly_price_rm"))
    enriched["rental_type_daily_available"] = bool(enriched.get("daily_price_rm"))

    if enriched.get("monthly_price_rm"):
        enriched["estimated_yearly_price_rm"] = enriched.get("monthly_price_rm") * 12

    if changed:
        enriched["detail_enriched"] = True
        enriched["detail_enriched_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        enriched["detail_enriched"] = bool(enriched.get("detail_enriched", False))

    return enriched, changed


def _enrich_listings_from_detail_pages(listings: List[Dict], metadata: Dict, enabled: bool = False) -> Tuple[List[Dict], Dict]:
    """Optionally open detail pages to fill missing furnishing/unit attributes.

    This is intentionally optional because it can substantially increase scrape
    time. It only enriches already accepted /details/ listing cards, so it does
    not expand the dataset or reintroduce SEO/FAQ/schema noise.
    """
    metadata = metadata or {}
    metadata["detail_enrichment_enabled"] = bool(enabled)

    if not enabled or not listings:
        metadata["detail_enrichment_attempted"] = 0
        metadata["detail_enrichment_successful"] = 0
        metadata["detail_enrichment_duration_seconds"] = 0
        metadata["detail_enrichment_duration_label"] = _format_duration(0)
        return listings, metadata

    candidates = [
        listing for listing in listings
        if _listing_needs_detail_enrichment(listing)
    ][:DETAIL_ENRICH_MAX_LISTINGS]

    metadata["detail_enrichment_attempted"] = len(candidates)

    if not candidates:
        metadata["detail_enrichment_successful"] = 0
        metadata["detail_enrichment_duration_seconds"] = 0
        metadata["detail_enrichment_duration_label"] = _format_duration(0)
        metadata.setdefault("notes", []).append("Detail enrichment enabled, but no listing required detail-page enrichment.")
        return listings, metadata

    start_perf = time.perf_counter()
    enriched_by_url: Dict[str, Dict] = {}
    successful = 0
    failed = 0

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        metadata["detail_enrichment_successful"] = 0
        metadata["detail_enrichment_failed"] = len(candidates)
        metadata.setdefault("notes", []).append(f"Detail enrichment skipped because Playwright is unavailable: {exc}")
        return listings, metadata

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 1100},
                locale="en-US",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": BASE_URL,
                },
            )

            page = context.new_page()

            for listing in candidates:
                listing_url = listing.get("listing_url")
                if not _is_direct_speedhome_listing_url(listing_url):
                    continue

                try:
                    time.sleep(DETAIL_ENRICH_DELAY_SECONDS)
                    response = page.goto(str(listing_url), wait_until="domcontentloaded", timeout=60000)
                    if response and response.status >= 400:
                        failed += 1
                        continue

                    try:
                        page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception:
                        pass

                    page.wait_for_timeout(800)
                    detail_html = page.content()
                    detail_fields = _parse_detail_listing_fields(detail_html)
                    enriched_listing, changed = _merge_detail_fields_into_listing(listing, detail_fields)

                    if changed:
                        successful += 1
                        enriched_by_url[str(listing_url)] = enriched_listing

                except Exception:
                    failed += 1
                    continue

            context.close()
            browser.close()

    except Exception as exc:
        metadata.setdefault("notes", []).append(f"Detail enrichment encountered an error: {exc}")

    output = []
    for listing in listings:
        listing_url = str(listing.get("listing_url") or "")
        output.append(enriched_by_url.get(listing_url, listing))

    elapsed = max(0.0, time.perf_counter() - start_perf)
    metadata["detail_enrichment_successful"] = successful
    metadata["detail_enrichment_failed"] = failed
    metadata["detail_enrichment_duration_seconds"] = round(elapsed, 2)
    metadata["detail_enrichment_duration_label"] = _format_duration(elapsed)
    metadata["detail_enrichment_max_listings"] = DETAIL_ENRICH_MAX_LISTINGS

    metadata.setdefault("notes", []).append(
        "Detail enrichment opened accepted listing detail pages only for cards with missing furnishing/unit fields. "
        f"Attempted {len(candidates)} detail page(s), enriched {successful}, failed {failed}, duration {_format_duration(elapsed)}."
    )

    return output, metadata


def _parse_dom_listings(soup: BeautifulSoup, source_url: str, source_area: str) -> List[Dict]:
    listings = []
    seen_identity = set()

    for anchor in _extract_candidate_anchors(soup, source_url):
        href = anchor.get("href", "")
        listing_url = _normalize_listing_url(href, source_url=source_url)

        if not _is_direct_speedhome_listing_url(listing_url):
            continue

        card = _find_best_card_for_anchor(anchor, source_url)
        card_text = _clean_text(card.get_text(" ", strip=True))

        monthly_price = _parse_price_rm(card_text)
        daily_price = _parse_daily_price_rm(card_text)
        explicit_yearly_price = _parse_explicit_yearly_price_rm(card_text)
        size_sqft = _parse_size_sqft(card_text)
        bedrooms = _parse_bedroom_count(card_text)
        bathrooms = _parse_bathroom_count(card_text)
        carparks = _parse_carpark_count(card_text)
        furnishing = _parse_furnishing(card_text)
        title = _extract_title_from_card(card, anchor, card_text, source_area, listing_url)

        listing = {
            "title": title,
            "property_area": source_area,
            "source_area": source_area,
            "bedrooms": bedrooms,
            "bedroom_count": bedrooms,
            "bedroom_type": _bedroom_type_from_count(bedrooms, card_text),
            "room_type_label": _parse_room_unit_type(card_text),
            "bathrooms": bathrooms,
            "bathroom_count": bathrooms,
            "bathroom_type_label": _parse_bathroom_type_label(card_text),
            "carparks": carparks,
            "carpark_count": carparks,
            "monthly_price_rm": monthly_price,
            "yearly_price_rm": explicit_yearly_price,
            "explicit_yearly_price_rm": explicit_yearly_price,
            "estimated_yearly_price_rm": monthly_price * 12 if monthly_price else None,
            "daily_price_rm": daily_price,
            "size_sqft": size_sqft,
            "furnishing": furnishing,
            "detected_rental_type": "Monthly" if monthly_price else "Unknown",
            "listing_url": listing_url,
            "source_url": source_url,
            "rental_type_monthly_available": bool(monthly_price),
            "rental_type_yearly_available": bool(explicit_yearly_price),
            "rental_type_daily_available": bool(daily_price),
            "raw_text": card_text[:1500],
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        }

        if not _is_valid_listing_candidate(listing):
            continue

        identity = str(listing_url)

        if identity in seen_identity:
            continue

        seen_identity.add(identity)
        listings.append(listing)

    return listings


def _listing_identity(listing: Dict) -> str:
    listing_url = listing.get("listing_url")

    if listing_url:
        return str(listing_url)

    return "|".join(
        [
            str(listing.get("title") or ""),
            str(listing.get("monthly_price_rm") or ""),
            str(listing.get("size_sqft") or ""),
            str(listing.get("bedrooms") or ""),
            str(listing.get("bathrooms") or ""),
        ]
    )

def _listing_quality_score(listing: Dict) -> int:
    fields = [
        "listing_url",
        "title",
        "monthly_price_rm",
        "size_sqft",
        "bedrooms",
        "room_type_label",
        "bathrooms",
        "carparks",
        "furnishing",
        "raw_text",
    ]

    score = 0

    for field in fields:
        value = listing.get(field)

        if value not in [None, "", "Unknown"]:
            score += 1

    if listing.get("listing_url") and listing.get("listing_url") != listing.get("source_url"):
        score += 3

    return score



def _dedupe_listings(listings: List[Dict]) -> List[Dict]:
    best_by_identity: Dict[str, Dict] = {}

    for listing in listings:
        if not _is_valid_listing_candidate(listing):
            continue

        identity = _listing_identity(listing)

        if not identity.strip("|"):
            continue

        existing = best_by_identity.get(identity)

        if existing is None:
            best_by_identity[identity] = listing
            continue

        if _listing_quality_score(listing) > _listing_quality_score(existing):
            best_by_identity[identity] = listing

    return list(best_by_identity.values())

def _parse_speedhome_listings_with_metadata(
    html: str,
    source_url: str,
    captured_json_payload_texts: Optional[List[str]] = None,
) -> Tuple[List[Dict], Dict]:
    soup = BeautifulSoup(html, "lxml")
    source_area = get_source_area_from_url(source_url)
    source_reported_total_count = _extract_reported_result_count_from_text(
        soup.get_text(" ", strip=True)
    )

    candidate_anchors = _extract_candidate_anchors(soup, source_url)

    # Source of truth for /rent/<area> pages: the visible listing cards and their
    # direct /details/ links on that exact search-result page. This matches what
    # the evaluator can manually verify by opening the same SPEEDHOME URL.
    dom_listings = _parse_dom_listings(soup, source_url, source_area)
    combined = _dedupe_listings(dom_listings)

    # JSON payloads are intentionally not merged into the result. SPEEDHOME pages
    # contain SEO/FAQ/schema/recommendation JSON that can mention prices and
    # locations unrelated to the visible result cards. Using DOM cards prevents
    # malformed JSON-LD rows and title/link/price mismatches.
    html_json_payload_count = len(_extract_json_payloads_from_html(soup))
    captured_json_payload_count = len(captured_json_payload_texts or [])

    rendered_direct_listing_count = len(combined)
    raw_reported_ratio = (
        round((rendered_direct_listing_count / source_reported_total_count) * 100, 2)
        if source_reported_total_count else None
    )

    if not source_reported_total_count:
        reported_vs_rendered_status = "reported_total_unavailable"
        scrape_coverage_ratio = None
        coverage_note = "SPEEDHOME did not expose a clear reported total on this page."
    elif rendered_direct_listing_count < source_reported_total_count:
        reported_vs_rendered_status = "partial_rendered_sample"
        scrape_coverage_ratio = raw_reported_ratio
        coverage_note = (
            "Collected cards are fewer than the SPEEDHOME-reported target count; "
            "treat analysis as an observed rendered-page sample."
        )
    elif rendered_direct_listing_count == source_reported_total_count:
        reported_vs_rendered_status = "matches_reported_total"
        scrape_coverage_ratio = 100.0
        coverage_note = "Collected cards match the SPEEDHOME-reported target count."
    else:
        reported_vs_rendered_status = "rendered_cards_exceed_reported_total"
        scrape_coverage_ratio = None
        coverage_note = (
            "Rendered direct listing cards exceed the SPEEDHOME-reported target count. "
            "SPEEDHOME may render nearby or broader source-page cards in addition to the target-count headline."
        )

    metadata = {
        "parser_candidate_anchor_count": len(candidate_anchors),
        "parser_dom_listing_count": len(dom_listings),
        "parser_html_json_payload_count": html_json_payload_count,
        "parser_captured_json_payload_count": captured_json_payload_count,
        "parser_json_listing_count": 0,
        "parser_parsed_listing_count": rendered_direct_listing_count,
        "parser_source_of_truth": "visible_dom_listing_cards_only",
        "source_reported_total_count": source_reported_total_count,
        "rendered_direct_listing_count": rendered_direct_listing_count,
        "reported_vs_rendered_status": reported_vs_rendered_status,
        "reported_to_rendered_ratio_raw": raw_reported_ratio,
        "scrape_coverage_ratio": scrape_coverage_ratio,
        "scrape_coverage_note": coverage_note,
        "rendered_extra_card_count": (
            max(0, rendered_direct_listing_count - source_reported_total_count)
            if source_reported_total_count else None
        ),
    }

    return combined, metadata


def parse_speedhome_listings(html: str, source_url: str) -> List[Dict]:
    listings, _metadata = _parse_speedhome_listings_with_metadata(html, source_url)
    return listings


def scrape_speedhome_with_metadata(user_input: str, use_cache: bool = True, crawl_mode: str = "quick", enrich_missing_details: bool = False) -> Tuple[List[Dict], Dict]:
    scrape_started_at = datetime.now()
    scrape_started_perf = time.perf_counter()

    def _apply_live_scrape_timing(target_metadata: Dict) -> Dict:
        elapsed_seconds = max(0.0, time.perf_counter() - scrape_started_perf)
        target_metadata["scrape_started_at"] = scrape_started_at.isoformat(timespec="seconds")
        target_metadata["scrape_finished_at"] = datetime.now().isoformat(timespec="seconds")
        target_metadata["scrape_duration_seconds"] = round(elapsed_seconds, 2)
        target_metadata["scrape_duration_label"] = _format_duration(elapsed_seconds)
        target_metadata["current_run_duration_seconds"] = round(elapsed_seconds, 2)
        target_metadata["current_run_duration_label"] = _format_duration(elapsed_seconds)
        return target_metadata

    url = normalize_input_to_url(user_input)
    # The assessment scope is SPEEDHOME public rent pages. Search text is
    # normalized into /rent/<slug>, while direct URLs are validated by shape,
    # not by the autocomplete suggestion list.
    url = validate_direct_speedhome_rent_url(url)
    source_area = get_source_area_from_url(url)

    crawl_mode = str(crawl_mode or "quick").strip().lower()
    if crawl_mode not in ["quick", "deeper", "full", "extended"]:
        crawl_mode = "quick"

    metadata = {
        "input": user_input,
        "normalized_url": url,
        "source_area": source_area,
        "crawl_mode": crawl_mode,
        "detail_enrichment_requested": bool(enrich_missing_details),
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "scrape_started_at": scrape_started_at.isoformat(timespec="seconds"),
        "cache_used": False,
        "fetch_method": None,
        "html_length": 0,
        "robots_allowed": None,
        "robots_url": None,
        "robots_policy_source": None,
        "notes": [],
    }

    metadata["custom_url_outside_suggestions"] = is_direct_speedhome_rent_url_outside_suggestions(url)

    likely_typo_base = detect_likely_concatenated_suggestion_typo(url)
    if likely_typo_base:
        metadata["likely_typo_base_area"] = likely_typo_base
        metadata["notes"].append(
            f"Custom URL looks like a typo of '{likely_typo_base}'. No live scrape was started for this target."
        )
        metadata = _apply_live_scrape_timing(metadata)
        raise SpeedhomeFetchError(
            "This SPEEDHOME rent URL looks like a typo of a known area. "
            f"Please check the slug or choose '{likely_typo_base}' from the suggestions.",
            metadata=metadata,
        )

    cache = _load_cache()
    cache_key = f"{url}::crawl={crawl_mode}::detail_enrich={bool(enrich_missing_details)}"

    if use_cache and cache_key in cache:
        cached_entry = cache[cache_key]
        cached_listings = cached_entry.get("listings", [])

        if cached_listings:
            cached_metadata = cached_entry.get("metadata", {}).copy()
            cached_suspected, cached_guard_metadata = _custom_url_broad_fallback_guard(url, cached_listings, cached_metadata)

            if cached_suspected:
                metadata.update(cached_guard_metadata)
                metadata["notes"].append(
                    "Ignored cached custom URL result because it looks like a broad fallback dataset for an unlisted or invalid rent slug."
                )
            else:
                lookup_seconds = max(0.0, time.perf_counter() - scrape_started_perf)
                cached_metadata.update(cached_guard_metadata)
                cached_metadata["cache_used"] = True
                cached_metadata["fetch_method"] = cached_metadata.get("fetch_method", "cache")
                cached_metadata["cache_retrieved_at"] = datetime.now().isoformat(timespec="seconds")
                cached_metadata["cache_lookup_duration_seconds"] = round(lookup_seconds, 2)
                cached_metadata["cache_lookup_duration_label"] = _format_duration(lookup_seconds)
                cached_metadata["current_run_duration_seconds"] = round(lookup_seconds, 2)
                cached_metadata["current_run_duration_label"] = _format_duration(lookup_seconds)

                # Older cache files may not have duration labels yet. Rebuild the label
                # from stored seconds when possible so diagnostics remain readable.
                if not cached_metadata.get("scrape_duration_label") and cached_metadata.get("scrape_duration_seconds") is not None:
                    cached_metadata["scrape_duration_label"] = _format_duration(cached_metadata.get("scrape_duration_seconds"))

                return cached_listings, cached_metadata

        metadata["notes"].append("Existing empty cache entry ignored; refetching page.")

    allowed, robots_url, robots_policy_source = _robots_allowed(url)

    metadata["robots_allowed"] = allowed
    metadata["robots_url"] = robots_url
    metadata["robots_policy_source"] = robots_policy_source

    if robots_policy_source == "assessment_allowed_rent_path":
        metadata["notes"].append("Rent path allowed for this assessment scope.")

    if not allowed:
        metadata["notes"].append("Scraping blocked by robots.txt.")
        return [], _apply_live_scrape_timing(metadata)

    html = ""
    captured_json_payloads: List[str] = []

    try:
        html, request_metadata = _fetch_with_requests(url)
        metadata["fetch_method"] = "requests"
        metadata.update(request_metadata)

        final_url = metadata.get("requests_final_url") or url
        if not _same_rent_target(url, final_url):
            metadata["notes"].append(
                f"Request redirected away from the requested rent target: {final_url}"
            )
            metadata = _apply_live_scrape_timing(metadata)
            raise SpeedhomeFetchError(
                "The SPEEDHOME page redirected away from the requested rent URL. "
                "No previous or unrelated dataset was reused.",
                metadata=metadata,
            )

    except SpeedhomeFetchError:
        raise

    except Exception as exc:
        metadata["notes"].append(f"Requests failed: {exc}")

        try:
            html, playwright_metadata = _fetch_with_playwright(url, crawl_mode=crawl_mode)
            metadata["fetch_method"] = "playwright"

            captured_json_payloads = playwright_metadata.pop("_captured_json_payloads", [])
            metadata.update(playwright_metadata)

            final_url = metadata.get("playwright_final_url") or metadata.get("playwright_response_url") or url
            if not _same_rent_target(url, final_url):
                metadata["notes"].append(
                    f"Browser rendering redirected away from the requested rent target: {final_url}"
                )
                metadata = _apply_live_scrape_timing(metadata)
                raise SpeedhomeFetchError(
                    "The SPEEDHOME page redirected away from the requested rent URL. "
                    "No previous or unrelated dataset was reused.",
                    metadata=metadata,
                )

        except SpeedhomeFetchError:
            raise

        except Exception as playwright_exc:
            metadata["notes"].append(f"Playwright failed: {playwright_exc}")

            metadata = _apply_live_scrape_timing(metadata)
            raise SpeedhomeFetchError(
                "Failed to fetch SPEEDHOME page using both requests and Playwright.",
                metadata=metadata,
            ) from playwright_exc

    metadata["html_length"] = len(html)

    listings, parser_metadata = _parse_speedhome_listings_with_metadata(
        html=html,
        source_url=url,
        captured_json_payload_texts=captured_json_payloads,
    )

    metadata.update(parser_metadata)

    suspected_broad_fallback, guard_metadata = _custom_url_broad_fallback_guard(url, listings, metadata)
    metadata.update(guard_metadata)

    if suspected_broad_fallback:
        metadata["notes"].append(
            "Custom rent URL broad-fallback guard triggered: SPEEDHOME reported a very small target count, "
            "but rendered a much larger set of direct listing cards. No unrelated fallback dataset was accepted or cached."
        )
        metadata = _apply_live_scrape_timing(metadata)
        raise SpeedhomeFetchError(
            "This custom SPEEDHOME rent URL appears to render broader fallback listings instead of a reliable target dataset. "
            "Please verify the URL slug or choose a suggested area/apartment. No unrelated listing dataset was reused.",
            metadata=metadata,
        )

    listings, metadata = _enrich_listings_from_detail_pages(
        listings=listings,
        metadata=metadata,
        enabled=bool(enrich_missing_details),
    )

    metadata["raw_listing_count"] = len(listings)

    metadata["notes"].append(
        "Parser uses strict direct-listing validation: only /details/ URLs are accepted. "
        "Parser diagnostics: "
        f"candidate anchors={metadata.get('parser_candidate_anchor_count', 0)}, "
        f"DOM listings={metadata.get('parser_dom_listing_count', 0)}, "
        f"HTML JSON payloads={metadata.get('parser_html_json_payload_count', 0)}, "
        f"captured JSON payloads={metadata.get('parser_captured_json_payload_count', 0)}, "
        f"JSON listings={metadata.get('parser_json_listing_count', 0)}, "
        f"final listings={metadata.get('parser_parsed_listing_count', 0)}."
    )

    reported_total = metadata.get("source_reported_total_count")
    if reported_total:
        status = metadata.get("reported_vs_rendered_status")
        coverage = metadata.get("scrape_coverage_ratio")
        raw_ratio = metadata.get("reported_to_rendered_ratio_raw")
        if status == "rendered_cards_exceed_reported_total":
            metadata["notes"].append(
                f"SPEEDHOME page reports {reported_total} target result(s), while this scrape collected "
                f"{len(listings)} valid direct listing card(s) from the rendered public page(s). "
                "This is reported as a rendered-vs-reported mismatch, not as >100% coverage."
            )
        elif coverage is not None:
            metadata["notes"].append(
                f"SPEEDHOME page reports {reported_total} total result(s); "
                f"this scrape collected {len(listings)} valid direct listing card(s) "
                f"from the rendered public page(s) ({coverage}% coverage)."
            )
        else:
            metadata["notes"].append(
                f"SPEEDHOME page reports {reported_total} total result(s); "
                f"this scrape collected {len(listings)} valid direct listing card(s). "
                f"Raw rendered/reported ratio: {raw_ratio}%."
            )

    if metadata.get("crawl_mode") in ["deeper", "full", "extended"]:
        metadata["notes"].append(
            "Deeper crawl follows public pagination and also tries numeric /rent/<area>?page=N pages "
            f"when rel=next stops. Pages fetched: {metadata.get('playwright_paginated_pages_fetched', 1)}; "
            f"stop reason: {metadata.get('playwright_pagination_stop_reason', '-')}."
        )

    metadata = _apply_live_scrape_timing(metadata)

    if listings:
        cache[cache_key] = {
            "metadata": metadata,
            "listings": listings,
        }

        _save_cache(cache)
    else:
        metadata["notes"].append("No listings parsed. Empty result was not cached.")

    return listings, metadata


def scrape_speedhome(user_input: str) -> List[Dict]:
    listings, _metadata = scrape_speedhome_with_metadata(user_input)
    return listings