# 0020. Fetch the rest of an incomplete recipe, on by default

- **Status:** Accepted
- **Date:** 2026-11-13
- **Issue:** #114

## Context

About one recipe in sixteen on a real library is marked incomplete: the
caption says "link in bio", "full recipe on the blog", or "comment BIRRIA".
When the caption links straight to the recipe's page, that page nearly always
carries the whole recipe as schema.org JSON-LD, the structured data recipe
blogs publish for search engines.

Until now nothing left the server unless someone configured it: Instagram for
enrichment, a geocoder, hosted AI. Fetching a page is a new outbound request.
What it sends is small: the server's address, and the URL of one page, which
says that someone on this server saved a post linking to it. Nothing about the
person goes with it, and the page is a public one the author pointed to.

## Decision

**Fetch it, unless `HIKIDASHI_RECIPE_FETCH=off`.**

- Only a URL in the caption with a path (a recipe page, not a home page), and
  only for a recipe still marked incomplete that nobody has corrected.
- Once per reel. A page that fails, or has no recipe, is tried again after a
  week at the earliest (`recipe_fetch`).
- The recipe found is a `web` row that stands in for the model's for everyone,
  until someone corrects theirs. A re-read by the model leaves it alone.
- The server refuses any address that is not on the public internet, before
  every request and every redirect, so a caption cannot point it at a router
  or another container. The check is not pinned to the connection; see the
  `shortcut:` note in `app/recipes/web.py`.
- Link-in-bio, comment-a-keyword and home-page links are shown, not fetched.

## Consequences

- An incomplete recipe with a link completes itself the first time it is
  opened, with no setup.
- A site sees one request from the server per such reel. `off` stops it, and
  the link is still shown to follow by hand.
- Aggregators (Linktree, Beacons), profile bio links and searching a site for
  a post are left for later: they need more requests, to more places.
