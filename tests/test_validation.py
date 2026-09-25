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
    segments, anchors = validate_payload(valid_payload())
    assert [s.id for s in segments] == [1, 2, 3]
    assert segments[0].marks == (1, 2, 3, 4)
    assert anchors == []


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
    segments, anchors = validate_payload(payload)
    assert [s.id for s in segments] == [-1, -2, -3]
    assert anchors == []


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


# ---------------------------------------------------------------------------
# 定位锚点校验
# ---------------------------------------------------------------------------


def anchor_payload(anchors):
    payload = valid_payload()
    payload["anchors"] = anchors
    return payload


def test_no_anchors_field_returns_empty() -> None:
    _, anchors = validate_payload(valid_payload())
    assert anchors == []


def test_empty_anchor_list_treated_as_omitted() -> None:
    _, anchors = validate_payload(anchor_payload([]))
    assert anchors == []


@pytest.mark.parametrize("count", [1])
def test_too_few_anchors(count: int) -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": 0, "archived_depth": 5}
        for _ in range(count)
    ])
    with pytest.raises(ValidationFailure, match="至少需要 2 个"):
        validate_payload(payload)


def test_too_many_anchors() -> None:
    payload = valid_payload()
    # 三段各 4 刻度，合法引用不超过 12 个，构造 5 个互不相同引用
    payload["anchors"] = [
        {"segment_id": 1, "position": 0, "archived_depth": 0},
        {"segment_id": 1, "position": 1, "archived_depth": 0},
        {"segment_id": 1, "position": 2, "archived_depth": 0},
        {"segment_id": 1, "position": 3, "archived_depth": 0},
        {"segment_id": 2, "position": 0, "archived_depth": 0},
    ]
    with pytest.raises(ValidationFailure, match="最多允许 4 个"):
        validate_payload(payload)


def test_anchors_must_be_array() -> None:
    with pytest.raises(ValidationFailure, match="定位锚点数组"):
        validate_payload(anchor_payload({"segment_id": 1}))


def test_anchor_referencing_missing_segment() -> None:
    payload = anchor_payload([
        {"segment_id": 99, "position": 0, "archived_depth": 1},
        {"segment_id": 1, "position": 0, "archived_depth": 1},
    ])
    with pytest.raises(ValidationFailure, match="不存在的段 99"):
        validate_payload(payload)


def test_anchor_bad_position() -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": 4, "archived_depth": 1},  # 只有位置 0-3
        {"segment_id": 2, "position": 0, "archived_depth": 1},
    ])
    with pytest.raises(ValidationFailure, match="刻度位置 4 不存在"):
        validate_payload(payload)


def test_anchor_negative_position() -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": -1, "archived_depth": 1},
        {"segment_id": 2, "position": 0, "archived_depth": 1},
    ])
    with pytest.raises(ValidationFailure, match="刻度位置 -1 不存在"):
        validate_payload(payload)


def test_anchor_duplicate_same_mark_rejected() -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": 2, "archived_depth": 3},
        {"segment_id": 1, "position": 2, "archived_depth": 9},  # 重复引用同一刻度
        {"segment_id": 2, "position": 0, "archived_depth": 4},
    ])
    with pytest.raises(ValidationFailure, match="重复引用段 1 的同一刻度位置 2"):
        validate_payload(payload)


def test_anchors_must_cover_two_segments() -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": 0, "archived_depth": 1},
        {"segment_id": 1, "position": 1, "archived_depth": 2},
    ])
    with pytest.raises(ValidationFailure, match="覆盖至少两段"):
        validate_payload(payload)


def test_anchor_missing_fields_first_reason() -> None:
    payload = anchor_payload([
        {"position": 0, "archived_depth": 1},
        {"segment_id": 2, "position": 0, "archived_depth": 1},
    ])
    with pytest.raises(ValidationFailure, match="缺少段落编号 segment_id"):
        validate_payload(payload)


def test_anchor_non_integer_depth_rejected() -> None:
    payload = anchor_payload([
        {"segment_id": 1, "position": 0, "archived_depth": 3.5},
        {"segment_id": 2, "position": 0, "archived_depth": 1},
    ])
    with pytest.raises(ValidationFailure, match="档案原始水深 archived_depth 必须是整数"):
        validate_payload(payload)


def test_valid_anchors_parsed_in_order() -> None:
    payload = anchor_payload([
        {"segment_id": 3, "position": 2, "archived_depth": -7},
        {"segment_id": 1, "position": 0, "archived_depth": 0},
    ])
    _, anchors = validate_payload(payload)
    assert [(a.index, a.segment_id, a.position, a.archived_depth) for a in anchors] == [
        (0, 3, 2, -7),
        (1, 1, 0, 0),
    ]
