"""Question packs for Jev (PLAN section 3.8): the questions, their rubrics, and the registry that serves them.

`jevloan.modules.base` holds the registry and the wire-format builders. Each pack module (`a_readiness`,
`b_fraud`, `c_appraisal`, `d_triage`, `e_memo_kfs`, `f_monitoring`) registers its questions on import;
`base.load_all()` imports them all.
"""
