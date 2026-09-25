"""请求体校验：返回面向修复师的「首个原因」。"""

from __future__ import annotations

from typing import Any

from .solver import (
    MAX_ANCHORS,
    MAX_MARKS_PER_SEGMENT,
    MAX_SEGMENTS,
    MIN_ANCHORS,
    MIN_MARKS_PER_SEGMENT,
    MIN_SEGMENTS,
    MARK_TYPES,
    Anchor,
    Segment,
)


class ValidationFailure(Exception):
    """输入不合法；消息即为需要展示的首个原因。"""


def _is_real_int(value: Any) -> bool:
    # bool 是 int 的子类，JSON 的 true/false 不能当作刻度或编号。
    return isinstance(value, int) and not isinstance(value, bool)


def validate_payload(payload: Any) -> tuple[list[Segment], list[Anchor]]:
    if not isinstance(payload, dict):
        raise ValidationFailure("请求体必须是包含 segments 的 JSON 对象")

    if "segments" not in payload:
        raise ValidationFailure("缺少 segments 字段")

    raw_segments = payload["segments"]
    if not isinstance(raw_segments, list):
        raise ValidationFailure("segments 必须是段落数组")

    count = len(raw_segments)
    if count < MIN_SEGMENTS:
        raise ValidationFailure(f"图纸段数为 {count}，至少需要 {MIN_SEGMENTS} 段")
    if count > MAX_SEGMENTS:
        raise ValidationFailure(f"图纸段数为 {count}，最多允许 {MAX_SEGMENTS} 段")

    segments: list[Segment] = []
    seen_ids: set[int] = set()

    for index, raw in enumerate(raw_segments):
        where = f"第 {index + 1} 段（录入顺序）"

        if not isinstance(raw, dict):
            raise ValidationFailure(f"{where}：段落必须是对象")

        if "id" not in raw:
            raise ValidationFailure(f"{where}：缺少段落编号 id")
        seg_id = raw["id"]
        if not _is_real_int(seg_id):
            raise ValidationFailure(f"{where}：编号 id 必须是整数")
        if seg_id in seen_ids:
            raise ValidationFailure(f"段落编号 {seg_id} 重复，编号必须唯一")
        seen_ids.add(seg_id)

        if "marks" not in raw:
            raise ValidationFailure(f"段 {seg_id}：缺少刻度 marks 字段")
        marks = raw["marks"]
        if not isinstance(marks, list):
            raise ValidationFailure(f"段 {seg_id}：刻度 marks 必须是数组")

        if "types" not in raw:
            raise ValidationFailure(f"段 {seg_id}：缺少刻线类型 types 字段")
        types = raw["types"]
        if not isinstance(types, list):
            raise ValidationFailure(f"段 {seg_id}：刻线类型 types 必须是数组")

        mark_count = len(marks)
        if mark_count < MIN_MARKS_PER_SEGMENT:
            raise ValidationFailure(
                f"段 {seg_id}：只有 {mark_count} 个刻度，"
                f"每段至少需要 {MIN_MARKS_PER_SEGMENT} 个"
            )
        if mark_count > MAX_MARKS_PER_SEGMENT:
            raise ValidationFailure(
                f"段 {seg_id}：有 {mark_count} 个刻度，"
                f"每段最多允许 {MAX_MARKS_PER_SEGMENT} 个"
            )
        if len(types) != mark_count:
            raise ValidationFailure(
                f"段 {seg_id}：刻度数（{mark_count}）与刻线类型数（{len(types)}）"
                f"不一致，需逐个对应"
            )

        for position, value in enumerate(marks):
            if not _is_real_int(value):
                raise ValidationFailure(
                    f"段 {seg_id}：第 {position + 1} 个刻度不是整数"
                )

        for position in range(1, mark_count):
            if marks[position] <= marks[position - 1]:
                raise ValidationFailure(
                    f"段 {seg_id}：刻度必须沿纸边严格递增，"
                    f"第 {position}、{position + 1} 个刻度为 "
                    f"{marks[position - 1]}、{marks[position]}"
                )

        for position, value in enumerate(types):
            if not isinstance(value, str):
                raise ValidationFailure(
                    f"段 {seg_id}：第 {position + 1} 个刻线类型必须是字符串"
                )
            if value not in MARK_TYPES:
                allowed = "、".join(MARK_TYPES)
                raise ValidationFailure(
                    f"段 {seg_id}：第 {position + 1} 个刻线类型 {value!r} 不可辨识，"
                    f"允许的类型为 {allowed}"
                )

        segments.append(
            Segment(
                id=seg_id,
                marks=tuple(marks),
                types=tuple(types),
            )
        )

    anchors = _validate_anchors(payload, segments)
    return segments, anchors


def _validate_anchors(payload: dict, segments: list[Segment]) -> list[Anchor]:
    """校验可选的 anchors 字段，按录入顺序报告首个原因。

    约束：0 个（省略，走原裁决）或 {MIN_ANCHORS}–{MAX_ANCHORS} 个；须覆盖至少
    两段；段必须存在、位置必须为该段内合法刻度位置；同一锚点不能重复引用
    「段 + 刻度位置」。
    """

    if "anchors" not in payload:
        return []

    raw_anchors = payload["anchors"]
    if not isinstance(raw_anchors, list):
        raise ValidationFailure("anchors 必须是定位锚点数组")

    anchor_count = len(raw_anchors)
    if anchor_count == 0:
        # 显式空数组视同未提供锚点，保持与原裁决请求兼容。
        return []
    if anchor_count < MIN_ANCHORS:
        raise ValidationFailure(
            f"定位锚点为 {anchor_count} 个，锚定裁决至少需要 {MIN_ANCHORS} 个"
        )
    if anchor_count > MAX_ANCHORS:
        raise ValidationFailure(
            f"定位锚点为 {anchor_count} 个，最多允许 {MAX_ANCHORS} 个"
        )

    by_id = {segment.id: segment for segment in segments}
    anchors: list[Anchor] = []
    seen_refs: set[tuple[int, int]] = set()
    covered_ids: set[int] = set()

    for index, raw in enumerate(raw_anchors):
        where = f"第 {index + 1} 个锚点（录入顺序）"
        if not isinstance(raw, dict):
            raise ValidationFailure(f"{where}：锚点必须是对象")

        if "segment_id" not in raw:
            raise ValidationFailure(f"{where}：缺少段落编号 segment_id")
        segment_id = raw["segment_id"]
        if not _is_real_int(segment_id):
            raise ValidationFailure(f"{where}：段落编号 segment_id 必须是整数")
        if segment_id not in by_id:
            raise ValidationFailure(
                f"{where}：引用了不存在的段 {segment_id}，段落编号必须已在 segments 中录入"
            )
        segment = by_id[segment_id]

        if "position" not in raw:
            raise ValidationFailure(f"{where}（段 {segment_id}）：缺少刻度位置 position")
        position = raw["position"]
        if not _is_real_int(position):
            raise ValidationFailure(f"{where}（段 {segment_id}）：刻度位置 position 必须是整数")
        if position < 0 or position >= len(segment.marks):
            raise ValidationFailure(
                f"{where}（段 {segment_id}）：刻度位置 {position} 不存在，"
                f"该段只有 {len(segment.marks)} 个刻度（位置 0–{len(segment.marks) - 1}）"
            )

        ref = (segment_id, position)
        if ref in seen_refs:
            raise ValidationFailure(
                f"{where}：与更早的锚点重复引用段 {segment_id} 的同一刻度位置 {position}"
                f"（刻度 {segment.marks[position]}），同一刻度只能被一个锚点引用"
            )
        seen_refs.add(ref)
        covered_ids.add(segment_id)

        if "archived_depth" not in raw:
            raise ValidationFailure(f"{where}（段 {segment_id}）：缺少档案原始水深 archived_depth")
        archived_depth = raw["archived_depth"]
        if not _is_real_int(archived_depth):
            raise ValidationFailure(
                f"{where}（段 {segment_id}）：档案原始水深 archived_depth 必须是整数"
            )

        anchors.append(
            Anchor(
                index=index,
                segment_id=segment_id,
                position=position,
                archived_depth=archived_depth,
            )
        )

    if len(covered_ids) < 2:
        the_id = next(iter(covered_ids))
        raise ValidationFailure(
            f"全部 {anchor_count} 个锚点都落在段 {the_id} 上，"
            f"定位锚点必须覆盖至少两段"
        )

    return anchors
