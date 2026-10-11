"""Pre-classification screening: input hygiene, scope and abuse signals.

Everything here is a high-precision rule evaluated on the folded ticket text (homoglyphs,
invisible characters and spaced-out letters undone). These signals are computed
independently of any model, so a model can never talk its way past them.
"""
from __future__ import annotations

import re

from .models import Screening, Ticket
from .text import contains_secret, content_terms, fold, normalize

SUPPORTED_LANGUAGES = frozenset({"en"})
MAX_MESSAGE_CHARS = 4000


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_TARGETS = r"(?:instructions?|rules|prompts?|polic(?:y|ies)|guidelines|guardrails|restrictions|configuration|system message)"

INJECTION_PATTERNS = _compile(
    rf"\b(?:ignore|disregard|override|bypass)\b.{{0,40}}\b{_TARGETS}",
    rf"\bforget\b.{{0,20}}\b{_TARGETS}",
    r"\b(?:system|developer)\s+(?:prompt|message|mode|override)\b",
    r"\b(?:admin|maintenance|debug|god|sudo|unrestricted)\s+mode\b",
    rf"\b(?:reveal|show|print|repeat|leak|output|dump|paste)\b.{{0,30}}\b(?:your|the)\s+(?:\w+\s+){{0,3}}{_TARGETS}",
    rf"\b(?:what|which)\s+(?:are|were)\s+(?:your|the)\s+(?:\w+\s+){{0,2}}{_TARGETS}\s+(?:you\s+(?:were\s+)?given|you\s+follow)",
    r"\byou are now\b|\bpretend (?:to be|you are)\b|\bjailbreak\b|\bdo anything now\b",
    r"\bact as (?:an? |my |the )?(?:admin\w*|developer|system|root|supervisor|unrestricted)\b",
    r"\b(?:new|updated|real) instructions?\s*:",
    r"\bas the (?:assistant|ai|model|bot)\b.{0,40}\b(?:must|should|will)\b",
    r"\b(?:mark|close|resolve) (?:this|the) ticket\b.{0,40}\b(?:without|do not|don't)\s+escalat",
    r"</?\s*(?:system|assistant|instructions?|ticket|kb_snippets)\s*>",
)

# Questions that can only be answered from the client's own records, or that ask
# support to take an action on the account.
ACCOUNT_SPECIFIC_PATTERNS = _compile(
    r"\bwhy (?:was|is|did|has|were|have|does)\b.{0,40}\b(?:my|our)\b",
    r"\b(?:what|where) is (?:my|our|the status of my)\b",
    r"\b(?:what|which)\s+(?:is|are)\s+the\b.{0,40}\b(?:on|for|of|linked to|registered (?:to|on|with))\s+my\b",
    r"\bstatus of (?:my|our)\b",
    r"\bwhen will (?:my|i|it)\b.{0,40}\b(?:arrive|receive|get|be (?:credited|approved|processed|unlocked|released))\b",
    r"\b(?:check|look (?:at|into)|verify) my (?:account|transaction|payment|withdrawal|deposit|balance)\b",
    r"\bhow much\b.{0,20}\bin my\b",
    r"\b(?:read|tell|give|send|show|confirm)\s+me\b.{0,60}\b(?:digits|card number|email address|phone number|address|password|balance|transactions?|history|account details)\b",
    r"\b(?:approve|unlock|unblock|reverse|refund|release|skip|bypass|waive|speed up|expedite)\b.{0,30}\bmy\b",
)

# Requests about someone else's account, or bulk data about other users.
THIRD_PARTY_PATTERNS = _compile(
    r"\bmy\s+(?:husband|wife|partner|spouse|friend|brother|sister|son|daughter|mother|mom|mum|father|dad|colleague|boss|client|customer|relative)'?s?\b.{0,50}\b(?:account|balance|withdraw\w*|deposit\w*|transactions?|password|email|details|history)\b",
    r"\b(?:his|her)\s+(?:account|balance|password|transactions?|withdraw\w*|deposit\w*|email|details|history)\b",
    r"\b(?:someone|somebody|another (?:person|user|client|customer))(?:\s+else)?'?s?\b.{0,30}\b(?:account|balance|details|transactions?|email)\b",
    r"\b(?:for|of|about)\s+(?:user|client|customer|account holder)s?\s*(?:id|number|no\.?|#)?\s*#?\d{3,}\b",
    r"\b(?:all|every|other)\s+(?:users|clients|customers|accounts|traders)\b",
    r"\b(?:list|export|dump|download)\b.{0,30}\b(?:users|clients|customers|accounts)\b",
)

# Common function words of other languages, used to catch non-English text that is
# labelled "en". Words that are also common English words are excluded.
_FOREIGN_WORDS = frozenset(
    """el la los las que por favor ayuda cuenta mi mis es pero una del esta está
    le les mon ma mes est pas je ne compte avec pour mais
    ich nicht mein meine ist das der und konto bitte
    não nao meu minha conta uma obrigado
    saya tidak akaun akun yang boleh tolong""".split()
)
_ENGLISH_WORDS = frozenset(
    """the a an and is are was my i to of it in on for not can how what why when do you
    with have has this that but me""".split()
)

# Stems that mark a ticket as belonging to account / payments / trading support.
DOMAIN_TERMS = frozenset(
    content_terms(
        """account deposit withdraw payment pay card bank balance fund money transfer
        transaction wallet fee charge commission refund password email signin lock locked
        verification verify kyc document identity trade trading asset profit invest market
        platform app login register registration bonus limit statement otp authentication
        2fa security support crypto stock forex phone profile address personal"""
    )
)


def _language_ok(ticket: Ticket, text: str) -> tuple[bool, str | None]:
    language = (ticket.language or "").strip().lower().replace("_", "-").split("-")[0]
    if language not in SUPPORTED_LANGUAGES:
        return False, f"language '{ticket.language}' is not supported"
    words = re.findall(r"[^\W\d_]+", text.lower())
    foreign = sum(w in _FOREIGN_WORDS for w in words)
    english = sum(w in _ENGLISH_WORDS for w in words)
    if foreign >= 2 and foreign > english:
        return False, "message text appears to be non-English despite the language label"
    return True, None


def screen_ticket(ticket: Ticket) -> Screening:
    text = (ticket.message or "").strip()
    truncated = len(text) > MAX_MESSAGE_CHARS
    if truncated:
        text = text[:MAX_MESSAGE_CHARS]
    folded = fold(text)

    reasons: list[str] = []
    empty = not re.search(r"[^\W_]", folded)
    if empty:
        reasons.append("message has no readable content")
    if truncated:
        reasons.append(f"message truncated to {MAX_MESSAGE_CHARS} characters")

    language_ok, language_reason = _language_ok(ticket, folded)
    if language_reason:
        reasons.append(language_reason)

    checks = {
        "injection": (INJECTION_PATTERNS, "message contains instruction-override / prompt-injection phrasing"),
        "third_party": (THIRD_PARTY_PATTERNS, "request concerns another person's account or other users' data"),
        "account_specific": (ACCOUNT_SPECIFIC_PATTERNS, "answer requires the client's own account records or an account action"),
    }
    hits = {name: any(p.search(folded) for p in patterns) for name, (patterns, _) in checks.items()}
    reasons += [reason for name, (_, reason) in checks.items() if hits[name]]

    sensitive = contains_secret(text)
    if sensitive:
        reasons.append("message contains credentials or card data")

    return Screening(
        text=text,
        empty=empty,
        truncated=truncated,
        unsupported_language=not language_ok and not empty,
        injection=hits["injection"],
        account_specific=hits["account_specific"],
        in_domain=bool(DOMAIN_TERMS & set(content_terms(normalize(text)))),
        third_party=hits["third_party"],
        sensitive_data=sensitive,
        reasons=tuple(reasons),
    )
