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

定位锚点
========
修复师可额外提供 2–4 个定位锚点：每个锚点引用某段的某个刻度位置，并给出
档案中确认的该刻度原始水深。锚点必须覆盖至少两段。

全局坐标约定：段的「全局偏移」``offset`` 满足 ``全局水深 = 段内刻度 + offset``；
边界平移量 ``shift = 右段刻度 − 左段刻度``，因此
``offset_右 = offset_左 − shift``。无锚点时以链首段为基准（链首段 offset = 0），
有锚点时全局基准（各段 offset 的整体平移）由档案原始水深确定。锚点要求其引用
刻度换算出的全局水深与档案值**精确一致**。带锚点时必须联合选择段落顺序与每个
接缝的具体平移方案，不能逐边界取局部最优后再核对锚点。
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

MIN_ANCHORS = 2
MAX_ANCHORS = 4


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
    """一个相邻边界的配对方案。"""

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
class Anchor:
    """修复师录入的定位锚点：引用某段 0 基刻度位置及其档案原始水深。"""

    segment_id: int
    position: int
    depth: int


@dataclass(frozen=True)
class SegmentOffset:
    """某段相对全局基准（链首段局部坐标）的偏移：全局水深 = 段内刻度 + offset。"""

    segment_id: int
    offset: int


@dataclass(frozen=True)
class AnchorResolution:
    """单个锚点在裁决结果中的换算证据。"""

    segment_id: int
    position: int
    mark: int
    archive_depth: int
    global_depth: int


@dataclass(frozen=True)
class Adjudication:
    """完整裁决结果。"""

    order: tuple[int, ...]
    edges: tuple[EdgeResult, ...]
    unpaired: tuple[UnpairedMark, ...]
    total_pairs: int
    total_error: int
    offsets: tuple[SegmentOffset, ...] = ()
    anchors: tuple[AnchorResolution, ...] = ()


def best_edge(left: Segment, right: Segment) -> Optional[EdgeResult]:
    """计算 ``left → right`` 边界的最优配对，无任何同类型刻度时返回 ``None``。

    同一平移量 ``d`` 下的所有可配对刻度天然构成合法配对：
    ``mark_right = mark_left + d`` 与刻度严格递增共同保证两侧位置一一对应且
    顺序一致，因此直接在各平移量之间按「配对数最多、|d| 最小、d 最小」决胜。
    """

    by_shift = _shift_options(left, right)
    if not by_shift:
        return None

    best_shift = _choose_best_shift(by_shift)
    return _build_edge(left, right, best_shift, by_shift[best_shift])


def _shift_options(
    left: Segment, right: Segment
) -> dict[int, tuple[tuple[int, int], ...]]:
    """枚举 ``left → right`` 边界全部候选平移量及该平移量下的天然配对位置。"""

    by_shift: dict[int, list[tuple[int, int]]] = {}
    for a, (mark_left, type_left) in enumerate(zip(left.marks, left.types)):
        for b, (mark_right, type_right) in enumerate(zip(right.marks, right.types)):
            if type_left != type_right:
                continue
            shift = mark_right - mark_left
            by_shift.setdefault(shift, []).append((a, b))
    return {shift: tuple(raw) for shift, raw in by_shift.items()}


def _choose_best_shift(by_shift: dict[int, tuple[tuple[int, int], ...]]) -> int:
    """自由边界的局部择优：配对数最多、|shift| 最小、shift 最小。"""

    best_key: Optional[tuple[int, int, int]] = None
    best_shift = 0
    for shift, pairs in by_shift.items():
        key = (-len(pairs), abs(shift), shift)
        if best_key is None or key < best_key:
            best_key, best_shift = key, shift
    return best_shift


def _build_edge(
    left: Segment,
    right: Segment,
    shift: int,
    pair_positions: tuple[tuple[int, int], ...],
) -> EdgeResult:
    return EdgeResult(
        left_id=left.id,
        right_id=right.id,
        shift=shift,
        pairs=tuple(
            Pair(
                left_position=a,
                right_position=b,
                mark=left.marks[a],
                mark_type=left.types[a],
            )
            for a, b in pair_positions
        ),
    )


def adjudicate(
    segments: list[Segment], anchors: Optional[list[Anchor]] = None
) -> Adjudication:
    """在全部段落排列中搜索最优刻度链。

    段数至多 6，排列数至多 720，直接穷举；每个有向边界的全部候选平移量只
    计算一次并缓存。排序键为 ``(-配对总数, |平移量|总和, 编号元组)``，编号元组
    字典序即「按原录入编号序列稳定决胜」。

    带锚点时，对每条排列把锚点换算约束施加到对应接缝区间，在**可行**平移组合
    中联合择优，而非先逐接缝选局部最优再核对锚点。
    """

    anchors = list(anchors or [])
    n = len(segments)

    options: dict[tuple[int, int], dict[int, tuple[tuple[int, int], ...]]] = {}
    for i in range(n):
        for j in range(n):
            if i != j:
                options[(i, j)] = _shift_options(segments[i], segments[j])

    index_by_id = {s.id: i for i, s in enumerate(segments)}
    anchor_seg_indices = [index_by_id[a.segment_id] for a in anchors]

    def solve(anchor_subset: list[int]) -> Optional[_Solution]:
        return _search(
            segments, anchors, anchor_seg_indices, anchor_subset, options
        )

    base = solve([])
    if base is None:
        raise _no_chain_error(segments, options)

    if anchors:
        full = solve(list(range(len(anchors))))
        if full is None:
            # 找到首个使「此前全部锚点」变得不可满足的锚点（输入顺序）。
            for k in range(1, len(anchors) + 1):
                if solve(list(range(k))) is None:
                    raise _anchor_blocked_error(anchors[k - 1], k)
            # 理论不可达：full 为 None 时必有前缀不可行。
            raise _anchor_blocked_error(anchors[-1], len(anchors))
        solution = full
    else:
        solution = base

    return _build_result(segments, anchors, solution, options)


@dataclass
class _Solution:
    perm: tuple[int, ...]
    shifts: tuple[int, ...]


def _search(
    segments: list[Segment],
    anchors: list[Anchor],
    anchor_seg_indices: list[int],
    anchor_subset: list[int],
    options: dict[tuple[int, int], dict[int, tuple[tuple[int, int], ...]]],
) -> Optional[_Solution]:
    """对给定锚点子集枚举全部排列，返回全局最优可行方案；不可行返回 ``None``。"""

    n = len(segments)
    best_key: Optional[tuple[int, int, tuple[int, ...]]] = None
    best: Optional[_Solution] = None

    for perm in permutations(range(n)):
        edge_options: list[dict[int, tuple[tuple[int, int], ...]]] = [
            options[(u, v)] for u, v in zip(perm, perm[1:])
        ]
        if any(not opts for opts in edge_options):
            continue

        shifts = _choose_shifts(
            segments,
            anchors,
            anchor_seg_indices,
            anchor_subset,
            perm,
            edge_options,
        )
        if shifts is None:
            continue

        total_pairs = 0
        total_abs_shift = 0
        for opts, shift in zip(edge_options, shifts):
            total_pairs += len(opts[shift])
            total_abs_shift += abs(shift)

        id_tuple = tuple(segments[idx].id for idx in perm)
        key = (-total_pairs, total_abs_shift, id_tuple)
        if best_key is None or key < best_key:
            best_key, best = key, _Solution(perm=perm, shifts=tuple(shifts))

    return best


def _choose_shifts(
    segments: list[Segment],
    anchors: list[Anchor],
    anchor_seg_indices: list[int],
    anchor_subset: list[int],
    perm: tuple[int, ...],
    edge_options: list[dict[int, tuple[tuple[int, int], ...]]],
) -> Optional[list[int]]:
    """为一条排列决定每条接缝的平移量。

    锚点把链上「相邻两个被锚定段」之间的接缝区间约束成一个整体：区间内平移量
    之和由档案水深差与刻度差唯一确定，必须在该区间的全部可行平移组合中**联合**
    择优；锚点跨度之外的自由接缝按局部规则择优。
    """

    shifts: list[Optional[int]] = [None] * len(edge_options)

    # 链位置 -> 该段上各锚点的（档案水深 − 局部刻度）。同一段可有多个锚点，
    # 其换算出的全局偏移（= 档案水深 − 局部刻度）必须完全一致。
    position_of_idx = {seg_idx: p for p, seg_idx in enumerate(perm)}
    groups: dict[int, list[int]] = {}
    for anchor_no in anchor_subset:
        seg_idx = anchor_seg_indices[anchor_no]
        q = (
            anchors[anchor_no].depth
            - segments[seg_idx].marks[anchors[anchor_no].position]
        )
        groups.setdefault(position_of_idx[seg_idx], []).append(q)

    if groups:
        group_q: dict[int, int] = {}
        for position, q_values in groups.items():
            if any(q != q_values[0] for q in q_values[1:]):
                return None  # 同一段上的锚点互相矛盾
            group_q[position] = q_values[0]

        occupied = sorted(group_q)
        for p_prev, p_next in zip(occupied, occupied[1:]):
            # offset_右 = offset_左 − shift，故沿链
            # q_next − q_prev = offset_next − offset_prev = −Σ(区间内 shift)
            e_start, e_end = p_prev, p_next - 1
            target = group_q[p_prev] - group_q[p_next]
            chosen = _best_range_assignment(
                edge_options[e_start : e_end + 1], target
            )
            if chosen is None:
                return None
            for offset_index, shift in enumerate(chosen):
                shifts[e_start + offset_index] = shift

    for e, opts in enumerate(edge_options):
        if shifts[e] is None:
            shifts[e] = _choose_best_shift(opts)

    return [s for s in shifts if s is not None]


def _best_range_assignment(
    edge_options: list[dict[int, tuple[tuple[int, int], ...]]],
    target_sum: int,
) -> Optional[list[int]]:
    """在一段连续接缝上求满足 ``Σshift = target_sum`` 的最优平移组合。

    目标键为 ``(-区间配对总数, 区间|shift|之和, 平移量元组)``；以区间和为状态做
    动态规划，每个和只保留键序最小的前缀（目标只依赖区间和，且键可按前缀分解）。
    """

    # sum -> ((-配对数, |shift|和, shift元组), shift 序列)
    states: dict[int, tuple[tuple[int, int, tuple[int, ...]], tuple[int, ...]]] = {
        0: ((0, 0, ()), ())
    }
    for opts in edge_options:
        next_states: dict[
            int, tuple[tuple[int, int, tuple[int, ...]], tuple[int, ...]]
        ] = {}
        for partial_sum, (key, chosen) in states.items():
            neg_pairs, abs_sum, shift_tuple = key
            for shift, pairs in opts.items():
                new_sum = partial_sum + shift
                new_key = (
                    neg_pairs - len(pairs),
                    abs_sum + abs(shift),
                    shift_tuple + (shift,),
                )
                old = next_states.get(new_sum)
                if old is None or new_key < old[0]:
                    next_states[new_sum] = (new_key, chosen + (shift,))
        states = next_states
        if not states:
            return None

    winner = states.get(target_sum)
    if winner is None:
        return None
    return list(winner[1])


def _no_chain_error(
    segments: list[Segment],
    options: dict[tuple[int, int], dict[int, tuple[tuple[int, int], ...]]],
) -> AdjudicationError:
    # 与原实现一致：按排列穷举顺序找第一个「无合法配对」的有向边界作为示例原因。
    first_failure: Optional[tuple[int, int]] = None
    for perm in permutations(range(len(segments))):
        for u, v in zip(perm, perm[1:]):
            if not options[(u, v)]:
                first_failure = (u, v)
                break
        if first_failure is not None:
            break

    if first_failure is None:
        # 每条有向边界都有候选，但不存在让所有边界同时成立的排列。
        return AdjudicationError(
            "无法拼成合法刻度链：不存在使每个相邻边界都有合法配对的段落顺序",
            code="no_valid_chain",
        )
    u, v = first_failure
    return AdjudicationError(
        f"无法拼成合法刻度链：不存在使每个相邻边界都有合法配对的段落顺序"
        f"（例如 段{segments[u].id} → 段{segments[v].id} 边界上没有类型相同的"
        f"可配对刻度）",
        code="no_valid_chain",
    )


def _anchor_blocked_error(anchor: Anchor, ordinal: int) -> AdjudicationError:
    return AdjudicationError(
        f"定位锚点 {ordinal}（段 {anchor.segment_id} 位置 {anchor.position}、"
        f"档案原始水深 {anchor.depth}）阻断：不存在任何满足此前全部锚点的"
        f"段落顺序与接缝平移方案，锚点之间相互矛盾或无法连成满足全部锚点的刻度链",
        code="anchors_unsatisfiable",
    )


def _build_result(
    segments: list[Segment],
    anchors: list[Anchor],
    solution: _Solution,
    options: dict[tuple[int, int], dict[int, tuple[tuple[int, int], ...]]],
) -> Adjudication:
    perm, shifts = solution.perm, solution.shifts
    index_by_id = {s.id: i for i, s in enumerate(segments)}
    used_positions: dict[int, set[int]] = {i: set() for i in range(len(segments))}

    edges: list[EdgeResult] = []
    total_pairs = 0
    total_error = 0
    for u, v, shift in zip(perm, perm[1:], shifts):
        edge = _build_edge(segments[u], segments[v], shift, options[(u, v)][shift])
        edges.append(edge)
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

    # 全局偏移：先以链首段局部坐标为基准（链首段 chain_offset = 0，
    # chain_offset_右 = chain_offset_左 − shift）。
    chain_offsets: list[int] = []
    running = 0
    for edge_index in range(len(perm)):
        if edge_index > 0:
            running -= shifts[edge_index - 1]
        chain_offsets.append(running)
    chain_offset_by_idx = {idx: value for idx, value in zip(perm, chain_offsets)}

    # 有锚点时全局基准由档案水深确定：整体浮动使锚点换算值精确等于档案值；
    # 无锚点时保持链首段为 0，偏移即各段相对全局基准（链首段）的平移。
    datum_shift = 0
    if anchors:
        first_idx = index_by_id[anchors[0].segment_id]
        datum_shift = (
            anchors[0].depth
            - segments[first_idx].marks[anchors[0].position]
            - chain_offset_by_idx[first_idx]
        )

    offsets = [
        SegmentOffset(
            segment_id=segments[idx].id,
            offset=chain_offset_by_idx[idx] + datum_shift,
        )
        for idx in perm
    ]
    global_offset_by_idx = {
        idx: chain_offset_by_idx[idx] + datum_shift for idx in perm
    }

    anchor_resolutions: list[AnchorResolution] = []
    for anchor in anchors:
        idx = index_by_id[anchor.segment_id]
        mark = segments[idx].marks[anchor.position]
        global_depth = mark + global_offset_by_idx[idx]
        anchor_resolutions.append(
            AnchorResolution(
                segment_id=anchor.segment_id,
                position=anchor.position,
                mark=mark,
                archive_depth=anchor.depth,
                global_depth=global_depth,
            )
        )

    return Adjudication(
        order=tuple(segments[idx].id for idx in perm),
        edges=tuple(edges),
        unpaired=tuple(unpaired),
        total_pairs=total_pairs,
        total_error=total_error,
        offsets=tuple(offsets),
        anchors=tuple(anchor_resolutions),
    )
