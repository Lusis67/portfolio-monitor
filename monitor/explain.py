"""One Claude API call: why did the material movers move?

Only called when something actually moved more than note_threshold_pct. If
nothing did, no call is made at all.

Three failure modes this file exists to get right:

  * `max_tokens` caps output AND thinking together. A brief that outgrows it
    comes back truncated with stop_reason "max_tokens", and a truncated brief
    must never reach the inbox — so that raises instead of returning.
  * `refusal` likewise: HTTP 200, no usable answer. Raise, don't send.
  * With a server tool in the request the turn can come back `pause_turn`.
    That reports success but the turn is unfinished. Resume it a bounded
    number of times, and never hand back the partial text as the answer.

A failed web search is a different matter. It arrives as HTTP 200 with an
error object where the results list should be, so the code branches on the
shape rather than indexing first — and a failed search degrades to answering
from the numbers alone. It never kills the run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

MODEL = "claude-opus-5-5"
# Generous on purpose: max_tokens bounds thinking and output together, and the
# code raises rather than sends if it is ever hit.
MAX_TOKENS = 16000
MAX_PAUSE_RESUMES = 4
MAX_SEARCHES = 6

SYSTEM = """You write one short paragraph per holding for a private weekly \
portfolio email. The reader is the owner, reading on a phone over coffee.

Rules, in order of importance:

1. The numbers in the brief are the facts. They are computed from closing \
prices and they are correct. Never contradict one, never restate one wrongly, \
and never let something you read online overrule one.
2. Anything you find by searching is somebody's dated opinion, not a fact. \
Attribute it: who said it and when (e.g. "the AFR reported on 1 October that \
..."). If you cannot attribute it, leave it out.
3. If you cannot find out why something moved, say so plainly. "No clear \
single driver; the sector was weak" is a good answer. Inventing a cause is not.
4. No advice. Do not suggest buying, selling or holding anything.
5. Two or three sentences per holding. No preamble, no sign-off, no markdown \
headings, no bullet points.

Format your reply as one block per holding, each starting with the ticker \
followed by a colon, like:

BHP.AX: <two or three sentences>
VAS.AX: <two or three sentences>

Nothing before the first ticker and nothing after the last block."""


@dataclass
class Explanation:
    notes: dict[str, str] = field(default_factory=dict)
    leftover: str = ""
    searches_run: int = 0
    search_errors: list[str] = field(default_factory=list)
    model: str = MODEL

    @property
    def degraded(self) -> bool:
        return bool(self.search_errors)


class ExplanationTruncated(Exception):
    """The brief came back unfinished. Never send a half-written email."""


class ExplanationUnavailable(Exception):
    """The API could not be reached at all. The email still goes out."""


def build_prompt(rows, portfolio, threshold_pct: float) -> str:
    lines = [
        f"Here is this week's price action for the holdings that moved more than "
        f"{threshold_pct:g}%. Explain each one.",
        "",
    ]
    for row in rows:
        lines.append(
            f"- {row.ticker} ({row.holding.asset_type}): "
            f"{row.week_change_pct:+.2f}% on the week, "
            f"from {row.prev_price:,.2f} to {row.quote.price:,.2f} "
            f"{portfolio.currency}."
        )
    if portfolio.previous_run is not None:
        lines.append("")
        lines.append(
            f'"This week" means since the previous run on '
            f"{portfolio.previous_run.isoformat()}"
            + (f" ({portfolio.days_covered} days)." if portfolio.days_covered is not None else ".")
        )
    lines.append("")
    lines.append(
        "These are ASX-listed securities. Search the web for what happened to each "
        "one over that period. Today is the date of this run; prefer sources from "
        "within the period above and say the date of anything you cite."
    )
    return "\n".join(lines)


def _collect_text(content) -> str:
    return "".join(b.text for b in content if getattr(b, "type", None) == "text")


def _inspect_searches(content, searches: list[int], errors: list[str]) -> None:
    """Count searches and detect the error-object-instead-of-results shape."""
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use":
            searches[0] += 1
        elif btype in ("web_search_tool_result", "web_fetch_tool_result"):
            payload = getattr(block, "content", None)
            # Success: a list of results. Failure: a single error object.
            # Branch on the shape; never index first.
            if isinstance(payload, list):
                continue
            code = getattr(payload, "error_code", None)
            if code is None and isinstance(payload, dict):
                code = payload.get("error_code")
            errors.append(str(code or "unknown web search error"))


def explain(rows, portfolio, threshold_pct: float, client=None) -> Explanation:
    """Make the one API call. Raises on a truncated or refused brief."""
    if not rows:
        raise ValueError("explain() called with nothing to explain")

    if client is None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ExplanationUnavailable("ANTHROPIC_API_KEY is not set")
        client = anthropic.Anthropic()

    prompt = build_prompt(rows, portfolio, threshold_pct)
    user_message = {"role": "user", "content": prompt}
    messages = [user_message]

    tools = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": MAX_SEARCHES,
        }
    ]

    text_parts: list[str] = []
    searches = [0]
    search_errors: list[str] = []

    for attempt in range(MAX_PAUSE_RESUMES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                output_config={"effort": "medium"},
                system=SYSTEM,
                messages=messages,
                tools=tools,
            )
        except Exception as exc:
            import anthropic

            if isinstance(exc, anthropic.APIError):
                raise ExplanationUnavailable(f"{type(exc).__name__}: {exc}") from exc
            raise

        _inspect_searches(response.content, searches, search_errors)
        text_parts.append(_collect_text(response.content))
        stop = response.stop_reason

        if stop == "max_tokens":
            raise ExplanationTruncated(
                "the brief hit max_tokens and came back truncated — refusing to send "
                "a half-written email"
            )
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise ExplanationTruncated(
                f"the model declined to answer (stop_reason=refusal, category={category!r})"
            )
        if stop == "pause_turn":
            if attempt >= MAX_PAUSE_RESUMES:
                raise ExplanationTruncated(
                    f"the turn was still paused after {MAX_PAUSE_RESUMES} resumes — "
                    "refusing to pass off a partial answer as the finished one"
                )
            # Re-send the user message and the paused assistant turn; the server
            # resumes from the trailing server_tool_use block on its own. Do not
            # add a "continue" message.
            messages = [user_message, {"role": "assistant", "content": response.content}]
            continue
        break

    text = "".join(text_parts).strip()
    if not text:
        raise ExplanationTruncated("the model returned no text at all")

    notes, leftover = parse_notes(text, [r.ticker for r in rows])
    for code in search_errors:
        print(
            f"WEB SEARCH FAILED — {code}. The notes below are from the numbers alone.",
            file=sys.stderr,
        )
    return Explanation(
        notes=notes,
        leftover=leftover,
        searches_run=searches[0],
        search_errors=search_errors,
    )


def parse_notes(text: str, tickers) -> tuple[dict[str, str], str]:
    """Split "TICKER: note" blocks apart. Anything unmatched is kept, not dropped."""
    notes: dict[str, str] = {}
    current: str | None = None
    leftover: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if current and buffer:
            notes[current] = " ".join(" ".join(buffer).split())

    for line in text.splitlines():
        stripped = line.strip()
        matched = None
        for ticker in tickers:
            if stripped.upper().startswith(ticker.upper() + ":"):
                matched = ticker
                break
        if matched:
            flush()
            current = matched
            buffer = [stripped[len(matched) + 1 :].strip()]
        elif current:
            buffer.append(stripped)
        elif stripped:
            leftover.append(stripped)
    flush()
    return notes, " ".join(leftover).strip()
