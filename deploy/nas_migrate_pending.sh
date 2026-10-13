#!/bin/bash
# StuLink 补迁移脚本（2026-10-13，为 NAS 10.193.191.210）
#
# 背景（为什么需要它）
# -------------------
# 远端「教师工作台」与「调课审批」一进就 500：timetable.db 的 schedule_swaps 缺列
# （batch_id / exchange_kind / partner_* / need_partner_confirm …）。SQLAlchemy 加载
# 这条记录要 SELECT 模型声明的**所有列**，缺任何一列就
#     sqlite3.OperationalError: no such column: schedule_swaps.xxx
# 于是整页 500（不是过滤条件的问题）。
#
# 根因：唯一带迁移的整包脚本 deploy/nas_update_v1.18.9.2.sh 的 run_mig 清单**停在
# 2026-10-10 那批**，10-11 ～ 10-13 的 14 个迁移从未执行；deploy/hot_update.ps1
# 只 docker cp 代码、不跑迁移（脚本头部自己写明「涉及数据库迁移请走整包脚本」）。
# 且 app/__init__.py 启动时的 create_all() **只建缺失的表、不给已有表补列**。
#
# 用法（在本地机器执行；该账号在 docker 组，docker 命令无需 sudo）
#   ssh 17752560383@10.193.191.210 "bash -s" < deploy/nas_migrate_pending.sh
#
# 幂等：单个迁移自身幂等（列已存在则跳过；回填只处理未处理过的行），可重复执行。
# 安全：不停应用就改 SQLite 会有写锁 / 半迁移风险（整包脚本原话），故流程为
#       现状快照 → 停容器 → 备份数据 → 一次性容器按序跑迁移 → check_columns --fix
#       兜底 → 启动 → 复核。**代码不动**（临时镜像只用于跑迁移，确保依赖与 /app 一致）。
#
# 注意：若 [0/6] 显示容器里的 base.html 仍带 Git 冲突标记（grep -c 不为 0），
#       说明跑的是 2026-10-13 上午那版合并残留代码，请先用 deploy/nas_update_*.sh
#       或 hot_update.ps1 更新到 master（5b8011be，含 e44ddc82 的清理）再跑本脚本。
set -u
DIR=/volume1/docker/stulink
CT=stulink
DATA="$DIR/stulink-data"
TS=$(date +%Y%m%d_%H%M%S)
IMG="stulink:mig_pending"
BAK="$DIR/stulink-data-backup-mig-$TS"

fail() {
    echo "!! $1"
    echo "   回滚：docker start $CT（本脚本未改动容器内代码）"
    echo "   数据回滚：docker stop $CT && rm -rf $DATA && cp -a $BAK $DATA && docker start $CT"
    exit 1
}

echo "==================== StuLink 补迁移开始（$TS）===================="

echo "[0/6] 现状快照（只读）"
echo "  容器版本：$(docker exec "$CT" grep -m1 'StuLink v' /app/config.py || echo 取不到)"
echo "  base.html 冲突标记数：$(docker exec "$CT" grep -c '<<<<<<<' /app/app/templates/base.html || echo 0)（应为 0；不为 0 请先更新代码）"
echo "  -- 缺列清单（模型列 vs 现网列，只读；有缺口会列出并返回非 0）--"
docker exec "$CT" python scripts/check_columns.py 2>&1 | tail -n 30 || true

echo "[1/6] 停容器（迁移必须在应用停止状态跑）"
docker stop "$CT" >/dev/null || fail "停止容器失败"
echo "      stopped"

echo "[2/6] 备份数据目录 → $(basename "$BAK")"
cp -a "$DATA" "$BAK" || fail "数据备份失败"
du -sh "$BAK" 2>/dev/null

echo "[3/6] 提交临时镜像（容器已停，确保迁移用的是容器内代码与依赖）"
docker commit "$CT" "$IMG" >/dev/null || fail "docker commit 失败"

echo "[4/6] 按序补跑缺失的迁移"
run_mig() {
    echo "  --> $1"
    docker run --rm -v "$DATA":/app/data -w /app \
        --entrypoint python "$IMG" "$1" 2>&1 | tail -n 6
    rc=${PIPESTATUS[0]}
    [ "$rc" = "0" ] || fail "迁移失败：$1（rc=$rc）"
}
# ── 结构类：加列 / 建表 / 索引 / 回填 ──────────────────────────────────────
run_mig scripts/migrate_term_schedule_dates.py            # 学期起止日期（教学周计算）
run_mig scripts/migrate_drop_legacy_timetable_20261010.py # 清 academic.db 里的旧课表表
run_mig scripts/migrate_exam_teacher_links.py             # 成绩：考试-教师映射
run_mig scripts/migrate_exam_four_stage.py                # 成绩：四段式（exam_kind 等）
run_mig scripts/migrate_swap_batch_20261010.py            # 调课：batch_id + 索引 + 历史归批
run_mig scripts/migrate_swap_exchange_20261012.py         # 换课：partner_* 共 8 列
run_mig scripts/migrate_swap_exchange_v2_20261012.py      # 换课：swap_time/swap_class/partner_swap_date
run_mig scripts/migrate_swap_auto_confirm_20261013.py     # 调课：need_partner_confirm
run_mig scripts/migrate_morality_tables_20261013.py       # 德育独立成库
run_mig scripts/migrate_affair_invig_exclude_20261013.py  # 考务：监考排除名单
# ── 数据 / 权限类（幂等）──────────────────────────────────────────────────
run_mig scripts/migrate_subject_leaders.py
run_mig scripts/migrate_oplog_http_fields.py
run_mig scripts/migrate_score_edit_perm_20261011.py
run_mig scripts/migrate_swap_perm_roles_20261011.py

echo "[5/6] 兜底：补齐模型声明但现网缺失的列（改库前自动备份对应库）"
docker run --rm -v "$DATA":/app/data -w /app \
    --entrypoint python "$IMG" scripts/check_columns.py --fix 2>&1 | tail -n 20
echo "      （非 0 退出不中断：下一步会只读复核）"

echo "[6/6] 启动容器 + 复核"
docker start "$CT" >/dev/null || fail "启动容器失败"
for i in $(seq 1 40); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/login || true)
    if [ "$code" = "200" ]; then echo "      就绪（第 ${i} 次探测 HTTP $code）"; break; fi
    sleep 3
done
docker rmi "$IMG" >/dev/null 2>&1 && echo "      临时镜像已清理"
docker ps --filter "name=^$CT$" --format '  {{.Names}} | {{.Status}}'
if docker exec "$CT" python scripts/check_columns.py >/dev/null 2>&1; then
    echo "  ✓ 缺列复核：0 处缺口"
else
    echo "  ✗ 仍有缺口 —— 请把下面输出发出来："
    docker exec "$CT" python scripts/check_columns.py 2>&1 | tail -n 20
fi

echo "  请手工点一次：教师工作台 / 调课审批"
echo "==================== 补迁移完成 ===================="
echo "回滚方式（如需）："
echo "  数据：docker stop $CT && rm -rf $DATA && cp -a $BAK $DATA && docker start $CT"
echo "  代码：本脚本未改动容器内代码，无需回滚"
