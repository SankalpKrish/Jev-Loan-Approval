"""Policy and pricing configuration: typed, validated, immutable (PLAN sections 3.9 and 3.10).

A bad file must fail at load time, not on the first file it mis-routes. Besides shape, this checks the rules
that make the system safe to run: weights sum to one, bands are ordered, every queue has a real owner key,
reason codes are unique, and the two switches that keep a human in the loop (`decline.require_human_confirmation`
and `modules.B.never_auto_decline`) are true. There is no code path that turns either off.
"""

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from jevloan.config import ConfigError, load_yaml_versioned
from jevloan.data.schema import SEGMENTS
from jevloan.policy.outcomes import PRICEABLE_BANDS, QUEUES, ReasonCode

POLICY_PATH = "config/policy/policy_v1.yaml"
PRICING_PATH = "config/pricing/pricing_v1.yaml"
UNASSIGNED = "UNASSIGNED"
WEIGHT_TOLERANCE = 1e-6
TIER_NAMES = ("T1", "T2", "T3")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _unit(name: str, value: float, *, open_low: bool = False) -> float:
    if not math.isfinite(value) or not (0 < value <= 1 if open_low else 0 <= value <= 1):
        raise ValueError(f"{name} must be a probability in {'(0, 1]' if open_low else '[0, 1]'}, got {value!r}")
    return value


# ------------------------------------------------------------------------------------------------ policy


class Owner(_M):
    role: str = Field(min_length=1)
    owner_name: str = Field(min_length=1)


class MinConfidence(_M):
    noul: float
    choice: float
    score: float

    @field_validator("noul", "choice", "score")
    @classmethod
    def _in_unit(cls, v: float) -> float:
        return _unit("min_confidence", v)


class ModuleA(_M):
    fail_if_p_below: float
    on_fail: Literal["DEFICIENCY_NOTICE"]
    missing_item_names: dict[str, str]

    @field_validator("fail_if_p_below")
    @classmethod
    def _p(cls, v: float) -> float:
        return _unit("A.fail_if_p_below", v, open_low=True)

    @field_validator("missing_item_names")
    @classmethod
    def _names(cls, v: dict[str, str]) -> dict[str, str]:
        if not v or any(not qid.startswith("A_") or not name.strip() for qid, name in v.items()):
            raise ValueError("A.missing_item_names needs a non-empty human-readable name for each A_ question")
        return v


class _AtLeast(_M):
    p_at_least: float

    @field_validator("p_at_least")
    @classmethod
    def _p(cls, v: float) -> float:
        return _unit("B.p_at_least", v, open_low=True)


class _AtMost(_M):
    p_at_most: float

    @field_validator("p_at_most")
    @classmethod
    def _p(cls, v: float) -> float:
        return _unit("B.p_at_most", v)


class InvestigateIf(_M):
    yes_is_bad: _AtLeast
    yes_is_good: _AtMost


class ModuleB(_M):
    investigate_if: InvestigateIf
    on_signal: Literal["FRAUD_INVESTIGATION"]
    never_auto_decline: bool

    @field_validator("never_auto_decline")
    @classmethod
    def _never(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("modules.B.never_auto_decline must be true: a fraud signal goes to investigation, never to a decline")
        return v


class Bands(_M):
    pass_: float = Field(alias="pass")
    borderline: float

    @model_validator(mode="after")
    def _ordered(self) -> "Bands":
        if not (0 < self.borderline < self.pass_ <= 1):
            raise ValueError(f"bands must satisfy 0 < borderline < pass <= 1, got borderline={self.borderline}, pass={self.pass_}")
        return self


class SegmentC(_M):
    weights: dict[str, float]
    bands: Bands

    @field_validator("weights")
    @classmethod
    def _weights(cls, v: dict[str, float]) -> dict[str, float]:
        if not v or any(not qid.startswith("C_") or not math.isfinite(w) or w <= 0 for qid, w in v.items()):
            raise ValueError("C weights must be positive numbers keyed by C_ question ids")
        if abs(sum(v.values()) - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"C weights must sum to 1 (+/-{WEIGHT_TOLERANCE:g}), got {sum(v.values()):.9f}")
        return v


class ModuleC(_M):
    segments: dict[str, SegmentC]

    @field_validator("segments")
    @classmethod
    def _all_segments(cls, v: dict[str, SegmentC]) -> dict[str, SegmentC]:
        if set(v) != set(SEGMENTS):
            raise ValueError(f"C.segments must cover exactly {sorted(SEGMENTS)}, got {sorted(v)}")
        return v


class ModuleD(_M):
    order_by: Literal["closeness_desc"]
    attach_top_doubt: bool


class ModuleE(_M):
    fail_if_p_below: float
    deterministic_apr_tolerance_pp: float = Field(ge=0)
    on_fail: Literal["DISBURSAL_BLOCKED"]

    @field_validator("fail_if_p_below")
    @classmethod
    def _p(cls, v: float) -> float:
        return _unit("E.fail_if_p_below", v, open_low=True)


class ModuleF(_M):
    warning_threshold_p: float
    tiers: dict[str, int]
    covenant_breach_counts_as: int = Field(ge=1)

    @field_validator("warning_threshold_p")
    @classmethod
    def _p(cls, v: float) -> float:
        return _unit("F.warning_threshold_p", v, open_low=True)

    @field_validator("tiers")
    @classmethod
    def _tiers(cls, v: dict[str, int]) -> dict[str, int]:
        if tuple(v) != TIER_NAMES:
            raise ValueError(f"F.tiers must list exactly {TIER_NAMES} in that order, got {tuple(v)}")
        counts = list(v.values())
        if counts[0] < 1 or any(a >= b for a, b in zip(counts, counts[1:], strict=False)):
            raise ValueError(f"F.tiers must be strictly increasing warning counts starting at 1 or more, got {v}")
        return v


class Modules(_M):
    A: ModuleA
    B: ModuleB
    C: ModuleC
    D: ModuleD
    E: ModuleE
    F: ModuleF


class Decline(_M):
    require_human_confirmation: bool

    @field_validator("require_human_confirmation")
    @classmethod
    def _human(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("decline.require_human_confirmation must be true: a decline recommendation is never final without a human")
        return v


class Policy(_M):
    version: str
    sha256: str
    owners: dict[str, Owner]
    queues: dict[str, str]
    noul_confidence: Literal["abs(2p-1)"]
    min_confidence: MinConfidence
    modules: Modules
    decline: Decline
    reason_codes: dict[str, dict[str, str]]  # queue -> {code: description}: what a reviewer can record
    system_reason_codes: dict[str, str]  # what the engine attaches to a decision

    @model_validator(mode="after")
    def _consistent(self) -> "Policy":
        if set(self.queues) != set(QUEUES):
            raise ValueError(f"queues must be exactly {sorted(QUEUES)}, got {sorted(self.queues)}")
        unknown_owners = sorted({o for o in self.queues.values() if o not in self.owners})
        if unknown_owners:
            raise ValueError(f"queues reference owner keys that do not exist: {unknown_owners}")
        stray = sorted(set(self.reason_codes) - set(QUEUES))
        if stray:
            raise ValueError(f"reason_codes are grouped by queue; unknown queue(s): {stray}")
        missing_groups = sorted(set(QUEUES) - set(self.reason_codes))
        if missing_groups:
            raise ValueError(f"every queue needs reviewer reason codes; none for: {missing_groups}")

        seen: set[str] = set()
        for code in [c for group in self.reason_codes.values() for c in group] + list(self.system_reason_codes):
            if code in seen:
                raise ValueError(f"reason code {code!r} is defined more than once")
            seen.add(code)
        if any(not desc.strip() for group in self.reason_codes.values() for desc in group.values()):
            raise ValueError("every reason code needs a description")

        wanted = {c.value for c in ReasonCode}
        if set(self.system_reason_codes) != wanted:
            raise ValueError(
                "system_reason_codes must describe exactly the codes the engine emits; "
                f"missing {sorted(wanted - set(self.system_reason_codes))}, unknown {sorted(set(self.system_reason_codes) - wanted)}"
            )
        return self

    # ---- lookups the queue, pipeline and API use
    def human_reason_codes(self) -> dict[str, str]:
        """Every code a reviewer may record, across queues."""
        return {code: desc for group in self.reason_codes.values() for code, desc in group.items()}

    def reason_codes_for(self, queue: str) -> dict[str, str]:
        return dict(self.reason_codes[queue])

    def owner_for_queue(self, queue: str) -> Owner:
        return self.owners[self.queues[queue]]

    def describe(self, code: str) -> str:
        """The description of any reason code, reviewer-recorded or engine-attached."""
        return self.human_reason_codes().get(code) or self.system_reason_codes[code]


def owners_unassigned(policy: Policy) -> list[str]:
    """Owner keys that still have no named human (production start-up refuses while this is non-empty; decision D9)."""
    return [key for key, owner in policy.owners.items() if not owner.owner_name.strip() or owner.owner_name.strip().upper() == UNASSIGNED]


def load_policy(path: str | Path | None = None) -> Policy:
    """Load and validate the policy YAML (default: config/policy/policy_v1.yaml). Raises ConfigError if it is unsafe."""
    where = path or POLICY_PATH
    data, version, sha = load_yaml_versioned(where)
    data["version"] = version
    try:
        return Policy(sha256=sha, **data)
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"invalid policy {where}: {exc}") from exc


# ------------------------------------------------------------------------------------------------ pricing


class PremiumTier(_M):
    composite_at_least: float
    premium_pct: float

    @field_validator("composite_at_least")
    @classmethod
    def _c(cls, v: float) -> float:
        return _unit("composite_at_least", v)


class PricingConfig(_M):
    version: str
    sha256: str
    benchmark_rate_pct: float
    segment_spread_pct: dict[str, float]
    risk_premium_pct_by_band: dict[str, list[PremiumTier]]
    bureau_adjustment_pct: dict[str, float]
    bureau_adjustment_default_pct: float
    floor_pct: dict[str, float]
    cap_pct: dict[str, float]
    processing_fee_pct: dict[str, float]

    @model_validator(mode="after")
    def _consistent(self) -> "PricingConfig":
        for name in ("segment_spread_pct", "floor_pct", "cap_pct", "processing_fee_pct"):
            if set(getattr(self, name)) != set(SEGMENTS):
                raise ValueError(f"{name} must cover exactly {sorted(SEGMENTS)}")
        for segment in SEGMENTS:
            if self.floor_pct[segment] > self.cap_pct[segment]:
                raise ValueError(f"floor_pct exceeds cap_pct for {segment}")
            if not 0 <= self.processing_fee_pct[segment] < 100:
                raise ValueError(f"processing_fee_pct for {segment} must be in [0, 100)")
        if set(self.risk_premium_pct_by_band) != set(PRICEABLE_BANDS):
            raise ValueError(f"risk_premium_pct_by_band must cover exactly {list(PRICEABLE_BANDS)}: no other band can be priced")
        for band, tiers in self.risk_premium_pct_by_band.items():
            floors = [t.composite_at_least for t in tiers]
            if not tiers or any(a <= b for a, b in zip(floors, floors[1:], strict=False)):
                raise ValueError(f"premium tiers for {band} must be non-empty and strictly descending by composite_at_least")
        return self


def load_pricing(path: str | Path | None = None) -> PricingConfig:
    """Load and validate the pricing YAML (default: config/pricing/pricing_v1.yaml)."""
    where = path or PRICING_PATH
    data, version, sha = load_yaml_versioned(where)
    data["version"] = version
    try:
        return PricingConfig(sha256=sha, **data)
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"invalid pricing config {where}: {exc}") from exc
