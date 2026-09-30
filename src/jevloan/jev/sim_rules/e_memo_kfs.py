"""Simulator rules for module E: memo and KFS consistency (PLAN sections 3.4, 3.8).

Each rule reads only the sanction_docs state: the memo's tenure and conditions against `policy_grid_row`, the two APR
fields, the memo's quoted rate against `kfs.rate_pct`, and the section headings in `kfs.text`. The simulator hands a rule the
question but not its id, so `E_disclosure_*` reads the heading it was asked about from `instructions.data.section_heading`,
which is the same thing real Jev is told.

Accuracy is about 0.9 to 0.94 (`noisy_p`): the answer follows the comparison correctly most of the time and misreads it
now and then with real confidence, the way a model does.
"""

from __future__ import annotations

import random
import re

from jevloan.jev.sim_rules import get, noisy_p, noul, sim_rule

_STOP = frozenset({"the", "and", "for", "with", "from", "that", "this", "over", "under", "above", "before", "where", "which"})
_MEMO_RATE = re.compile(r"\brate\s+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3 and w not in _STOP}


def _condition_stated(required: str, conditions: list[str], memo_text: str) -> bool:
    """True if a memo condition (or the memo text) states the requirement, allowing slightly different wording."""
    wanted = _words(required)
    if not wanted:
        return True
    for candidate in [*conditions, memo_text]:
        if len(wanted & _words(str(candidate))) >= 0.8 * len(wanted):
            return True
    return False


def memo_matches_grid(state: dict) -> bool | None:
    """The comparison the rubric asks for; None if the state carries no grid row to compare with."""
    row = get(state, "policy_grid_row")
    if not isinstance(row, dict):
        return None
    tenure, limit = get(state, "sanction_memo.tenure_months"), row.get("max_tenure_months")
    if isinstance(tenure, (int, float)) and isinstance(limit, (int, float)) and tenure > limit:
        return False
    conditions = [str(c) for c in get(state, "sanction_memo.conditions", []) or []]
    memo_text = str(get(state, "sanction_memo.text", "") or "")
    for required in row.get("required_conditions") or []:
        text = required.get("text", "") if isinstance(required, dict) else str(required)
        if not _condition_stated(text, conditions, memo_text):
            return False
    return True


def rate_math_correct(state: dict) -> bool | None:
    stated, recomputed = get(state, "kfs.apr_stated_pct"), get(state, "kfs.apr_recomputed_pct")
    if not isinstance(stated, (int, float)) or not isinstance(recomputed, (int, float)):
        return None
    if abs(stated - recomputed) >= 0.3:  # a few hundredths is a match, half a point or more is not
        return False
    rate = get(state, "kfs.rate_pct")
    quoted = _MEMO_RATE.search(str(get(state, "sanction_memo.text", "") or ""))
    if quoted and isinstance(rate, (int, float)) and abs(float(quoted.group(1)) - rate) > 0.006:
        return False
    return True


def heading_present(state: dict, heading: str) -> bool:
    return heading.casefold() in str(get(state, "kfs.text", "") or "").casefold()


def disclosures_complete(state: dict) -> bool | None:
    listed = [d.get("heading") for d in get(state, "required_disclosures", []) or [] if isinstance(d, dict) and d.get("heading")]
    if not listed:
        return None
    return all(heading_present(state, heading) for heading in listed)


def _answer(signal: bool | None, rng: random.Random, accuracy: float) -> dict:
    return noul(0.5 if signal is None else noisy_p(signal, rng, accuracy=accuracy))


@sim_rule("E_memo_matches_grid")
def e_memo_matches_grid(state: dict, question: dict, rng: random.Random) -> dict:
    return _answer(memo_matches_grid(state), rng, 0.93)


@sim_rule("E_rate_math_correct")
def e_rate_math_correct(state: dict, question: dict, rng: random.Random) -> dict:
    return _answer(rate_math_correct(state), rng, 0.93)


@sim_rule("E_disclosures_complete")
def e_disclosures_complete(state: dict, question: dict, rng: random.Random) -> dict:
    return _answer(disclosures_complete(state), rng, 0.90)  # checking eight headings at once is harder than one


@sim_rule("E_disclosure_*")
def e_disclosure(state: dict, question: dict, rng: random.Random) -> dict:
    heading = get(question, "instructions.data.section_heading")
    if not heading:
        return noul(0.5)  # a question without its heading tells the reader nothing to look for
    return _answer(heading_present(state, str(heading)), rng, 0.94)
