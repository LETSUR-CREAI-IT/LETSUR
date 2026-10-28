"""Common judgement: observations and claims -> seven event types with detail data.

Rules (team taxonomy sections 1-3, user decisions of 2026-10-15):
- all_verified: any verified same-condition change from a source whose authority
  covers that route's price is a price_change; no size or repetition gate.
- Failures, missing rows, disabled sources, first observations, replays, and
  parser-only differences never create events.
- A claim that cannot be confirmed stays a candidate with gaps. Cause, LETSUR
  usage, and significance are unknown unless an input states them.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import DEFAULT_POLICY, SCHEMA_VERSION
from .catalog import Catalog, authority
from .evidence import (MAKER_PRICE_VIA_CHANNEL, MUTABLE_CONDITIONS, Reparser, comparability, core_observations, digest,
                       maker_list_price, parent_semantic, parser_fingerprint, series_key, signal_claims)
from .notices import NEGATED

OFFICIAL = ("manufacturer_official", "channel_official")
BILLING_KEYS = {"unit", "tier", "cache", "batch", "surcharge", "multiplier", "context_threshold", "cache_ttl"}
SUPPORT_KEYS = {"resolution", "input_format", "region", "context_limit", "license", "alias"}
_AMOUNT = r"\$\s?([0-9]+(?:\.[0-9]+)?)"
# before -> after: "$0.119 → $0.238", "from $X to $Y"; or an after amount alone: "cut to $0.27".
MOVE = re.compile(_AMOUNT + r"\s*(?:→|->|\bto\b)\s*" + _AMOUNT, re.I)
TO_AMOUNT = re.compile(r"\bto\s+" + _AMOUNT, re.I)
BLENDED = re.compile(r"\bblended\b", re.I)  # a blended rate is not the price of one billing item
PERCENT_OFF = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%\s?off", re.I)
END_PHRASE = re.compile(r"\b(?:through|until)\s+([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?")
# A condition_change of a billing policy needs the policy as stated by the source (a parser condition row with its
# locator, evidence.MUTABLE_CONDITIONS). The ratio of two observed prices is shown as derived information only: it is not
# a stated multiplier, so a base-only or cache-only price move stays a price_change.
VERIFIED = ("current_parser_both_raws", "stored_parse_of_before_raw")
# A sentence with a negation or cancellation word is not a positive claim, whoever publishes it.
NEGATED_GAP = {"field": "negated_statement", "class": "check_first",
               "reason": "the sentence negates, cancels or postpones the claim; it is not read as a positive statement",
               "next_check": "read the notice and confirm what is actually in effect"}
VERCEL_DEPRECATED_AT = re.compile(r"data\.endpoints\[\d+\]\.deprecated_at")  # signals parser vercel_endpoint_signals


def empty_state() -> dict:
    return {"schema_version": SCHEMA_VERSION, "policy": DEFAULT_POLICY, "processed_runs": [], "series": {},
            "baselines": [], "events": {}, "not_comparable": [], "gaps": [], "claims_seen": []}


def pct(before: str, after: str) -> str | None:
    old, new = Decimal(before), Decimal(after)
    if old == 0:
        return None
    return format(((new - old) / old * 100).quantize(Decimal("0.01")), "f")


def ref(item: dict) -> dict:
    return {key: item.get(key, "") for key in ("run_id", "source_id", "observation_id", "raw_path", "raw_sha256", "locator", "source_key", "url")}


def same_parser(prev: dict) -> bool:
    """The stored parse of the before raw came from the code that parses its spec now."""
    current = parser_fingerprint(prev["spec"])
    return current is not None and prev.get("parser_fingerprint") == current


def side(obs: dict) -> dict:
    return {"amount": obs["amount"], "currency": obs["currency"], "unit": obs["unit"], "observation_id": obs["observation_id"],
            "run_id": obs["run_id"], "observed_at_utc": obs["observed_at_utc"], "effective_from": obs.get("effective_from", ""),
            "effective_until": obs.get("effective_until", "")}


def base_event(event_id: str, event_type: str | None, model_id: str, route: str, status: str) -> dict:
    return {"event_id": event_id, "revision": 1, "event_type": event_type, "type_candidates": [], "subtags": [],
            "status": status, "review_status": "unreviewed", "model_id": model_id, "route": route,
            "source_key": "", "billing_item": "", "conditions": {}, "evidence_level": "", "before": None, "after": None,
            "change": {}, "unchanged_items": [], "comparison": {}, "dates": {}, "evidence_refs": [], "sources": [],
            "lineages": 0, "independence": "unknown", "values_by_source": {}, "conflicts": [], "related": [],
            "unknown_reason": "", "gaps": [], "next_checks": [], "cause": "unknown",
            "cause_detail": {"supplier_explanation": None, "independent_verification": None},
            "letsur_usage": "unknown", "significance": "unknown", "impact": "unknown",
            "suggestion": "", "history": []}


def evidence_level(auth: str) -> str:
    return "official" if auth in OFFICIAL else ("unofficial" if auth == "unofficial" else "unverified")


class Builder:
    def __init__(self, catalog: Catalog, state: dict | None = None) -> None:
        self.catalog = catalog
        self.state = state or empty_state()
        self.reparser = Reparser()
        self.run_dirs: dict[str, Path] = {}
        self.new_events: list[str] = []
        self.revised: list[str] = []
        self.notes: list[dict] = []
        self.as_of: str | None = None

    # ---- state helpers -------------------------------------------------
    def _put(self, event: dict, reason: str) -> None:
        events = self.state["events"]
        old = events.get(event["event_id"])
        if old is None:
            event["history"] = [{"revision": 1, "reason": reason}]
            events[event["event_id"]] = event
            self.new_events.append(event["event_id"])
            return
        comparable = {k: v for k, v in event.items() if k not in ("revision", "history")}
        if comparable == {k: v for k, v in old.items() if k not in ("revision", "history")}:
            return
        event["revision"] = old["revision"] + 1
        event["history"] = old["history"] + [{"revision": event["revision"], "reason": reason}]
        events[event["event_id"]] = event
        if event["event_id"] not in self.new_events:
            self.revised.append(event["event_id"])

    def _not_comparable(self, obs: dict, reason: str) -> None:
        item = {"source_key": obs["source_key"], "model_id": obs["model_id"], "route": obs["route"],
                "billing_item": obs["billing_item"], "reason": reason, "conditions": obs["conditions"],
                "unknown_conditions": obs["unknown_conditions"], "run_id": obs["run_id"], "observation_id": obs["observation_id"]}
        key = (item["source_key"], item["billing_item"], reason, digest(obs["semantic"]))
        if all((x["source_key"], x["billing_item"], x["reason"], x.get("_key")) != key for x in self.state["not_comparable"]):
            self.state["not_comparable"].append({**item, "_key": key[3]})

    # ---- core runs ----------------------------------------------------
    def core_run(self, run: dict) -> None:
        observations, statuses = core_observations(run, self.catalog)
        run_id = run["manifest"]["run_id"]
        for status in statuses:
            if status["status"] != "ok":
                self.state["gaps"].append({"kind": "source_" + status["status"], **status})
        current = [o for o in observations if o["price_kind"] == "published_rate"]
        future = [o for o in observations if o["price_kind"] == "announced_future_rate"]
        conflicts = self._conflicts(current)
        count: dict[str, int] = {}
        for obs in current:
            count[obs["series"]] = count.get(obs["series"], 0) + 1
        # Rows that differ only by a stated policy cannot be paired with the previous run: unknown, not a wildcard.
        ambiguous = {o["series"] for o in current if o["policy"] and count[o["series"]] > 1}
        for obs in current:
            reason = comparability(obs) or ("policy_pair_not_unique" if obs["series"] in ambiguous else "")
            if reason:
                self._not_comparable(obs, reason)
                continue
            self._diff(obs, current, conflicts)
        for obs in future:
            reason = comparability(obs)
            if reason or not obs["effective_from"]:
                self._not_comparable(obs, reason or "effective_date_missing")
                continue
            self._scheduled(obs, current, run_id)

    def _conflicts(self, current: list[dict]) -> dict[str, dict]:
        """Same model/route/item/conditions from different registered sources with different amounts."""
        groups: dict[str, dict[str, str]] = {}
        for obs in current:
            if comparability(obs):
                continue
            key = digest([obs["model_id"], obs["route"], obs["billing_item"], obs["currency"], obs["unit"], list(obs["semantic"])])
            groups.setdefault(key, {})[obs["source_key"]] = obs["amount"]
        return {key: values for key, values in groups.items() if len({Decimal(v) for v in values.values()}) > 1}

    def _stored(self, obs: dict, checks: dict | None = None) -> dict:
        parsed = self.reparser.amounts(self.run_dirs[obs["run_id"]], obs)
        policies = self.reparser.policies(self.run_dirs[obs["run_id"]], obs)
        return {"obs": {k: v for k, v in obs.items() if k not in ("spec", "semantic")}, "spec": obs["spec"],
                "semantic": [list(x) for x in obs["semantic"]], "rule_checks": checks or {},
                "reparsed_amount": None if parsed is None else parsed.get(obs["series"]),
                "reparsed_policy": None if policies is None else policies.get(obs["series"]),
                "parser_fingerprint": parser_fingerprint(obs["spec"])}

    def _rule_checks(self, obs: dict, run_obs: list[dict]) -> dict:
        """Each stated policy of obs against the observed prices of the same raw. The base item must be one unique row of
        the same source, series fields and stated conditions; otherwise the check is unknown. The ratio is derived only."""
        found = {}
        for key, stated in obs["policy"].items():
            base_item = MUTABLE_CONDITIONS[key]
            base_series = series_key(obs["source_key"], {**obs, "billing_item": base_item}, parent_semantic(obs["semantic"]))
            pairs = [o for o in run_obs if o["source_id"] == obs["source_id"] and o["series"] == base_series and not comparability(o)]
            check = {"stated_value": stated["value"], "stated_locator": stated["locator"], "base_item": base_item}
            if len(pairs) != 1 or Decimal(pairs[0]["amount"]) == 0:
                found[key] = dict(check, consistent=None, derived_ratio=None,
                                  reason="base item missing" if not pairs else ("base pair not unique" if len(pairs) > 1 else "base amount is zero"))
                continue
            derived = ratio(obs["amount"], pairs[0]["amount"])
            found[key] = dict(check, base_amount=pairs[0]["amount"], base_observation_id=pairs[0]["observation_id"],
                              derived_ratio=derived, consistent=Decimal(derived) == Decimal(stated["value"]))
        return found

    def _diff(self, obs: dict, run_obs: list[dict], conflicts: dict) -> None:
        series = self.state["series"]
        prev = series.get(obs["series"])
        checks = self._rule_checks(obs, run_obs)
        for key, check in checks.items():
            if check["consistent"] is False:  # kept visible; neither the rule nor the prices is picked
                self.state["gaps"].append({"kind": "stated_rule_price_mismatch", "source_key": obs["source_key"], "billing_item": obs["billing_item"],
                                           "condition_key": key, "observation_id": obs["observation_id"], **check,
                                           "note": "stated rule and observed prices of the same raw disagree"})
        if prev is None:
            series[obs["series"]] = self._stored(obs, checks)
            self.state["baselines"].append({"source_key": obs["source_key"], "model_id": obs["model_id"], "route": obs["route"],
                                            "billing_item": obs["billing_item"], "conditions": obs["conditions"],
                                            "observation_id": obs["observation_id"], "run_id": obs["run_id"]})
            return
        before = prev["obs"]
        old_policy = before.get("policy", {})
        moved = [key for key in obs["policy"] if key in old_policy
                 and Decimal(old_policy[key]["value"]) != Decimal(obs["policy"][key]["value"])]
        for key in old_policy:
            if key not in obs["policy"]:
                self.state["gaps"].append({"kind": "condition_value_unknown", "source_key": obs["source_key"], "billing_item": obs["billing_item"],
                                           "condition_key": key, "after_observation_id": obs["observation_id"],
                                           "note": "stated policy not found this run; not an end and not a condition change"})
        if moved:
            verdict = self._verify_policy(prev, obs, moved)
            series[obs["series"]] = self._stored(obs, checks)
            if verdict in ("parser_artifact", "csv_raw_mismatch"):
                self.state["gaps"].append({"kind": verdict, "source_key": obs["source_key"], "billing_item": obs["billing_item"],
                                           "condition_keys": moved,
                                           "before_observation_id": before["observation_id"], "after_observation_id": obs["observation_id"],
                                           "note": "CSV condition differs but preserved raw bodies do not show the change under the current parser"})
                return
            for key in moved:
                self._condition_event(prev, obs, key, checks.get(key), verdict)
            return
        if Decimal(before["amount"]) == Decimal(obs["amount"]):
            series[obs["series"]] = self._stored(obs, checks)
            return
        verdict = self._verify(prev, obs)
        stored = self._stored(obs, checks)
        series[obs["series"]] = stored
        if verdict in ("parser_artifact", "csv_raw_mismatch"):
            self.state["gaps"].append({"kind": verdict, "source_key": obs["source_key"], "billing_item": obs["billing_item"],
                                       "before_observation_id": before["observation_id"], "after_observation_id": obs["observation_id"],
                                       "note": "CSV value differs but preserved raw bodies do not show the change under the current parser"})
            return
        if verdict == "parser_fingerprint_mismatch":
            self.state["gaps"].append({"kind": verdict, "source_key": obs["source_key"], "billing_item": obs["billing_item"],
                                       "before_observation_id": before["observation_id"], "after_observation_id": obs["observation_id"],
                                       "note": "before raw not given and the stored parse came from different or unknown parser code"})
        self._price_event(before, obs, verdict, run_obs, conflicts)

    def _verify(self, prev: dict, obs: dict) -> str:
        before = prev["obs"]
        if before["raw_sha256"] and before["raw_sha256"] == obs["raw_sha256"]:
            return "parser_artifact"
        after_amounts = self.reparser.amounts(self.run_dirs[obs["run_id"]], obs)
        after_re = None if after_amounts is None else after_amounts.get(obs["series"])
        if after_re is None:
            return "parser_unverifiable"
        if Decimal(after_re) != Decimal(obs["amount"]):
            return "csv_raw_mismatch"
        before_dir = self.run_dirs.get(before["run_id"])
        if before_dir is not None:
            before_amounts = self.reparser.amounts(before_dir, {**before, "spec": prev["spec"]})
            before_re = None if before_amounts is None else before_amounts.get(before["series"])
            basis = "current_parser_both_raws"
        elif not same_parser(prev):
            return "parser_fingerprint_mismatch"
        else:
            before_re, basis = prev.get("reparsed_amount"), "stored_parse_of_before_raw"
        if before_re is None:
            return "parser_unverifiable"
        if Decimal(before_re) == Decimal(after_re):
            return "parser_artifact"
        if Decimal(before_re) != Decimal(before["amount"]):
            return "csv_raw_mismatch"
        return basis

    def _verify_policy(self, prev: dict, obs: dict, moved: list[str]) -> str:
        """Same checks as _verify on the stated policy rows and the item amount: CSV equals the current parse of each
        raw, and the parsed policy values themselves move."""
        before = prev["obs"]
        if before["raw_sha256"] and before["raw_sha256"] == obs["raw_sha256"]:
            return "parser_artifact"
        after_dir = self.run_dirs[obs["run_id"]]
        after_amounts, after_policies = self.reparser.amounts(after_dir, obs), self.reparser.policies(after_dir, obs)
        before_dir = self.run_dirs.get(before["run_id"])
        if before_dir is not None:
            parsed = self.reparser.amounts(before_dir, {**before, "spec": prev["spec"]})
            policies = self.reparser.policies(before_dir, {**before, "spec": prev["spec"]})
            b_amount = None if parsed is None else parsed.get(before["series"])
            b_policy = None if policies is None else policies.get(before["series"])
            basis = "current_parser_both_raws"
        elif not same_parser(prev):
            return "parser_fingerprint_mismatch"
        else:
            b_amount, b_policy, basis = prev.get("reparsed_amount"), prev.get("reparsed_policy"), "stored_parse_of_before_raw"
        a_amount = None if after_amounts is None else after_amounts.get(obs["series"])
        a_policy = None if after_policies is None else after_policies.get(obs["series"])
        if None in (a_amount, a_policy, b_amount, b_policy):
            return "parser_unverifiable"
        if Decimal(a_amount) != Decimal(obs["amount"]) or Decimal(b_amount) != Decimal(before["amount"]):
            return "csv_raw_mismatch"
        for key in moved:
            if key not in a_policy or key not in b_policy or Decimal(a_policy[key]) != Decimal(obs["policy"][key]["value"]) \
                    or Decimal(b_policy[key]) != Decimal(before["policy"][key]["value"]):
                return "csv_raw_mismatch"
            if Decimal(a_policy[key]) == Decimal(b_policy[key]):
                return "parser_artifact"
        return basis

    def _condition_event(self, prev: dict, obs: dict, key: str, check: dict | None, verdict: str) -> None:
        before = prev["obs"]
        event_id = "ev_" + digest(["condition_change", obs["series"], key, before["observation_id"], obs["observation_id"]])
        verified = verdict in VERIFIED
        checks = {"before": prev.get("rule_checks", {}).get(key), "after": check}
        contradicted = [name for name, item in checks.items() if item and item["consistent"] is False]
        event = base_event(event_id, "condition_change", obs["model_id"], obs["route"],
                           "confirmed" if verified and obs["authority"] in OFFICIAL and not contradicted else "candidate")
        delta = Decimal(obs["amount"]) - Decimal(before["amount"])
        old, now = before["policy"][key], obs["policy"][key]

        def condition_side(item: dict, stated: dict, item_check: dict | None) -> dict:
            item_check = item_check or {}
            return {"value": stated["value"], "unit": stated["unit"], "basis": "stated", "locator": stated["locator"],
                    "item_amount": item["amount"], "base_item": MUTABLE_CONDITIONS[key], "base_amount": item_check.get("base_amount"),
                    "derived_ratio": item_check.get("derived_ratio"), "rule_consistent_with_prices": item_check.get("consistent"),
                    "item_observation_id": item["observation_id"], "base_observation_id": item_check.get("base_observation_id"),
                    "run_id": item["run_id"], "raw_path": item["raw_path"], "raw_sha256": item["raw_sha256"],
                    "source_key": item["source_key"], "url": item["url"]}

        event.update(subtags=[f"billing.{key}"], source_key=obs["source_key"], billing_item=obs["billing_item"],
                     conditions={k: v for k, v in obs["conditions"].items() if k not in MUTABLE_CONDITIONS},
                     evidence_level=evidence_level(obs["authority"]), before=side(before), after=side(obs),
                     change={"condition_key": key, "before_value": old["value"], "after_value": now["value"],
                             "price_delta": format(delta, "f"), "price_pct": pct(before["amount"], obs["amount"]),
                             "price_direction": "up" if delta > 0 else ("down" if delta < 0 else "unchanged"),
                             "note": "the item's price change is part of this condition change; no separate price_change"},
                     comparison={"comparable": True, "basis": "same registered source, model, route, billing item, currency, unit, upstream "
                                 f"and every stated condition apart from the stated {key}, read from both raws",
                                 "raw_check": verdict},
                     dates={"observed_before_utc": before["observed_at_utc"], "observed_after_utc": obs["observed_at_utc"],
                            "published": None, "effective_from": None,
                            "note": "change happened between the two observations; no publication or effective date stated"},
                     evidence_refs=[ref(before), ref(obs)],
                     sources=[{"source_key": obs["source_key"], "authority": obs["authority"], "role": obs["role"], "scope": obs["scope"]}],
                     lineages=1, unknown_reason="cause, LETSUR usage, and contract price are not in the inputs",
                     suggestion="review cached-token cost on this route and condition before any pricing decision",
                     next_checks=["confirm the official source still shows the new condition on the next run"])
        event["condition"] = {"key": key, "basis": "stated by the source (locator); derived_ratio is item amount / base item amount "
                              "of the same raw, shown for reference and never used as the policy",
                              "before": condition_side(before, old, checks["before"]), "after": condition_side(obs, now, checks["after"])}
        for name in contradicted:
            event["conflicts"].append({"kind": "stated_rule_vs_prices", "side": name, **checks[name]})
            event["gaps"].append({"field": f"{name}.{key}", "class": "check_first", "reason": "stated rule and observed prices disagree",
                                  "next_check": "read the source page; neither value is picked"})
        for name, item in checks.items():
            if item is None or item["consistent"] is None:
                event["gaps"].append({"field": f"{name}.derived_ratio", "class": "limits_value",
                                      "reason": "the exact base item row is not known" if item is None else item["reason"],
                                      "next_check": "the stated rule is kept; the price consistency check is unknown"})
        if not verified:
            event["gaps"].append({"field": "raw_check", "class": "check_first", "reason": verdict,
                                  "next_check": "re-read preserved raw bodies with the current parser"})
        event["gaps"].append({"field": "letsur_usage", "class": "check_first", "reason": "no LETSUR usage or contract input",
                              "next_check": "confirm whether LETSUR uses this route and condition"})
        self._put(event, "verified change of a stated billing condition")

    def _price_event(self, before: dict, obs: dict, verdict: str, run_obs: list[dict], conflicts: dict) -> None:
        event_id = "ev_" + digest(["price_change", obs["series"], before["observation_id"], obs["observation_id"]])
        verified = verdict in ("current_parser_both_raws", "stored_parse_of_before_raw")
        status = "confirmed" if verified and obs["authority"] in OFFICIAL else "candidate"
        event = base_event(event_id, "price_change", obs["model_id"], obs["route"], status)
        delta = Decimal(obs["amount"]) - Decimal(before["amount"])
        event.update(source_key=obs["source_key"], billing_item=obs["billing_item"], conditions=obs["conditions"],
                     evidence_level=evidence_level(obs["authority"]), before=side(before), after=side(obs),
                     change={"delta": format(delta, "f"), "pct": pct(before["amount"], obs["amount"]),
                             "direction": "up" if delta > 0 else "down",
                             "note": "free_to_paid; pct not computed" if Decimal(before["amount"]) == 0 else ""},
                     comparison={"comparable": True, "basis": "same registered source, model, route, billing item, currency, unit, "
                                 "upstream and every stated condition", "raw_check": verdict},
                     dates={"observed_before_utc": before["observed_at_utc"], "observed_after_utc": obs["observed_at_utc"],
                            "published": None, "effective_from": None,
                            "note": "change happened between the two observations; no publication or effective date stated"},
                     evidence_refs=[ref(before), ref(obs)],
                     sources=[{"source_key": obs["source_key"], "authority": obs["authority"], "role": obs["role"],
                               "scope": obs["scope"]}],
                     lineages=1, unknown_reason="cause, LETSUR usage, and contract price are not in the inputs",
                     suggestion="review whether LETSUR uses this route and condition before any pricing decision")
        same_source = [o for o in run_obs if o["source_key"] == obs["source_key"] and o["series"] != obs["series"] and not comparability(o)]
        for other in same_source:
            prev = self.state["series"].get(other["series"])
            if prev and Decimal(prev["obs"]["amount"]) == Decimal(other["amount"]):
                event["unchanged_items"].append({"billing_item": other["billing_item"], "conditions": other["conditions"],
                                                 "amount": other["amount"], "observation_id": other["observation_id"]})
        group = digest([obs["model_id"], obs["route"], obs["billing_item"], obs["currency"], obs["unit"], list(obs["semantic"])])
        if group in conflicts:
            event["status"] = "conflicted"
            event["values_by_source"] = conflicts[group]
            event["conflicts"] = [{"kind": "registered_sources_disagree", "values_by_source": conflicts[group]}]
        for key, check in self._rule_checks(obs, run_obs).items():
            if check["consistent"] is False:  # the item's own stated rule disagrees with its prices: not confirmed
                event["status"] = "candidate" if event["status"] == "confirmed" else event["status"]
                event["conflicts"].append({"kind": "stated_rule_vs_prices", "condition_key": key, **check})
                event["gaps"].append({"field": key, "class": "check_first", "reason": "stated rule and observed prices disagree",
                                      "next_check": "read the source page; neither value is picked"})
        if not verified:
            event["gaps"].append({"field": "raw_check", "class": "check_first", "reason": verdict,
                                  "next_check": "re-read preserved raw bodies with the current parser"})
        event["gaps"].append({"field": "letsur_usage", "class": "check_first", "reason": "no LETSUR usage or contract input",
                              "next_check": "confirm whether LETSUR uses this route and condition"})
        event["next_checks"] = ["confirm the official source still shows the new value on the next run"]
        self._absorb_candidates(event)
        self._put(event, "verified same-condition price difference")

    def _scheduled(self, obs: dict, current: list[dict], run_id: str) -> None:
        if obs["authority"] not in OFFICIAL:
            # A future rate needs an official date and rate; an unofficial one is kept visible, not dropped.
            self._not_comparable(obs, "future_rate_not_official")
            return
        anchor_conditions = {k: v for k, v in obs["conditions"].items() if k != "rate_period"}
        now = [o for o in current if o["source_key"] == obs["source_key"] and o["billing_item"] == obs["billing_item"]
               and not comparability(o) and {k: v for k, v in o["conditions"].items() if k != "rate_period"} == anchor_conditions]
        event_id = "ev_" + digest(["scheduled_change", obs["source_key"], obs["model_id"], obs["route"], obs["billing_item"],
                                   sorted(anchor_conditions.items()), obs["upstream_provider"]])
        before = now[0] if len(now) == 1 else None
        event = base_event(event_id, "scheduled_change", obs["model_id"], obs["route"], "confirmed" if before else "candidate")
        event.update(source_key=obs["source_key"], billing_item=obs["billing_item"], conditions=anchor_conditions,
                     evidence_level=evidence_level(obs["authority"]), before=side(before) if before else None, after=side(obs),
                     change={"pct": pct(before["amount"], obs["amount"]) if before else None,
                             "delta": format(Decimal(obs["amount"]) - Decimal(before["amount"]), "f") if before else None},
                     comparison={"comparable": bool(before), "basis": "current and announced future rate of the same source, "
                                 "item and conditions apart from rate_period"},
                     dates={"current_rate_until": before.get("effective_until") if before else None,
                            "effective_from": obs["effective_from"], "published": None,
                            "first_observed_utc": obs["observed_at_utc"],
                            "note": "effective date is stated by the source; no time or timezone stated"},
                     evidence_refs=[ref(o) for o in ([before] if before else []) + [obs]],
                     sources=[{"source_key": obs["source_key"], "authority": obs["authority"], "role": obs["role"], "scope": obs["scope"]}],
                     lineages=1, unknown_reason="cause and LETSUR usage are not in the inputs",
                     suggestion="review cost on this route before the effective date",
                     next_checks=["re-observe right after the effective date to confirm the new rate is applied"])
        if not before:
            event["gaps"].append({"field": "before", "class": "check_first", "reason": "no single current rate with the same conditions",
                                  "next_check": "check the current rate on the source page"})
        old = self.state["events"].get(event_id)
        if old:  # same anchor: keep the event, carry the first observation forward
            event["dates"]["first_observed_utc"] = old["dates"].get("first_observed_utc", obs["observed_at_utc"])
            if old["after"] and (old["after"]["amount"], old["after"]["effective_from"]) == (obs["amount"], obs["effective_from"]):
                event["after"], event["before"], event["evidence_refs"] = old["after"], old["before"], old["evidence_refs"]
        self._put(event, "official future rate with a stated effective date")

    # ---- signal runs and claims --------------------------------------
    def signal_run(self, run: dict) -> None:
        claims, statuses, gaps = signal_claims(run, self.catalog)
        for status in statuses:
            if status["status"] != "ok":
                self.state["gaps"].append({"kind": "signal_source_" + status["status"], **status})
        self.state["gaps"].extend(gaps)
        for claim in claims:
            if claim["claim_id"] in self.state["claims_seen"]:
                continue
            self.state["claims_seen"].append(claim["claim_id"])
            self.claim(claim)

    def claim(self, claim: dict) -> None:
        if claim["kind"] == "availability" and claim.get("route_added_date") and not claim.get("maker_first_release_date"):
            # Only a confirmed maker release event supplies the first release date; never an observation date.
            release = [e["dates"].get("maker_first_release_date") for e in self.state["events"].values()
                       if e["event_type"] == "model_release" and e["status"] == "confirmed" and e["model_id"] == claim["model_id"]]
            if len(set(release)) == 1 and release[0]:
                claim = dict(claim, maker_first_release_date=release[0])
        event_type, subtags, status, gaps, extra = classify_claim(claim)
        if event_type is None and not extra.get("type_candidates"):
            return
        if claim.get("blockers"):
            status, gaps = "candidate", list(claim["blockers"]) + gaps
        if self._attach(claim, event_type):
            return
        anchor = claim.get("anchor") or claim["lineage"]
        via = claim.get("announced_via")
        # A card whose channel route was cleared keeps the event_id that route gave it, so a stored card is corrected in place.
        event_id = "ev_" + digest(["claim", claim["model_id"], via["route"] if via else claim.get("route", ""), event_type or "", anchor])
        event = base_event(event_id, event_type, claim["model_id"], claim.get("route", ""), status)
        event.update(subtags=subtags, type_candidates=extra.get("type_candidates", []), source_key=claim["source_key"],
                     evidence_level=evidence_level(claim["authority"]), evidence_refs=[ref(claim)],
                     sources=[{"source_key": claim["source_key"], "authority": claim["authority"], "role": claim["role"],
                               "scope": claim.get("scope", []), "lineage": claim["lineage"], "quote": claim["quote"]}],
                     lineages=1, gaps=gaps, dates=extra.get("dates", {}), change=extra.get("change", {}),
                     unknown_reason="cause, LETSUR usage, and exact scope are not in the inputs",
                     suggestion=extra.get("suggestion", "review the source before acting"),
                     next_checks=extra.get("next_checks", []))
        event["claim_kind"] = claim["kind"]
        event["anchor"] = anchor
        if via:
            event["announced_via"] = via
        event["billing_item"] = claim.get("billing_item", "")
        if claim.get("endpoint_id"):
            event["endpoint_id"] = claim["endpoint_id"]  # the one upstream endpoint of the route the claim is about
        if claim.get("before") or claim.get("after"):
            event["before"], event["after"] = claim.get("before"), claim.get("after")
            if event["before"] and event["after"] and event["before"].get("amount") and event["after"].get("amount"):
                event["change"] = dict(event["change"], pct=pct(event["before"]["amount"], event["after"]["amount"]),
                                       delta=format(Decimal(event["after"]["amount"]) - Decimal(event["before"]["amount"]), "f"))
        if claim.get("effective_from"):
            event["dates"] = dict(event["dates"], effective_from=claim["effective_from"])
        self._put(event, f"{claim['kind']} claim from {claim['source_key']}")

    def _attach(self, claim: dict, event_type: str | None) -> bool:
        """Attach a claim to an existing event only on a strong anchor: the same article sentence of the same
        claim kind, or an observed change on the claim's stated route whose direction and after amount it states."""
        anchor = claim.get("anchor") or claim["lineage"]
        for event in self.state["events"].values():
            if event["model_id"] != claim["model_id"] or event["event_type"] != event_type:
                continue
            # Same anchor = same article and sentence, same kind and route; a shared page alone is not enough.
            same_anchor = bool(event.get("anchor")) and event["anchor"] == anchor and event.get("claim_kind") == claim["kind"] \
                and event.get("route") == claim.get("route", "")
            amount_anchor = event_type == "price_change" and bool(claim.get("route")) and claim["route"] == event["route"] \
                and _states_change(claim["kind"], claim["quote"], event)
            if not same_anchor and not amount_anchor:
                continue
            if any(s["source_key"] == claim["source_key"] and s.get("quote") == claim["quote"] for s in event["sources"]):
                return True  # the same source restating the same sentence is a re-observation, not a revision
            updated = dict(event, sources=list(event["sources"]), evidence_refs=list(event["evidence_refs"]))
            updated["sources"].append({"source_key": claim["source_key"], "authority": claim["authority"], "role": claim["role"],
                                       "scope": claim.get("scope", []), "lineage": claim["lineage"], "quote": claim["quote"]})
            updated["evidence_refs"].append(ref(claim))
            updated["lineages"] = len({s.get("lineage") or s["source_key"] for s in updated["sources"]})
            self._put(updated, f"supporting {claim['kind']} claim from {claim['source_key']}")
            return True
        return False

    def _absorb_candidates(self, event: dict) -> None:
        """A later official price change confirms an earlier candidate only when the candidate names the same route
        and states the same direction and after amount. A candidate without a route is only linked as related."""
        target = None
        for old in list(self.state["events"].values()):
            if old["status"] != "candidate" or old.get("claim_kind") not in ("price_increase", "price_decrease"):
                continue
            if old["model_id"] != event["model_id"] or (old["route"] and old["route"] != event["route"]):
                continue
            if not any(_states_change(old["claim_kind"], s.get("quote", ""), event) for s in old["sources"]):
                continue
            if old["route"] and target is None:
                target = old
                continue
            event["related"].append({"event_id": old["event_id"], "relation": "candidate_states_same_change",
                                     "note": "linked, not merged" if old["route"] else "candidate does not state a route; linked, not merged"})
        if target is None:
            return
        official = dict(event, event_id=target["event_id"], sources=target["sources"] + event["sources"],
                        evidence_refs=target["evidence_refs"] + event["evidence_refs"])
        official["lineages"] = len({s.get("lineage") or s["source_key"] for s in official["sources"]})
        official["claim_kind"] = target["claim_kind"]
        event.clear()
        event.update(official)

    # ---- driver ---------------------------------------------------------
    def _clear_channel_routes(self) -> None:
        """Stored channel cards of a maker's list price move lose the channel route as signal_claims now reads them.
        Their claims are already seen and never re-read, so they are corrected here once, under the same event_id."""
        for event in list(self.state["events"].values()):
            if event.get("anchor") and event.get("claim_kind") in ("price_increase", "price_decrease") and event["route"] \
                    and "announced_via" not in event and event["sources"][0]["authority"] == "channel_official" \
                    and maker_list_price(event["sources"][0]["quote"], event["model_id"]):
                self._put(dict(event, route="", announced_via={"route": event["route"], "note": MAKER_PRICE_VIA_CHANNEL}),
                          "route cleared: " + MAKER_PRICE_VIA_CHANNEL)

    def build(self, runs: list[dict]) -> None:
        self._clear_channel_routes()
        processed = set(self.state["processed_runs"])
        for run in runs:
            self.run_dirs[run["manifest"]["run_id"]] = run["dir"]
            # The newest run date, skipped runs included, is the day a discount period is judged against.
            day = run["manifest"].get("created_at_utc", "")[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and (self.as_of is None or day > self.as_of):
                self.as_of = day
        for run in runs:
            run_id = run["manifest"]["run_id"]
            if run_id in processed:
                self.notes.append({"run_id": run_id, "note": "already processed; skipped"})
                continue
            processed.add(run_id)
            self.state["processed_runs"].append(run_id)
            if run["manifest"]["mode"] != "live":
                self.notes.append({"run_id": run_id, "note": "replay run; adds no observations"})
                continue
            if run["kind"] == "core":
                self.core_run(run)
            else:
                self.signal_run(run)


def ratio(amount: str, base: str) -> str:
    return format((Decimal(amount) / Decimal(base)).normalize(), "f")


def _after_amounts(text: str) -> list[str]:
    """After amounts only: the right side of "$a → $b" / "$a to $b", else "to $b". Before amounts never match."""
    moves = MOVE.findall(text or "")
    return [after for _, after in moves] if moves else TO_AMOUNT.findall(text or "")


def _states_change(kind: str, text: str, event: dict) -> bool:
    """The text states this observed change: same direction and after amount, and not a blended rate."""
    direction = {"price_increase": "up", "price_decrease": "down"}.get(kind)
    if not direction or direction != event["change"].get("direction") or not event["after"] or BLENDED.search(text or "") \
            or NEGATED.search(text or ""):
        return False
    return any(Decimal(amount) == Decimal(event["after"]["amount"]) for amount in _after_amounts(text))


def classify_claim(claim: dict) -> tuple[str | None, list[str], str, list[dict], dict]:
    """Shared claim classification for signal rows, manual entries, and legacy evidence."""
    kind, auth, quote = claim["kind"], claim["authority"], claim.get("quote", "")
    route = claim.get("route", "")
    official_route = auth in OFFICIAL and route and "route_price" in claim.get("scope", [])
    gaps: list[dict] = [dict(NEGATED_GAP)] if NEGATED.search(quote) else []
    negated = bool(gaps)
    extra: dict = {}
    if kind in ("discount", "discount_active", "free"):
        percent = PERCENT_OFF.search(quote)
        end = END_PHRASE.search(quote)
        stated_pct = "100" if kind == "free" else (percent.group(1) if percent else None)
        extra["change"] = {"discount_pct": stated_pct, "discount_pct_basis": "stated" if stated_pct else None,
                           "discounted_amount": claim.get("discount_amount")}
        extra["dates"] = {"published": claim.get("published_at_utc") or None, "end_phrase": end.group(0) if end else None,
                          "end_date": claim.get("end_date"), "note": "end year is not invented; phrase kept as written"}
        extra["suggestion"] = "check whether the discount applies to LETSUR's account, tier and region"
        if official_route and (stated_pct or claim.get("discount_amount")) and (end or claim.get("end_date")):
            status = "candidate" if negated else "confirmed"
        else:
            status = "candidate"
            gaps.append({"field": "discount_period_or_target", "class": "check_first",
                         "reason": "period, target or route authority not stated by an official source for this route",
                         "next_check": "find the channel's official discount terms"})
        if end and not claim.get("end_date") and not re.search(r"\d{4}", end.group(0)):
            gaps.append({"field": "end_date_year", "class": "limits_value", "reason": "the end phrase states no year; none is inferred",
                         "next_check": "confirm the end year on the channel's discount terms"})
        if kind == "discount_active":
            gaps.append({"field": "sub_provider", "class": "limits_value",
                         "reason": "discount is on one upstream endpoint inside the channel, not the whole route",
                         "next_check": "check which upstream endpoint LETSUR would be served by"})
        gaps.append({"field": "account_actual_price", "class": "check_first", "reason": "account, tier and region not verified",
                     "next_check": "confirm the price on LETSUR's account"})
        return "limited_discount", [], status, gaps, extra
    if kind in ("price_increase", "price_decrease"):
        extra["dates"] = {"published": claim.get("published_at_utc") or None}  # the day event_groups joins cards on
        gaps.append({"field": "observed_price", "class": "check_first",
                     "reason": "text mentions a price move; no same-condition observed values yet",
                     "next_check": "wait for or re-run the registered price source"})
        return "price_change", [], "candidate", gaps, extra
    if kind == "deprecation":
        if claim.get("source_key", "").startswith("openrouter_endpoints") and claim.get("locator") == "data.expiration_date":
            extra["dates"] = {"effective_from": claim.get("date_phrases") or None, "published": None,
                              "note": "channel API expiration_date of the model listing on this route; call stop not confirmed"}
            extra["suggestion"] = "prepare an alternative route before the expiration date"
            gaps.append({"field": "shutdown_confirmed", "class": "limits_value", "reason": "expiration date stated; call stop not observed",
                         "next_check": "re-check the endpoint after the date"})
            return "route_change", ["remove"], "confirmed" if auth == "channel_official" and not negated else "candidate", gaps, extra
        gaps.append({"field": "scope", "class": "check_first",
                     "reason": "feed text matched a retirement word; model vs endpoint vs route scope not verified",
                     "next_check": "read the notice and confirm what ends and when"})
        if auth == "manufacturer_official":
            return "model_end", [], "candidate", gaps, extra
        return "route_change", ["remove"], "candidate", gaps, extra
    if kind == "route_removal":
        extra["dates"] = {"effective_from": claim.get("effective_from"), "published": claim.get("published_at_utc") or None,
                          "note": "channel notice of removal from its own route; not a model end"}
        gaps.append({"field": "shutdown_confirmed", "class": "limits_value", "reason": "removal notice only",
                     "next_check": "confirm call stop on this route after the date"})
        return "route_change", ["remove"], "confirmed" if official_route and claim.get("effective_from") and not negated else "candidate", gaps, extra
    if kind == "announced_future_rate":
        if official_route and claim.get("effective_from") and claim.get("after_amount"):
            return "scheduled_change", [], "candidate" if negated else "confirmed", gaps, extra
        gaps.append({"field": "effective_from_or_rate", "class": "check_first", "reason": "future date and new rate both required",
                     "next_check": "find an official statement with both"})
        return "scheduled_change", [], "candidate", gaps, extra
    if kind == "condition":
        key = claim.get("condition_key", "")
        group = "billing" if any(token in key for token in BILLING_KEYS) else ("support" if any(token in key for token in SUPPORT_KEYS) else "")
        if not group:
            gaps.append({"field": "subtag", "class": "check_first", "reason": f"condition {key!r} has no billing/support tag",
                         "next_check": "tag the condition"})
            return "condition_change", [], "candidate", gaps, extra
        status = "confirmed" if official_route and not negated else "candidate"
        return "condition_change", [f"{group}.{key}"], status, gaps, extra
    if kind == "endpoint_deprecation":
        if claim.get("source_key", "").startswith("vercel_endpoints") and VERCEL_DEPRECATED_AT.fullmatch(claim.get("locator", "")):
            # Vercel's API documentation defines only status 0 and does not list deprecated_at: never confirmed from it (2026-10-28).
            extra["dates"] = {"effective_from": claim.get("date_phrases") or None, "published": None,
                              "note": "deprecated_at of one upstream endpoint on this route (other upstreams of the route may "
                                      "continue); not a route or model shutdown"}
            extra["suggestion"] = "check which upstream LETSUR is served by and prepare another upstream or route before the date"
            gaps.append({"field": "undocumented_field", "class": "check_first",
                         "reason": "deprecated_at is absent from Vercel's API documentation; the end date is read from the field name only",
                         "next_check": "confirm the end date with Vercel (model page, changelog or support)"})
            gaps.append({"field": "shutdown_confirmed", "class": "limits_value", "reason": "end date from an undocumented field; call stop not observed",
                         "next_check": "re-check the endpoint after the date"})
            return "route_change", ["remove"], "candidate", gaps, extra
        extra["dates"] = {"effective_from": claim.get("effective_from"), "published": claim.get("published_at_utc") or None,
                          "note": "deprecation date of one endpoint on this route; not an API shutdown date"}
        gaps.append({"field": "shutdown_confirmed", "class": "limits_value", "reason": "deprecation notice only",
                     "next_check": "confirm call stop after the date"})
        if "model_lifecycle" in claim.get("scope", []) and claim.get("model_wide"):
            return "model_end", [], "candidate" if negated else "confirmed", gaps, extra
        return "route_change", ["remove"], "confirmed" if official_route and not negated else "candidate", gaps, extra
    if kind == "availability":
        release = claim.get("maker_first_release_date")
        added = claim.get("route_added_date")
        extra["dates"] = {"maker_first_release_date": release, "route_added_date": added,
                          "note": "the 7-day release bundle uses the maker's official first release date, never an observation date"}
        if release and added:
            from datetime import date
            within = (date.fromisoformat(added) - date.fromisoformat(release)).days <= 7
            return ("model_release" if within else "route_change"), ([] if within else ["add"]), \
                "confirmed" if official_route and not negated else "candidate", gaps, extra
        gaps.append({"field": "maker_first_release_date", "class": "check_first",
                     "reason": "no official first release date from the model maker",
                     "next_check": "find the maker's official release announcement"})
        extra["type_candidates"] = ["model_release", "route_change"]
        return None, ["add"], "candidate", gaps, extra
    return None, [], "candidate", gaps, extra


def legacy_authority(model_id: str, route: str) -> tuple[str, list[str]]:
    level = authority(model_id, route)
    return level, ["route_price", "route_availability"] + (["model_lifecycle"] if level == "manufacturer_official" else [])


def discount_period(event: dict, as_of: str | None) -> tuple[str | None, dict | None]:
    """(period_state, period_end) of a limited discount that states an end; (None, None) for any other event.
    An end phrase without a year takes the publication year, or the next year when that day falls before publication.
    The end day itself still counts as active. Derived at output only, so the stored event and its revision stay as they are."""
    dates = event["dates"]
    if event["event_type"] != "limited_discount" or not (dates.get("end_date") or dates.get("end_phrase")):
        return None, None
    end, basis = dates.get("end_date"), "stated"
    if not end:
        month, day, year = END_PHRASE.search(dates["end_phrase"]).groups()
        published = (dates.get("published") or "")[:10]
        basis = "stated" if year else "derived_from_publication"
        try:
            found = datetime.strptime(f"{month} {day} {year or published[:4]}", "%B %d %Y").date()
            if not year and found.isoformat() < published:
                found = found.replace(year=found.year + 1)
            end = found.isoformat()
        except ValueError:  # no publication date to take the year from, or not a calendar day
            end, basis = None, None
    state = "unknown" if end is None or as_of is None else ("ended" if as_of > end else "active")
    return state, {"date": end, "basis": basis, "as_of": as_of}


GROUP_WINDOW_DAYS = 3  # user decision 2026-10-19: cards without a stated date join within 3 days of the group's first card


def _group_day(event: dict) -> str | None:
    """The day a card was published, else the day it was first seen: a stated route date, an observation, or its first run."""
    dates = event["dates"]
    run_id = event["evidence_refs"][0].get("run_id", "") if event["evidence_refs"] else ""
    run_day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}" if re.match(r"\d{8}T", run_id) else ""
    for value in [dates.get(key) or "" for key in ("published", "route_added_date", "observed_after_utc", "first_observed_utc")] + [run_day]:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    return None


def event_groups(events: list[dict]) -> dict[str, tuple[str, str]]:
    """{event_id: (group_key, group_role)}: cards about one occurrence share a key whatever their route (user decision
    2026-10-19). Same model, type and subtags, and the same stated end (discount) or effective date; without one, a card
    day within GROUP_WINDOW_DAYS of the group's first card. A discount also needs the same rate, but endpoint discounts join
    one another whatever their rate; a price change needs the same direction. The maker's own card represents the group,
    else the earliest card. Derived at output like period_state; never stored, so a key can change when an earlier card arrives."""
    days = {event["event_id"]: _group_day(event) for event in events}
    pools: dict[str, tuple[list, object, list[dict]]] = {}
    for event in events:
        kind, change = event.get("claim_kind"), event["change"]
        value: list = []
        if event["event_type"] == "limited_discount":
            value = ["endpoint"] if kind == "discount_active" else [change.get("discount_pct"), change.get("discounted_amount")]
        elif event["event_type"] == "price_change":
            value = [change.get("direction") or {"price_increase": "up", "price_decrease": "down"}.get(kind)]
        stated = (event["period_end"] or {}).get("date") if event["event_type"] == "limited_discount" else event["dates"].get("effective_from")
        signature = [event["model_id"], event["event_type"] or sorted(event["type_candidates"]), sorted(event["subtags"]), value]
        pools.setdefault(digest(signature + [stated]), (signature, stated, []))[2].append(event)
    roles: dict[str, tuple[str, str]] = {}
    for signature, stated, members in pools.values():
        clusters: list[tuple[list, list[dict]]] = [(["stated", stated], members)] if stated else []
        if not stated:
            for event in sorted((e for e in members if days[e["event_id"]]), key=lambda e: (days[e["event_id"]], e["event_id"])):
                day = days[event["event_id"]]
                if clusters and (datetime.fromisoformat(day) - datetime.fromisoformat(clusters[-1][0][1])).days <= GROUP_WINDOW_DAYS:
                    clusters[-1][1].append(event)
                else:
                    clusters.append((["seen", day], [event]))
            clusters += [(["alone", e["event_id"]], [e]) for e in members if not days[e["event_id"]]]
        for anchor, cards in clusters:
            key = "grp_" + digest(signature + [anchor])
            lead = min(cards, key=lambda e: (not any(s.get("authority") == "manufacturer_official" for s in e["sources"]),
                                             days[e["event_id"]] or "9999-12-31", e["event_id"]))
            for event in cards:
                roles[event["event_id"]] = (key, "representative" if event is lead else "member")
    return roles


def output(builder: Builder) -> tuple[dict, dict]:
    state = builder.state
    events = []
    for event in sorted(state["events"].values(), key=lambda e: e["event_id"]):
        period_state, period_end = discount_period(event, builder.as_of)
        events.append(dict(event, period_state=period_state, period_end=period_end))
    roles = event_groups(events)
    for event in events:  # copies: the stored events and their revisions stay as they are
        event["group_key"], event["group_role"] = roles[event["event_id"]]
    not_comparable = [{k: v for k, v in item.items() if k != "_key"} for item in state["not_comparable"]]
    events_json = {"schema_version": SCHEMA_VERSION, "policy": state["policy"], "baselines": state["baselines"],
                   "events": events, "not_comparable": not_comparable, "gaps": state["gaps"]}
    counts: dict[str, int] = {}
    active: dict[str, int] = {}  # ended discounts stay listed and counted above; they are left out here only
    for event in events:
        key = f"{event['event_type'] or 'unclassified'}:{event['status']}"
        counts[key] = counts.get(key, 0) + 1
        if event["period_state"] != "ended":
            active[key] = active.get(key, 0) + 1
    report = {"status": "ok", "policy": state["policy"], "processed_runs": state["processed_runs"],
              "new_events": builder.new_events, "revised_events": builder.revised, "event_counts": counts,
              "active_event_counts": active, "notes": builder.notes, "not_comparable": len(not_comparable), "gaps": len(state["gaps"])}
    return events_json, report
