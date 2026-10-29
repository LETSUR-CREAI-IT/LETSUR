"""Re-detect which upstream route LETSUR's gateway uses for each model and propose an update of the routes file.

    LETSUR_API_BASE=<gateway url> LETSUR_API_KEY=<key> python3 -B -m scripts.market_collection.letsur_routes \\
        [--models id[,id...]] [--include-images] [--routes FILE] [--output FILE] [--write]

No gateway endpoint names a model's route, so each model gets one tiny request per endpoint it needs (no retries) and the
route is read from what the upstream leaves in the answer: the named deployment in a header, ids, provider fields,
content-filter fields. An answer with no such mark leaves the model unresolved and the note keeps the evidence. Video
generation is never requested and image generation only with --include-images (both cost money). The base URL and the key
come only from the environment and are never printed or written. The proposal goes to --output; --routes is rewritten only
with --write.

LETSUR's route for a model changes over days and can spread over several routes, so a run adds to the file instead of
replacing: a route seen for the first time becomes a record of its own with that day's checked_at, a route seen again only
gets a new checked_at, and the older records of other routes stay with their own dates. Only observed and inferred records
are touched (an inferred one is replaced by an observed one of the same route); settings and manual records never are. Only
models in our registry (models.py, plus ALIASES for LETSUR's other ids) are asked and recorded; the rest of LETSUR's list is
named in the diff only. Records get note and checked_at (the run date, KST); models with no route anywhere go to the top-level
"unresolved" map as {model_id: note}, and an entry already there is left as it is. No record gets an endpoint_id: OpenRouter
and Vercel pick a sub-provider per request, so the one seen goes into the note, which is plain Korean because the admin's
route tab shows it as written.
Exit codes: 0 done, 1 no model gave a usable answer (nothing written), 2 refused (bad setup or the key was refused).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.market_collection.models import MODEL_MODALITIES

BASE_ENV, KEY_ENV = "LETSUR_API_BASE", "LETSUR_API_KEY"
DEFAULT_ROUTES = Path("samples/market_collection/demo_internal_routes.json")
DEFAULT_OUTPUT = Path("data/market_collection/letsur_routes.proposed.json")
KST = timezone(timedelta(hours=9))
MAX_BODY_BYTES = 8 * 1024 * 1024  # one low-quality gpt-image answer is about 1 MB of base64
LONG_TEXT = 200  # longer strings (image data, reasoning text) are cut: nothing here reads them
KEEP_HEADERS = ("x-letsur-ai-gateway-model-id", "x-letsur-ai-gateway-response-cost")
MANAGED = ("observed", "inferred")  # the bases this tool writes; settings and manual records are a person's and stay
ALIASES = {"claude-haiku-4-5-20251001": "claude-haiku-4-5", "byteplus-seedance-2.5": "seedance-2.5"}  # LETSUR's id -> the one our registry uses
ASK = "Reply with: ok"
ASK_NO_DRAW = "Reply with the word ok only. Do not draw."  # a Gemini image model would otherwise answer with a picture
PATHS = {"chat": "/v1/chat/completions", "messages": "/v1/messages", "responses": "/v1/responses",
         "images": "/v1/images/generations", "embeddings": "/v1/embeddings"}
OPENAI_FAMILY = re.compile(r"(gpt-|o\d)")
# The header model id of a named deployment reads <model>-<where>: -azure-<region> (11 seen on 10/28), -bedrock-<region> (3 on
# 10/30) and -openai[-<account>] (gpt-6.1-terra-openai-letsur-2 on 10/28). -vertex-<region> has not been seen; it is the same naming.
DEPLOYMENTS = (
    (re.compile(r".+?-azure-([a-z0-9][a-z0-9-]*)"), "azure_foundry", lambda where: f"Azure 배포 지역 {where}"),
    (re.compile(r".+?-bedrock-([a-z0-9][a-z0-9-]*)"), "aws_bedrock", lambda where: f"Bedrock 배포 지역 {where}"),
    (re.compile(r".+?-vertex-([a-z0-9][a-z0-9-]*)"), "google_vertex", lambda where: f"Vertex 배포 지역 {where}"),
    (re.compile(r".+?-openai(?:-([a-z0-9][a-z0-9-]*))?"), "openai_direct", lambda where: f"OpenAI 직접 배포 {where}" if where else "OpenAI 직접 배포"),
)
AZURE_KEYS = ("prompt_filter_results", "content_filter_results", "content_filters", "serving_pipereplica")
OPENROUTER_ID = re.compile(r"gen-\d+-")
GEMINI_ID = re.compile(r"[A-Za-z0-9_-]{20,26}")
OPENAI_CHAT_ID = re.compile(r"chatcmpl-[A-Za-z0-9]{20,40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHAT_UUID_ID = re.compile("chatcmpl-" + UUID.pattern)  # an id the gateway made itself
BEDROCK_USAGE = {"cache_creation_input_tokens", "cache_read_input_tokens"}  # usage fields of the Anthropic/Bedrock style
PLAIN_FIELDS = {"id", "object", "created", "created_at", "model", "choices", "usage", "estimated_cost", "data", "type",
                "role", "content", "stop_reason", "stop_sequence"}  # fields every answer has: not worth a note
# Notes below and in the rules are Korean: they are shown to the admin as written.
NOT_PROBED = {"video": "렛서 영상 API는 자체 입력 형식이라 생성 없이 경로가 드러나지 않음(호출하지 않음)",
              "image": "이미지 생성은 비용이 들어 호출하지 않음"}
DIFF_PARTS = ("new_route", "upgraded", "re_seen", "older", "covered", "unresolved", "no_answer", "not_registered")


class GatewayError(Exception):
    """The whole run must stop: the gateway refused the key or the model list could not be read."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None  # urllib would resend the Authorization header to wherever the answer points


# ---- what to ask -------------------------------------------------------------------------------------------------------

def kind(model: str, modality=()) -> str:
    """Which requests a model takes. Video and image models cost money to answer, so they are told apart first."""
    if "video" in modality or model.startswith(("kling-", "byteplus-seedance", "seedance", "veo-")):
        return "video"
    if "embedding" in model:
        return "embedding"
    if model.startswith("gpt-image"):
        return "image"
    return "claude" if model.startswith("claude-") else "chat"


def plan(model: str, model_kind: str, include_images: bool) -> list[str]:
    """The endpoints to ask, in order; the next one is asked only while the ones before did not settle the route."""
    if model_kind == "chat":
        return ["chat", "responses"] if OPENAI_FAMILY.match(model) else ["chat"]
    return {"claude": ["messages"], "embedding": ["embeddings"], "image": ["images"] if include_images else [], "video": []}[model_kind]


def payload(name: str, model: str) -> dict:
    ask = ASK_NO_DRAW if "image" in model or "nano-banana" in model else ASK
    return {"chat": {"model": model, "messages": [{"role": "user", "content": ask}], "max_tokens": 16},
            "messages": {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": ASK}]},
            "responses": {"model": model, "input": ASK, "max_output_tokens": 16},
            "images": {"model": model, "prompt": "a small red dot on white", "size": "1024x1024", "quality": "low", "n": 1},
            "embeddings": {"model": model, "input": "ok"}}[name]


# ---- the gateway -------------------------------------------------------------------------------------------------------

def mask(text: str, secrets) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def slim(value, secrets):
    """A parsed body with the secrets masked and long strings cut to a marker, so no later step can keep an image or the key."""
    if isinstance(value, str):
        value = mask(value, secrets)
        return value if len(value) <= LONG_TEXT else f"{value[:32]}<{len(value)} chars>"
    if isinstance(value, list):
        return [slim(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: slim(item, secrets) for key, item in value.items()}
    return value


class Gateway:
    """Every answer comes back as {status, headers, body, error}: only the two headers read here, the body parsed and
    slimmed, error the exception name of a request that got no answer."""

    def __init__(self, base: str, key: str, timeout: float, opener=None):
        self.base, self.key, self.timeout = base.rstrip("/"), key, timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect)

    def request(self, path: str, body: dict | None = None, anthropic: bool = False) -> dict:
        auth = {"x-api-key": self.key, "anthropic-version": "2023-06-01"} if anthropic else {"Authorization": f"Bearer {self.key}"}
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", **auth}, method="GET" if body is None else "POST")
        try:
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    status, headers, raw = response.status, response.headers, response.read(MAX_BODY_BYTES)
            except urllib.error.HTTPError as exc:
                status, headers, raw = exc.code, exc.headers, exc.read(MAX_BODY_BYTES)
        except (OSError, http.client.HTTPException) as exc:  # URLError and timeouts are OSErrors, also while reading an error body
            return {"status": None, "headers": {}, "body": None, "error": type(exc).__name__}
        if status == 401:
            raise GatewayError("HTTP 401: the gateway refused LETSUR_API_KEY")
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        return {"status": status, "headers": {name.lower(): mask(value, (self.key, self.base)) for name, value in headers.items()
                                              if name.lower() in KEEP_HEADERS},
                "body": slim(parsed, (self.key, self.base)) if isinstance(parsed, dict) else None, "error": None}

    def probe(self, name: str, model: str) -> dict:
        return self.request(PATHS[name], payload(name, model), anthropic=name == "messages")

    def models(self) -> list[tuple[str, tuple]]:
        answer = self.request("/v1/models")
        data = (answer["body"] or {}).get("data")
        if answer["status"] != 200 or not isinstance(data, list):
            raise GatewayError(f"the model list could not be read ({answer['error'] or failure(answer)})")
        return [(item["id"], tuple(item.get("modality") or ())) for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]


# ---- reading an answer -------------------------------------------------------------------------------------------------

def walk(value):
    """Every dict inside a parsed body, the body itself first."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def first(body, key):
    """The first value stored under `key` at any depth, or None."""
    return next((node[key] for node in walk(body) if key in node), None)


def error_code(body) -> str:
    """LETSUR's own error code (error.code, else a type), never a message: the gateway masks upstream errors and a message
    could echo anything. A type may be a URL, so only its last segment is kept."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    for value in (*((error.get("code"), error.get("type")) if isinstance(error, dict) else ()), body.get("code"), body.get("type")):
        if isinstance(value, str) and re.fullmatch(r"[\w.-]{1,64}", value.rsplit("/", 1)[-1]):
            return value.rsplit("/", 1)[-1]
    return ""


def failure(answer: dict) -> str:
    """The status and LETSUR's error code, or the exception name when nothing answered; never a message."""
    if answer.get("status") is None:
        return f"응답 없음({answer['error']})" if answer.get("error") else "응답 없음"
    code = error_code(answer.get("body"))
    return f"{answer['status']}" + (f" {code}" if code else "")


def id_shape(value) -> str:
    """An id without its random part, enough in a note to tell id families apart; "" when there is no id."""
    text = UUID.sub("<uuid>", value) if isinstance(value, str) else ""
    prefix = re.match(r"[A-Za-z]+[-_]", text)
    prefix = prefix.group() if prefix else ""
    return text if not text or "<uuid>" in text else f"{prefix}<{len(text) - len(prefix)}자>"


def named_deployment(model, ok):
    for answer in ok.values():
        header = answer["headers"].get("x-letsur-ai-gateway-model-id", "")
        for pattern, route, note in DEPLOYMENTS:
            match = pattern.fullmatch(header)
            if match:
                return route, None, "observed", note(match[1])


def openrouter(model, ok):
    for answer in ok.values():
        body = answer["body"]
        provider = body["provider"] if isinstance(body.get("provider"), str) else ""
        usage = body["usage"] if isinstance(body.get("usage"), dict) else {}
        if OPENROUTER_ID.match(str(body.get("id"))) and (provider or "is_byok" in usage):
            return "openrouter", None, "observed", f"이번 호출의 하위 공급자 {provider}(요청마다 바뀔 수 있음)" if provider else ""


def vercel(model, ok):
    for answer in ok.values():
        gateway = first(answer["body"], "gateway")
        routing = gateway.get("routing") if isinstance(gateway, dict) else None
        final, resolved = (routing or {}).get("finalProvider"), (routing or {}).get("resolvedProvider")
        if isinstance(routing, dict) and (final or resolved):
            planned = f"계획 {resolved}, " if final and resolved and final != resolved else ""  # the planned one failed, another answered
            return "vercel_ai_gateway", None, "observed", f"이번 호출의 하위 공급자 {final or resolved}({planned}요청마다 바뀔 수 있음)"


def anthropic(model, ok):
    ident = str(ok["messages"]["body"].get("id")) if "messages" in ok else ""
    for prefix, route in (("msg_bdrk_", "aws_bedrock"), ("msg_vrtx_", "google_vertex"), ("msg_01", "anthropic_direct")):
        if ident.startswith(prefix):
            return route, None, "observed", ""


def azure_body(model, ok):
    if any(node.keys() & AZURE_KEYS for answer in ok.values() for node in walk(answer["body"])):
        return "azure_foundry", None, "observed", "Azure 콘텐츠 필터 필드(배포 지역 표기 없음)"


def openai_billing(model, ok):
    for answer in ok.values():
        billing = answer["body"].get("billing")
        if isinstance(billing, dict) and billing.get("payer"):  # OpenAI's own response objects say who pays
            return "openai_direct", None, "observed", ""


def google(model, ok):
    for answer in ok.values():
        body = answer["body"]
        if GEMINI_ID.fullmatch(str(body.get("id"))) and any(str(key).startswith("vertex_ai_") for key in body):
            return "google_direct", None, "observed", "Gemini 응답(AI Studio와 Vertex는 구별하지 못함)"


def openai_guess(model, ok):
    chat = ok.get("chat")
    if "images" in ok or (chat and OPENAI_FAMILY.match(model) and OPENAI_CHAT_ID.fullmatch(str(chat["body"].get("id")))):
        # the registry names OpenAI's image route "openai" (its image sources), its text route "openai_direct"
        return "openai" if "images" in ok else "openai_direct", None, "inferred", "Azure 콘텐츠 필터 필드가 없음(필터를 끈 Azure 배포도 같아 보임)"


def bedrock_shape(model, ok):
    chat = ok.get("chat")
    usage = chat["body"].get("usage") if chat else None
    if chat and CHAT_UUID_ID.fullmatch(str(chat["body"].get("id"))) and isinstance(usage, dict) and BEDROCK_USAGE <= usage.keys():
        return "aws_bedrock", None, "inferred", "응답 형식이 Bedrock을 거친 GPT 응답과 같음(chatcmpl-uuid id, cache_creation_input_tokens)"


# Most specific first: a deployment named in the header and a gateway's own marks cannot come from anywhere else, content-filter
# fields can also be passed through, and the two shapes that only fit one route come last.
RULES = (named_deployment, openrouter, vercel, anthropic, azure_body, openai_billing, google, openai_guess, bedrock_shape)


def classify(model: str, responses: dict) -> tuple[str | None, str | None, str | None, str]:
    """(route_id, endpoint_id, basis, note) for what one model's answers show. `responses` maps an endpoint name (chat,
    messages, responses, images, embeddings) to {status, headers (lowercase names), body}, as Gateway returns them.
    route_id is None when they show no route; the note then says what was seen instead. basis is observed (the upstream
    left its mark in the answer) or inferred (the answer only fits one route). endpoint_id is always None: the gateways
    pick a sub-provider per request, so the one seen in this call is only worded in the note."""
    ok = {name: {**answer, "headers": answer.get("headers") or {}} for name, answer in responses.items()
          if answer.get("status") == 200 and isinstance(answer.get("body"), dict)}
    for rule in RULES:
        found = rule(model, ok)
        if found:
            return found
    return None, None, None, unresolved_note(model, responses)


def unresolved_note(model: str, responses: dict) -> str:
    if not responses:
        return NOT_PROBED.get(kind(model), "호출하지 않음")
    parts = []
    for name, answer in responses.items():
        body = answer.get("body")
        if answer.get("status") == 200 and isinstance(body, dict):
            shape, extra = id_shape(body.get("id")), sorted(set(body) - PLAIN_FIELDS)
            seen = ([f"id 형식 {shape}"] if shape else []) + ["경로 표시 없음"]
            if extra:
                seen.append(f"그 밖의 필드 {', '.join(extra[:6])}" + (f" 외 {len(extra) - 6}개" if len(extra) > 6 else ""))
            parts.append(f"{name} 200: {', '.join(seen)}")
        else:
            parts.append(f"{name} {failure(answer)}")
    return "; ".join(parts)


def answered(responses: dict) -> bool:
    """False when the gateway said nothing usable about the model: no 200 and some endpoint without a firm answer (no
    connection, a timeout, a rate limit, a 5xx). That is no finding, so it is not written to the unresolved map. Nothing asked
    (video, images) is a finding: the note says why (all() of no answers is true)."""
    states = [answer.get("status") for answer in responses.values()]
    return any(state == 200 for state in states) or all(
        isinstance(state, int) and 400 <= state < 500 and state not in (408, 429) for state in states)


def resolve(model: str, responses: dict) -> dict:
    route, endpoint, basis, note = classify(model, responses)
    return {"route_id": route, "endpoint_id": endpoint, "basis": basis, "note": note, "answered": answered(responses)}


# ---- running -----------------------------------------------------------------------------------------------------------

def probe(gateway, model: str, model_kind: str, include_images: bool) -> dict:
    responses = {}
    for name in plan(model, model_kind, include_images):
        responses[name] = gateway.probe(name, model)
        if classify(model, responses)[2] == "observed":
            break  # settled: another request would only cost money
    return responses


def today() -> str:
    return datetime.now(KST).date().isoformat()


def cost(answer: dict) -> float:
    body = answer.get("body") or {}
    for raw in (answer["headers"].get("x-letsur-ai-gateway-response-cost"), (body.get("estimated_cost") or {}).get("amount")):
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


# ---- the routes document -----------------------------------------------------------------------------------------------

def load_routes(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} cannot be read ({type(exc).__name__})") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list) \
            or not all(isinstance(r, dict) and isinstance(r.get("model_id"), str) for r in doc["records"]) \
            or not isinstance(doc.get("unresolved", {}), dict):
        raise ValueError(f"{path} is not a routes file (records list of model records, optional unresolved map)")
    return doc


def merge(current: dict, results: dict, checked_at: str) -> tuple[dict, dict]:
    """The routes document with this run's results added, and what happened to each model's records.

    A route seen for the first time becomes a record of its own with `checked_at`, next to the model's other records; a route
    seen again only gets a new date (and the new note, when there is one), but an inferred record is replaced by an observed
    one; an observed record seen only as inferred keeps its date. Records of other routes stay as they were, with their own
    dates, and so do settings and manual records, which also stop a new record of the same route. A model with a route anywhere
    needs no unresolved entry; one with none gets one when the gateway answered, and an entry already there stays as written."""
    records, unresolved = [dict(record) for record in current["records"]], dict(current.get("unresolved") or {})
    diff = {part: [] for part in DIFF_PARTS}
    for model in sorted(results):
        result, name = results[model], ALIASES.get(model, model)
        route, refreshed, weaker = result["route_id"], None, None
        mine = [i for i, r in enumerate(records) if r["model_id"] == name and r.get("basis") in MANAGED]
        if route:
            same = next((i for i in mine if records[i].get("route_id") == route), None)
            new = {"model_id": name, "route_id": route, "endpoint_id": result["endpoint_id"], "basis": result["basis"],
                   **({"note": result["note"]} if result["note"] else {}), "checked_at": checked_at}
            after = (route, result["endpoint_id"], result["basis"])
            if any(r["model_id"] == name and r.get("basis") not in MANAGED and r.get("route_id") == route for r in records):
                diff["covered"].append((name, after))
            elif same is None:
                own = [i for i, r in enumerate(records) if r["model_id"] == name]
                others = [tuple(records[i].get(key) for key in ("route_id", "endpoint_id", "basis", "checked_at")) for i in own]
                refreshed = own[-1] + 1 if own else len(records)
                records.insert(refreshed, new)
                diff["new_route"].append((name, after, others))
            elif records[same].get("basis") == "inferred" and result["basis"] == "observed":
                records[same], refreshed = new, same
                diff["upgraded"].append((name, route))
            elif records[same].get("basis") == result["basis"]:
                for key, value in (("note", result["note"]), ("checked_at", checked_at)):
                    if value:
                        records[same][key] = value
                refreshed = same
                diff["re_seen"].append(name)
            else:
                weaker = same
        for i in mine:
            if i != refreshed:
                why = "inferred only this run" if i == weaker else "" if route else result["note"]
                record = tuple(records[i].get(key) for key in ("route_id", "endpoint_id", "basis"))
                diff["older"].append((name, record, records[i].get("checked_at"), why))
        if any(r["model_id"] == name and r.get("route_id") for r in records):
            unresolved.pop(name, None)
        elif name in unresolved or result["answered"]:
            unresolved.setdefault(name, result["note"])
            diff["unresolved"].append(name)
        else:
            diff["no_answer"].append((name, result["note"]))
    return {**current, "records": records, "unresolved": unresolved}, diff


def describe(parts) -> str:
    return " + ".join(f"{route}{'/' + endpoint if endpoint else ''} [{basis}]" for route, endpoint, basis in parts)


def format_diff(diff: dict) -> str:
    lines = []

    def section(title, rows):
        if rows:
            lines.extend([f"{title} ({len(rows)})", *(f"  {row}" for row in rows)])

    def dated(route, endpoint, basis, day):
        return f"{describe([(route, endpoint, basis)])} {day or 'no date'}" if basis in MANAGED else describe([(route, endpoint, basis)])

    section("new route", [f"{name}: {describe([after])}" + (f" (also {', '.join(dated(*other) for other in others)})" if others else "")
                          for name, after, others in diff["new_route"]])
    section("upgraded to observed", [f"{name}: {route}" for name, route in diff["upgraded"]])
    section("older-dated, not seen this run", [f"{name}: {dated(*record, day)}" + (f" ({why})" if why else "") for name, record, day, why in diff["older"]])
    section("covered by a settings or manual record", [f"{name}: {describe([after])}" for name, after in diff["covered"]])
    section("no usable answer, nothing recorded", [f"{name}: {note}" for name, note in diff["no_answer"]])
    for title, names in (("re-seen", diff["re_seen"]), ("unresolved", diff["unresolved"]), ("not registered, skipped", diff["not_registered"])):
        if names:
            lines.append(f"{title} ({len(names)}): " + ", ".join(names))
    return "\n".join(lines)


def main(argv=None, env=None, opener=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.market_collection.letsur_routes", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", action="append", default=[], help="model ids to check, comma separated (default: every model the gateway lists)")
    parser.add_argument("--include-images", action="store_true", help="also request one low-quality image per gpt-image model (costs money)")
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES, help="the routes file to compare with (default: %(default)s)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where the proposal is written (default: %(default)s)")
    parser.add_argument("--write", action="store_true", help="also overwrite --routes with the proposal")
    parser.add_argument("--timeout", type=float, default=90, help="seconds to wait for one answer (default: %(default)s)")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    base, key = env.get(BASE_ENV, "").strip(), env.get(KEY_ENV, "").strip()
    if not base or not key:
        print(f"refused: set {BASE_ENV} and {KEY_ENV} in the environment", file=sys.stderr)
        return 2
    if not base.startswith("https://"):
        print(f"refused: {BASE_ENV} must start with https:// (the key is sent with every request)", file=sys.stderr)
        return 2
    if not args.write and args.output.resolve() == args.routes.resolve():
        print("refused: --output is the routes file; add --write to overwrite it", file=sys.stderr)
        return 2
    try:
        current = load_routes(args.routes)
        gateway = Gateway(base, key, args.timeout, opener)
        listed = [(model.strip(), ()) for part in args.models for model in part.split(",") if model.strip()] or gateway.models()
        wanted = sorted(dict(listed).items())
        models = [(model, modality) for model, modality in wanted if ALIASES.get(model, model) in MODEL_MODALITIES]
        results, answers = {}, {}
        for number, (model, modality) in enumerate(models, 1):
            answers[model] = probe(gateway, model, kind(model, modality), args.include_images)
            results[model] = resolve(model, answers[model])
            result = results[model]
            label = result["route_id"] or ("not requested" if not answers[model] else "unresolved" if result["answered"] else "no answer")
            print(f"[{number}/{len(models)}] {model}: {label}", file=sys.stderr)
    except (GatewayError, ValueError) as exc:
        print(f"refused: {mask(str(exc), (key, base))}", file=sys.stderr)
        return 2
    if not any(answer.get("status") == 200 for responses in answers.values() for answer in responses.values()):
        print("no model gave a usable answer; nothing written", file=sys.stderr)
        return 1
    proposal, diff = merge(current, results, today())
    diff["not_registered"] = [model for model, _ in wanted if ALIASES.get(model, model) not in MODEL_MODALITIES]
    text = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    if args.write:
        args.routes.write_text(text, encoding="utf-8")
    requests = [answer for responses in answers.values() for answer in responses.values()]
    print(format_diff(diff))
    print(f"\nchecked {len(answers)} models with {len(requests)} requests, gateway-reported cost {sum(map(cost, requests)):.4f}")
    print(f"proposal written to {args.output}; " + (f"{args.routes} overwritten" if args.write else f"{args.routes} left as it was (--write overwrites it)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
