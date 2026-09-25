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


def check_anchored_success() -> None:
    # 带定位锚点的裁决：锚点要求两段接缝平移之和为 5（联合择优，不能逐接缝贪心）。
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        # 段1@0(mark1) 全局 100；段3@3(mark10) 全局 104 ⇒ 接缝平移之和为 5。
        "anchors": [
            {"segment_id": 1, "position": 0, "depth": 100},
            {"segment_id": 3, "position": 3, "depth": 104},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 200, f"锚定裁决成功路径应为 200，实际 {status}：{body}")
    result = body["result"]

    # 每段恰用一次；偏移随顺序给出
    assert_true(sorted(result["order"]) == [1, 2, 3], "锚定裁决顺序必须使用全部段")
    assert_true(len(result["offsets"]) == 3, "必须返回每段相对全局基准的偏移")
    offsets = {o["segment_id"]: o["offset"] for o in result["offsets"]}

    # 独立复核：每条接缝类型一致、平移量一致，且偏移 = 左段偏移 − shift
    segments = {s["id"]: s for s in payload["segments"]}
    for edge in result["edges"]:
        assert_true(
            offsets[edge["right_id"]] == offsets[edge["left_id"]] - edge["shift"],
            f"偏移与接缝平移不一致：{edge}",
        )
        shifts = set()
        for pair in edge["pairs"]:
            left = segments[edge["left_id"]]
            right = segments[edge["right_id"]]
            assert_true(
                left["types"][pair["left_position"]]
                == right["types"][pair["right_position"]]
                == pair["type"],
                "锚定裁决配对刻线类型不一致",
            )
            shifts.add(
                right["marks"][pair["right_position"]]
                - left["marks"][pair["left_position"]]
            )
        assert_true(shifts == {edge["shift"]}, "锚定裁决边界平移量不一致")

    # 逐锚点换算值必须与档案原始水深精确一致
    assert_true(len(result["anchors"]) == 2, "必须返回逐锚点换算证据")
    for anchor in result["anchors"]:
        assert_true(
            anchor["global_depth"] == anchor["archive_depth"],
            f"锚点换算水深 {anchor['global_depth']} 与档案值 "
            f"{anchor['archive_depth']} 不精确一致",
        )

    print(
        f"[ok] POST /api/adjudicate（定位锚点）-> 顺序 {result['order']}，"
        f"接缝 {[e['shift'] for e in result['edges']]}，"
        f"{sum(1 for a in result['anchors'] if a['global_depth'] == a['archive_depth'])}"
        f" 个锚点全部精确一致"
    )


def check_anchored_invalid_input() -> None:
    # 锚点引用不存在的段：400 + 首个原因。
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        "anchors": [
            {"segment_id": 9, "position": 0, "depth": 100},
            {"segment_id": 1, "position": 0, "depth": 100},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 400, f"非法锚点应返回 400，实际 {status}")
    assert_true(
        body["ok"] is False and "不存在" in body["error"]["message"],
        f"错误原因不正确：{body}",
    )
    print(
        f"[ok] POST /api/adjudicate（非法锚点）-> {status} "
        f"{body['error']['message']}"
    )


def check_anchors_unsatisfiable() -> None:
    # 接缝平移之和需要 13（超出可达 0..6），锚点互相矛盾：422 + 首个阻断锚点。
    payload = {
        "segments": [
            {"id": 1, "marks": [1, 2, 3, 4], "types": ["major"] * 4},
            {"id": 2, "marks": [4, 5, 6, 7], "types": ["major"] * 4},
            {"id": 3, "marks": [7, 8, 9, 10], "types": ["major"] * 4},
        ],
        "anchors": [
            {"segment_id": 1, "position": 0, "depth": 100},
            {"segment_id": 3, "position": 3, "depth": 96},
        ],
    }
    status, body = request("POST", "/api/adjudicate", payload)
    assert_true(status == 422, f"锚点矛盾应返回 422，实际 {status}")
    assert_true(
        body["ok"] is False
        and body["error"]["code"] == "anchors_unsatisfiable"
        and "锚点 2" in body["error"]["message"],
        f"错误对象不正确：{body}",
    )
    print(
        f"[ok] POST /api/adjudicate（锚点阻断）-> {status} "
        f"{body['error']['message']}"
    )


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


def main() -> int:
    print(f"对 {BASE_URL} 执行 API 冒烟 ...")
    try:
        check_health()
        check_index()
        check_success()
        check_anchored_success()
        check_anchored_invalid_input()
        check_anchors_unsatisfiable()
        check_invalid_input()
        check_no_valid_chain()
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 冒烟失败：{exc}", file=sys.stderr)
        return 1
    print("冒烟全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
