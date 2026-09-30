"""Module F: post-disbursal monitoring (PLAN sections 3.7, 3.8; spec section 5).

Asked in the `monitoring` stage. State: `loan` (with `balance_change_band`), `repayment` (six months of `{m, dpd_days, dpd_band, emi_bounced,
partial_payment, avg_balance_band}`) and, for MSME loans, `covenants` (`{covenant_id, required,
reported_value_band, evidence_text}`).

* `F_ews_*`: four early-warning Nouls, all "yes is bad". Jev cannot do arithmetic and does not count long lists, so each
  one points at one field and asks for a plain pattern over it: `dpd_days` (small integers) that rise or reach 30, a flag
  that is set in more than one month, and `loan.balance_change_band` (code has already compared the last two months with
  the first two) being `falling_40_plus`. Each truth is a function of what the state shows (PLAN 3.8 truth-alignment).
* `F_covenant_<covenant_id>`: one Noul per MSME covenant, "yes is good" (the covenant is complied with). Each applies only
  when that covenant is present in `state.covenants`. `instructions.data.covenant_id` says which entry to read, so the
  question is unambiguous when four covenants are asked in one call.

Every F question is routing relevant: warnings aggregate into watchlist tiers in the policy engine.
"""

from __future__ import annotations

from collections.abc import Callable

from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register

_MSME = frozenset({"msme_business"})
_ORDER = "Read the entries of `repayment` in order of `m`, oldest month first."

# --------------------------------------------------------------------------------------------- F_ews_*

register(
    QuestionDef(
        qid="F_ews_dpd_rising",
        module="F",
        stage="monitoring",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_bad",
        routing_relevant=True,
        reason_text="Delinquency is rising: repayments are getting later, or a month reached 30 days past due or more",
        wire=noul_wire(
            "Is delinquency rising in `repayment[*].dpd_days`? Answer yes if either of these is true. "
            "(1) Any entry has `dpd_days` of 30 or more. "
            "(2) In at least three of the last four entries, `dpd_days` is higher than in the entry just before it.",
            refer_to=["`repayment`"],
            focus=(
                _ORDER + " Look at `dpd_days` only, the days past due in each month. Compare each of the last four "
                "entries with the entry just before it: only a strictly higher number counts as a rise, an equal "
                "number is not a rise. Ignore `emi_bounced`, `partial_payment` and `avg_balance_band`: other "
                "questions cover them. `dpd_band` is the same information in bands; read `dpd_days`."
            ),
            true_what=(
                "At least one entry has `dpd_days` of 30 or more, or `dpd_days` is higher than in the previous entry "
                "in at least three of the last four entries."
            ),
            true_examples=[
                "`dpd_days` runs 0, 0, 12, 35, 58, 84: one entry is 30 or more.",
                "`dpd_days` runs 0, 0, 4, 11, 18, 25: higher than the entry before in each of the last four entries, although no entry reaches 30.",
                "`dpd_days` is 0 in every entry except one entry at 32, then back to 0: one month at 30 or more is enough.",
            ],
            false_what=(
                "Every entry has `dpd_days` below 30, and `dpd_days` is higher than in the previous entry in two or "
                "fewer of the last four entries."
            ),
            false_not_for=(
                "A `dpd_days` that repeats the same number from entry to entry (equal is not higher), and "
                "`emi_bounced` or `partial_payment` flags on their own."
            ),
            false_examples=[
                "`dpd_days` is 0 in every entry.",
                "`dpd_days` runs 0, 5, 0, 5, 0, 5: it goes up and down, and stays below 30.",
                "`dpd_days` runs 0, 3, 6, 6, 6, 9: higher than the entry before in only two of the last four entries.",
            ],
        ),
    )
)

register(
    QuestionDef(
        qid="F_ews_emi_bounces",
        module="F",
        stage="monitoring",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_bad",
        routing_relevant=True,
        reason_text="Two or more EMIs have bounced in the monitoring period",
        wire=noul_wire(
            "Is `emi_bounced` true in two or more different entries of `repayment`?",
            refer_to=["`repayment`"],
            focus=(
                _ORDER + " Look at `emi_bounced` only. A late payment (`dpd_band`) or a `partial_payment` is not a "
                "bounced EMI."
            ),
            true_what="`emi_bounced` is true in at least two entries of `repayment`, next to each other or apart.",
            true_examples=[
                "`emi_bounced` is true in the third and fourth entries and false in the others.",
                "`emi_bounced` is true in the first entry and in the sixth entry.",
                "`emi_bounced` is true in three of the entries.",
            ],
            false_what="`emi_bounced` is true in only one entry of `repayment`, or in none.",
            false_not_for=(
                "Entries that are late (`dpd_band` above `0`) or short-paid (`partial_payment` true) while "
                "`emi_bounced` is false. Those are not bounced EMIs."
            ),
            false_examples=[
                "`emi_bounced` is false in every entry.",
                "`emi_bounced` is true in one entry only, while `dpd_band` is worse in the later entries.",
            ],
        ),
    )
)

register(
    QuestionDef(
        qid="F_ews_partial_payments",
        module="F",
        stage="monitoring",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_bad",
        routing_relevant=True,
        reason_text="Two or more EMIs were only partly paid in the monitoring period",
        wire=noul_wire(
            "Is `partial_payment` true in two or more different entries of `repayment`?",
            refer_to=["`repayment`"],
            focus=(
                _ORDER + " Look at `partial_payment` only. A bounced EMI (`emi_bounced`) or a late payment "
                "(`dpd_band`) is not a partial payment."
            ),
            true_what="`partial_payment` is true in at least two entries of `repayment`, next to each other or apart.",
            true_examples=[
                "`partial_payment` is true in the fourth and fifth entries and false in the others.",
                "`partial_payment` is true in the second entry and in the sixth entry.",
                "`partial_payment` is true in three of the entries.",
            ],
            false_what="`partial_payment` is true in only one entry of `repayment`, or in none.",
            false_not_for=(
                "Entries with `emi_bounced` true or a worse `dpd_band` while `partial_payment` is false. Those are not "
                "partial payments."
            ),
            false_examples=[
                "`partial_payment` is false in every entry.",
                "`partial_payment` is true in one entry only, while `emi_bounced` is true in two entries.",
            ],
        ),
    )
)

register(
    QuestionDef(
        qid="F_ews_balance_stress",
        module="F",
        stage="monitoring",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_bad",
        routing_relevant=True,
        reason_text="Account balance has fallen sharply over the monitoring period",
        wire=noul_wire(
            "Is `loan.balance_change_band` `falling_40_plus`?",
            refer_to=["`loan.balance_change_band`", "`repayment`"],
            focus=(
                "`loan.balance_change_band` compares the average balance of the last two months with the first two "
                "months. Its values, from best to worst, are `rising`, `flat`, `falling_10_40` and `falling_40_plus`. "
                "Only `falling_40_plus` is yes. `avg_balance_band` in `repayment` is background: read "
                "`loan.balance_change_band` and nothing else."
            ),
            true_what="`loan.balance_change_band` is `falling_40_plus`.",
            true_examples=[
                "`loan.balance_change_band` is `falling_40_plus`, and `avg_balance_band` runs `75k-1L`, `75k-1L`, `50-75k`, `25-50k`, `25-50k`, `10-25k`.",
                "`loan.balance_change_band` is `falling_40_plus`, even when some months of `avg_balance_band` look steady.",
            ],
            false_what="`loan.balance_change_band` is `rising`, `flat` or `falling_10_40`.",
            false_not_for=(
                "`falling_10_40`. A fall of 10 to 40 percent is a moderate fall and belongs on this side. "
                "A single low month in `avg_balance_band` does not change the answer either."
            ),
            false_examples=[
                "`loan.balance_change_band` is `falling_10_40`.",
                "`loan.balance_change_band` is `rising`, and `avg_balance_band` runs `25-50k`, `25-50k`, `50-75k`, `50-75k`, `75k-1L`, `75k-1L`.",
                "`loan.balance_change_band` is `flat`, while `avg_balance_band` dips to `10-25k` in the third entry.",
            ],
        ),
    )
)

# --------------------------------------------------------------------------------------------- F_covenant_<id>

_FIND_ENTRY = (
    "Find the entry in `covenants` whose `covenant_id` is `{cid}` (also given in `data.covenant_id`) and read its "
    "`required`, `reported_value_band` and `evidence_text`. Ignore the other covenants."
)

# covenant_id -> question and rubric. "Complied with" is always the yes side.
_COVENANTS: dict[str, dict] = {
    "dscr_min_1_25": {
        "question": (
            "Is the covenant `dscr_min_1_25` complied with, meaning the reported debt service coverage is at or above "
            "the `required` value?"
        ),
        "true_what": (
            "`reported_value_band` is at or above `required` (a coverage of 1.25 or more), and `evidence_text` says the "
            "coverage meets the minimum."
        ),
        "true_examples": [
            "`required` is 1.25 and `reported_value_band` is `1.25-1.5`.",
            "`reported_value_band` is `>2` and `evidence_text` reports a coverage well above the required minimum.",
        ],
        "false_what": "`reported_value_band` is below `required`, or `evidence_text` says the coverage is under the minimum.",
        "false_not_for": "A coverage at or above the minimum, even a narrow one. A band that starts at the required value is complied with.",
        "false_examples": [
            "`required` is 1.25 and `reported_value_band` is `1-1.25`.",
            "`reported_value_band` is `<1`.",
            "`evidence_text` says the coverage for the last audited year is below the required minimum of 1.25.",
        ],
        "reason": "MSME covenant breached: debt service coverage is below the required minimum of 1.25",
    },
    "stock_statement_monthly": {
        "question": (
            "Is the covenant `stock_statement_monthly` complied with, meaning stock statements were received on time "
            "in every one of the last 6 months?"
        ),
        "true_what": (
            "`reported_value_band` reaches `required` (every month on time), and `evidence_text` says the statements "
            "were received on time for all the months."
        ),
        "true_examples": [
            "`required` is 6 and `reported_value_band` is 6.",
            "`evidence_text` says stock statements were received on time for 6 of the last 6 months.",
        ],
        "false_what": "`reported_value_band` is below `required`: at least one month's statement was late or missing.",
        "false_not_for": "All months on time. Only a month that is late or missing makes this covenant breached.",
        "false_examples": [
            "`required` is 6 and `reported_value_band` is 4.",
            "`evidence_text` says stock statements were received on time for 3 of the last 6 months.",
            "`reported_value_band` is 5: one late month is enough to breach the covenant.",
        ],
        "reason": "MSME covenant breached: stock statements were not received on time every month",
    },
    "no_unapproved_borrowing": {
        "question": (
            "Is the covenant `no_unapproved_borrowing` complied with, meaning the reported number of unapproved new "
            "loan facilities is equal to `required`, which is 0?"
        ),
        "true_what": (
            "`reported_value_band` is 0 (equal to `required`), and `evidence_text` says no new borrowing was found "
            "beyond the sanctioned facilities."
        ),
        "true_examples": [
            "`required` is 0 and `reported_value_band` is 0.",
            "`evidence_text` says the bureau and auditor check found no new borrowing beyond the sanctioned facilities.",
        ],
        "false_what": (
            "`reported_value_band` is above `required`: at least one new facility was taken without the bank's consent."
        ),
        "false_not_for": "A check that found nothing new. A `reported_value_band` of 0 is complied with.",
        "false_examples": [
            "`required` is 0 and `reported_value_band` is 1.",
            "`evidence_text` says new loan facilities were taken without the bank's consent.",
        ],
        "reason": "MSME covenant breached: new borrowing was taken without the bank's consent",
    },
    "insurance_current": {
        "question": (
            "Is the covenant `insurance_current` complied with, meaning the insurance on the financed assets is "
            "current?"
        ),
        "true_what": (
            "`reported_value_band` shows the insurance as current (true), and `evidence_text` says the insurance is "
            "current."
        ),
        "true_examples": [
            "`required` is true and `reported_value_band` is true.",
            "`evidence_text` says the insurance on the hypothecated stock is current until a later month.",
        ],
        "false_what": (
            "`reported_value_band` shows the insurance as not current (false), or `evidence_text` says the insurance "
            "has expired."
        ),
        "false_not_for": "Insurance that is current. A renewal date in the future is complied with.",
        "false_examples": [
            "`required` is true and `reported_value_band` is false.",
            "`evidence_text` says the insurance on the hypothecated stock expired and renewal is not evidenced.",
        ],
        "reason": "MSME covenant breached: insurance on the financed assets is not current",
    },
}


def _covenant_wire(covenant_id: str) -> dict:
    spec = _COVENANTS[covenant_id]
    return noul_wire(
        spec["question"],
        refer_to=["`covenants`"],
        focus=_FIND_ENTRY.format(cid=covenant_id),
        data={"covenant_id": covenant_id},
        true_what=spec["true_what"],
        true_examples=spec["true_examples"],
        false_what=spec["false_what"],
        false_not_for=spec["false_not_for"],
        false_examples=spec["false_examples"],
    )


def _covenant_present(covenant_id: str) -> Callable[[dict], bool]:
    def applies(state: dict) -> bool:
        return any(
            isinstance(entry, dict) and entry.get("covenant_id") == covenant_id for entry in state.get("covenants") or []
        )

    return applies


for _cid, _spec in _COVENANTS.items():
    register(
        QuestionDef(
            qid=f"F_covenant_{_cid}",
            module="F",
            stage="monitoring",
            qtype="noul",
            segments=_MSME,
            risk_polarity="yes_is_good",
            routing_relevant=True,
            reason_text=_spec["reason"],
            wire=_covenant_wire(_cid),
            applies=_covenant_present(_cid),
        )
    )
