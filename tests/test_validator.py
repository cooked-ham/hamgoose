from hamgoose.models import Mission, Milestone, ValidationResult
from hamgoose.validator import _parse_validation
from hamgoose.controller import MissionController


def test_parse_validation_treats_string_false_as_false():
    text = '```json\n{"passed": "false", "severity": "major", "summary": "fail"}\n```'
    result = _parse_validation("scrutiny", text)
    assert result.passed is False


def test_validation_reports_use_distinct_files(tmp_path):
    mission = Mission(
        id="M1", goal="g", repo=str(tmp_path),
        milestones={"MS01": Milestone(id="MS01", objective="m")},
    )
    ctl = MissionController(str(tmp_path))
    first = ValidationResult(kind="scrutiny", passed=False, summary="first")
    second = ValidationResult(kind="user_testing", passed=True, summary="second")
    mission.milestones["MS01"].validation.append(first)
    ctl._save_validation(mission, "MS01", first)
    mission.milestones["MS01"].validation.append(second)
    ctl._save_validation(mission, "MS01", second)
    files = sorted(p.name for p in (tmp_path / ".goose" / "hamgoose" / "M1" / "validation").glob("MS01-*.json"))
    assert files == ["MS01-1.json", "MS01-2.json"]
