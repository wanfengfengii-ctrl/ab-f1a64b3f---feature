"""用独立暴力 oracle + 随机输入交叉验证求解器（固定随机种子，可复现）。"""

from __future__ import annotations

import random
from itertools import permutations

from app.solver import Segment, best_edge, adjudicate


def oracle_best_edge(left: Segment, right: Segment):
    """独立枚举所有合法「同平移量」配对组，按配对数、|shift|、shift 决胜。"""
    candidates = []
    for i, (ml, tl) in enumerate(zip(left.marks, left.types)):
        for j, (mr, tr) in enumerate(zip(right.marks, right.types)):
            if tl != tr:
                continue
            candidates.append((mr - ml, i, j))
    shifts = {s for s, _, _ in candidates}
    feasible = []
    for shift in shifts:
        pairs = tuple((i, j) for s, i, j in candidates if s == shift)
        left_pos = [p[0] for p in pairs]
        right_pos = [p[1] for p in pairs]
        # 一对一 + 保持两侧原始顺序
        assert len(set(left_pos)) == len(left_pos)
        assert len(set(right_pos)) == len(right_pos)
        assert left_pos == sorted(left_pos)
        assert right_pos == sorted(right_pos)
        feasible.append((len(pairs), abs(shift), shift, pairs))
    if not feasible:
        return None
    feasible.sort(key=lambda x: (-x[0], x[1], x[2]))
    return feasible[0]


def oracle_adjudicate(segments: list[Segment]):
    best = None
    for perm in permutations(range(len(segments))):
        total_pairs = 0
        total_abs = 0
        ok = True
        for u, v in zip(perm, perm[1:]):
            edge = oracle_best_edge(segments[u], segments[v])
            if edge is None:
                ok = False
                break
            total_pairs += edge[0]
            total_abs += edge[1]
        if not ok:
            continue
        id_tuple = tuple(segments[i].id for i in perm)
        key = (-total_pairs, total_abs, id_tuple)
        if best is None or key < best[0]:
            best = (key, perm)
    return best


def random_segments(rng: random.Random, hard: bool = False) -> list[Segment]:
    n = rng.randint(3, 5)
    # 随机不重复编号（允许编号与录入位置无关，含负编号）
    ids = rng.sample(range(-8, 12), n)
    segments = []
    for k, seg_id in enumerate(ids):
        count = rng.randint(4, 7)
        marks = sorted(rng.sample(range(-30, 60), count))
        if hard:
            # 每段只含单一类型，相邻类型分组不同则必然无解。
            only_type = ("major", "minor", "reference")[k % 3]
            types = [only_type] * count
        else:
            types = [rng.choice(["major", "minor", "reference"]) for _ in marks]
        segments.append(Segment(id=seg_id, marks=tuple(marks), types=tuple(types)))
    return segments


def _cases(rng: random.Random, count: int):
    for i in range(count):
        # 约三分之一为「单一类型段」困难样本，稳定覆盖无解分支。
        yield random_segments(rng, hard=(i % 3 == 0))


def test_best_edge_matches_oracle() -> None:
    rng = random.Random(20260925)
    for segs in _cases(rng, 400):
        a, b = rng.sample(segs, 2)
        expected = oracle_best_edge(a, b)
        actual = best_edge(a, b)
        if expected is None:
            assert actual is None
        else:
            assert actual is not None
            assert actual.shift == expected[2]
            assert actual.pair_count == expected[0]
            assert [(p.left_position, p.right_position) for p in actual.pairs] == [
                (i, j) for i, j in expected[3]
            ]


def test_adjudicate_matches_oracle() -> None:
    rng = random.Random(424242)
    no_chain = 0
    for segs in _cases(rng, 150):
        expected = oracle_adjudicate(segs)
        if expected is None:
            no_chain += 1
            try:
                adjudicate(segs)
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "code", None) == "no_valid_chain"
            else:
                raise AssertionError("oracle 认为无合法链，求解器却返回了方案")
            continue

        result = adjudicate(segs)
        expected_perm = expected[1]
        expected_order = tuple(segs[i].id for i in expected_perm)
        assert result.order == expected_order
        assert result.total_pairs == -expected[0][0]
        assert result.total_error == expected[0][1]
        assert [e.shift for e in result.edges] == [
            oracle_best_edge(segs[u], segs[v])[2]
            for u, v in zip(expected_perm, expected_perm[1:])
        ]
    # 随机样本中应至少出现过一些无解情形，确保该分支确实被覆盖
    assert no_chain > 0
