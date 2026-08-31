"""HG-05: code-first worker prompt with hard exploration budget; the output
contract block must stay byte-identical (the parser depends on it)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from harness import F, MS, make_controller  # noqa: E402

from hamgoose import prompting  # noqa: E402


def _mission(tmp_path):
    ctl = make_controller(tmp_path)
    m = ctl.create_mission("test goal")
    ctl.plan(m.id, features=[F("F001", "implement the handler")], milestones=[MS("MS01", "obj")])
    return ctl._get(m.id)


def test_budget_lines_present(tmp_path):
    m = _mission(tmp_path)
    p = prompting.worker_prompt(m, m.features["F001"], {"enabled": False}, "")
    assert "Analysis phase: at most 5 bullets" in p
    assert "BEFORE any second analysis pass" in p
    assert "minimal working change with a real test" in p.lower() or \
        "A minimal working change with a real test" in p
    assert "implement the minimal version and say so" in p


def test_output_contract_block_is_verbatim(tmp_path):
    m = _mission(tmp_path)
    p = prompting.worker_prompt(m, m.features["F001"], {"enabled": False}, "")
    assert prompting._WORKER_CONTRACT in p
    # schema keys the parser relies on
    for key in ('"status"', '"summary"', '"changed_files"', '"tests"', '"notes"', '"blocked_reason"'):
        assert key in p


def test_no_limit_resume_block_on_first_attempt(tmp_path):
    m = _mission(tmp_path)
    p = prompting.worker_prompt(m, m.features["F001"], {"enabled": False}, "")
    assert "RESUMING A CUT-OFF RUN" not in p
