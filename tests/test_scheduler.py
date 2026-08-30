from hamgoose import scheduler
from hamgoose.models import Feature, FeatureStatus, Milestone, Mission


def _feat(fid, deps=(), paths=(), st=FeatureStatus.PENDING, prio=100):
    return Feature(id=fid, title="t" + fid, dependencies=list(deps), expected_paths=list(paths),
                   milestone="MS01", status=st, priority=prio)


def _mission(feats):
    m = Mission(id="M1", goal="g", repo=".")
    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=list(feats.keys()))
    for k, v in feats.items():
        m.features[k] = v
    return m


def test_ready_only_when_deps_complete():
    m = _mission({"F001": _feat("F001", st=FeatureStatus.COMPLETED),
                  "F002": _feat("F002", deps=["F001"]),
                  "F003": _feat("F003", deps=["F002"])})
    ready = scheduler.ready_features(m, "MS01")
    assert [f.id for f in ready] == ["F002"]


def test_conflicting_paths_run_sequentially():
    m = _mission({"F001": _feat("F001", paths=["a"]),
                  "F002": _feat("F002", paths=["a/sub"]),
                  "F003": _feat("F003", paths=["b"])})
    ready = scheduler.ready_features(m, "MS01")
    batch = scheduler.select_batch(ready, 2)
    ids = [f.id for f in batch]
    assert len(batch) <= 2
    # F002 overlaps F001 so it cannot be in the same batch as F001
    assert not ({"F001", "F002"} <= set(ids))


def test_concurrency_ceiling():
    m = _mission({("F%03d" % i): _feat("F%03d" % i, paths=["p%d" % i]) for i in range(1, 4)})
    ready = scheduler.ready_features(m, "MS01")
    assert len(scheduler.select_batch(ready, 2)) == 2
    assert len(scheduler.select_batch(ready, 1)) == 1


def test_priority_ordering():
    m = _mission({"F001": _feat("F001", prio=50), "F002": _feat("F002", prio=10)})
    ready = scheduler.ready_features(m, "MS01")
    batch = scheduler.select_batch(ready, 2)
    assert batch[0].id == "F002"  # lower number = higher priority


def test_cycle_detected():
    m = _mission({"F001": _feat("F001", deps=["F002"]), "F002": _feat("F002", deps=["F001"])})
    assert scheduler.find_cycle(m.features)
