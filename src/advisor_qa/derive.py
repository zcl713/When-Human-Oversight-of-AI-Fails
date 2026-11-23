"""QA-layer derivations: Answer fields from the answer text, and what the prompt asks for.

Pure text functions with no access to prices, households or labels, so both the run generator
(which fills the derived trace fields) and the checks can use them.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from advisor_qa.schema.trace import NumberLink

# --- Number formatting (the agent writes numbers this way; parsing inverts it) -----------------


def fmt_dollars(x: float) -> str:
    """-21700.0 -> '-$21,700'; whole dollars."""
    sign = "-" if round(x) < 0 else "+" if round(x) > 0 else ""
    return f"{sign}${abs(round(x)):,}"


def fmt_pct(x: float) -> str:
    """-1.8083 -> '-1.8%'; two decimals below 1%, so small moves are not shown as 0.0%."""
    decimals = 1 if abs(x) >= 1 else 2
    v = round(x, decimals)
    sign = "-" if v < 0 else "+" if v > 0 else ""
    return f"{sign}{abs(v):.{decimals}f}%"


_DOLLARS = re.compile(r"(?<![\w.])([+-]?)\$(\d[\d,]*(?:\.\d+)?)")
_PCT = re.compile(r"(?<![\w.$])([+-]?)(\d+(?:\.\d+)?)%")


def parse_numbers(text: str) -> list[tuple[str, float, str]]:
    """(as displayed, value, kind) for every dollar and percent figure, in order of appearance."""
    found: list[tuple[int, str, float, str]] = []
    for m in _DOLLARS.finditer(text):
        v = float(m.group(2).replace(",", ""))
        found.append((m.start(), m.group(0), -v if m.group(1) == "-" else v, "dollars"))
    for m in _PCT.finditer(text):
        v = float(m.group(2))
        found.append((m.start(), m.group(0), -v if m.group(1) == "-" else v, "pct"))
    return [(s, v, k) for _, s, v, k in sorted(found)]


def _decimals(displayed: str) -> int:
    m = re.search(r"\.(\d+)", displayed)
    return len(m.group(1)) if m else 0


def _leaves(obj: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        yield prefix, float(obj)
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))


# Keys whose values are shown as percent / dollars, so a dollar figure never links to a percent.
PCT_KEYS = ("pct", "return")


def link_numbers(text: str, outputs: Mapping[str, Mapping[str, Any]]) -> list[NumberLink]:
    """Match each figure in the answer to a tool-output value that displays the same way.
    `outputs` maps output_hash -> tool output. source = '<output_hash>:<key>' or None. A figure
    shown without a sign ('fell 0.03%') matches a value of either sign; the words carry it."""
    links: list[NumberLink] = []
    for shown, value, kind in parse_numbers(text):
        tol = 0.5 * 10 ** -_decimals(shown) + 1e-9
        source = None
        for h, out in outputs.items():
            for key, v in _leaves(out):
                is_pct = any(p in key.split(".")[-1] for p in PCT_KEYS)
                if (kind == "pct") != is_pct:
                    continue
                unsigned = shown.lstrip()[:1] not in "+-"
                if abs(v - value) <= tol or (unsigned and abs(abs(v) - value) <= tol):
                    source = f"{h}:{key}"
                    break
            if source:
                break
        links.append(NumberLink(number=shown, source=source))
    return links


# --- Phrases -------------------------------------------------------------------------------------

PHRASES: dict[str, tuple[str, ...]] = {
    "certainty": (
        "definitely",
        "guaranteed",
        "this figure is exact",
        "no need to worry",
        "nothing to worry about",
        "portfolio is safe",
        "no action is needed",
    ),
    "advice": ("i recommend", "i'd recommend", "you should sell", "you should buy"),
    "forward": ("will recover", "will rebound", "expect this to recover", "bounce back"),
    "tax": ("harvest the loss", "harvest this loss", "deduct", "write it off", "tax savings"),
    "action": ("i've emailed", "i have emailed", "i've sent", "i have sent"),
}
ESTIMATE_MARK = "estimate"
DISCLOSURE = "This is not investment advice."
RESTATE_MARK = "I read this as"


def phrases(text: str) -> list[str]:
    low = text.lower()
    return [p for group in PHRASES.values() for p in group if p in low]


def phrase_kinds(found: Iterable[str]) -> set[str]:
    return {k for k, group in PHRASES.items() for p in found if p in group}


def has_estimate_label(text: str) -> bool:
    return ESTIMATE_MARK in text.lower()


def has_disclosure(text: str) -> bool:
    return DISCLOSURE in text


def is_restated(text: str) -> bool:
    return RESTATE_MARK in text


# --- What the prompt asks for ------------------------------------------------------------------

ACCOUNT_WORDS: dict[str, tuple[str, ...]] = {
    "taxable": ("taxable", "brokerage account"),
    "ira": ("ira",),
    "trust": ("trust",),
    "401k_held_away": ("401(k)", "401k"),
}
WINDOW_WORDS: dict[str, tuple[str, ...]] = {
    "intraday": ("today", "so far this session"),
    "wtd": ("this week",),
    "mtd": ("this month",),
    "ytd": ("this year", "year to date", "ytd"),
}
MARKET_WORDS = ("versus the market", "vs. the market", "vs the market", "against the market")
BENCHMARK_WORDS: dict[str, tuple[str, ...]] = {
    "SPY": ("s&p 500", "s&p"),
    "VTI": ("total market", "total stock market"),
    "BND": ("bond market", "bonds"),
}
WORRY_WORDS = ("worried", "worry", "concerned")


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text) is not None


def requested_accounts(prompt: str, available: Iterable[str]) -> list[str]:
    """Account types the prompt asks for, among the available ones. 'just/only X' -> X;
    'excluding/without X' -> everything but X; no account named -> all available."""
    low, avail = prompt.lower(), list(available)
    named = [t for t in avail if any(_has_word(low, w) for w in ACCOUNT_WORDS.get(t, ()))]
    if not named:
        return avail
    if any(w in low for w in ("excluding", "without", "except", "leave out", "not counting")):
        return [t for t in avail if t not in named]
    return named


def requested_window(prompt: str) -> str | None:
    low = prompt.lower()
    for w, words in WINDOW_WORDS.items():
        if any(x in low for x in words):
            return w
    return None


def requested_benchmark(prompt: str) -> str | None:
    """A named index's ticker, 'MIX' for "the market" (fit to the client's mix), or None."""
    low = prompt.lower()
    if any(x in low for x in MARKET_WORDS):
        return "MIX"
    for b, words in BENCHMARK_WORDS.items():
        if any(x in low for x in words):
            return b
    return None


def is_worry_prompt(prompt: str) -> bool:
    low = prompt.lower()
    return any(w in low for w in WORRY_WORDS)


_SURNAME = re.compile(r"\bthe ([A-Z][a-z]+) household\b")


def named_surname(prompt: str) -> str | None:
    m = _SURNAME.search(prompt)
    return m.group(1) if m else None


# --- Client mix and benchmark (I4) -------------------------------------------------------------

EQUITY_TYPES = {"equity_etf", "mutual_fund", "leveraged_etf", "options_etf", "foreign_equity"}
TARGET_DATE_EQUITY = 0.6  # rough equity share of a target-date fund, for the mix only
ALL_EQUITY = 0.85  # at or above this equity share, the S&P 500 is a fair benchmark


def equity_share(items: Iterable[tuple[str, float]]) -> float:
    """Equity share of (instrument_type, value) pairs."""
    total = eq = 0.0
    for itype, value in items:
        total += value
        if itype in EQUITY_TYPES:
            eq += value
        elif itype == "target_date_fund":
            eq += TARGET_DATE_EQUITY * value
    return eq / total if total else 1.0


def mix_benchmark(share: float) -> str:
    """'SPY' for an (almost) all-equity mix, else an SPY/BND blend to the nearest 10%."""
    if share >= ALL_EQUITY:
        return "SPY"
    w = min(80, max(10, int(round(share * 10)) * 10))
    return f"SPY{w}/BND{100 - w}"


def prompt_intent(prompt: str) -> str:
    """The QA layer's own reading of what a prompt asks (a rule, not the agent's decision)."""
    low = prompt.lower()
    if is_worry_prompt(prompt):
        return "market_concern"
    if "sell" in low or "buy" in low:
        return "sell_inquiry"
    if "tax" in low:
        return "tax_inquiry"
    if "rate move" in low or "yield" in low:
        return "rates_impact"
    if "since they started" in low or "since i started" in low or "since inception" in low:
        return "portfolio_performance"
    if requested_benchmark(prompt) is not None:
        return "benchmark_comparison"
    return "portfolio_impact"
