"""用独立暴力 oracle + 随机输入交叉验证求解器（固定随机种子，可复现）。"""

from __future__ import annotations

import random
from itertools import permutations

import pytest

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


# ---------------------------------------------------------------------------
# 锚定裁决：独立暴力 oracle 交叉验证（联合穷举 排列 × 各接缝全部平移方案）
# ---------------------------------------------------------------------------


def oracle_edge_shifts(left: Segment, right: Segment):
    """返回 {shift: 配对数}，仅含类型相同刻度推导出的平移量。"""
    found: dict[int, int] = {}
    for i, (ml, tl) in enumerate(zip(left.marks, left.types)):
        for j, (mr, tr) in enumerate(zip(right.marks, right.types)):
            if tl == tr:
                d = mr - ml
                found[d] = found.get(d, 0) + 1
    return found


def oracle_anchored(segments, anchors):
    """独立枚举：每段恰用一次 × 每接缝任一合法 shift，锚点要求精确偏移。

    返回最优键 (-配对总数, |shift|和, 编号元组) 与排列；不可行返回 None。
    偏移约定 offset_右 = offset_左 − shift，锚点要求 offset = 档案水深 − 刻度。
    """
    n = len(segments)
    by_id = {s.id: s for s in segments}
    need = {}
    for a in anchors:
        seg = by_id[a.segment_id]
        need.setdefault(a.segment_id, set()).add(a.archived_depth - seg.marks[a.position])

    best = None
    for perm in permutations(range(n)):
        first_need = need.get(segments[perm[0]].id)
        if first_need and first_need != {0}:
            continue
        # offset -> (配对数, |shift|和)
        states = {0: (0, 0)}
        dead = False
        for u, v in zip(perm, perm[1:]):
            shifts = oracle_edge_shifts(segments[u], segments[v])
            if not shifts:
                dead = True
                break
            nxt = {}
            for off, (pc, ab) in states.items():
                for d, cnt in shifts.items():
                    noff = off - d
                    cand = (pc + cnt, ab + abs(d))
                    old = nxt.get(noff)
                    if old is None or (-cand[0], cand[1]) < (-old[0], old[1]):
                        nxt[noff] = cand
            vneed = need.get(segments[v].id)
            if vneed is not None:
                if len(vneed) != 1:
                    dead = True
                    break
                (needed,) = vneed
                nxt = {needed: nxt[needed]} if needed in nxt else {}
            if not nxt:
                dead = True
                break
            states = nxt
        if dead:
            continue
        id_tuple = tuple(segments[i].id for i in perm)
        for off, (pc, ab) in states.items():
            key = (-pc, ab, id_tuple)
            if best is None or key < best[0]:
                best = (key, perm)
    return best


def random_anchored_case(rng):
    """构造一个锚点必然可行的案例：先选链与接缝 shift，再由偏移反推档案水深。"""
    from app.solver import Anchor

    segs = random_segments(rng)
    n = len(segs)
    perm = tuple(rng.sample(range(n), n))
    shifts = []
    feasible_edges = True
    for u, v in zip(perm, perm[1:]):
        choices = oracle_edge_shifts(segs[u], segs[v])
        if not choices:
            feasible_edges = False
            break
        shifts.append(rng.choice(sorted(choices)))
    if not feasible_edges:
        return None

    offsets = [0]
    for d in shifts:
        offsets.append(offsets[-1] - d)
    offset_by_node = {node: offsets[k] for k, node in enumerate(perm)}

    # 选 2–min(4, n) 个锚点，节点互异故天然覆盖至少两段且不重复引用同段刻度。
    anchor_count = rng.randint(2, min(4, n))
    chosen_nodes = rng.sample(range(n), anchor_count)
    anchors = []
    for index, node in enumerate(chosen_nodes):
        position = rng.randrange(len(segs[node].marks))
        mark = segs[node].marks[position]
        depth = mark + offset_by_node[node]
        anchors.append(
            Anchor(
                index=index,
                segment_id=segs[node].id,
                position=position,
                archived_depth=depth,
            )
        )
    return segs, anchors


def test_anchored_matches_oracle_on_feasible_cases() -> None:
    rng = random.Random(20260926)
    made = 0
    attempts = 0
    while made < 120 and attempts < 2000:
        attempts += 1
        case = random_anchored_case(rng)
        if case is None:
            continue
        segs, anchors = case
        expected = oracle_anchored(segs, anchors)
        assert expected is not None, "构造案例应可行，oracle 却判不可行"
        result = adjudicate(segs, anchors)
        # 锚点全部精确成立
        by_id = {s.id: s for s in segs}
        for a, res in zip(anchors, result.anchor_resolutions):
            assert res.global_depth == a.archived_depth
            seg = by_id[a.segment_id]
            assert res.global_depth == seg.marks[a.position] + res.offset
        # 层级指标与独立 oracle 的最优值一致
        assert result.total_pairs == -expected[0][0]
        assert result.total_error == expected[0][1]
        assert result.order == tuple(segs[i].id for i in expected[1])
        made += 1
    assert made == 120, f"可行构造样本不足：{made}"


def test_anchored_infeasibility_matches_oracle() -> None:
    from app.solver import Anchor, AdjudicationError

    rng = random.Random(777)
    checked_conflicts = 0
    for _ in range(200):
        segs = random_segments(rng)
        ids = [s.id for s in segs]
        k = rng.randint(2, 4)
        anchors = []
        used = set()
        for i in range(k):
            sid = rng.choice(ids)
            seg = next(s for s in segs if s.id == sid)
            pos = rng.randrange(len(seg.marks))
            if (sid, pos) in used:
                continue
            used.add((sid, pos))
            anchors.append(Anchor(i, sid, pos, rng.randint(-200, 200)))
        if len(anchors) < 2 or len({a.segment_id for a in anchors}) < 2:
            continue
        feasible = oracle_anchored(segs, anchors) is not None
        if feasible:
            result = adjudicate(segs, anchors)
            assert all(r.global_depth == r.archived_depth for r in result.anchor_resolutions)
        else:
            with pytest.raises(AdjudicationError) as exc:
                adjudicate(segs, anchors)
            assert exc.value.code == "anchor_conflict"
            checked_conflicts += 1
    assert checked_conflicts > 0, "随机样本中应出现过不可行锚点组合"
