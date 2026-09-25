"""solver.adjudicate 全链路裁决测试：层级目标、稳定决胜、未配对刻度、无合法链。"""

from __future__ import annotations

import pytest

from app.solver import AdjudicationError, Segment, adjudicate


def seg(seg_id: int, marks: list[int], types_: list[str]) -> Segment:
    return Segment(id=seg_id, marks=tuple(marks), types=tuple(types_))


def test_orders_chain_by_shared_overlaps() -> None:
    # 用类型通道锁定唯一的链方向，并让其余类型的非等距刻度在任何平移下
    # 都只能配 1 对，从而使零平移的接缝配对（|shift| 最小）胜出。
    a = seg(1, [1, 2, 3, 4], ["major", "major", "major", "reference"])
    b = seg(2, [4, 10, 17, 25], ["reference", "major", "major", "minor"])
    c = seg(3, [25, 30, 40, 55], ["minor", "major", "major", "major"])
    result = adjudicate([a, b, c])
    assert result.order == (1, 2, 3)
    assert [e.shift for e in result.edges] == [0, 0]
    assert result.total_pairs == 2
    assert result.total_error == 0
    assert [(p.left_position, p.right_position) for p in result.edges[0].pairs] == [(3, 0)]
    assert [(p.left_position, p.right_position) for p in result.edges[1].pairs] == [(3, 0)]


def test_maximize_pairs_then_minimize_shift_chain_level() -> None:
    # 全部 major 化的相邻段可配 4 对；段3 只有一个 major（刻度7），其余 minor。
    #   顺序 1→2→3 / 3→2→1：配对 5、|shift| 和 3
    #   顺序 2→1→3 / 3→1→2：配对 5、|shift| 和 6
    #   顺序 1→3→2 / 2→3→1：配对 2
    a = seg(1, [1, 2, 3, 4], ["major"] * 4)
    b = seg(2, [4, 5, 6, 7], ["major"] * 4)
    c = seg(3, [7, 8, 9, 10], ["major", "minor", "minor", "minor"])
    result = adjudicate([a, b, c])
    assert result.order == (1, 2, 3)      # 配对数优先，其次 |shift|，最后编号
    assert result.total_pairs == 5
    assert result.total_error == 3


def test_stable_tie_break_by_original_id_sequence() -> None:
    # 三段完全同构：任何顺序配对数与 |shift| 总和都相同，必须取编号升序。
    common_types = ["major", "minor", "major", "reference"]
    a = seg(1, [0, 1, 2, 3], common_types)
    b = seg(2, [0, 1, 2, 3], common_types)
    c = seg(3, [0, 1, 2, 3], common_types)
    assert adjudicate([a, b, c]).order == (1, 2, 3)
    # 打乱录入顺序，结果仍按编号稳定决胜。
    assert adjudicate([c, a, b]).order == (1, 2, 3)
    assert adjudicate([b, c, a]).order == (1, 2, 3)


def test_each_segment_used_exactly_once() -> None:
    a = seg(1, [1, 2, 3, 4], ["major"] * 4)
    b = seg(2, [4, 5, 6, 7], ["major"] * 4)
    c = seg(3, [7, 8, 9, 10], ["major"] * 4)
    result = adjudicate([a, b, c])
    assert sorted(result.order) == [1, 2, 3]
    assert len(set(result.order)) == 3
    assert len(result.edges) == 2
    edge_ids = [result.edges[0].left_id] + [e.right_id for e in result.edges]
    assert edge_ids == list(result.order)
    assert result.total_pairs == sum(e.pair_count for e in result.edges)
    assert result.total_error == sum(e.error for e in result.edges)


def test_unpaired_marks_listed_with_positions() -> None:
    # 1→2：major 仅 4→4（shift0）1 对；
    # 2→3：minor 同配对数下 |shift| 最小者为 shift1（21→22、22→23）2 对。
    a = seg(1, [1, 2, 3, 4], ["major"] * 4)
    b = seg(2, [4, 20, 21, 22], ["major", "minor", "minor", "minor"])
    c = seg(3, [22, 23, 50, 51], ["minor"] * 4)
    result = adjudicate([a, b, c])
    assert result.order == (1, 2, 3)
    unpaired_by_seg: dict[int, set[int]] = {}
    for item in result.unpaired:
        unpaired_by_seg.setdefault(item.segment_id, set()).add(item.position)
    assert unpaired_by_seg[1] == {0, 1, 2}   # 段1 仅位置3（刻度4）配对
    assert unpaired_by_seg[2] == {1}         # 段2 位置0/2/3 配对，刻度20（位置1）未配对
    assert unpaired_by_seg[3] == {2, 3}      # 段3 位置0/1 配对
    # 未配对条目按链顺序、段内位置顺序排列
    seen: list[tuple[int, int]] = [(u.segment_id, u.position) for u in result.unpaired]
    assert seen == sorted(seen, key=lambda x: (result.order.index(x[0]), x[1]))


def test_no_valid_chain_when_an_edge_has_no_legal_pair() -> None:
    # 段3 全部 minor，与全 major 的段1 无论如何排列都隔不开：
    # 三选二的直接相邻无法避免 major/minor 边界。
    a = seg(1, [1, 2, 3, 4], ["major"] * 4)
    b = seg(2, [4, 5, 6, 7], ["major"] * 4)
    c = seg(3, [7, 8, 9, 10], ["minor"] * 4)
    with pytest.raises(AdjudicationError) as exc_info:
        adjudicate([a, b, c])
    assert exc_info.value.code == "no_valid_chain"


def test_result_evidence_internally_consistent() -> None:
    a = seg(5, [1, 4, 7, 10], ["major", "minor", "reference", "major"])
    b = seg(2, [10, 13, 16, 19], ["major", "minor", "reference", "major"])
    c = seg(9, [19, 22, 25, 28], ["major", "minor", "reference", "major"])
    result = adjudicate([a, b, c])

    segments = {s.id: s for s in (a, b, c)}
    paired_positions: dict[int, set[int]] = {s.id: set() for s in (a, b, c)}
    for edge in result.edges:
        shifts = set()
        for pair in edge.pairs:
            left_mark = segments[edge.left_id].marks[pair.left_position]
            right_mark = segments[edge.right_id].marks[pair.right_position]
            shifts.add(right_mark - left_mark)
            assert segments[edge.left_id].types[pair.left_position] == pair.mark_type
            assert segments[edge.right_id].types[pair.right_position] == pair.mark_type
            paired_positions[edge.left_id].add(pair.left_position)
            paired_positions[edge.right_id].add(pair.right_position)
        assert len(shifts) == 1  # 同一边界平移量一致
        assert shifts.pop() == edge.shift

    assert result.total_error == sum(e.error for e in result.edges)
    assert result.total_pairs == sum(e.pair_count for e in result.edges)
    for seg_id, segment in segments.items():
        covered = paired_positions[seg_id] | {
            u.position for u in result.unpaired if u.segment_id == seg_id
        }
        assert covered == set(range(len(segment.marks)))  # 每个刻度恰好出现一次
