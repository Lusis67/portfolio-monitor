"""HTML and plain-text email bodies.

Constraints, all of them from reading email on a phone:
  * inline CSS only — <style> blocks are stripped by several clients,
  * no external assets — no images, no web fonts, no tracking pixel,
  * the summary table has five columns and sets no fixed pixel widths, so it
    reflows rather than demanding a horizontal scroll,
  * every warning sits above the numbers, not below them, because a number
    you have already read is a number you have already believed.
"""

from __future__ import annotations

import html
from datetime import date

BODY = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
WRAP = f"{BODY};max-width:640px;margin:0 auto;padding:16px;color:#1b1b1b;font-size:16px;line-height:1.45"
TH = (
    "text-align:right;padding:6px 4px;border-bottom:2px solid #c9c9c9;"
    "font-size:12px;text-transform:uppercase;letter-spacing:0.03em;color:#555"
)
TD = "padding:8px 4px;border-bottom:1px solid #e6e6e6;text-align:right;font-size:14px"
MUTED = "color:#6b6b6b;font-size:12px"
UP = "#157347"
DOWN = "#b02a37"
WARN_BOX = (
    "background:#fff4e5;border-left:4px solid #e08600;padding:10px 12px;"
    "margin:0 0 14px 0;border-radius:3px;font-size:14px"
)
ERR_BOX = (
    "background:#fdecee;border-left:4px solid #b02a37;padding:10px 12px;"
    "margin:0 0 14px 0;border-radius:3px;font-size:14px"
)
INFO_BOX = (
    "background:#eef4fb;border-left:4px solid #2f6fab;padding:10px 12px;"
    "margin:0 0 14px 0;border-radius:3px;font-size:14px"
)


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def money(value, currency: str) -> str:
    if value is None:
        return "—"
    return f"{currency} {value:,.2f}"


def pct(value, places: int = 1, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.{places}f}%" if signed else f"{value:.{places}f}%"


def _change_colour(value) -> str:
    if value is None:
        return "#6b6b6b"
    return UP if value > 0 else (DOWN if value < 0 else "#1b1b1b")


def subject(config, portfolio, alerts, run_date: date) -> str:
    bits = [f"{config.portfolio_name} weekly — {run_date.strftime('%-d %b %Y')}"]
    flags = []
    if portfolio.first_run:
        flags.append("first run, snapshot")
    if portfolio.failed_tickers:
        flags.append(f"{len(portfolio.failed_tickers)} price(s) missing")
    if alerts and alerts.triggered:
        flags.append(f"{len(alerts.triggered)} alert(s)")
    if flags:
        bits.append("(" + "; ".join(flags) + ")")
    return " ".join(bits)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def render_html(config, portfolio, alerts, explanation, run_date: date, state_warning=None,
                explain_warning=None) -> str:
    out: list[str] = []
    a = out.append

    a(f'<div style="{WRAP}">')
    a(
        f'<h1 style="font-size:20px;margin:0 0 2px 0">{_e(config.portfolio_name)}</h1>'
        f'<p style="{MUTED};margin:0 0 16px 0">Week to {_e(run_date.strftime("%A %-d %B %Y"))}'
        + (
            f' · since {_e(portfolio.previous_run.isoformat())}'
            if portfolio.previous_run
            else " · first run"
        )
        + "</p>"
    )

    # ---- everything the reader must know before believing a number ----
    for warning in portfolio.warnings:
        box = ERR_BOX if portfolio.failed_tickers and "No price for" in warning else WARN_BOX
        if warning.startswith("This is the first run") or warning.startswith("No holdings"):
            box = INFO_BOX
        a(f'<p style="{box}">{_e(warning)}</p>')

    if config.config_errors:
        items = "".join(f"<li>{_e(err)}</li>" for err in config.config_errors)
        a(
            f'<div style="{ERR_BOX}"><strong>holdings.yaml needs attention</strong>'
            f'<ul style="margin:6px 0 0 18px;padding:0">{items}</ul></div>'
        )

    if explain_warning:
        a(f'<p style="{WARN_BOX}">{_e(explain_warning)}</p>')
    if explanation is not None and explanation.degraded:
        a(
            f'<p style="{WARN_BOX}">Web search failed this run '
            f"({_e(', '.join(explanation.search_errors))}), so the notes below are "
            "written from the price moves alone, without any outside reporting.</p>"
        )
    if state_warning:
        a(f'<p style="{WARN_BOX}">{_e(state_warning)}</p>')

    # ---- the total ----
    if portfolio.unconfigured:
        a(
            '<p style="margin:0 0 16px 0">No portfolio value to report — '
            "nothing is configured yet.</p>"
        )
    else:
        total = money(portfolio.total_value, config.currency)
        suffix = ""
        if portfolio.failed_tickers:
            suffix = (
                f' <span style="{MUTED}">(excludes '
                f'{_e(", ".join(portfolio.failed_tickers))})</span>'
            )
        a(
            f'<p style="margin:0 0 16px 0;font-size:18px"><strong>{_e(total)}</strong>'
            f'{suffix}</p>'
        )

    a(_html_table(config, portfolio))
    a(_html_notes(portfolio, explanation, config))
    a(_html_alerts(alerts))

    a(
        f'<p style="{MUTED};margin-top:24px;border-top:1px solid #e6e6e6;padding-top:10px">'
        "Prices are last closes from Yahoo Finance via yfinance, which is a scraper "
        "and sometimes simply fails; anything it could not fetch is named above rather "
        "than filled in. Nothing here is advice."
        "</p>"
    )
    a("</div>")
    return "\n".join(out)


def _html_table(config, portfolio) -> str:
    if not portfolio.rows:
        return f'<p style="{MUTED}">holdings.yaml lists no holdings.</p>'

    groups: dict[str, list] = {}
    for row in portfolio.rows:
        groups.setdefault(row.holding.asset_type, []).append(row)

    out = [
        '<table role="presentation" style="width:100%;border-collapse:collapse;'
        'table-layout:fixed;margin:0 0 20px 0">',
        "<thead><tr>",
        f'<th style="{TH};text-align:left;width:30%">Holding</th>',
        f'<th style="{TH};width:18%">Last</th>',
        f'<th style="{TH};width:17%">Week</th>',
        f'<th style="{TH};width:20%">Weight</th>',
        f'<th style="{TH};width:15%">Drift</th>',
        "</tr></thead><tbody>",
    ]

    for asset_type, rows in groups.items():
        out.append(
            f'<tr><td colspan="5" style="padding:10px 4px 4px 0;{MUTED};'
            f'text-transform:uppercase;letter-spacing:0.04em">{_e(asset_type)}</td></tr>'
        )
        for row in rows:
            out.append(_html_row(row, config))

    out.append("</tbody></table>")
    return "".join(out)


def _html_row(row, config) -> str:
    name = f'<strong>{_e(row.ticker)}</strong>'
    if not row.priced:
        name += (
            f'<br><span style="color:{DOWN};font-size:12px">no price — '
            f"excluded from the total</span>"
        )
    elif row.holding.units == 0:
        name += f'<br><span style="{MUTED}">units: 0</span>'
    else:
        name += f'<br><span style="{MUTED}">{row.holding.units:g} units</span>'

    last = f"{row.quote.price:,.2f}" if row.priced else "—"

    if row.week_change_pct is None:
        week = f'<span style="{MUTED}">—</span>'
    else:
        week = (
            f'<span style="color:{_change_colour(row.week_change_pct)}">'
            f"{row.week_change_pct:+.1f}%</span>"
        )

    if row.actual_weight is None:
        weight = f'<span style="{MUTED}">—</span>'
    else:
        weight = f"{row.actual_weight:.1f}%"
    weight += f'<br><span style="{MUTED}">target {row.target_weight:.1f}%</span>'

    if row.drift_pp is None:
        drift = f'<span style="{MUTED}">—</span>'
    else:
        drift = f"{row.drift_pp:+.1f}pp"

    return (
        "<tr>"
        f'<td style="{TD};text-align:left;word-break:break-word">{name}</td>'
        f'<td style="{TD}">{last}</td>'
        f'<td style="{TD}">{week}</td>'
        f'<td style="{TD}">{weight}</td>'
        f'<td style="{TD}">{drift}</td>'
        "</tr>"
    )


def _html_notes(portfolio, explanation, config) -> str:
    if explanation is None or (not explanation.notes and not explanation.leftover):
        if portfolio.first_run or portfolio.unconfigured:
            return ""
        return (
            f'<p style="{MUTED};margin:0 0 20px 0">Nothing moved more than '
            f"{config.note_threshold_pct:g}% this week, so there is nothing to explain.</p>"
        )

    out = ['<h2 style="font-size:16px;margin:20px 0 8px 0">What moved, and why</h2>']
    by_ticker = {r.ticker: r for r in portfolio.rows}
    for ticker, note in explanation.notes.items():
        row = by_ticker.get(ticker)
        headline = ""
        if row is not None and row.week_change_pct is not None:
            headline = (
                f' <span style="color:{_change_colour(row.week_change_pct)};font-size:14px">'
                f"{row.week_change_pct:+.1f}%</span>"
            )
        out.append(
            f'<p style="margin:0 0 12px 0"><strong>{_e(ticker)}</strong>{headline}<br>'
            f"{_e(note)}</p>"
        )
    if explanation.leftover:
        out.append(f'<p style="margin:0 0 12px 0">{_e(explanation.leftover)}</p>')
    return "".join(out)


def group_not_evaluated(entries):
    """Collapse one-line-per-ticker into one line per (rule, reason)."""
    grouped: dict[tuple[str, str], list[str]] = {}
    for entry in entries:
        grouped.setdefault((entry.rule_name, entry.reason), []).append(entry.ticker)
    return [(name, ", ".join(tickers), reason) for (name, reason), tickers in grouped.items()]


def _html_alerts(alerts) -> str:
    if alerts is None:
        return ""
    out = []
    if alerts.triggered:
        out.append('<h2 style="font-size:16px;margin:20px 0 8px 0">Alerts</h2>')
        for trigger in alerts.triggered:
            out.append(
                f'<p style="margin:0 0 12px 0;padding:10px 12px;background:#f6f6f6;'
                f'border-radius:3px">'
                f"<strong>{_e(trigger.rule_name)}</strong><br>{_e(trigger.message)}<br>"
                f'<span style="{MUTED}">{_e(trigger.arithmetic)}</span></p>'
            )
    else:
        out.append(
            f'<p style="{MUTED};margin:20px 0 8px 0">No alert rules triggered.</p>'
        )

    if alerts.not_evaluated:
        items = "".join(
            f"<li>{_e(name)} ({_e(tickers)}) — {_e(reason)}</li>"
            for name, tickers, reason in group_not_evaluated(alerts.not_evaluated)
        )
        out.append(
            f'<div style="{WARN_BOX}"><strong>Rules that could not be checked</strong>'
            f'<ul style="margin:6px 0 0 18px;padding:0">{items}</ul></div>'
        )
    return "".join(out)


# --------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------

def render_text(config, portfolio, alerts, explanation, run_date: date, state_warning=None,
                explain_warning=None) -> str:
    lines: list[str] = []
    lines.append(config.portfolio_name)
    header = f"Week to {run_date.strftime('%A %-d %B %Y')}"
    if portfolio.previous_run:
        header += f" (since {portfolio.previous_run.isoformat()})"
    else:
        header += " (first run)"
    lines.append(header)
    lines.append("=" * len(header))
    lines.append("")

    for warning in portfolio.warnings:
        lines += ["! " + warning, ""]
    for err in config.config_errors:
        lines += ["! holdings.yaml: " + err, ""]
    if explain_warning:
        lines += ["! " + explain_warning, ""]
    if explanation is not None and explanation.degraded:
        lines += [
            "! Web search failed ("
            + ", ".join(explanation.search_errors)
            + "); notes are from the numbers alone.",
            "",
        ]
    if state_warning:
        lines += ["! " + state_warning, ""]

    if portfolio.unconfigured:
        lines += ["No portfolio value to report — nothing is configured yet.", ""]
    else:
        total = money(portfolio.total_value, config.currency)
        if portfolio.failed_tickers:
            total += f"  (excludes {', '.join(portfolio.failed_tickers)})"
        lines += [f"Total: {total}", ""]

    for row in portfolio.rows:
        price = f"{row.quote.price:,.2f}" if row.priced else "no price"
        week = pct(row.week_change_pct, signed=True) if row.week_change_pct is not None else "—"
        weight = pct(row.actual_weight) if row.actual_weight is not None else "—"
        drift = f"{row.drift_pp:+.1f}pp" if row.drift_pp is not None else "—"
        lines.append(
            f"{row.ticker:<10} {price:>10}  week {week:>7}  "
            f"weight {weight:>6} (target {row.target_weight:.1f}%, drift {drift})"
        )
    lines.append("")

    if explanation is not None and (explanation.notes or explanation.leftover):
        lines += ["WHAT MOVED, AND WHY", "-" * 19]
        for ticker, note in explanation.notes.items():
            lines += [f"{ticker}: {note}", ""]
        if explanation.leftover:
            lines += [explanation.leftover, ""]
    elif not portfolio.first_run and not portfolio.unconfigured:
        lines += [
            f"Nothing moved more than {config.note_threshold_pct:g}% this week.",
            "",
        ]

    if alerts is not None:
        if alerts.triggered:
            lines += ["ALERTS", "------"]
            for trigger in alerts.triggered:
                lines += [
                    f"* {trigger.rule_name}: {trigger.message}",
                    f"  {trigger.arithmetic}",
                ]
            lines.append("")
        else:
            lines += ["No alert rules triggered.", ""]
        if alerts.not_evaluated:
            lines += ["RULES THAT COULD NOT BE CHECKED", "-" * 31]
            for name, tickers, reason in group_not_evaluated(alerts.not_evaluated):
                lines.append(f"* {name} ({tickers}) — {reason}")
            lines.append("")

    lines.append(
        "Prices are last closes from Yahoo Finance via yfinance, which is a scraper "
        "and sometimes fails; anything it could not fetch is named above rather than "
        "filled in. Nothing here is advice."
    )
    return "\n".join(lines)
