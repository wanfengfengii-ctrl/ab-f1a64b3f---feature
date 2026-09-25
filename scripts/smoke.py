"""对运行中的服务做真实 HTTP API 冒烟。

通过 BASE_URL 环境变量指定目标（默认 http://127.0.0.1:8000），
覆盖：健康检查、首页、裁决成功（并独立复核服务端返回的每项证据）、
非法输入 400、无合法链 422。任一断言失败即以非零退出码退出。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def request(method: str, path: str, payload=None) -> tuple[int, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_health() -> None:
    status, body = request("GET", "/health")
    assert_true(status == 200, f"/health 状态码应为 200，实际 {status}")
    assert_true(body == {"status": "ok"}, f"/health 返回异常：{body}")
    print(f"[ok] GET /health -> {status} {body}")


def check_index() -> None:
    # 首页用 GET 直接抓，验证 HTML 已由服务提供。
    with urllib.request.urlopen(BASE_URL + "/", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert_true(resp.status == 200, f"/ 状态码应为 200，实际 {resp.status}")
    assert_true("刻度链" in html, "首页缺少应用标题")
    print(f"[ok] GET / -> {resp.status}（HTML {len(html)} 字节）")


def check_success() -> None:
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 4, 7, 10],
             "types": ["major", "minor", "reference", "major"]},
            {"id": 2, "marks": [10, 13, 16, 19],
             "types": ["major", "minor", "reference", "major"]},
            {"id": 3, "marks": [19, 22, 25, 28],
             "types": ["major", "minor", "reference", "major"]},
        ]
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 200, f"裁决成功路径状态码应为 200，实际 {status}：{body}")
    assert_true(body.get("ok") is True, "成功响应 ok 必须为 true")
    result = body["result"]

    # 独立复核服务端返回的每项证据
    segments = {s["id"]: s for s in payload["segments"]}
    order = result["order"]
    assert_true(sorted(order) == sorted(segments), "顺序必须使每段恰使用一次")
    assert_true(len(result["edges"]) == len(order) - 1, "边界数应为段数-1")

    paired: dict[int, set[int]] = {sid: set() for sid in segments}
    total_pairs = 0
    total_error = 0
    for edge, left_id, right_id in zip(result["edges"], order, order[1:]):
        assert_true(edge["left_id"] == left_id and edge["right_id"] == right_id,
                    f"边界端点与顺序不一致：{edge}")
        shifts = set()
        for pair in edge["pairs"]:
            lp, rp = pair["left_position"], pair["right_position"]
            left = segments[left_id]
            right = segments[right_id]
            # 类型相同
            assert_true(left["types"][lp] == right["types"][rp] == pair["type"],
                        f"配对刻线类型不一致：{pair}")
            # 边界内平移量一致，对齐刻度真实存在于右段
            shift = right["marks"][rp] - left["marks"][lp]
            shifts.add(shift)
            assert_true(pair["aligned_mark"] == left["marks"][lp] + shift,
                        "aligned_mark 与平移量不一致")
            assert_true(right["marks"][rp] == pair["aligned_mark"],
                        "对齐刻度在右段不存在")
            paired[left_id].add(lp)
            paired[right_id].add(rp)
        assert_true(len(shifts) == 1, f"边界 {left_id}->{right_id} 平移量不一致：{shifts}")
        assert_true(edge["shift"] == shifts.pop(), "边界 shift 字段与配对不符")
        assert_true(edge["pair_count"] == len(edge["pairs"]), "pair_count 不匹配")
        total_pairs += edge["pair_count"]
        total_error += abs(edge["shift"])

    assert_true(result["total_pairs"] == total_pairs, "total_pairs 汇总不一致")
    assert_true(result["total_error"] == total_error, "total_error 汇总不一致")

    # 配对位置 + 未配对位置恰好覆盖每段全部刻度一次
    for sid, segment in segments.items():
        unpaired_positions = {
            u["position"] for u in result["unpaired"] if u["segment_id"] == sid
        }
        covered = paired[sid] | unpaired_positions
        assert_true(covered == set(range(len(segment["marks"]))),
                    f"段 {sid} 的配对/未配对刻度未能恰好覆盖全部刻度")
        for u in result["unpaired"]:
            if u["segment_id"] == sid:
                pos = u["position"]
                assert_true(u["mark"] == segment["marks"][pos], "未配对刻度值不匹配")
                assert_true(u["type"] == segment["types"][pos], "未配对刻线类型不匹配")

    print(f"[ok] POST /api/adjudicate（成功）-> 顺序 {order}，"
          f"{total_pairs} 对配对，总误差 {total_error}")


def check_invalid_input() -> None:
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 2, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ]
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 400, f"非法输入应返回 400，实际 {status}")
    assert_true(body["ok"] is False and "严格递增" in body["error"]["message"],
                f"错误原因不正确：{body}")
    print(f"[ok] POST /api/adjudicate（非法输入）-> {status} {body['error']['message']}")


def check_no_valid_chain() -> None:
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["minor"] * 4},
        ]
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 422, f"无合法链应返回 422，实际 {status}")
    assert_true(body["ok"] is False and body["error"]["code"] == "no_valid_chain",
                f"错误对象不正确：{body}")
    print(f"[ok] POST /api/adjudicate（无合法链）-> {status} {body['error']['message']}")


def check_anchored_success() -> None:
    # 等间距同构段：顺序 1→2→3，接缝 shift 各为 3，段偏移 0,-3,-6。
    # 锚点：段1 刻度1→档案水深1；段3 刻度10→档案水深4（10 + (-6) = 4）。
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        "anchors": [
            {"segment_id": 1, "position": 0, "archived_depth": 1},
            {"segment_id": 3, "position": 3, "archived_depth": 4},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 200, f"锚定裁决成功路径应为 200，实际 {status}：{body}")
    result = body["result"]
    assert_true(result["anchor_constrained"] is True, "应标记为锚定裁决")
    assert_true(result["order"] == [1, 2, 3], f"顺序异常：{result['order']}")

    offsets = {o["segment_id"]: o["offset"] for o in result["segment_offsets"]}
    assert_true([o["segment_id"] for o in result["segment_offsets"]] == result["order"],
                "偏移必须按链顺序逐段给出")
    # offset_右 = offset_左 − shift
    for edge in result["edges"]:
        assert_true(
            offsets[edge["right_id"]] == offsets[edge["left_id"]] - edge["shift"],
            f"段偏移与接缝平移量不一致：{edge}",
        )
    assert_true(offsets == {1: 0, 2: -3, 3: -6}, f"偏移异常：{offsets}")

    assert_true(len(result["anchors"]) == 2, "应返回两个锚点的逐点换算")
    for item in result["anchors"]:
        assert_true(item["global_depth"] == item["mark"] + item["offset"],
                    f"换算全局水深与刻度+偏移不一致：{item}")
        assert_true(item["global_depth"] == item["archived_depth"],
                    f"锚点换算值必须与档案水深精确一致：{item}")
        assert_true(item["matches_archive"] is True, "matches_archive 应为 true")
    print(f"[ok] POST /api/adjudicate（锚定裁决成功）-> 顺序 {result['order']}，"
          f"偏移 {result['segment_offsets']}，两锚点均精确一致")


def check_anchored_conflict() -> None:
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        "anchors": [
            {"segment_id": 1, "position": 0, "archived_depth": 1},
            {"segment_id": 3, "position": 3, "archived_depth": 999},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 422, f"锚点矛盾应返回 422，实际 {status}")
    assert_true(body["error"]["code"] == "anchor_conflict", f"错误码异常：{body}")
    assert_true("首个阻断锚点" in body["error"]["message"], f"原因应指认首个阻断锚点：{body}")
    print(f"[ok] POST /api/adjudicate（锚点矛盾）-> {status} {body['error']['message'][:40]}...")


def check_anchor_bad_reference() -> None:
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        "anchors": [
            {"segment_id": 42, "position": 0, "archived_depth": 1},
            {"segment_id": 1, "position": 0, "archived_depth": 1},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 400, f"锚点引用不存在的段应返回 400，实际 {status}")
    assert_true("不存在的段 42" in body["error"]["message"], f"首个原因异常：{body}")
    print(f"[ok] POST /api/adjudicate（锚点非法引用）-> {status} {body['error']['message']}")


def main() -> int:
    print(f"对 {BASE_URL} 执行 API 冒烟 ...")
    try:
        check_health()
        check_index()
        check_success()
        check_invalid_input()
        check_no_valid_chain()
        check_anchored_success()
        check_anchored_conflict()
        check_anchor_bad_reference()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟失败：{exc}", file=sys.stderr)
        return 1
    print("冒烟全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
