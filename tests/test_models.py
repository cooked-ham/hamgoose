from hamgoose.models import Feature, FeatureResult, FeatureStatus, Mission, MissionStatus, ValidationResult, Finding


def test_feature_serialization_roundtrip():
    f = Feature(id="F001", title="t", milestone="MS01", status=FeatureStatus.COMPLETED,
                dependencies=["F000"], acceptance_criteria=["a"])
    f.result = FeatureResult(summary="s", changed_files=["x.py"], tests=["pytest"])
    d = f.to_dict()
    f2 = Feature.from_dict(d)
    assert f2.status == FeatureStatus.COMPLETED
    assert f2.result.changed_files == ["x.py"]
    assert f2.result.summary == "s"


def test_mission_serialization_roundtrip():
    m = Mission(id="M1", goal="g", repo="/tmp/x", status=MissionStatus.RUNNING)
    m.features["F001"] = Feature(id="F001", title="t", milestone="MS01")
    from hamgoose.models import Milestone

    m.milestones["MS01"] = Milestone(id="MS01", objective="o", features=["F001"])
    m.final_validation.append(ValidationResult(kind="final", passed=True, findings=[Finding(feature="F001", criterion="c", problem="p")]))
    d = m.to_dict()
    m2 = Mission.from_dict(d)
    assert m2.features["F001"].id == "F001"
    assert m2.final_validation[0].findings[0].feature == "F001"
