import asyncio
import json
import random

import httpx2
import pytest

from jevloan.canonical import canonical_json, sha256_hex
from jevloan.jev.simulator import SimulatedJevTransport
from jevloan.jev.sim_rules import choice, noisy_p, noul, score

URL = "https://sim.test/v1/systemone"
STATE = {"segment": "salaried_personal", "income": {"band": "50-75k"}}
NOUL_Q = {"type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}}
CHOICE_Q = {"type": "choice", "instructions": "Which?", "criteria": {"a": "first", "b": "second", "c": None}}
SCORE_Q = {"type": "score", "instructions": "How much?", "criteria": ["low", {"level": "mid"}, "high"]}


def request_body(questions: dict, state: object = STATE) -> dict:
    return {"state": state, "model": "jev-latest", "questions": questions}


async def send(transport: SimulatedJevTransport, body: object | None = None, *, content: bytes | None = None) -> httpx2.Response:
    async with httpx2.AsyncClient(transport=transport) as client:
        if content is not None:
            return await client.post(URL, content=content)
        return await client.post(URL, json=body)


def sim(**kwargs) -> SimulatedJevTransport:
    kwargs.setdefault("latency_ms", (0, 0))
    return SimulatedJevTransport(**kwargs)


# --- validation: 422 with a JSON body, like the real API ----------------------------------------------------


def with_question(question: dict) -> dict:
    return request_body({"q": question})


INVALID_BODIES = {
    "no type": with_question({"instructions": "x"}),
    "unknown type": with_question({"type": "rank", "instructions": "x"}),
    "no instructions": with_question({"type": "noul"}),
    "null instructions": with_question({"type": "noul", "instructions": None}),
    "choice without criteria": with_question({"type": "choice", "instructions": "x"}),
    "choice with no options": with_question({"type": "choice", "instructions": "x", "criteria": {}}),
    "choice with 256 options": with_question(
        {"type": "choice", "instructions": "x", "criteria": {f"o{i}": None for i in range(256)}}
    ),
    "choice criteria as a list": with_question({"type": "choice", "instructions": "x", "criteria": ["a", "b"]}),
    "score without criteria": with_question({"type": "score", "instructions": "x"}),
    "score with 1 level": with_question({"type": "score", "instructions": "x", "criteria": ["only"]}),
    "score with 11 levels": with_question({"type": "score", "instructions": "x", "criteria": [str(i) for i in range(11)]}),
    "score criteria as an object": with_question({"type": "score", "instructions": "x", "criteria": {"a": 1, "b": 2}}),
    "noul criteria as a list": with_question({"type": "noul", "instructions": "x", "criteria": ["yes", "no"]}),
    "question is not an object": request_body({"q": "Is it?"}),
    "no questions": request_body({}),
    "questions missing": {"state": "s", "model": "jev-latest"},
    "state missing": {"model": "jev-latest", "questions": {"q": NOUL_Q}},
    "model missing": {"state": "s", "questions": {"q": NOUL_Q}},
    "body is a list": [1, 2, 3],
}


@pytest.mark.parametrize("body", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
async def test_invalid_requests_get_a_422_with_a_json_error_body(body):
    response = await send(sim(), body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail and all({"loc", "msg", "type"} <= set(problem) for problem in detail)


async def test_a_body_that_is_not_json_gets_a_422():
    response = await send(sim(), content=b"{not json")
    assert response.status_code == 422
    assert response.json()["detail"]


async def test_every_bad_question_is_reported():
    response = await send(sim(), request_body({"a": {"type": "noul"}, "b": {"type": "score", "instructions": "x", "criteria": ["one"]}}))
    locs = [problem["loc"] for problem in response.json()["detail"]]
    assert ["body", "questions", "a", "instructions"] in locs
    assert ["body", "questions", "b", "criteria"] in locs


@pytest.mark.parametrize(
    "question",
    [
        {"type": "choice", "instructions": "x", "criteria": {"only": None}},
        {"type": "choice", "instructions": "x", "criteria": {f"o{i}": None for i in range(255)}},
        {"type": "score", "instructions": "x", "criteria": ["a", "b"]},
        {"type": "score", "instructions": "x", "criteria": [str(i) for i in range(10)]},
        {"type": "noul", "instructions": {"question": "x", "refer_to": ["`a`"]}},
        {"type": "noul", "instructions": ["x", "y"], "criteria": {"true": {"what": "t"}, "false": None}},
    ],
)
async def test_boundary_and_structured_requests_are_accepted(question):
    response = await send(sim(), with_question(question))
    assert response.status_code == 200, response.text


async def test_the_sdk_error_path_sees_a_422():
    """End to end through the real SDK: a malformed question is a TypeSafeUnprocessableEntityError."""
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeUnprocessableEntityError

    client = AsyncTypeSafeClient(api_key="k", retry=RetryPolicy(max_retries=0), transport=sim())
    with pytest.raises(TypeSafeUnprocessableEntityError, match="criteria"):
        await client.system_one(state=STATE, questions={"q": {"type": "score", "instructions": "x", "criteria": ["one"]}})
    await client.aclose()


async def test_other_paths_are_not_served():
    async with httpx2.AsyncClient(transport=sim()) as client:
        assert (await client.get("https://sim.test/v1/models")).status_code == 404


# --- answers ------------------------------------------------------------------------------------------------


async def test_response_is_api_shaped():
    body = request_body({"n": NOUL_Q, "c": CHOICE_Q, "s": SCORE_Q})
    raw = json.dumps(body).encode()
    response = await send(sim(model_version="sim-jev-9.9"), content=raw)
    data = response.json()
    assert response.status_code == 200
    assert data["model"] == "sim-jev-9.9"
    assert set(data["answers"]) == {"n", "c", "s"}
    assert data["usage"]["input_tokens"] == len(raw) // 4
    assert data["usage"]["output_tokens"] > 0
    assert response.headers["x-typesafe-request-id"]
    assert data["answers"]["n"].keys() == {"type", "noul"}  # no derived_confidence: the API does not send one
    assert data["answers"]["c"].keys() == {"type", "choice", "probabilities", "confidence"}
    assert data["answers"]["s"].keys() == {"type", "score", "probabilities", "legend", "confidence"}


async def test_default_model_version_is_sim_jev():
    data = (await send(sim(), request_body({"n": NOUL_Q}))).json()
    assert data["model"] == "sim-jev-0.1"


async def test_input_tokens_track_the_request_size():
    small = (await send(sim(), request_body({"n": NOUL_Q}, state="x"))).json()["usage"]["input_tokens"]
    big = (await send(sim(), request_body({"n": NOUL_Q}, state="x" * 4000))).json()["usage"]["input_tokens"]
    assert big - small == pytest.approx(1000, abs=2)


async def test_unknown_question_ids_get_no_information_answers():
    answers = (await send(sim(), request_body({"n": NOUL_Q, "c": CHOICE_Q, "s": SCORE_Q}))).json()["answers"]
    assert answers["n"]["noul"] == 0.5
    assert answers["c"]["confidence"] == 0.0
    assert answers["c"]["probabilities"] == {"a": 0.3333, "b": 0.3333, "c": 0.3333}
    assert max(answers["s"]["probabilities"], key=answers["s"]["probabilities"].get) == "1"  # the middle level


async def test_choice_answers_pick_the_argmax_with_the_documented_confidence():
    transport = sim(rules={"c": lambda state, question, rng: choice({"a": 0.2, "b": 0.7, "c": 0.1})})
    answer = (await send(transport, request_body({"c": CHOICE_Q}))).json()["answers"]["c"]
    assert answer["choice"] == "b"
    assert answer["probabilities"] == {"a": 0.2, "b": 0.7, "c": 0.1}
    assert answer["confidence"] == pytest.approx(max(0, (3 * 0.7 - 1) / 2))


async def test_a_flat_choice_has_zero_confidence_and_a_clear_one_is_near_one():
    transport = sim(
        rules={
            "flat": lambda state, question, rng: choice({"a": 1, "b": 1, "c": 1}),
            "sure": lambda state, question, rng: choice({"a": 0.98, "b": 0.01, "c": 0.01}),
        }
    )
    answers = (await send(transport, request_body({"flat": CHOICE_Q, "sure": CHOICE_Q}))).json()["answers"]
    assert answers["flat"]["confidence"] == 0.0
    assert answers["sure"]["confidence"] == pytest.approx(0.97)


async def test_score_is_the_expected_level_and_carries_the_rubric_as_its_legend():
    transport = sim(rules={"s": lambda state, question, rng: score([0.1, 0.2, 0.7])})
    answer = (await send(transport, request_body({"s": SCORE_Q}))).json()["answers"]["s"]
    assert answer["score"] == pytest.approx(0.2 + 2 * 0.7)
    assert answer["probabilities"] == {"0": 0.1, "1": 0.2, "2": 0.7}
    assert answer["legend"] == {"0": "low", "1": {"level": "mid"}, "2": "high"}
    assert answer["confidence"] == pytest.approx((3 * 0.7 - 1) / 2)


async def test_choice_options_a_rule_left_out_get_zero():
    transport = sim(rules={"c": lambda state, question, rng: choice({"a": 3, "b": 1})})
    probabilities = (await send(transport, request_body({"c": CHOICE_Q}))).json()["answers"]["c"]["probabilities"]
    assert probabilities == {"a": 0.75, "b": 0.25, "c": 0.0}


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (lambda state, question, rng: noul(0.5), "noul"),  # a Noul answer to a choice question
        (lambda state, question, rng: choice({"a": 1, "zzz": 1}), "zzz"),  # an option the question does not have
    ],
)
async def test_a_rule_with_the_wrong_shape_fails_loudly(rule, message):
    with pytest.raises(ValueError, match=message):
        await send(sim(rules={"c": rule}), request_body({"c": CHOICE_Q}))


async def test_a_score_rule_with_the_wrong_number_of_levels_fails_loudly():
    with pytest.raises(ValueError, match="levels"):
        await send(sim(rules={"s": lambda state, question, rng: score([1, 1])}), request_body({"s": SCORE_Q}))


async def test_rules_see_the_state_the_question_and_a_seeded_rng():
    seen = {}

    def rule(state, question, rng):
        seen.update(state=state, question=question, draw=rng.random())
        return noul(0.9)

    await send(sim(rules={"n": rule}), request_body({"n": NOUL_Q}))
    assert seen["state"] == STATE
    assert seen["question"] == NOUL_Q
    expected = random.Random(int(sha256_hex(f"0|{sha256_hex(canonical_json(STATE))}|n"), 16)).random()
    assert seen["draw"] == expected


async def test_glob_rules_apply_and_exact_rules_win():
    transport = sim(
        rules={
            "E_disclosure_*": lambda state, question, rng: noul(0.1),
            "E_disclosure_apr": lambda state, question, rng: noul(0.9),
        }
    )
    answers = (await send(transport, request_body({"E_disclosure_apr": NOUL_Q, "E_disclosure_penal": NOUL_Q}))).json()["answers"]
    assert answers["E_disclosure_apr"]["noul"] == 0.9
    assert answers["E_disclosure_penal"]["noul"] == 0.1


# --- determinism ---------------------------------------------------------------------------------------------


def noisy_rule(state, question, rng):
    return noul(noisy_p(True, rng))


async def answers_for(seed: int, state: object, qid: str = "n") -> dict:
    return (await send(sim(seed=seed, rules={"*": noisy_rule}), request_body({qid: NOUL_Q}, state=state))).json()["answers"][qid]


async def test_same_seed_state_and_question_give_the_same_answer():
    assert await answers_for(3, STATE) == await answers_for(3, STATE)


async def test_answers_vary_with_seed_state_and_question_id():
    seeds = {(await answers_for(seed, STATE))["noul"] for seed in range(20)}
    states = {(await answers_for(0, {"case": i}))["noul"] for i in range(20)}
    qids = {(await answers_for(0, STATE, qid=f"q{i}"))["noul"] for i in range(20)}
    assert len(seeds) > 1 and len(states) > 1 and len(qids) > 1


async def test_answers_do_not_depend_on_what_else_is_asked():
    alone = (await send(sim(rules={"*": noisy_rule}), request_body({"n": NOUL_Q}))).json()["answers"]["n"]
    together = (await send(sim(rules={"*": noisy_rule}), request_body({"x": NOUL_Q, "n": NOUL_Q}))).json()["answers"]["n"]
    assert alone == together


# --- latency -------------------------------------------------------------------------------------------------


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def record(seconds: float, *args):
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", record)
    return recorded


async def test_zero_latency_means_no_sleep(sleeps):
    await send(sim(latency_ms=(0, 0)), request_body({"n": NOUL_Q}))
    assert sleeps == []


async def test_latency_is_drawn_from_the_range_and_is_seeded(sleeps):
    for _ in range(2):
        await send(sim(latency_ms=(40, 60), seed=5), request_body({"n": NOUL_Q}))
    assert len(sleeps) == 2 and sleeps[0] == sleeps[1]
    assert 0.040 <= sleeps[0] <= 0.060
    await send(sim(latency_ms=(40, 60), seed=6), request_body({"n": NOUL_Q}))
    assert sleeps[2] != sleeps[0]


async def test_invalid_requests_are_rejected_without_the_latency(sleeps):
    await send(sim(latency_ms=(40, 60)), request_body({}))
    assert sleeps == []
