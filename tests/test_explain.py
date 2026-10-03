"""The Claude call: the three ways it can look successful and not be.

No network. A fake client stands in for the SDK so every stop_reason and the
failed-search shape can be exercised deliberately.
"""

from harness import check, done, equal, heading
from datetime import date

from monitor.compute import build_portfolio, movers
from monitor.config import Config, Holding
from monitor.explain import (
    ExplanationTruncated,
    ExplanationUnavailable,
    explain,
    parse_notes,
)
from monitor.prices import Quote


class Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class Response:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class FakeClient:
    """Returns the queued responses in order and records every request."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if not self.queue:
            raise AssertionError("the code made more API calls than the test queued")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text(s):
    return Block("text", text=s)


def fixture():
    cfg = Config("T", "AUD", [Holding("BHP.AX", 10, 100, "equity")], [], 0, [], 3)
    p = build_portfolio(cfg, {"BHP.AX": Quote("BHP.AX", price=44.0)},
                        previous_prices={"BHP.AX": 40.0},
                        previous_run=date(2026, 9, 26), today=date(2026, 10, 3))
    return cfg, p, movers(p, 3)


cfg, portfolio, moved = fixture()
equal("the fixture has one holding worth explaining", [r.ticker for r in moved], ["BHP.AX"])

heading("the happy path")
client = FakeClient(Response([text("BHP.AX: Iron ore firmed; the AFR reported on "
                                   "1 October that supply was tight.")]))
result = explain(moved, portfolio, 3, client=client)
equal("one note, keyed by ticker", list(result.notes), ["BHP.AX"])
check("the note survives intact", "AFR" in result.notes["BHP.AX"])
equal("one API call, not several", len(client.requests), 1)
req = client.requests[0]
equal("the model is the current Opus", req["model"], "claude-opus-5-5")
check("the web search tool is attached",
      any(t["type"].startswith("web_search") for t in req["tools"]), str(req["tools"]))
check("no thinking budget_tokens is sent (removed on this model)",
      "thinking" not in req or "budget_tokens" not in str(req.get("thinking")))
check("the prompt carries the real numbers",
      "+10.00%" in req["messages"][0]["content"]
      and "40.00" in req["messages"][0]["content"],
      req["messages"][0]["content"])
check("the system prompt forbids laundering opinion into fact",
      "dated opinion" in req["system"])
check("and forbids overruling a number", "overrule" in req["system"])

heading("max_tokens: truncated, and never sent")
client = FakeClient(Response([text("BHP.AX: Iron ore fir")], stop_reason="max_tokens"))
try:
    explain(moved, portfolio, 3, client=client)
    check("raises rather than returning the half-written brief", False)
except ExplanationTruncated as exc:
    check("raises ExplanationTruncated", True)
    check("and says why", "max_tokens" in str(exc), str(exc))

heading("refusal: no usable answer, so no email")
client = FakeClient(Response([text("")], stop_reason="refusal",
                             stop_details=Block("refusal", category="cyber")))
try:
    explain(moved, portfolio, 3, client=client)
    check("raises on refusal", False)
except ExplanationTruncated as exc:
    check("raises ExplanationTruncated", True)
    check("and reports the category", "cyber" in str(exc), str(exc))

heading("pause_turn: reported as success, but unfinished")
client = FakeClient(
    Response([Block("server_tool_use", name="web_search"), text("BHP.AX: Iron ore ")],
             stop_reason="pause_turn"),
    Response([text("firmed on tight supply.")], stop_reason="end_turn"),
)
result = explain(moved, portfolio, 3, client=client)
equal("it resumed", len(client.requests), 2)
check("the resume re-sends the paused assistant turn",
      client.requests[1]["messages"][-1]["role"] == "assistant",
      str([m["role"] for m in client.requests[1]["messages"]]))
check("no 'continue' message is bolted on",
      len(client.requests[1]["messages"]) == 2,
      str([m["role"] for m in client.requests[1]["messages"]]))
check("both halves of the answer survive",
      result.notes["BHP.AX"] == "Iron ore firmed on tight supply.",
      result.notes.get("BHP.AX"))

heading("pause_turn forever: bounded, and the partial is never returned")
client = FakeClient(*[
    Response([text("BHP.AX: partial ")], stop_reason="pause_turn") for _ in range(9)
])
try:
    explain(moved, portfolio, 3, client=client)
    check("gives up rather than passing off the partial", False)
except ExplanationTruncated:
    check("raises ExplanationTruncated", True)
    check("after a bounded number of resumes", len(client.requests) == 5,
          f"made {len(client.requests)} calls")

heading("a failed web search is HTTP 200 with an error object, not a list")
error_block = Block("web_search_tool_result",
                    content=Block("web_search_tool_result_error",
                                  error_code="max_uses_exceeded"))
client = FakeClient(Response([Block("server_tool_use", name="web_search"), error_block,
                              text("BHP.AX: No reporting available; the move is "
                                   "consistent with the sector.")]))
result = explain(moved, portfolio, 3, client=client)
check("the run survives", result.notes["BHP.AX"].startswith("No reporting"))
check("degradation is recorded", result.degraded is True)
check("with the error code", "max_uses_exceeded" in result.search_errors,
      str(result.search_errors))

ok_block = Block("web_search_tool_result", content=[Block("web_search_result")])
client = FakeClient(Response([ok_block, text("BHP.AX: Fine.")]))
result = explain(moved, portfolio, 3, client=client)
check("a successful search list is not mistaken for an error", result.degraded is False)

heading("an unreachable API loses the notes, not the email")
import anthropic

client = FakeClient(anthropic.APIConnectionError(request=None))
try:
    explain(moved, portfolio, 3, client=client)
    check("raises ExplanationUnavailable", False)
except ExplanationUnavailable:
    check("raises ExplanationUnavailable, which main() survives", True)
except ExplanationTruncated:
    check("must not be treated as a truncation", False)

heading("empty text is a failure, not an empty answer")
client = FakeClient(Response([], stop_reason="end_turn"))
try:
    explain(moved, portfolio, 3, client=client)
    check("raises", False)
except ExplanationTruncated:
    check("raises ExplanationTruncated", True)

heading("parsing keeps whatever it cannot attribute")
notes, leftover = parse_notes("Preamble line.\nBHP.AX: One.\nTwo.\nVAS.AX: Three.",
                              ["BHP.AX", "VAS.AX"])
equal("notes", notes, {"BHP.AX": "One. Two.", "VAS.AX": "Three."})
equal("nothing is dropped", leftover, "Preamble line.")

done()
