"""Re-detect which upstream route LETSUR's gateway uses for each model and propose an update of the routes file.

    LETSUR_API_BASE=<gateway url> LETSUR_API_KEY=<key> python3 -B -m scripts.market_collection.letsur_routes \\
        [--models id[,id...]] [--samples N] [--workers N] [--include-images] [--bedrock-check] [--routes FILE] [--output FILE] [--write]

No gateway endpoint names a model's route, so each model gets tiny requests (no retries) and the route is read from what the
upstream leaves in the answer: the named deployment in a header, ids, provider fields, content-filter fields. LETSUR spreads a
model over several deployments and picks one per request, so each model is asked --samples times (default 6; each time the
endpoints it needs, the next one only while the ones before did not settle the route) on --workers threads. A Gemini model's
chat request carries a safety setting with Vertex's own `method` field: the Gemini API refuses it, Vertex takes it. A route is
recorded only from such a mark (basis observed): an answer that only fits a route leaves its deployment unresolved and the note
keeps the evidence. With --bedrock-check, a model with a deployment that left only the gateway's own chatcmpl-<uuid> id gets
three more kinds of chat request, to see whether that deployment checks Bedrock's own performanceConfig field. Video generation
is never requested and image generation only with --include-images, once per model (both cost money). The base URL and the key
come only from the environment and are never printed or written. The proposal goes to --output; --routes is rewritten only
with --write.

The routes file shows what LETSUR uses now. For a model this run identified a route of, the observed records of the routes it
saw are kept (a new checked_at; a note already there stays) or added with that day's checked_at, and its observed records of
routes it did not see are dropped; a model with no identified answer keeps its records. Settings and manual records are a
person's and are never touched; one of the same route stands for that route. Inferred records, which earlier versions wrote,
are dropped for a model that answered this run. Only models in our registry (models.py, plus ALIASES for LETSUR's other ids) are
asked and recorded; the rest of LETSUR's list is named in the diff only. A model with a deployment that answered without
showing its route gets an entry in the top-level "unresolved" map as {model_id: note}, even when other deployments showed
theirs; one whose answers all showed routes loses it, and an entry already there is left as written. No record gets an
endpoint_id: OpenRouter and Vercel pick a sub-provider per request, so the ones seen go into the note, which is plain Korean
because the admin's route tab shows it as written.
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
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.market_collection.models import MODEL_MODALITIES

BASE_ENV, KEY_ENV = "LETSUR_API_BASE", "LETSUR_API_KEY"
DEFAULT_ROUTES = Path("samples/market_collection/demo_internal_routes.json")
DEFAULT_OUTPUT = Path("data/market_collection/letsur_routes.proposed.json")
KST = timezone(timedelta(hours=9))
MAX_BODY_BYTES = 8 * 1024 * 1024  # one low-quality gpt-image answer is about 1 MB of base64
LONG_TEXT = 200  # longer strings (image data, reasoning text) are cut: nothing here reads them
DEPLOYMENT = "x-letsur-ai-gateway-model-id"  # which of a model's deployments answered: the same on every answer of one, none on errors
KEEP_HEADERS = (DEPLOYMENT, "x-letsur-ai-gateway-response-cost")
MANAGED = ("observed",)  # the basis this tool writes; settings and manual records are a person's and stay
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
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CHAT_UUID_ID = re.compile("chatcmpl-" + UUID.pattern)  # an id the gateway made itself: the upstream left none
PLAIN_FIELDS = {"id", "object", "created", "created_at", "model", "choices", "usage", "estimated_cost", "data", "type",
                "role", "content", "stop_reason", "stop_sequence"}  # fields every answer has: not worth a note
# Gemini API (Google AI Studio) and Vertex answer a Gemini model in the same shape, but only Vertex's SafetySetting has `method`
# (Google's API discovery documents: generativelanguage v1beta revision 20261029 has threshold and category, aiplatform v1
# revision 20261028 also method) and Google refuses an unknown field with 400. Through LETSUR on 10/30 a chat request with this
# setting got 400 invalid_parameter whenever a Gemini API deployment answered, OpenRouter and Vercel took it (on other models
# too), and without `method` every deployment answered 200. No Vertex answer was among those 72 calls.
VERTEX_ONLY = [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH", "method": "SEVERITY"}]
# Z.ai's chat API takes a request_id from the caller and answers with it (docs.z.ai chat completion: "passed by the user side
# ... if not provided, the platform will generate one"); OpenRouter, Vercel and the other GLM deployments answered the same
# field without it (10/30). So a GLM answer that carries back this prefix came from Z.ai's own API.
ECHO = "letsur-route-check-"
# Bedrock's Converse API takes performanceConfig.latency only as standard or optimized. A deployment that answers "standard"
# and an unknown field but never "bogus" (the gateway then hands the request to another deployment) checks that field, so it is
# Bedrock: the chatcmpl-<uuid> deployments of glm-4.7 and glm-5 did so on 10/30. Sent only with --bedrock-check.
PERFORMANCE = {"standard": {"performanceConfig": {"latency": "standard"}}, "bogus": {"performanceConfig": {"latency": "bogus"}},
               "unknown": {"foo_probe_param": 1}}
CHANCE = 0.05  # how likely a deployment that takes "bogus" may be to miss every bogus request by the router's draw alone
# Notes below and in the rules are Korean: they are shown to the admin as written.
NOT_PROBED = {"video": "렛서 영상 API는 자체 입력 형식이라 생성 없이 경로가 드러나지 않음(호출하지 않음)",
              "image": "이미지 생성은 비용이 들어 호출하지 않음"}
BEDROCK_NOTE = "Bedrock 전용 performanceConfig 값 검사를 함"
DIFF_PARTS = ("new_route", "re_seen", "dropped", "inferred", "kept", "covered", "unresolved", "no_answer", "not_registered")


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
    """The endpoints one sample asks, every one of them (probe)."""
    if model_kind == "chat":
        return ["chat", "responses"] if OPENAI_FAMILY.match(model) else ["chat"]
    return {"claude": ["messages"], "embedding": ["embeddings"], "image": ["images"] if include_images else [], "video": []}[model_kind]


def gemini(model: str) -> bool:
    """A Gemini model: its chat request carries VERTEX_ONLY, and its answer to that tells the Gemini API from Vertex."""
    return model.startswith("gemini")


def payload(name: str, model: str) -> dict:
    ask = ASK_NO_DRAW if "image" in model or "nano-banana" in model else ASK
    vertex = {"safety_settings": VERTEX_ONLY} if gemini(model) else {}
    echo = {"request_id": ECHO + uuid.uuid4().hex[:16]} if model.startswith("glm") else {}
    return {"chat": {"model": model, "messages": [{"role": "user", "content": ask}], "max_tokens": 16, **vertex, **echo},
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
    slimmed, error the exception name of a request that got no answer. Requests run on several threads, so once the key is
    refused the requests still to come are refused here without being sent."""

    def __init__(self, base: str, key: str, timeout: float, opener=None):
        self.base, self.key, self.timeout = base.rstrip("/"), key, timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self.refused = False

    def request(self, path: str, body: dict | None = None, anthropic: bool = False) -> dict:
        if self.refused:
            raise GatewayError("HTTP 401: the gateway refused LETSUR_API_KEY")
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
            self.refused = True
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
        header = answer["headers"].get(DEPLOYMENT, "")
        for pattern, route, note in DEPLOYMENTS:
            match = pattern.fullmatch(header)
            if match:
                return route, None, "observed", note(match[1])


def openrouter_host(body):
    """The sub-provider an OpenRouter answer names ("" when it shows OpenRouter's marks but no name), None for other answers."""
    provider = body["provider"] if isinstance(body.get("provider"), str) else ""
    usage = body["usage"] if isinstance(body.get("usage"), dict) else {}
    if OPENROUTER_ID.match(str(body.get("id"))) and (provider or "is_byok" in usage):
        return provider


def vercel_host(body):
    """(the sub-provider that answered, the planned one when that failed and another answered, else "") of a Vercel answer,
    None for other answers."""
    gateway = first(body, "gateway")
    routing = gateway.get("routing") if isinstance(gateway, dict) else None
    final, resolved = (routing or {}).get("finalProvider"), (routing or {}).get("resolvedProvider")
    if isinstance(routing, dict) and (final or resolved):
        return final or resolved, resolved if final and resolved and final != resolved else ""


def via(hosts, planned=(), over="호출") -> str:
    """The note of a gateway that picks a sub-provider per request: the ones that answered, and the planned ones that failed."""
    plan = f"계획 {'·'.join(planned)}, " if planned else ""
    return f"이번 {over}의 하위 공급자 {'·'.join(hosts)}({plan}요청마다 바뀔 수 있음)"


def openrouter(model, ok):
    for answer in ok.values():
        host = openrouter_host(answer["body"])
        if host is not None:
            return "openrouter", None, "observed", via([host]) if host else ""


def vercel(model, ok):
    for answer in ok.values():
        host = vercel_host(answer["body"])
        if host:
            return "vercel_ai_gateway", None, "observed", via([host[0]], [host[1]] if host[1] else [])


def anthropic(model, ok):
    ident = str(ok["messages"]["body"].get("id")) if "messages" in ok else ""
    for prefix, route in (("msg_bdrk_", "aws_bedrock"), ("msg_vrtx_", "google_vertex"), ("msg_01", "anthropic_direct")):
        if ident.startswith(prefix):
            return route, None, "observed", ""


def zai(model, ok):
    for answer in ok.values():
        if str(answer["body"].get("request_id", "")).startswith(ECHO):
            return "zai_direct", None, "observed", "보낸 request_id를 그대로 돌려줌(Z.ai API 동작)"


def azure_body(model, ok):
    if any(node.keys() & AZURE_KEYS for answer in ok.values() for node in walk(answer["body"])):
        return "azure_foundry", None, "observed", "Azure 콘텐츠 필터 필드(배포 지역 표기 없음)"


def openai_billing(model, ok):
    for answer in ok.values():
        billing = answer["body"].get("billing")
        if isinstance(billing, dict) and billing.get("payer"):  # OpenAI's own response objects say who pays
            return "openai_direct", None, "observed", ""


def vertex(model, ok):
    body = ok["chat"]["body"] if "chat" in ok and gemini(model) else {}  # only the chat request carried VERTEX_ONLY
    if GEMINI_ID.fullmatch(str(body.get("id"))) and any(str(key).startswith("vertex_ai_") for key in body):
        return "google_vertex", None, "observed", "Vertex 전용 안전 설정 칸(method)을 받아들임"


def gemini_api(model, responses):
    """The one rule that reads a refusal: the Gemini API's 400 to VERTEX_ONLY, which LETSUR reports as invalid_parameter."""
    chat = responses.get("chat") or {}
    if gemini(model) and chat.get("status") == 400 and error_code(chat.get("body")) == "invalid_parameter":
        return "google_direct", None, "observed", "Gemini API가 Vertex 전용 안전 설정 칸(method)을 거절함"


# Most specific first: a deployment named in the header and a gateway's own marks cannot come from anywhere else, and
# content-filter fields can also be passed through. An answer that only fits one route (what earlier versions inferred from:
# no Azure field on an OpenAI answer, a Bedrock-like GLM answer) names none.
RULES = (named_deployment, openrouter, vercel, zai, anthropic, azure_body, openai_billing, vertex)


def classify(model: str, responses: dict) -> tuple[str | None, str | None, str | None, str]:
    """(route_id, endpoint_id, basis, note) for what one sample of a model's answers shows. `responses` maps an endpoint name
    (chat, messages, responses, images, embeddings) to {status, headers (lowercase names), body}, as Gateway returns them.
    route_id is None when they show no route; the note then says what was seen instead. basis is always observed: the
    upstream left its mark in the answer (or refused a field only another route takes). endpoint_id is always None: the
    gateways pick a sub-provider per request, so the one seen in this call is only worded in the note."""
    ok = {name: {**answer, "headers": answer.get("headers") or {}} for name, answer in responses.items()
          if answer.get("status") == 200 and isinstance(answer.get("body"), dict)}
    for rule in RULES:
        found = rule(model, ok)
        if found:
            return found
    return gemini_api(model, responses) or (None, None, None, unresolved_note(model, responses))


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


# ---- a model's samples together ----------------------------------------------------------------------------------------

def summarize(model: str, samples: list[dict], bedrock_shown=frozenset()) -> dict:
    """What all of a model's samples show, for merge and the progress line.

    routes maps each route an answer showed to its note; tally counts the samples by route, or as unresolved or no answer (a
    sample that asked nothing is not counted). Deployments are told apart by their header, so only 200 answers count (an error
    carries none): a deployment that one of its answers identified is known, and so is one in `bedrock_shown`, which
    --bedrock-check showed. unidentified is true when an unknown deployment answered 200 or a sample got only firm errors, and
    the note then says what was seen; otherwise it says why nothing was. checked is true when some request got a firm answer."""
    routes, deployments, failed, quiet, tally = {}, {}, [], [], Counter()
    for responses in samples:
        read = {}
        for name, answer in responses.items():
            route, _, _, note = classify(model, {name: answer})
            if not route and (answer.get("headers") or {}).get(DEPLOYMENT) in bedrock_shown:
                route, note = "aws_bedrock", BEDROCK_NOTE
            read[name] = route, note
        settled = next((route for route, _ in read.values() if route), None)
        if responses:
            tally[settled or ("unresolved" if answered(responses) else "no answer")] += 1
        if not settled:
            quiet.append(unresolved_note(model, responses))
        for name, answer in responses.items():
            route, note = read[name]
            if route:
                routes.setdefault(route, []).append((note, answer.get("body")))
            if answer.get("status") == 200 and isinstance(answer.get("body"), dict):
                header = (answer.get("headers") or {}).get(DEPLOYMENT, "")
                deployments.setdefault(header, []).append(None if route else unresolved_note(model, {name: answer}))
            elif not settled and answered(responses):
                failed.append(unresolved_note(model, {name: answer}))
    # an answer without the header names no deployment, so another answer never makes it known
    unknown = [notes for header, notes in deployments.items() if any(notes) and not (header and None in notes)]
    shapes = sorted({note for notes in unknown for note in notes if note})
    parts = [f"배포 {len(deployments)}개 중 {len(unknown)}개 경로 표시 없음: {'; '.join(shapes)}"] if unknown else []
    parts += sorted(set(failed))
    return {"routes": {route: route_note(route, seen) for route, seen in sorted(routes.items())},
            "tally": dict(sorted(tally.items(), key=lambda item: (item[0] in ("unresolved", "no answer"), item[0]))),
            "answered": any(answered(responses) for responses in samples),
            "checked": any(responses and answered(responses) for responses in samples),
            "unidentified": bool(parts), "note": "; ".join(parts) or "; ".join(sorted(set(quiet)))}


def route_note(route: str, seen: list) -> str:
    """One note for a route that several answers showed: OpenRouter's and Vercel's sub-providers are named once each, other
    notes joined. Sorted, so it does not depend on which answer came first."""
    if route == "openrouter":
        hosts = sorted({openrouter_host(body) for _, body in seen} - {""})
        return via(hosts, over="확인") if hosts else ""
    if route == "vercel_ai_gateway":
        hosts = [vercel_host(body) for _, body in seen]
        return via(sorted({host for host, _ in hosts}), sorted({plan for _, plan in hosts} - {""}), "확인")
    return "; ".join(sorted({note for note, _ in seen} - {""}))


def uuid_deployments(model: str, samples: list[dict]) -> set[str]:
    """The deployments (header values) whose chat answers showed no route and carry only the id the gateway made,
    chatcmpl-<uuid>: the ones --bedrock-check tests."""
    return {(answer.get("headers") or {}).get(DEPLOYMENT, "") for responses in samples for name, answer in responses.items()
            if name == "chat" and answer.get("status") == 200 and isinstance(answer.get("body"), dict)
            and CHAT_UUID_ID.fullmatch(str(answer["body"].get("id"))) and not classify(model, {name: answer})[0]} - {""}


def bedrock(candidates: set[str], answers: dict[str, list[dict]]) -> set[str]:
    """The candidate deployments that answered PERFORMANCE's standard and unknown requests (by their header) but none of the
    bogus ones. The router draws a deployment per request, so one counts only when it took so many of the other answers that
    missing every bogus request by that draw alone would happen less often than CHANCE."""
    took = {variant: [(answer.get("headers") or {}).get(DEPLOYMENT, "") for answer in got if answer.get("status") == 200]
            for variant, got in answers.items()}
    tried = sum(answered({"chat": answer}) for answer in answers["bogus"])  # a timeout or a rate limit reached no deployment
    other = took["standard"] + took["unknown"]
    return {header for header in candidates if header in took["standard"] and header in took["unknown"]
            and header not in took["bogus"] and (1 - other.count(header) / len(other)) ** tried < CHANCE}


# ---- running -----------------------------------------------------------------------------------------------------------

def probe(gateway, model: str, model_kind: str, include_images: bool) -> dict:
    """One sample. The OpenAI family asks Responses even when its chat answer already shows a route: each request lands on any
    of the model's deployments, and only a Responses answer shows OpenAI's own one (billing). gpt-5.5 had one among its Azure
    deployments on 10/30."""
    return {name: gateway.probe(name, model) for name in plan(model, model_kind, include_images)}


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
    """The routes document with this run's results in, and what happened to each model's records.

    The file shows what LETSUR uses now (2026-10-30). For a model this run identified a route of, an observed record of a route
    it saw gets the new date (and a note only when it had none), a route seen for the first time becomes a record of its own with
    `checked_at` next to the model's other records, and its observed records of routes it did not see are dropped. A model with
    no identified answer keeps its records: the run proved nothing against them. Settings and manual records are never touched
    and stop a new record of the same route. Inferred records, which earlier versions wrote, are dropped for a model the run
    checked. A model with a deployment that answered without its route gets an unresolved entry even when others showed routes;
    one whose answers all showed routes loses it; one with no route record left gets it when the gateway answered. An entry
    already there stays as written."""
    records, unresolved = [dict(record) for record in current["records"]], dict(current.get("unresolved") or {})
    diff = {part: [] for part in DIFF_PARTS}
    for model in sorted(results):
        result, name = results[model], ALIASES.get(model, model)
        own = [i for i, r in enumerate(records) if r["model_id"] == name]
        mine = [i for i in own if records[i].get("basis") in MANAGED]
        guessed = [i for i in own if records[i].get("basis") == "inferred"] if result["checked"] else []
        seen, new = [], []
        for route, note in result["routes"].items():
            same = next((i for i in mine if records[i].get("route_id") == route), None)
            if same is not None:
                if note and not records[same].get("note"):  # a note already there (a person's or an earlier run's) stays
                    records[same]["note"] = note
                records[same]["checked_at"] = checked_at
                seen.append(same)
            elif any(records[i].get("route_id") == route for i in own if i not in mine and i not in guessed):
                diff["covered"].append((name, (route, None, "observed")))
            else:
                new.append({"model_id": name, "route_id": route, "endpoint_id": None, "basis": "observed",
                            **({"note": note} if note else {}), "checked_at": checked_at})
        if seen:
            diff["re_seen"].append(name)
        unseen = [i for i in mine if i not in seen]
        gone = guessed + (unseen if result["routes"] else [])
        others = [tuple(records[i].get(key) for key in ("route_id", "endpoint_id", "basis", "checked_at")) for i in own if i not in gone]
        diff["new_route"].extend((name, (record["route_id"], None, "observed"), others) for record in new)
        for i in unseen:
            row = (name, tuple(records[i].get(key) for key in ("route_id", "endpoint_id", "basis")), records[i].get("checked_at"))
            if result["routes"]:
                diff["dropped"].append(row)
            else:
                diff["kept"].append((*row, result["note"]))
        diff["inferred"].extend((name, tuple(records[i].get(key) for key in ("route_id", "endpoint_id", "basis")),
                                 records[i].get("checked_at")) for i in guessed)
        at = own[-1] + 1 if own else len(records)
        records[at:at] = new  # after every record of the model, so the indexes in `gone` still hold
        for i in sorted(gone, reverse=True):
            del records[i]
        has_route = any(r["model_id"] == name and r.get("route_id") for r in records)
        if result["unidentified"] or not has_route and (name in unresolved or result["answered"]):
            unresolved.setdefault(name, result["note"])
            diff["unresolved"].append(name)
        elif has_route:
            unresolved.pop(name, None)
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
    section("observed before, not seen this run, dropped", [f"{name}: {dated(*record, day)}" for name, record, day in diff["dropped"]])
    section("inferred (no longer written), dropped", [f"{name}: {describe([record])} {day or 'no date'}" for name, record, day in diff["inferred"]])
    section("no route identified this run, kept", [f"{name}: {dated(*record, day)}" + (f" ({why})" if why else "") for name, record, day, why in diff["kept"]])
    section("covered by a settings or manual record", [f"{name}: {describe([after])}" for name, after in diff["covered"]])
    section("no usable answer, nothing recorded", [f"{name}: {note}" for name, note in diff["no_answer"]])
    for title, names in (("re-seen", diff["re_seen"]), ("unresolved", diff["unresolved"]), ("not registered, skipped", diff["not_registered"])):
        if names:
            lines.append(f"{title} ({len(names)}): " + ", ".join(names))
    return "\n".join(lines)


def positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text} is not 1 or more")
    return value


def main(argv=None, env=None, opener=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.market_collection.letsur_routes", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", action="append", default=[], help="model ids to check, comma separated (default: every model the gateway lists)")
    parser.add_argument("--samples", type=positive, default=6, help="times each model is asked; LETSUR picks one of a model's deployments per request (default: %(default)s)")
    parser.add_argument("--workers", type=positive, default=6, help="requests sent at the same time (default: %(default)s)")
    parser.add_argument("--include-images", action="store_true", help="also request one low-quality image per gpt-image model, once (costs money)")
    parser.add_argument("--bedrock-check", action="store_true",
                        help="test chatcmpl-<uuid> deployments for Bedrock's own performanceConfig check (3 x --samples more requests per such model)")
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
        models = [(model, kind(model, modality)) for model, modality in wanted if ALIASES.get(model, model) in MODEL_MODALITIES]
        results, answers, checks = {}, {}, {}
        pool = ThreadPoolExecutor(max_workers=args.workers)
        try:
            # Every (model, sample) job is queued at once and read back model by model, so the lines come in order. An image costs
            # money, so it is asked once; a video model is asked nothing.
            jobs = [(model, [pool.submit(probe, gateway, model, model_kind, args.include_images)
                             for _ in range(1 if model_kind in ("image", "video") else args.samples)]) for model, model_kind in models]
            for number, (model, futures) in enumerate(jobs, 1):
                answers[model] = [future.result() for future in futures]
                results[model] = summarize(model, answers[model])
                seen = ", ".join(f"{label}×{count}" for label, count in results[model]["tally"].items())
                print(f"[{number}/{len(jobs)}] {model}: {seen or 'not requested'}", file=sys.stderr)
            candidates = {model: uuid_deployments(model, answers[model]) for model in answers} if args.bedrock_check else {}
            tests = {model: {variant: [pool.submit(gateway.request, PATHS["chat"], {**payload("chat", model), **extra})
                                       for _ in range(args.samples)] for variant, extra in PERFORMANCE.items()}
                     for model, found in candidates.items() if found}
            for number, (model, futures) in enumerate(tests.items(), 1):
                checks[model] = {variant: [future.result() for future in got] for variant, got in futures.items()}
                shown = bedrock(candidates[model], checks[model])
                results[model] = summarize(model, answers[model], shown)
                print(f"[bedrock {number}/{len(tests)}] {model}: {len(shown)} of {len(candidates[model])} chatcmpl-<uuid> deployments"
                      " check Bedrock's own field", file=sys.stderr)
        finally:
            pool.shutdown(cancel_futures=True)  # after a refused key or Ctrl-C the jobs still queued are dropped unsent
    except (GatewayError, ValueError) as exc:
        print(f"refused: {mask(str(exc), (key, base))}", file=sys.stderr)
        return 2
    requests = [answer for samples in answers.values() for responses in samples for answer in responses.values()]
    if not any(result["routes"] for result in results.values()) and not any(answer.get("status") == 200 for answer in requests):
        print("no model gave a usable answer; nothing written", file=sys.stderr)  # a Gemini API refusal is a usable answer
        return 1
    proposal, diff = merge(current, results, today())
    diff["not_registered"] = [model for model, _ in wanted if ALIASES.get(model, model) not in MODEL_MODALITIES]
    text = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    if args.write:
        args.routes.write_text(text, encoding="utf-8")
    requests += [answer for got in checks.values() for variant in got.values() for answer in variant]
    print(format_diff(diff))
    print(f"\nchecked {len(answers)} models with {len(requests)} requests, gateway-reported cost {sum(map(cost, requests)):.4f}")
    print(f"proposal written to {args.output}; " + (f"{args.routes} overwritten" if args.write else f"{args.routes} left as it was (--write overwrites it)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
