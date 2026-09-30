"""Deterministic synthetic loan-book generator (PLAN section 3.6).

``generate_book(n, seed)`` builds file ``i`` from its own ``random.Random(f"{seed}:{i}")``, so any file can be
regenerated on its own and a seed always gives the same book. The generative process, in the order it happens:

1. segment (fixed 20-file cycle: 30/20/25/25), demographics (gender, language, PIN cluster, city, name),
   disparity subset draws, employment type, fraud type;
2. a latent creditworthiness ``z ~ N(0,1)`` that drives bureau score, delinquency, bounces, volatility and
   card use, so the features correlate like real ones do;
3. income, then loan size from a target FOIR (MSME: target DSCR), tenure capped by the sanction grid and by
   retirement age; property, GST and identity blocks;
4. the plan of defects (missing items, memo defects) and their documents;
5. labels computed from the true features (``data/risk.py``), the 12-month outcome drawn from the risk function,
   six months of repayment consistent with that outcome, MSME covenants, and the truth of every question.

Disparity subsets are chosen by draws that never look at any feature or label, and their engineered attribute is
kept out of the label computation (see docs/DATA_CARD.md).
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator

from jevloan import finance
from jevloan.data import names as nm
from jevloan.data.documents import (
    EXTRA_CONDITIONS,
    DocPlan,
    PIIRecorder,
    Writer,
    add_months,
    build_documents,
    build_memo_and_kfs,
)
from jevloan.data.risk import (
    AS_OF,
    STATEMENTS_REQUIRED,
    closeness_level,
    draw_outcome,
    ews_truth,
    pd_for_file,
    primary_weakness,
    question_truth,
    synthetic_credit_decision,
)
from jevloan.data.schema import (
    PRODUCT_BY_SEGMENT,
    Address,
    Application,
    Applicant,
    Bank,
    Bureau,
    CoApplicant,
    Covenant,
    Demographics,
    Gst,
    Identity,
    Income,
    Kfs,
    Labels,
    LoanFile,
    Meta,
    Obligations,
    PIIInventory,
    PostDisbursal,
    Property,
    RepaymentMonth,
    SanctionMemo,
    disclosure_ids,
    grid_row_for,
)

GENERATOR_VERSION = "gen-1.0"

# 30% salaried, 20% self-employed, 25% MSME, 25% home, interleaved so any prefix of the book is representative
_S, _E, _M, _H = "salaried_personal", "self_employed", "msme_business", "secured_home"
SEGMENT_CYCLE = [_S, _H, _M, _E, _S, _M, _H, _S, _E, _M, _S, _H, _M, _E, _S, _H, _M, _S, _E, _H]

SEG = {
    _S: dict(age=(23, 57), fshare=0.32, income_med=52_000, income_sigma=0.55, income_clip=(16_000, 400_000), foir_mu=0.40, foir_sd=0.12, ntc_p=0.11),
    _E: dict(age=(27, 62), fshare=0.25, income_med=85_000, income_sigma=0.60, income_clip=(25_000, 900_000), foir_mu=0.37, foir_sd=0.12, ntc_p=0.07),
    _M: dict(age=(30, 64), fshare=0.12, income_med=450_000, income_sigma=0.75, income_clip=(120_000, 4_000_000), foir_mu=0.60, foir_sd=0.15, ntc_p=0.03),
    _H: dict(age=(26, 52), fshare=0.30, income_med=110_000, income_sigma=0.55, income_clip=(35_000, 900_000), foir_mu=0.42, foir_sd=0.11, ntc_p=0.04),
}
LANGS = ["en", "hi", "ta", "bn", "mr", "te"]
LANG_W = [0.44, 0.20, 0.10, 0.09, 0.09, 0.08]
CLUSTERS = [f"PC{k}" for k in range(1, 9)]
CLUSTER_W = [0.14, 0.16, 0.15, 0.14, 0.12, 0.11, 0.10, 0.08]
LANG_TAG_P = {"ta": 0.18, "bn": 0.18, "hi": 0.02, "mr": 0.05}  # lang_doc_script: share of these speakers
GENDER_TAG_P = 0.60  # share of self-employed women given informal_declared income documentation
PINCODE_TAG_P = 0.30  # share of PC7 files whose bureau score is reported as missing

FRAUD_P = {"identity_mismatch": 0.015, "synthetic_identity": 0.010}
FRAUD_SALARY_P = 0.0324  # among salaried applicants, so the whole book is about 1.5%
FRAUD_GST_P = 0.0405  # among GST-registered business files, so the whole book is about 1.5%

MISSING_P = {"income_proof": 0.048, "address_proof": 0.043, "statements": 0.034, "fields_coherence": 0.029}
MEMO_P = {"memo_condition_mismatch": 0.07, "apr_math_wrong": 0.07, "disclosure_missing": 0.085}

RATE_BASE = {_S: 11.25, _E: 12.75, _M: 10.25, _H: 8.65}
PURPOSES = {
    _S: ["medical", "wedding", "education", "travel", "home_renovation", "debt_consolidation", "consumer_durables"],
    _E: ["working_capital", "business_expansion", "equipment_purchase", "inventory"],
    _M: ["machinery_term_loan", "plant_expansion", "capex", "working_capital_term"],
    _H: ["home_purchase", "home_construction", "plot_purchase", "home_improvement"],
}
TENURES = {
    "personal_loan_unsecured": ([12, 18, 24, 36, 48, 60], [8, 8, 20, 30, 20, 14]),
    "business_loan_self_employed": ([24, 36, 48, 60, 72, 84], [10, 25, 25, 20, 10, 10]),
    "msme_term_loan": ([36, 48, 60, 72, 84, 96, 120], [12, 20, 25, 15, 12, 10, 6]),
    "home_loan": ([60, 84, 96, 120, 180, 240, 300, 360], [1, 1, 1, 8, 15, 30, 22, 25]),
}
AMOUNT_LIMITS = {
    "personal_loan_unsecured": (50_000, 4_000_000),
    "business_loan_self_employed": (100_000, 20_000_000),
    "msme_term_loan": (1_000_000, 50_000_000),
    "home_loan": (1_000_000, 50_000_000),
}


# ------------------------------------------------------------------------------------------------ small helpers


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _lognorm(rng: random.Random, median: float, sigma: float) -> float:
    return median * math.exp(rng.gauss(0.0, sigma))


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _pick(rng: random.Random, items: list, weights: list[float]):
    return rng.choices(items, weights=weights, k=1)[0]


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _round_to(x: float, step: int) -> int:
    return int(round(x / step) * step)


def _round_amount(product: str, x: float) -> int:
    if product == "personal_loan_unsecured":
        return _round_to(x, 10_000 if x < 1_000_000 else 25_000)
    if product == "business_loan_self_employed":
        return _round_to(x, 25_000 if x < 5_000_000 else 100_000)
    if product == "msme_term_loan":
        return _round_to(x, 100_000 if x < 20_000_000 else 500_000)
    return _round_to(x, 50_000 if x < 5_000_000 else 100_000)


def _months_to(age: int, dob_month: int, retire_at: int) -> int:
    return (retire_at - age) * 12 + (dob_month - AS_OF.month)


# ------------------------------------------------------------------------------------------------ loan sizing


def _size_loan(rng, seg, emp, income, rate, age, dob_month, z, hard_grid_cap=True):
    """Returns (income, principal, tenure, existing_emi, proposed_emi). The income may be raised so that a
    minimum-size loan does not produce an absurd FOIR."""
    product = PRODUCT_BY_SEGMENT[seg]
    P = SEG[seg]
    if seg == _M:
        dscr_t = _clip(rng.gauss(1.65 + 0.15 * z, 0.4), 0.85, 3.5)
        total = income / dscr_t
        existing = total * rng.betavariate(1.5, 2.5) * 0.8
    else:
        target = _clip(rng.gauss(P["foir_mu"] - 0.02 * z, P["foir_sd"]), 0.08, 0.95)
        total = income * target
        existing = total * rng.betavariate(1.3, 3.0) * 0.9
    room = max(total - existing, 0.0)
    values, weights = TENURES[product]
    retire = 60 if emp == "salaried" else 70
    remaining = _months_to(age, dob_month, retire)
    ok = [(v, w) for v, w in zip(values, weights) if v <= remaining] or [(min(values), 1)]
    tenure = _pick(rng, [v for v, _ in ok], [w for _, w in ok])
    lo, hi = AMOUNT_LIMITS[product]
    principal = lo
    for _ in range(5):
        k = finance.emi(1.0, rate, tenure)
        principal = int(_clip(_round_amount(product, room / k), lo, hi))
        row = grid_row_for(product, principal)
        if tenure <= row["max_tenure_months"] or not hard_grid_cap:
            break
        smaller = [v for v in values if v <= row["max_tenure_months"] and v <= remaining]
        tenure = max(smaller) if smaller else min(values)
    proposed = int(round(finance.emi(principal, rate, tenure)))
    existing_i = int(round(existing))
    if (existing_i + proposed) / income > 1.05:  # minimum-size loan on a small income: lift the income instead
        income = _round_to((existing_i + proposed) / rng.uniform(0.75, 1.0), 500)
    return int(income), principal, tenure, existing_i, proposed


# ------------------------------------------------------------------------------------------------ repayment months


def _repayment(rng: random.Random, outcome: str, emi_amt: int, base_balance: float) -> list[RepaymentMonth]:
    dpd = [0] * 6
    bounced = [False] * 6
    partial = [False] * 6
    factor = [1.0] * 6
    if outcome == "repays":
        for m in range(6):
            if rng.random() < 0.06:
                dpd[m] = rng.randint(2, 9)
            bounced[m] = rng.random() < 0.015
            partial[m] = rng.random() < 0.02
            factor[m] = 1.0 + rng.gauss(0, 0.12)
        if rng.random() < 0.04:  # a rare clean repayer whose balance runs down
            for m in range(4, 6):
                factor[m] *= rng.uniform(0.4, 0.58)
    elif outcome == "slips":
        for m in rng.sample(range(6), rng.randint(1, 3)):
            if rng.random() < 0.5:
                bounced[m], dpd[m] = True, rng.randint(5, 25)
            else:
                partial[m], dpd[m] = True, rng.randint(3, 20)
        if rng.random() < 0.2:
            dpd[rng.randrange(6)] = rng.randint(30, 45)
        drift = rng.uniform(0.6, 1.05)
        for m in range(6):
            factor[m] = 1.0 + (drift - 1.0) * m / 5 + rng.gauss(0, 0.1)
    else:  # defaults
        onset = _pick(rng, [2, 3, 4, 5, 6], [10, 34, 26, 20, 10])
        floor = rng.uniform(0.25, 0.55)
        for m in range(6):
            if m + 1 < onset:
                dpd[m] = rng.randint(0, 4) if rng.random() < 0.15 else 0
                continue
            dpd[m] = rng.randint(6, 20) if m + 1 == onset else dpd[m - 1] + rng.randint(10, 35)
            bounced[m] = rng.random() < 0.8
            partial[m] = rng.random() < 0.35
            factor[m] = max(floor, 1.0 - (1.0 - floor) * (m + 2 - onset) / max(1, 7 - onset))
    return [
        RepaymentMonth(m=m + 1, dpd=dpd[m], emi_bounced=bounced[m], partial_payment=partial[m],
                       avg_balance_inr=int(max(1_000, base_balance * max(0.05, factor[m]))))
        for m in range(6)
    ]


def _covenants(rng, w: Writer, outcome: str, dscr0: float, biz: str, cfo_name: str) -> list[Covenant]:
    p = {"repays": (0.05, 0.08, 0.03, 0.05), "slips": (0.25, 0.30, 0.12, 0.18), "defaults": (0.60, 0.55, 0.30, 0.35)}[outcome]
    w.rec.org(biz)
    w.rec.person(cfo_name)
    out = []
    breach = rng.random() < p[0]
    rep = round(rng.uniform(0.85, 1.24) if breach else max(1.26, dscr0 * rng.uniform(0.95, 1.15)), 2)
    out.append(Covenant(covenant_id="dscr_min_1_25", required=1.25, reported_value=rep,
                        evidence_text=f"{biz} compliance certificate signed by {cfo_name}: DSCR for the last audited year is {rep:.2f} against the required minimum 1.25."))
    breach = rng.random() < p[1]
    rep = rng.randint(1, 5) if breach else 6
    out.append(Covenant(covenant_id="stock_statement_monthly", required=6, reported_value=rep,
                        evidence_text=f"Stock statements were received on time for {rep} of the last 6 months (due by the 10th of each month)."))
    breach = rng.random() < p[2]
    rep = rng.randint(1, 2) if breach else 0
    txt = (f"Bureau and auditor check found {rep} new loan facilities of {w.inr(rng.randint(5, 60) * 100_000, 'rs.')} taken without the bank's consent."
           if breach else "Bureau and auditor check found no new borrowing beyond the sanctioned facilities.")
    out.append(Covenant(covenant_id="no_unapproved_borrowing", required=0, reported_value=rep, evidence_text=txt))
    breach = rng.random() < p[3]
    when = add_months(AS_OF.replace(day=1), -rng.randint(1, 4) if breach else rng.randint(3, 11))
    out.append(Covenant(covenant_id="insurance_current", required=True, reported_value=not breach,
                        evidence_text=("Insurance on hypothecated stock expired on " + w.dmy(when, 0) + "; renewal not evidenced.") if breach
                        else ("Insurance on hypothecated stock is current until " + w.dmy(when, 0) + ".")))
    return out


def _is_breach(c: Covenant) -> bool:
    if c.covenant_id == "dscr_min_1_25":
        return c.reported_value < c.required
    if c.covenant_id == "stock_statement_monthly":
        return c.reported_value < c.required
    if c.covenant_id == "no_unapproved_borrowing":
        return c.reported_value > c.required
    return c.reported_value != c.required


# ------------------------------------------------------------------------------------------------ one file


def generate_file(i: int, seed: int) -> LoanFile:  # noqa: C901 - one linear story is easier to audit than helpers
    rng = random.Random(f"{seed}:{i}")
    file_id = f"F{i + 1:06d}"
    seg = SEGMENT_CYCLE[i % len(SEGMENT_CYCLE)]
    P = SEG[seg]
    product = PRODUCT_BY_SEGMENT[seg]
    rec = PIIRecorder()
    w = Writer(rng, rec)
    plan = DocPlan()

    # ---- demographics and disparity subset draws (no feature or label is looked at)
    gender = "X" if rng.random() < 0.005 else ("F" if rng.random() < P["fshare"] else "M")
    language = _pick(rng, LANGS, LANG_W)
    cluster = _pick(rng, CLUSTERS, CLUSTER_W)
    u_lang, u_gender, u_pin = rng.random(), rng.random(), rng.random()
    tag = None
    if u_lang < LANG_TAG_P.get(language, 0.0):
        tag = "lang_doc_script"
    elif gender == "F" and seg == _E and u_gender < GENDER_TAG_P:
        tag = "gender_income_proxy"
    elif cluster == "PC7" and u_pin < PINCODE_TAG_P:
        tag = "pincode_bureau_thin"
    native_lang = language if tag == "lang_doc_script" else None

    city = nm.draw_city(rng, language)
    name_region = nm.NATIVE_LANG_REGION.get(language, city.region)
    person = nm.draw_person(rng, name_region, gender, native_lang)
    plan.name_region = name_region
    street_idx = nm.draw_street_index(rng)
    line1 = nm.line1_latin(rng, street_idx)
    address = Address(line1=line1, city=city.name, pincode=nm.draw_pin(rng, city), state=city.state)
    if native_lang:
        plan.native_lang = native_lang
        plan.native_line1 = nm.line1_native(line1, street_idx, native_lang)

    # ---- employment, GST, fraud
    emp = {"salaried_personal": "salaried", "self_employed": "self_employed", "msme_business": "business_owner"}.get(seg)
    if seg == _H:
        emp = "salaried" if rng.random() < 0.65 else "self_employed"
    has_gst = seg == _M or (seg == _E and rng.random() < 0.60)
    fraud_type = None
    u = rng.random()
    p_sal = FRAUD_SALARY_P if emp == "salaried" and seg in (_S, _H) else 0.0
    p_gst = FRAUD_GST_P if has_gst else 0.0
    edges = [("identity_mismatch", FRAUD_P["identity_mismatch"]), ("synthetic_identity", FRAUD_P["synthetic_identity"]),
             ("salary_pattern_mismatch", p_sal), ("gst_bank_mismatch", p_gst)]
    acc = 0.0
    for kind, p in edges:
        acc += p
        if u < acc:
            fraud_type = kind
            break
    fraud = fraud_type is not None
    synthetic = fraud_type == "synthetic_identity"
    plan.fraud_type = fraud_type
    plan.templated = synthetic

    # ---- latent quality, age, tenure in role
    z = rng.gauss(0.0, 1.0)
    lo_a, hi_a = P["age"]
    age = int(rng.triangular(lo_a, hi_a, lo_a + (hi_a - lo_a) * 0.35))
    if seg == _H and emp == "salaried":
        age = min(age, 50)
    dob_year = AS_OF.year - age
    plan.dob_month, plan.dob_day = rng.randint(1, 12), rng.randint(1, 28)
    if emp == "salaried":
        years = _clip(_lognorm(rng, 4.5, 0.75), 0.3, min(35.0, max(0.5, age - 21)))
    elif seg == _M:
        years = _clip(_lognorm(rng, 9.0, 0.65), 1.0, min(40.0, age - 21))
    else:
        years = _clip(_lognorm(rng, 6.0, 0.75), 0.5, min(35.0, age - 20))
    if synthetic:
        years = rng.uniform(0.2, 0.6) if emp == "salaried" else rng.uniform(0.3, 1.0)
    years = round(years, 1)

    # ---- bureau
    age_factor = 2.0 if age < 28 else (0.5 if age > 42 else 1.0)
    genuine_ntc = synthetic or rng.random() < P["ntc_p"] * age_factor
    true_score = None if genuine_ntc else int(_clip(round(725 + 55 * z + rng.gauss(0, 18)), 300, 900))
    if genuine_ntc:
        dpd = writeoffs = 0
    else:
        dpd = 0
        if rng.random() < _sig(-1.55 - 0.85 * z):
            dpd = int(min(180, 4 + rng.expovariate(1.0 / (24 * math.exp(-0.30 * z)))))
        writeoffs = 1 if rng.random() < _sig(-4.4 - 1.0 * z) else 0
    bounces = min(6, _poisson(rng, 0.16 * math.exp(-0.55 * z) + 0.012 * dpd))
    enquiries = _poisson(rng, 0.7 if genuine_ntc else 1.1 * math.exp(-0.15 * z))
    active_loans = 0 if genuine_ntc else _poisson(rng, 1.4)
    if genuine_ntc:
        history = rng.randint(0, 3 if synthetic else 5)
    else:
        history = int(_clip((age - 21) * 12 * rng.uniform(0.3, 0.9), 12, 240))
    reported_score = true_score
    if tag == "pincode_bureau_thin" and true_score is not None:
        reported_score = None
        history = rng.randint(0, 5)
    util = round(_clip(rng.gauss(0.35 - 0.10 * z, 0.2), 0.0, 1.0), 2)

    # ---- rate, income, loan
    adj = 1.5 if reported_score is None else (-0.5 if reported_score >= 780 else 0.0 if reported_score >= 725 else 0.5 if reported_score >= 675 else 1.0 if reported_score >= 650 else 2.0)
    rate = round(round((RATE_BASE[seg] + adj + rng.uniform(-0.25, 0.75)) / 0.05) * 0.05, 2)
    lo_i, hi_i = P["income_clip"]
    income = _round_to(_clip(_lognorm(rng, P["income_med"], P["income_sigma"]), lo_i, hi_i), 500)
    if synthetic:
        income = _round_to(income, 5000) or 5000
    income, principal, tenure, existing_emi, proposed_emi = _size_loan(rng, seg, emp, income, rate, age, plan.dob_month, z)

    # volatility
    if emp == "salaried":
        vol = abs(rng.gauss(0.06, 0.04)) * math.exp(-0.1 * z) + (rng.uniform(0.12, 0.35) if rng.random() < 0.08 else 0.0)
    elif seg == _M:
        vol = _lognorm(rng, 0.30, 0.4) * math.exp(-0.12 * z)
    else:
        vol = _lognorm(rng, 0.27, 0.45) * math.exp(-0.12 * z)
    vol = round(_clip(vol, 0.01, 0.9), 3)

    # ---- plan the missing items
    missing = [k for k, p in MISSING_P.items() if rng.random() < p]
    if fraud_type == "gst_bank_mismatch" and "statements" in missing:
        missing.remove("statements")
    if "income_proof" in missing:
        plan.income_defect = "stale" if rng.random() < 0.75 else "absent"
    if "address_proof" in missing:
        plan.address_defect = _pick(rng, ["expired", "wrong_type", "mismatched"], [40, 30, 30])
        if native_lang and plan.address_defect == "wrong_type":
            plan.address_defect = "expired"
    coherence = None
    if "fields_coherence" in missing:
        kinds = ["income_off_type", "experience_impossible"] + (["tenure_past_retirement"] if emp == "salaried" else [])
        coherence = _pick(rng, kinds, [0.4, 0.25, 0.35] if emp == "salaried" else [0.6, 0.4])
    if coherence == "tenure_past_retirement":
        remaining = _months_to(age, plan.dob_month, 60)
        row = grid_row_for(product, principal)
        bad = int(math.ceil((remaining + rng.randint(8, 36)) / 6.0) * 6)
        if bad > row["max_tenure_months"] or bad <= remaining + 6:
            coherence = "income_off_type"
        else:
            tenure = bad
            proposed_emi = int(round(finance.emi(principal, rate, tenure)))
    if coherence == "experience_impossible":
        years = float(age - rng.randint(10, 14))
    plan.coherence = coherence

    # declared income
    gap = abs(rng.gauss(0.02, 0.03)) if rng.random() < 0.85 else rng.uniform(0.08, 0.25)
    if coherence == "income_off_type":
        gap = rng.uniform(1.5, 3.5)
    declared = _round_to(income * (1 + gap), 500)

    # ---- names, identifiers
    holder = "P"
    pan = nm.gen_pan(rng, holder, person.last)
    aadhaar = nm.gen_aadhaar(rng)
    phone = nm.gen_phone(rng)
    if synthetic:
        email_kind = "disposable" if rng.random() < 0.85 else "free"
    elif emp == "salaried" and rng.random() < 0.45:
        email_kind = "corporate"
    else:
        email_kind = "disposable" if rng.random() < 0.008 else "free"
    if emp == "salaried":
        employer = nm.draw_employer(rng)
        business = None
        org_primary = employer
    else:
        employer = None
        entity = None
        if seg == _M:
            kind = _pick(rng, ["company", "firm", "prop"], [55, 30, 15])
            entity = {"company": "Pvt Ltd", "firm": rng.choice(["& Sons", "Co", "Associates", "LLP"]), "prop": ""}[kind]
        business = nm.draw_business(rng, entity)
        org_primary = business
    email = nm.gen_email(rng, person.name, email_kind, org_primary if email_kind == "corporate" else None)
    account = nm.gen_account_number(rng)

    # ---- structured identifiers into the inventory, applicant and primary org first
    rec.person(person.name)
    if person.native:
        rec.alias("person_names", person.native, person.name)
    rec.org(org_primary)
    rec.pan(pan)
    rec.aadhaar(aadhaar)
    rec.phone(phone)
    rec.email(email)
    rec.account(account)
    rec.address(address.line1)
    rec.pincode(address.pincode)

    # co-applicant
    co = None
    co_p = {_S: 0.04, _E: 0.10, _M: 0.30, _H: 0.55}[seg]
    if rng.random() < co_p:
        if seg == _M:
            co_gender, relation = rng.choice(["M", "F"]), rng.choice(["business partner", "director"])
        else:
            co_gender, relation = ("F" if person.gender == "M" else "M"), "spouse"
        co_first = nm.draw_person(rng, name_region, co_gender).first
        co_last = person.last if relation == "spouse" else nm.draw_person(rng, name_region, co_gender).last
        co = CoApplicant(name=f"{co_first} {co_last}", pan=nm.gen_pan(rng, "P", co_last), relation=relation)
        rec.person(co.name)
        rec.pan(co.pan)

    # ---- bank
    required = STATEMENTS_REQUIRED[seg]
    if "statements" in missing:
        months_cov = rng.randint(2, 5) if required == 6 else rng.randint(6, 11)
    else:
        months_cov = _pick(rng, [required, required + 1, required + 3, required + 6], [70, 12, 10, 8]) if required == 12 else _pick(rng, [6, 7, 9, 12], [75, 10, 8, 7])
    if emp == "salaried":
        avg_credits = int(income * rng.uniform(1.0, 1.25))
    elif seg == _M:
        avg_credits = int(income / rng.uniform(0.06, 0.14))
    else:
        avg_credits = int(income / rng.uniform(0.12, 0.28))
    cash_share = round(rng.uniform(0.0, 0.08) if emp == "salaried" else rng.uniform(0.05, 0.45) if seg != _M else rng.uniform(0.03, 0.3), 3)
    narration = employer
    if fraud_type == "salary_pattern_mismatch":
        alt = nm.draw_employer(rng)
        while alt == employer:
            alt = nm.draw_employer(rng)
        narration = alt
        plan.alt_employer = alt
        rec.org(alt)
    sal_months = None
    if emp == "salaried":
        sal_months = max(0, min(months_cov, 12) - (1 if rng.random() < 0.1 else 0))
    bank = Bank(
        account_number=account, months_covered=months_cov, most_recent_month_age=_pick(rng, [0, 1, 2], [10, 85, 5]),
        salary_credits_months=sal_months, salary_narration_employer=narration, avg_monthly_credits_inr=avg_credits,
        emi_bounces_6m=bounces, cash_deposit_share=cash_share,
        min_balance_breaches_6m=min(6, _poisson(rng, 0.25 * math.exp(-0.4 * z) + 0.25 * bounces)),
    )

    # ---- GST
    gst = None
    if has_gst:
        bank_credits = avg_credits * 12
        if fraud_type == "gst_bank_mismatch":
            ratio = rng.uniform(2.1, 4.5) if rng.random() < 0.7 else rng.uniform(0.25, 0.45)
        else:
            ratio = _clip(rng.gauss(1.03, 0.09), 0.85, 1.2)
        months_filed = min(12, int(years * 12))
        on_time = max(0, months_filed - _poisson(rng, 0.6 * math.exp(-0.3 * z)))
        state_code = city.gst_code
        if seg == _M and business and business.endswith("Pvt Ltd"):
            biz_pan = nm.gen_pan(rng, "C", business)
        elif seg == _M and business and not any(business.endswith(s) for s in ("Pvt Ltd",)) and entity:
            biz_pan = nm.gen_pan(rng, "F", business)
        else:
            biz_pan = pan
        plan.biz_pan = biz_pan
        rec.pan(biz_pan)
        gst = Gst(gstin=nm.gen_gstin(rng, state_code, biz_pan), filings_on_time_12m=on_time,
                  turnover_12m_inr=int(bank_credits * ratio), bank_credits_12m_inr=int(bank_credits), months_filed=months_filed)
    elif seg in (_E, _M):
        plan.biz_pan = pan
    if seg in (_E, _M) or (seg == _H and emp == "self_employed"):
        b_city = city
        b_line = nm.line1_latin(rng, nm.draw_street_index(rng), business=True)
        plan.business_address = Address(line1=b_line, city=b_city.name, pincode=nm.draw_pin(rng, b_city), state=b_city.state)

    # ---- property
    prop = None
    if seg == _H:
        ltv_t = _clip(rng.gauss(70 - 1.5 * z, 9), 35, 92)
        vmin = principal / (ltv_t / 100)
        u_s = rng.random()
        spread = rng.uniform(0, 0.08) if u_s < 0.8 else rng.uniform(0.08, 0.15) if u_s < 0.95 else rng.uniform(0.15, 0.30)
        vmax = vmin * (1 + spread)
        market, val2 = (vmax, vmin) if rng.random() < 0.5 else (vmin, vmax)
        market_i, val2_i = _round_to(market, 10_000), _round_to(val2, 10_000)
        title = _pick(rng, ["clear", "pending_mutation", "disputed"], [88, 8, 4])
        legal = {"clear": _pick(rng, ["positive", "pending"], [95, 5]),
                 "pending_mutation": _pick(rng, ["pending", "positive"], [60, 40]),
                 "disputed": _pick(rng, ["adverse", "pending"], [70, 30])}[title]
        ptype = rng.choice(["apartment", "independent_house", "builder_floor", "plot_with_construction"])
        prop = Property(market_value_inr=market_i, valuation_2_inr=val2_i, property_type=ptype, title_status=title,
                        legal_opinion=legal, ltv=round(100 * principal / min(market_i, val2_i), 1))
        p_city = city if rng.random() < 0.8 else nm.draw_city(rng, "en")
        p_addr = Address(line1=nm.line1_latin(rng, nm.draw_street_index(rng)), city=p_city.name, pincode=nm.draw_pin(rng, p_city), state=p_city.state)
        plan.seed_extras = {"ptype": ptype.replace("_", " "), "prop_addr": p_addr, "co_name": co.name if co else None}

    # ---- identity block
    linked = rng.random() < 0.96
    phone_vintage = rng.randint(6, 60) if age < 25 else rng.randint(12, 180)
    if rng.random() < 0.015:
        phone_vintage = rng.randint(0, 2)
    shared = _pick(rng, [0, 1, 3], [94, 4.5, 1.5])
    consistent = rng.random() < 0.985
    if fraud_type == "identity_mismatch":
        linked = rng.random() < 0.45
        shared = _pick(rng, [0, 1, 3], [60, 30, 10])
    if synthetic:
        phone_vintage = rng.randint(0, 2) if rng.random() < 0.85 else rng.randint(3, 8)
        linked = rng.random() < 0.4
        shared = 3 if rng.random() < 0.6 else 1
        consistent = rng.random() > 0.9
    identity = Identity(pan_aadhaar_linked=linked, phone_vintage_months=phone_vintage, email_domain_type=email_kind,
                        address_shared_with_other_apps=shared, bureau_history_vs_age_consistent=consistent)

    # ---- documentation type and informal income
    if emp == "salaried":
        doc_type_income = "salary_slip"
    elif seg == _M:
        doc_type_income = _pick(rng, ["gst_and_bank", "itr"], [70, 30])
    elif seg == _E:
        doc_type_income = "informal_declared" if (tag == "gender_income_proxy" or rng.random() < 0.04) else (
            _pick(rng, ["itr", "gst_and_bank"], [50, 50]) if has_gst else "itr")
    else:
        doc_type_income = "itr"
    plan.informal = doc_type_income == "informal_declared"

    # ---- identity fraud material
    if fraud_type == "identity_mismatch":
        k = 1 if rng.random() < 0.6 else 2
        plan.id_kinds = tuple(rng.sample(["pan", "name", "dob"], k))
        alt_p = nm.draw_person(rng, name_region, gender)
        while alt_p.name == person.name:
            alt_p = nm.draw_person(rng, name_region, gender)
        plan.alt_person = alt_p
        plan.alt_pan = nm.gen_pan(rng, "P", alt_p.last)
        plan.alt_dob_year = dob_year + rng.choice([-1, 1]) * rng.randint(6, 15)

    # ---- alternative address for a mismatched proof
    if plan.address_defect == "mismatched":
        alt_city = nm.draw_city(rng, language)
        if native_lang:
            alt_idx = nm.draw_street_index(rng)
            alt_l1 = nm.line1_latin(rng, alt_idx)
            plan.alt_native_line1 = nm.line1_native(alt_l1, alt_idx, native_lang)
        else:
            alt_l1 = nm.line1_latin(rng, nm.draw_street_index(rng))
        plan.alt_address = Address(line1=alt_l1, city=alt_city.name, pincode=nm.draw_pin(rng, alt_city), state=alt_city.state)

    # ---- memo and KFS plan
    row = grid_row_for(product, principal)
    plan.rate_pct = rate
    plan.memo_tenure = tenure
    memo_defects: list[str] = []
    if rng.random() < MEMO_P["memo_condition_mismatch"]:
        if rng.random() < 0.5:
            plan.memo_tenure = row["max_tenure_months"] + 12 * rng.randint(1, 3)
        else:
            plan.memo_missing_condition = rng.choice([c["id"] for c in row["required_conditions"]])
        memo_defects.append("memo_condition_mismatch")
    elif rng.random() < 0.3:
        room_extra = rng.sample(EXTRA_CONDITIONS, 1)
        plan.extra_conditions = tuple(room_extra)
    proc_pct = {_S: rng.uniform(0.8, 2.0), _E: rng.uniform(0.8, 1.5), _M: rng.uniform(0.4, 1.0), _H: rng.uniform(0.25, 0.75)}[seg]
    proc = _round_to(principal * proc_pct / 100, 10)
    items = {"processing fee": proc, "GST on processing fee": int(round(proc * 0.18)), "documentation charges": rng.choice([500, 1000, 1500, 2000, 3000, 5000])}
    if seg in (_S, _H) and rng.random() < 0.3:
        items["insurance premium"] = _round_to(principal * rng.uniform(0.003, 0.009), 10)
    plan.fee_items = items
    if rng.random() < MEMO_P["apr_math_wrong"]:
        emi_m = int(round(finance.emi(principal, rate, plan.memo_tenure)))
        apr_true = finance.apr_from_components(principal, sum(items.values()), emi_m, plan.memo_tenure)
        plan.apr_variant = "plain_rate" if (apr_true - rate >= 0.5 and rng.random() < 0.6) else "off"
        memo_defects.append("apr_math_wrong")
    if rng.random() < MEMO_P["disclosure_missing"]:
        cands = [d for d in disclosure_ids() if d != "apr"]
        k = 1 if rng.random() < 0.8 else 2
        plan.omit_disclosures = tuple(d for d in disclosure_ids() if d in rng.sample(cands, k))
        memo_defects += [f"disclosure_missing:{d}" for d in plan.omit_disclosures]
    off_p = nm.draw_person(rng, name_region, rng.choice(["M", "F"]))
    plan.officer, plan.officer_phone = off_p, nm.gen_phone(rng)
    plan.officer_email = f"grievance.{off_p.first.lower()}@{nm.org_slug('Uttam Bank')}.co.in"

    # ---- assemble the draft (labels needing only fraud), then compute labels from the true features
    application = Application(
        product=product, loan_amount_inr=principal, tenure_months=tenure, purpose=rng.choice(PURPOSES[seg]),
        employment_type=emp, employer_name=employer, business_name=business, years_in_job_or_business=years,
        declared_monthly_income_inr=declared, city_tier=city.tier,
    )
    placeholder_memo = SanctionMemo(text="", product=product, ticket_band=row["band"], tenure_months=tenure, conditions=[], rate_pct=rate)
    placeholder_kfs = Kfs(text="", principal_inr=principal, fees_inr=0, rate_pct=rate, emi_inr=proposed_emi, tenure_months=tenure, apr_stated_pct=rate, disclosures_present=[])
    draft = LoanFile(
        file_id=file_id, segment=seg,
        applicant=Applicant(name=person.name, name_native=person.native, gender=gender, dob_year=dob_year, pan=pan, aadhaar=aadhaar,
                            phone=phone, email=email, address=address, preferred_language=language),
        co_applicant=co,
        demographics=Demographics(gender=gender, pincode_cluster=cluster, language=language),
        application=application,
        bureau=Bureau(score=reported_score, active_loans=active_loans, max_dpd_12m=dpd, enquiries_6m=enquiries,
                      writeoffs_or_settlements=writeoffs, history_months=history),
        income=Income(verified_monthly_income_inr=income, volatility_cv=vol, months_history=min(240, int(years * 12)),
                      documentation_type=doc_type_income),
        obligations=Obligations(existing_emi_inr=existing_emi, proposed_emi_inr=proposed_emi, credit_card_utilization=util),
        bank=bank, gst=gst, property=prop, identity=identity, documents=[], sanction_memo=placeholder_memo, kfs=placeholder_kfs,
        post_disbursal=PostDisbursal(months=[RepaymentMonth(m=k, dpd=0, emi_bounced=False, partial_payment=False, avg_balance_inr=1) for k in range(1, 7)]),
        labels=Labels(fraud=fraud, fraud_type=fraud_type),
        meta=Meta(generator_version=GENERATOR_VERSION, seed=seed, disparity_subset=tag,
                  true_bureau_score=true_score if tag == "pincode_bureau_thin" else None),
        pii_inventory=PIIInventory(),
    )
    sanctionable, _failed = synthetic_credit_decision(draft)
    pd = pd_for_file(draft)
    outcome = draw_outcome(pd, rng)
    weakness = primary_weakness(draft)
    closeness = closeness_level(draft)

    # ---- repayment and covenants
    if emp == "salaried":
        base_balance = max(income * rng.uniform(0.8, 2.5), proposed_emi * 2)
    elif seg == _H:
        base_balance = income * rng.uniform(1.0, 3.0)
    else:
        base_balance = avg_credits * rng.uniform(0.10, 0.35)
    months = _repayment(rng, outcome, proposed_emi, base_balance)
    covenants: list[Covenant] = []
    if seg == _M:
        cfo = nm.draw_person(rng, name_region, rng.choice(["M", "F"]))
        dscr0 = 12 * income / (12 * (existing_emi + proposed_emi))
        covenants = _covenants(rng, w, outcome, dscr0, business or "", cfo.name)
    breaches = [c.covenant_id for c in covenants if _is_breach(c)]

    # ---- documents, memo and KFS
    docs = build_documents(draft, plan, w)
    memo, kfs = build_memo_and_kfs(draft, plan, w)

    labels = Labels(
        sanctionable=sanctionable, fraud=fraud, fraud_type=fraud_type,
        missing_items=[m for m in ("income_proof", "address_proof", "statements", "fields_coherence") if m in missing],
        outcome_12m=outcome, risk_pd=round(pd, 6), primary_weakness=weakness, closeness_level=closeness,
        memo_defects=memo_defects, ews_truth={**ews_truth(months)}, covenant_breaches=breaches,
    )
    final = draft.model_copy(update={
        "documents": docs, "sanction_memo": memo, "kfs": kfs, "post_disbursal": PostDisbursal(months=months, covenants=covenants),
        "labels": labels, "pii_inventory": rec.inventory(),
    })
    final.labels.question_truth = question_truth(final)
    return final


def iter_book(n: int, seed: int) -> Iterator[LoanFile]:
    for i in range(n):
        yield generate_file(i, seed)


def generate_book(n: int, seed: int) -> list[LoanFile]:
    """``n`` synthetic loan files, deterministic for a given ``seed``."""
    return list(iter_book(n, seed))
