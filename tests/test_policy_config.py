"""Policy and pricing configuration: the shipped files load, and every unsafe edit is refused at load time."""

import copy
from pathlib import Path

import pytest
import yaml
from test_policy_catalog import POLICY_YAML, policy_variant

from jevloan.canonical import sha256_hex
from jevloan.config import ConfigError
from jevloan.data.schema import SEGMENTS
from jevloan.policy.config import Policy, load_policy, load_pricing, owners_unassigned
from jevloan.policy.outcomes import QUEUES, ReasonCode

PRICING_YAML = POLICY_YAML.parents[1] / "pricing" / "pricing_v1.yaml"


def refused(tmp_path: Path, mutate, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        policy_variant(tmp_path, mutate)


def test_shipped_policy_loads_with_version_and_hash():
    policy = load_policy()
    assert isinstance(policy, Policy)
    assert policy.version == "policy-2026.09-v1"
    assert policy.sha256 == sha256_hex(POLICY_YAML.read_bytes())
    assert load_policy(POLICY_YAML) == policy
    assert policy.min_confidence.noul == 0.5 and policy.min_confidence.choice == 0.5 and policy.min_confidence.score == 0.45
    assert policy.noul_confidence == "abs(2p-1)"


def test_shipped_policy_matches_the_plan():
    policy = load_policy()
    c = policy.modules.C.segments
    assert set(c) == set(SEGMENTS)
    assert c["salaried_personal"].weights == {"C_willingness": 0.30, "C_capacity": 0.30, "C_foir_within_limit": 0.15,
                                              "C_income_stable": 0.10, "C_recent_delinquency": 0.15}
    assert list(c["self_employed"].weights.values()) == [0.25, 0.30, 0.15, 0.15, 0.15]
    assert list(c["msme_business"].weights.values()) == [0.25, 0.35, 0.15, 0.10, 0.15]
    assert c["secured_home"].weights["C_collateral_adequacy"] == 0.15 and c["secured_home"].weights["C_collateral_title_clear"] == 0.10
    for segment in c.values():
        assert sum(segment.weights.values()) == pytest.approx(1.0, abs=1e-6)
        assert (segment.bands.pass_, segment.bands.borderline) == (0.70, 0.50)
    assert policy.modules.A.fail_if_p_below == 0.5 and policy.modules.A.on_fail == "DEFICIENCY_NOTICE"
    assert policy.modules.A.missing_item_names["A_income_proof_current"] == "Current income proof (salary slips within 2 months / latest ITR)"
    assert policy.modules.B.investigate_if.yes_is_bad.p_at_least == 0.8 and policy.modules.B.investigate_if.yes_is_good.p_at_most == 0.2
    assert policy.modules.B.never_auto_decline is True and policy.decline.require_human_confirmation is True
    assert policy.modules.D.order_by == "closeness_desc" and policy.modules.D.attach_top_doubt is True
    assert policy.modules.E.deterministic_apr_tolerance_pp == 0.10 and policy.modules.E.on_fail == "DISBURSAL_BLOCKED"
    f = policy.modules.F
    assert (f.warning_threshold_p, dict(f.tiers), f.covenant_breach_counts_as) == (0.6, {"T1": 1, "T2": 2, "T3": 3}, 2)


def test_owners_queues_and_reason_codes_are_complete():
    policy = load_policy()
    assert set(policy.queues) == set(QUEUES)
    assert set(policy.queues.values()) <= set(policy.owners)
    assert {"credit_exceptions", "fraud_investigation", "deficiency_ops", "decline_confirmation", "disbursal_correction",
            "watchlist_review", "model_risk"} == set(policy.owners)
    assert policy.owners["credit_exceptions"].role == "Zonal Credit Head"
    assert policy.owner_for_queue("credit_review") is policy.owners["credit_exceptions"]
    for code in ("ACCEPT_AS_ADVISED", "MODIFY_TERMS", "REJECT_MODEL_MISREAD_DOCS", "REJECT_MODEL_MISREAD_CONDUCT",
                 "OVERRIDE_POLICY_EXCEPTION", "FRAUD_CONFIRMED", "FRAUD_CLEARED", "DEFICIENCY_CURED"):
        assert code in policy.human_reason_codes()
    assert set(policy.reason_codes) == set(QUEUES)
    assert "FRAUD_CONFIRMED" in policy.reason_codes_for("fraud_investigation")
    # every code the engine can emit is described, and none is also offered to reviewers
    assert set(policy.system_reason_codes) == {c.value for c in ReasonCode}
    assert not set(policy.system_reason_codes) & set(policy.human_reason_codes())
    assert policy.describe("MODEL_TIMEOUT") and policy.describe("ACCEPT_AS_ADVISED")


def test_owners_unassigned_lists_every_unnamed_owner(tmp_path):
    policy = load_policy()
    assert owners_unassigned(policy) == list(policy.owners)  # dev default: nobody is named yet
    named = policy_variant(tmp_path, lambda d: [o.update(owner_name="A. Rao") for o in d["owners"].values()])
    assert owners_unassigned(named) == []
    partial = policy_variant(tmp_path, lambda d: d["owners"]["model_risk"].update(owner_name="A. Rao"))
    assert "model_risk" not in owners_unassigned(partial) and len(owners_unassigned(partial)) == len(partial.owners) - 1
    blank = policy_variant(tmp_path, lambda d: d["owners"]["model_risk"].update(owner_name="unassigned"))
    assert "model_risk" in owners_unassigned(blank)  # case does not matter


# ------------------------------------------------------------------------------------------------ refusals


def test_weights_must_sum_to_one(tmp_path):
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"]["salaried_personal"]["weights"].update(C_willingness=0.35), "sum to 1")
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"]["secured_home"]["weights"].pop("C_collateral_adequacy"), "sum to 1")
    policy_variant(tmp_path, lambda d: d["modules"]["C"]["segments"]["salaried_personal"]["weights"].update(  # within tolerance
        C_willingness=0.30 + 5e-7))


def test_weights_must_be_positive_c_questions(tmp_path):
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"]["salaried_personal"]["weights"].update(C_willingness=0.0, C_capacity=0.6), "positive")
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"]["salaried_personal"]["weights"].update(A_income_proof_current=0.0), "C_")


def test_every_segment_needs_weights(tmp_path):
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"].pop("msme_business"), "segments")


@pytest.mark.parametrize("bands", [{"pass": 0.5, "borderline": 0.7}, {"pass": 0.6, "borderline": 0.6}, {"pass": 1.2, "borderline": 0.5},
                                   {"pass": 0.7, "borderline": 0.0}, {"pass": 0.7, "borderline": -0.1}])
def test_bands_must_be_ordered(tmp_path, bands):
    refused(tmp_path, lambda d: d["modules"]["C"]["segments"]["self_employed"].update(bands=bands), "bands")


def test_human_confirmation_of_a_decline_cannot_be_switched_off(tmp_path):
    refused(tmp_path, lambda d: d["decline"].update(require_human_confirmation=False), "require_human_confirmation")
    for falsy in ("no", "false", 0, None):  # every spelling of "off" is refused, not only the literal False
        refused(tmp_path, lambda d, v=falsy: d["decline"].update(require_human_confirmation=v), "require_human_confirmation|bool")
    refused(tmp_path, lambda d: d.pop("decline"), "decline")


def test_a_fraud_signal_cannot_be_made_to_decline(tmp_path):
    refused(tmp_path, lambda d: d["modules"]["B"].update(never_auto_decline=False), "never_auto_decline")
    refused(tmp_path, lambda d: d["modules"]["B"].update(on_signal="DECLINE_RECOMMENDED"), "on_signal")


def test_routing_actions_are_fixed_to_safe_outcomes(tmp_path):
    refused(tmp_path, lambda d: d["modules"]["A"].update(on_fail="PROCEED_TO_SANCTIONING_AUTHORITY"), "on_fail")
    refused(tmp_path, lambda d: d["modules"]["E"].update(on_fail="DISBURSAL_CLEARED_FOR_HUMAN_SIGNOFF"), "on_fail")


def test_owner_keys_referenced_by_queues_must_exist(tmp_path):
    refused(tmp_path, lambda d: d["queues"].update(credit_review="nobody"), "owner keys")
    refused(tmp_path, lambda d: d["owners"].pop("watchlist_review"), "owner keys")


def test_queues_must_be_exactly_the_known_queues(tmp_path):
    refused(tmp_path, lambda d: d["queues"].pop("fraud_investigation"), "queues must be")
    refused(tmp_path, lambda d: d["queues"].update(extra_queue="model_risk"), "queues must be")


def test_reason_codes_must_be_unique_and_described(tmp_path):
    refused(tmp_path, lambda d: d["reason_codes"]["fraud_investigation"].update(ACCEPT_AS_ADVISED="dup"), "more than once")
    refused(tmp_path, lambda d: d["system_reason_codes"].update(FRAUD_CONFIRMED="dup"), "more than once|exactly")
    refused(tmp_path, lambda d: d["reason_codes"]["credit_review"].update(ACCEPT_AS_ADVISED="  "), "description")
    refused(tmp_path, lambda d: d["reason_codes"].update(made_up_queue={"X": "y"}), "unknown queue")
    refused(tmp_path, lambda d: d["reason_codes"].pop("watchlist_review"), "none for")


def test_every_engine_code_needs_a_description(tmp_path):
    refused(tmp_path, lambda d: d["system_reason_codes"].pop("LOW_CONFIDENCE"), "LOW_CONFIDENCE")
    refused(tmp_path, lambda d: d["system_reason_codes"].update(NOT_A_CODE="x"), "NOT_A_CODE")


@pytest.mark.parametrize("path,value", [
    (("min_confidence", "noul"), 1.5), (("min_confidence", "score"), -0.1), (("modules", "A", "fail_if_p_below"), 0.0),
    (("modules", "B", "investigate_if", "yes_is_bad", "p_at_least"), 1.5), (("modules", "E", "deterministic_apr_tolerance_pp"), -1),
    (("modules", "F", "warning_threshold_p"), 0), (("modules", "F", "covenant_breach_counts_as"), 0),
    (("modules", "F", "tiers"), {"T1": 2, "T2": 2, "T3": 3}), (("modules", "F", "tiers"), {"T1": 0, "T2": 1, "T3": 2}),
    (("modules", "F", "tiers"), {"T1": 1, "T2": 2}), (("noul_confidence",), "p"), (("modules", "D", "order_by"), "oldest_first"),
    (("modules", "A", "missing_item_names"), {}),
])
def test_out_of_range_values_are_refused(tmp_path, path, value):
    def mutate(d):
        node = d
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    with pytest.raises(ConfigError):
        policy_variant(tmp_path, mutate)


def test_unknown_keys_and_missing_sections_are_refused(tmp_path):
    refused(tmp_path, lambda d: d.update(surprise=1), "surprise")
    refused(tmp_path, lambda d: d["modules"].pop("F"), "F")
    refused(tmp_path, lambda d: d.pop("owners"), "owners")


def test_missing_version_or_file_is_refused(tmp_path):
    data = yaml.safe_load(POLICY_YAML.read_text())
    del data["version"]
    (tmp_path / "p.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError, match="version"):
        load_policy(tmp_path / "p.yaml")
    with pytest.raises(ConfigError, match="not found"):
        load_policy(tmp_path / "missing.yaml")


def test_a_loaded_policy_is_immutable():
    policy = load_policy()
    with pytest.raises(Exception):
        policy.version = "x"
    with pytest.raises(Exception):
        policy.decline.require_human_confirmation = False


# ------------------------------------------------------------------------------------------------ pricing config


def pricing_variant(tmp_path: Path, mutate):
    data = copy.deepcopy(yaml.safe_load(PRICING_YAML.read_text()))
    mutate(data)
    path = tmp_path / "pricing_variant.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return load_pricing(path)


def test_shipped_pricing_loads():
    pricing = load_pricing()
    assert pricing.version == "pricing-2026.09-v1" and pricing.sha256 == sha256_hex(PRICING_YAML.read_bytes())
    assert pricing.benchmark_rate_pct == 8.75
    assert pricing.segment_spread_pct == {"salaried_personal": 3.0, "self_employed": 3.75, "msme_business": 3.0, "secured_home": 0.25}
    assert [(t.composite_at_least, t.premium_pct) for t in pricing.risk_premium_pct_by_band["pass"]] == [(0.85, 0.0), (0.70, 0.5)]
    assert [(t.composite_at_least, t.premium_pct) for t in pricing.risk_premium_pct_by_band["borderline_approved_by_human"]] == [(0.0, 1.25)]
    assert pricing.bureau_adjustment_pct == {"800+": -0.25, "750-799": 0.0, "700-749": 0.25, "650-699": 0.5, "NTC": 0.75}
    assert pricing.bureau_adjustment_default_pct == 1.0
    assert set(pricing.floor_pct) == set(pricing.cap_pct) == set(pricing.processing_fee_pct) == set(SEGMENTS)


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["floor_pct"].update(salaried_personal=20.0), "floor_pct exceeds cap_pct"),
    (lambda d: d["segment_spread_pct"].pop("secured_home"), "segment_spread_pct"),
    (lambda d: d["processing_fee_pct"].update(msme_business=120), "processing_fee_pct"),
    (lambda d: d["risk_premium_pct_by_band"].update(borderline=[{"composite_at_least": 0.0, "premium_pct": 1.0}]), "cover exactly"),
    (lambda d: d["risk_premium_pct_by_band"].pop("borderline_approved_by_human"), "cover exactly"),
    (lambda d: d["risk_premium_pct_by_band"]["pass"].reverse(), "descending"),
    (lambda d: d.pop("benchmark_rate_pct"), "benchmark_rate_pct"),
])
def test_bad_pricing_is_refused(tmp_path, mutate, match):
    with pytest.raises(ConfigError, match=match):
        pricing_variant(tmp_path, mutate)
