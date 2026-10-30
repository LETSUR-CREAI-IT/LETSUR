"""LETSUR route re-detection: classification rules, a model's samples together, merge and diff, request shapes, key masking.

Every answer is small hand-written JSON shaped like the gateway's (made-up ids, names and deployment headers); nothing touches
the network.
"""

import copy
import io
import json
import threading
import urllib.error
import urllib.parse
from collections import Counter
from pathlib import Path

import pytest

from scripts.market_collection import letsur_routes as lr
from scripts.market_collection.models import MODEL_MODALITIES

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

@pytest.mark.parametrize("header, route, note", [
    ("m-azure-west-us-3", "azure_foundry", "Azure 배포 지역 west-us-3"),
    ("gpt-5.4-2026-03-05-azure-canada-east", "azure_foundry", "Azure 배포 지역 canada-east"),  # dots and dates in the model part
    ("gpt-6.1-terra-bedrock-us-east-1", "aws_bedrock", "Bedrock 배포 지역 us-east-1"),
    ("m-vertex-europe-west4", "google_vertex", "Vertex 배포 지역 europe-west4"),
    ("gpt-6.1-terra-openai-letsur-2", "openai_direct", "OpenAI 직접 배포 letsur-2"),
    ("m-openai", "openai_direct", "OpenAI 직접 배포"),
])
def test_a_named_deployment_in_the_header_names_the_route_and_where(header, route, note):
    assert classify("m", chat=answer(chat(), model_id=header)) == (route, None, "observed", note)


@pytest.mark.parametrize("header", ["0" * 64, "m-azure", "m-bedrock", "m-vertex", "azure-m", "m-openaix", "bedrock-us-east-1", ""])
def test_a_hash_or_a_name_without_its_place_names_no_route(header):
    assert classify("glm-x", chat=answer(chat("chatcmpl-" + UUID), model_id=header))[0] is None


def test_the_header_holds_for_every_endpoint_and_beats_what_the_body_looks_like():
    assert classify("m", embeddings=answer({"data": []}, model_id="m-bedrock-us-west-2"))[0] == "aws_bedrock"
    assert classify("claude-x", messages=answer({"id": "msg_01" + "x" * 20}, model_id="claude-x-bedrock-us-east-1"))[0] == "aws_bedrock"
    body = {**chat("chatcmpl-" + UUID, usage={"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}), "prompt_filter_results": []}
    assert classify("gpt-x", chat=answer(body, model_id="gpt-x-openai-letsur-2"))[0] == "openai_direct"


BEDROCK_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def test_answers_that_only_fit_a_route_name_none_and_keep_what_was_seen():
    """Earlier versions inferred a route from these shapes; a guess is no longer recorded (2026-10-30)."""
    bedrock_like = chat("chatcmpl-" + UUID, usage=BEDROCK_USAGE)  # what glm-4.7 and glm-5 answered
    assert classify("glm-x", chat=answer(bedrock_like, model_id="0" * 64)) == (None, None, None, "chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음")
    assert classify("gpt-x", chat=answer(bedrock_like))[:3] == (None, None, None)
    assert classify("gpt-x", chat=answer(chat())) == (None, None, None, "chat 200: id 형식 chatcmpl-<29자>, 경로 표시 없음")  # no Azure field
    assert classify("o3-x", chat=answer(chat()))[:3] == (None, None, None)
    image = {"created": 1, "data": [{"b64_json": "x", "revised_prompt": None}], "usage": {"total_tokens": 2}}
    assert classify("gpt-image-2", images=answer(image)) == (None, None, None, "images 200: 경로 표시 없음")


def test_a_mark_that_names_a_route_is_still_read_on_those_shapes():
    shaped = chat("chatcmpl-" + UUID, usage=BEDROCK_USAGE)
    assert classify("gpt-x", chat=answer({**shaped, "prompt_filter_results": []}))[:3] == ("azure_foundry", None, "observed")
    assert classify("gpt-x", chat=answer({**shaped, "billing": {"payer": "developer"}}))[:3] == ("openai_direct", None, "observed")
    billing = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}}
    assert classify("gpt-x", chat=answer(shaped), responses=answer(billing))[:3] == ("openai_direct", None, "observed")


def test_an_unmarked_chat_answer_still_gets_the_responses_probe_that_could_settle_it():
    gateway = FakeGateway({"chat": answer(chat("chatcmpl-" + UUID, usage=BEDROCK_USAGE)), "responses": answer({"id": "resp_" + "x" * 40, "object": "response"})})
    responses = lr.probe(gateway, "gpt-x", "chat", False)
    assert gateway.calls == ["chat", "responses"] and lr.classify("gpt-x", responses)[:3] == (None, None, None)


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


GEMINI_REFUSAL = {"type": "invalid_parameter", "title": "Invalid Parameter", "status": 400, "detail": "detail not copied"}
GEMINI_BODY = chat("x" * 22, vertex_ai_grounding_metadata=[], vertex_ai_safety_results=[])
REFUSED = "Gemini API가 Vertex 전용 안전 설정 칸(method)을 거절함"


def test_the_gemini_api_refuses_the_vertex_only_safety_field_and_vertex_takes_it():
    assert classify("gemini-x", chat=answer(GEMINI_REFUSAL, 400)) == ("google_direct", None, "observed", REFUSED)
    assert classify("gemini-x", chat=answer(GEMINI_BODY)) == ("google_vertex", None, "observed", "Vertex 전용 안전 설정 칸(method)을 받아들임")


def test_the_gemini_rules_hold_only_for_the_request_that_carried_the_field():
    assert classify("glm-x", chat=answer(GEMINI_REFUSAL, 400)) == (None, None, None, "chat 400 invalid_parameter")  # asked without it
    assert classify("m", chat=answer(GEMINI_BODY))[0] is None  # without the field the Gemini shape fits AI Studio and Vertex alike
    assert classify("gemini-x", chat=answer({"error": {"code": "validation_error"}}, 400))[0] is None  # another refusal
    assert classify("gemini-x", embeddings=answer(GEMINI_REFUSAL, 400))[0] is None
    assert classify("gemini-x", chat=answer(vercel_body(finalProvider="google")))[0] == "vercel_ai_gateway"  # the gateways take it


def test_vertex_fields_need_a_gemini_style_id():
    assert classify("m", chat=answer(chat("chatcmpl-" + UUID, vertex_ai_safety_results=[])))[0] is None
    assert classify("gemini-x", chat=answer(chat("chatcmpl-" + UUID, vertex_ai_safety_results=[])))[0] is None


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


def test_the_responses_answer_settles_what_the_chat_answer_left_open():
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


def test_an_image_answer_shows_azure_only_through_its_filter_fields():
    plain = {"created": 1, "data": [{"b64_json": "x", "revised_prompt": None}], "usage": {"total_tokens": 2}}
    assert classify("gpt-image-2", images=answer(plain))[:3] == (None, None, None)  # no Azure field is no proof of OpenAI
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
    assert lr.answered({}) and not lr.answered({"chat": silent})  # nothing asked (video, images) is a finding: the note says why
    assert not any(lr.answered({"chat": answer(None, status)}) for status in (408, 429, 500, 503))
    assert all(lr.answered({"chat": answer(None, status)}) for status in (200, 400, 403, 404))
    # one firm answer is enough only when it is a 200; a 404 next to a timeout leaves the other endpoint unknown
    assert lr.answered({"chat": answer(None, 500), "responses": answer(chat())})
    assert lr.answered({"chat": answer(None, 404), "responses": answer(None, 404)})
    assert not lr.answered({"chat": silent, "responses": answer(None, 404)})


# ---- a model's samples together ----------------------------------------------------------------------------------------

SILENT_ANSWER = {"status": None, "headers": {}, "body": None, "error": "TimeoutError"}
BILLING = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}}
B, V, O = "b" * 64, "v" * 64, "o" * 64  # deployment headers: usually a hash, the same on every answer of one deployment


def test_samples_that_land_on_different_deployments_give_every_route_with_its_count():
    alpha, beta = vercel_body(resolvedProvider="alpha", finalProvider="alpha"), vercel_body(resolvedProvider="alpha", finalProvider="beta")
    samples = [{"chat": answer(GEMINI_REFUSAL, 400)}, {"chat": answer(alpha, model_id=V)}, {"chat": answer(beta, model_id=V)},
               {"chat": answer(alpha, model_id=V)}, {"chat": answer(chat("gen-1700000000-a", provider="Baidu"), model_id=O)},
               {"chat": answer(chat("gen-1700000000-b", provider="Alibaba"), model_id=O)}]
    result = lr.summarize("gemini-x", samples)
    assert result["routes"] == {"google_direct": REFUSED,
                                "openrouter": "이번 확인의 하위 공급자 Alibaba·Baidu(요청마다 바뀔 수 있음)",
                                "vercel_ai_gateway": "이번 확인의 하위 공급자 alpha·beta(계획 alpha, 요청마다 바뀔 수 있음)"}
    assert result["tally"] == {"google_direct": 1, "openrouter": 2, "vercel_ai_gateway": 3} and list(result["tally"])[0] == "google_direct"
    assert (result["answered"], result["checked"], result["unidentified"]) == (True, True, False)
    assert lr.summarize("gemini-x", samples[::-1])["routes"] == result["routes"]  # the order of the answers does not matter


def test_deployments_that_answered_without_a_mark_are_counted_by_their_header():
    unmarked, other = chat("chatcmpl-" + UUID), chat("Unzhari6KuzI2roP_K26EQ")
    vercel = vercel_body(finalProvider="alpha")
    samples = [{"chat": answer(vercel, model_id=V)}, {"chat": answer(unmarked, model_id=B)}, {"chat": answer(unmarked, model_id=B)},
               {"chat": answer(other, model_id=O)}, {"chat": answer(vercel, model_id=V)}, {"chat": answer(unmarked, model_id=B)}]
    result = lr.summarize("glm-x", samples)
    assert result["routes"] == {"vercel_ai_gateway": "이번 확인의 하위 공급자 alpha(요청마다 바뀔 수 있음)"}
    assert result["tally"] == {"vercel_ai_gateway": 2, "unresolved": 4} and list(result["tally"]) == ["vercel_ai_gateway", "unresolved"]
    assert result["unidentified"] and result["note"] == (
        "배포 3개 중 2개 경로 표시 없음: chat 200: id 형식 <22자>, 경로 표시 없음; chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음")


def test_a_deployment_that_one_of_its_answers_identified_is_known_where_another_showed_nothing():
    samples = [{"chat": answer(chat(), model_id=O), "responses": answer(BILLING, model_id=O)},  # the Responses answer settled it
               {"chat": answer(chat(), model_id=O), "responses": answer(BILLING, model_id=V)}]
    result = lr.summarize("gpt-x", samples)
    assert (result["routes"], result["tally"], result["unidentified"]) == ({"openai_direct": ""}, {"openai_direct": 2}, False)
    # an answer without the header names no deployment, so nothing ties it to the one the Responses answer showed
    assert lr.summarize("gpt-x", [{"chat": answer(chat()), "responses": answer(BILLING)}])["note"] \
        == "배포 1개 중 1개 경로 표시 없음: chat 200: id 형식 chatcmpl-<29자>, 경로 표시 없음"


def test_errors_silence_and_nothing_asked_each_say_why_in_the_note():
    errors = lr.summarize("m", [{"chat": answer(None, 404)}, {"chat": answer({"error": {"code": "model_not_found"}}, 404)}])
    assert (errors["routes"], errors["unidentified"], errors["note"]) == ({}, True, "chat 404; chat 404 model_not_found")
    quiet = lr.summarize("m", [{"chat": SILENT_ANSWER}, {"chat": answer(None, 503)}])
    assert (quiet["answered"], quiet["checked"], quiet["unidentified"], quiet["tally"]) == (False, False, False, {"no answer": 2})
    assert quiet["note"] == "chat 503; chat 응답 없음(TimeoutError)"
    video = lr.summarize("kling-x", [{}])
    assert (video["answered"], video["checked"], video["unidentified"], video["tally"], video["note"]) == (True, False, False, {}, lr.NOT_PROBED["video"])
    mixed = lr.summarize("m", [{"chat": answer(vercel_body(finalProvider="alpha"), model_id=V)}, {"chat": SILENT_ANSWER}])
    assert mixed["tally"] == {"vercel_ai_gateway": 1, "no answer": 1} and not mixed["unidentified"]  # silence is no finding
    partial = lr.summarize("gpt-x", [{"chat": answer(chat(), model_id=O), "responses": SILENT_ANSWER}])
    assert partial["note"] == "배포 1개 중 1개 경로 표시 없음: chat 200: id 형식 chatcmpl-<29자>, 경로 표시 없음; responses 응답 없음(TimeoutError)"


def bedrock_answers(standard, bogus, unknown):
    """PERFORMANCE answers by the deployment header that answered each request; None is a refusal from every deployment."""
    def one(header):
        return answer(chat("chatcmpl-" + UUID), model_id=header) if header else answer({"type": "invalid_parameter"}, 400)
    return {"standard": [one(h) for h in standard], "bogus": [one(h) for h in bogus], "unknown": [one(h) for h in unknown]}


def test_a_deployment_that_answers_standard_and_an_unknown_field_but_never_bogus_is_bedrock():
    assert lr.bedrock({B}, bedrock_answers([B, V, B, B, V, B], [V] * 6, [B, B, V, B, V, V])) == {B}  # the gateway gave bogus to V
    assert lr.bedrock({B}, bedrock_answers([B] * 6, [None] * 6, [B] * 6)) == {B}  # the model's only deployment refused every bogus


def test_the_bedrock_check_needs_every_part_of_the_evidence():
    assert lr.bedrock({B}, bedrock_answers([B] * 6, [B, V, V, V, V, V], [B] * 6)) == set()  # it took bogus once: any value goes
    assert lr.bedrock({B}, bedrock_answers([B] * 6, [V] * 6, [V] * 6)) == set()  # never seen taking the unknown field
    assert lr.bedrock({B}, bedrock_answers([V] * 6, [V] * 6, [B] * 6)) == set()  # never seen taking standard
    # 2 of 12 answers: it would miss six bogus draws by chance a third of the time
    assert lr.bedrock({B}, bedrock_answers([B, V, V, V, V, V], [V] * 6, [B, V, V, V, V, V])) == set()
    assert lr.bedrock({B}, {**bedrock_answers([B] * 6, [], [B] * 6), "bogus": [SILENT_ANSWER] * 6}) == set()  # no bogus reached it


def test_only_unmarked_chat_answers_with_the_gateways_own_id_are_bedrock_candidates():
    samples = [{"chat": answer(chat("chatcmpl-" + UUID), model_id=B)}, {"chat": answer(chat("chatcmpl-" + UUID))},  # no header
               {"chat": answer(chat(), model_id=O)}, {"chat": answer(chat("chatcmpl-" + UUID), model_id="m-azure-uk-south")},
               {"embeddings": answer({"id": "chatcmpl-" + UUID, "data": []}, model_id=V)}]
    assert lr.uuid_deployments("glm-x", samples) == {B}


def test_a_deployment_the_bedrock_check_showed_counts_as_aws_bedrock():
    samples = [{"chat": answer(chat("chatcmpl-" + UUID), model_id=B)}, {"chat": answer(vercel_body(finalProvider="bedrock"), model_id=V)}]
    result = lr.summarize("glm-x", samples, {B})
    assert result["routes"] == {"aws_bedrock": lr.BEDROCK_NOTE, "vercel_ai_gateway": "이번 확인의 하위 공급자 bedrock(요청마다 바뀔 수 있음)"}
    assert not result["unidentified"] and lr.summarize("glm-x", samples)["unidentified"]


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


def test_probe_asks_every_endpoint_of_the_plan_even_when_the_first_answer_names_the_route():
    gateway = FakeGateway({"chat": answer(chat(), model_id="gpt-x-azure-uk-south"), "responses": answer(BILLING)})
    assert list(lr.probe(gateway, "gpt-x", "chat", False)) == ["chat", "responses"] and gateway.calls == ["chat", "responses"]


def test_probe_asks_the_responses_endpoint_only_while_the_chat_answer_leaves_doubt():
    billing = {"id": "resp_" + "x" * 40, "object": "response", "billing": {"payer": "developer"}}
    gateway = FakeGateway({"chat": answer(chat()), "responses": answer(billing)})
    responses = lr.probe(gateway, "gpt-x", "chat", False)
    assert gateway.calls == ["chat", "responses"] and lr.classify("gpt-x", responses)[2] == "observed"
    other = FakeGateway({"chat": answer(chat())})
    lr.probe(other, "glm-x", "chat", False)
    assert other.calls == ["chat"]


def test_the_openai_family_asks_responses_even_after_a_settled_chat_answer():
    """2026-10-30: gpt-5.5 answered through Azure on its chat requests and once through OpenAI's own API on Responses; a chat
    answer never shows OpenAI's deployment, so a settled one does not stop the Responses request."""
    azure = answer(chat(prompt_filter_results=[{"prompt_index": 0, "content_filter_results": FILTERS}]))
    gateway = FakeGateway({"chat": azure, "responses": answer(BILLING)})
    responses = lr.probe(gateway, "gpt-5.5", "chat", False)
    assert gateway.calls == ["chat", "responses"]
    assert lr.summarize("gpt-5.5", [responses])["routes"] == {"azure_foundry": NO_REGION, "openai_direct": ""}


def test_a_glm_answer_that_carries_back_the_request_id_came_from_zai():
    """Z.ai's API answers with the request_id it was sent; the other GLM deployments did not (10/30)."""
    sent = lr.ECHO + "0123456789abcdef"
    assert classify("glm-5.3", chat=answer(chat("202610301058398f926911e5e54d11", request_id=sent)))[:3] == ("zai_direct", None, "observed")
    assert classify("glm-5.3", chat=answer(chat("202610301058398f926911e5e54d11", request_id="2026103010583900")))[0] is None
    assert classify("glm-5.3", chat=answer(chat(UUID)))[0] is None


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
    assert "Do not draw" in opener.requests[0]["body"]["messages"][0]["content"] and "safety_settings" in opener.requests[0]["body"]


def test_only_a_gemini_models_chat_request_carries_the_vertex_only_safety_setting():
    opener = FakeOpener(lambda path, body: (200, {}, {}))
    gateway = lr.Gateway(BASE, KEY, 5, opener)
    for model in ("gemini-2.5-flash", "glm-5", "gpt-5.4", "deepseek-v4-pro"):
        gateway.probe("chat", model)
    gateway.probe("responses", "gemini-2.5-flash")
    sent = [r["body"] for r in opener.requests]
    assert sent[0] == {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 16,
                       "safety_settings": [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH", "method": "SEVERITY"}]}
    assert not any("safety_settings" in body for body in sent[1:])
    # only a GLM chat request carries a request_id, a new one each time
    assert sent[1]["request_id"].startswith(lr.ECHO) and not any("request_id" in body for body in sent[:1] + sent[2:])
    gateway.probe("chat", "glm-5")
    assert opener.requests[-1]["body"]["request_id"] != sent[1]["request_id"]


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
    opener = FakeOpener(lambda path, body: (401, {}, {"error": {"code": "invalid_api_key"}}))
    gateway = lr.Gateway(BASE, KEY, 5, opener)
    with pytest.raises(lr.GatewayError) as refused:
        gateway.probe("chat", "m")
    with pytest.raises(lr.GatewayError):
        gateway.probe("chat", "other")  # refused without being sent: other threads stop too
    assert KEY not in str(refused.value) and len(opener.requests) == 1
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


def result(*routes, note="", answered=True, checked=None, unidentified=None):
    """A model's result as summarize gives it; routes are (route_id, note) pairs. By default a model that answered but showed
    no route has a deployment that answered without its mark."""
    routes = dict(routes)
    return {"routes": routes, "tally": {}, "answered": answered, "checked": answered if checked is None else checked,
            "unidentified": answered and not routes if unidentified is None else unidentified, "note": note}


DAY, OLD = "2026-10-29", "2026-10-28"
SEEN = "이번 확인의 하위 공급자 Example Host(요청마다 바뀔 수 있음)"
HERE = "Bedrock 배포 지역 us-east-1"
SILENT = "chat 응답 없음(TimeoutError)"
UNKNOWN = "배포 2개 중 1개 경로 표시 없음: chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음"
CURRENT = {
    "is_demo": False, "purpose": "경로 파일", "basis_note": "note",
    "records": [
        record("a", "openrouter", note="hand written"),                           # seen again, nothing new to say: the note stays
        record("b", "azure_foundry", checked_at=OLD),                             # now on Bedrock only: the Azure record goes
        record("c", "google_direct", basis="settings"), record("c", "openrouter", basis="settings"),
        record("d", "anthropic_direct", checked_at=OLD),                          # answers, but shows no route now: kept
        record("e", "vercel_ai_gateway"),                                         # no answer at all: kept
        record("f", "openai_direct", basis="inferred", note="guess", checked_at=OLD),   # an earlier version's guess, now observed
        record("g", "openai_direct", checked_at=OLD), record("g", "azure_foundry", checked_at=OLD),  # both seen again
        record("claude-haiku-4-5", "anthropic_direct"),
        record("m", "fal", basis="manual", note="by hand"),
        record("p", "openrouter", endpoint="Host", checked_at=OLD),               # an endpoint a person put there stays
        record("q", "aws_bedrock", basis="inferred", note="Bedrock 모양", checked_at=OLD),  # answered without a mark: the guess goes
        record("r", "BytePlus ModelArk", basis="inferred", note="id와 단가"),       # a video model is asked nothing: the guess stays
        record("s", "fal", basis="manual"), record("s", "vercel_ai_gateway", checked_at=OLD),
    ],
    "unresolved": {"u": "human note", "w": "old note", "z": "old note", "k": "old note"},
}
RESULTS = {
    "a": result(("openrouter", "")),
    "b": result(("aws_bedrock", HERE)),
    "c": result(("google_direct", "Gemini API가 Vertex 전용 안전 설정 칸(method)을 거절함")),
    "d": result(note="chat 404 model_not_found"),
    "e": result(note=SILENT, answered=False),
    "f": result(("openai_direct", "")),
    "g": result(("azure_foundry", "Azure 배포 지역 uk-south"), ("openai_direct", "")),
    "h": result(("openrouter", SEEN)),
    "claude-haiku-4-5-20251001": result(("anthropic_direct", "")),
    "k": result(("vercel_ai_gateway", SEEN), note=UNKNOWN, unidentified=True),  # a route and a deployment that showed none
    "m": result(("fal", "")),
    "p": result(("openrouter", SEEN)),
    "q": result(note=UNKNOWN),
    "r": result(note=lr.NOT_PROBED["video"], checked=False, unidentified=False),
    "s": result(("openrouter", SEEN)),
    "u": result(note="new evidence"),
    "v": result(note=UNKNOWN),
    "w": result(("openrouter", SEEN)),
    "x": result(note=SILENT, answered=False),
    "z": result(note=SILENT, answered=False),
}


def test_merge_keeps_what_the_run_saw_and_drops_the_observed_routes_it_did_not_see():
    before = copy.deepcopy(CURRENT)
    doc, diff = lr.merge(CURRENT, RESULTS, DAY)
    assert CURRENT == before  # the document it was given is not edited
    assert doc["records"] == [
        record("a", "openrouter", note="hand written", checked_at=DAY),           # re-seen: dated; an empty new note keeps the old one
        record("b", "aws_bedrock", note=HERE, checked_at=DAY),                    # the route seen now; the Azure one not seen is gone
        record("c", "google_direct", basis="settings"), record("c", "openrouter", basis="settings"),
        record("d", "anthropic_direct", checked_at=OLD),                          # no route identified: nothing proved against it
        record("e", "vercel_ai_gateway"),
        record("f", "openai_direct", checked_at=DAY),                             # where the guess was
        record("g", "openai_direct", checked_at=DAY), record("g", "azure_foundry", note="Azure 배포 지역 uk-south", checked_at=DAY),
        record("claude-haiku-4-5", "anthropic_direct", checked_at=DAY),           # LETSUR's dated id maps to ours
        record("m", "fal", basis="manual", note="by hand"),                       # a person's record is never touched
        record("p", "openrouter", endpoint="Host", note=SEEN, checked_at=DAY),
        record("r", "BytePlus ModelArk", basis="inferred", note="id와 단가"),
        record("s", "fal", basis="manual"), record("s", "openrouter", note=SEEN, checked_at=DAY),
        record("h", "openrouter", note=SEEN, checked_at=DAY),                     # a model with no record yet goes to the end
        record("k", "vercel_ai_gateway", note=SEEN, checked_at=DAY),
        record("w", "openrouter", note=SEEN, checked_at=DAY)]
    assert (doc["is_demo"], doc["purpose"], doc["basis_note"]) == (False, "경로 파일", "note") and list(doc)[:3] == ["is_demo", "purpose", "basis_note"]
    assert diff["new_route"] == [("b", ("aws_bedrock", None, "observed"), []), ("f", ("openai_direct", None, "observed"), []),
                                 ("h", ("openrouter", None, "observed"), []), ("k", ("vercel_ai_gateway", None, "observed"), []),
                                 ("s", ("openrouter", None, "observed"), [("fal", None, "manual", None)]),
                                 ("w", ("openrouter", None, "observed"), [])]
    assert diff["re_seen"] == ["a", "claude-haiku-4-5", "g", "p"]
    assert diff["dropped"] == [("b", ("azure_foundry", None, "observed"), OLD), ("s", ("vercel_ai_gateway", None, "observed"), OLD)]
    assert diff["inferred"] == [("f", ("openai_direct", None, "inferred"), OLD), ("q", ("aws_bedrock", None, "inferred"), OLD)]
    assert diff["kept"] == [("d", ("anthropic_direct", None, "observed"), OLD, "chat 404 model_not_found"),
                            ("e", ("vercel_ai_gateway", None, "observed"), None, SILENT)]
    assert diff["covered"] == [("c", ("google_direct", None, "observed")), ("m", ("fal", None, "observed"))]


def test_unresolved_entries_follow_what_the_answers_showed_and_a_written_note_is_never_replaced():
    doc, diff = lr.merge(CURRENT, RESULTS, DAY)
    # d's old record stays, but its answers showed no route; k showed one route and a deployment without a mark
    assert doc["unresolved"] == {"u": "human note", "z": "old note", "k": "old note", "d": "chat 404 model_not_found", "q": UNKNOWN, "v": UNKNOWN}
    assert list(doc["unresolved"]) == ["u", "z", "k", "d", "q", "v"]              # the order of the file, new entries at the end
    assert diff["unresolved"] == ["d", "k", "q", "u", "v", "z"] and diff["no_answer"] == [("x", SILENT)]  # no answer, no record: no finding


def test_an_entry_goes_only_when_every_answer_showed_a_route():
    current = {"records": [record("m", "fal", basis="manual"), record("s", "openrouter", basis="settings"), record("o", "openrouter")],
               "unresolved": {"m": "old", "s": "old", "o": "old", "e": "old", "other": "kept"}}
    results = {"m": result(("fal", "")), "s": result(("openrouter", "")), "o": result(note="new"), "n": result(note="new"),
               "e": result(("vercel_ai_gateway", ""))}
    doc, diff = lr.merge(current, results, DAY)
    assert doc["records"] == [*current["records"], record("e", "vercel_ai_gateway", checked_at=DAY)]
    assert doc["unresolved"] == {"o": "old", "other": "kept", "n": "new"} and diff["unresolved"] == ["n", "o"]


def test_nothing_asked_is_a_finding_that_gets_its_entry():
    plain = {"is_demo": False, "records": []}
    doc, _ = lr.merge(plain, {"v": result(note="영상은 호출하지 않음", checked=False, unidentified=False)}, DAY)
    assert doc == {"is_demo": False, "records": [], "unresolved": {"v": "영상은 호출하지 않음"}}


def test_a_second_run_the_same_day_adds_nothing_and_changes_nothing():
    first, _ = lr.merge(CURRENT, RESULTS, DAY)
    second, diff = lr.merge(first, RESULTS, DAY)
    assert second == first and not (diff["new_route"] or diff["dropped"] or diff["inferred"])
    assert diff["re_seen"] == ["a", "b", "claude-haiku-4-5", "f", "g", "h", "k", "p", "s", "w"]


def test_the_file_follows_the_routes_letsur_uses_from_run_to_run():
    first, _ = lr.merge(CURRENT, RESULTS, DAY)
    both, diff = lr.merge(first, {"b": result(("aws_bedrock", HERE), ("azure_foundry", "Azure 배포 지역 uk-south"))}, "2026-10-31")
    assert [r for r in both["records"] if r["model_id"] == "b"] == [
        record("b", "aws_bedrock", note=HERE, checked_at="2026-10-31"), record("b", "azure_foundry", note="Azure 배포 지역 uk-south", checked_at="2026-10-31")]
    assert diff["re_seen"] == ["b"] and diff["new_route"] == [("b", ("azure_foundry", None, "observed"), [("aws_bedrock", None, "observed", "2026-10-31")])]
    back, diff = lr.merge(both, {"b": result(("azure_foundry", ""))}, "2026-11-01")
    assert [r for r in back["records"] if r["model_id"] == "b"] == [record("b", "azure_foundry", note="Azure 배포 지역 uk-south", checked_at="2026-11-01")]
    assert diff["dropped"] == [("b", ("aws_bedrock", None, "observed"), "2026-10-31")]


def test_a_route_seen_again_keeps_the_note_already_written():
    """A person's (or an earlier run's) note says more than a re-run's generic one, so only the date moves (2026-10-30)."""
    current = {"records": [record("q", "google_direct", note="10/29 이미지 모델 응답이 Gemini 형식", checked_at=OLD)]}
    doc, diff = lr.merge(current, {"q": result(("google_direct", "Gemini API가 Vertex 전용 안전 설정 칸(method)을 거절함"))}, DAY)
    assert doc["records"] == [record("q", "google_direct", note="10/29 이미지 모델 응답이 Gemini 형식", checked_at=DAY)] and diff["re_seen"] == ["q"]


def test_a_run_over_some_models_leaves_the_others_alone():
    doc, diff = lr.merge(CURRENT, {"h": result(("openrouter", SEEN))}, DAY)
    assert doc["records"][:-1] == CURRENT["records"] and doc["unresolved"] == CURRENT["unresolved"]
    assert [part for part, rows in diff.items() if rows] == ["new_route"]


def test_diff_text_names_what_was_added_dropped_and_kept():
    _, diff = lr.merge(CURRENT, RESULTS, DAY)
    diff["not_registered"] = ["gpt-4", "jev-latest"]
    lines = lr.format_diff(diff).splitlines()
    for expected in ("new route (6)", "  b: aws_bedrock [observed]", "  s: openrouter [observed] (also fal [manual])",
                     "observed before, not seen this run, dropped (2)", "  b: azure_foundry [observed] 2026-10-28",
                     "  s: vercel_ai_gateway [observed] 2026-10-28",
                     "inferred (no longer written), dropped (2)", "  f: openai_direct [inferred] 2026-10-28", "  q: aws_bedrock [inferred] 2026-10-28",
                     "no route identified this run, kept (2)", "  d: anthropic_direct [observed] 2026-10-28 (chat 404 model_not_found)",
                     "  e: vercel_ai_gateway [observed] no date (chat 응답 없음(TimeoutError))",
                     "covered by a settings or manual record (2)", "  c: google_direct [observed]",
                     "no usable answer, nothing recorded (1)", "  x: chat 응답 없음(TimeoutError)",
                     "re-seen (4): a, claude-haiku-4-5, g, p", "unresolved (6): d, k, q, u, v, z", "not registered, skipped (2): gpt-4, jev-latest"):
        assert expected in lines


def test_load_routes_accepts_the_committed_file_and_an_empty_merge_keeps_it_as_it_is():
    current = lr.load_routes(SAMPLE)
    doc, diff = lr.merge(current, {}, DAY)
    assert doc == {**current, "unresolved": current.get("unresolved", {})} and not any(diff.values())


@pytest.mark.parametrize("text", ["not json", "[]", '{"records": {}}', '{"records": [1]}', '{"records": [{"route_id": "x"}]}',
                                  '{"records": [], "unresolved": []}'])
def test_load_routes_refuses_a_file_that_is_not_a_routes_document(tmp_path, text):
    path = tmp_path / "routes.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        lr.load_routes(path)


# ---- the registry --------------------------------------------------------------------------------------------------------

def test_aliases_point_at_registered_models_and_only_from_models_the_registry_does_not_know():
    assert lr.ALIASES == {"claude-haiku-4-5-20251001": "claude-haiku-4-5", "byteplus-seedance-2.5": "seedance-2.5"}
    assert all(target in MODEL_MODALITIES for target in lr.ALIASES.values())
    assert not any(letsur_id in MODEL_MODALITIES for letsur_id in lr.ALIASES)


# ---- the command -------------------------------------------------------------------------------------------------------

LETSUR_ONLY = ("gpt-4", "jev-latest", "gpt-audio-1.5")  # in LETSUR's list, not in our registry
LISTING = {"object": "list", "data": [{"id": model, "modality": ["video" if model.startswith(("kling", "byteplus")) else "text"]} for model in (
    "gpt-5.4", "gpt-6.1-terra", "glm-5", "glm-5.2", "deepseek-v4-pro", "kimi-k2.5", "claude-haiku-4-5-20251001", "byteplus-seedance-2.5",
    "kling-v3-text-to-video", "gpt-image-2", *LETSUR_ONLY)]}
SHAPED = chat("chatcmpl-" + UUID, usage=BEDROCK_USAGE)


def gateway_answers(path, body):
    if path == "/v1/models":
        return 200, {}, LISTING
    cost = {"x-letsur-ai-gateway-response-cost": "0.001"}
    if path == "/v1/messages":
        return 200, {}, {"id": "msg_011CgU28vnYHk9rwjN2bBdHG", "type": "message", "estimated_cost": {"amount": "0.002"}}
    if path == "/v1/images/generations":
        return 200, cost, {"created": 1, "data": [{"b64_json": "A" * 3000}], "usage": {"total_tokens": 2}}
    if path == "/v1/chat/completions":
        return 200, {
            "gpt-5.4": {**cost, "x-letsur-ai-gateway-model-id": "gpt-5.4-azure-canada-east"},
            "gpt-6.1-terra": {**cost, "x-letsur-ai-gateway-model-id": "gpt-6.1-terra-bedrock-us-east-1"},
        }.get(body["model"], cost), {
            "glm-5": SHAPED, "glm-5.2": chat("chatcmpl-" + UUID, provider_note=f"{KEY} {BASE}"),
            "deepseek-v4-pro": chat("gen-1700000000-abcdefghijklmnopqrst", provider="Example Host"),
            "kimi-k2.5": vercel_body(resolvedProvider="alpha", finalProvider="beta"),
        }.get(body["model"], chat())
    return 404, {}, {"error": {"code": "route_not_found"}}


ROUTES = {
    "is_demo": False, "purpose": "경로 파일", "basis_note": "note",
    "records": [record("gpt-6.1-terra", "openai_direct", checked_at=OLD), record("deepseek-v4-pro", "openrouter"),
                record("kimi-k2.5", "openrouter"), record("claude-haiku-4-5", "anthropic_direct"),
                record("seedance-2.5", "fal", basis="settings"), record("seedance-2.5", "BytePlus ModelArk", basis="settings")],
    "unresolved": {"glm-5": "Bedrock 형식이나 확인 못 함", "glm-5.2": "사람이 쓴 설명"},
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    routes, output = tmp_path / "routes.json", tmp_path / "out" / "proposal.json"
    routes.write_text(json.dumps(ROUTES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(lr, "today", lambda: DAY)
    opener = FakeOpener(gateway_answers)

    def run(*args, env=None, opener=opener):
        env = {lr.BASE_ENV: BASE, lr.KEY_ENV: KEY} if env is None else env
        return lr.main(["--routes", str(routes), "--output", str(output), *args], env=env, opener=opener)
    return run, routes, output, opener


def test_a_run_samples_the_registered_models_names_the_rest_and_keeps_only_the_routes_it_saw(setup, capsys):
    run, routes, output, opener = setup
    before = routes.read_bytes()
    assert run() == 0
    asked = Counter((r["path"], (r["body"] or {}).get("model")) for r in opener.requests)
    assert asked == {("/v1/models", None): 1, **{(path, model): 6 for path, model in (
        ("/v1/messages", "claude-haiku-4-5-20251001"), ("/v1/chat/completions", "deepseek-v4-pro"), ("/v1/chat/completions", "glm-5"),
        ("/v1/chat/completions", "glm-5.2"), ("/v1/chat/completions", "gpt-5.4"), ("/v1/chat/completions", "gpt-6.1-terra"),
        ("/v1/chat/completions", "kimi-k2.5"), ("/v1/responses", "gpt-5.4"), ("/v1/responses", "gpt-6.1-terra"))}}
    # six samples each, the OpenAI family on both endpoints; no video, no image, no LETSUR-only model
    text = output.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n" and "경로 파일" in text
    proposal = json.loads(text)
    assert proposal["records"] == [
        record("gpt-6.1-terra", "aws_bedrock", note="Bedrock 배포 지역 us-east-1", checked_at=DAY),   # the 10/28 route not seen now is gone
        {**record("deepseek-v4-pro", "openrouter"), "note": "이번 확인의 하위 공급자 Example Host(요청마다 바뀔 수 있음)", "checked_at": DAY},
        record("kimi-k2.5", "vercel_ai_gateway", note="이번 확인의 하위 공급자 beta(계획 alpha, 요청마다 바뀔 수 있음)", checked_at=DAY),
        {**record("claude-haiku-4-5", "anthropic_direct"), "checked_at": DAY},                     # LETSUR's dated id, our id
        record("seedance-2.5", "fal", basis="settings"), record("seedance-2.5", "BytePlus ModelArk", basis="settings"),
        record("gpt-5.4", "azure_foundry", note="Azure 배포 지역 canada-east", checked_at=DAY)]
    # glm-5's Bedrock-like answer is no proof any more: the entry already written stays
    assert proposal["unresolved"] == {"glm-5": "Bedrock 형식이나 확인 못 함", "glm-5.2": "사람이 쓴 설명",
                                      "kling-v3-text-to-video": lr.NOT_PROBED["video"], "gpt-image-2": lr.NOT_PROBED["image"]}
    assert not set(LETSUR_ONLY) & ({r["model_id"] for r in proposal["records"]} | set(proposal["unresolved"]))
    assert "byteplus-seedance-2.5" not in text and routes.read_bytes() == before  # never overwritten without --write
    captured = capsys.readouterr()
    for expected in ("new route (3)", "  gpt-6.1-terra: aws_bedrock [observed]", "  kimi-k2.5: vercel_ai_gateway [observed]", "  gpt-5.4: azure_foundry [observed]",
                     "observed before, not seen this run, dropped (2)", "  gpt-6.1-terra: openai_direct [observed] 2026-10-28",
                     "  kimi-k2.5: openrouter [observed] no date", "re-seen (2): claude-haiku-4-5, deepseek-v4-pro",
                     "unresolved (4): glm-5, glm-5.2, gpt-image-2, kling-v3-text-to-video", "not registered, skipped (3): gpt-4, gpt-audio-1.5, jev-latest"):
        assert expected in captured.out.splitlines()
    assert "checked 10 models with 54 requests, gateway-reported cost 0.0480" in captured.out
    for expected in ("[6/10] gpt-5.4: azure_foundry×6", "[8/10] gpt-image-2: not requested", "[4/10] glm-5: unresolved×6"):
        assert expected in captured.err.splitlines()
    assert "gpt-4" not in captured.err and "[bedrock" not in captured.err
    for secret in (KEY, BASE):
        assert secret not in captured.out + captured.err + text


def rotating(turns):
    """A gateway whose router hands each model's requests to its deployments in turn: `turns` maps a model to the answers."""
    lock, asked = threading.Lock(), Counter()

    def respond(path, body):
        with lock:  # the requests come from several threads
            asked[body["model"]] += 1
            number = asked[body["model"]] - 1
        return turns[body["model"]][number % len(turns[body["model"]])]
    return respond


def test_sampling_records_every_route_a_model_is_spread_over(setup, capsys):
    run, _, output, _ = setup
    to = {"x-letsur-ai-gateway-model-id": V}
    opener = FakeOpener(rotating({
        "gemini-2.5-flash": [(400, {}, GEMINI_REFUSAL), (200, to, vercel_body(finalProvider="google")),
                             (200, to, vercel_body(finalProvider="google")), (200, to, vercel_body(resolvedProvider="google", finalProvider="vertex"))],
        "glm-5": [(200, {"x-letsur-ai-gateway-model-id": B}, chat("chatcmpl-" + UUID)), (200, to, vercel_body(finalProvider="bedrock"))]}))
    assert run("--models", "gemini-2.5-flash,glm-5", "--samples", "4", "--workers", "3", opener=opener) == 0
    assert all(("safety_settings" in r["body"]) == (r["body"]["model"] == "gemini-2.5-flash") for r in opener.requests)
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert [r for r in proposal["records"] if r["model_id"] in ("gemini-2.5-flash", "glm-5")] == [
        record("gemini-2.5-flash", "google_direct", note=REFUSED, checked_at=DAY),
        record("gemini-2.5-flash", "vercel_ai_gateway", note="이번 확인의 하위 공급자 google·vertex(계획 google, 요청마다 바뀔 수 있음)", checked_at=DAY),
        record("glm-5", "vercel_ai_gateway", note="이번 확인의 하위 공급자 bedrock(요청마다 바뀔 수 있음)", checked_at=DAY)]
    assert proposal["unresolved"]["glm-5"] == "Bedrock 형식이나 확인 못 함"  # a deployment showed no route; the written entry stays
    err = capsys.readouterr().err.splitlines()
    assert "[1/2] gemini-2.5-flash: google_direct×1, vercel_ai_gateway×3" in err and "[2/2] glm-5: vercel_ai_gateway×2, unresolved×2" in err


def test_a_new_unresolved_note_counts_the_deployments_that_showed_no_route(setup):
    run, routes, output, _ = setup
    routes.write_text(json.dumps({"records": [], "unresolved": {}}), encoding="utf-8")
    opener = FakeOpener(rotating({"glm-5": [(200, {"x-letsur-ai-gateway-model-id": B}, chat("chatcmpl-" + UUID)),
                                            (200, {"x-letsur-ai-gateway-model-id": V}, vercel_body(finalProvider="bedrock"))]}))
    assert run("--models", "glm-5", "--samples", "4", opener=opener) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["unresolved"] == {"glm-5": "배포 2개 중 1개 경로 표시 없음: chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음"}


def test_a_leftover_inferred_record_goes_when_its_model_is_checked_and_stays_when_it_is_not(setup, capsys):
    run, routes, output, _ = setup
    routes.write_text(json.dumps({"records": [record("glm-5", "aws_bedrock", basis="inferred", note="Bedrock 모양", checked_at=OLD),
                                              record("kling-v3-text-to-video", "kling_direct", basis="inferred", note="단가")],
                                  "unresolved": {}}), encoding="utf-8")
    assert run("--models", "glm-5,kling-v3-text-to-video") == 0
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert proposal["records"] == [record("kling-v3-text-to-video", "kling_direct", basis="inferred", note="단가")]  # asked nothing
    assert proposal["unresolved"] == {"glm-5": "배포 1개 중 1개 경로 표시 없음: chat 200: id 형식 chatcmpl-<uuid>, 경로 표시 없음"}
    out = capsys.readouterr().out.splitlines()
    assert "inferred (no longer written), dropped (1)" in out and "  glm-5: aws_bedrock [inferred] 2026-10-28" in out


def bedrock_gateway(path, body):
    """glm-5 sits on a Bedrock deployment that refuses a bogus latency; the gateway then hands the request to a Vercel one."""
    if (body.get("performanceConfig") or {}).get("latency") == "bogus":
        return 200, {"x-letsur-ai-gateway-model-id": V}, vercel_body(finalProvider="bedrock")
    return 200, {"x-letsur-ai-gateway-model-id": B}, chat("chatcmpl-" + UUID)


def test_the_bedrock_check_runs_only_with_its_flag_and_records_the_deployment_it_shows(setup, capsys):
    run, _, output, _ = setup
    plain = FakeOpener(bedrock_gateway)
    assert run("--models", "glm-5", opener=plain) == 0
    assert len(plain.requests) == 6 and not any({"performanceConfig", "foo_probe_param"} & set(r["body"]) for r in plain.requests)
    assert not [r for r in json.loads(output.read_text(encoding="utf-8"))["records"] if r["model_id"] == "glm-5"]
    capsys.readouterr()
    checked = FakeOpener(bedrock_gateway)
    assert run("--models", "glm-5", "--bedrock-check", opener=checked) == 0
    extra = Counter(json.dumps({key: r["body"][key] for key in ("performanceConfig", "foo_probe_param") if key in r["body"]}) for r in checked.requests)
    assert extra == {"{}": 6, '{"performanceConfig": {"latency": "standard"}}': 6, '{"performanceConfig": {"latency": "bogus"}}': 6,
                     '{"foo_probe_param": 1}': 6}
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert [r for r in proposal["records"] if r["model_id"] == "glm-5"] == [record("glm-5", "aws_bedrock", note=lr.BEDROCK_NOTE, checked_at=DAY)]
    assert "glm-5" not in proposal["unresolved"]  # its only deployment is now known
    captured = capsys.readouterr()
    assert "[bedrock 1/1] glm-5: 1 of 1 chatcmpl-<uuid> deployments check Bedrock's own field" in captured.err.splitlines()
    assert "checked 1 models with 24 requests" in captured.out


def test_write_overwrites_the_routes_file_with_the_same_proposal(setup, capsys):
    run, routes, output, _ = setup
    assert run("--write") == 0
    assert routes.read_bytes() == output.read_bytes() and "gpt-5.4" in routes.read_text(encoding="utf-8")
    assert "overwritten" in capsys.readouterr().out


def test_a_second_run_the_same_day_leaves_the_file_as_the_first_wrote_it(setup):
    run, routes, output, _ = setup
    assert run("--write") == 0
    first = routes.read_bytes()
    assert run("--write") == 0 and routes.read_bytes() == first


def test_models_option_checks_only_those_models_without_listing_and_still_skips_unregistered_ones(setup, capsys):
    run, _, output, opener = setup
    assert run("--models", "gpt-5.4,glm-5,gpt-4", "--models", "claude-haiku-4-5-20251001") == 0
    assert Counter(r["path"] for r in opener.requests) == {"/v1/chat/completions": 12, "/v1/responses": 6, "/v1/messages": 6}
    assert "kling-v3-text-to-video" not in json.loads(output.read_text(encoding="utf-8"))["unresolved"]
    assert "not registered, skipped (1): gpt-4" in capsys.readouterr().out.splitlines()


def test_a_run_over_only_unregistered_models_asks_nothing_and_writes_nothing(setup, capsys):
    run, _, output, opener = setup
    assert run("--models", "gpt-4,jev-latest") == 1 and not opener.requests and not output.exists()
    assert "nothing written" in capsys.readouterr().err


def test_an_image_is_requested_only_with_the_flag_and_then_once_whatever_the_samples(setup):
    run, _, output, opener = setup
    assert run("--models", "gpt-image-2", "--include-images", "--samples", "5") == 0
    image = [r for r in opener.requests if r["path"] == "/v1/images/generations"]
    assert len(opener.requests) == len(image) == 1 and image[0]["body"]["quality"] == "low"
    proposal = json.loads(output.read_text(encoding="utf-8"))
    assert not [r for r in proposal["records"] if r["model_id"] == "gpt-image-2"]  # no Azure field is no proof of OpenAI
    assert proposal["unresolved"]["gpt-image-2"] == "배포 1개 중 1개 경로 표시 없음: images 200: 경로 표시 없음" and "3000" not in json.dumps(proposal)


def test_a_refused_key_ends_the_run_with_nothing_written(setup, capsys):
    run, _, output, opener = setup
    assert run(opener=FakeOpener(lambda path, body: (401, {}, {"error": {"code": "invalid_api_key"}}))) == 2
    assert not output.exists() and KEY not in capsys.readouterr().err


def test_a_key_refused_mid_run_stops_every_thread(setup, capsys):
    run, _, output, _ = setup
    opener = FakeOpener(lambda path, body: (200, {}, LISTING) if path == "/v1/models" else (401, {}, {"error": {"code": "invalid_api_key"}}))
    assert run("--workers", "3", opener=opener) == 2
    asked = [r for r in opener.requests if r["path"] != "/v1/models"]
    assert 1 <= len(asked) <= 3 and not output.exists() and KEY not in capsys.readouterr().err  # one request per thread at most


def test_a_run_where_nothing_answered_writes_nothing(setup, capsys):
    run, _, output, _ = setup
    assert run("--models", "gpt-5.4", opener=FakeOpener(lambda path, body: (404, {}, {"error": {"code": "route_not_found"}}))) == 1
    assert not output.exists() and "nothing written" in capsys.readouterr().err


def test_a_run_where_only_the_gemini_api_refused_is_still_written(setup):
    run, _, output, _ = setup
    assert run("--models", "gemini-2.5-flash-lite", opener=FakeOpener(lambda path, body: (400, {}, GEMINI_REFUSAL))) == 0
    records = json.loads(output.read_text(encoding="utf-8"))["records"]
    assert [r for r in records if r["model_id"] == "gemini-2.5-flash-lite"] == [record("gemini-2.5-flash-lite", "google_direct", note=REFUSED, checked_at=DAY)]


@pytest.mark.parametrize("option", ["--samples", "--workers"])
def test_samples_and_workers_must_be_one_or_more(setup, option):
    run, _, output, opener = setup
    with pytest.raises(SystemExit) as refused:
        run(option, "0")
    assert refused.value.code == 2 and not opener.requests and not output.exists()


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
