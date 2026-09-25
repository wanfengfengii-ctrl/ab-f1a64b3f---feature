"""定位锚点请求体校验测试：首个原因、数量/覆盖约束与重复引用。"""

from __future__ import annotations

import pytest

from app.validation import ValidationFailure, validate_anchors, validate_payload


def segments_with(count: int = 3):
    payload = {
        "segments": [
            {
                "id": i + 1,
                "marks": [1 + 3 * i, 2 + 3 * i, 3 + 3 * i, 4 + 3 * i],
                "types": ["major"] * 4,
            }
            for i in range(count)
        ]
    }
    return payload["segments"]


def parse(anchors_raw, segments_raw=None):
    segments_raw = segments_with() if segments_raw is None else segments_raw
    segments = validate_payload({"segments": segments_raw})
    return validate_anchors({"segments": segments_raw, "anchors": anchors_raw}, segments)


def test_no_anchors_field_returns_empty() -> None:
    segments = validate_payload({"segments": segments_with()})
    assert validate_anchors({"segments": segments_with()}, segments) == []


def test_null_anchors_treated_as_absent() -> None:
    assert parse(None) == []


def test_anchors_must_be_array() -> None:
    with pytest.raises(ValidationFailure, match="定位锚点数组"):
        parse({"segment_id": 1, "position": 0, "depth": 1})


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_anchors(count: int) -> None:
    anchors = [
        {"segment_id": 1, "position": 0, "depth": 100},
        {"segment_id": 2, "position": 0, "depth": 103},
    ][:count]
    with pytest.raises(ValidationFailure, match="至少需要 2 个"):
        parse(anchors)


def test_too_many_anchors() -> None:
    anchors = [
        {"segment_id": 1, "position": 0, "depth": 100},
        {"segment_id": 2, "position": 0, "depth": 103},
        {"segment_id": 3, "position": 0, "depth": 106},
        {"segment_id": 1, "position": 1, "depth": 101},
        {"segment_id": 2, "position": 1, "depth": 104},
    ]
    with pytest.raises(ValidationFailure, match="最多允许 4 个"):
        parse(anchors)


def test_anchor_must_be_object() -> None:
    with pytest.raises(ValidationFailure, match="锚点必须是对象"):
        parse([42, {"segment_id": 2, "position": 0, "depth": 1}])


def test_missing_segment_id() -> None:
    with pytest.raises(ValidationFailure, match="缺少段落引用"):
        parse([{"position": 0, "depth": 1}, {"segment_id": 2, "position": 0, "depth": 2}])


def test_segment_id_must_be_int() -> None:
    with pytest.raises(ValidationFailure, match="segment_id 必须是整数"):
        parse(
            [
                {"segment_id": "1", "position": 0, "depth": 1},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_segment_id_boolean_rejected() -> None:
    with pytest.raises(ValidationFailure, match="segment_id 必须是整数"):
        parse(
            [
                {"segment_id": True, "position": 0, "depth": 1},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_referenced_segment_must_exist() -> None:
    anchors = [
        {"segment_id": 9, "position": 0, "depth": 100},
        {"segment_id": 1, "position": 0, "depth": 100},
    ]
    with pytest.raises(ValidationFailure, match="引用的段落编号 9 不存在"):
        parse(anchors)


def test_missing_position() -> None:
    with pytest.raises(ValidationFailure, match="缺少该段刻度位置"):
        parse(
            [
                {"segment_id": 1, "depth": 1},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_position_must_be_int() -> None:
    with pytest.raises(ValidationFailure, match="position 必须是整数"):
        parse(
            [
                {"segment_id": 1, "position": 1.5, "depth": 1},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_negative_position_rejected() -> None:
    with pytest.raises(ValidationFailure, match="没有位置 -1"):
        parse(
            [
                {"segment_id": 1, "position": -1, "depth": 1},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_position_out_of_range_reports_first_reason() -> None:
    # 段 2 只有 4 个刻度（位置 0–3）
    with pytest.raises(ValidationFailure, match="段 2 没有位置 4"):
        parse(
            [
                {"segment_id": 2, "position": 4, "depth": 1},
                {"segment_id": 1, "position": 0, "depth": 2},
            ]
        )


def test_missing_depth() -> None:
    with pytest.raises(ValidationFailure, match="缺少档案原始水深"):
        parse(
            [
                {"segment_id": 1, "position": 0},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_depth_must_be_int() -> None:
    with pytest.raises(ValidationFailure, match="档案原始水深 depth 必须是整数"):
        parse(
            [
                {"segment_id": 1, "position": 0, "depth": 12.5},
                {"segment_id": 2, "position": 0, "depth": 2},
            ]
        )


def test_duplicate_same_mark_reference_rejected() -> None:
    with pytest.raises(ValidationFailure, match="重复引用段 1 位置 0"):
        parse(
            [
                {"segment_id": 1, "position": 0, "depth": 100},
                {"segment_id": 1, "position": 0, "depth": 101},
            ]
        )


def test_two_anchors_on_same_segment_allowed_if_marks_differ() -> None:
    # 同一段不同刻度可以同时作为锚点（此时覆盖段数不足，仍须至少两段）：
    with pytest.raises(ValidationFailure, match="至少需要覆盖两段"):
        parse(
            [
                {"segment_id": 1, "position": 0, "depth": 100},
                {"segment_id": 1, "position": 1, "depth": 101},
            ]
        )
    # 补上另一段即合法；同段两锚点的语义矛盾由求解阶段裁决。
    anchors = parse(
        [
            {"segment_id": 1, "position": 0, "depth": 100},
            {"segment_id": 1, "position": 1, "depth": 101},
            {"segment_id": 2, "position": 0, "depth": 103},
        ]
    )
    assert [(a.segment_id, a.position, a.depth) for a in anchors] == [
        (1, 0, 100),
        (1, 1, 101),
        (2, 0, 103),
    ]


def test_first_reason_in_entry_order() -> None:
    # 第一个锚点引用不存在的段，第二个锚点本身也非法：必须报第一个。
    with pytest.raises(ValidationFailure) as exc_info:
        parse(
            [
                {"segment_id": 99, "position": 0, "depth": 1},
                {"segment_id": 1, "position": 100, "depth": 2},
            ]
        )
    assert "99" in str(exc_info.value)


def test_valid_anchors_parsed_in_order() -> None:
    anchors = parse(
        [
            {"segment_id": 3, "position": 2, "depth": -15},
            {"segment_id": 1, "position": 0, "depth": 200},
        ]
    )
    assert anchors[0].segment_id == 3
    assert anchors[0].position == 2
    assert anchors[0].depth == -15
    assert anchors[1].segment_id == 1
