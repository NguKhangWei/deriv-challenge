"""LOAD_DATA: read and schema-check the input files. Fails fast on malformed input."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .models import Article, Ticket

T = TypeVar("T", bound=BaseModel)


class InputError(ValueError):
    pass


def _load_list(path: Path, model: type[T], id_field: str) -> list[T]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"{path}: file not found") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(raw, list):
        raise InputError(f"{path}: expected a JSON array")

    items, seen = [], set()
    for i, entry in enumerate(raw):
        try:
            item = model.model_validate(entry)
        except ValidationError as exc:
            raise InputError(f"{path}[{i}]: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}") from exc
        key = getattr(item, id_field)
        if key in seen:
            raise InputError(f"{path}: duplicate {id_field} {key!r}")
        seen.add(key)
        items.append(item)
    return items


def load_tickets(path: Path) -> list[Ticket]:
    return _load_list(path, Ticket, "id")


def load_articles(path: Path) -> list[Article]:
    articles = _load_list(path, Article, "article_id")
    if not articles:
        raise InputError(f"{path}: knowledge base is empty")
    return articles
