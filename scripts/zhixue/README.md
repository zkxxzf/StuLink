# 智学网成绩抓取与入库

复用**本机 Edge 的登录态**抓成绩，不自己登录（智学网有极验风控，
脚本登录会被拦 60500）。全流程只在本地跑，产物落 `data/zhixue/`。

## 前置：让脚本用上你的浏览器登录态

`browser_session.py` 会把默认 Edge 的 Cookie / Local State /
Local Storage 复制到 `data/zhixue/.edge-live`，再用调试端口 9222
启动这个独立 profile 的 Edge。**登录态存在 Local Storage 里，
只复制 Cookie 是不够的**（会话 cookie 不落盘）。

- 用户自己的 Edge 不需要关闭，也不会被抢占；
- 若目标站点登录已过期，脚本会在窗口里提示你登录一次；
- 不要关掉脚本打开的那个 Edge 窗口，抓取要靠它。

## 限时练（2026-10-20 起的主链路）

限时练 = 名称含「限时练 / 标准化作业 / 限时作业 / 限时训练」的场次。
**不看 `markingExamTypeName`** —— 智学网会把「高三年级第三次限时练」
标成「月考」。只导 2024/2025/2026 级，其余届自动排除。

```powershell
# 1) 列清单（生成 data/zhixue/timed/plan.json，可断点续抓）
py -3.12 -u scripts/zhixue/scrape_timed.py --list

# 2) 抓成绩（推荐多标签并发；已抓过的按文件名跳过）
py -3.12 -u scripts/zhixue/scrape_timed_async.py --workers 4 --import-after

#    串行版（并发出问题时的退路）
py -3.12 -u scripts/zhixue/scrape_timed.py --scrape --list-only-from-plan

# 3) 生成「智学网考号 ↔ 学号」对应表（新考号自动补，冲突不写）
py -3.12 -u scripts/zhixue/build_student_map.py --apply

# 4) 导入限时练（默认预览，--apply 才写库）
py -3.12 -u scripts/zhixue/import_to_timed.py --apply
```

要点：

- 同一学生在**不同场次可能不是同一个考号**（2024 级有过
  「8 位号」和「2744+学号后 6 位」两套），所以匹配统一先查
  `data/zhixue/学生对应.txt` 换学号，再走 `timed_match`；
- 同一天有两场时各建各的场次，不会互相整科覆盖；
- 导入的操作人固定记「AI 自动导入」，不写脚本名与抓取地址。

## 普通考试（月考 / 期中 / 期末）

仍然走 `to_stulink_excel.py` → `import_to_stulink.py`（写 `exams` /
`exam_scores`）。限时练**不要**再用这条链路。

## 文件分工

| 文件 | 作用 |
|---|---|
| `browser_session.py` | 复用本机 Edge 登录态、起调试端口、判登录态 |
| `fetch_zhixue.py` | 原始抓取脚本（导出件 / 逐页读表 / 探查） |
| `zhixue_list.py` | 报告列表：按学年 × 学期 × 分页收集考试清单 |
| `scrape_timed.py` | 限时练批量抓取（清单 → 逐场读学生成绩） |
| `scrape_timed_async.py` | 同上的多标签并发版（推荐，4 标签约 45 秒/场） |
| `build_student_map.py` | 生成考号 ↔ 学号对应表（唯一才认） |
| `import_to_timed.py` | 限时练入库（逐科覆盖，不碰普通考试） |
| `to_stulink_excel.py` | 抓取结果 → 普通成绩导入 Excel |
| `import_to_stulink.py` | 普通考试入库（月考/期中/期末） |
| `batch_import.py` | 普通考试的批量流水线 |
