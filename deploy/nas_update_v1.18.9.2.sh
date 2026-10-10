#!/bin/bash
# StuLink v1.18.9.2 轻量部署脚本（在 NAS 上执行）
#
# 用法（本地机器执行；该账号在 docker 组，docker 命令无需 sudo，因此无需密码）：
#   ssh 17752560383@10.193.191.210 "bash -s" < deploy/nas_update_v1.18.9.2.sh
#
# 前置：代码包已上传到 /volume1/docker/stulink/_v11892.tar.gz
#       （MD5 1704603a7b58a46985e016bd85169da4）
#
# 本版含【教务「一个功能一库」拆分】——必须在**应用停止**状态跑迁移，
# 否则会出现 SQLite 写锁 / 半迁移。故流程为：
#   校验包 → 备份代码 → 备份数据 → 解包 → 停容器 → 复制代码 → 跑迁移 → 启动 → 验收
#
# 迁移用**一次性容器**（同镜像 + 挂载新代码与数据卷）执行：容器已停时无法 docker exec，
# 又必须保证迁移期间应用不读写数据。

set -u
DIR=/volume1/docker/stulink
CT=stulink
IMG=stulink:v1.7.2
PKG="$DIR/_v11892.tar.gz"
DATA="$DIR/stulink-data"
TS=$(date +%Y%m%d_%H%M%S)
WORK="$DIR/_unpack_v11892_$TS"
EXPECT=1704603a7b58a46985e016bd85169da4

fail() {
    echo "!! $1"
    echo "   回滚建议：docker start $CT && docker cp $DIR/code_backup_v11892_$TS/. $CT:/app/ && docker restart $CT"
    echo "   数据回滚：docker stop $CT && rm -rf $DATA && cp -a $DIR/stulink-data-backup-v11892-$TS $DATA && docker start $CT"
    exit 1
}

echo "==================== StuLink v1.18.9.2 部署开始 ===================="

echo "[0/9] 校验代码包"
[ -f "$PKG" ] || fail "找不到 $PKG"
GOT=$(md5sum "$PKG" | awk '{print $1}')
echo "      MD5=$GOT"
[ "$GOT" = "$EXPECT" ] || fail "MD5 不匹配（期望 $EXPECT）"
echo "      MD5 OK"

echo "[1/9] 备份容器内代码 → code_backup_v11892_$TS"
docker cp "$CT":/app "$DIR/code_backup_v11892_$TS" || fail "代码备份失败"
du -sh "$DIR/code_backup_v11892_$TS" 2>/dev/null

echo "[2/9] 解包新代码"
rm -rf "$WORK"; mkdir -p "$WORK"
tar -xzf "$PKG" -C "$WORK" || fail "解包失败"
ls "$WORK" | head -n 6

echo "[3/9] 停止应用容器（迁移期间必须停应用）"
docker stop "$CT" >/dev/null || fail "停止容器失败"
echo "      stopped"

echo "[4/9] 备份数据目录 → stulink-data-backup-v11892-$TS"
# 必须放在停容器之后：SQLite 开启 WAL 后，运行中 cp -a 可能拿到主库与 -wal 不一致的
# 快照（回滚时数据缺损）。停容器后主库/-wal/-shm 一起复制即为一致快照。
cp -a "$DATA" "$DIR/stulink-data-backup-v11892-$TS" || fail "数据备份失败"
du -sh "$DIR/stulink-data-backup-v11892-$TS" 2>/dev/null

echo "[5/9] 复制新代码进容器 /app（容器已停，docker cp 依然可用）"
docker cp "$WORK/." "$CT":/app/ || fail "复制代码失败"

echo "[6/9] 执行迁移（容器已停；先提交为临时镜像再跑，确保用的是新代码与镜像内依赖）"
# 注：不能把新代码挂到 /app 再 docker run——那会盖掉镜像内已装的依赖（本机实测
#     报 ModuleNotFoundError: pptx，因该依赖在容器可写层而非镜像里）。
IMGT=stulink:mig_v11892
docker commit "$CT" "$IMGT" >/dev/null || fail "提交临时镜像失败"
run_mig() {
    echo "  --> $1"
    docker run --rm -v "$DATA":/app/data -w /app \
        --entrypoint python "$IMGT" "$1" 2>&1 | tail -n 8
    rc=${PIPESTATUS[0]}
    [ "$rc" = "0" ] || fail "迁移失败：$1（rc=$rc）"
}
run_mig scripts/migrate_class_active_20261009.py
run_mig scripts/migrate_achievement_pdf_20261009.py
run_mig scripts/migrate_form_achievement_20261010.py
run_mig scripts/migrate_split_academic_20261010.py
run_mig scripts/migrate_swap_approval_20261009.py
run_mig scripts/migrate_teaching_links_20261010.py
run_mig scripts/migrate_scope_and_cleanup_20261010.py
run_mig scripts/add_performance_indexes.py

echo "[7/9] 启动容器"
docker start "$CT" >/dev/null || fail "启动容器失败"
echo "      等待服务就绪…"
for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/login || true)
    if [ "$code" = "200" ]; then echo "      就绪（第 ${i} 次探测 HTTP $code）"; break; fi
    sleep 3
done
docker rmi "$IMGT" >/dev/null 2>&1 && echo "      临时镜像已清理"

echo "[8/9] 验收：容器 / 页面 / 版本号"
docker ps --filter "name=^$CT$" --format '  {{.Names}} | {{.Status}}'
curl -s -o /dev/null -w '  /login -> %{http_code}\n' http://127.0.0.1/login
docker exec "$CT" grep -m1 "StuLink v" /app/config.py

echo "[9/9] 验收：教务分库结果"
docker exec "$CT" python -c "
import sqlite3, os
d = '/app/data'
def tabs(f):
    p = os.path.join(d, f)
    if not os.path.exists(p): return None
    c = sqlite3.connect(p)
    return {r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")}
def rows(f, t):
    try:
        c = sqlite3.connect(os.path.join(d, f))
        return c.execute('select count(*) from %s' % t).fetchone()[0]
    except Exception:
        return -1
ac, insp, ach, fm = tabs('academic.db'), tabs('inspection.db'), tabs('achievement.db'), tabs('forms.db')
print('  inspection.db 存在/表:', insp is not None, 'inspection_records' in (insp or set()), 'rows=%s' % rows('inspection.db','inspection_records'))
print('  achievement.db 存在/表:', ach is not None, 'teacher_achievements' in (ach or set()), 'rows=%s' % rows('achievement.db','teacher_achievements'))
print('  forms.db 存在/表:', fm is not None, 'form_templates' in (fm or set()), 'rows=%s' % rows('forms.db','form_templates'))
print('  academic.db 保留表:', sorted(t for t in (ac or set()) if t in ('teachers','subject_leaders','work_records','attendance_records')))
print('  academic.db 已迁出表仍在?:', sorted(t for t in (ac or set()) if t in ('inspection_records','teacher_achievements','form_templates')))
"

echo "清理临时解包目录"
rm -rf "$WORK"

# 记录"已部署提交"，供 deploy/hot_update.ps1 增量更新时做差异基准：
#   ssh NAS 'bash -s -- <commit>' < deploy/nas_update_v1.18.9.2.sh
COMMIT="${1:-}"
if [ -n "$COMMIT" ]; then
    printf '%s\n' "$COMMIT" > "$DIR/.deployed_commit"
    echo "已记录已部署提交：$COMMIT"
fi

echo "==================== 部署完成 v1.18.9.2 ===================="
echo "回滚方式（如需）："
echo "  代码：docker cp $DIR/code_backup_v11892_$TS/. $CT:/app/ && docker restart $CT"
echo "  数据：docker stop $CT && rm -rf $DATA && cp -a $DIR/stulink-data-backup-v11892-$TS $DATA && docker start $CT"
