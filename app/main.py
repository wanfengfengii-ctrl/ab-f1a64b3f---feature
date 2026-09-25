"""FastAPI 入口：页面、健康检查与裁决业务 API。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .solver import AdjudicationError, adjudicate
from .validation import ValidationFailure, validate_payload

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="航海测深图刻度链裁决服务",
    version="1.0.0",
    description="校验被裁切段落后的航海测深图能否按原航线顺序拼成可信刻度链。",
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

    成功返回段落顺序、相邻边界配对、未配对刻度与总拼接误差；
    输入不合法或无合法链时返回错误对象，页面据此清除旧方案并展示首个原因。
    """

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response(400, "请求体不是合法的 JSON", "invalid_json")

    try:
        segments = validate_payload(payload)
        result = adjudicate(segments)
    except ValidationFailure as exc:
        return _error_response(400, str(exc), "invalid_input")
    except AdjudicationError as exc:
        return _error_response(422, str(exc), exc.code)

    return JSONResponse(status_code=200, content={"ok": True, "result": _serialize(result)})


def _serialize(result) -> dict:
    return {
        "order": list(result.order),
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
    }
