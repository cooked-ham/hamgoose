from hamgoose import plan
from hamgoose.models import Feature, Milestone, Mission


def _m(feats):
    m = Mission(id="M1", goal="g", repo=".")
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=list(feats.keys()))
    for k, v in feats.items():
        v.milestone = "MS01"
        m.features[k] = v
    return m


def _f(fid, title="Do the thing", deps=(), criteria=["c"]):
    return Feature(id=fid, title=title, dependencies=list(deps), acceptance_criteria=list(criteria),
                   milestone="MS01")


def test_cycle_detected():
    m = _m({"F001": _f("F001", deps=["F002"]), "F002": _f("F002", deps=["F001"])})
    assert any("cycle" in i for i in plan.validate_plan(m))


def test_dangling_dependency():
    m = _m({"F001": _f("F001", deps=["F999"])})
    assert any("unknown feature" in i for i in plan.validate_plan(m))


def test_self_dependency_and_fix():
    m = _m({"F001": _f("F001", deps=["F001"])})
    assert any("self-dependency" in i for i in plan.validate_plan(m))
    plan.fix_plan(m)
    assert m.features["F001"].dependencies == []


def test_vague_title():
    m = _m({"F001": _f("F001", title="update", criteria=[])})
    assert any("vague" in i for i in plan.validate_plan(m))


def test_micro_feature():
    m = _m({"F001": _f("F001", title="create file main.py", criteria=["c"])})
    assert any("micro-feature" in i for i in plan.validate_plan(m))


def test_good_plan_no_structural_issues():
    m = _m({"F001": _f("F001"), "F002": _f("F002", deps=["F001"])})
    issues = [i for i in plan.validate_plan(m) if "cycle" in i or "self-dependency" in i or "unknown feature" in i]
    assert issues == []
