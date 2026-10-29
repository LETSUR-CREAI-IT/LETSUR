"""Re-detect which upstream route LETSUR's gateway uses for each model and propose an update of the routes file.

    LETSUR_API_BASE=<gateway url> LETSUR_API_KEY=<key> python3 -B -m scripts.market_collection.letsur_routes \\
        [--models id[,id...]] [--include-images] [--routes FILE] [--output FILE] [--write]

No gateway endpoint names a model's route, so each model gets one tiny request per endpoint it needs (no retries) and the
route is read from what the upstream leaves in the answer: ids, provider fields, content-filter fields. An answer with no
such mark leaves the model unresolved and the note keeps the evidence. Video generation is never requested and image
generation only with --include-images (both cost money). The base URL and the key come only from the environment and are
never printed or written. The proposal goes to --output; --routes is rewritten only with --write. Records get note and
checked_at (the run date, KST); models with no route go to the top-level "unresolved" map as {model_id: note}. No record
gets an endpoint_id: OpenRouter and Vercel pick a sub-provider per request, so the one seen goes into the note, which is
plain Korean because the admin's route tab shows it as written.
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

BASE_ENV, KEY_ENV = "LETSUR_API_BASE", "LETSUR_API_KEY"
DEFAULT_ROUTES = Path("samples/market_collection/demo_internal_routes.json")
DEFAULT_OUTPUT = Path("data/market_collection/letsur_routes.proposed.json")
KST = timezone(timedelta(hours=9))
MAX_BODY_BYTES = 8 * 1024 * 1024  # one low-quality gpt-image answer is about 1 MB of base64
LONG_TEXT = 200  # longer strings (image data, reasoning text) are cut: nothing here reads them
KEEP_HEADERS = ("x-letsur-ai-gateway-model-id", "x-letsur-ai-gateway-response-cost")
MANAGED = ("observed", "inferred")  # the bases this tool writes; settings and manual records are a person's and stay
ALIASES = {"claude-haiku-4-5-20251001": "claude-haiku-4-5"}  # LETSUR's dated id -> the id the collectors and the routes file use
ASK = "Reply with: ok"
ASK_NO_DRAW = "Reply with the word ok only. Do not draw."  # a Gemini image model would otherwise answer with a picture
PATHS = {"chat": "/v1/chat/completions", "messages": "/v1/messages", "responses": "/v1/responses",
         "images": "/v1/images/generations", "embeddings": "/v1/embeddings"}
OPENAI_FAMILY = re.compile(r"(gpt-|o\d)")
AZURE_DEPLOYMENT = re.compile(r".+?-azure-(?P<region>[a-z0-9][a-z0-9-]*)")  # header model id of a named Azure deployment
AZURE_KEYS = ("prompt_filter_results", "content_filter_results", "content_filters", "serving_pipereplica")
OPENROUTER_ID = re.compile(r"gen-\d+-")
GEMINI_ID = re.compile(r"[A-Za-z0-9_-]{20,26}")
OPENAI_CHAT_ID = re.compile(r"chatcmpl-[A-Za-z0-9]{20,40}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PLAIN_FIELDS = {"id", "object", "created", "created_at", "model", "choices", "usage", "estimated_cost", "data", "type",
                "role", "content", "stop_reason", "stop_sequence"}  # fields every answer has: not worth a note
# Notes below and in the rules are Korean: they are shown to the admin as written.
NOT_PROBED = {"video": "렛서 영상 API는 자체 입력 형식이라 생성 없이 경로가 드러나지 않음(호출하지 않음)",
              "image": "이미지 생성은 비용이 들어 호출하지 않음"}
DIFF_PARTS = ("added", "changed", "unchanged", "now_unresolved", "unresolved", "kept", "covered")


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


def azure_header(model, ok):
    for answer in ok.values():
        match = AZURE_DEPLOYMENT.fullmatch(answer["headers"].get("x-letsur-ai-gateway-model-id", ""))
        if match:
            return "azure_foundry", None, "observed", f"Azure 배포 지역 {match['region']}"


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
        return "openai_direct", None, "inferred", "Azure 콘텐츠 필터 필드가 없음(필터를 끈 Azure 배포도 같아 보임)"


# Most specific first: a gateway's own marks cannot come from anywhere else, content-filter fields can also be passed through.
RULES = (azure_header, openrouter, vercel, anthropic, azure_body, openai_billing, google, openai_guess)


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
    """False when the gateway said nothing usable about the model: nothing asked, or no 200 and some endpoint without a firm
    answer (no connection, a timeout, a rate limit, a 5xx). That says nothing about the route, so the old record must stand."""
    states = [answer.get("status") for answer in responses.values()]
    return any(state == 200 for state in states) or (bool(states) and all(
        isinstance(state, int) and 400 <= state < 500 and state not in (408, 429) for state in states))


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
    """The routes document with this run's results applied, and what changed model by model.

    Only observed and inferred records belong to this tool: a settings or manual record stays as it is, and when one already
    names the route a model was seen on the model gets no second record (nor an unresolved entry when no route was seen).
    A model with no usable answer keeps what the file already knew about it. New records go where the model's old one was,
    else at the end."""
    records, unresolved = [dict(record) for record in current["records"]], dict(current.get("unresolved") or {})
    diff = {part: [] for part in DIFF_PARTS}
    for model in sorted(results):
        result, name = results[model], ALIASES.get(model, model)
        mine = [i for i, r in enumerate(records) if r["model_id"] == name and r.get("basis") in MANAGED]
        held = [r for r in records if r["model_id"] == name and r.get("basis") not in MANAGED]
        before = [(records[i].get("route_id"), records[i].get("endpoint_id"), records[i].get("basis")) for i in mine]
        if not result["answered"] and (mine or name in unresolved):
            diff["kept"].append((name, before, result["note"]))
            continue
        at = mine[0] if mine else len(records)
        records = [r for i, r in enumerate(records) if i not in mine]
        if result["route_id"] is None:
            named = any(r.get("route_id") for r in held)  # a settings or manual record already names its route
            if named:
                unresolved.pop(name, None)
            else:
                unresolved[name] = result["note"]
            if mine or not named:
                diff["now_unresolved" if mine else "unresolved"].append((name, before, result["note"]))
            continue
        new = {"model_id": name, "route_id": result["route_id"], "endpoint_id": result["endpoint_id"], "basis": result["basis"],
               **({"note": result["note"]} if result["note"] else {}), "checked_at": checked_at}
        after, was_unresolved = (new["route_id"], new["endpoint_id"], new["basis"]), unresolved.pop(name, None) is not None
        if any(r.get("route_id") == new["route_id"] for r in held):
            diff["covered"].append((name, after))
            continue
        records.insert(at, new)
        if not mine:
            diff["added"].append((name, after, was_unresolved))
        elif before == [after]:
            diff["unchanged"].append(name)
        else:
            diff["changed"].append((name, before, after))
    return {**current, "records": records, "unresolved": dict(sorted(unresolved.items()))}, diff


def describe(parts) -> str:
    return " + ".join(f"{route}{'/' + endpoint if endpoint else ''} [{basis}]" for route, endpoint, basis in parts) or "no record"


def format_diff(diff: dict) -> str:
    lines = []

    def section(title, rows):
        if rows:
            lines.extend([f"{title} ({len(rows)})", *(f"  {row}" for row in rows)])

    section("added", [f"{name}: {describe([after])}" + (" (was unresolved)" if was else "") for name, after, was in diff["added"]])
    section("changed", [f"{name}: {describe(before)} -> {describe([after])}" for name, before, after in diff["changed"]])
    section("now unresolved", [f"{name}: was {describe(before)} -> {note}" for name, before, note in diff["now_unresolved"]])
    section("kept, no usable answer", [f"{name}: {describe(before)} ({note})" for name, before, note in diff["kept"]])
    section("covered by a settings or manual record", [f"{name}: {describe([after])}" for name, after in diff["covered"]])
    lines.append(f"unchanged ({len(diff['unchanged'])})")
    if diff["unresolved"]:
        lines.append(f"unresolved ({len(diff['unresolved'])}): " + ", ".join(name for name, _, _ in diff["unresolved"]))
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
        models, results, answers = sorted(dict(listed).items()), {}, {}
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
