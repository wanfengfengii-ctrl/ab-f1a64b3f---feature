"""solver.best_edge 的单元测试。"""

from __future__ import annotations

from app.solver import Pair, Segment, best_edge


def seg(seg_id: int, marks: list[int], types_: list[str]) -> Segment:
    return Segment(id=seg_id, marks=tuple(marks), types=tuple(types_))


def test_prefers_more_pairs_over_smaller_shift() -> None:
    # shift=4 有 3 对（2→6,3→7,4→8），shift=5 有 4 对（1→6,2→7,3→8,4→9），
    # 尽管 |5|>|4|，仍必须选配对数更多的 shift=5。
    left = seg(1, [1, 2, 3, 4], ["major"] * 4)
    right = seg(2, [6, 7, 8, 9], ["major"] * 4)
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.shift == 5
    assert edge.pair_count == 4
    assert [(p.left_position, p.right_position) for p in edge.pairs] == [
        (0, 0), (1, 1), (2, 2), (3, 3),
    ]


def test_tie_on_pair_count_prefers_smaller_abs_shift() -> None:
    # major：左 {1}、右 {4,5} → shift=3 与 shift=4 都恰好 1 对；
    # 其余类型两侧不重合，不产生候选。同配对数下必须选 |shift| 更小者。
    left = seg(1, [1, 2, 3, 4], ["major", "minor", "minor", "minor"])
    right = seg(2, [4, 5, 6, 7], ["major", "major", "reference", "reference"])
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.pair_count == 1
    assert edge.shift == 3
    assert edge.pairs == (
        Pair(left_position=0, right_position=0, mark=1, mark_type="major"),
    )


def test_negative_shift_supported() -> None:
    # 右段整体在左段左侧，平移量为负，仍须正确对齐。
    left = seg(1, [10, 11, 12, 13], ["major"] * 4)
    right = seg(2, [1, 2, 3, 4], ["major"] * 4)
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.shift == -9
    assert edge.pair_count == 4
    assert edge.error == 9


def test_only_same_type_marks_pair() -> None:
    # 数值上 shift=2 四处全部重合，但只有两处刻线类型一致。
    left = seg(1, [1, 2, 3, 4], ["major", "major", "major", "major"])
    right = seg(2, [3, 4, 5, 6], ["major", "minor", "minor", "major"])
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.shift == 2
    assert [(p.left_position, p.right_position) for p in edge.pairs] == [(0, 0), (3, 3)]
    for pair in edge.pairs:
        assert pair.mark_type == "major"


def test_all_pairs_share_single_consistent_shift_and_preserve_order() -> None:
    left = seg(1, [2, 5, 9, 14], ["major", "minor", "reference", "major"])
    right = seg(2, [6, 9, 13, 18], ["minor", "major", "minor", "major"])
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.pair_count >= 1
    # 每对推导出的平移量在边界内必须一致：
    for pair in edge.pairs:
        assert right.marks[pair.right_position] - left.marks[pair.left_position] == edge.shift
        assert right.marks[pair.right_position] == pair.mark + edge.shift
    # 配对保持两侧各自原始顺序且为一对一（位置严格递增、不重复）：
    left_positions = [p.left_position for p in edge.pairs]
    right_positions = [p.right_position for p in edge.pairs]
    assert left_positions == sorted(left_positions)
    assert right_positions == sorted(right_positions)
    assert len(set(left_positions)) == len(left_positions)
    assert len(set(right_positions)) == len(right_positions)


def test_no_shared_type_returns_none() -> None:
    left = seg(1, [1, 2, 3, 4], ["major"] * 4)
    right = seg(2, [3, 4, 5, 6], ["minor"] * 4)
    assert best_edge(left, right) is None


def test_error_is_abs_shift() -> None:
    left = seg(1, [1, 2, 3, 4], ["major"] * 4)
    right = seg(2, [3, 4, 5, 6], ["major"] * 4)
    edge = best_edge(left, right)
    assert edge is not None
    assert edge.shift == 2
    assert edge.error == 2
