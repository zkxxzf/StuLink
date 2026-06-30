#!/bin/bash
# 宿舍管理系统 - Docker 一键升级脚本
# 用法: bash upgrade.sh

set -e

echo "========================================"
echo "  宿舍管理系统 - 容器升级"
echo "========================================"

# 1. 备份数据库
echo "[1/4] 备份数据库..."
cp data/dormitory.db "data/backups/dormitory_$(date +%Y%m%d_%H%M%S).db"
echo "  ✓ 已备份"

# 2. 停止容器
echo "[2/4] 停止旧容器..."
docker-compose down
echo "  ✓ 已停止"

# 3. 重新构建并启动
echo "[3/4] 重新构建镜像..."
docker-compose up -d --build
echo "  ✓ 构建完成"

# 4. 等待启动
echo "[4/4] 等待服务启动..."
sleep 3
echo ""
echo "========================================"
echo "  升级完成！"
echo "  访问: http://$(hostname -I | awk '{print $1}'):5000"
echo "========================================"
