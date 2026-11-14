"""Find the `runs-on` label sets in workflow files, with a line scanner.

Not a YAML parser (decision 10: standard library only). It reads the three
forms a `runs-on` usually takes — a scalar, an inline list, a block list — and
calls everything else unreadable: an expression (`${{ … }}`), a runner group
mapping (`group:` / `labels:`), a block scalar, a flow list split over lines.
`join` lists unreadable ones for the operator, whose confirmation is the real
check; only jobs matching a confirmed set are ever served.
"""

from __future__ import annotations

import re

KEY = re.compile(r"^(?P<indent>[ \t]*)runs-on[ \t]*:(?P<rest>.*)$")
# A bare `-` is an item too: an empty one, which makes the list unreadable rather than ending it.
ITEM = re.compile(r"^(?P<indent>[ \t]*)-(?:[ \t]+(?P<value>.*))?$")
# `key: value` or `key:` -- a mapping, such as a runner group, not a label.
MAPPING = re.compile(r":(?:[ \t]|$)")


def _strip_comment(text: str) -> str:
    """Drop a `# comment` that is outside quotes and starts the text or follows whitespace."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _scalar(text: str) -> str | None:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        inner = text[1:-1]
        if text[0] == "'":
            inner = inner.replace("''", "'")
        value = inner
    else:
        if (any(ch in text for ch in "[]{},&*!|>") or text.startswith(("'", '"'))
                or MAPPING.search(text)):
            return None
        value = text
    if not value.strip() or "${{" in value:
        return None
    return value.strip()


def _inline(rest: str) -> tuple[str, ...] | None:
    if not rest.endswith("]"):
        return None
    items = [_scalar(part) for part in rest[1:-1].split(",")]
    if not items or any(item is None for item in items):
        return None
    return tuple(items)


def scan(text: str) -> list[tuple[int, tuple[str, ...] | None]]:
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        key = KEY.match(line)
        if not key:
            continue
        rest = _strip_comment(key.group("rest")).strip()
        if rest.startswith("["):
            labels = _inline(rest)
        elif rest:
            value = _scalar(rest)
            labels = (value,) if value is not None else None
        else:
            labels = _block(lines, i + 1, len(key.group("indent")))
        found.append((i + 1, labels))
    return found


def _block(lines: list[str], start: int, key_indent: int) -> tuple[str, ...] | None:
    """A block list under the key: `- item` lines indented at least as deep as the key."""
    items = []
    for line in lines[start:]:
        body = _strip_comment(line)
        if not body.strip():
            continue
        item = ITEM.match(body)
        if item and len(item.group("indent")) >= key_indent:
            value = _scalar(item.group("value") or "")
            if value is None:
                return None
            items.append(value)
            continue
        indent = len(body) - len(body.lstrip(" \t"))
        if indent > key_indent:
            return None  # a mapping or other nested value under runs-on: a group, not labels
        break
    return tuple(items) or None


def self_hosted_sets(files: dict[str, str]) -> tuple[list[list[str]], list[str]]:
    sets: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    unreadable: list[str] = []
    for path, text in files.items():
        for line, labels in scan(text):
            if labels is None:
                unreadable.append(f"{path}:{line}")
                continue
            spelled: list[str] = []
            for label in labels:
                if label.lower() not in {s.lower() for s in spelled}:
                    spelled.append(label)
            key = frozenset(label.lower() for label in labels)
            if "self-hosted" in key and key not in seen:
                seen.add(key)
                sets.append(spelled)
    return sets, unreadable
