"""Run one loan file through the three model and policy stages."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from jevloan.audit.log import AuditLog
from jevloan.config import RuntimeConfig, resolve_path
from jevloan.data.schema import LoanFile, load_disclosures, load_grid
from jevloan.jev.gateway import JevGateway
from jevloan.modules import base as module_base
from jevloan.policy.config import PricingConfig, Policy, load_policy, load_pricing
from jevloan.policy.engine import PolicyEngine, StageDecision
from jevloan.policy.outcomes import Outcome
from jevloan.policy.pricing import price
from jevloan.pii.gate import PIIGate
from jevloan.queue.store import HumanQueue
from jevloan.state import build_state
from jevloan.state.base import STAGES


def decision_dict(decision: StageDecision) -> dict[str, Any]:
    """Convert the complete policy result to a JSON-safe mapping."""
    return asdict(decision)


class Pipeline:
    """The side-effecting orchestration layer around state, Jev, policy, queue and pricing."""

    def __init__(
        self,
        runtime: RuntimeConfig,
        policy_engine: PolicyEngine,
        pricing: PricingConfig,
        gateway: JevGateway,
        audit: AuditLog,
        queue: HumanQueue,
        *,
        policy: Policy | None = None,
    ) -> None:
        self.runtime = runtime
        self.policy_engine = policy_engine
        self.pricing = pricing
        self.gateway = gateway
        self.audit = audit
        self.queue = queue
        self.policy = policy

    @property
    def policy_version(self) -> str:
        return self.policy_engine.policy_version

    def has_sanction_authorization(self, file_id: str) -> bool:
        """Whether an explicit, recorded appraisal sanction authorization exists for this file."""
        for row in reversed(self.audit.trail(file_id)):
            if row.get("event_type") != "HUMAN_DECISION" or row.get("stage") != "appraisal":
                continue
            if row.get("policy_version") != self.policy_version:
                return False
            detail = row.get("detail_json")
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except json.JSONDecodeError:
                    return False
            if (
                row.get("human_decision") == "accept"
                and row.get("policy_outcome") == Outcome.PROCEED_TO_SANCTIONING_AUTHORITY.value
                and isinstance(row.get("reviewer_id"), str)
                and bool(row["reviewer_id"].strip())
                and isinstance(detail, dict)
                and detail.get("sanction_authorized") is True
            ):
                return True
            return False
        return False

    async def run_stage(self, file: LoanFile, stage: str, *, offline: bool = False) -> StageDecision:
        """Run exactly one stage and persist its policy, queue and optional pricing outcomes.

        Replay sets ``offline=True`` so every file receives all stages. Online sanction document review is
        gated by an explicit HUMAN_DECISION audit marker written by an authorized reviewer.
        """
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        if stage == "sanction_docs" and not offline and not self.has_sanction_authorization(file.file_id):
            raise PermissionError(
                f"sanction_docs for {file.file_id} requires an audited appraisal HUMAN_DECISION with "
                "decision='accept' and detail_json.sanction_authorized=true"
            )

        state, questions = await asyncio.to_thread(_prepare_stage, file, stage)
        call = await self.gateway.ask(
            file_id=file.file_id,
            segment=file.segment,
            stage=stage,
            policy_version=self.policy_version,
            state=state,
            questions=questions,
        )
        decision = self.policy_engine.decide(
            file_id=file.file_id,
            segment=file.segment,
            stage=stage,
            call=call,
            state=state,
            asked=questions,
        )
        self.audit.append(
            event_type="POLICY_OUTCOME",
            file_id=file.file_id,
            segment=file.segment,
            stage=stage,
            policy_version=self.policy_version,
            model_version=call.model_version,
            policy_outcome=str(decision.outcome),
            latency_ms=call.latency_ms,
            input_tokens=call.input_tokens,
            detail_json={"decision": decision_dict(decision), "failure_kind": call.failure},
        )

        if decision.queue is not None:
            self.queue.enqueue(decision)  # this is the one and only QUEUED audit writer

        if decision.outcome == Outcome.PROCEED_TO_SANCTIONING_AUTHORITY:
            if decision.composite is None or decision.band is None:
                raise ValueError(f"policy returned unpriceable PROCEED decision for {file.file_id}: missing composite/band")
            priced = price(
                segment=file.segment,
                product=file.application.product,
                band=decision.pricing_band or decision.band,
                composite=decision.composite,
                bureau_score_band=_bureau_band(file.bureau.score),
                principal_inr=file.application.loan_amount_inr,
                tenure_months=file.application.tenure_months,
                pricing=self.pricing,
            )
            self.audit.append(
                event_type="PRICING",
                file_id=file.file_id,
                segment=file.segment,
                stage=stage,
                policy_version=self.policy_version,
                policy_outcome=str(decision.outcome),
                detail_json=priced.to_dict(),
            )
        return decision

    async def process_file(
        self,
        file: LoanFile,
        stages: tuple[str, ...] = STAGES,
        *,
        offline: bool = False,
    ) -> dict[str, StageDecision]:
        """Run requested stages in order; repeated stages are executed as separate attempts.

        The returned mapping is keyed by stage for convenience, so for repeated stages the latest decision wins;
        every attempt is still represented in the audit log and replay output.
        """
        results: dict[str, StageDecision] = {}
        for stage in stages:
            results[stage] = await self.run_stage(file, stage, offline=offline)
        return results


def _prepare_stage(file: LoanFile, stage: str) -> tuple[dict, dict]:
    """Keep redaction and rubric preparation from delaying in-flight model responses."""
    state = build_state(file, stage)
    return state, module_base.questions_for(stage, file.segment, state)


def _bureau_band(score: int | None) -> str:
    from jevloan.state.base import bureau_score_band

    return bureau_score_band(score)


def build_pipeline(runtime: RuntimeConfig, *, transport: Any = None) -> Pipeline:
    """Load configured W2 components and wire a ready-to-run pipeline."""
    policy = load_policy()
    pricing = load_pricing()
    audit = AuditLog(resolve_path(runtime.db_path))
    from jevloan.modules.base import catalog

    policy_engine = PolicyEngine(policy, catalog(), load_grid(), load_disclosures())
    gate = PIIGate()
    gateway = JevGateway(runtime, audit, gate, transport=transport)
    queue = HumanQueue(resolve_path(runtime.db_path), audit, policy=policy)
    return Pipeline(runtime, policy_engine, pricing, gateway, audit, queue, policy=policy)
