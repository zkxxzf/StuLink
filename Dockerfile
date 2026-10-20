# 使用 Python 3.12 精简镜像（与 requirements.lock 的解析版本一致）
FROM python:3.12-slim

WORKDIR /app

# 设置时区 + 运行期不写 .pyc（/app 可只读挂载）
ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装依赖（P2 M-21：带 hash 的锁，禁止未锁定包进入镜像）
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码（打包进镜像，无需卷挂载）
COPY app/ ./app/
COPY config.py run.py ./
COPY scripts/ ./scripts/

# 创建数据目录（运行时通过卷挂载持久化）
RUN mkdir -p /app/data /app/data/backups

# 暴露端口
EXPOSE 5000

# P2 M-16：真实健康检查（DB 连通），替代 compose 里“只看密钥文件”的假健康
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3).status == 200 else 1)"

# 生产模式启动（默认）
CMD ["python", "run.py"]
