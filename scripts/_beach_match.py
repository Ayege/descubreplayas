# Copyright 2026 Ayesha Yege
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Decide from a file's Commons categories whether it is a photo of a beach.

Pure logic, no network. A file counts as beach-tagged when a category has a whole beach
word, or names the beach in full. Titles can't be used: a photo of a beach is often
called just "panoramio.jpg". This narrows the candidates; a person still reviews them.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import NamedTuple

# Whole words, accent- and case-folded. "Praia" is left out: it's Portuguese and adds
# nothing for this country.
BEACH_WORDS = frozenset({"beach", "beaches", "playa", "playas"})

# A category starting with one of these words marks a file as mixed: a resort's beach
# can be a good photo, so these are flagged for a closer look, never dropped. Matched on
# the start of a word, so "Hotels" and "Rooms" count and "Liverpool" doesn't.
FLAG_TERMS = ("hotel", "resort", "pool", "restaurant", "room", "market", "diving", "underwater")

TAGGED = "tagged"
MIXED = "mixed"
NOT_TAGGED = "not_tagged"

_TOKEN_RE = re.compile(r"[^\W_]+")
_PARENTHETICAL_RE = re.compile(r"\s*\(([^)]*)\)")


class NameRule(NamedTuple):
    """A beach's full catalogue name as words, and the words a category also needs
    when another beach shares that name (the two Playa Blancas).
    """

    name: tuple[str, ...]
    context: tuple[str, ...]


def words(text: str) -> tuple[str, ...]:
    """Lower-case words with accents removed."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return tuple(_TOKEN_RE.findall(plain.casefold()))


def _contains_run(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    size = len(needle)
    return size > 0 and any(haystack[i:i + size] == needle for i in range(len(haystack) - size + 1))


def beach_name_rules(beaches: Iterable[Mapping]) -> dict[str, NameRule]:
    """One rule per beach from its full name, without any "(Place)" suffix. A name
    several beaches share also needs the province or the suffix in the category.
    Bare words such as "Grande" or "Dorada" are never used on their own.
    """
    entries = []
    for beach in beaches:
        suffix = _PARENTHETICAL_RE.search(beach["name"])
        base = _PARENTHETICAL_RE.sub("", beach["name"]).strip()
        entries.append((beach, words(base), words(suffix.group(1)) if suffix else ()))
    counts: dict[tuple[str, ...], int] = {}
    for _beach, base, _suffix in entries:
        counts[base] = counts.get(base, 0) + 1
    rules: dict[str, NameRule] = {}
    for beach, base, suffix in entries:
        shared = counts[base] > 1
        context = (suffix or words(beach["province"])) if shared else ()
        rules[beach["name"]] = NameRule(base, context)
    return rules


def _is_beach_category(category_words: tuple[str, ...], rule: NameRule | None) -> bool:
    if BEACH_WORDS.intersection(category_words):
        return True
    if rule is None or not _contains_run(category_words, rule.name):
        return False
    return not rule.context or _contains_run(category_words, rule.context)


def _is_flagged(category_words: tuple[str, ...]) -> bool:
    return any(word.startswith(term) for word in category_words for term in FLAG_TERMS)


def classify(categories: Iterable[str], rule: NameRule | None = None) -> str:
    """TAGGED, MIXED (tagged, and a category suggests a hotel, pool, market or the
    like), or NOT_TAGGED.
    """
    folded = [words(category) for category in categories]
    if not any(_is_beach_category(category, rule) for category in folded):
        return NOT_TAGGED
    return MIXED if any(_is_flagged(category) for category in folded) else TAGGED
