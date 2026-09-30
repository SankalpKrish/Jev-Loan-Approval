"""The versioned pricing formula (RBI constraint (e): every rate defensible line by line; PLAN section 3.9).

    rate = benchmark + segment spread + risk premium(band, composite) + bureau adjustment, clamped to [floor, cap]

The model never sets price. The only model-derived inputs are the C band and composite, and they arrive from the
policy engine. Only a file that PROCEEDs ("pass") or that a human approved from the borderline queue
("borderline_approved_by_human") can be priced; any other band raises. All arithmetic is exact decimal, so the
line items add up to the rate to the last digit. If the clamp moves the rate, the move is its own line item.
"""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

from jevloan.data.schema import PRODUCT_BY_SEGMENT
from jevloan.finance import apr_from_components, emi
from jevloan.policy.config import PricingConfig
from jevloan.policy.outcomes import PRICEABLE_BANDS


class PricingError(ValueError):
    """The file cannot be priced under this formula (wrong band, unknown segment, inconsistent inputs)."""


@dataclass(frozen=True)
class PricingResult:
    rate_pct: float  # after the floor/cap clamp
    apr_pct: float  # nominal annualised IRR including the processing fee
    processing_fee_inr: int
    emi_inr: int
    line_items: list[dict]  # [{name, value_pct, source}]: they sum exactly to rate_pct
    formula_version: str
    pre_clamp_rate_pct: float  # the sum of the four formula terms, before any clamp

    def to_dict(self) -> dict:
        """JSON-ready form for the PRICING audit entry."""
        return asdict(self)


def _dec(x: float) -> Decimal:
    return Decimal(str(x))


def price(
    *,
    segment: str,
    product: str,
    band: str,
    composite: float,
    bureau_score_band: str,
    principal_inr: int | float,
    tenure_months: int,
    pricing: PricingConfig,
) -> PricingResult:
    """Price one file. Raises PricingError for a band that may not be priced or inputs that do not fit the formula."""
    if band not in PRICEABLE_BANDS:
        raise PricingError(f"band {band!r} cannot be priced: only {list(PRICEABLE_BANDS)} may be (the model never sets price)")
    if segment not in pricing.segment_spread_pct:
        raise PricingError(f"unknown segment {segment!r}")
    if PRODUCT_BY_SEGMENT.get(segment) != product:
        raise PricingError(f"product {product!r} does not belong to segment {segment!r}")
    if not 0 <= composite <= 1:
        raise PricingError(f"composite must be in [0, 1], got {composite!r}")
    if principal_inr <= 0 or tenure_months <= 0:
        raise PricingError("principal and tenure must be positive")
    tier = next((t for t in pricing.risk_premium_pct_by_band[band] if composite >= t.composite_at_least), None)
    if tier is None:
        raise PricingError(f"composite {composite} is below every premium tier configured for band {band!r}")

    if bureau_score_band in pricing.bureau_adjustment_pct:
        bureau, bureau_source = pricing.bureau_adjustment_pct[bureau_score_band], f"bureau_adjustment_pct[{bureau_score_band}]"
    else:
        bureau, bureau_source = pricing.bureau_adjustment_default_pct, "bureau_adjustment_default_pct"
    terms = [
        ("benchmark_rate", pricing.benchmark_rate_pct, "benchmark_rate_pct"),
        ("segment_spread", pricing.segment_spread_pct[segment], f"segment_spread_pct[{segment}]"),
        ("risk_premium", tier.premium_pct, f"risk_premium_pct_by_band[{band}] composite>={tier.composite_at_least:g}"),
        ("bureau_adjustment", bureau, bureau_source),
    ]
    items = [{"name": name, "value_pct": _dec(value), "source": source} for name, value, source in terms]
    pre_clamp = sum((i["value_pct"] for i in items), Decimal(0))

    floor, cap = _dec(pricing.floor_pct[segment]), _dec(pricing.cap_pct[segment])
    rate = min(max(pre_clamp, floor), cap)
    if rate != pre_clamp:
        which = "floor" if rate == floor else "cap"
        items.append({"name": f"{which}_clamp", "value_pct": rate - pre_clamp, "source": f"{which}_pct[{segment}]"})
    if sum((i["value_pct"] for i in items), Decimal(0)) != rate:  # cannot happen; a wrong price must not escape
        raise PricingError("internal error: line items do not add up to the rate")

    fee = int((_dec(principal_inr) * _dec(pricing.processing_fee_pct[segment]) / 100).quantize(Decimal(1), ROUND_HALF_UP))
    instalment = round(emi(float(principal_inr), float(rate), tenure_months))  # rounded to the rupee, as the borrower pays it
    apr = apr_from_components(float(principal_inr), fee, instalment, tenure_months)
    return PricingResult(
        rate_pct=float(rate),
        apr_pct=apr,
        processing_fee_inr=fee,
        emi_inr=instalment,
        line_items=[{**i, "value_pct": float(i["value_pct"])} for i in items],
        formula_version=pricing.version,
        pre_clamp_rate_pct=float(pre_clamp),
    )
