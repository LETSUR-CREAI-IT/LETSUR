"""Synthetic Gemini API pricing page in the markup the live page ships (no raw page is stored)."""

HEAD = '<tr><th></th><th scope="col">Free Tier</th><th scope="col">Paid Tier, per 1M tokens in USD</th></tr>'
LABELS = ("Input price", "Output price (including thinking tokens)", "Context caching price")
IMPLICIT_ONLY = "Implicit caching only (explicit caching is not supported)."
FLASH38_STORAGE = ("$0.50 / 1,000,000 tokens per hour (storage price) through December 31, 2026.<br>"
                   "$1.00 / 1,000,000 tokens per hour (storage price) starting January 1, 2027.")
# Gemini 4 Argon steps up on 2026-12-01; per tier: (current, future) for input, output and cache read.
ARGON_RATES = {
    "standard": (("2.00", "4.00"), ("10.00", "20.00"), ("0.10", "0.20")),
    "batch": (("1.00", "2.00"), ("5.00", "10.00"), ("0.05", "0.10")),
    "flex": (("1.00", "2.00"), ("5.00", "10.00"), ("0.05", "0.10")),
    "priority": (("3.60", "7.20"), ("18.00", "36.00"), ("0.18", "0.36")),
    "contributor": (("1.00", "2.00"), ("5.00", "10.00"), ("0.05", "0.10")),
}
GROUNDING = ("Grounding with Google Search<sup>*</sup>", "5,000 free search requests per month, then $14 per 1,000 requests.")


def table(*rows):
    body = "".join(f"<tr><td>{label}</td><td>Free of charge</td><td>{cell}</td></tr>" for label, cell in rows)
    return f'<table class="pricing-table"><thead>{HEAD}</thead><tbody>{body}</tbody></table>'


def section(title, ident, tiers):
    """One model: an h2 followed by one h3 + table per tier. tiers are (tier name, table rows)."""
    parts = "".join(f'<section><h3 id="{tier}_1">{tier.title()}</h3>{table(*rows)}</section>' for tier, rows in tiers)
    return f'<h2 id="{ident}" data-text="{title}">{title}</h2>{parts}'


def argon_section(cache_note=IMPLICIT_ONLY):
    tiers = []
    for tier, rates in ARGON_RATES.items():
        cells = [f"${now} through November 30, 2026.<br>${later} starting December 1, 2026." for now, later in rates]
        if cache_note:
            cells[2] += f"<br>{cache_note}"
        rows = list(zip(LABELS, cells))
        tiers.append((tier, [*rows, GROUNDING] if tier == "standard" else rows))
    return section("Gemini 4 Argon", "gemini-4-argon", tiers)


def flash38_section():
    standard = [("Input price", "$0.75 through December 31, 2026.<br>$1.50 starting January 1, 2027."),
                ("Output price (including thinking tokens)", "$3.75 through December 31, 2026.<br>$7.50 starting January 1, 2027."),
                ("Context caching price", "$0.075 through December 31, 2026.<br>$0.15 starting January 1, 2027.<br>" + FLASH38_STORAGE),
                GROUNDING]
    batch = [("Input price", "$0.375 through December 31, 2026.<br>$0.75 starting January 1, 2027."),
             ("Context caching price", "$0.0375 through December 31, 2026.<br>$0.075 starting January 1, 2027.<br>" + FLASH38_STORAGE)]
    contributor = [("Input price", "$0.10 through December 31, 2026.<br>$0.20 starting January 1, 2027.")]
    return section("Gemini 3.8 Flash", "gemini-3.8-flash", [("standard", standard), ("batch", batch), ("contributor", contributor)])


def neighbour_section(title, ident, price):
    """A neighbouring model whose rates a parser must never borrow."""
    return section(title, ident, [("standard", [("Input price", f"${price} through December 31, 2026.<br>${price} starting January 1, 2027.")])])


def page(*sections):
    return ("<html><body><h1>Gemini Developer API pricing</h1>" + "".join(sections) + "</body></html>").encode()


PAGE = page(argon_section(), flash38_section(), neighbour_section("Gemini 3.6 Flash", "gemini-3.6-flash", "8.88"))
