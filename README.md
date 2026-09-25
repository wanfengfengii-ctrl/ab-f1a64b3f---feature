# 航海测深图 · 刻度链修复裁决服务

海事博物馆修复一张被裁成多段的航海测深图。边缘残留的深度刻度可能重复或缺失，
本服务根据各段沿纸边严格递增的整数刻度及其刻线类型，裁决：

1. 各段按原航线方向的**排列顺序**（每段恰使用一次）；
2. 相邻两段的**重叠刻度配对**（仅允许刻线类型相同、保持各自原始顺序的一对一配对，
   且同一相邻边界内所有配对的平移量必须一致）；
3. 各段的**未配对刻度**；
4. **总拼接误差**（各相邻边界平移量绝对值之和）。

裁决依次按以下层级择优：

1. 最大化全部边界的配对总数；
2. 最小化绝对平移量总和；
3. 按原录入编号序列（编号元组字典序）稳定决胜。

任一边界在所有排列下都找不到合法配对，或输入不合法时，服务返回错误原因，
页面清除旧方案并只显示首个原因。

### 定位锚点（可选）

修复师可额外录入 **2–4 个定位锚点**，锚点必须**覆盖至少两段**。每个锚点指定：

- `segment_id`：引用的段落编号（必须存在）；
- `position`：该段内 0 基刻度位置（必须存在）；
- `depth`：档案中确认的该刻度**原始水深**（整数）。

提供锚点后，裁决在「所有段恰用一次、每处接缝仍满足既有配对规则」的前提下，
**联合**选择段落顺序和各接缝的具体平移方案，使每个锚点换算后的全局水深
（`段内刻度 + 该段全局偏移`）与档案值**精确一致**；不会先逐接缝取局部最优配对
再回头核对锚点。择优层级不变：配对总数 → 总平移误差 → 原编号序列。

锚点引用不存在的段/位置、重复引用同一刻度、数量或覆盖段数不合法时返回首个输入
原因（`400 invalid_input`）；锚点彼此矛盾或无法形成满足全部锚点的链时，清除旧
结论并返回首个阻断锚点（`422 anchors_unsatisfiable`）。不提供 `anchors` 字段时，
请求与结果与原裁决完全兼容（额外返回的 `offsets` 以链首段为基准、`anchors` 为空）。

## 技术栈

- Python 3.11 / FastAPI / Uvicorn（仅标准库 + 少量第三方包）
- 原生 HTML + JavaScript 前端，无构建步骤
- pytest 测试；真实 HTTP 冒烟脚本（标准库 `urllib`）
- Docker / Docker Compose，含健康检查与一次性 `verify` 服务

## 本地运行（无需 Docker）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 <http://localhost:8000/>，录入三至六段（每段四至十个严格递增整数刻度及
逐个对应的 `major` / `minor` / `reference` 类型），点击「裁决」。

## Docker Compose

```bash
# 构建并以后台方式启动 Web 服务（宿主端口默认 8000）
docker compose up -d --build

# 使用自定义宿主机端口
HOST_PORT=9090 docker compose up -d
```

健康检查由 Compose 与 Dockerfile 双重声明，访问 `GET /health`。

### 一次性校验服务 verify

`verify` 是一次性（one-shot）服务：等待 `web` 健康后，依次执行

1. `pytest` 全部代码测试；
2. `python -m compileall` 构建/语法检查；
3. 对运行中的 `web` 发起真实 HTTP API 冒烟；

随后自行退出，退出码报告综合结果（0 = 全部通过，非零 = 存在失败）：

```bash
docker compose build
docker compose run --rm verify
docker compose logs verify      # 查看详细输出
```

> 冒烟脚本 `scripts/smoke.py` 通过 `BASE_URL` 环境变量指定目标，
> Compose 中自动设为 `http://web:8000`。

## API

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /api/adjudicate`

请求：

```json
{
  "segments": [
    {"id": 1, "marks": [1, 2, 3, 4], "types": ["major", "major", "major", "major"]},
    {"id": 2, "marks": [4, 5, 6, 7], "types": ["major", "major", "major", "major"]},
    {"id": 3, "marks": [7, 8, 9, 10], "types": ["major", "major", "major", "major"]}
  ],
  "anchors": [
    {"segment_id": 1, "position": 0, "depth": 100},
    {"segment_id": 3, "position": 3, "depth": 103}
  ]
}
```

`anchors` 可整体省略（或为 `null`），即原裁决模式。

成功响应（`200`，节略）：

```json
{
  "ok": true,
  "result": {
    "order": [1, 2, 3],
    "offsets": [
      {"segment_id": 1, "offset": 99},
      {"segment_id": 2, "offset": 96},
      {"segment_id": 3, "offset": 93}
    ],
    "edges": [
      {
        "left_id": 1, "right_id": 2, "shift": 3, "pair_count": 4, "error": 3,
        "pairs": [
          {"left_position": 0, "right_position": 0,
           "mark": 1, "aligned_mark": 4, "type": "major"}
        ]
      }
    ],
    "unpaired": [],
    "anchors": [
      {"segment_id": 1, "position": 0, "mark": 1,
       "archive_depth": 100, "global_depth": 100},
      {"segment_id": 3, "position": 3, "mark": 10,
       "archive_depth": 103, "global_depth": 103}
    ],
    "total_pairs": 8,
    "total_error": 6
  }
}
```

错误响应：

- `400 {"ok": false, "error": {"code": "invalid_input" | "invalid_json", "message": "首个原因"}}`
- `422 {"ok": false, "error": {"code": "no_valid_chain" | "anchors_unsatisfiable", "message": "..."}}`

页面展示的顺序、配对、未配对刻度、平移量与误差均直接来自该业务 API，
前端不做任何重新计算。

## 输入约束

| 项目 | 约束 |
| --- | --- |
| 段数 | 3–6 段 |
| 每段刻度数 | 4–10 个 |
| 刻度 | 整数，沿纸边严格递增 |
| 刻线类型 | `major` / `minor` / `reference`，与刻度逐个对应 |
| 段落编号 | 整数且全局唯一（顺序与数值不作要求） |
| 定位锚点 | 可选 2–4 个，至少覆盖两段；引用存在的段与 0 基刻度位置，档案水深为整数，同一刻度不得重复引用 |

## 测试

```bash
.venv/bin/python -m pytest
```

- `tests/test_solver_edges.py`：单边界配对规则（类型过滤、一致平移、顺序保持）
- `tests/test_solver_chain.py`：层级目标、稳定决胜、未配对刻度、无合法链
- `tests/test_solver_anchors.py`：锚点联合择优、偏移/换算证据、锚点改变顺序、首个阻断锚点
- `tests/test_solver_property.py`：随机输入对比独立暴力 oracle（含锚点场景）
- `tests/test_anchors_validation.py`：锚点各类非法输入的首个原因
- `tests/test_validation.py`：各类非法输入的首个原因
- `tests/test_api.py`：健康检查、成功/失败路径与证据字段（含锚点，真实 ASGI 栈）
