"""带定位锚点的全链路裁决测试。

覆盖：
* 锚点精确换算、各段全局偏移与逐锚点证据；
* 必须**联合**选择接缝平移（不能逐接缝取局部最优再核对锚点）；
* 锚点可改变段落顺序；
* 层级择优（配对总数、总平移误差、编号序列）在锚点约束下仍成立；
* 锚点矛盾 / 无法成链时报「首个阻断锚点」，且无锚点请求行为保持兼容。
"""

from __future__ import annotations

import pytest

from app.solver import AdjudicationError, Anchor, Segment, adjudicate


def seg(seg_id: int, marks: list[int], types_: list[str] | None = None) -> Segment:
    if types_ is None:
        types_ = ["major"] * len(marks)
    return Segment(id=seg_id, marks=tuple(marks), types=tuple(types_))


def standard_three() -> list[Segment]:
    # [1,2,3,4] → [4,5,6,7] → [7,8,9,10]
    return [
        seg(1, [1, 2, 3, 4]),
        seg(2, [4, 5, 6, 7]),
        seg(3, [7, 8, 9, 10]),
    ]


def test_unanchored_request_unchanged() -> None:
    result = adjudicate(standard_three())
    assert result.order == (1, 2, 3)
    assert [e.shift for e in result.edges] == [3, 3]
    assert result.anchors == ()
    # 无锚点时偏移以链首段为基准（链首段 0，offset_右 = offset_左 − shift）
    assert [(o.segment_id, o.offset) for o in result.offsets] == [
        (1, 0),
        (2, -3),
        (3, -6),
    ]


def test_unanchored_empty_tuple_equals_none() -> None:
    assert adjudicate(standard_three(), ()) == adjudicate(standard_three())


def test_anchors_consistent_with_greedy_chain() -> None:
    # 链 1→2→3、shift 各 3；锚点 段1@0=100、段3@3=103（mark10 + offset(-3)=103）。
    anchors = [Anchor(1, 0, 100), Anchor(3, 3, 103)]
    result = adjudicate(standard_three(), anchors)
    assert result.order == (1, 2, 3)
    assert [e.shift for e in result.edges] == [3, 3]
    assert [(o.segment_id, o.offset) for o in result.offsets] == [
        (1, 99),
        (2, 96),
        (3, 93),
    ]
    assert len(result.anchors) == 2
    for resolved, anchor in zip(result.anchors, anchors):
        assert (resolved.segment_id, resolved.position) == (
            anchor.segment_id,
            anchor.position,
        )
        assert resolved.global_depth == anchor.depth == resolved.archive_depth


def test_every_anchor_global_depth_matches_archive_exactly() -> None:
    # 锚点：段1@0(mark1)=100 ⇒ 段1 偏移99；段2@2(mark6)=99 ⇒ 段2 偏移93。
    result = adjudicate(
        standard_three(), [Anchor(1, 0, 100), Anchor(2, 2, 99)]
    )
    for resolved in result.anchors:
        assert resolved.global_depth == resolved.archive_depth
    # 全局联合最优为顺序 (1,3,2)：1→3 shift6（4 对）、3→2 shift0（1 对），
    # 5 对、误差 6；而顺序 (1,2,3) 被迫 shift6+shift3 仅 5 对、误差 9。
    assert result.order == (1, 3, 2)
    assert [e.shift for e in result.edges] == [6, 0]
    assert result.total_pairs == 5
    assert result.total_error == 6
    offset_by_id = {o.segment_id: o.offset for o in result.offsets}
    assert offset_by_id == {1: 99, 3: 93, 2: 93}


def test_joint_shift_selection_deviates_from_per_edge_greedy() -> None:
    # 自由裁决逐接缝取局部最优：shift=3（4 对）。锚点要求 s1+s2=5：
    # 各 shift 的配对数为 {0:1,1:2,2:3,3:4,4:3,5:2,6:1}，
    # 和为 5 的组合中 (2,3)/(3,2) 各 7 对，联合最优必须取 (2,3)（元组决胜）。
    # 段3@3 刻度10，全局 104 ⇒ q3=94；段1@0 q1=99 ⇒ s1+s2 = q1−q3 = 5。
    anchors = [Anchor(1, 0, 100), Anchor(3, 3, 104)]
    result = adjudicate(standard_three(), anchors)
    assert [e.shift for e in result.edges] == [2, 3]
    assert result.total_pairs == 7
    assert result.total_error == 5
    for resolved in result.anchors:
        assert resolved.global_depth == resolved.archive_depth


def test_joint_selection_other_target() -> None:
    # s1+s2=4：(1,3) 与 (2,2) 都 6 对、误差 4，元组决胜取 (1,3)。
    anchors = [Anchor(1, 0, 100), Anchor(3, 3, 105)]
    result = adjudicate(standard_three(), anchors)
    assert [e.shift for e in result.edges] == [1, 3]
    assert result.total_pairs == 6
    assert result.total_error == 4


def test_anchors_can_change_chain_order() -> None:
    # 固定实例：无锚点最优顺序为 (2,1,3)；该组锚点只与 (1,3,2) 相容。
    segments = [
        seg(1, [1, 4, 10, 12], ["major", "reference", "major", "minor"]),
        seg(2, [1, 6, 16, 18], ["major", "major", "minor", "minor"]),
        seg(3, [2, 7, 13, 19], ["major", "reference", "major", "major"]),
    ]
    base = adjudicate(segments)
    assert base.order == (2, 1, 3)

    anchors = [Anchor(3, 1, 89), Anchor(2, 3, 96)]
    result = adjudicate(segments, anchors)
    assert result.order == (1, 3, 2)
    assert [e.shift for e in result.edges] == [3, 4]
    for resolved in result.anchors:
        assert resolved.global_depth == resolved.archive_depth
    # 全部段仍恰用一次
    assert sorted(result.order) == [1, 2, 3]


def test_hierarchy_pair_count_primary_under_anchors() -> None:
    # s1+s2=6：候选 (0,6) 2 对、(1,5) 4 对、(2,4) 6 对、(3,3) 8 对。
    # 联合择优必须选配对最多的 (3,3)，而不是误差相同但配对更少的组合。
    # 段3@3(mark10) 全局103 ⇒ q3=93；q1=99 ⇒ s1+s2=6。
    anchors = [Anchor(1, 0, 100), Anchor(3, 3, 103)]
    result = adjudicate(standard_three(), anchors)
    assert [e.shift for e in result.edges] == [3, 3]
    assert result.total_pairs == 8
    assert result.total_error == 6


def test_id_sequence_tie_break_with_anchors() -> None:
    # 三段完全同构：任何排列下锚点只在一种镜像顺序中可行；这里验证对称锚点下
    # 仍按编号元组决胜。锚点引用 段1@0 与 段3@3，所有排列穷举后
    # 编号元组最小的可行方案取胜。
    common = [0, 1, 2, 3]
    types = ["major", "minor", "major", "reference"]
    segments = [
        seg(1, common, types),
        seg(2, common, types),
        seg(3, common, types),
    ]
    # 档案水深与 shift=0、链 1→2→3 一致（段1@0=50，段3@3=53）。
    anchors = [Anchor(1, 0, 50), Anchor(3, 3, 53)]
    result = adjudicate(segments, anchors)
    assert result.order == (1, 2, 3)
    assert [e.shift for e in result.edges] == [0, 0]
    assert [(o.segment_id, o.offset) for o in result.offsets] == [
        (1, 50),
        (2, 50),
        (3, 50),
    ]


def test_unsatisfiable_anchor_pair_reports_first_blocking() -> None:
    # q1=99；段3@3 深度96 ⇒ q3=86 ⇒ 需要 s1+s2=13，超出可达范围（每边 0..6）。
    anchors = [Anchor(1, 0, 100), Anchor(3, 3, 96)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate(standard_three(), anchors)
    assert exc_info.value.code == "anchors_unsatisfiable"
    assert "锚点 2" in str(exc_info.value)
    assert "段 3" in str(exc_info.value)


def test_contradictory_anchors_on_same_segment_reported() -> None:
    # 段1 位置0(刻度1) 深度100 ⇒ 偏移99；位置1(刻度2) 深度102 ⇒ 偏移100，矛盾。
    anchors = [Anchor(1, 0, 100), Anchor(1, 1, 102), Anchor(2, 0, 103)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate(standard_three(), anchors)
    assert exc_info.value.code == "anchors_unsatisfiable"
    # 首个阻断锚点是第 2 个（第一个单独可满足）。
    assert "锚点 2" in str(exc_info.value)


def test_first_blocking_anchor_among_three() -> None:
    # 锚点1、2 相容（段1@0=100，段2@0(mark4)=103 ⇒ 接缝 shift=0）；
    # 锚点3 段3@3 深度90（q=80）要求 s2=19，不可达。
    anchors = [Anchor(1, 0, 100), Anchor(2, 0, 103), Anchor(3, 3, 90)]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate(standard_three(), anchors)
    assert exc_info.value.code == "anchors_unsatisfiable"
    message = str(exc_info.value)
    assert "锚点 3" in message
    assert "90" in message


def test_earlier_prefix_feasible_means_first_blocker_is_later() -> None:
    # 与上例对照：只有前两个锚点时裁决必须成功。
    anchors = [Anchor(1, 0, 100), Anchor(2, 0, 103)]
    result = adjudicate(standard_three(), anchors)
    assert result.edges[0].shift == 0
    assert all(a.global_depth == a.archive_depth for a in result.anchors)


def test_four_anchors_all_resolved() -> None:
    anchors = [
        Anchor(1, 0, 100),
        Anchor(2, 0, 103),
        Anchor(3, 0, 106),
        Anchor(3, 3, 109),
    ]
    result = adjudicate(standard_three(), anchors)
    assert [e.shift for e in result.edges] == [0, 0]
    assert len(result.anchors) == 4
    assert [a.global_depth for a in result.anchors] == [100, 103, 106, 109]


def test_anchor_evidence_marks_and_depths() -> None:
    result = adjudicate(
        standard_three(), [Anchor(2, 1, 200), Anchor(3, 2, 200)]
    )
    by_key = {(a.segment_id, a.position): a for a in result.anchors}
    # 段2 位置1 刻度5 全局200；段3 位置2 刻度9 全局200。
    assert by_key[(2, 1)].mark == 5
    assert by_key[(2, 1)].global_depth == 200
    assert by_key[(3, 2)].mark == 9
    assert by_key[(3, 2)].global_depth == 200


def test_no_valid_chain_takes_priority_and_code() -> None:
    # 即使锚点引用合法，段3 全 minor 导致任何排列都无法成链：报 no_valid_chain。
    segments = [
        seg(1, [1, 2, 3, 4]),
        seg(2, [4, 5, 6, 7]),
        seg(3, [7, 8, 9, 10], ["minor"] * 4),
    ]
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate(segments, [Anchor(1, 0, 100), Anchor(3, 0, 107)])
    assert exc_info.value.code == "no_valid_chain"


def test_result_remains_internally_consistent_with_anchors() -> None:
    result = adjudicate(
        standard_three(), [Anchor(1, 3, 10), Anchor(3, 0, 10)]
    )
    segments = {s.id: s for s in standard_three()}
    offset_by_id = {o.segment_id: o.offset for o in result.offsets}
    # 偏移与接缝平移一致：offset_右 = offset_左 − shift
    for edge in result.edges:
        assert (
            offset_by_id[edge.right_id]
            == offset_by_id[edge.left_id] - edge.shift
        )
    # 未配对 + 配对仍恰好覆盖每段全部刻度一次
    paired: dict[int, set[int]] = {sid: set() for sid in segments}
    for edge in result.edges:
        for pair in edge.pairs:
            paired[edge.left_id].add(pair.left_position)
            paired[edge.right_id].add(pair.right_position)
    for sid, segment in segments.items():
        unpaired_positions = {
            u.position for u in result.unpaired if u.segment_id == sid
        }
        assert paired[sid] | unpaired_positions == set(range(len(segment.marks)))
    # 锚点换算 = 段内刻度 + 该段全局偏移
    for resolved in result.anchors:
        mark = segments[resolved.segment_id].marks[resolved.position]
        assert resolved.mark == mark
        assert mark + offset_by_id[resolved.segment_id] == resolved.global_depth
