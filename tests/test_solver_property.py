"""用独立暴力 oracle + 随机输入交叉验证求解器（固定随机种子，可复现）。"""

from __future__ import annotations

import random
from itertools import permutations, product

from app.solver import Anchor, Segment, best_edge, adjudicate


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


def oracle_anchored(segments: list[Segment], anchors: list[Anchor]):
    """独立暴力枚举：排列 × 每接缝全部候选平移，强制锚点精确换算。

    返回 ``(key, perm, shifts)`` 形式的最优可行解；任何排列/组合都无法满足
    全部锚点时返回 ``None``。
    """

    best = None
    id_to_index = {s.id: i for i, s in enumerate(segments)}

    for perm in permutations(range(len(segments))):
        per_edge: list[list[tuple[int, tuple[tuple[int, int], ...]]]] = []
        ok = True
        for u, v in zip(perm, perm[1:]):
            # 该边的全部候选 shift 由两侧所有同类型刻度对决定
            all_groups: dict[int, list[tuple[int, int]]] = {}
            for i, (ml, tl) in enumerate(
                zip(segments[u].marks, segments[u].types)
            ):
                for j, (mr, tr) in enumerate(
                    zip(segments[v].marks, segments[v].types)
                ):
                    if tl == tr:
                        all_groups.setdefault(mr - ml, []).append((i, j))
            if not all_groups:
                ok = False
                break
            per_edge.append(
                [(shift, tuple(pairs)) for shift, pairs in all_groups.items()]
            )
        if not ok:
            continue

        # 链首段相对自身为 0；全局基准无关紧要，锚点约束只涉及偏移差。
        for choices in product(*per_edge):
            shifts = [c[0] for c in choices]
            rel = [0]
            for shift in shifts:
                rel.append(rel[-1] - shift)

            def global_depth(anchor: Anchor) -> int:
                seg_idx = id_to_index[anchor.segment_id]
                seg_pos = perm.index(seg_idx)
                return segments[seg_idx].marks[anchor.position] + rel[seg_pos]

            # 全局基准可整体浮动：由第一个锚点确定 datum，其余锚点必须与之相容。
            first = anchors[0]
            first_idx = id_to_index[first.segment_id]
            datum = first.depth - (
                segments[first_idx].marks[first.position]
                + rel[perm.index(first_idx)]
            )
            if any(global_depth(a) + datum != a.depth for a in anchors):
                continue

            total_pairs = sum(len(c[1]) for c in choices)
            total_abs = sum(abs(s) for s in shifts)
            id_tuple = tuple(segments[i].id for i in perm)
            key = (-total_pairs, total_abs, id_tuple)
            if best is None or key < best[0]:
                best = (key, perm, tuple(shifts))
    return best


def _random_anchors(
    rng: random.Random, segments: list[Segment], satisfiable: bool
) -> list[Anchor]:
    """生成 2–3 个锚点；``satisfiable=True`` 时按随机可行链构造以保证有解。"""

    n = len(segments)
    count = rng.choice([2, 2, 3])
    if satisfiable:
        perm = list(range(n))
        rng.shuffle(perm)
        rel = [0]
        for u, v in zip(perm, perm[1:]):
            left, right = segments[u], segments[v]
            same_type_pairs = [
                right.marks[j] - left.marks[i]
                for i, tl in enumerate(left.types)
                for j, tr in enumerate(right.types)
                if tl == tr
            ]
            if not same_type_pairs:
                return _random_anchors(rng, segments, False)
            rel.append(rel[-1] - rng.choice(same_type_pairs))
        datum = rng.randint(50, 150)
        chosen_segments = rng.sample(range(n), k=min(count, n))
        anchors = []
        used: set[tuple[int, int]] = set()
        for idx in chosen_segments:
            pos = rng.randrange(len(segments[idx].marks))
            if (idx, pos) in used:
                continue
            used.add((idx, pos))
            anchors.append(
                Anchor(
                    segment_id=segments[idx].id,
                    position=pos,
                    depth=segments[idx].marks[pos] + rel[list(perm).index(idx)] + datum,
                )
            )
        if len({a.segment_id for a in anchors}) < 2:
            return _random_anchors(rng, segments, False)
        return anchors

    # 不可满足倾向：首个锚点水深取低值、其余取高值，datum 吸收首个锚点后，
    # 其余锚点与它的偏移差远超任何链可达范围（刻度仅 -30..60、至多 4 条接缝）。
    first_idx, *rest = rng.sample(range(n), k=min(count + 1, n))
    first_pos = rng.randrange(len(segments[first_idx].marks))
    anchors = [
        Anchor(
            segment_id=segments[first_idx].id,
            position=first_pos,
            depth=rng.randint(5000, 5500),
        )
    ]
    used = {(first_idx, first_pos)}
    for idx in rest:
        pos = rng.randrange(len(segments[idx].marks))
        if (idx, pos) in used:
            continue
        used.add((idx, pos))
        anchors.append(
            Anchor(
                segment_id=segments[idx].id,
                position=pos,
                depth=rng.randint(8500, 9000),
            )
        )
    if len({a.segment_id for a in anchors}) < 2:
        idx = (first_idx + 1) % n
        anchors.append(
            Anchor(
                segment_id=segments[idx].id,
                position=0,
                depth=rng.randint(8500, 9000),
            )
        )
    return anchors


def _edge_shift_legal(left: Segment, right: Segment, shift: int) -> bool:
    """独立复核：该平移量下至少存在一对同类型刻度。"""
    return any(
        mr - ml == shift
        for ml, tl in zip(left.marks, left.types)
        for mr, tr in zip(right.marks, right.types)
        if tl == tr
    )


def random_small_segments(rng: random.Random) -> list[Segment]:
    """锚点性质测试专用：3–4 段、4–6 个刻度，控制暴力 oracle 组合规模。"""
    n = rng.randint(3, 4)
    ids = rng.sample(range(-8, 12), n)
    segments = []
    for seg_id in ids:
        count = rng.randint(4, 6)
        marks = sorted(rng.sample(range(-30, 60), count))
        types = [rng.choice(["major", "minor", "reference"]) for _ in marks]
        segments.append(Segment(id=seg_id, marks=tuple(marks), types=tuple(types)))
    return segments


def test_adjudicate_with_anchors_matches_oracle() -> None:
    rng = random.Random(990011)
    satisfiable_cases = 0
    unsatisfiable_cases = 0
    for i in range(110):
        segments = random_small_segments(rng)
        anchors = _random_anchors(rng, segments, satisfiable=(i % 2 == 0))
        if len(anchors) < 2 or len({a.segment_id for a in anchors}) < 2:
            continue
        expected = oracle_anchored(segments, anchors)
        if expected is None:
            unsatisfiable_cases += 1
            try:
                adjudicate(segments, anchors)
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "code", None) in (
                    "anchors_unsatisfiable",
                    "no_valid_chain",
                )
            else:
                raise AssertionError("oracle 认为锚点不可满足，求解器却返回了方案")
            continue

        satisfiable_cases += 1
        result = adjudicate(segments, anchors)
        key, perm, _shifts = expected
        # 三级择优指标（配对总数、总平移误差、编号序列）必须与暴力最优一致
        assert result.order == tuple(segments[i].id for i in perm)
        assert result.total_pairs == -key[0]
        assert result.total_error == key[1]
        # 求解器给出的每条接缝平移量必须真实合法（独立复核）
        by_id = {s.id: s for s in segments}
        for edge in result.edges:
            assert _edge_shift_legal(
                by_id[edge.left_id], by_id[edge.right_id], edge.shift
            )
        # 每个锚点换算后的全局水深与档案值精确一致
        for resolved in result.anchors:
            assert resolved.global_depth == resolved.archive_depth
    assert satisfiable_cases > 30
    assert unsatisfiable_cases > 5
