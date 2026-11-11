"""Hikidashi: a self-hosted, searchable library for your Instagram saves."""

from __future__ import annotations

import os
import sys

# Hikidashi was called ReelVault before 0.18.0. A setting still under its old
# REELVAULT_ name counts, unless the HIKIDASHI_ one is also set, so an
# install configured before the rename (an .env, an Unraid template, a GPU
# desktop's worker) keeps working. Done here, on first import, so every
# os.environ read in the app sees the new names. Drop it in 0.19.0.
_LEGACY, _CURRENT = "REELVAULT_", "HIKIDASHI_"
_renamed = sorted(
    key
    for key in os.environ
    if key.startswith(_LEGACY) and _CURRENT + key[len(_LEGACY) :] not in os.environ
)
for _key in _renamed:
    os.environ[_CURRENT + _key[len(_LEGACY) :]] = os.environ[_key]
if _renamed:
    print(
        f"hikidashi: read {', '.join(_renamed)} under the old name; "
        f"rename to {_CURRENT}* before 0.19.0",
        file=sys.stderr,
    )
