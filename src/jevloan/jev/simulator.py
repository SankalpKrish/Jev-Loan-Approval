"""Offline stand-in for the TypeSafe API, at the httpx2 transport layer (PLAN section 3.4, decision D10).

The real SDK code path runs unchanged: the SDK builds and sends a genuine `POST /v1/systemone`, and this
transport validates it the way the API does and answers with API-shaped JSON. Answers come from the rules
in `jevloan.jev.sim_rules` (or a no-information default). Every answer is deterministic in
(seed, state, question id). Numbers from here are PROVISIONAL and never count towards a GO.
"""

import asyncio
import json
import random
from collections.abc import Mapping
from typing import Any

import httpx2

from jevloan.canonical import canonical_json, sha256_hex
from jevloan.jev.sim_rules import SimRule, choice, default_answer, noul, resolve, score

_KINDS = ("noul", "choice", "score")


def _problem(loc: list[str], msg: str, kind: str = "value_error") -> dict:
    return {"loc": ["body", *loc], "msg": msg, "type": kind}


def _question_problems(qid: str, question: Any) -> list[dict]:
    where = ["questions", qid]
    if not isinstance(question, dict) or "type" not in question:
        return [_problem(where + ["type"], "Field required", "missing")]
    kind = question["type"]
    if kind not in _KINDS:
        return [_problem(where + ["type"], "Input should be 'noul', 'choice' or 'score'", "literal_error")]
    problems = []
    if question.get("instructions") in (None, ""):
        problems.append(_problem(where + ["instructions"], "Field required", "missing"))
    criteria = question.get("criteria")
    if kind == "choice" and not (isinstance(criteria, dict) and 1 <= len(criteria) <= 255):
        problems.append(_problem(where + ["criteria"], "A choice needs 1 to 255 options (an object)"))
    if kind == "score" and not (isinstance(criteria, list) and 2 <= len(criteria) <= 10):
        problems.append(_problem(where + ["criteria"], "A score needs 2 to 10 levels (a list)"))
    if kind == "noul" and criteria is not None and not isinstance(criteria, dict):
        problems.append(_problem(where + ["criteria"], "Noul criteria must be an object with 'true' and 'false'"))
    return problems


def _request_problems(body: Any) -> list[dict]:
    if not isinstance(body, dict):
        return [_problem([], "The request body must be a JSON object")]
    problems = [_problem([name], "Field required", "missing") for name in ("state", "model", "questions") if name not in body]
    if "questions" in body:
        questions = body["questions"]
        if not isinstance(questions, dict) or not questions:
            problems.append(_problem(["questions"], "At least one question is required"))
        else:
            for qid, question in questions.items():
                problems += _question_problems(qid, question)
    return problems


def _conform(qid: str, answer: dict, question: dict) -> dict:
    """Make a rule's answer fit its question, and recompute the derived fields so they stay consistent."""
    kind = question["type"]
    if answer.get("type") != kind:
        raise ValueError(f"sim rule for {qid!r} answered a {kind} question with a {answer.get('type')!r} answer")
    if kind == "noul":
        return noul(answer["noul"])
    if kind == "choice":
        options = list(question["criteria"])
        unknown = sorted(set(answer["probabilities"]) - set(options))
        if unknown:
            raise ValueError(f"sim rule for {qid!r} returned options {unknown} that the question does not have")
        return choice({option: answer["probabilities"].get(option, 0.0) for option in options})
    levels = question["criteria"]
    if len(answer["probabilities"]) != len(levels):
        raise ValueError(f"sim rule for {qid!r} returned {len(answer['probabilities'])} levels for a {len(levels)}-level score")
    conformed = score([answer["probabilities"][str(level)] for level in range(len(levels))])
    conformed["legend"] = {str(level): description for level, description in enumerate(levels)}  # the rubric, as sent
    return conformed


class SimulatedJevTransport(httpx2.AsyncBaseTransport):
    """`rules` maps a question id or glob to a rule (the gateway passes `sim_rules.REGISTRY`); with none,
    every question gets the no-information default. `latency_ms=(0, 0)` skips the sleep entirely."""

    def __init__(
        self,
        rules: Mapping[str, SimRule] | None = None,
        latency_ms: tuple[float, float] = (30, 120),
        seed: int = 0,
        model_version: str = "sim-jev-0.1",
    ) -> None:
        self._rules: Mapping[str, SimRule] = {} if rules is None else rules
        self._latency_ms = latency_ms
        self._seed = seed
        self._model_version = model_version

    def _rng(self, state_hash: str, salt: str) -> random.Random:
        return random.Random(int(sha256_hex(f"{self._seed}|{state_hash}|{salt}"), 16))

    def _answer(self, state: Any, state_hash: str, qid: str, question: dict) -> dict:
        rule = resolve(self._rules, qid)
        raw = rule(state, question, self._rng(state_hash, qid)) if rule else default_answer(question)
        return _conform(qid, raw, question)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        raw = await request.aread()
        if request.method != "POST" or request.url.path != "/v1/systemone":
            return httpx2.Response(404, json={"error": f"simulator only serves POST /v1/systemone, not {request.method} {request.url.path}"})
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if problems := _request_problems(body):
            return httpx2.Response(422, json={"detail": problems})

        state_hash = sha256_hex(canonical_json(body["state"]))
        low, high = self._latency_ms
        if high > 0:
            await asyncio.sleep(self._rng(state_hash, "latency").uniform(low, high) / 1000)
        answers = {qid: self._answer(body["state"], state_hash, qid, question) for qid, question in body["questions"].items()}
        return httpx2.Response(
            200,
            headers={"x-typesafe-request-id": f"sim-{state_hash[:16]}"},
            json={
                "model": self._model_version,
                "answers": answers,
                "usage": {"input_tokens": max(1, len(raw) // 4), "output_tokens": 10 + 8 * len(answers)},
            },
        )
