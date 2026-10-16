"""Collection runs -> seven event types, with evidence, state, and detail data.

The event layer reads existing core runs (manifest.json, three CSVs, raw/) and
signal runs as they are; it never edits them and never fetches. See docs/events.md.
"""

EVENT_TYPES = ("price_change", "scheduled_change", "limited_discount", "condition_change",
               "model_release", "model_end", "route_change")
SCHEMA_VERSION = 1
DEFAULT_POLICY = "all_verified"
