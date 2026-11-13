"""What the main routes return, for the API explorer (#147).

The routes build plain dicts. These models describe them for the OpenAPI
schema; they do not filter or check responses at run time, so a field added to
a route is never silently dropped. tests/test_explorer.py checks real
responses against them, so they cannot drift. Every model allows more fields
than it names.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class SearchMatch(Open):
    match: list[str]
    similarity: float | None
    at_ms: int | None = None
    said: str | None = None


class RecipeCard(Open):
    title: str | None
    time_minutes: int | None
    ingredients: int
    incomplete: bool


class Card(Open):
    """A reel as the grid shows it."""

    shortcode: str
    permalink: str
    author: str | None
    author_name: str | None
    saved_at: int | None
    state: str
    caption: str | None
    duration: int | None
    like_count: int | None
    thumb: str | None
    video: str | None
    hashtags: list[str]
    collections: list[str]
    tag: str | None
    copies: int
    recipe: RecipeCard | None
    match: SearchMatch | None


class ItemPage(Open):
    total: int
    offset: int
    limit: int
    items: list[Card]


class Cards(Open):
    items: list[Card]


class Rediscover(Open):
    on_this_day: list[Card]
    never_opened: list[Card]


class Ingredient(Open):
    item: str
    quantity: str | None
    unit: str | None
    note: str | None
    groups: list[str]


class Recipe(Open):
    title: str | None
    servings: int | None
    time_minutes: int | None
    ingredients: list[Ingredient]
    steps: list[str]
    incomplete: bool
    hidden: bool
    edited: bool
    has_model: bool
    web_url: str | None
    link: dict[str, Any] | None = None
    fetchable: bool | None = None


class ItemDetail(Open):
    """Everything about one reel in your library."""

    shortcode: str
    permalink: str
    author: str | None
    caption: str | None
    state: str
    transcript: str | None
    segments: list[list[Any]] | None
    ocr_text: str | None
    summary: str | None
    keywords: list[str]
    saved_at: int | None
    thumb: str | None
    video: str | None
    slides: list[str]
    hashtags: list[str]
    collections: list[str]
    tag: str | None
    tags: list[dict[str, Any]]
    places: list[dict[str, Any]]
    recipe: Recipe | None
    copies: list[dict[str, Any]]
    listed: bool


class Citation(Open):
    n: int
    shortcode: str
    at_ms: int | None


class Answer(Open):
    found: bool
    answer: str
    citations: list[Citation]
    items: list[Card]
    model: str


class Named(Open):
    name: str
    count: int


class Me(Open):
    id: int
    username: str
    ig_handle: str | None
    is_admin: bool
    auth: str
    collapse_copies: bool


class Translation(Open):
    recipe: Recipe | None
    model: str | None


# (method, path) -> what it returns
RESPONSES: dict[tuple[str, str], Any] = {
    ("GET", "/api/items"): ItemPage,
    ("GET", "/api/items/{shortcode}"): ItemDetail,
    ("GET", "/api/items/{shortcode}/similar"): Cards,
    ("GET", "/api/rediscover"): Rediscover,
    ("GET", "/api/ask"): Answer,
    ("GET", "/api/tags"): list[Named],
    ("GET", "/api/collections"): list[Named],
    ("GET", "/api/me"): Me,
    ("PUT", "/api/me"): Me,
    ("GET", "/api/items/{shortcode}/recipe/translation"): Translation,
    ("PUT", "/api/items/{shortcode}/recipe"): Recipe | None,
    ("DELETE", "/api/items/{shortcode}/recipe"): Recipe | None,
    ("POST", "/api/items/{shortcode}/recipe/fetch"): Recipe | None,
}
