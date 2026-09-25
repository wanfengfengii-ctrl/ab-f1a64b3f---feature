FROM python:3.11-slim

# 不生成 .pyc、日志不缓冲，便于容器排障
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖以利用镜像层缓存
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# 拷贝源码、测试与脚本
COPY app ./app
COPY tests ./tests
COPY scripts ./scripts
COPY pyproject.toml ./

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser \
    && chmod +x scripts/verify.sh \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 容器级健康检查：slim 镜像无 curl，使用标准库访问 /health
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=5 \
    CMD python -c "import json,urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3); sys.exit(0 if json.load(r).get('status')=='ok' else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
