"""Module E: sanction memo and Key Fact Statement consistency (PLAN sections 3.7, 3.8; spec section 5).

Asked in the `sanction_docs` stage. State: `sanction_memo`, `kfs`, `policy_grid_row` and `required_disclosures`.

* `E_memo_matches_grid`: does the memo respect the grid row for its product and ticket band. The row is put into
  `instructions.data` as structured data (the vendor's structured-instructions pattern), next to the memo's tenure,
  so that the comparison is explicit and the numbers sit side by side.
* `E_rate_math_correct`: the stated and the recomputed APR are the same number to within a few hundredths, and the
  memo text quotes the same rate as the KFS. Code checks the APR deterministically (and is authoritative); this Noul
  corroborates it and also covers the memo/KFS rate check that code does not do.
* `E_disclosures_complete`: every heading in `required_disclosures` is a section heading in `kfs.text`.
* `E_disclosure_<id>`: one Noul per required disclosure, asking only whether that section heading is present. Jev does
  not count reliably, so the per-heading questions are the dependable ones; the aggregate is the cheap summary.
  They are speculative (`routing_relevant=False`) and supply the human-readable reason.

Wording follows docs/vendor/typesafe/model-jaggedness_jev-1.13.md: literal, one comparison at a time, state
fields in backticks, and no arithmetic beyond comparing two numbers printed next to each other.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from jevloan.data.schema import load_disclosures
from jevloan.modules.base import ALL_SEGMENTS, QuestionDef, noul_wire, register

# --------------------------------------------------------------------------------------------- E_memo_matches_grid


def _memo_matches_grid_wire(state: dict) -> dict:
    """The rubric, with the applicable grid row (and the memo's tenure, next to it) as structured data."""
    row = state.get("policy_grid_row")
    if not isinstance(row, dict) or not row:
        raise ValueError("E_memo_matches_grid needs `policy_grid_row` in the sanction_docs state")
    data: dict = {"grid_row": copy.deepcopy(row)}
    memo = state.get("sanction_memo")
    if isinstance(memo, dict) and "tenure_months" in memo:
        data["memo_tenure_months"] = memo["tenure_months"]
    return noul_wire(
        "Does the sanction memo satisfy the policy grid row in `data.grid_row`? Both of these must be true. "
        "(1) `data.memo_tenure_months` is the same as or smaller than `data.grid_row.max_tenure_months`. "
        "(2) Each condition in `data.grid_row.required_conditions` is also stated in `sanction_memo.conditions`.",
        refer_to=[
            "`sanction_memo.tenure_months`",
            "`sanction_memo.conditions`",
            "`sanction_memo.text`",
            "`policy_grid_row`",
        ],
        focus=(
            "`data.grid_row` is the grid row for this memo's product and ticket band (the same row as `policy_grid_row`). "
            "Check the tenure first. Then go through `data.grid_row.required_conditions` one condition at a time and look "
            "for the same requirement in `sanction_memo.conditions` or `sanction_memo.text`. The wording may differ "
            "slightly when the meaning is the same. Extra conditions in the memo that the grid row does not list are "
            "allowed. Ignore `data.grid_row.max_ltv_pct`: the memo does not state a loan-to-value limit."
        ),
        data=data,
        true_what=(
            "The memo tenure is equal to or below `data.grid_row.max_tenure_months`, and every required condition in "
            "the grid row has a matching condition in the memo, worded the same or with the same meaning."
        ),
        true_examples=[
            "`data.memo_tenure_months` is 60, `data.grid_row.max_tenure_months` is 84, and every entry of `data.grid_row.required_conditions` has a matching entry in `sanction_memo.conditions`.",
            "`data.memo_tenure_months` is 84 and `data.grid_row.max_tenure_months` is 84: a tenure equal to the maximum is allowed.",
            "`sanction_memo.conditions` lists all the required conditions and adds one more condition that the grid row does not list.",
        ],
        false_what=(
            "The memo tenure is larger than `data.grid_row.max_tenure_months`, or at least one required condition in "
            "the grid row has no matching condition in the memo."
        ),
        false_not_for=(
            "A memo whose condition wording differs slightly from the `text` in the grid row but means the same thing, "
            "and a memo that has extra conditions the grid row does not list. Both of those still satisfy the grid row."
        ),
        false_examples=[
            "`data.memo_tenure_months` is 96 and `data.grid_row.max_tenure_months` is 84.",
            "`data.grid_row.required_conditions` includes a guarantor condition, but no entry of `sanction_memo.conditions` mentions a guarantor.",
            "The tenure is within the maximum, but the equitable mortgage condition in the grid row is not in `sanction_memo.conditions`.",
        ],
    )


register(
    QuestionDef(
        qid="E_memo_matches_grid",
        module="E",
        stage="sanction_docs",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_good",
        routing_relevant=True,
        reason_text="Sanction memo does not match the policy grid: the tenure is over the limit or a required condition is missing",
        build=_memo_matches_grid_wire,
    )
)

# --------------------------------------------------------------------------------------------- E_rate_math_correct

register(
    QuestionDef(
        qid="E_rate_math_correct",
        module="E",
        stage="sanction_docs",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_good",
        routing_relevant=True,
        reason_text="Stated APR in the KFS does not match the recomputed APR, or the memo and the KFS quote different rates",
        wire=noul_wire(
            "Are both of these true? "
            "(1) `kfs.apr_stated_pct` and `kfs.apr_recomputed_pct` are the same number, apart from a difference of a few hundredths. "
            "(2) The rate written after the word rate in `sanction_memo.text` is the same number as `kfs.rate_pct`.",
            refer_to=[
                "`kfs.apr_stated_pct`",
                "`kfs.apr_recomputed_pct`",
                "`kfs.rate_pct`",
                "`sanction_memo.text`",
            ],
            focus=(
                "Compare the two APR values first: both are written with two decimals in `kfs`. The APR includes fees, so "
                "it is normally higher than `kfs.rate_pct`; never compare the APR with the rate. The second comparison is "
                "only between the rate quoted in `sanction_memo.text` and `kfs.rate_pct`."
            ),
            true_what=(
                "`kfs.apr_stated_pct` is equal to `kfs.apr_recomputed_pct` or differs from it only by a few hundredths, "
                "and the memo text quotes the same rate as `kfs.rate_pct`."
            ),
            true_examples=[
                "`kfs.apr_stated_pct` is 11.42 and `kfs.apr_recomputed_pct` is 11.44; `sanction_memo.text` says rate 10.50% and `kfs.rate_pct` is 10.5.",
                "`kfs.apr_stated_pct` is 9.87 and `kfs.apr_recomputed_pct` is 9.87; the memo text and `kfs.rate_pct` both show 9.25.",
            ],
            false_what=(
                "`kfs.apr_stated_pct` differs from `kfs.apr_recomputed_pct` by half a percentage point or more, or the "
                "memo text quotes a rate that is a different number from `kfs.rate_pct`."
            ),
            false_not_for=(
                "A difference of a few hundredths between the two APR values (for example 11.42 and 11.44), and an APR "
                "that is higher than `kfs.rate_pct` only because it includes fees."
            ),
            false_examples=[
                "`kfs.apr_stated_pct` is 10.50 and `kfs.apr_recomputed_pct` is 11.63: the stated APR is a plain rate that leaves out the fees.",
                "`kfs.apr_stated_pct` is 12.90 and `kfs.apr_recomputed_pct` is 12.10.",
                "The two APR values agree, but `sanction_memo.text` says rate 10.50% while `kfs.rate_pct` is 11.25.",
            ],
        ),
    )
)

# --------------------------------------------------------------------------------------------- E_disclosures_complete

register(
    QuestionDef(
        qid="E_disclosures_complete",
        module="E",
        stage="sanction_docs",
        qtype="noul",
        segments=ALL_SEGMENTS,
        risk_polarity="yes_is_good",
        routing_relevant=True,
        reason_text="KFS is missing a required disclosure section",
        wire=noul_wire(
            "Does `kfs.text` contain a numbered section for every heading listed in `required_disclosures`?",
            refer_to=["`kfs.text`", "`required_disclosures`"],
            focus=(
                "Take the headings in `required_disclosures` one at a time and look for each one as the heading of a "
                "numbered section in `kfs.text`. Only the headings matter: do not judge what a section says."
            ),
            true_what=(
                "Every `heading` in `required_disclosures` is found in `kfs.text` as the heading of a numbered section, "
                "in any order."
            ),
            true_examples=[
                "Each `heading` in `required_disclosures` appears in `kfs.text` in the form \"<number>. <heading>:\" followed by the section's content.",
                "The sections in `kfs.text` are in a different order from `required_disclosures`, but every heading is present.",
            ],
            false_what="At least one `heading` in `required_disclosures` does not appear as a section heading in `kfs.text`.",
            false_not_for="A section that has its heading but only a short or thin content. That section is present.",
            false_examples=[
                "`required_disclosures` lists the heading \"Grievance redressal officer\", but `kfs.text` has no such heading.",
                "`required_disclosures` lists \"Cooling-off period\", and `kfs.text` mentions cooling off only inside another section, with no heading of its own.",
                "The numbered sections of `kfs.text` run on without a \"Penal charges\" heading that `required_disclosures` lists.",
            ],
        ),
    )
)

# --------------------------------------------------------------------------------------------- E_disclosure_<id>


def _disclosure_wire(heading: str) -> dict:
    return noul_wire(
        f'Does `kfs.text` contain a section headed "{heading}"?',
        refer_to=["`kfs.text`"],
        focus=(
            "Only the section heading matters. Look for it as the title of a numbered section, written like "
            "\"<number>. <heading>:\" followed by the section's content. Do not judge what the section says, and do not "
            "look at the other sections."
        ),
        data={"section_heading": heading},
        true_what=f'`kfs.text` has a numbered section whose heading is "{heading}", followed by that section\'s content.',
        true_examples=[
            f'`kfs.text` contains "<number>. {heading}:" followed by the content of the section.',
            f'The heading "{heading}" is present and the content after it is only one short sentence.',
        ],
        false_what=f'No section of `kfs.text` has the heading "{heading}".',
        false_not_for=f'A section headed "{heading}" whose content is short or incomplete. That section is present.',
        false_examples=[
            f'`kfs.text` goes from the section before to the section after with no "{heading}" heading between them.',
            f'The topic is mentioned in passing inside another section, but no section carries the heading "{heading}".',
        ],
    )


def _disclosure_builder(disclosure_id: str, fallback_heading: str) -> Callable[[dict], dict]:
    def build(state: dict) -> dict:
        heading = fallback_heading
        for entry in state.get("required_disclosures") or []:
            if isinstance(entry, dict) and entry.get("id") == disclosure_id and entry.get("heading"):
                heading = entry["heading"]  # the state's heading is the policy in force
        return _disclosure_wire(heading)

    return build


def _disclosure_applies(disclosure_id: str) -> Callable[[dict], bool]:
    def applies(state: dict) -> bool:
        listed = state.get("required_disclosures")
        if not listed:  # a state that does not carry the list: ask for every disclosure the policy file names
            return True
        return any(isinstance(entry, dict) and entry.get("id") == disclosure_id for entry in listed)

    return applies


for _d in load_disclosures():
    register(
        QuestionDef(
            qid=f"E_disclosure_{_d['id']}",
            module="E",
            stage="sanction_docs",
            qtype="noul",
            segments=ALL_SEGMENTS,
            risk_polarity="yes_is_good",
            routing_relevant=False,
            reason_text=f'KFS is missing the "{_d["heading"]}" disclosure',
            build=_disclosure_builder(_d["id"], _d["heading"]),
            applies=_disclosure_applies(_d["id"]),
        )
    )
