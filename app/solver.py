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

定位锚点（锚定裁决）
====================
修复师可额外录入 2–4 个定位锚点：每个锚点指定某段、该段内刻度位置以及档案中
确认的原始水深。锚点必须覆盖至少两段。锚定裁决在「所有段恰用一次、每处接缝
仍满足既有配对规则」的前提下，**联合**选择段落顺序与各接缝的具体平移方案
（不能逐接缝先选局部最优再回头检查锚点），令每个锚点刻度换算后的全局水深
（刻度 + 该段相对全局基准的偏移）与档案值精确一致；择优层级与原裁决相同。
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

#: 定位锚点数量约束（二至四个，且须覆盖至少两段）
MIN_ANCHORS = 2
MAX_ANCHORS = 4

#: 一处接缝的一种平移方案：(平移量, 配对位置对元组)
ShiftChoice = tuple[int, tuple[tuple[int, int], ...]]


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
class Anchor:
    """一个定位锚点：段 ``segment_id`` 的 ``position`` 处刻度对应档案原始水深。

    ``index`` 为锚点录入顺序（0 基），用于在矛盾时指认首个阻断锚点。
    """

    index: int
    segment_id: int
    position: int
    archived_depth: int


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
class AnchorResolution:
    """一个锚点在裁决链中的换算证据。"""

    index: int
    segment_id: int
    position: int
    mark: int
    mark_type: str
    offset: int
    archived_depth: int
    global_depth: int


@dataclass(frozen=True)
class Adjudication:
    """完整裁决结果。"""

    order: tuple[int, ...]
    edges: tuple[EdgeResult, ...]
    unpaired: tuple[UnpairedMark, ...]
    total_pairs: int
    total_error: int
    #: 各段相对全局基准的偏移，顺序与 ``order`` 对齐；首段恒为 0。
    offsets: tuple[int, ...] = ()
    #: 逐锚点换算证据，按锚点录入顺序排列。
    anchor_resolutions: tuple[AnchorResolution, ...] = ()
    #: 本次裁决是否由定位锚点联合约束。
    anchor_constrained: bool = False


def edge_choices(left: Segment, right: Segment) -> Optional[list[ShiftChoice]]:
    """枚举 ``left → right`` 边界的全部合法平移方案，按接缝内偏好排序。

    同一平移量 ``d`` 下的所有可配对刻度天然构成合法配对：
    ``mark_right = mark_left + d`` 与刻度严格递增共同保证两侧位置一一对应且
    顺序一致。返回按「配对数最多、|d| 最小、d 最小」排序的方案列表；
    两侧没有任何同类型刻度时返回 ``None``（该有向边界不可能存在）。
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

    choices: list[ShiftChoice] = [
        (shift, tuple(raw_pairs)) for shift, raw_pairs in by_shift.items()
    ]
    # 收集顺序为左位置外层、右位置内层，即按左位置升序输出。
    choices.sort(key=lambda choice: (-len(choice[1]), abs(choice[0]), choice[0]))
    return choices


def best_edge(left: Segment, right: Segment) -> Optional[EdgeResult]:
    """计算 ``left → right`` 边界的局部最优配对，无任何同类型刻度时返回 ``None``。

    局部最优只在无锚点裁决中逐接缝使用；锚定裁决必须联合选择接缝方案，
    不能直接采用本函数的结果。
    """

    choices = edge_choices(left, right)
    if choices is None:
        return None
    return _make_edge(left, right, choices[0])


def _make_edge(
    left: Segment, right: Segment, choice: ShiftChoice
) -> EdgeResult:
    shift, raw_pairs = choice
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
            for a, b in raw_pairs
        ),
    )


def adjudicate(
    segments: list[Segment], anchors: Optional[list[Anchor]] = None
) -> Adjudication:
    """在全部段落排列中搜索最优刻度链。

    段数至多 6，排列数至多 720，直接穷举；每个有向边界的全部合法平移方案
    只计算一次并缓存。排序键为 ``(-配对总数, |平移量|总和, 编号元组)``，
    编号元组字典序即「按原录入编号序列稳定决胜」。

    未提供锚点时走原裁决路径，行为与历史完全一致；提供锚点时由
    :func:`_adjudicate_anchored` 联合搜索段落顺序与各接缝平移方案。
    """

    anchor_list = list(anchors or [])

    n = len(segments)
    choice_cache: dict[tuple[int, int], Optional[list[ShiftChoice]]] = {}
    for i in range(n):
        for j in range(n):
            if i != j:
                choice_cache[(i, j)] = edge_choices(segments[i], segments[j])

    if not anchor_list:
        return _adjudicate_legacy(segments, choice_cache)
    return _adjudicate_anchored(segments, anchor_list, choice_cache)


def _adjudicate_legacy(
    segments: list[Segment],
    choice_cache: dict[tuple[int, int], Optional[list[ShiftChoice]]],
) -> Adjudication:
    """无锚点裁决：每处接缝独立取局部最优平移方案。"""

    n = len(segments)
    edge_cache: dict[tuple[int, int], Optional[EdgeResult]] = {
        (u, v): (
            None
            if choice_cache[(u, v)] is None
            else _make_edge(segments[u], segments[v], choice_cache[(u, v)][0])
        )
        for u in range(n)
        for v in range(n)
        if u != v
    }

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

    return _build_result(segments, best_perm, best_edges, (), False)


def _anchor_requirements(
    segments: list[Segment], anchors: list[Anchor]
) -> dict[int, list[tuple[Anchor, int]]]:
    """把锚点换算为「段数组下标 → [(锚点, 该段必需的全局偏移)]」。

    锚点要求 ``刻度 + 段偏移 = 档案水深``，故必需偏移为 ``档案水深 − 刻度``。
    """

    index_by_id = {s.id: i for i, s in enumerate(segments)}
    required: dict[int, list[tuple[Anchor, int]]] = {}
    for anchor in anchors:
        node = index_by_id[anchor.segment_id]
        mark = segments[node].marks[anchor.position]
        required.setdefault(node, []).append(
            (anchor, anchor.archived_depth - mark)
        )
    return required


#: 固定两端偏移的接缝段上的最优路径：(配对数, |平移|和, 逐接缝平移量)
GapPath = tuple[int, int, tuple[int, ...]]


def _expand_forward(
    layer: dict[int, GapPath],
    choices: list[ShiftChoice],
) -> dict[int, GapPath]:
    """从前驱段偏移出发跨过一条接缝：新偏移 = 旧偏移 − shift。"""

    nxt: dict[int, GapPath] = {}
    for offset, (pairs, abs_shift, seq) in layer.items():
        for shift, raw_pairs in choices:
            new_offset = offset - shift
            candidate: GapPath = (
                pairs + len(raw_pairs),
                abs_shift + abs(shift),
                seq + (shift,),
            )
            old = nxt.get(new_offset)
            if old is None or (-candidate[0], candidate[1], candidate[2]) < (
                -old[0],
                old[1],
                old[2],
            ):
                nxt[new_offset] = candidate
    return nxt


def _expand_backward(
    layer: dict[int, GapPath],
    choices: list[ShiftChoice],
) -> dict[int, GapPath]:
    """从后继段偏移逆向跨过一条接缝：前驱偏移 = 后继偏移 + shift。"""

    nxt: dict[int, GapPath] = {}
    for offset, (pairs, abs_shift, seq) in layer.items():
        for shift, raw_pairs in choices:
            new_offset = offset + shift
            candidate: GapPath = (
                pairs + len(raw_pairs),
                abs_shift + abs(shift),
                (shift,) + seq,
            )
            old = nxt.get(new_offset)
            if old is None or (-candidate[0], candidate[1], candidate[2]) < (
                -old[0],
                old[1],
                old[2],
            ):
                nxt[new_offset] = candidate
    return nxt


def _solve_fixed_gap(
    edges: list[list[ShiftChoice]],
    start_offset: int,
    target_offset: int,
) -> Optional[GapPath]:
    """两端偏移都被钉死的接缝段（链首或相邻锚点之间），用中点会合求解。

    前向枚举前 ``L//2`` 条接缝、后向枚举其余接缝，在中点按偏移会合；每侧至多
    跨越 ``ceil(L/2)`` 条接缝，把最坏的 ``|平移方案|^L`` 降为平方根量级。
    返回最优 ``(配对数, |平移|和, 逐接缝平移量)``，无法精确到达目标时 ``None``。
    """

    length = len(edges)
    if length == 0:
        return (0, 0, ()) if start_offset == target_offset else None

    mid = length // 2
    forward: dict[int, GapPath] = {start_offset: (0, 0, ())}
    for choices in edges[:mid]:
        forward = _expand_forward(forward, choices)
        if not forward:
            return None

    backward: dict[int, GapPath] = {target_offset: (0, 0, ())}
    for choices in reversed(edges[mid:]):
        backward = _expand_backward(backward, choices)
        if not backward:
            return None

    best: Optional[GapPath] = None
    for mid_offset, head in forward.items():
        tail = backward.get(mid_offset)
        if tail is None:
            continue
        candidate: GapPath = (
            head[0] + tail[0],
            head[1] + tail[1],
            head[2] + tail[2],
        )
        if best is None or (-candidate[0], candidate[1], candidate[2]) < (
            -best[0],
            best[1],
            best[2],
        ):
            best = candidate
    return best


def _best_solution_for_perm(
    perm: tuple[int, ...],
    choice_cache: dict[tuple[int, int], Optional[list[ShiftChoice]]],
    required: dict[int, list[tuple[Anchor, int]]],
) -> Optional[tuple[int, int, list[int]]]:
    """单个排列上的联合动态规划。

    逐接缝枚举**全部**合法平移方案而不是只取局部最优，与锚点联合决定，锚点
    所在段的偏移必须恰好等于档案值要求。链上相邻两个「偏移被钉死」的位置
    （链首偏移恒为 0、各锚点段偏移固定）之间用中点会合求解（见
    :func:`_solve_fixed_gap`）；最后一个锚点之后偏移不再受约束，用 Pareto
    支配剪枝（后续接缝可选方案与当前偏移无关，配对更多且 |平移|更小者必然
    支配）。段数至多 6 且锚点覆盖至少两段，该组合保证最坏输入也很快。

    返回 ``(配对总数, |平移|总和, 各接缝平移量)``；无法满足锚点时 ``None``。
    """

    pin_offset: dict[int, int] = {0: 0}
    for pos, node in enumerate(perm):
        wanted = {r for _, r in required.get(node, ())}
        if wanted:
            if len(wanted) != 1:
                # 同一段上多个锚点要求不同偏移，段内即矛盾。
                return None
            needed = next(iter(wanted))
            if pos == 0 and needed != 0:
                # 全局基准约定：链首段偏移恒为 0。
                return None
            pin_offset[pos] = needed

    pin_positions = sorted(pin_offset)

    total_pairs = 0
    total_abs = 0
    shifts: list[int] = []

    # 固定端接缝段：链首（偏移 0）→ 首个锚点 → … → 最后一个锚点。
    for start_pos, end_pos in zip(pin_positions, pin_positions[1:]):
        edges: list[list[ShiftChoice]] = []
        for k in range(start_pos, end_pos):
            choices = choice_cache[(perm[k], perm[k + 1])]
            if choices is None:
                return None
            edges.append(choices)
        gap = _solve_fixed_gap(
            edges, pin_offset[start_pos], pin_offset[end_pos]
        )
        if gap is None:
            return None
        pairs, abs_shift, seq = gap
        total_pairs += pairs
        total_abs += abs_shift
        shifts.extend(seq)

    # 自由尾段：最后一个锚点之后偏移不再受约束，Pareto 支配剪枝。
    tail_start = pin_positions[-1]
    tail_layer: dict[int, GapPath] = {
        pin_offset[tail_start]: (total_pairs, total_abs, tuple(shifts))
    }
    for k in range(tail_start, len(perm) - 1):
        choices = choice_cache[(perm[k], perm[k + 1])]
        if choices is None:
            return None
        tail_layer = _expand_forward(tail_layer, choices)
        tail_layer = _pareto_prune_gap(tail_layer)
        if not tail_layer:
            return None

    end_offset, end_path = min(
        tail_layer.items(),
        key=lambda item: (-item[1][0], item[1][1], item[0]),
    )
    total_pairs, total_abs, full_seq = end_path
    del end_offset
    return total_pairs, total_abs, list(full_seq)


def _pareto_prune_gap(states: dict[int, GapPath]) -> dict[int, GapPath]:
    """丢弃被支配状态：另一状态配对数不更少且 |平移|和不更大（其一严格）。

    仅在后续偏移不再被锚点约束时安全：当前偏移不影响后续接缝的可选方案。
    """

    items = list(states.items())
    keep: dict[int, GapPath] = {}
    for offset, state in items:
        pairs, abs_shift = state[0], state[1]
        dominated = False
        for other_offset, other in items:
            if other_offset == offset:
                continue
            if other[0] >= pairs and other[1] <= abs_shift and (
                other[0] > pairs or other[1] < abs_shift
            ):
                dominated = True
                break
        if not dominated:
            keep[offset] = state
    return keep


def _adjudicate_anchored(
    segments: list[Segment],
    anchors: list[Anchor],
    choice_cache: dict[tuple[int, int], Optional[list[ShiftChoice]]],
) -> Adjudication:
    """锚定裁决：联合穷举段落顺序与各接缝平移方案，使全部锚点精确成立。"""

    n = len(segments)
    required = _anchor_requirements(segments, anchors)

    best_key: Optional[tuple[int, int, tuple[int, ...]]] = None
    best_perm: Optional[tuple[int, ...]] = None
    best_shifts: Optional[list[int]] = None

    for perm in permutations(range(n)):
        solution = _best_solution_for_perm(perm, choice_cache, required)
        if solution is None:
            continue
        total_pairs, total_abs_shift, shifts = solution
        id_tuple = tuple(segments[idx].id for idx in perm)
        key = (-total_pairs, total_abs_shift, id_tuple)
        if best_key is None or key < best_key:
            best_key = key
            best_perm = perm
            best_shifts = shifts

    if best_perm is None or best_shifts is None:
        raise _anchor_failure(segments, anchors, n, choice_cache)

    edges: list[EdgeResult] = []
    for u, v, shift in zip(best_perm, best_perm[1:], best_shifts):
        choices = choice_cache[(u, v)]
        assert choices is not None
        chosen = next(choice for choice in choices if choice[0] == shift)
        edges.append(_make_edge(segments[u], segments[v], chosen))

    return _build_result(segments, best_perm, edges, tuple(anchors), True)


def _anchor_failure(
    segments: list[Segment],
    anchors: list[Anchor],
    n: int,
    choice_cache: dict[tuple[int, int], Optional[list[ShiftChoice]]],
) -> AdjudicationError:
    """定位首个阻断锚点：最短的、无法被任何链满足的锚点前缀中的最后一个锚点。"""

    def prefix_feasible(prefix: list[Anchor]) -> bool:
        prefix_required = _anchor_requirements(segments, prefix)
        return any(
            _best_solution_for_perm(perm, choice_cache, prefix_required) is not None
            for perm in permutations(range(n))
        )

    blocker_index = 0
    for j in range(len(anchors)):
        if not prefix_feasible(anchors[: j + 1]):
            blocker_index = j
            break

    anchor = anchors[blocker_index]
    node = next(i for i, s in enumerate(segments) if s.id == anchor.segment_id)
    mark = segments[node].marks[anchor.position]
    where = (
        f"第 {blocker_index + 1} 个锚点（段 {anchor.segment_id} "
        f"位置 {anchor.position}，刻度 {mark}，"
        f"档案原始水深 {anchor.archived_depth}）"
    )
    if blocker_index == 0:
        message = (
            f"锚定裁决失败：{where}是首个阻断锚点——在任何段落顺序与任何合法"
            f"接缝平移方案下，该刻度换算后的全局水深都无法精确等于档案值"
        )
    else:
        message = (
            f"锚定裁决失败：锚点彼此矛盾，{where}是首个阻断锚点——仅用前 "
            f"{blocker_index} 个锚点尚能成链，加入该锚点后不存在同时满足全部"
            f"锚点的段落顺序与接缝平移链（各接缝方案必须与锚点联合选择，"
            f"不能逐接缝先取局部最优再核对锚点）"
        )
    return AdjudicationError(message, code="anchor_conflict")


def _build_result(
    segments: list[Segment],
    perm: tuple[int, ...],
    edges: list[EdgeResult],
    anchors: tuple[Anchor, ...],
    anchor_constrained: bool,
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

    # 各段相对全局基准的偏移：首段为 0；配对满足 右刻度=左刻度+shift，
    # 而全局水深 = 刻度 + 偏移，故下游段偏移 = 上游段偏移 − shift。
    offsets: list[int] = [0]
    for edge in edges:
        offsets.append(offsets[-1] - edge.shift)

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

    offset_by_node = {
        node: offsets[k] for k, node in enumerate(perm)
    }
    resolutions = tuple(
        AnchorResolution(
            index=anchor.index,
            segment_id=anchor.segment_id,
            position=anchor.position,
            mark=segments[index_by_id[anchor.segment_id]].marks[anchor.position],
            mark_type=segments[index_by_id[anchor.segment_id]].types[anchor.position],
            offset=offset_by_node[index_by_id[anchor.segment_id]],
            archived_depth=anchor.archived_depth,
            global_depth=(
                segments[index_by_id[anchor.segment_id]].marks[anchor.position]
                + offset_by_node[index_by_id[anchor.segment_id]]
            ),
        )
        for anchor in anchors
    )

    return Adjudication(
        order=tuple(segments[idx].id for idx in perm),
        edges=tuple(edges),
        unpaired=tuple(unpaired),
        total_pairs=total_pairs,
        total_error=total_error,
        offsets=tuple(offsets),
        anchor_resolutions=resolutions,
        anchor_constrained=anchor_constrained,
    )
