#!/bin/sh
# verify 一次性服务入口：测试 -> 构建检查 -> 真实 HTTP API 冒烟。
# 任一步失败立即以非零退出码结束（set -e）。
set -eu

# 容器内工作目录是 /app；本地运行时回退到仓库根目录（脚本所在目录的上一级）。
if [ -d /app ]; then
  cd /app
else
  cd "$(dirname "$0")/.."
fi

# 容器内直接用 python；本地存在 .venv 时优先使用其解释器。
if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python}"
fi

echo "==> [1/3] 代码测试（pytest）"
$PYTHON -m pytest

echo "==> [2/3] 构建检查（字节码编译全部源码与脚本）"
$PYTHON -m compileall -q app scripts

echo "==> [3/3] API 冒烟（目标 ${BASE_URL:-http://127.0.0.1:8000}）"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}" $PYTHON scripts/smoke.py

echo ""
echo "verify 完成：测试、构建检查、API 冒烟全部通过。"
