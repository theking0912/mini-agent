FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 创建数据目录（挂载 volume 用）
RUN mkdir -p /data && chmod 755 /data

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:8080/api/auth/login'); assert r.status == 200 or r.status == 405" 2>/dev/null || \
      python -c "import urllib.request; r = urllib.request.urlopen('http://localhost:8080/docs', timeout=5); assert r.status == 200" || exit 1

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
