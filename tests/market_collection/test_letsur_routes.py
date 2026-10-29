"""LETSUR route re-detection: classification rules, merge and diff, request shapes, key masking.

Every answer is small hand-written JSON shaped like the gateway's (made-up ids and names); nothing touches the network.
"""

import copy
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from scripts.market_collection import letsur_routes as lr

KEY, BASE = "test-key-0123456789", "https://gateway.test"
SAMPLE = Path(__file__).resolve().parents[2] / "samples/market_collection/demo_internal_routes.json"
UUID = "123e4567-e89b-12d3-a456-426614174000"
FILTERS = {"hate": {"filtered": False, "severity": "safe"}}
NO_REGION = "Azure 콘텐츠 필터 필드(배포 지역 표기 없음)"


def answer(body, status=200, model_id=None):
    return {"status": status, "headers": {"x-letsur-ai-gateway-model-id": model_id} if model_id else {}, "body": body, "error": None}


def chat(ident="chatcmpl-" + "A" * 29, **extra):
    return {"id": ident, "object": "chat.completion", "model": "m", "created": 1,
            "choices": [{"index": 0, "message": {"role": "assistant"}}], "usage": {"total_tokens": 1}, **extra}


def vercel_body(**routing):
    message = {"role": "assistant", "provider_specific_fields": {"provider_metadata": {"gateway": {"routing": routing}}}}
    return chat("gen_01ABCDEFGHJKMNPQRSTVWXYZ12", choices=[{"index": 0, "message": message}])


def classify(model, **answers):
    return lr.classify(model, answers)


# ---- classify: the rules ------------------------------------------------------------------------------------------------

def test_azure_named_deployment_gives_its_region_in_the_note():
    assert classify("m", chat=answer(chat(), model_id="m-azure-west-us-3")) == ("azure_foundry", None, "observed", "Azure 배포 지역 west-us-3")


@pytest.mark.parametrize("extra", [
    {"prompt_filter_results": [{"prompt_index": 0, "content_filter_results": FILTERS}]},
    {"choices": [{"index": 0, "message": {}, "provider_specific_fields": {"content_filter_results": FILTERS}}]},
    {"routing": {"serving_pipereplica": "replica-1"}},
    {"content_filters": [{"blocked": False}]},  # the Responses format
])
def test_azure_content_filter_fields_alone_show_azure_without_a_region(extra):
    assert classify("m", chat=answer({**chat(), **extra}, model_id="0" * 64)) == ("azure_foundry", None, "observed", NO_REGION)


def test_openrouter_generation_id_with_a_provider_puts_the_sub_provider_in_the_note_never_in_the_endpoint():
    body = chat("gen-1700000000-abcdefghijklmnopqrst", provider="Example Host")
    assert classify("m", chat=answer(body)) == ("openrouter", None, "observed", "이번 호출의 하위 공급자 Example Host(요청마다 바뀔 수 있음)")


def test_openrouter_without_a_provider_still_shows_through_the_usage_block_but_a_bare_id_does_not():
    ident = "gen-1700000000-abcdefghijklmnopqrst"
    assert classify("m", chat=answer(chat(ident, usage={"is_byok": False}))) == ("openrouter", None, "observed", "")
    assert classify("m", chat=answer(chat(ident)))[0] is None


def test_vercel_routing_puts_the_serving_provider_in_the_note_never_in_the_endpoint():
    body = vercel_body(resolvedProvider="alpha", finalProvider="alpha", fallbacksAvailable=["beta"])
    assert classify("m", chat=answer(body)) == ("vercel_ai_gateway", None, "observed", "이번 호출의 하위 공급자 alpha(요청마다 바뀔 수 있음)")


def test_vercel_fallback_notes_the_planned_provider_and_resolved_alone_is_enough():
    assert classify("m", chat=answer(vercel_body(resolvedProvider="alpha", finalProvider="beta"))) \
        == ("vercel_ai_gateway", None, "observed", "이번 호출의 하위 공급자 beta(계획 alpha, 요청마다 바뀔 수 있음)")
    assert classify("m", chat=answer(vercel_body(resolvedProvider="alpha"))) \
        == ("vercel_ai_gateway", None, "observed", "이번 호출의 하위 공급자 alpha(요청마다 바뀔 수 있음)")


def test_a_gateways_own_marks_beat_content_filter_fields_passed_through():
    body = {**vercel_body(finalProvider="alpha"), "prompt_filter_results": [{"content_filter_results": FILTERS}]}
    assert classify("m", chat=answer(body))[0] == "vercel_ai_gateway"


def test_gemini_answer_is_google_direct_without_telling_ai_studio_from_vertex():
    body = chat("x" * 22, vertex_ai_grounding_metadata=[], vertex_ai_safety_results=[])
    assert classify("gemini-x", chat=answer(body)) == ("google_direct", None, "observed", "Gemini 응답(AI Studio와 Vertex는 구별하지 못함)")


def test_vertex_fields_need_a_gemini_style_id():
    assert classify("m", chat=answer(chat("chatcmpl-" + UUID, vertex_ai_safety_results=[])))[0] is None


@pytest.mark.parametrize("ident, route", [("msg_011CgU28vnYHk9rwjN2bBdHG", "anthropic_direct"),
                                           ("msg_bdrk_011CgU28vnYHk9rwjN2bBdHG", "aws_bedrock"),
                                           ("msg_vrtx_011CgU28vnYHk9rwjN2bBdHG", "google_vertex")])
def test_claude_message_id_names_the_route(ident, route):
    body = {"id": ident, "type": "message", "role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    assert classify("claude-x", messages=answer(body)) == (route, None, "observed", "")


def test_an_unknown_message_id_and_a_translated_chat_answer_show_no_route():
    assert classify("claude-x", messages=answer({"id": "msg_other-" + "z" * 20, "type": "message"})) \
        == (None, None, None, "messages 200: id 형식 msg_<26자>, 경로 표시 없음")
    assert classify("claude-x", chat=answer(chat("chatcmpl-" + UUID)))[0] is None


def test_billing_payer_in_an_openai_response_object_is_openai_direct():
    body = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}, "access_programs": None}
    assert classify("gpt-x", responses=answer(body)) == ("openai_direct", None, "observed", "")
    translated = chat("resp_" + "x" * 40, billing={"payer": "developer"})  # a Responses-only model answered through chat
    assert classify("gpt-x", chat=answer(translated)) == ("openai_direct", None, "observed", "")


def test_a_chat_answer_with_no_azure_field_is_only_an_inference_for_an_openai_model():
    assert classify("gpt-x", chat=answer(chat())) == ("openai_direct", None, "inferred", "Azure 콘텐츠 필터 필드가 없음(필터를 끈 Azure 배포도 같아 보임)")
    assert classify("o3-x", chat=answer(chat()))[2] == "inferred"
    assert classify("glm-x", chat=answer(chat()))[:3] == (None, None, None)  # the same id shape from another maker proves nothing


def test_the_responses_answer_settles_what_the_chat_answer_only_suggested():
    billing = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}}
    assert classify("gpt-x", chat=answer(chat()), responses=answer(billing)) == ("openai_direct", None, "observed", "")
    filters = {"id": "resp_" + "x" * 40, "object": "response", "content_filters": [{"blocked": False}]}
    assert classify("gpt-x", chat=answer(chat()), responses=answer(filters))[:3] == ("azure_foundry", None, "observed")


def test_embeddings_show_no_route_unless_the_deployment_is_named():
    body = {"model": "text-embedding-x", "data": [{"embedding": [0.1]}], "usage": {"total_tokens": 1}}
    assert classify("text-embedding-x", embeddings=answer(body, model_id="0" * 64)) == (None, None, None, "embeddings 200: 경로 표시 없음")
    assert classify("text-embedding-x", embeddings=answer(body, model_id="text-embedding-x-azure-uk-south"))[0] == "azure_foundry"


def test_video_and_unrequested_images_stay_unresolved_with_the_reason():
    assert lr.classify("kling-v3-text-to-video", {}) == (None, None, None, "렛서 영상 API는 자체 입력 형식이라 생성 없이 경로가 드러나지 않음(호출하지 않음)")
    assert lr.classify("gpt-image-2", {}) == (None, None, None, "이미지 생성은 비용이 들어 호출하지 않음")


def test_an_image_answer_without_azure_fields_is_an_inference_and_with_them_an_observation():
    plain = {"created": 1, "data": [{"b64_json": "x", "revised_prompt": None}], "usage": {"total_tokens": 2}}
    assert classify("gpt-image-2", images=answer(plain))[:3] == ("openai_direct", None, "inferred")
    filtered = {**plain, "data": [{"b64_json": "x", "content_filter_results": FILTERS}]}
    assert classify("gpt-image-2", images=answer(filtered)) == ("azure_foundry", None, "observed", NO_REGION)


# ---- classify: what an unresolved note keeps ---------------------------------------------------------------------------

def test_unresolved_note_keeps_the_id_shape_and_unusual_fields_but_never_the_random_part():
    assert classify("glm-x", chat=answer(chat("chatcmpl-" + UUID))) == (None, None, None, "chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음")
    assert classify("glm-x", chat=answer(chat(UUID)))[3] == "chat 200: id 형식 <uuid>, 경로 표시 없음"
    assert classify("glm-x", chat=answer(chat("Unzhari6KuzI2roP_K26EQ")))[3] == "chat 200: id 형식 <22자>, 경로 표시 없음"
    extra = chat(system_fingerprint="fp", service_tier="default")
    assert classify("glm-x", chat=answer(extra))[3] == "chat 200: id 형식 chatcmpl-<29자>, 경로 표시 없음, 그 밖의 필드 service_tier, system_fingerprint"
    many = chat(**{f"field_{n}": n for n in range(8)})  # the note names six and counts the rest
    assert classify("glm-x", chat=answer(many))[3].endswith("그 밖의 필드 field_0, field_1, field_2, field_3, field_4, field_5 외 2개")


def test_failed_requests_keep_the_status_and_the_gateway_code_only():
    detail = "detail that must not be copied"
    cases = [(answer({"type": "service_error", "title": "t", "status": 404, "detail": detail, "error_ref": "err_1"}, 404), "404 service_error"),
             (answer({"error": {"code": "validation_error", "message": detail, "model": "m"}}, 400), "400 validation_error"),
             (answer({"type": "https://gateway.test/errors/model_not_found", "detail": detail}, 404), "404 model_not_found"),
             (answer({"message": detail}, 502), "502"), (answer(None, 500), "500"),
             ({"status": None, "headers": {}, "body": None, "error": "TimeoutError"}, "응답 없음(TimeoutError)"),
             ({"status": None, "headers": {}, "body": None, "error": None}, "응답 없음")]
    for failed, text in cases:
        route, endpoint, basis, note = classify("m", chat=failed)
        assert (route, endpoint, basis, note) == (None, None, None, f"chat {text}")
        assert detail not in note and "gateway.test" not in note
    two = classify("gpt-x", chat=answer(None, 404), responses=cases[5][0])  # every endpoint asked is named
    assert two[3] == "chat 404; responses 응답 없음(TimeoutError)"


def test_answered_is_false_for_silence_and_for_trouble_that_says_nothing_about_the_model():
    silent = {"status": None, "headers": {}, "body": None, "error": "TimeoutError"}
    assert not lr.answered({}) and not lr.answered({"chat": silent})
    assert not any(lr.answered({"chat": answer(None, status)}) for status in (408, 429, 500, 503))
    assert all(lr.answered({"chat": answer(None, status)}) for status in (200, 400, 403, 404))
    # one firm answer is enough only when it is a 200; a 404 next to a timeout leaves the other endpoint unknown
    assert lr.answered({"chat": answer(None, 500), "responses": answer(chat())})
    assert lr.answered({"chat": answer(None, 404), "responses": answer(None, 404)})
    assert not lr.answered({"chat": silent, "responses": answer(None, 404)})


# ---- what to ask -------------------------------------------------------------------------------------------------------

def test_kind_and_plan_decide_which_endpoints_a_model_gets():
    assert [lr.kind(m) for m in ("claude-opus-5", "text-embedding-3-small", "gpt-image-2", "kling-v3-text-to-video", "gemini-3-pro-image", "glm-5")] \
        == ["claude", "embedding", "image", "video", "chat", "chat"]
    assert lr.kind("something-new", ("video",)) == "video"
    assert lr.plan("gpt-5.6", "chat", False) == lr.plan("o3-mini", "chat", False) == ["chat", "responses"]
    assert lr.plan("glm-5", "chat", False) == ["chat"] and lr.plan("claude-x", "claude", False) == ["messages"]
    assert lr.plan("text-embedding-x", "embedding", False) == ["embeddings"] and lr.plan("kling-x", "video", True) == []
    assert lr.plan("gpt-image-2", "image", False) == [] and lr.plan("gpt-image-2", "image", True) == ["images"]


class FakeGateway:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def probe(self, name, model):
        self.calls.append(name)
        return self.answers[name]


def test_probe_stops_as_soon_as_the_route_is_observed():
    gateway = FakeGateway({"chat": answer(chat(), model_id="gpt-x-azure-uk-south")})
    assert list(lr.probe(gateway, "gpt-x", "chat", False)) == ["chat"] and gateway.calls == ["chat"]


def test_probe_asks_the_responses_endpoint_only_while_the_chat_answer_leaves_doubt():
    billing = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}}
    gateway = FakeGateway({"chat": answer(chat()), "responses": answer(billing)})
    responses = lr.probe(gateway, "gpt-x", "chat", False)
    assert gateway.calls == ["chat", "responses"] and lr.classify("gpt-x", responses)[2] == "observed"
    other = FakeGateway({"chat": answer(chat())})
    lr.probe(other, "glm-x", "chat", False)
    assert other.calls == ["chat"]


def test_probe_makes_no_request_for_video_nor_for_images_without_the_flag():
    gateway = FakeGateway({})
    assert lr.probe(gateway, "kling-x", "video", True) == {} and lr.probe(gateway, "gpt-image-2", "image", False) == {} and gateway.calls == []


# ---- the gateway: request shapes, masking ------------------------------------------------------------------------------

class FakeAnswer:
    def __init__(self, status, headers, raw):
        self.status, self.headers, self.raw = status, headers, raw

    def read(self, size=-1):
        return self.raw if size < 0 else self.raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for urllib's opener: `respond(path, body)` returns (status, headers, payload) or raises like a dead connection."""

    def __init__(self, respond):
        self.respond, self.requests = respond, []

    def open(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        path = urllib.parse.urlsplit(request.full_url).path
        self.requests.append({"method": request.get_method(), "url": request.full_url, "path": path, "body": body, "timeout": timeout,
                              "headers": {name.lower(): value for name, value in request.header_items()}})
        status, headers, payload = self.respond(path, body)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", headers, io.BytesIO(raw))
        return FakeAnswer(status, headers, raw)


def test_each_endpoint_gets_its_own_request_shape_and_the_key_stays_in_headers():
    opener = FakeOpener(lambda path, body: (200, {}, {"id": "x", "data": [{"id": "model-x"}]}))
    gateway = lr.Gateway(BASE + "/", KEY, 7, opener)
    for name in lr.PATHS:
        gateway.probe(name, "model-x")
    gateway.models()
    sent = {r["path"]: r for r in opener.requests}
    assert [r["method"] for r in opener.requests] == ["POST"] * 5 + ["GET"] and sent["/v1/models"]["body"] is None
    assert all(r["url"].startswith(BASE + "/v1/") and KEY not in r["url"] and r["timeout"] == 7 for r in opener.requests)
    assert sent["/v1/chat/completions"]["body"] == {"model": "model-x", "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 16}
    assert sent["/v1/responses"]["body"] == {"model": "model-x", "input": "Reply with: ok", "max_output_tokens": 16}
    assert sent["/v1/embeddings"]["body"] == {"model": "model-x", "input": "ok"}
    assert sent["/v1/images/generations"]["body"]["quality"] == "low" and sent["/v1/images/generations"]["body"]["n"] == 1
    assert sent["/v1/chat/completions"]["headers"]["authorization"] == f"Bearer {KEY}"
    message = sent["/v1/messages"]  # the Anthropic format has its own key header
    assert message["headers"]["x-api-key"] == KEY and message["headers"]["anthropic-version"] and "authorization" not in message["headers"]
    assert message["body"] == {"model": "model-x", "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with: ok"}]}


def test_a_gemini_image_model_is_asked_not_to_draw():
    opener = FakeOpener(lambda path, body: (200, {}, {}))
    lr.Gateway(BASE, KEY, 5, opener).probe("chat", "gemini-3-pro-image")
    assert "Do not draw" in opener.requests[0]["body"]["messages"][0]["content"]


def test_key_and_base_url_are_masked_and_long_text_is_cut_on_receipt():
    long_text = "A" * 5000
    body = {"id": "x", "provider": f"p-{KEY}", "note": f"see {BASE}/v1", "data": [{"b64_json": long_text}]}
    opener = FakeOpener(lambda path, _: (200, {"X-Letsur-AI-Gateway-Model-Id": f"m-{KEY}", "Set-Cookie": "kept-out"}, body))
    got = lr.Gateway(BASE, KEY, 5, opener).probe("chat", "m")
    dumped = json.dumps(got)
    assert KEY not in dumped and BASE not in dumped and long_text not in dumped
    assert got["body"]["provider"] == "p-***" and got["body"]["note"] == "see ***/v1"
    assert got["body"]["data"][0]["b64_json"] == "A" * 32 + "<5000 chars>"
    assert got["headers"] == {"x-letsur-ai-gateway-model-id": "m-***"}  # only the headers this tool reads are kept


def test_failed_requests_come_back_as_data_without_the_exception_text():
    def respond(path, body):
        if path == "/v1/chat/completions":
            raise TimeoutError(f"timed out talking to {BASE}")
        if path == "/v1/messages":
            raise urllib.error.URLError(f"cannot reach {BASE}")
        return 502, {}, b"<html>bad gateway</html>"
    gateway = lr.Gateway(BASE, KEY, 5, FakeOpener(respond))
    assert gateway.probe("chat", "m") == {"status": None, "headers": {}, "body": None, "error": "TimeoutError"}
    assert gateway.probe("messages", "m")["error"] == "URLError"
    assert gateway.probe("embeddings", "m") == {"status": 502, "headers": {}, "body": None, "error": None}


def test_a_dead_connection_while_reading_an_error_body_is_still_no_answer():
    class Dying(io.BytesIO):
        def read(self, size=-1):
            raise TimeoutError()

    class Opener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 502, "bad gateway", {}, Dying())
    assert lr.Gateway(BASE, KEY, 5, Opener()).probe("chat", "m") == {"status": None, "headers": {}, "body": None, "error": "TimeoutError"}


def test_a_refused_key_stops_the_run_and_a_redirect_is_never_followed():
    gateway = lr.Gateway(BASE, KEY, 5, FakeOpener(lambda path, body: (401, {}, {"error": {"code": "invalid_api_key"}})))
    with pytest.raises(lr.GatewayError) as refused:
        gateway.probe("chat", "m")
    assert KEY not in str(refused.value)
    assert any(isinstance(handler, lr._NoRedirect) for handler in lr.Gateway(BASE, KEY, 5).opener.handlers)
    assert lr._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.test/") is None


def test_model_list_gives_ids_with_their_modality_and_fails_loudly():
    listing = {"object": "list", "data": [{"id": "a", "modality": ["text"]}, {"id": "b", "modality": ["video"]}, {"id": "c"}, {"nope": 1}]}
    assert lr.Gateway(BASE, KEY, 5, FakeOpener(lambda p, b: (200, {}, listing))).models() == [("a", ("text",)), ("b", ("video",)), ("c", ())]
    with pytest.raises(lr.GatewayError, match="404 route_not_found"):
        lr.Gateway(BASE, KEY, 5, FakeOpener(lambda p, b: (404, {}, {"error": {"code": "route_not_found"}}))).models()


# ---- merge and diff ----------------------------------------------------------------------------------------------------

def record(model, route, endpoint=None, basis="observed", **more):
    return {"model_id": model, "route_id": route, "endpoint_id": endpoint, "basis": basis, **more}


def result(route=None, endpoint=None, basis=None, note="", answered=True):
    return {"route_id": route, "endpoint_id": endpoint, "basis": basis, "note": note, "answered": answered}


CURRENT = {
    "is_demo": False, "purpose": "경로 파일", "basis_note": "note",
    "records": [record("a", "openrouter"), record("b", "azure_foundry"), record("c", "google_direct", basis="settings"),
                record("c", "openrouter", basis="settings"), record("d", "anthropic_direct"), record("e", "vercel_ai_gateway", "x"),
                record("claude-haiku-4-5", "anthropic_direct"), record("m", "fal", basis="manual", note="by hand"),
                record("g", "openrouter", "Host")],  # an endpoint a person once put on an observed record
    "unresolved": {"u": "old note", "w": "old note"},
}
SEEN = "이번 호출의 하위 공급자 Example Host(요청마다 바뀔 수 있음)"
RESULTS = {
    "a": result("vercel_ai_gateway", None, "observed", SEEN),
    "b": result("azure_foundry", None, "observed", "Azure 배포 지역 uk-south"),
    "c": result("google_direct", None, "observed", "Gemini 응답(AI Studio와 Vertex는 구별하지 못함)"),
    "d": result(note="chat 404 model_not_found"),
    "e": result(note="chat 응답 없음(TimeoutError)", answered=False),
    "f": result("aws_bedrock", None, "observed"),
    "g": result("openrouter", None, "observed", SEEN),
    "u": result("openai_direct", None, "inferred", "guess"),
    "w": result(note="new evidence"),
    "x": result(note="chat 500", answered=False),
    "claude-haiku-4-5-20251001": result("anthropic_direct", None, "observed"),
    "m": result("fal", None, "observed"),
}


def test_merge_applies_the_results_model_by_model():
    before = copy.deepcopy(CURRENT)
    doc, diff = lr.merge(CURRENT, RESULTS, "2026-10-29")
    assert CURRENT == before  # the document it was given is not edited
    assert doc["records"] == [
        record("a", "vercel_ai_gateway", note=SEEN, checked_at="2026-10-29"),  # replaced where the old one was
        record("b", "azure_foundry", note="Azure 배포 지역 uk-south", checked_at="2026-10-29"),
        record("c", "google_direct", basis="settings"), record("c", "openrouter", basis="settings"),  # a settings record stays
        record("e", "vercel_ai_gateway", "x"),  # no usable answer: kept as it was
        record("claude-haiku-4-5", "anthropic_direct", checked_at="2026-10-29"),  # LETSUR's dated id maps to ours
        record("m", "fal", basis="manual", note="by hand"),
        record("g", "openrouter", note=SEEN, checked_at="2026-10-29"),  # the tool never carries an endpoint over
        record("f", "aws_bedrock", checked_at="2026-10-29"),
        record("u", "openai_direct", basis="inferred", note="guess", checked_at="2026-10-29")]
    assert doc["unresolved"] == {"d": "chat 404 model_not_found", "w": "new evidence", "x": "chat 500"}
    assert (doc["is_demo"], doc["purpose"], doc["basis_note"]) == (False, "경로 파일", "note") and list(doc)[:3] == ["is_demo", "purpose", "basis_note"]
    assert diff["added"] == [("f", ("aws_bedrock", None, "observed"), False), ("u", ("openai_direct", None, "inferred"), True)]
    assert diff["changed"] == [("a", [("openrouter", None, "observed")], ("vercel_ai_gateway", None, "observed")),
                               ("g", [("openrouter", "Host", "observed")], ("openrouter", None, "observed"))]
    assert diff["unchanged"] == ["b", "claude-haiku-4-5"]
    assert diff["now_unresolved"] == [("d", [("anthropic_direct", None, "observed")], "chat 404 model_not_found")]
    assert diff["unresolved"] == [("w", [], "new evidence"), ("x", [], "chat 500")]
    assert diff["kept"] == [("e", [("vercel_ai_gateway", "x", "observed")], "chat 응답 없음(TimeoutError)")]
    assert diff["covered"] == [("c", ("google_direct", None, "observed")), ("m", ("fal", None, "observed"))]


def test_a_model_a_person_gave_a_route_is_not_listed_as_unresolved():
    current = {"records": [record("m", "fal", basis="manual"), record("s", "openrouter", basis="settings")],
               "unresolved": {"m": "old", "s": "old", "other": "kept"}}
    doc, diff = lr.merge(current, {"m": result(note="new"), "s": result(note="new"), "n": result(note="new")}, "2026-10-29")
    assert doc["records"] == current["records"] and doc["unresolved"] == {"n": "new", "other": "kept"}
    assert [name for name, _, _ in diff["unresolved"]] == ["n"]  # m and s are not unresolved: a person named their routes


def test_a_second_run_with_the_same_results_changes_nothing_but_the_date():
    first, _ = lr.merge(CURRENT, RESULTS, "2026-10-29")
    second, diff = lr.merge(first, RESULTS, "2026-10-30")
    assert [{k: v for k, v in r.items() if k != "checked_at"} for r in second["records"]] == [{k: v for k, v in r.items() if k != "checked_at"} for r in first["records"]]
    assert not (diff["added"] or diff["changed"] or diff["now_unresolved"]) and second["unresolved"] == first["unresolved"]


def test_a_run_over_some_models_leaves_the_others_alone():
    doc, diff = lr.merge(CURRENT, {"a": result("vercel_ai_gateway", None, "observed", SEEN)}, "2026-10-29")
    assert doc["records"][0]["route_id"] == "vercel_ai_gateway" and doc["records"][1:] == CURRENT["records"][1:]
    assert doc["unresolved"] == CURRENT["unresolved"]
    assert [part for part, rows in diff.items() if rows] == ["changed"]


def test_merge_into_a_file_without_an_unresolved_map_adds_one_at_the_end():
    plain = {"is_demo": False, "records": []}
    doc, _ = lr.merge(plain, {"v": result(note="영상은 호출하지 않음", answered=False)}, "2026-10-29")
    assert doc == {"is_demo": False, "records": [], "unresolved": {"v": "영상은 호출하지 않음"}}


def test_diff_text_lists_every_change_and_counts_the_rest():
    _, diff = lr.merge(CURRENT, RESULTS, "2026-10-29")
    lines = lr.format_diff(diff).splitlines()
    for expected in ("added (2)", "  f: aws_bedrock [observed]", "  u: openai_direct [inferred] (was unresolved)",
                     "changed (2)", "  a: openrouter [observed] -> vercel_ai_gateway [observed]",
                     "  g: openrouter/Host [observed] -> openrouter [observed]",
                     "now unresolved (1)", "  d: was anthropic_direct [observed] -> chat 404 model_not_found",
                     "kept, no usable answer (1)", "  e: vercel_ai_gateway/x [observed] (chat 응답 없음(TimeoutError))",
                     "covered by a settings or manual record (2)", "unchanged (2)", "unresolved (2): w, x"):
        assert expected in lines


def test_load_routes_accepts_the_committed_file_and_an_empty_merge_keeps_its_records():
    current = lr.load_routes(SAMPLE)
    doc, diff = lr.merge(current, {}, "2026-10-29")
    assert doc["records"] == current["records"] and not any(diff.values())


@pytest.mark.parametrize("text", ["not json", "[]", '{"records": {}}', '{"records": [1]}', '{"records": [{"route_id": "x"}]}',
                                  '{"records": [], "unresolved": []}'])
def test_load_routes_refuses_a_file_that_is_not_a_routes_document(tmp_path, text):
    path = tmp_path / "routes.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        lr.load_routes(path)


# ---- the command -------------------------------------------------------------------------------------------------------

LISTING = {"object": "list", "data": [{"id": "gpt-x", "modality": ["text"]}, {"id": "claude-x", "modality": ["text"]},
                                      {"id": "glm-x", "modality": ["text"]}, {"id": "kling-x", "modality": ["video"]},
                                      {"id": "gpt-image-2", "modality": ["text"]}, {"id": "or-x", "modality": ["text"]},
                                      {"id": "vc-x", "modality": ["text"]}]}


def gateway_answers(path, body):
    if path == "/v1/models":
        return 200, {}, LISTING
    cost = {"x-letsur-ai-gateway-response-cost": "0.001"}
    if path == "/v1/chat/completions" and body["model"] == "gpt-x":
        return 200, {**cost, "x-letsur-ai-gateway-model-id": "gpt-x-azure-uk-south"}, chat()
    if path == "/v1/chat/completions" and body["model"] == "or-x":
        return 200, cost, chat("gen-1700000000-abcdefghijklmnopqrst", provider="Example Host")
    if path == "/v1/chat/completions" and body["model"] == "vc-x":
        return 200, cost, vercel_body(resolvedProvider="alpha", finalProvider="beta")
    if path == "/v1/chat/completions":
        return 200, cost, chat("chatcmpl-" + UUID, provider_note=f"{KEY} {BASE}")
    if path == "/v1/messages":
        return 200, {}, {"id": "msg_011CgU28vnYHk9rwjN2bBdHG", "type": "message", "estimated_cost": {"amount": "0.002"}}
    if path == "/v1/images/generations":
        return 200, cost, {"created": 1, "data": [{"b64_json": "A" * 3000}], "usage": {"total_tokens": 2}}
    return 404, {}, {"error": {"code": "route_not_found"}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    routes, output = tmp_path / "routes.json", tmp_path / "out" / "proposal.json"
    routes.write_text(json.dumps(CURRENT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(lr, "today", lambda: "2026-10-29")
    opener = FakeOpener(gateway_answers)

    def run(*args, env=None, opener=opener):
        env = {lr.BASE_ENV: BASE, lr.KEY_ENV: KEY} if env is None else env
        return lr.main(["--routes", str(routes), "--output", str(output), *args], env=env, opener=opener)
    return run, routes, output, opener


def test_run_over_every_listed_model_writes_a_proposal_and_leaves_the_routes_file(setup, capsys):
    run, routes, output, opener = setup
    before = routes.read_bytes()
    assert run() == 0
    asked = [(r["path"], (r["body"] or {}).get("model")) for r in opener.requests]
    assert asked == [("/v1/models", None), ("/v1/messages", "claude-x"), ("/v1/chat/completions", "glm-x"), ("/v1/chat/completions", "gpt-x"),
                     ("/v1/chat/completions", "or-x"), ("/v1/chat/completions", "vc-x")]
    # gpt-image-2 and the video model were listed but never requested; gpt-x was settled by its first answer
    text = output.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n" and "경로 파일" in text
    proposal = json.loads(text)
    assert [r for r in proposal["records"] if r["model_id"] in ("claude-x", "gpt-x", "or-x", "vc-x")] == [  # no gateway record gets an endpoint
        record("claude-x", "anthropic_direct", checked_at="2026-10-29"),
        record("gpt-x", "azure_foundry", note="Azure 배포 지역 uk-south", checked_at="2026-10-29"),
        record("or-x", "openrouter", note="이번 호출의 하위 공급자 Example Host(요청마다 바뀔 수 있음)", checked_at="2026-10-29"),
        record("vc-x", "vercel_ai_gateway", note="이번 호출의 하위 공급자 beta(계획 alpha, 요청마다 바뀔 수 있음)", checked_at="2026-10-29")]
    assert proposal["unresolved"]["glm-x"] == "chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음, 그 밖의 필드 provider_note"
    assert proposal["unresolved"]["kling-x"] == "렛서 영상 API는 자체 입력 형식이라 생성 없이 경로가 드러나지 않음(호출하지 않음)"
    assert proposal["unresolved"]["gpt-image-2"] == "이미지 생성은 비용이 들어 호출하지 않음"
    assert routes.read_bytes() == before  # never overwritten without --write
    captured = capsys.readouterr()
    assert "added (4)" in captured.out and "checked 7 models with 5 requests, gateway-reported cost 0.0060" in captured.out
    assert "[2/7] glm-x: unresolved" in captured.err and "[5/7] kling-x: not requested" in captured.err
    for secret in (KEY, BASE):
        assert secret not in captured.out + captured.err + text


def test_write_overwrites_the_routes_file_with_the_same_proposal(setup, capsys):
    run, routes, output, _ = setup
    assert run("--write") == 0
    assert routes.read_bytes() == output.read_bytes() and "gpt-x" in routes.read_text(encoding="utf-8")
    assert "overwritten" in capsys.readouterr().out


def test_models_option_checks_only_those_models_without_listing(setup):
    run, _, output, opener = setup
    assert run("--models", "gpt-x,glm-x", "--models", "claude-x") == 0
    assert sorted(r["path"] for r in opener.requests) == ["/v1/chat/completions", "/v1/chat/completions", "/v1/messages"]
    assert "kling-x" not in json.loads(output.read_text(encoding="utf-8"))["unresolved"]


def test_image_models_are_requested_only_with_the_flag(setup):
    run, _, output, opener = setup
    assert run("--models", "gpt-image-2", "--include-images") == 0
    image = [r for r in opener.requests if r["path"] == "/v1/images/generations"]
    assert len(image) == 1 and image[0]["body"]["quality"] == "low"
    added = [r for r in json.loads(output.read_text(encoding="utf-8"))["records"] if r["model_id"] == "gpt-image-2"]
    assert added[0]["basis"] == "inferred" and "3000" not in json.dumps(added)


def test_a_refused_key_ends_the_run_with_nothing_written(setup, capsys):
    run, _, output, opener = setup
    assert run(opener=FakeOpener(lambda path, body: (401, {}, {"error": {"code": "invalid_api_key"}}))) == 2
    assert not output.exists() and KEY not in capsys.readouterr().err


def test_a_run_where_nothing_answered_writes_nothing(setup, capsys):
    run, _, output, _ = setup
    assert run("--models", "gpt-x", opener=FakeOpener(lambda path, body: (404, {}, {"error": {"code": "route_not_found"}}))) == 1
    assert not output.exists() and "nothing written" in capsys.readouterr().err


@pytest.mark.parametrize("env", [{}, {lr.BASE_ENV: BASE}, {lr.KEY_ENV: KEY}, {lr.BASE_ENV: "http://gateway.test", lr.KEY_ENV: KEY}])
def test_the_run_is_refused_without_both_variables_and_over_https(setup, capsys, env):
    run, _, output, opener = setup
    assert run(env=env) == 2 and not opener.requests and not output.exists()
    assert KEY not in capsys.readouterr().err


def test_output_may_not_be_the_routes_file_without_write(tmp_path, setup):
    _, routes, _, opener = setup
    env = {lr.BASE_ENV: BASE, lr.KEY_ENV: KEY}
    assert lr.main(["--routes", str(routes), "--output", str(routes)], env=env, opener=opener) == 2 and not opener.requests
    assert lr.main(["--routes", str(tmp_path / "missing.json"), "--output", str(tmp_path / "o.json")], env=env, opener=opener) == 2
