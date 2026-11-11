"""Turning a place from a caption into a point on the map. Stdlib only.

Speaks Nominatim's /search API, so it works with a self-hosted Nominatim or,
by opting in, the public instance at its limit of one request a second. Nothing
is looked up unless HIKIDASHI_GEOCODER names a server: captions stay on this
machine by default.

A caption's place is rarely a clean query. "TICKET CHECK-IN LOCATION // 504 E
Main St, Bellville, TX" finds nothing as written, and "Katz's Delicatessen —
205 E Houston St" depends on the server knowing the shop. So each place is
tried as a short series of queries, best first, and the first that finds
anything wins.

How sure a result is comes from how much the candidates agree. "Portland,
Maine" names one place; "Cedar Park" names dozens across the US, and a pin on
the first of them is as likely to be wrong as right. The score is the share of
the candidates' weight that lies near the top one, so a query whose answers
are all in one spot scores 1, and a pin that would be a guess scores low and
stays off the map.

A place that is sure of itself can still be wrong: "Barnett Cave" is a cave
in New Zealand and a farm in Tennessee, and only the second is in OSM. When a
reel's other places agree on a region, `Region` says where, and the worker
looks again inside it for the places that fall outside, or were unsure.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import _SPLIT, _STREET

# Bumping this looks every place up again: a new query strategy or score.
GEO_V = 1

PUBLIC = "https://nominatim.openstreetmap.org"
# Nominatim's usage policy asks for an identifying User-Agent.
USER_AGENT = "Hikidashi (+https://github.com/spanthetree/hikidashi)"

CANDIDATES = 5  # results asked for, to judge how ambiguous a query is
NEAR_KM = 50  # candidates this close to the top one agree with it
# Below this, a pin is more likely wrong than right, and is left off the map.
CONFIDENT = 0.6
# A result this coarse (a country, a continent) is not a place you can go.
COARSEST_RANK = 4

_HANDLE = re.compile(r"\s*@[\w.]+")
# "Ste 130", "Unit A-102": no geocoder indexes the suite, and some give up on it.
_SUITE = re.compile(r",?\s*(?:\b(?:Ste|Suite|Unit|Apt)\b\.?|#)\s*[\w\-]+", re.IGNORECASE)
# "(3 nights)", "(@visitsekiparks)": asides, never part of the place.
_ASIDE = re.compile(r"\s*\([^)]*\)|\s*…\s*$")
_JUNK_LEAD = re.compile(r"^.*?//\s*")


class GeocodeError(Exception):
    """The server could not be asked: unreachable, refused, or bad JSON."""


@dataclass(frozen=True)
class Located:
    lat: float
    lng: float
    label: str  # the server's name for what it found
    kind: str  # "restaurant", "city", "peak": its type
    confidence: float  # 0-1, see the module docstring
    query: str  # the variant that found it

    @property
    def confident(self) -> bool:
        return self.confidence >= CONFIDENT


def queries(text: str) -> list[str]:
    """What to ask for, best first, without repeats."""
    tries: list[str] = []

    def add(q: str) -> None:
        q = " ".join(q.split()).strip(" ,-–—/")
        if len(q) >= 3 and q not in tries:
            tries.append(q)

    # "Mackinac Island, Michigan @puremichigan": the handle credits a tourist
    # board, and confuses every geocoder.
    clean = _ASIDE.sub("", _HANDLE.sub("", text))
    add(clean)
    # A street address alone, without the venue or label wrapped round it,
    # and then without its suite.
    street = _STREET.search(clean)
    if street:
        add(street.group(0))
        add(_SUITE.sub("", street.group(0)))
    # "LOCATION // 504 E Main St": whatever follows the separator.
    add(_JUNK_LEAD.sub("", clean))
    # "Slice of Life BBQ, 4-21-13 Jingumae, Shibuya, Tokyo": drop the venue,
    # but only ahead of an address. "Hot Springs, Arkansas" is not a venue in
    # Arkansas, and the state alone is no place to put a pin.
    rest = clean.split(",", 1)[1] if "," in clean else ""
    if re.search(r"\d", rest):
        add(rest)
    # "Katz's Delicatessen — 205 E Houston St": the venue, should the server
    # know it but not the address. Not "Location: 100 West Laurel Street",
    # where what comes before the colon is a label.
    if street and not street.group(0).startswith(_SPLIT.split(clean, 1)[0]):
        name = _SPLIT.split(clean, 1)[0]
        if not re.fullmatch(r"(?i)location|address|where", name):
            add(name)
    return tries


def _km(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Great-circle distance between two results, in km."""
    la1, lo1, la2, lo2 = map(
        math.radians, (float(a["lat"]), float(a["lon"]), float(b["lat"]), float(b["lon"]))
    )
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1.0, h)))


def km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return _km({"lat": lat1, "lon": lng1}, {"lat": lat2, "lon": lng2})


# A region needs this many sure places to say where a reel is.
REGION_MIN = 2
# A place is outside it beyond this many times the places' usual distance from
# their middle, and never nearer than this.
OUTLIER_FACTOR = 3
OUTLIER_MIN_KM = 1000
# Places further than this from their middle, as a rule, are a list of the
# best of a whole country ("ten US road trips"), not of one region, and say
# nothing about where the rest should be.
REGION_MAX_KM = 700


@dataclass(frozen=True)
class Region:
    """Where most of one reel's places are."""

    lat: float
    lng: float
    reach_km: float  # how far from the middle a place still belongs

    @classmethod
    def of(cls, points: list[tuple[float, float]]) -> Region | None:
        """The region a reel's sure places agree on, or None with too few, or
        too far apart. The middle is the median, so one place on the wrong
        continent does not drag it there."""
        if len(points) < REGION_MIN:
            return None
        lat = sorted(p[0] for p in points)[len(points) // 2]
        lng = sorted(p[1] for p in points)[len(points) // 2]
        usual = sorted(km(lat, lng, *p) for p in points)[len(points) // 2]
        if usual > REGION_MAX_KM:
            return None
        return cls(lat, lng, max(OUTLIER_FACTOR * usual, OUTLIER_MIN_KM))

    def holds(self, lat: float, lng: float) -> bool:
        return km(self.lat, self.lng, lat, lng) <= self.reach_km

    @property
    def box(self) -> str:
        """west,north,east,south: Nominatim's viewbox."""
        dlat = self.reach_km / 111
        dlng = self.reach_km / (111 * max(math.cos(math.radians(self.lat)), 0.05))
        north, south = min(self.lat + dlat, 90), max(self.lat - dlat, -90)
        west, east = max(self.lng - dlng, -180), min(self.lng + dlng, 180)
        return f"{west:.4f},{north:.4f},{east:.4f},{south:.4f}"


def confidence(results: list[dict[str, Any]]) -> float:
    """How much the candidates agree with the first. See the module docstring."""
    if not results:
        return 0.0
    top = results[0]

    def weight(r: dict[str, Any]) -> float:
        return max(float(r.get("importance") or 0), 0.01)

    total = sum(weight(r) for r in results)
    near = sum(weight(r) for r in results if _km(top, r) <= NEAR_KM)
    score = near / total
    if int(top.get("place_rank") or 30) <= COARSEST_RANK:
        score = min(score, 0.2)
    return round(score, 3)


def locate(
    text: str, search: Callable[..., list[dict[str, Any]]], region: Region | None = None
) -> Located | None:
    """The best point for one caption place, or None when nothing matches.
    Within `region` only, when one is given."""
    for q in queries(text):
        results = search(q, box=region.box) if region else search(q)
        if region:
            results = [r for r in results if region.holds(float(r["lat"]), float(r["lon"]))]
        if results:
            top = results[0]
            return Located(
                lat=float(top["lat"]),
                lng=float(top["lon"]),
                label=top.get("display_name") or q,
                kind=top.get("type") or top.get("addresstype") or "",
                confidence=confidence(results),
                query=q,
            )
    return None


class Nominatim:
    """A Nominatim server, asked no more often than `delay` seconds apart."""

    def __init__(
        self,
        url: str,
        *,
        delay: float = 1.0,
        timeout: float = 20,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ):
        self.url = url.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self._open = opener
        self._lock = threading.Lock()
        self._last = float("-inf")

    def search(self, q: str, box: str | None = None) -> list[dict[str, Any]]:
        """Up to CANDIDATES answers, best first. Inside `box` only, if given."""
        fields: dict[str, object] = {"q": q, "format": "jsonv2", "limit": CANDIDATES}
        if box:
            fields.update(viewbox=box, bounded=1)
        params = urllib.parse.urlencode(fields)
        req = urllib.request.Request(
            f"{self.url}/search?{params}",
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        )
        with self._lock:
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                with self._open(req, timeout=self.timeout) as res:
                    body = res.read()
            except (urllib.error.URLError, OSError) as exc:
                raise GeocodeError(f"{self.url}: {exc}") from exc
            finally:
                self._last = time.monotonic()
        try:
            found = json.loads(body)
        except ValueError as exc:
            raise GeocodeError(f"{self.url} did not answer in JSON") from exc
        if not isinstance(found, list):
            raise GeocodeError(f"{self.url}: {str(found)[:120]}")
        return found
