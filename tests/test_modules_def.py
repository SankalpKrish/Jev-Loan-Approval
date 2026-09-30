"""Question packs D, E and F (PLAN 3.8): registry metadata, which questions apply where, rubric shape, the PII gate,
the grid row in E_memo_matches_grid, and a round trip through the gateway with the simulator backend."""

import copy
import re

import pytest

from jevloan.audit.log import AuditLog
from jevloan.config import RuntimeConfig
from jevloan.data.generator import generate_book
from jevloan.data.schema import grid_row_for, load_disclosures, load_grid
from jevloan.jev import sim_rules
from jevloan.jev.gateway import JevGateway
from jevloan.jev.simulator import SimulatedJevTransport
from jevloan.modules import base
from jevloan.modules.base import ALL_SEGMENTS, questions_for, validate_wire
from jevloan.pii.gate import PIIGate

base.load_all()

HOME = frozenset({"secured_home"})
MSME = frozenset({"msme_business"})
DISCLOSURE_IDS = [d["id"] for d in load_disclosures()]
COVENANT_IDS = ["dscr_min_1_25", "stock_statement_monthly", "no_unapproved_borrowing", "insurance_current"]
EWS_IDS = ["F_ews_dpd_rising", "F_ews_emi_bounces", "F_ews_partial_payments", "F_ews_balance_stress"]
E_AGGREGATE = ["E_memo_matches_grid", "E_rate_math_correct", "E_disclosures_complete"]
D_DOUBT = [
    "D_doubt_income_documentation",
    "D_doubt_repayment_history",
    "D_doubt_debt_burden",
    "D_doubt_stability",
    "D_doubt_collateral",
    "D_doubt_thin_file",
]

# qid -> (module, stage, qtype, segments, risk_polarity, routing_relevant): the PLAN 3.8 table plus the brief
EXPECTED: dict[str, tuple] = {
    "D_closeness": ("D", "appraisal", "score", ALL_SEGMENTS, "ordinal_high_is_good", False),
    **{q: ("D", "appraisal", "noul", ALL_SEGMENTS, "yes_is_bad", False) for q in D_DOUBT if q != "D_doubt_collateral"},
    "D_doubt_collateral": ("D", "appraisal", "noul", HOME, "yes_is_bad", False),
    **{q: ("E", "sanction_docs", "noul", ALL_SEGMENTS, "yes_is_good", True) for q in E_AGGREGATE},
    **{f"E_disclosure_{d}": ("E", "sanction_docs", "noul", ALL_SEGMENTS, "yes_is_good", False) for d in DISCLOSURE_IDS},
    **{q: ("F", "monitoring", "noul", ALL_SEGMENTS, "yes_is_bad", True) for q in EWS_IDS},
    **{f"F_covenant_{c}": ("F", "monitoring", "noul", MSME, "yes_is_good", True) for c in COVENANT_IDS},
}


def _def_registry() -> dict[str, base.QuestionDef]:
    return {qid: q for qid, q in base.REGISTRY.items() if q.module in "DEF"}


# --- states ---------------------------------------------------------------------------------------------------


def sanction_state(product="personal_loan_unsecured", principal=1_500_000, *, disclosures=None) -> dict:
    """A hand-built, clean sanction_docs state (PLAN 3.7) for the real grid row of `product` at `principal`."""
    row = grid_row_for(product, principal)
    listed = disclosures if disclosures is not None else load_disclosures()
    conditions = [c["text"] for c in row["required_conditions"]]
    sections = " ".join(f"{i}. {d['heading']}: stated." for i, d in enumerate(listed, start=1))
    grid = {k: row[k] for k in ("product", "band", "max_tenure_months")}
    grid["required_conditions"] = [{"id": c["id"], "text": c["text"]} for c in row["required_conditions"]]
    if row["max_ltv_pct"] is not None:
        grid["max_ltv_pct"] = row["max_ltv_pct"]
    return {
        "schema": "jevloan.state.v1",
        "stage": "sanction_docs",
        "segment": "salaried_personal",
        "sanction_memo": {
            "text": "Sanction memo (proposed). Applicant [APPLICANT]. Conditions: "
            + " ".join(f"{i}. {c}." for i, c in enumerate(conditions, start=1)),
            "product": product,
            "ticket_band": row["band"],
            "tenure_months": row["max_tenure_months"] - 12,
            "conditions": conditions,
        },
        "kfs": {
            "text": f"Key Fact Statement. Borrower [APPLICANT]. {sections}",
            "apr_stated_pct": 11.42,
            "apr_recomputed_pct": 11.43,
            "rate_pct": 10.5,
            "tenure_months": row["max_tenure_months"] - 12,
        },
        "policy_grid_row": grid,
        "required_disclosures": [{"id": d["id"], "heading": d["heading"]} for d in listed],
    }


def monitoring_state(covenants=None) -> dict:
    months = [
        {"m": m, "dpd_days": 0, "dpd_band": "0", "emi_bounced": False, "partial_payment": False, "avg_balance_band": "50-75k"}
        for m in range(1, 7)
    ]
    return {
        "schema": "jevloan.state.v1",
        "stage": "monitoring",
        "segment": "msme_business" if covenants else "salaried_personal",
        "loan": {
            "product": "msme_term_loan", "loan_amount_band": "10-25L", "tenure_months": 60, "months_since_disbursal": 6,
            "balance_change_band": "flat",
        },
        "repayment": months,
        "covenants": covenants or [],
    }


COVENANT_ENTRIES = [
    {"covenant_id": "dscr_min_1_25", "required": 1.25, "reported_value_band": "1.25-1.5",
     "evidence_text": "Compliance certificate: DSCR for the last audited year is 1.31 against the required minimum 1.25."},
    {"covenant_id": "stock_statement_monthly", "required": 6, "reported_value_band": 6,
     "evidence_text": "Stock statements were received on time for 6 of the last 6 months."},
    {"covenant_id": "no_unapproved_borrowing", "required": 0, "reported_value_band": 0,
     "evidence_text": "Bureau and auditor check found no new borrowing beyond the sanctioned facilities."},
    {"covenant_id": "insurance_current", "required": True, "reported_value_band": True,
     "evidence_text": "Insurance on hypothecated stock is current until Feb 2027."},
]


@pytest.fixture(scope="module")
def book():
    return generate_book(40, 7)  # 40 files cover all four segments


@pytest.fixture(scope="module")
def states(book):
    build_state = pytest.importorskip("jevloan.state").build_state
    return {(f.file_id, stage): build_state(f, stage) for f in book for stage in ("appraisal", "sanction_docs", "monitoring")}


# --- the registry ---------------------------------------------------------------------------------------------


def test_expected_table_has_eight_disclosures_and_four_covenants():
    assert len(DISCLOSURE_IDS) == 8
    assert sum(q.startswith("E_disclosure_") for q in EXPECTED) == 8
    assert sum(q.startswith("F_covenant_") for q in EXPECTED) == 4


def test_the_pack_registers_exactly_the_d_e_f_questions_of_the_catalogue():
    assert set(_def_registry()) == set(EXPECTED)


@pytest.mark.parametrize("qid", sorted(EXPECTED))
def test_question_metadata(qid):
    module, stage, qtype, segments, polarity, routing = EXPECTED[qid]
    q = base.REGISTRY[qid]
    assert (q.module, q.stage, q.qtype, q.segments, q.risk_polarity, q.routing_relevant) == (
        module, stage, qtype, segments, polarity, routing,
    )


def test_every_disclosure_in_the_policy_file_has_a_question():
    for d in load_disclosures():
        q = base.REGISTRY[f"E_disclosure_{d['id']}"]
        assert d["heading"] in q.reason_text


@pytest.mark.parametrize("qid", sorted(EXPECTED))
def test_reason_text_reads_as_a_problem_statement_for_a_human(qid):
    reason = base.REGISTRY[qid].reason_text
    assert len(reason) >= 25 and reason == reason.strip()
    assert "_" not in reason, "a reviewer should not see raw ids"
    assert not reason.startswith(qid)


def test_grievance_reason_names_the_disclosure():
    assert base.REGISTRY["E_disclosure_grievance_redressal_officer"].reason_text == (
        'KFS is missing the "Grievance redressal officer" disclosure'
    )


# --- which questions apply where ------------------------------------------------------------------------------


@pytest.mark.parametrize("segment", sorted(ALL_SEGMENTS))
def test_appraisal_d_questions_by_segment(segment, book, states):
    file = next(f for f in book if f.segment == segment)
    asked = {q for q in questions_for("appraisal", segment, states[(file.file_id, "appraisal")]) if q.startswith("D_")}
    expected = {"D_closeness", *D_DOUBT}
    if segment != "secured_home":
        expected.discard("D_doubt_collateral")
    assert asked == expected


@pytest.mark.parametrize("segment", sorted(ALL_SEGMENTS))
def test_sanction_docs_asks_exactly_the_e_questions(segment):
    state = sanction_state()
    asked = questions_for("sanction_docs", segment, state)
    assert set(asked) == set(E_AGGREGATE) | {f"E_disclosure_{d}" for d in DISCLOSURE_IDS}
    assert all(q.startswith("E_") for q in asked)


@pytest.mark.parametrize("segment", ["salaried_personal", "self_employed", "secured_home"])
def test_monitoring_for_non_msme_asks_only_the_four_early_warnings(segment):
    assert set(questions_for("monitoring", segment, monitoring_state())) == set(EWS_IDS)


def test_monitoring_for_msme_adds_one_question_per_covenant():
    asked = set(questions_for("monitoring", "msme_business", monitoring_state(COVENANT_ENTRIES)))
    assert asked == set(EWS_IDS) | {f"F_covenant_{c}" for c in COVENANT_IDS}


def test_covenant_questions_follow_the_covenants_present_in_the_state():
    partial = [c for c in COVENANT_ENTRIES if c["covenant_id"] in ("dscr_min_1_25", "insurance_current")]
    asked = {q for q in questions_for("monitoring", "msme_business", monitoring_state(partial)) if q.startswith("F_covenant_")}
    assert asked == {"F_covenant_dscr_min_1_25", "F_covenant_insurance_current"}
    assert not [q for q in questions_for("monitoring", "msme_business", monitoring_state()) if q.startswith("F_covenant_")]


@pytest.mark.parametrize("segment", ["salaried_personal", "self_employed", "secured_home"])
def test_covenant_questions_are_msme_only_even_if_the_state_carries_covenants(segment):
    asked = questions_for("monitoring", segment, monitoring_state(COVENANT_ENTRIES))
    assert not [q for q in asked if q.startswith("F_covenant_")]


def test_real_states_ask_the_right_sets(book, states):
    for f in book:
        monitoring = questions_for("monitoring", f.segment, states[(f.file_id, "monitoring")])
        covenants = {q for q in monitoring if q.startswith("F_covenant_")}
        assert covenants == ({f"F_covenant_{c}" for c in COVENANT_IDS} if f.segment == "msme_business" else set())
        assert set(EWS_IDS) <= set(monitoring)
        sanction = questions_for("sanction_docs", f.segment, states[(f.file_id, "sanction_docs")])
        assert len(sanction) == 3 + len(DISCLOSURE_IDS)


def test_a_disclosure_question_only_applies_if_the_state_lists_it():
    listed = [d for d in load_disclosures() if d["id"] != "penal_charges"]
    asked = questions_for("sanction_docs", "salaried_personal", sanction_state(disclosures=listed))
    assert "E_disclosure_penal_charges" not in asked
    assert len([q for q in asked if q.startswith("E_disclosure_")]) == len(DISCLOSURE_IDS) - 1


def test_a_disclosure_question_uses_the_heading_the_state_carries():
    state = sanction_state()
    state["required_disclosures"][0]["heading"] = "Effective annual rate"
    wire = questions_for("sanction_docs", "salaried_personal", state)["E_disclosure_apr"]
    assert wire["instructions"]["data"] == {"section_heading": "Effective annual rate"}
    assert '"Effective annual rate"' in wire["instructions"]["question"]


def test_a_state_without_the_disclosure_list_asks_for_every_disclosure_in_the_policy_file():
    state = sanction_state()
    del state["required_disclosures"]
    asked = questions_for("sanction_docs", "salaried_personal", state)
    assert {q for q in asked if q.startswith("E_disclosure_")} == {f"E_disclosure_{d}" for d in DISCLOSURE_IDS}


# --- rubric shape and the PII gate ----------------------------------------------------------------------------


def all_def_wires(book, states) -> dict[str, dict]:
    """Every D, E and F wire that any real state of the sample asks for (keyed by qid, the last one wins) plus
    the E wires for every row of the sanction grid."""
    wires: dict[str, dict] = {}
    for f in book:
        for stage in ("appraisal", "sanction_docs", "monitoring"):
            asked = questions_for(stage, f.segment, states[(f.file_id, stage)])
            wires.update({q: w for q, w in asked.items() if q[0] in "DEF"})
    return wires


def test_every_wire_passes_the_rubric_shape_rules_and_the_pii_gate(book, states):
    gate = PIIGate()
    wires = all_def_wires(book, states)
    assert set(wires) == set(EXPECTED)  # the sample exercises every question
    for qid, wire in wires.items():
        assert validate_wire(wire) == [], qid
        assert gate.scan(wire) == [], qid
        gate.check(wire)


def test_the_wire_for_every_grid_row_passes_the_pii_gate():
    gate = PIIGate()
    for row in load_grid()["rows"]:
        principal = row["max_amount_inr"] or row["min_amount_inr"] * 2
        state = sanction_state(row["product"], principal)
        wire = questions_for("sanction_docs", "salaried_personal", state)["E_memo_matches_grid"]
        assert validate_wire(wire) == []
        assert gate.scan(wire) == [], row["product"] + row["band"]


NEGATIVE_WORDS = re.compile(r"\b(lack|not|without|never|unless|neither|nor|except)\b|n't", re.IGNORECASE)


@pytest.mark.parametrize("qid", sorted(EXPECTED))
def test_rubric_is_literal(qid, book, states):
    wire = all_def_wires(book, states)[qid]
    instructions = wire["instructions"]
    question = instructions["question"]
    # points at state fields by name, in backticks
    assert "`" in question or "`" in instructions.get("focus", ""), "question names no state field"
    assert instructions["refer_to"]
    assert all(ref.startswith("`") and ref.endswith("`") for ref in instructions["refer_to"])
    # no negations in the question itself, so no double negatives can hide there
    assert not NEGATIVE_WORDS.search(question), question
    if wire["type"] == "noul":
        true_side, false_side = wire["criteria"]["true"], wire["criteria"]["false"]
        assert not true_side["what"].lower().startswith(("no ", "not ")), "the true side must be a plain yes"
        assert "not_for" in false_side and false_side["not_for"].strip()
        for side in (true_side, false_side):
            assert 2 <= len(side["examples"]) <= 3
    else:
        levels = wire["criteria"]
        assert len(levels) == 5
        assert len({level["level"] for level in levels}) == 5
        for level in levels:
            assert level["what"].strip() and level["not_for"].strip() and 2 <= len(level["examples"]) <= 3


def test_no_rubric_contains_a_realistic_identifier_or_a_currency_figure(book, states):
    figure = re.compile(r"(?:Rs\.?|INR|₹)\s*\d|\b\d{6,}\b")
    for qid, wire in all_def_wires(book, states).items():
        text = str(wire)
        assert not figure.search(text), qid


# --- E_memo_matches_grid --------------------------------------------------------------------------------------


@pytest.mark.parametrize("product,principal", [("personal_loan_unsecured", 3_000_000), ("msme_term_loan", 30_000_000), ("home_loan", 9_000_000)])
def test_memo_question_carries_the_applicable_grid_row_as_structured_data(product, principal):
    state = sanction_state(product, principal)
    wire = questions_for("sanction_docs", "salaried_personal", state)["E_memo_matches_grid"]
    data = wire["instructions"]["data"]
    assert data["grid_row"] == state["policy_grid_row"]
    assert data["grid_row"]["max_tenure_months"] == grid_row_for(product, principal)["max_tenure_months"]
    assert [c["id"] for c in data["grid_row"]["required_conditions"]] == [
        c["id"] for c in grid_row_for(product, principal)["required_conditions"]
    ]
    assert data["memo_tenure_months"] == state["sanction_memo"]["tenure_months"]


def test_memo_question_data_is_a_copy_not_the_state():
    state = sanction_state()
    before = copy.deepcopy(state)
    wire = questions_for("sanction_docs", "salaried_personal", state)["E_memo_matches_grid"]
    wire["instructions"]["data"]["grid_row"]["max_tenure_months"] = 1
    wire["instructions"]["data"]["grid_row"]["required_conditions"].clear()
    assert state == before


def test_memo_question_needs_a_grid_row_in_the_state():
    state = sanction_state()
    del state["policy_grid_row"]
    with pytest.raises(ValueError, match="policy_grid_row"):
        questions_for("sanction_docs", "salaried_personal", state)


def test_the_memo_question_words_the_comparison_literally():
    wire = questions_for("sanction_docs", "salaried_personal", sanction_state())["E_memo_matches_grid"]
    question = wire["instructions"]["question"]
    for path in ("`data.memo_tenure_months`", "`data.grid_row.max_tenure_months`", "`data.grid_row.required_conditions`", "`sanction_memo.conditions`"):
        assert path in question


def test_rate_math_question_compares_two_decimals_side_by_side_and_the_memo_rate():
    wire = questions_for("sanction_docs", "salaried_personal", sanction_state())["E_rate_math_correct"]
    question = wire["instructions"]["question"]
    for path in ("`kfs.apr_stated_pct`", "`kfs.apr_recomputed_pct`", "`kfs.rate_pct`", "`sanction_memo.text`"):
        assert path in question
    assert "11.42" in str(wire["criteria"]["true"]["examples"])  # two decimals, side by side, in the examples


def test_each_disclosure_question_names_its_heading_and_only_its_heading():
    wires = questions_for("sanction_docs", "salaried_personal", sanction_state())
    for d in load_disclosures():
        wire = wires[f"E_disclosure_{d['id']}"]
        assert d["heading"] in wire["instructions"]["question"]
        assert wire["instructions"]["data"] == {"section_heading": d["heading"]}
        others = [o["heading"] for o in load_disclosures() if o["id"] != d["id"]]
        text = str(wire)
        assert not [h for h in others if h in text and h not in d["heading"] and d["heading"] not in h]


def test_covenant_questions_say_which_covenant_they_are_about():
    wires = questions_for("monitoring", "msme_business", monitoring_state(COVENANT_ENTRIES))
    for cid in COVENANT_IDS:
        wire = wires[f"F_covenant_{cid}"]
        assert wire["instructions"]["data"] == {"covenant_id": cid}
        assert f"`{cid}`" in wire["instructions"]["question"]
        assert "complied with" in wire["instructions"]["question"]  # the yes side is compliance


def test_early_warning_questions_point_at_one_field_each():
    wires = questions_for("monitoring", "salaried_personal", monitoring_state())
    assert "`repayment[*].dpd_days`" in wires["F_ews_dpd_rising"]["instructions"]["question"]
    assert "`loan.balance_change_band`" in wires["F_ews_balance_stress"]["instructions"]["question"]
    assert "`falling_40_plus`" in wires["F_ews_balance_stress"]["instructions"]["question"]
    for qid in EWS_IDS:
        assert wires[qid]["type"] == "noul"
        assert "`repayment`" in wires[qid]["instructions"]["refer_to"]
    assert wires["F_ews_balance_stress"]["instructions"]["refer_to"][0] == "`loan.balance_change_band`"


def test_the_dpd_question_states_the_truth_definition_on_dpd_days():
    wire = questions_for("monitoring", "salaried_personal", monitoring_state())["F_ews_dpd_rising"]
    question = wire["instructions"]["question"]
    assert "30 or more" in question and "at least three of the last four" in question
    focus = wire["instructions"]["focus"]
    assert "strictly higher" in focus and "equal" in focus  # an equal number is not a rise
    examples = " ".join(wire["criteria"]["true"]["examples"] + wire["criteria"]["false"]["examples"])
    assert "0, 0, 4, 11, 18, 25" in examples  # rising through the last four entries without reaching 30
    assert "0, 3, 6, 6, 6, 9" in examples  # two rises in the last four entries is not enough


def test_the_balance_question_says_only_the_worst_band_is_yes():
    wire = questions_for("monitoring", "salaried_personal", monitoring_state())["F_ews_balance_stress"]
    assert wire["criteria"]["true"]["what"] == "`loan.balance_change_band` is `falling_40_plus`."
    false_side = wire["criteria"]["false"]
    for band in ("`rising`", "`flat`", "`falling_10_40`"):
        assert band in false_side["what"]


# --- round trip through the gateway with the simulator backend ------------------------------------------------


@pytest.fixture
async def gateway(tmp_db):
    sim_rules.load_all()
    audit = AuditLog(tmp_db)
    transport = SimulatedJevTransport(rules=sim_rules.REGISTRY, latency_ms=(0, 0))
    gw = JevGateway(RuntimeConfig(backend="sim", rate_limit_rpm=600_000), audit, PIIGate(), transport)
    yield gw
    await gw.aclose()


async def test_the_simulator_accepts_every_d_e_f_wire_end_to_end(gateway, book, states):
    """State and questions go through the real PII gate and the egress guard, the SDK, and the simulator."""
    seen: set[str] = set()
    for segment in sorted(ALL_SEGMENTS):
        file = next(f for f in book if f.segment == segment)
        for stage in ("appraisal", "sanction_docs", "monitoring"):
            state = states[(file.file_id, stage)]
            questions = questions_for(stage, segment, state)
            result = await gateway.ask(
                file_id=file.file_id, segment=segment, stage=stage, policy_version="policy-test", state=state, questions=questions
            )
            assert result.ok, (segment, stage, result.failure, result.failure_detail)
            assert set(result.answers) == set(questions)
            for qid, question in questions.items():
                if qid[0] not in "DEF":
                    continue
                seen.add(qid)
                answer = result.answers[qid]
                assert answer["type"] == question["type"]
                if answer["type"] == "noul":
                    assert 0.0 <= answer["noul"] <= 1.0 and 0.0 <= answer["derived_confidence"] <= 1.0
                else:
                    assert len(answer["probabilities"]) == len(question["criteria"]) == 5
                    assert abs(sum(answer["probabilities"].values()) - 1.0) < 1e-3
                    assert 0.0 <= answer["confidence"] <= 1.0 and 0.0 <= answer["score"] <= 4.0
    assert seen == set(EXPECTED)


async def test_the_gateway_blocks_a_wire_that_carries_pii(gateway, book, states):
    """The gate really is in the path: the same call with an identifier in a rubric is refused, nothing sent."""
    file = book[0]
    state = states[(file.file_id, "sanction_docs")]
    questions = questions_for("sanction_docs", file.segment, state)
    questions["E_memo_matches_grid"]["instructions"]["focus"] += " Applicant PAN is ABCDE1234F."
    result = await gateway.ask(
        file_id=file.file_id, segment=file.segment, stage="sanction_docs", policy_version="policy-test", state=state, questions=questions
    )
    assert not result.ok and result.failure == "pii_blocked"
