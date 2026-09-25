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

## 档案定位锚点（锚定裁决）

修复师可在既有裁决页额外录入 **2–4 个定位锚点**（也可不录，走原裁决）。
每个锚点指定：

- `segment_id`：某段的段落编号（必须已录入）；
- `position`：该段内刻度位置（0 基，必须真实存在）；
- `archived_depth`：档案中确认的该刻度**原始水深**（整数）。

锚点必须**覆盖至少两段**，且不能重复引用「同一段的同一刻度」。

锚定裁决在「所有段恰用一次、每处接缝仍满足上述配对规则」的前提下，
**联合**选择段落顺序与每处接缝的具体平移方案——不是先为每处接缝单独挑局部
最优配对再回头检查锚点——使每个锚点刻度换算后的**全局水深**

```
全局水深 = 段内刻度 + 该段相对全局基准的偏移
```

与档案原始水深**精确一致**。其中首段偏移恒为 0；由于配对满足
`右段刻度 = 左段刻度 + shift`，故 `下游段偏移 = 上游段偏移 − shift`。

择优层级与原裁决完全相同：① 配对总数最多；② 总平移误差（|shift| 之和）
最小；③ 原编号序列字典序稳定决胜。

成功时额外展示**各段相对全局基准的偏移**与**逐锚点换算值**（刻度、偏移、
换算全局水深、档案水深及精确一致性核对）。

锚点引用不存在的段或位置、重复引用同一刻度，返回 `400 invalid_input` 并给出
首个输入原因；锚点彼此矛盾或无法形成满足全部锚点的链时，返回
`422 anchor_conflict`，页面清除旧结论并指明**首个阻断锚点**（最短的不可满足
锚点前缀中的最后一个）。

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

请求（`anchors` 可省略或为 0 个；提供 2–4 个时执行锚定裁决）：

```json
{
  "segments": [
    {"id": 1, "marks": [2, 10, 13, 14],
     "types": ["major", "reference", "minor", "reference"]},
    {"id": 2, "marks": [10, 11, 20, 25, 30],
     "types": ["reference", "major", "major", "major", "reference"]}
  ],
  "anchors": [
    {"segment_id": 1, "position": 1, "archived_depth": 10},
    {"segment_id": 2, "position": 4, "archived_depth": 30}
  ]
}
```

成功响应（`200`）：

```json
{
  "ok": true,
  "result": {
    "order": [1, 2, 3, 4],
    "segment_offsets": [
      {"segment_id": 1, "offset": 0},
      {"segment_id": 2, "offset": 0}
    ],
    "edges": [
      {
        "left_id": 1,
        "right_id": 2,
        "shift": 0,
        "pair_count": 1,
        "error": 0,
        "pairs": [
          {"left_position": 1, "right_position": 0,
           "mark": 10, "aligned_mark": 10, "type": "reference"}
        ]
      }
    ],
    "unpaired": [
      {"segment_id": 1, "position": 0, "mark": 2, "type": "major"}
    ],
    "total_pairs": 3,
    "total_error": 0,
    "anchor_constrained": true,
    "anchors": [
      {"index": 0, "segment_id": 1, "position": 1, "mark": 10,
       "type": "reference", "offset": 0,
       "archived_depth": 10, "global_depth": 10, "matches_archive": true}
    ]
  }
}
```

未提供锚点时 `anchor_constrained` 为 `false`、`anchors` 为 `[]`，
其余字段（含 `segment_offsets`）含义不变，原裁决请求与结果完全兼容。

错误响应：

- `400 {"ok": false, "error": {"code": "invalid_input" | "invalid_json", "message": "首个原因"}}`
- `422 {"ok": false, "error": {"code": "no_valid_chain" | "anchor_conflict", "message": "..."}}`

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
| 定位锚点 | 可省略；否则 2–4 个，覆盖至少两段，不重复引用同段同一刻度 |
| 锚点档案水深 | 整数（允许负数），换算后须与全局水深精确一致 |

## 测试

```bash
.venv/bin/python -m pytest
```

- `tests/test_solver_edges.py`：单边界配对规则（类型过滤、一致平移、顺序保持）
- `tests/test_solver_chain.py`：层级目标、稳定决胜、未配对刻度、无合法链
- `tests/test_solver_anchors.py`：锚点联合选序与接缝方案（含强制非局部最优接缝）、
  逐锚点精确换算、偏移链、首个阻断锚点、无锚点兼容
- `tests/test_solver_property.py`：随机输入对比独立暴力 oracle（含锚定裁决
  联合穷举 oracle 与可行/不可行两类随机案例）
- `tests/test_validation.py`：各类非法输入（含锚点缺字段、引用不存在段/位置、
  重复刻度、覆盖不足两段）的首个原因
- `tests/test_api.py`：健康检查、成功/失败路径与证据字段（真实 ASGI 栈，
  含锚定成功、`anchor_conflict`、非法锚点引用）
