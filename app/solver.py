"""核心领域逻辑：校验输入、计算边界配对、搜索最优刻度链。

业务规则
========
* 每段图纸恰使用一次，段内刻度沿纸边严格递增，每个刻度带一种刻线类型。
* 相邻两段只允许用「类型相同」的刻度一对一配对，且配对必须保持两段各自的
  原始顺序；同一相邻边界内所有配对推导出的平移量必须一致。
* 裁决目标依次为：
  1. 最大化全部边界的配对总数；
  2. 最小化平移量绝对值总和；
  3. 按原录入编号序列（排列字典序）稳定决胜。
* 任一边界不存在合法配对时，整条链不合法。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from typing import Optional

#: 允许辨识的刻线类型
MARK_TYPES = ("major", "minor", "reference")

MIN_SEGMENTS = 3
MAX_SEGMENTS = 6
MIN_MARKS_PER_SEGMENT = 4
MAX_MARKS_PER_SEGMENT = 10


class AdjudicationError(Exception):
    """裁决失败。``code`` 供前端区分错误类别，消息为面向修复师的首个原因。"""

    def __init__(self, message: str, code: str = "invalid_input") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Segment:
    """一段图纸：录入编号 + 沿边严格递增的刻度与对应刻线类型。"""

    id: int
    marks: tuple[int, ...]
    types: tuple[str, ...]


@dataclass(frozen=True)
class Pair:
    """一对配对刻度（位置均为 0 基，刻度值在平移后重合）。"""

    left_position: int
    right_position: int
    mark: int
    mark_type: str


@dataclass(frozen=True)
class EdgeResult:
    """一个相邻边界的最优配对方案。"""

    left_id: int
    right_id: int
    shift: int
    pairs: tuple[Pair, ...]

    @property
    def pair_count(self) -> int:
        return len(self.pairs)

    @property
    def error(self) -> int:
        """该边界的拼接误差，即平移量绝对值。"""
        return abs(self.shift)


@dataclass(frozen=True)
class UnpairedMark:
    segment_id: int
    position: int
    mark: int
    mark_type: str


@dataclass(frozen=True)
class Adjudication:
    """完整裁决结果。"""

    order: tuple[int, ...]
    edges: tuple[EdgeResult, ...]
    unpaired: tuple[UnpairedMark, ...]
    total_pairs: int
    total_error: int


def best_edge(left: Segment, right: Segment) -> Optional[EdgeResult]:
    """计算 ``left → right`` 边界的最优配对，无任何同类型刻度时返回 ``None``。

    同一平移量 ``d`` 下的所有可配对刻度天然构成合法配对：
    ``mark_right = mark_left + d`` 与刻度严格递增共同保证两侧位置一一对应且
    顺序一致，因此直接在各平移量之间按「配对数最多、|d| 最小、d 最小」决胜。
    """

    by_shift: dict[int, list[tuple[int, int]]] = {}
    for a, (mark_left, type_left) in enumerate(zip(left.marks, left.types)):
        for b, (mark_right, type_right) in enumerate(zip(right.marks, right.types)):
            if type_left != type_right:
                continue
            shift = mark_right - mark_left
            by_shift.setdefault(shift, []).append((a, b))

    if not by_shift:
        return None

    best_key: Optional[tuple[int, int, int]] = None
    best_shift = 0
    best_pairs: tuple[tuple[int, int], ...] = ()
    for shift, raw_pairs in by_shift.items():
        # 收集顺序为左位置外层、右位置内层，即按左位置升序输出。
        pairs = tuple(raw_pairs)
        key = (-len(pairs), abs(shift), shift)
        if best_key is None or key < best_key:
            best_key, best_shift, best_pairs = key, shift, pairs

    return EdgeResult(
        left_id=left.id,
        right_id=right.id,
        shift=best_shift,
        pairs=tuple(
            Pair(
                left_position=a,
                right_position=b,
                mark=left.marks[a],
                mark_type=left.types[a],
            )
            for a, b in best_pairs
        ),
    )


def adjudicate(segments: list[Segment]) -> Adjudication:
    """在全部段落排列中搜索最优刻度链。

    段数至多 6，排列数至多 720，直接穷举；每个有向边界只计算一次并缓存。
    排序键为 ``(-配对总数, |平移量|总和, 编号元组)``，编号元组字典序即
    「按原录入编号序列稳定决胜」。
    """

    n = len(segments)

    edge_cache: dict[tuple[int, int], Optional[EdgeResult]] = {}
    for i in range(n):
        for j in range(n):
            if i != j:
                edge_cache[(i, j)] = best_edge(segments[i], segments[j])

    best_key: Optional[tuple[int, int, tuple[int, ...]]] = None
    best_perm: Optional[tuple[int, ...]] = None
    best_edges: Optional[list[EdgeResult]] = None
    first_failure: Optional[tuple[int, int]] = None

    for perm in permutations(range(n)):
        edges: list[EdgeResult] = []
        total_pairs = 0
        total_abs_shift = 0
        valid = True
        for u, v in zip(perm, perm[1:]):
            edge = edge_cache[(u, v)]
            if edge is None:
                valid = False
                if first_failure is None:
                    first_failure = (u, v)
                break
            edges.append(edge)
            total_pairs += edge.pair_count
            total_abs_shift += abs(edge.shift)

        if not valid:
            continue

        # 第三级决胜：按原录入编号序列（编号元组字典序）稳定决胜，
        # 与段落数组的录入先后无关。
        id_tuple = tuple(segments[idx].id for idx in perm)
        key = (-total_pairs, total_abs_shift, id_tuple)
        if best_key is None or key < best_key:
            best_key = key
            best_perm = perm
            best_edges = edges

    if best_perm is None or best_edges is None:
        assert first_failure is not None
        u, v = first_failure
        raise AdjudicationError(
            f"无法拼成合法刻度链：不存在使每个相邻边界都有合法配对的段落顺序"
            f"（例如 段{segments[u].id} → 段{segments[v].id} 边界上没有类型相同的"
            f"可配对刻度）",
            code="no_valid_chain",
        )

    return _build_result(segments, best_perm, best_edges)


def _build_result(
    segments: list[Segment],
    perm: tuple[int, ...],
    edges: list[EdgeResult],
) -> Adjudication:
    index_by_id = {s.id: i for i, s in enumerate(segments)}
    used_positions: dict[int, set[int]] = {i: set() for i in range(len(segments))}

    total_pairs = 0
    total_error = 0
    for edge in edges:
        u = index_by_id[edge.left_id]
        v = index_by_id[edge.right_id]
        for pair in edge.pairs:
            used_positions[u].add(pair.left_position)
            used_positions[v].add(pair.right_position)
        total_pairs += edge.pair_count
        total_error += edge.error

    unpaired: list[UnpairedMark] = []
    for idx in perm:
        segment = segments[idx]
        for position in range(len(segment.marks)):
            if position not in used_positions[idx]:
                unpaired.append(
                    UnpairedMark(
                        segment_id=segment.id,
                        position=position,
                        mark=segment.marks[position],
                        mark_type=segment.types[position],
                    )
                )

    return Adjudication(
        order=tuple(segments[idx].id for idx in perm),
        edges=tuple(edges),
        unpaired=tuple(unpaired),
        total_pairs=total_pairs,
        total_error=total_error,
    )
