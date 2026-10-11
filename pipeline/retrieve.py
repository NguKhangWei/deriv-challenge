"""RETRIEVE_CONTEXT: rank KB articles against the ticket text only."""
from __future__ import annotations

import math
from collections import Counter
from typing import Protocol, Sequence

from .models import Article, Hit
from .text import content_terms


class Retriever(Protocol):
    def index(self, articles: Sequence[Article]) -> None: ...

    def retrieve(self, query: str) -> list[Hit]: ...


class BM25Retriever:
    """Okapi BM25 over title (boosted) + body.

    Deterministic: no randomness, scores rounded, ties broken by article_id.
    Returns 0-3 hits; an empty list means nothing cleared the relevance bar.
    """

    def __init__(
        self,
        *,
        k1: float = 1.5,
        b: float = 0.75,
        title_weight: int = 2,
        min_score: float = 1.5,
        relative_cutoff: float = 0.4,
        top_k: int = 3,
    ) -> None:
        self.k1, self.b, self.title_weight = k1, b, title_weight
        self.min_score, self.relative_cutoff, self.top_k = min_score, relative_cutoff, top_k
        self._docs: list[tuple[Article, Counter[str], int]] = []
        self._idf: dict[str, float] = {}
        self._avg_len = 0.0

    def index(self, articles: Sequence[Article]) -> None:
        self._docs = []
        for article in sorted(articles, key=lambda a: a.article_id):
            terms = content_terms(article.title) * self.title_weight + content_terms(article.body)
            self._docs.append((article, Counter(terms), len(terms)))
        n = len(self._docs)
        df: Counter[str] = Counter()
        for _, tf, _ in self._docs:
            df.update(tf.keys())
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self._avg_len = sum(length for *_, length in self._docs) / n if n else 0.0

    def score_all(self, query: str) -> list[Hit]:
        q_terms = sorted(set(content_terms(query)))
        hits = []
        for article, tf, length in self._docs:
            score = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                norm = self.k1 * (1 - self.b + self.b * length / self._avg_len)
                score += self._idf[term] * f * (self.k1 + 1) / (f + norm)
            hits.append(Hit(article.article_id, article.title, round(score, 4)))
        return sorted(hits, key=lambda h: (-h.score, h.article_id))

    def retrieve(self, query: str) -> list[Hit]:
        ranked = self.score_all(query)
        if not ranked or ranked[0].score < self.min_score:
            return []
        floor = max(self.min_score, ranked[0].score * self.relative_cutoff)
        return [h for h in ranked if h.score >= floor][: self.top_k]
