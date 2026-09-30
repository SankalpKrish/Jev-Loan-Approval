"""The A, B and C question packs: registration, applicability, rubric shape, PII, and the simulator round trip."""

import re

import pytest

from abc_fixtures import book_rows, make_state
from jevloan.audit.log import AuditLog
from jevloan.cli import main
from jevloan.config import RuntimeConfig
from jevloan.jev import sim_rules
from jevloan.jev.gateway import JevGateway
from jevloan.jev.simulator import SimulatedJevTransport
from jevloan.modules import base, commands
from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, questions_for, register, validate_wire
from jevloan.pii.gate import PIIGate

ALL = ALL_SEGMENTS
SALARIED_HOME = {"salaried_personal", "secured_home"}
BUSINESS = {"self_employed", "msme_business"}

# qid -> (type, polarity, segments), PLAN 3.8
EXPECTED = {
    "A_income_proof_current": ("noul", "yes_is_good", ALL),
    "A_address_proof_valid": ("noul", "yes_is_good", ALL),
    "A_statements_cover_months": ("noul", "yes_is_good", ALL),
    "A_fields_cohere": ("noul", "yes_is_good", ALL),
    "B_identity_coheres": ("noul", "yes_is_good", ALL),
    "B_salary_matches_employer": ("noul", "yes_is_good", SALARIED_HOME),
    "B_gst_bank_consistent": ("noul", "yes_is_good", BUSINESS),
    "B_synthetic_identity_signals": ("noul", "yes_is_bad", ALL),
    "C_willingness": ("score", "ordinal_high_is_good", ALL),
    "C_recent_delinquency": ("noul", "yes_is_bad", ALL),
    "C_capacity": ("score", "ordinal_high_is_good", ALL),
    "C_foir_within_limit": ("noul", "yes_is_good", ALL),
    "C_income_stable": ("noul", "yes_is_good", ALL),
    "C_collateral_adequacy": ("score", "ordinal_high_is_good", {"secured_home"}),
    "C_collateral_title_clear": ("noul", "yes_is_good", {"secured_home"}),
}
FIVE_LEVEL_SCORES = ("C_willingness", "C_capacity", "C_collateral_adequacy")


def abc_defs() -> dict[str, QuestionDef]:
    return {qid: q for qid, q in base.catalog().items() if q.module in "ABC"}


def abc_ids(wires: dict) -> set[str]:
    return {qid for qid in wires if qid[0] in "ABC"}


# --- registration and metadata -----------------------------------------------------------------------------


def test_the_abc_qids_are_exactly_the_catalogue():
    assert set(abc_defs()) == set(EXPECTED)
    assert len(EXPECTED) == 15


@pytest.mark.parametrize("qid", sorted(EXPECTED))
def test_metadata(qid):
    q = abc_defs()[qid]
    qtype, polarity, segments = EXPECTED[qid]
    assert (q.qtype, q.risk_polarity, set(q.segments)) == (qtype, polarity, segments)
    assert q.module == qid[0] and q.stage == "appraisal"
    assert q.routing_relevant is True
    assert q.reason_text.strip() and len(q.reason_text) < 200
    assert q.wire is not None and q.build is None


def test_only_the_gst_and_salary_questions_have_state_tests():
    with_test = {qid for qid, q in abc_defs().items() if q.applies is not None}
    assert with_test == {"B_salary_matches_employer", "B_gst_bank_consistent"}


# --- applicability -----------------------------------------------------------------------------------------

A_IDS = {"A_income_proof_current", "A_address_proof_valid", "A_statements_cover_months", "A_fields_cohere"}
EVERYONE = A_IDS | {
    "B_identity_coheres",
    "B_synthetic_identity_signals",
    "C_willingness",
    "C_recent_delinquency",
    "C_capacity",
    "C_foir_within_limit",
    "C_income_stable",
}


def test_salaried_personal_loan():
    assert abc_ids(questions_for("appraisal", "salaried_personal", make_state("salaried_personal"))) == EVERYONE | {"B_salary_matches_employer"}


def test_self_employed_with_and_without_gst():
    with_gst = make_state("self_employed")
    no_gst = make_state("self_employed")
    del no_gst["gst"]
    assert abc_ids(questions_for("appraisal", "self_employed", with_gst)) == EVERYONE | {"B_gst_bank_consistent"}
    assert abc_ids(questions_for("appraisal", "self_employed", no_gst)) == EVERYONE


def test_msme_always_asks_the_gst_question_and_never_the_salary_one():
    ids = abc_ids(questions_for("appraisal", "msme_business", make_state("msme_business")))
    assert ids == EVERYONE | {"B_gst_bank_consistent"}


def test_home_loan_asks_salary_only_for_a_salaried_applicant():
    secured = {"C_collateral_adequacy", "C_collateral_title_clear"}
    salaried = make_state("secured_home")
    self_employed = make_state("secured_home", {"application.employment_type": "self_employed"})
    assert abc_ids(questions_for("appraisal", "secured_home", salaried)) == EVERYONE | secured | {"B_salary_matches_employer"}
    assert abc_ids(questions_for("appraisal", "secured_home", self_employed)) == EVERYONE | secured


def test_collateral_questions_are_for_home_loans_only():
    for segment in ("salaried_personal", "self_employed", "msme_business"):
        ids = abc_ids(questions_for("appraisal", segment, make_state(segment)))
        assert not ids & {"C_collateral_adequacy", "C_collateral_title_clear"}


def test_abc_questions_belong_to_the_appraisal_stage_only():
    assert {q.stage for q in abc_defs().values()} == {"appraisal"}


def test_applicability_on_real_states():
    """The same rules on states from the real builder: every file of the book slice, by segment and applicant."""
    combos = set()
    for row in book_rows():
        wires = questions_for("appraisal", row["segment"], row["state"])
        expected = set(EVERYONE)
        if row["segment"] == "secured_home":
            expected |= {"C_collateral_adequacy", "C_collateral_title_clear"}
        if row["segment"] == "salaried_personal" or (row["segment"] == "secured_home" and row["employment_type"] == "salaried"):
            expected.add("B_salary_matches_employer")
        if row["segment"] == "msme_business" or (row["segment"] == "self_employed" and row["has_gst"]):
            expected.add("B_gst_bank_consistent")
        assert abc_ids(wires) == expected, row["file_id"]
        # the generator's truth covers exactly the questions that are asked
        assert expected == {qid for qid in row["truth"] if qid[0] in "ABC"}, row["file_id"]
        combos.add((row["segment"], row["employment_type"], row["has_gst"]))
    assert ("secured_home", "salaried", False) in combos and ("secured_home", "self_employed", False) in combos
    assert ("self_employed", "self_employed", True) in combos and ("self_employed", "self_employed", False) in combos


# --- PII gate ----------------------------------------------------------------------------------------------


def test_every_abc_wire_passes_the_pii_gate():
    gate = PIIGate()
    for qid, q in abc_defs().items():
        gate.check(q.to_wire({}))
        gate.check(q.reason_text)


def test_the_wires_built_for_real_files_pass_the_gate_together_with_their_state():
    gate = PIIGate()
    for row in book_rows()[:40]:
        gate.check({"state": row["state"], "questions": questions_for("appraisal", row["segment"], row["state"])})


def test_no_rubric_carries_a_name_or_an_identifier_shaped_value():
    """Beyond the gate: only redaction-style tokens, no long digit runs, no rupee amounts."""
    for qid, q in abc_defs().items():
        text = str(q.to_wire({}))
        assert not re.search(r"\d{6,}", text), qid
        assert not re.search(r"(₹|Rs\.?|INR)\s*\d", text), qid
        for token in re.findall(r"\[[A-Za-z0-9_]+\]", text):
            if token == "[DOB_n]":  # the pattern of the birth tokens, as PLAN 3.7 writes it, not a value
                continue
            assert re.fullmatch(r"\[[A-Z]+(_[A-Z0-9]+)?\]", token), (qid, token)


# --- rubric shape ------------------------------------------------------------------------------------------

NEGATION = re.compile(r"\b(not|no|never|without|neither|nor|none)\b|n't", re.IGNORECASE)


@pytest.mark.parametrize("qid", sorted(EXPECTED))
def test_rubric_shape(qid):
    q = abc_defs()[qid]
    wire = q.to_wire({})
    assert validate_wire(wire) == []
    instructions = wire["instructions"]
    assert instructions["question"].endswith("?")
    assert not NEGATION.search(instructions["question"]), "no negations in the question itself"
    assert instructions["refer_to"] and instructions.get("focus")
    assert re.search(r"`[a-z_]+(\.[a-z_0-9]+)*`", instructions["question"] + instructions["focus"]), "fields are named in backticks"
    if q.qtype == "noul":
        criteria = wire["criteria"]
        assert set(criteria) == {"true", "false"}
        assert criteria["false"].get("not_for"), "the false side names its near misses"
        for side in criteria.values():
            assert side["what"].strip() and 2 <= len(side["examples"]) <= 3
    else:
        levels = wire["criteria"]
        assert len(levels) == (5 if qid in FIVE_LEVEL_SCORES else len(levels))
        names = [level["level"] for level in levels]
        assert len(set(names)) == len(names)
        for level in levels:
            assert level["what"].strip() and level.get("not_for") and 2 <= len(level["examples"]) <= 3


def test_the_three_scores_have_five_levels_worst_to_best():
    expected = {
        "C_willingness": ["severe", "serious", "weak", "minor_slip", "spotless"],
        "C_capacity": ["over_stretched", "over_limit", "at_limit", "comfortable", "strong"],
        "C_collateral_adequacy": ["inadequate", "weak", "marginal", "adequate", "strong"],
    }
    for qid, names in expected.items():
        assert [level["level"] for level in abc_defs()[qid].to_wire({})["criteria"]] == names


def test_the_rubrics_use_the_bands_of_the_state_builder():
    from jevloan.modules import a_readiness, c_appraisal
    from jevloan.state.base import VOCABULARY

    assert c_appraisal.FOIR_HEADROOM_BAND_ORDER == list(VOCABULARY["foir_headroom_pts"])
    assert c_appraisal.LTV_HEADROOM_BAND_ORDER == list(VOCABULARY["ltv_headroom_pts"])
    assert c_appraisal.DSCR_BAND_ORDER == list(VOCABULARY["dscr"])
    assert c_appraisal.VALUATION_SPREAD_BAND_ORDER == list(VOCABULARY["valuation_spread"])
    assert a_readiness.MONTHLY_INCOME_BAND_ORDER == list(VOCABULARY["monthly"])
    assert list(c_appraisal.CAPACITY_LEVEL_BY_FOIR_HEADROOM_BAND) == list(VOCABULARY["foir_headroom_pts"])
    assert list(c_appraisal.CAPACITY_LEVEL_BY_DSCR_BAND) == list(VOCABULARY["dscr"])
    assert list(c_appraisal.COLLATERAL_BASE_BY_LTV_HEADROOM_BAND) == list(VOCABULARY["ltv_headroom_pts"])
    # worst to best: the levels rise with the headroom
    for table in (c_appraisal.CAPACITY_LEVEL_BY_FOIR_HEADROOM_BAND, c_appraisal.CAPACITY_LEVEL_BY_DSCR_BAND, c_appraisal.COLLATERAL_BASE_BY_LTV_HEADROOM_BAND):
        assert list(table.values()) == [0, 1, 2, 3, 4]


def test_the_state_builder_edges_match_the_truths_the_rubrics_rely_on():
    from jevloan.state.base import BAND_TABLES

    assert tuple(BAND_TABLES["volatility"].edges) == (0.25, 0.35)  # low is stable (cv < 0.25); high is the capacity deduction
    assert tuple(BAND_TABLES["ltv_headroom_pts"].edges) == (-5, 0, 8, 15)  # the collateral truth's own thresholds


def _path_present(state: dict, path: str) -> bool:
    node = state
    for part in path.strip("`").split("."):
        if isinstance(node, dict) and node.get(part) is not None:
            node = node[part]
        else:
            return False
    return True


def test_every_field_a_question_refers_to_exists_in_the_real_state():
    """The pack was written against PLAN 3.7 by name; this holds it to the builder's actual output."""
    # a path that only some segments have: (path, segments where it may be absent)
    optional = {
        "`business.dscr_band`": {"salaried_personal", "secured_home"},  # only the business segments carry it
        "`obligations.foir_headroom_pts_band`": {"msme_business"},  # an MSME file is judged on DSCR instead
    }
    checked = set()
    for row in book_rows():
        for qid, wire in questions_for("appraisal", row["segment"], row["state"]).items():
            if qid[0] not in "ABC":
                continue
            for path in wire["instructions"]["refer_to"]:
                if row["segment"] in optional.get(path, ()) and not _path_present(row["state"], path):
                    continue
                assert _path_present(row["state"], path), (row["file_id"], qid, path)
                checked.add((qid, path))
    assert len(checked) > 40


# --- the simulator round trip ------------------------------------------------------------------------------


@pytest.fixture
async def gateway(tmp_db):
    sim_rules.load_all()
    transport = SimulatedJevTransport(rules=sim_rules.REGISTRY, latency_ms=(0, 0))
    gw = JevGateway(RuntimeConfig(backend="sim", rate_limit_rpm=600_000), AuditLog(tmp_db), PIIGate(), transport)
    yield gw
    await gw.aclose()


def one_row_per_kind() -> list[dict]:
    seen, rows = set(), []
    for row in book_rows():
        kind = (row["segment"], row["employment_type"], row["has_gst"])
        if kind not in seen:
            seen.add(kind)
            rows.append(row)
    return rows


async def test_the_simulator_accepts_and_answers_every_appraisal_question(gateway):
    for row in one_row_per_kind():
        questions = questions_for("appraisal", row["segment"], row["state"])
        result = await gateway.ask(
            file_id=row["file_id"], segment=row["segment"], stage="appraisal", policy_version="test", state=row["state"], questions=questions
        )
        assert result.ok, (row["segment"], result.failure, result.failure_detail)
        assert set(result.answers) == set(questions)
        for qid, answer in result.answers.items():
            assert answer["type"] == questions[qid]["type"]
            if answer["type"] == "noul":
                assert 0.0 <= answer["noul"] <= 1.0 and 0.0 <= answer["derived_confidence"] <= 1.0
            else:
                assert len(answer["probabilities"]) == len(questions[qid]["criteria"])
                assert abs(sum(answer["probabilities"].values()) - 1) < 0.01
                assert 0.0 <= answer["score"] <= len(questions[qid]["criteria"]) - 1


async def test_the_simulator_answers_the_hand_built_states_too(gateway):
    for segment in ALL_SEGMENTS:
        state = make_state(segment)
        questions = questions_for("appraisal", segment, state)
        result = await gateway.ask(file_id="T1", segment=segment, stage="appraisal", policy_version="test", state=state, questions=questions)
        assert result.ok and set(result.answers) == set(questions)


# --- the commands ------------------------------------------------------------------------------------------


@pytest.fixture
def small_sample(monkeypatch):
    monkeypatch.setattr(commands, "SAMPLE_BOOK_SIZE", 20)


def test_modules_list(capsys):
    assert main(["modules", "list"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split()[:5] == ["qid", "module", "stage", "type", "segments"]
    for qid in EXPECTED:
        assert qid in out
    row = next(line for line in lines if line.startswith("B_salary_matches_employer"))
    assert "salaried_personal,secured_home" in row
    assert "questions" in lines[-1]


def test_rubrics_export_writes_every_rubric_grouped_by_module(tmp_path, capsys, small_sample):
    out = tmp_path / "docs" / "RUBRICS.md"
    assert main(["modules", "rubrics-export", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Question rubrics")
    for module, title in (("A", "File readiness"), ("B", "Fraud screen"), ("C", "Appraisal support")):
        assert f"## Module {module}: {title}" in text
    assert text.index("## Module A") < text.index("## Module B") < text.index("## Module C")
    for qid in EXPECTED:
        assert f"### `{qid}`" in text
    assert "**Levels, worst to best.**" in text and "0. severe" in text and "4. spotless" in text
    assert "*Not for*" in text and "*Examples*" in text
    assert "Reason text" in text and str(len(base.catalog())) in text
    assert "wrote the rubrics" in capsys.readouterr().out


def test_check_passes_on_the_shipped_packs(capsys, small_sample):
    assert main(["modules", "check"]) == 0
    out = capsys.readouterr().out
    assert "0 problem(s)" in out and "FAIL" not in out


def test_check_fails_on_a_rubric_with_a_bad_shape_or_an_identifier(monkeypatch, capsys):
    base.load_all()
    monkeypatch.setattr(base, "REGISTRY", dict(base.REGISTRY))
    monkeypatch.setattr(commands, "sample_states", lambda: [])
    thin = base.noul_wire("Is it fine?", refer_to=["`a.b`"], true_what="w", true_examples=["1", "2"], false_what="w", false_examples=["1", "2"])
    thin["criteria"]["true"]["examples"] = ["only one"]
    leaky = base.noul_wire("Is it fine?", refer_to=["`a.b`"], true_what="w", true_examples=["1", "2"], false_what="w", false_examples=["1", "2"])
    leaky["criteria"]["true"]["examples"] = ["the PAN ABCDE1234F", "another"]
    for qid, wire in (("A_bad_shape", thin), ("A_leaky", leaky)):
        register(QuestionDef(qid, "A", "appraisal", "noul", frozenset({"salaried_personal"}), "yes_is_good", True, "reason", wire=wire))
    assert main(["modules", "check"]) == 1
    out = capsys.readouterr().out
    assert "FAIL A_bad_shape: rubric shape" in out and "FAIL A_leaky: PII gate blocked" in out
    assert "ABCDE1234F" not in out, "the check prints masked findings only"
