"""/api/adjudicate 与 /health 的 API 集成测试（走真实 ASGI 栈）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def valid_payload():
    return {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ]
    }


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_served() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "裁决" in resp.text


def test_adjudicate_success_shape() -> None:
    resp = client.post("/api/adjudicate", json=valid_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    result = body["result"]
    # 服务端返回的每项证据字段齐全
    assert result["order"] == [1, 2, 3]
    assert len(result["edges"]) == 2
    for edge in result["edges"]:
        assert {"left_id", "right_id", "shift", "pair_count", "error", "pairs"} <= edge.keys()
        for pair in edge["pairs"]:
            assert {
                "left_position", "right_position", "mark", "aligned_mark", "type",
            } <= pair.keys()
    assert isinstance(result["unpaired"], list)
    # 等间距同类型段：最优为 shift=3，每条边界 4 对，共 8 对、|shift| 和 6。
    assert result["total_pairs"] == 8
    assert result["total_error"] == 6
    # 所有证据来自服务端计算：order 中每段恰出现一次
    assert sorted(result["order"]) == [1, 2, 3]


def test_pairs_carry_consistent_shift_and_aligned_mark() -> None:
    payload = {
        "segments": [
            {"id": 10, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 20, "marks": [6, 7, 8, 9], "types": ["major"] * 4},
            {"id": 30, "marks": [11, 12, 13, 14], "types": ["major"] * 4},
        ]
    }
    result = client.post("/api/adjudicate", json=payload).json()["result"]
    assert result["order"] == [10, 20, 30]
    for edge in result["edges"]:
        for pair in edge["pairs"]:
            assert pair["aligned_mark"] == pair["mark"] + edge["shift"]


def test_invalid_input_returns_400_and_first_reason() -> None:
    payload = valid_payload()
    payload["segments"][0]["marks"] = [1, 2, 2, 4]
    resp = client.post("/api/adjudicate", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_input"
    assert "严格递增" in body["error"]["message"]


def test_malformed_json_returns_400() -> None:
    resp = client.post(
        "/api/adjudicate",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_json"


def test_no_valid_chain_returns_422() -> None:
    # 与求解器测试一致：第三段类型与前两段完全不相交，无任何合法排列。
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["minor"] * 4},
        ]
    }
    resp = client.post("/api/adjudicate", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "no_valid_chain"
    assert "无法拼成" in body["error"]["message"]


def test_each_segment_used_exactly_once_in_response() -> None:
    payload = valid_payload()
    result = client.post("/api/adjudicate", json=payload).json()["result"]
    chain = result["order"]
    assert len(chain) == 3
    assert len(set(chain)) == 3
    edge_endpoints = [e["left_id"] for e in result["edges"]] + [
        result["edges"][-1]["right_id"]
    ]
    assert edge_endpoints == chain
