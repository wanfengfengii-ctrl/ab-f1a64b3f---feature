"""validation.validate_payload 校验测试：各类非法输入的首个原因。"""

from __future__ import annotations

import pytest

from app.validation import ValidationFailure, validate_payload


def valid_payload(**overrides):
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ]
    }
    payload.update(overrides)
    return payload


def test_valid_payload_accepted() -> None:
    segments = validate_payload(valid_payload())
    assert [s.id for s in segments] == [1, 2, 3]
    assert segments[0].marks == (1, 2, 3, 4)


def test_top_level_must_be_object() -> None:
    with pytest.raises(ValidationFailure, match="JSON 对象"):
        validate_payload([1, 2, 3])


def test_missing_segments() -> None:
    with pytest.raises(ValidationFailure, match="缺少 segments"):
        validate_payload({})


def test_segments_must_be_array() -> None:
    with pytest.raises(ValidationFailure, match="段落数组"):
        validate_payload({"segments": {"id": 1}})


@pytest.mark.parametrize("count", [0, 1, 2])
def test_too_few_segments(count: int) -> None:
    payload = {"segments": valid_payload()["segments"][:count]}
    with pytest.raises(ValidationFailure, match="至少需要 3 段"):
        validate_payload(payload)


def test_too_many_segments() -> None:
    payload = {
        "segments": [
            {"id": i, "marks": [1, 2, 3, 4], "types": ["major"] * 4}
            for i in range(7)
        ]
    }
    with pytest.raises(ValidationFailure, match="最多允许 6 段"):
        validate_payload(payload)


def test_duplicate_ids_rejected() -> None:
    payload = valid_payload()
    payload["segments"][2]["id"] = 1
    with pytest.raises(ValidationFailure, match="编号 1 重复"):
        validate_payload(payload)


def test_non_integer_id_rejected() -> None:
    payload = valid_payload()
    payload["segments"][0]["id"] = "A1"
    with pytest.raises(ValidationFailure, match="编号 id 必须是整数"):
        validate_payload(payload)


def test_boolean_id_rejected() -> None:
    payload = valid_payload()
    payload["segments"][0]["id"] = True
    with pytest.raises(ValidationFailure, match="编号 id 必须是整数"):
        validate_payload(payload)


def test_negative_ids_allowed() -> None:
    payload = valid_payload()
    for i, seg in enumerate(payload["segments"]):
        seg["id"] = -(i + 1)
    segments = validate_payload(payload)
    assert [s.id for s in segments] == [-1, -2, -3]


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_too_few_marks(count: int) -> None:
    payload = valid_payload()
    seg = payload["segments"][0]
    marks = [1, 2, 3, 4][:count]
    seg["marks"] = marks
    seg["types"] = ["major"] * count
    with pytest.raises(ValidationFailure, match="至少需要 4 个"):
        validate_payload(payload)


def test_too_many_marks() -> None:
    payload = valid_payload()
    seg = payload["segments"][0]
    seg["marks"] = list(range(11))
    seg["types"] = ["major"] * 11
    with pytest.raises(ValidationFailure, match="最多允许 10 个"):
        validate_payload(payload)


def test_non_integer_mark_rejected() -> None:
    payload = valid_payload()
    payload["segments"][1]["marks"][0] = 4.5
    with pytest.raises(ValidationFailure, match="不是整数"):
        validate_payload(payload)


def test_boolean_mark_rejected() -> None:
    payload = valid_payload()
    payload["segments"][1]["marks"][0] = False
    with pytest.raises(ValidationFailure, match="不是整数"):
        validate_payload(payload)


def test_marks_must_be_strictly_increasing() -> None:
    payload = valid_payload()
    payload["segments"][0]["marks"] = [1, 2, 2, 4]
    with pytest.raises(ValidationFailure, match="严格递增"):
        validate_payload(payload)


def test_marks_decreasing_rejected() -> None:
    payload = valid_payload()
    payload["segments"][0]["marks"] = [4, 3, 2, 1]
    with pytest.raises(ValidationFailure, match="严格递增"):
        validate_payload(payload)


def test_marks_types_length_mismatch() -> None:
    payload = valid_payload()
    payload["segments"][0]["types"] = ["major"] * 3
    with pytest.raises(ValidationFailure, match="不一致"):
        validate_payload(payload)


def test_unknown_type_rejected() -> None:
    payload = valid_payload()
    payload["segments"][0]["types"][2] = "fathom"
    with pytest.raises(ValidationFailure, match="不可辨识"):
        validate_payload(payload)


def test_first_reason_reported_in_entry_order() -> None:
    # 同时存在段数合法但第一段即非法的问题，应报第一段的原因。
    payload = valid_payload()
    payload["segments"][0]["id"] = 1
    payload["segments"][1]["id"] = 1  # 重复
    payload["segments"][1]["marks"] = [1, 1, 1, 1]  # 也非递增
    with pytest.raises(ValidationFailure) as exc_info:
        validate_payload(payload)
    assert "编号 1 重复" in str(exc_info.value)
