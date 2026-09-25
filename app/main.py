"""FastAPI 入口：页面、健康检查与裁决业务 API。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .solver import Adjudication, AdjudicationError, adjudicate
from .validation import ValidationFailure, validate_payload

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="航海测深图刻度链裁决服务",
    version="1.1.0",
    description="校验被裁切段落后的航海测深图能否按原航线顺序拼成可信刻度链，"
    "并支持二至四个档案定位锚点的锚定联合裁决。",
)


def _error_response(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/adjudicate")
async def adjudicate_endpoint(request: Request) -> JSONResponse:
    """对录入段落进行裁决。

    成功返回段落顺序、各段相对全局基准的偏移、相邻边界配对、未配对刻度与
    总拼接误差；提供 anchors 时额外返回逐锚点换算证据。输入不合法返回 400，
    无合法链或锚点彼此矛盾/无法成链返回 422，页面据此清除旧方案并展示首个原因。
    """

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response(400, "请求体不是合法的 JSON", "invalid_json")

    try:
        segments, anchors = validate_payload(payload)
        result = adjudicate(segments, anchors)
    except ValidationFailure as exc:
        return _error_response(400, str(exc), "invalid_input")
    except AdjudicationError as exc:
        return _error_response(422, str(exc), exc.code)

    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize(result)})


def _serialize(result: Adjudication) -> dict:
    return {
        "order": list(result.order),
        "segment_offsets": [
            {
                "segment_id": seg_id,
                "offset": offset,
            }
            for seg_id, offset in zip(result.order, result.offsets)
        ],
        "edges": [
            {
                "left_id": edge.left_id,
                "right_id": edge.right_id,
                "shift": edge.shift,
                "pair_count": edge.pair_count,
                "error": edge.error,
                "pairs": [
                    {
                        "left_position": pair.left_position,
                        "right_position": pair.right_position,
                        "mark": pair.mark,
                        "aligned_mark": pair.mark + edge.shift,
                        "type": pair.mark_type,
                    }
                    for pair in edge.pairs
                ],
            }
            for edge in result.edges
        ],
        "unpaired": [
            {
                "segment_id": item.segment_id,
                "position": item.position,
                "mark": item.mark,
                "type": item.mark_type,
            }
            for item in result.unpaired
        ],
        "total_pairs": result.total_pairs,
        "total_error": result.total_error,
        "anchor_constrained": result.anchor_constrained,
        "anchors": [
            {
                "index": item.index,
                "segment_id": item.segment_id,
                "position": item.position,
                "mark": item.mark,
                "type": item.mark_type,
                "offset": item.offset,
                "archived_depth": item.archived_depth,
                "global_depth": item.global_depth,
                "matches_archive": item.global_depth == item.archived_depth,
            }
            for item in result.anchor_resolutions
        ],
    }
