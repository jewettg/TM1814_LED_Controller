"""Turn an address, town or ZIP code into coordinates, so sunrise and sunset can be worked out.

Uses OpenStreetMap's free Nominatim service. Only the text you type is sent (once per lookup, by this program, not by
your browser); only the resulting name and coordinates are stored. Its usage rules are followed: an identifying
User-Agent, at most one request per second, and results are cached.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Tuple

from . import __version__

URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"tm1814-led/{__version__} (home LED controller; https://github.com/jewettg/TM1814_LED_Controller)"
MIN_INTERVAL = 1.1          # seconds between requests (the service allows one per second)
CACHE_SECONDS = 24 * 3600


class GeocodeError(ValueError):
    """The address could not be looked up (message is shown to the user)."""


_lock = threading.Lock()
_last_request = 0.0
_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def _fetch(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(query: str, limit: int = 5, fetch: Callable[[str], Any] = _fetch,
           now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> List[Dict[str, Any]]:
    """Up to `limit` matches: [{"name", "latitude", "longitude"}], best first. Raises GeocodeError with a friendly message."""
    global _last_request
    q = " ".join(str(query or "").split())
    if len(q) < 3:
        raise GeocodeError("Type a street address, a town, or a ZIP code (at least 3 characters).")
    if len(q) > 200:
        raise GeocodeError("That is too long for an address.")
    key = q.lower()
    hit = _cache.get(key)
    if hit and now() - hit[0] < CACHE_SECONDS:
        return hit[1]
    url = URL + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": max(1, min(10, limit))})
    with _lock:                                           # one request at a time, spaced out
        wait = MIN_INTERVAL - (now() - _last_request)
        if wait > 0 and _last_request:
            sleep(wait)
        _last_request = now()
        try:
            raw = fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise GeocodeError("The address service is busy right now. Try again in a minute.") from exc
            raise GeocodeError(f"The address service answered with an error ({exc.code}).") from exc
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            raise GeocodeError("Could not reach the address service (is this computer online?). "
                               "Check the internet connection and try again.") from exc
    results: List[Dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            results.append({"name": str(item["display_name"]), "latitude": round(float(item["lat"]), 4),
                            "longitude": round(float(item["lon"]), 4)})
        except (KeyError, TypeError, ValueError):
            continue
    if not results:
        raise GeocodeError("No place was found for that. Try adding the town or state, or just a ZIP code.")
    _cache[key] = (now(), results)
    return results
