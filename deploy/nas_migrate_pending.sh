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
# 2026-10-14 更新：清单已补齐 —— 追加 10-13/10-14 新增的 5 个（AI 任务表、AI 报告
# kind/trace、管理员组 academic.edit、敏感字段权限 v1/v2）与学科组长 category 重建键。
# **上线要跑哪些迁移，以 docs/数据库迁移清单.md 为唯一真值**（本文件是执行器）：
#   py -3.12 scripts/migration_status.py --data-dir <生产数据目录> --pending
#
# 用法（在本地机器执行；该账号在 docker 组，docker 命令无需 sudo）
#   ssh <NAS_USER>@<NAS_HOST> "bash -s" < deploy/nas_migrate_pending.sh
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

echo "[3.5/6] 预先补齐模型缺列（**必须早于迁移**：迁移脚本读模型时若缺列会直接报错中止）"
# 2026-10-19：新增模型列（如 students.phone1_idx / teachers.phone_idx）若排在迁移之后
# 补，前面用 ORM 读 Student/Teacher 的数据脚本会因 "no such column" 被 allow-fail 跳过
# （实测 fix_exam_selection_snapshot 因此漏跑）。故把 check_columns --fix 提到 [4] 之前。
docker run --rm -v "$DATA":/app/data -w /app \
    --entrypoint python "$IMG" scripts/check_columns.py --fix 2>&1 | tail -n 15 || true

echo "[4/6] 按序补跑缺失的迁移"
run_mig() {
    # 2026-10-15：支持脚本参数（如「修数据」类脚本的 --apply）；原有单参调用不受影响
    # 2026-10-17：部分「修数据 / 划线」脚本在**目标数据不存在时主动中止**，返回非 0 是
    #   预期行为（如 apply_gaokao_bands「该场暂无成绩，先导入成绩」、
    #   import_bands_20260712「找不到名字含 20260712 的考试」）。这类脚本以
    #   `--allow-fail` 调用：非 0 只告警不中断整批；结构性迁移仍严格 fail-fast。
    local script="$1"; shift
    local allow_fail=0
    if [ "${1:-}" = "--allow-fail" ]; then allow_fail=1; shift; fi
    echo "  --> $script $*"
    docker run --rm -v "$DATA":/app/data -w /app \
        --entrypoint python "$IMG" "$script" "$@" 2>&1 | tail -n 6
    rc=${PIPESTATUS[0]}
    if [ "$rc" != "0" ]; then
        if [ "$allow_fail" = "1" ]; then
            echo "     [跳过] 脚本主动中止（rc=$rc，目标数据不存在，属预期，不中断）"
        else
            fail "迁移失败：$script（rc=$rc）"
        fi
    fi
}
# ── 结构类：加列 / 建表 / 索引 / 回填 ──────────────────────────────────────
run_mig scripts/migrate/migrate_term_schedule_dates.py            # 学期起止日期（教学周计算）
run_mig scripts/migrate/migrate_drop_legacy_timetable_20261010.py # 清 academic.db 里的旧课表表
run_mig scripts/migrate/migrate_exam_teacher_links.py             # 成绩：考试-教师映射
run_mig scripts/migrate/migrate_exam_four_stage.py                # 成绩：四段式（exam_kind 等）
run_mig scripts/migrate/migrate_swap_batch_20261010.py            # 调课：batch_id + 索引 + 历史归批
run_mig scripts/migrate/migrate_swap_exchange_20261012.py         # 换课：partner_* 共 8 列
run_mig scripts/migrate/migrate_swap_exchange_v2_20261012.py      # 换课：swap_time/swap_class/partner_swap_date
run_mig scripts/migrate/migrate_swap_auto_confirm_20261013.py     # 调课：need_partner_confirm
run_mig scripts/migrate/migrate_morality_tables_20261013.py       # 德育独立成库
run_mig scripts/migrate/migrate_affair_invig_exclude_20261013.py  # 考务：监考排除名单
run_mig scripts/migrate/migrate_ai_tasks_20261013.py              # 成绩：AI 任务表（与下一行成对）
run_mig scripts/migrate/migrate_ai_report_kind_20261013.py        # 成绩：AI 报告 kind/trace
run_mig scripts/migrate/migrate_ai_prompt_tpl_20261014.py         # 成绩：AI 提示词模板表 + ai_tasks.gen
# 2026-10-14 第三轮审计（CONS3-03）：建表必须排在「重建表」之前 —— 重建脚本对
# "无表库"是 SKIP，若先重建后建表，缺表的存量库会停在旧结构（无 category、唯一键 4 列）
run_mig scripts/migrate/migrate_subject_leaders.py                # 学科组长：建表（缺失时，目标结构）
run_mig scripts/migrate/migrate_subject_leader_category_20261014.py # 学科组长：category 列 + 重建唯一键
run_mig scripts/migrate/migrate_link_plans_20261014.py            # 任课映射：方案留档表 + 初始方案收拢
run_mig scripts/migrate/migrate_subject_leader_plans_20261014.py  # 学科组长：方案表 + 按已有学期补建
run_mig scripts/migrate/migrate_room_group_map_sort_20261014.py    # 考务：考场分组顺序（group_map.sort_order）
# 2026-10-14 第四轮审计（SEC-03 / PERF-117·118）：调课单「代办人」两列 + 补 6 个性能索引
run_mig scripts/migrate/migrate_swap_operator_20261014.py         # 调课：schedule_swaps 加 operator_uid/operator_name
run_mig scripts/migrate/migrate_perf_indexes_20261014.py          # 性能索引：oplog 4 条 + 证书/课表各 1 条
run_mig scripts/migrate/migrate_plan_current_unique_20261014.py   # 方案表 is_current 部分唯一索引（有重复会拒跑）
run_mig scripts/migrate/migrate_leader_group_single_20261014.py   # 综合组旧形态记录归并为「整组一位」（无记录则空跑）
# 2026-10-15：成绩复核（申请调分）申请表 —— 教师提交 / 有改分权限者审批
run_mig scripts/migrate/migrate_score_review_20261015.py           # 成绩复核：新建 score_review_requests 表
# 2026-10-15：组合总分线 —— exam_bands 加 selection 列 + 唯一键扩维（重建表，须停应用，见脚本头）
run_mig scripts/migrate/migrate_exam_band_selection_20261015.py     # 划线：exam_bands.selection（物化生等组合总分线）
# 2026-10-20：教育事业统计 —— teachers 补统计属性列 + 新建校舍资产年度参数表
# （必须在其它"读 Teacher/Student 模型"的数据脚本之前跑，否则 ORM 缺列会报错）
run_mig scripts/migrate/migrate_teacher_edu_fields_20261020.py --apply  # teachers：性别/民族/学历/职称/人员类别/在编/外籍
run_mig scripts/migrate/migrate_edu_stat_asset_20261020.py --apply      # 新建 edu_stat_assets（校舍/资产/达标项）
# 2026-10-15：考试选科快照滞后修正（学生转科后 exam_scores 的选科/方向没跟上；幂等，须 --apply）
run_mig scripts/fix/fix_exam_selection_snapshot_20261015.py --allow-fail --apply  # 修数据：按实考科目+名册刷新快照并重算排名（无不符行时输出「无需修正」）
run_mig scripts/seed/apply_gaokao_bands.py --allow-fail --apply              # 划线：按修正后的方向人群重算特控/本科线（幂等；该场无成绩时主动中止）
# 2026-10-16 第七轮审计（C-H1）：官方线表录入 —— exam 33（20260712 期末）特控/本科纯分数线（整场先删后插，幂等）
run_mig scripts/seed/import_bands_20260712.py --allow-fail --apply           # 划线：该场无成绩/无该场时脚本自行中止，不误伤
# 2026-10-19 第十三轮审计：历史名次 1,1,3 跳号重算（代码已是 1,1,3，存量库还是旧的 1,1,2）。
# 幂等；--apply 前自动整库备份，备份失败直接中止。
run_mig scripts/fix/recalc_exam_ranks_20261019.py --apply        # 修数据：重算全部考试的方向/班排名
# ── 数据 / 权限类（幂等）──────────────────────────────────────────────────
# 2026-10-14 第三轮审计（CONS3-02）：多身份改造的两个脚本原先**完全未登记**（只在
# docs/权限体系多身份改造执行计划.md 里点名）→ 上线必漏跑。按设计文档顺序：
# 先补 scope_kind 等列，再建 user_permission_groups 关联表并灌主组。
run_mig scripts/migrate/migrate_perm_multi_20261014.py            # 权限组：scope_kind/scope_grades/scope_classes + 废弃组清理
run_mig scripts/migrate/migrate_user_groups_p1.py                 # 权限组：user_permission_groups 关联表 + 主组灌入
run_mig scripts/migrate/migrate_oplog_http_fields.py
run_mig scripts/migrate/migrate_score_edit_perm_20261011.py
run_mig scripts/migrate/migrate_swap_perm_roles_20261011.py
run_mig scripts/migrate/migrate_admin_perm_academic_edit_20261013.py # 管理员组补 academic.edit
run_mig scripts/migrate/migrate_sensitive_perm_20261013.py           # 敏感字段权限（先）
run_mig scripts/migrate/migrate_sensitive_perm_v2_20261013.py        # 敏感字段权限收敛（后）
# 2026-10-14 第四轮审计（SEC-04）：AI 全局 Key 独立权限点 —— 给原有 system.users 的组补齐
run_mig scripts/migrate/migrate_ai_key_perm_20261014.py              # 权限组：补 grades.ai_manage
# 2026-10-19：手机号加密（方案 C：随机 IV + HMAC 盲索引，只支持精确等值查询）；
# 脚本自带 --apply 门控与执行前备份，幂等（库里已是密文的行跳过）
run_mig scripts/migrate/migrate_encrypt_student_phone_20261019.py --apply  # 学生 phone1/phone2 + phone1_idx/phone2_idx
run_mig scripts/migrate/migrate_encrypt_teacher_phone_20261019.py --apply  # 教师 phone + phone_idx
# 2026-10-19 审计 H-06：身份证确定性 IV → 随机 IV + 盲索引（两库；停机窗口；
# 脚本内部会先 backup_db，--apply 必须带 --confirm）
# 2026-10-19 第十三轮审计：清单 A17 状态是「⏸ 暂缓（用户决定）」，执行器却无条件跑。
# 默认跳过，上线前需要时显式 `RUN_A17=1 ./deploy/nas_migrate_pending.sh`。
if [ "${RUN_A17:-0}" = "1" ]; then
    run_mig scripts/migrate/migrate_encrypt_id_card_20261019.py --apply --confirm
else
    echo "  --  跳过 A17 身份证重加密（清单：⏸ 暂缓；需要时 RUN_A17=1 重跑）"
fi
# 2026-10-19 P1（M-01/M-03）：登录限流持久化表 + 小程序令牌版本列（仅结构，幂等）
run_mig scripts/migrate/migrate_login_throttle_20261019.py --apply
run_mig scripts/migrate/migrate_wx_token_ver_20261019.py --apply
# 2026-10-19 P2：AI 每日配额表（仅结构，幂等）
run_mig scripts/migrate/migrate_ai_usage_20261019.py --apply

# 2026-10-20 用户口径：身份组同步（校长 / 教务主任 / 年级长）—— 纯数据、幂等
run_mig scripts/seed/sync_role_groups_20261020.py

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
