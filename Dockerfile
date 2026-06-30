# 使用 Python 3.11 精简镜像
FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 代码通过 docker-compose volumes 挂载，不 COPY 到镜像
# 创建数据目录
RUN mkdir -p /app/data /app/data/backups

# 暴露端口
EXPOSE 5000

# 生产模式启动（默认）
CMD ["python", "run.py"]
