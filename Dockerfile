# 使用 Python 3.11 精简镜像
FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码（打包进镜像，无需卷挂载）
COPY app/ ./app/
COPY config.py run.py ./
COPY scripts/ ./scripts/

# 创建数据目录（运行时通过卷挂载持久化）
RUN mkdir -p /app/data /app/data/backups

# 暴露端口
EXPOSE 5000

# 生产模式启动（默认）
CMD ["python", "run.py"]
