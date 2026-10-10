#!/bin/bash
# StuLink v1.18.9.0 轻量部署脚本（在 NAS 上执行）
#
# 用法（本地机器执行，会提示输入 NAS 的 sudo 密码一次）：
#   ssh -t 17752560383@10.193.191.210 "sudo -v && bash -s" < deploy/nas_update_v1.18.9.0.sh
#
# 前置：代码包已上传到 /volume1/docker/stulink/_v11890.tar.gz（MD5 b8735374a9870861822da8568d838671）
# 流程：备份代码 → 备份数据 → 解包 → docker cp 进容器 → 容器内跑迁移 → 重启 → 验证

set -u
DIR=/volume1/docker/stulink
CT=stulink
PKG="$DIR/_v11890.tar.gz"
TS=$(date +%Y%m%d_%H%M%S)
WORK="$DIR/_unpack_v11890_$TS"

echo "==================== StuLink v1.18.9.0 部署开始 ===================="
echo "[0/7] 校验代码包"
if [ ! -f "$PKG" ]; then echo "!! 找不到 $PKG"; exit 1; fi
md5sum "$PKG"
EXPECT=b8735374a9870861822da8568d838671
GOT=$(md5sum "$PKG" | awk '{print $1}')
if [ "$GOT" != "$EXPECT" ]; then echo "!! MD5 不匹配（期望 $EXPECT）"; exit 1; fi
echo "      MD5 OK"

echo "[1/7] 备份当前容器内代码 → code_backup_v11890_$TS"
sudo docker cp "$CT":/app "$DIR/code_backup_v11890_$TS" || { echo "!! 代码备份失败，中止"; exit 1; }
du -sh "$DIR/code_backup_v11890_$TS" 2>/dev/null

echo "[2/7] 备份数据目录 → stulink-data-backup-v11890-$TS"
cp -a "$DIR/stulink-data" "$DIR/stulink-data-backup-v11890-$TS" || { echo "!! 数据备份失败，中止"; exit 1; }
du -sh "$DIR/stulink-data-backup-v11890-$TS" 2>/dev/null

echo "[3/7] 解包新代码"
rm -rf "$WORK"; mkdir -p "$WORK"
tar -xzf "$PKG" -C "$WORK" || { echo "!! 解包失败"; exit 1; }
ls "$WORK" | head -8

echo "[4/7] docker cp 新代码进容器 /app（覆盖，不带 data/）"
sudo docker cp "$WORK/." "$CT":/app/ || { echo "!! 复制失败"; exit 1; }

echo "[5/7] 容器内执行数据库迁移（幂等）：migrate_exam_four_stage"
sudo docker exec "$CT" python -m scripts.migrate_exam_four_stage --apply 2>&1 | tail -20

echo "[6/7] 重启容器"
sudo docker restart "$CT" >/dev/null && echo "      restarted"
echo "      等待服务就绪…"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:80/login || true)
  if [ "$code" = "200" ]; then echo "      就绪（第 ${i} 次探测 HTTP $code）"; break; fi
  sleep 3
done

echo "[7/7] 验收"
echo "  -- 容器状态 --"
sudo docker ps --filter "name=^$CT$" --format '{{.Names}} | {{.Status}}'
echo "  -- 首页 HTTP --"
curl -s -o /dev/null -w '  /login -> %{http_code}\n' http://127.0.0.1:80/login
echo "  -- 容器内版本号 --"
sudo docker exec "$CT" grep -m1 "StuLink v" /app/config.py || true
echo "  -- 关键新字段是否就位 --"
sudo docker exec "$CT" python - <<'PY' 2>&1 | tail -6
import sqlite3
c = sqlite3.connect('/app/data/grades.db')
cols = {r[1] for r in c.execute("PRAGMA table_info(exams)")}
print('exams.exam_kind/score_mode :', {'exam_kind','score_mode'} <= cols)
cols2 = {r[1] for r in c.execute("PRAGMA table_info(exam_scores)")}
print('exam_scores.exam_no/raw_score:', {'exam_no','raw_score'} <= cols2)
print('exam_scores 行数:', c.execute('select count(*) from exam_scores').fetchone()[0])
PY

echo "清理临时解包目录"
rm -rf "$WORK"
echo "==================== 部署完成 v1.18.9.0 ===================="
echo "回滚方式（如需）："
echo "  sudo docker cp $DIR/code_backup_v11890_$TS/. $CT:/app/ && sudo docker restart $CT"
