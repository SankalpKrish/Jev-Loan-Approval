"""The policy engine: the only place model answers become routing decisions (PLAN sections 3.9 and 3.10).

`PolicyEngine.decide()` is pure. It reads its arguments and the immutable policy, does no I/O and mutates
nothing, so the same inputs always give the same decision. It never sanctions and never sets price. Every
`Reason.text` is written by people (the rubric's `reason_text`, the policy's item names, or numbers our own
code computed); nothing the model returned is ever copied into text.

Appraisal precedence (decision D5), first match wins:
  1. the call failed, or the PII gate blocked it            -> HUMAN_REVIEW, reason code says why
  2. a confident fraud signal (module B)                    -> FRAUD_INVESTIGATION (never a decline)
  3. a confident readiness fail (module A)                  -> DEFICIENCY_NOTICE naming the items
  4. a routing question (A, B or C) below its minimum
     confidence, or asked but not answered                  -> HUMAN_REVIEW, LOW_CONFIDENCE
  5. the C composite: pass -> PROCEED_TO_SANCTIONING_AUTHORITY, borderline -> HUMAN_REVIEW (credit_review,
     ordered by closeness), fail -> DECLINE_RECOMMENDED (a human confirms it)
Uncertainty therefore blocks both auto-actions, PROCEED and DECLINE_RECOMMENDED.
"""

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from jevloan.data.schema import load_disclosures, load_grid
from jevloan.policy.config import Policy
from jevloan.policy.outcomes import (
    BAND_BORDERLINE,
    BAND_FAIL,
    BAND_PASS,
    CREDIT_REVIEW,
    DECLINE_CONFIRMATION,
    DEFICIENCY_OPS,
    DISBURSAL_CORRECTION,
    FAILURE_REASON_CODE,
    FRAUD_INVESTIGATION_QUEUE,
    OUTCOME_QUEUE,
    STAGE_FAILURE_QUEUE,
    STAGES,
    Outcome,
    ReasonCode,
)

if TYPE_CHECKING:  # the engine only reads a few attributes of a call result; it needs no SDK at runtime
    from jevloan.jev.gateway import JevCallResult

_MODULES = ("A", "B", "C", "D", "E", "F")
_PRECISION = 9  # composites, confidences and APR gaps are rounded so float noise cannot flip a threshold
_STAGE_MODULES = {"appraisal": "ABCD", "sanction_docs": "E", "monitoring": "F"}
_CLOSENESS_QID = "D_closeness"
_DOUBT_PREFIX = "D_doubt_"
_DISCLOSURE_PREFIX = "E_disclosure_"
_EWS_PREFIX = "F_ews_"
_COVENANT_PREFIX = "F_covenant_"
_APR_PSEUDO_QID = "DETERMINISTIC_APR_CHECK"


class QuestionLike(Protocol):
    """What the engine needs from a catalogue entry (`jevloan.modules.base.QuestionDef` satisfies it)."""

    qid: str
    module: str
    qtype: str
    risk_polarity: str
    routing_relevant: bool
    reason_text: str


@dataclass(frozen=True)
class Reason:
    qid: str
    p: float | None  # a Noul's p, a Score's expected level; None if the answer was unusable
    confidence: float  # 0.0 if the answer was unusable
    text: str  # from the rubric or the policy, never from the model


@dataclass(frozen=True)
class ModuleResult:
    """One module's verdict. `outcome` is lower-case for A-E ("pass"/"fail"/"signal"/"clear"/"uncertain"/
    "borderline"/"not_run"/"scored"), and the tier name for F ("T0".."T3"). `flags` are the qids that tripped
    the module's rule and `reasons` explain them. `score`: A/B/E lowest goodness, C the composite, D the
    closeness, F the warning count. `band` is set for C only, and is the band the answers alone imply even
    when a higher-precedence branch decided the file."""

    module: str
    outcome: str
    score: float | None
    band: str | None
    flags: list[str]
    reasons: list[Reason]
    low_confidence: bool


@dataclass(frozen=True)
class StageDecision:
    file_id: str
    segment: str
    stage: str
    outcome: Outcome
    queue: str | None
    closeness: float | None  # borderline files only: D_closeness normalised to [0, 1]
    top_reason: Reason | None  # borderline files only: the D_doubt_* answer with the highest p
    modules: dict[str, ModuleResult]
    reason_codes: list[str]
    policy_version: str
    reasons: list[Reason] = field(default_factory=list)  # what drove the outcome (items, signals, failing checks)
    deficiency_items: list[str] = field(default_factory=list)  # readiness fail: human names of the missing items
    composite: float | None = None  # C composite whenever computable, even if another branch decided the file
    band: str | None = None  # the band that composite implies: "pass" | "borderline" | "fail"
    derived: dict[str, dict[str, Any]] = field(default_factory=dict)  # qid -> p_or_score, goodness, confidence, low_confidence

    @property
    def pricing_band(self) -> str | None:
        """The band this decision may be priced under: "pass" for PROCEED, otherwise None (never price anything else)."""
        return BAND_PASS if self.outcome == Outcome.PROCEED_TO_SANCTIONING_AUTHORITY else None


@dataclass(frozen=True)
class _Read:
    """One answer, read through the catalogue's polarity. Unusable answers have no value and count as low confidence."""

    question: QuestionLike
    value: float | None  # a Noul's p, a Score's expected level
    goodness: float | None  # in [0, 1], 1 = best
    confidence: float
    low_confidence: bool

    @property
    def qid(self) -> str:
        return self.question.qid

    @property
    def usable(self) -> bool:
        return self.value is not None

    def derived(self) -> dict[str, Any]:
        return {"p_or_score": self.value, "goodness": self.goodness, "confidence": self.confidence, "low_confidence": self.low_confidence}


def _number(x: object) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def _text(question: QuestionLike) -> str:
    return getattr(question, "reason_text", "") or question.qid


class PolicyEngine:
    def __init__(
        self,
        policy: Policy,
        catalog: Mapping[str, QuestionLike],
        grid: dict | None = None,
        disclosures: list[dict] | list[str] | None = None,
    ) -> None:
        """`catalog` maps qid -> a question definition; a catalogue that cannot be routed safely raises ValueError.
        `grid` and `disclosures` default to the shipped sanction grid and KFS disclosures."""
        self._policy = policy
        self._catalog = dict(catalog)
        self.grid = grid if grid is not None else load_grid()  # carried for audit; judging the memo against it is module E's job
        raw = disclosures if disclosures is not None else load_disclosures()
        self._disclosure_ids = frozenset(d["id"] if isinstance(d, Mapping) else d for d in raw)
        self._min = policy.min_confidence
        self._validate_catalog()

    @property
    def policy_version(self) -> str:
        return self._policy.version

    # ------------------------------------------------------------------ catalogue validation

    def _validate_catalog(self) -> None:
        def bad(qid: str, why: str) -> ValueError:
            return ValueError(f"catalogue question {qid}: {why}")

        for qid, q in self._catalog.items():
            if q.qid != qid:
                raise bad(qid, f"is filed under the key {qid!r} but says qid={q.qid!r}")
            if q.module not in _MODULES:
                raise bad(qid, f"unknown module {q.module!r}")
            if q.qtype == "noul":
                ok = q.risk_polarity in ("yes_is_good", "yes_is_bad")
            elif q.qtype == "score":
                ok = q.risk_polarity == "ordinal_high_is_good"
            else:
                ok = q.qtype == "choice" and q.module == "D"  # the engine has no polarity semantics for a Choice
            if not ok:
                raise bad(qid, f"{q.qtype} with polarity {q.risk_polarity!r} cannot be routed")

            if q.module in ("A", "E") and not (q.qtype == "noul" and q.risk_polarity == "yes_is_good"):
                raise bad(qid, "module A and E questions must be yes_is_good Nouls (yes = the check passes)")
            if q.module == "B" and q.qtype != "noul":
                raise bad(qid, "module B questions must be Nouls")
            if q.module == "A" and qid not in self._policy.modules.A.missing_item_names:
                raise bad(qid, "has no human-readable name in modules.A.missing_item_names")
            if q.module == "D" and qid == _CLOSENESS_QID and q.qtype != "score":
                raise bad(qid, "must be a Score")
            if q.module == "D" and qid.startswith(_DOUBT_PREFIX) and q.qtype != "noul":
                raise bad(qid, "must be a Noul")
            if q.module == "E" and qid.startswith(_DISCLOSURE_PREFIX) and qid[len(_DISCLOSURE_PREFIX) :] not in self._disclosure_ids:
                raise bad(qid, "names a disclosure that is not in the KFS disclosures file")
            if q.module == "F":
                if q.qtype != "noul":
                    raise bad(qid, "module F questions must be Nouls")
                expected = {"F_ews_": "yes_is_bad", "F_covenant_": "yes_is_good"}
                prefix = next((p for p in expected if qid.startswith(p)), None)
                if prefix is None or q.risk_polarity != expected[prefix]:
                    raise bad(qid, "must be F_ews_* (yes_is_bad) or F_covenant_* (yes_is_good)")

        for segment, cfg in self._policy.modules.C.segments.items():
            for qid in cfg.weights:
                q = self._catalog.get(qid)
                if q is None or q.module != "C":
                    raise ValueError(f"policy weights {qid} for {segment}, but the catalogue has no C question with that id")

    # ------------------------------------------------------------------ reading answers

    def _unusable(self, q: QuestionLike) -> _Read:
        return _Read(q, None, None, 0.0, True)

    def _read(self, q: QuestionLike, answer: object) -> _Read:
        if not isinstance(answer, Mapping) or answer.get("type") != q.qtype:
            return self._unusable(q)
        if q.qtype == "noul":
            p = _number(answer.get("noul"))
            if p is None or not 0 <= p <= 1:
                return self._unusable(q)
            confidence = round(abs(2 * p - 1), _PRECISION)  # the policy's noul_confidence: abs(2p-1)
            goodness = p if q.risk_polarity == "yes_is_good" else 1 - p
            return _Read(q, p, goodness, confidence, confidence < self._min.noul)
        confidence = _number(answer.get("confidence"))
        if confidence is None or not 0 <= confidence <= 1:
            return self._unusable(q)
        if q.qtype == "score":
            level = _number(answer.get("score"))
            probabilities = answer.get("probabilities")
            levels = len(probabilities) if isinstance(probabilities, Mapping) else 0
            if level is None or levels < 2 or not -1e-9 <= level <= levels - 1 + 1e-9:
                return self._unusable(q)
            return _Read(q, level, min(1.0, max(0.0, level / (levels - 1))), confidence, confidence < self._min.score)
        return _Read(q, None, None, confidence, confidence < self._min.choice)  # a Choice carries confidence only

    def _read_all(self, stage: str, answers: Mapping[str, Any], asked: Collection[str] | None) -> dict[str, _Read]:
        """Every catalogued answer of this stage's modules, by qid. A question that was asked but has no answer
        is an unusable read; one that was neither asked nor answered simply does not apply and is skipped."""
        reads: dict[str, _Read] = {}
        for qid in sorted(set(answers) | set(asked or ())):
            q = self._catalog.get(qid)
            if q is None or q.module not in _STAGE_MODULES[stage]:
                continue
            reads[qid] = self._read(q, answers[qid]) if qid in answers else self._unusable(q)
        return reads

    @staticmethod
    def _reason(read: _Read, text: str | None = None) -> Reason:
        return Reason(read.qid, read.value, read.confidence, text if text is not None else _text(read.question))

    def _is_routing(self, read: _Read, weights: Mapping[str, float]) -> bool:
        """Does uncertainty on this answer block an auto-action? Modules A, B, C only; weighted questions always."""
        return read.question.module in ("A", "B", "C") and (read.question.routing_relevant or read.qid in weights)

    # ------------------------------------------------------------------ decide

    def decide(
        self,
        *,
        file_id: str,
        segment: str,
        stage: str,
        call: "JevCallResult",
        state: Mapping[str, Any],
        asked: Collection[str] | None = None,
    ) -> StageDecision:
        """Turn one stage's `JevCallResult` into a routing decision. Pure: no I/O, no mutation of the arguments.

        `asked` (optional) is the set of question ids that were sent; a routing question in it with no answer counts
        as low confidence. Without it, only answers that are present but unusable do."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        if segment not in self._policy.modules.C.segments:
            raise ValueError(f"unknown segment {segment!r}")
        base = {"file_id": file_id, "segment": segment, "stage": stage, "policy_version": self._policy.version}

        if not call.ok or call.failure is not None:
            code = FAILURE_REASON_CODE.get(call.failure, ReasonCode.MODEL_ERROR)
            return self._human_review(base, [code], state)
        answers = call.answers if isinstance(call.answers, Mapping) else {}
        reads = self._read_all(stage, answers, asked)
        if not reads:  # an answer set with none of this stage's questions is unusable, however it got here
            return self._human_review(base, [ReasonCode.MODEL_ERROR], state)

        derived = {qid: r.derived() for qid, r in reads.items()}
        if stage == "appraisal":
            return self._appraisal(base, reads, derived, segment)
        if stage == "sanction_docs":
            return self._sanction_docs(base, reads, derived, state)
        return self._monitoring(base, reads, derived)

    def _human_review(self, base: dict, codes: list[ReasonCode], state: Mapping[str, Any]) -> StageDecision:
        """A failed or unusable call: a person looks at the file, in the stage's own queue."""
        if base["stage"] == "sanction_docs":  # the APR check needs no model, so a finding is never lost to an outage
            found = self._apr_gap(state)
            if found is not None and found[2] > self._policy.modules.E.deterministic_apr_tolerance_pp:
                codes = [*codes, ReasonCode.APR_MISMATCH_DETERMINISTIC]
        return StageDecision(
            **base, outcome=Outcome.HUMAN_REVIEW, queue=STAGE_FAILURE_QUEUE[base["stage"]], closeness=None, top_reason=None,
            modules={}, reason_codes=[str(c) for c in codes],
        )

    # ------------------------------------------------------------------ appraisal (modules A, B, C, D)

    def _appraisal(self, base: dict, reads: dict[str, _Read], derived: dict, segment: str) -> StageDecision:
        by_module = {m: [r for r in reads.values() if r.question.module == m] for m in "ABCD"}
        weights = self._policy.modules.C.segments[segment].weights
        a = self._module_a(by_module["A"], weights)
        b = self._module_b(by_module["B"], weights)
        c = self._module_c(by_module["C"], segment)
        d = self._module_d(by_module["D"])
        modules = {"A": a, "B": b, "C": c, "D": d}
        composite, band = c.score, c.band
        common = {**base, "modules": modules, "composite": composite, "band": band, "derived": derived}

        if b.outcome == "signal":  # 2: fraud outranks paperwork; never a decline
            return StageDecision(**common, outcome=Outcome(self._policy.modules.B.on_signal), queue=FRAUD_INVESTIGATION_QUEUE,
                                 closeness=None, top_reason=None, reason_codes=[ReasonCode.FRAUD_SIGNAL.value], reasons=list(b.reasons))
        if a.outcome == "fail":  # 3: name what is missing
            return StageDecision(**common, outcome=Outcome(self._policy.modules.A.on_fail), queue=DEFICIENCY_OPS, closeness=None,
                                 top_reason=None, reason_codes=[ReasonCode.READINESS_FAIL.value], reasons=list(a.reasons),
                                 deficiency_items=[r.text for r in a.reasons])

        uncertain = [
            self._reason(r) for r in reads.values() if r.low_confidence and self._is_routing(r, weights)
        ]
        if uncertain:  # 4: blocks PROCEED and DECLINE_RECOMMENDED alike
            return StageDecision(**common, outcome=Outcome.HUMAN_REVIEW, queue=CREDIT_REVIEW, closeness=None, top_reason=None,
                                 reason_codes=[ReasonCode.LOW_CONFIDENCE.value], reasons=uncertain)
        if composite is None:  # nothing to weigh: never proceed on an empty composite
            return StageDecision(**common, outcome=Outcome.HUMAN_REVIEW, queue=CREDIT_REVIEW, closeness=None, top_reason=None,
                                 reason_codes=[ReasonCode.NO_APPRAISAL_ANSWERS.value])

        if band == BAND_PASS:  # 5
            return StageDecision(**common, outcome=Outcome.PROCEED_TO_SANCTIONING_AUTHORITY, queue=None, closeness=None,
                                 top_reason=None, reason_codes=[ReasonCode.COMPOSITE_PASS.value], reasons=list(c.reasons))
        if band == BAND_FAIL:
            return StageDecision(**common, outcome=Outcome.DECLINE_RECOMMENDED, queue=DECLINE_CONFIRMATION, closeness=None,
                                 top_reason=None, reason_codes=[ReasonCode.COMPOSITE_FAIL.value], reasons=list(c.reasons))
        closeness = d.score
        top = d.reasons[0] if d.reasons and self._policy.modules.D.attach_top_doubt else None
        return StageDecision(**common, outcome=Outcome.HUMAN_REVIEW, queue=CREDIT_REVIEW, closeness=closeness, top_reason=top,
                             reason_codes=[ReasonCode.COMPOSITE_BORDERLINE.value], reasons=list(c.reasons))

    def _module_a(self, reads: list[_Read], weights: Mapping[str, float]) -> ModuleResult:
        cfg = self._policy.modules.A
        fails = [r for r in reads if r.usable and not r.low_confidence and r.value < cfg.fail_if_p_below]
        uncertain = any(r.low_confidence and self._is_routing(r, weights) for r in reads)
        outcome = "not_run" if not reads else "fail" if fails else "uncertain" if uncertain else "pass"
        return ModuleResult(
            "A", outcome, self._lowest(reads), None, [r.qid for r in fails],
            [self._reason(r, cfg.missing_item_names[r.qid]) for r in fails], uncertain,
        )

    def _module_b(self, reads: list[_Read], weights: Mapping[str, float]) -> ModuleResult:
        rule = self._policy.modules.B.investigate_if
        signals = [
            r for r in reads
            if r.usable and (r.value >= rule.yes_is_bad.p_at_least if r.question.risk_polarity == "yes_is_bad"
                             else r.value <= rule.yes_is_good.p_at_most)
        ]
        uncertain = any(r.low_confidence and self._is_routing(r, weights) for r in reads)
        outcome = "not_run" if not reads else "signal" if signals else "uncertain" if uncertain else "clear"
        return ModuleResult("B", outcome, self._lowest(reads), None, [r.qid for r in signals], [self._reason(r) for r in signals], uncertain)

    def _module_c(self, reads: list[_Read], segment: str) -> ModuleResult:
        cfg = self._policy.modules.C.segments[segment]
        present = [r for r in reads if r.usable and r.qid in cfg.weights]
        uncertain = any(r.low_confidence and self._is_routing(r, cfg.weights) for r in reads)
        total = sum(cfg.weights[r.qid] for r in present)
        if not present:
            return ModuleResult("C", "uncertain" if uncertain else "not_run", None, None, [], [], uncertain)
        # Weights are renormalised over the questions that apply to (and were answered for) this file.
        composite = round(sum(cfg.weights[r.qid] * r.goodness for r in present) / total, _PRECISION)
        band = BAND_PASS if composite >= cfg.bands.pass_ else BAND_BORDERLINE if composite >= cfg.bands.borderline else BAND_FAIL
        drag = sorted(((cfg.weights[r.qid] / total * (1 - r.goodness), r) for r in present), key=lambda t: (-t[0], t[1].qid))
        drivers = [r for gap, r in drag if gap > 0]  # biggest weighted drag on the composite first
        return ModuleResult(
            "C", "uncertain" if uncertain else band, composite, band, [r.qid for r in drivers], [self._reason(r) for r in drivers], uncertain
        )

    def _module_d(self, reads: list[_Read]) -> ModuleResult:
        closeness_read = next((r for r in reads if r.qid == _CLOSENESS_QID and r.usable), None)
        closeness = None if closeness_read is None else round(closeness_read.goodness, _PRECISION)
        doubts = [r for r in reads if r.qid.startswith(_DOUBT_PREFIX) and r.usable]
        top = min(doubts, key=lambda r: (-r.value, r.qid), default=None)  # highest p; ties go to the earlier qid
        return ModuleResult(
            "D", "scored" if closeness is not None else "not_run", closeness, None, [] if top is None else [top.qid],
            [] if top is None else [self._reason(top)], any(r.low_confidence for r in reads),
        )

    @staticmethod
    def _lowest(reads: list[_Read]) -> float | None:
        return min((r.goodness for r in reads if r.usable), default=None)

    # ------------------------------------------------------------------ sanction documents (module E)

    def _apr_gap(self, state: Mapping[str, Any]) -> tuple[float, float, float] | None:
        """(stated, recomputed, absolute gap in percentage points) from the state's KFS block, or None if either is missing."""
        kfs = state.get("kfs") if isinstance(state, Mapping) else None
        if not isinstance(kfs, Mapping):
            return None
        stated, recomputed = _number(kfs.get("apr_stated_pct")), _number(kfs.get("apr_recomputed_pct"))
        if stated is None or recomputed is None:
            return None
        return stated, recomputed, round(abs(stated - recomputed), _PRECISION)

    def _sanction_docs(self, base: dict, reads: dict[str, _Read], derived: dict, state: Mapping[str, Any]) -> StageDecision:
        cfg = self._policy.modules.E
        e_reads = list(reads.values())
        fails = [r for r in e_reads if r.usable and not r.low_confidence and r.value < cfg.fail_if_p_below]
        uncertain = [r for r in e_reads if r.low_confidence]

        codes: list[ReasonCode] = []
        reasons: list[Reason] = []
        gap = self._apr_gap(state)
        if gap is None:  # no deterministic check, no clearance
            codes.append(ReasonCode.APR_CHECK_UNAVAILABLE)
            reasons.append(Reason(_APR_PSEUDO_QID, None, 0.0, "Stated or recomputed APR is missing from the file, so the APR could not be checked."))
        elif gap[2] > cfg.deterministic_apr_tolerance_pp:  # authoritative: holds even if the model says yes
            codes.append(ReasonCode.APR_MISMATCH_DETERMINISTIC)
            reasons.append(Reason(
                _APR_PSEUDO_QID, None, 1.0,
                f"Stated APR {gap[0]:.2f}% differs from the recomputed APR {gap[1]:.2f}% by {gap[2]:.2f} percentage points "
                f"(tolerance {cfg.deterministic_apr_tolerance_pp:.2f}).",
            ))
        if fails:
            codes.append(ReasonCode.E_CHECK_FAILED)
            reasons += [self._reason(r) for r in fails]
        if uncertain:  # uncertainty blocks disbursal; it never clears it
            codes.append(ReasonCode.LOW_CONFIDENCE)
            reasons += [self._reason(r) for r in uncertain]

        e = ModuleResult(
            "E", "fail" if fails else "uncertain" if uncertain else "pass", self._lowest(e_reads), None,
            [r.qid for r in fails], [self._reason(r) for r in fails], bool(uncertain),
        )
        common = {**base, "modules": {"E": e}, "derived": derived, "closeness": None, "top_reason": None}
        if codes:
            return StageDecision(**common, outcome=Outcome(cfg.on_fail), queue=DISBURSAL_CORRECTION,
                                 reason_codes=[str(c) for c in codes], reasons=reasons)
        return StageDecision(**common, outcome=Outcome.DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF, queue=None,
                             reason_codes=[ReasonCode.MEMO_KFS_CONSISTENT.value])

    # ------------------------------------------------------------------ monitoring (module F)

    def _monitoring(self, base: dict, reads: dict[str, _Read], derived: dict) -> StageDecision:
        cfg = self._policy.modules.F
        warn_at, breach_at = cfg.warning_threshold_p, round(1 - cfg.warning_threshold_p, _PRECISION)
        ews = [r for r in reads.values() if r.qid.startswith(_EWS_PREFIX) and r.usable and r.value >= warn_at]
        breaches = [r for r in reads.values() if r.qid.startswith(_COVENANT_PREFIX) and r.usable and round(r.value, _PRECISION) <= breach_at]
        warnings = len(ews) + cfg.covenant_breach_counts_as * len(breaches)  # a covenant breach is worth more than one warning
        tier = next((name for name, needed in reversed(cfg.tiers.items()) if warnings >= needed), "T0")
        flagged = sorted([*ews, *breaches], key=lambda r: r.qid)
        f = ModuleResult("F", tier, float(warnings), None, [r.qid for r in flagged], [self._reason(r) for r in flagged],
                         any(r.low_confidence for r in reads.values()))
        outcome = Outcome(f"WATCHLIST_{tier}")
        return StageDecision(
            **base, outcome=outcome, queue=OUTCOME_QUEUE[outcome], closeness=None, top_reason=None, modules={"F": f},
            reason_codes=[f"WATCHLIST_{tier}"], reasons=list(f.reasons), derived=derived,
        )
