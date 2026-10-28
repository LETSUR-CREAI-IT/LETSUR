"""Synthetic Anthropic pricing page for the Claude Haiku 5.5 prompt-length tier tests; it carries no network data."""

LOW = "Claude Haiku 5.5 (for prompts up to 100,000 tokens)"
HIGH = "Claude Haiku 5.5 (for prompts over 100,000 tokens)"
HEADER = "| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |"

# title -> (base input, 5m cache write, 1h cache write, cache hit, output) in USD per MTok, in table column order
RATES = {
    LOW: ("0.10", "0.125", "0.20", "0.01", "0.50"),
    HIGH: ("0.50", "0.625", "1", "0.05", "2.50"),
    "Claude Haiku 4.5": ("1", "1.25", "2", "0.10", "5"),
}


def table_row(title, rates):
    return "| " + " | ".join([title, *(f"${rate} / MTok" for rate in rates)]) + " |"


def page(rates=RATES) -> bytes:
    return "\n".join([
        "# Pricing", "## Model pricing", HEADER, "| --- | --- | --- | --- | --- | --- |",
        *(table_row(title, values) for title, values in rates.items()),
        "## Batch processing", "| Model | Batch input | Batch output |", "| --- | --- | --- |",
        f"| {LOW} | $0.05 / MTok | $0.25 / MTok |",
    ]).encode()


PAGE = page()
