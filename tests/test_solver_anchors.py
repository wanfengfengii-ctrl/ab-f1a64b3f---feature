"""锚定裁决测试：联合选择段落顺序与接缝平移方案、逐锚点精确换算、首个阻断锚点。"""

from __future__ import annotations

import pytest

from app.solver import AdjudicationError, Anchor, Segment, adjudicate


def seg(seg_id: int, marks: list[int], types_: list[str] | None = None) -> Segment:
    if types_ is None:
        types_ = ["major"] * len(marks)
    return Segment(id=seg_id, marks=tuple(marks), types=tuple(types_))


def anc(index: int, segment_id: int, position: int, depth: int) -> Anchor:
    return Anchor(
        index=index,
        segment_id=segment_id,
        position=position,
        archived_depth=depth,
    )


def test_no_anchors_remains_legacy_equivalent() -> None:
    a = seg(1, [1, 4, 7, 10], ["major", "minor", "reference", "major"])
    b = seg(2, [10, 13, 16, 19], ["major", "minor", "reference", "major"])
    c = seg(3, [19, 22, 25, 28], ["major", "minor", "reference", "major"])
    plain = adjudicate([a, b, c])
    assert plain.anchor_constrained is False
    assert plain.anchor_resolutions == ()
    # 该例接缝最优 shift=9（各 4 对），偏移链为 0,-9,-18
    assert [e.shift for e in plain.edges] == [9, 9]
    assert plain.offsets == (0, -9, -18)
    # 显式空锚点列表与省略完全等价
    also = adjudicate([a, b, c], [])
    assert also.order == plain.order
    assert [(e.shift, e.pairs) for e in also.edges] == [
        (e.shift, e.pairs) for e in plain.edges
    ]


def test_anchors_jointly_force_non_greedy_seam_shift() -> None:
    # 段1→段2 的局部最优是 shift=5（4 对）；锚点联合要求该接缝取 shift=4（3 对），
    # 逐接缝先选局部最优再核对锚点必然失败，只有联合搜索才能找到此方案。
    s1 = seg(1, [1, 2, 3, 4])
    s2 = seg(2, [6, 7, 8, 9])
    s3 = seg(3, [6, 7, 8, 9])

    legacy = adjudicate([s1, s2, s3])
    assert [e.shift for e in legacy.edges] == [5, 0]
    assert legacy.total_pairs == 8

    # 锚点：段1 刻度1 → 全局水深1（段1 偏移0）；段3 刻度9 → 全局水深5（段3 偏移-4）。
    # s3偏移 = -(d12 + d23)，d23=0（同构），故 d12 必须 = 4。
    anchors = [anc(0, 1, 0, 1), anc(1, 3, 3, 5)]
    result = adjudicate([s1, s2, s3], anchors)

    assert result.anchor_constrained is True
    assert result.order == (1, 2, 3)
    assert [e.shift for e in result.edges] == [4, 0]
    assert result.offsets == (0, -4, -4)
    assert result.total_pairs == 7
    assert result.total_error == 4
    assert len(result.anchor_resolutions) == 2
    for resolution in result.anchor_resolutions:
        assert resolution.global_depth == resolution.archived_depth
        assert resolution.global_depth == resolution.mark + resolution.offset
    first, second = result.anchor_resolutions
    assert (first.segment_id, first.global_depth) == (1, 1)
    assert (second.segment_id, second.global_depth) == (3, 5)


def test_anchors_pin_segment_order() -> None:
    # 三段完全同构：无锚点时编号升序 (1,2,3) 且全部 shift=0。
    s1 = seg(1, [0, 10, 20, 30])
    s2 = seg(2, [0, 10, 20, 30])
    s3 = seg(3, [0, 10, 20, 30])
    assert adjudicate([s1, s2, s3]).order == (1, 2, 3)

    # 锚点把全局偏移钉为：段1=0、段2=-20、段3=-10，
    # 唯一可行顺序为 1→3→2（接缝 shift 均为 10）。
    anchors = [anc(0, 1, 0, 0), anc(1, 2, 0, -20), anc(2, 3, 0, -10)]
    result = adjudicate([s1, s2, s3], anchors)
    assert result.order == (1, 3, 2)
    assert [e.shift for e in result.edges] == [10, 10]
    offset_by_id = dict(zip(result.order, result.offsets))
    assert offset_by_id == {1: 0, 3: -10, 2: -20}
    for resolution in result.anchor_resolutions:
        assert resolution.global_depth == resolution.archived_depth
    assert result.total_pairs == 6
    assert result.total_error == 20


def test_anchored_evidence_obeys_all_edge_rules() -> None:
    s1 = seg(1, [1, 2, 3, 4], ["major", "minor", "major", "reference"])
    s2 = seg(2, [4, 5, 6, 7], ["reference", "major", "minor", "major"])
    s3 = seg(3, [5, 12, 20, 30], ["major", "major", "minor", "reference"])
    # 只要锚点落在可达方案上即可；用较宽松的档案值。
    anchors = [anc(0, 1, 0, 1), anc(1, 3, 3, 30)]
    result = adjudicate([s1, s2, s3], anchors)
    segments = {s.id: s for s in (s1, s2, s3)}
    for edge in result.edges:
        shifts = set()
        left_pos, right_pos = [], []
        for pair in edge.pairs:
            lm = segments[edge.left_id].marks[pair.left_position]
            rm = segments[edge.right_id].marks[pair.right_position]
            shifts.add(rm - lm)
            left_pos.append(pair.left_position)
            right_pos.append(pair.right_position)
            assert (
                segments[edge.left_id].types[pair.left_position]
                == segments[edge.right_id].types[pair.right_position]
                == pair.mark_type
            )
        assert shifts == {edge.shift}
        assert left_pos == sorted(set(left_pos))
        assert right_pos == sorted(set(right_pos))
    # 偏移链与接缝 shift 一致：offset_右 = offset_左 − shift
    offsets = dict(zip(result.order, result.offsets))
    for edge in result.edges:
        assert offsets[edge.right_id] == offsets[edge.left_id] - edge.shift
    for resolution in result.anchor_resolutions:
        assert resolution.global_depth == resolution.archived_depth


def test_contradictory_anchors_on_same_segment_name_second_as_blocker() -> None:
    s1 = seg(1, [0, 10, 20, 30])
    s2 = seg(2, [0, 10, 20, 30])
    s3 = seg(3, [0, 10, 20, 30])
    # 锚点1 要求段1 偏移0；锚点2 要求段1 偏移 99−10=89，同段内即互相矛盾。
    anchors = [anc(0, 1, 0, 0), anc(1, 1, 1, 99), anc(2, 2, 0, -10)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate([s1, s2, s3], anchors)
    assert exc_info.value.code == "anchor_conflict"
    message = str(exc_info.value)
    assert "第 2 个锚点" in message
    assert "首个阻断锚点" in message


def test_first_anchor_blocking_when_no_chain_exists() -> None:
    # 三段类型互不相通，本来就不存在合法链；第一个锚点即为阻断锚点。
    s1 = seg(1, [1, 2, 3, 4], ["major"] * 4)
    s2 = seg(2, [4, 5, 6, 7], ["minor"] * 4)
    s3 = seg(3, [7, 8, 9, 10], ["reference"] * 4)
    anchors = [anc(0, 1, 0, 1), anc(1, 3, 3, 10)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate([s1, s2, s3], anchors)
    assert exc_info.value.code == "anchor_conflict"
    assert "第 1 个锚点" in str(exc_info.value)


def test_unreachable_archive_depth_names_blocking_anchor() -> None:
    s1 = seg(1, [0, 10, 20, 30])
    s2 = seg(2, [0, 10, 20, 30])
    s3 = seg(3, [0, 10, 20, 30])
    # 所有接缝 shift 只能是 10 的倍数，段3 偏移只能是 -30..30 间 10 的倍数；
    # 档案水深 999 要求段3 偏移 969，任何链都不可达。
    anchors = [anc(0, 1, 0, 0), anc(1, 3, 0, 999)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate([s1, s2, s3], anchors)
    assert exc_info.value.code == "anchor_conflict"
    assert "第 2 个锚点" in str(exc_info.value)


def test_four_anchors_accepted_and_all_exact() -> None:
    s1 = seg(1, [0, 10, 20, 30])
    s2 = seg(2, [0, 10, 20, 30])
    s3 = seg(3, [0, 10, 20, 30])
    anchors = [
        anc(0, 1, 0, 0),
        anc(1, 1, 3, 30),
        anc(2, 2, 1, 0),    # 段2 偏移 -10：10 + (-10) = 0
        anc(3, 3, 2, 0),    # 段3 偏移 -20：20 + (-20) = 0
    ]
    result = adjudicate([s1, s2, s3], anchors)
    assert result.order == (1, 2, 3)
    assert result.offsets == (0, -10, -20)
    assert len(result.anchor_resolutions) == 4
    assert [r.index for r in result.anchor_resolutions] == [0, 1, 2, 3]
    assert all(r.global_depth == r.archived_depth for r in result.anchor_resolutions)
