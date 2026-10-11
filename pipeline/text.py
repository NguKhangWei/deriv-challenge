"""Deterministic text normalisation shared by classification, retrieval and guards."""
from __future__ import annotations

import re
import unicodedata
from difflib import get_close_matches
from functools import lru_cache

STOPWORDS = frozenset(
    """a about after am an and any are as at be been before but by can could did do
    does for from had has have how i if in into is it its just me my no not now of on
    or our so still than that the their them then there these they this to too us was
    we were what when where which who why will with would you your should must may
    might shall""".split()
)

# Phrase-level canonicalisation applied before tokenising, so that variants such as
# "log in" / "sign-in" / "login" or "cash out" / "withdrawal" meet on one token.
_CANONICAL_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), repl)
    for pattern, repl in (
        (r"\b(?:shut|locked|kicked|blocked)\s+out\b", "locked"),
        (r"\b(?:sign|log)[\s-]?(?:in|on)s?\b", "signin"),
        (r"\blogins?\b", "signin"),
        (r"\be-mails?\b", "email"),
        (r"\b(?:cash(?:ed|ing)?[\s-]?outs?|payouts?|tak(?:e|ing)\s+(?:my\s+|the\s+)?(?:money|funds)\s+out)\b", "withdraw"),
        (r"\bwithdr[ae]w(?:als?|ing|n|s)?\b", "withdraw"),
        (r"\b(?:reject(?:ed|s|ion)?|denied|refused|bounced)\b", "declined"),
        (r"\btries\b", "attempts"),
        (r"\b(?:kyc|id\s+check)\b", "verification"),
        (r"\b(?:pwd|passcode)\b", "password"),
        (r"\btop(?:ped|ping)?[\s-]?ups?\b", "deposit"),
    )
)

_SUFFIXES = ("ations", "ation", "ings", "ing", "edly", "ed", "es", "s")

# --- Obfuscation folding -------------------------------------------------------

_INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤﻿]")
_CONFUSABLES = str.maketrans({
    # Cyrillic and Greek letters that render like Latin ones
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y",
    "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X",
    "ο": "o", "α": "a", "ι": "i", "ρ": "p", "Ο": "O", "Α": "A",
    # typographic quotes
    "‘": "'", "’": "'", "‛": "'", "′": "'", "“": '"', "”": '"',
})
_SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z] ){2,}[A-Za-z]\b")


def fold(text: str) -> str:
    """Undo common obfuscation so rule checks see what a human reader sees:
    NFKC (full-width forms), invisible characters, homoglyphs, curly quotes and
    s p a c e d - o u t letters."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _INVISIBLE.sub("", text).translate(_CONFUSABLES)
    return _SPACED_LETTERS.sub(lambda m: m.group(0).replace(" ", ""), text)


# --- Typo tolerance for domain vocabulary ----------------------------------------

_VOCAB = frozenset(
    """withdraw withdrawal withdrawals deposit deposits deposited password passwords
    verification verified account accounts locked declined balance transaction
    transactions payment payments transfer email attempts unlock blocked suspended
    recovery advice prediction investment trading profit identity document documents""".split()
)


@lru_cache(maxsize=4096)
def _correct(word: str) -> str:
    """Snap a near-miss spelling onto domain vocabulary (same first letter, close length)."""
    if word in _VOCAB:
        return word
    match = get_close_matches(word, _VOCAB, n=1, cutoff=0.85)
    if match and match[0][0] == word[0] and abs(len(match[0]) - len(word)) <= 2:
        return match[0]
    return word


def normalize(text: str) -> str:
    text = fold(text).lower()
    text = re.sub(r"[a-z]{5,}", lambda m: _correct(m.group(0)), text)
    for pattern, repl in _CANONICAL_PHRASES:
        text = pattern.sub(repl, text)
    return text


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def stem(token: str) -> str:
    """A small suffix stripper: enough to join deposit/deposited, lock/locked."""
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            if suffix == "s" and token.endswith("ss"):
                break
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    return token


def content_terms(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def support_ratio(claim: str, source: str) -> float:
    """Fraction of a claim's content terms that also appear in the source text."""
    claim_terms = set(content_terms(claim))
    if not claim_terms:
        return 1.0
    return len(claim_terms & set(content_terms(source))) / len(claim_terms)


# A credential stated in the message: "password is X", "OTP: 123456", "pin=1234".
SECRET_PATTERN = re.compile(
    r"\b(?:password|passcode|pwd|pin|otp|cvv|cvc|security code|verification code)\b"
    r"(?:\s*[:=]\s*|\s+(?:is|was)\s+(?!not\b|wrong\b|incorrect\b|correct\b|invalid\b|expired\b|still\b|too\b|being\b)|\s+)"
    r"(?=\S*\d)\S+",
    re.IGNORECASE,
)
_PASSWORD_IS = re.compile(
    r"\b(?:password|passcode|pwd|pin)\s*(?:[:=]|\s(?:is|was))\s+(?!not\b|wrong\b|incorrect\b|correct\b|invalid\b|expired\b|still\b|too\b|being\b)\S+",
    re.IGNORECASE,
)
CARD_PATTERN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PASSWORD_IS, "[REDACTED_SECRET]"),
    (SECRET_PATTERN, "[REDACTED_SECRET]"),
    (CARD_PATTERN, "[REDACTED_CARD]"),
    (re.compile(r"\b(?:exp(?:iry|ires)?\.?\s*)?(?:0[1-9]|1[0-2])\s*/\s*\d{2,4}\b", re.IGNORECASE), "[REDACTED_EXPIRY]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\+?\d[\d\s().-]{8,}\d"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{4,8}\b"), "[REDACTED_NUMBER]"),
)


def contains_secret(text: str) -> bool:
    folded = fold(text)
    return bool(_PASSWORD_IS.search(folded) or SECRET_PATTERN.search(folded) or CARD_PATTERN.search(folded))


def redact(text: str) -> str:
    """Mask secrets, card data, emails, phones and bare codes before text leaves the process."""
    text = fold(text)
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text
