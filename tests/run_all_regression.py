# -*- coding: utf-8 -*-
"""StuLink 一键回归：6 项自检 + 4 套回归（2026-10-13 全面审计 P2 产出）。

用法（在项目根目录）：
    py -3.12 tests\\run_all_regression.py             # 自检 + 4 套回归
    py -3.12 tests\\run_all_regression.py --with-mcp  # 额外跑 MCP 端到端自测（需 mcp_server\\.venv）
    py -3.12 tests\\run_all_regression.py --quick     # 只跑自检

输出末尾给出汇总表；有任何一项失败时退出码为 1（可用于 CI / 发布前检查）。
若把输出重定向到文件出现乱码，先执行 `chcp 65001`；直接在 cmd / PowerShell 窗口里
运行不受影响（脚本已对子进程输出做 utf-8 → 本机编码的兜底解码）。
"""
import locale
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_PY = os.path.join(BASE, 'mcp_server', '.venv', 'Scripts', 'python.exe')

SELF_CHECKS = [
    ('安全模式扫描', ['tests/scan_security_patterns.py']),
    ('列结构自检', ['tests/check_columns.py']),
    ('索引自检', ['tests/check_indexes.py']),
    # 2026-10-14 第三轮审计：手写 JS 的 ES2020+ 语法（旧微信 X5 会整段 ScriptError）
    # 曾被修过又回归（portrait/detail.html），固化成第 4 项自检。
    ('前端兼容自检', ['tests/check_frontend_compat.py']),
    # 2026-10-19 P1 M-23：迁移清单 / 执行器 / 登记表自动对账
    ('迁移对账', ['tests/check_migrations.py']),
    # 2026-10-19 P2：禁止业务代码直接用 xml.etree/lxml（统一走 defusedxml）
    ('XML 解析自检', ['tests/check_xml_parsers.py']),
]
SUITES = [
    ('教务回归', ['tests/academic_regression.py']),
    ('安全回归', ['tests/sec_regression.py']),
    ('宿舍回归', ['tests/dormitory_regression.py']),
    ('班级范围回归', ['tests/class_scope_regression.py']),
]
# 2026-10-14 第四轮审计（CONS-07）：把 smoke_*.py 挂进一键回归 —— 新功能（监考表导出 /
# AI 任务 / 方案中心 / 考务编排 / 划线分层）此前只在单独脚本里，改坏无人发现。
# 8 个脚本全部使用临时目录沙箱（不触碰 data/），实测合计约 30s。
# smoke_points_http.py 依赖常驻 127.0.0.1:5000 服务，保持手动执行。
SMOKES = [
    ('冒烟·成绩端到端', ['tests/smoke_grades.py']),
    ('冒烟·教务端到端', ['tests/smoke_academic.py']),
    ('冒烟·权限体系', ['tests/smoke_permissions.py']),
    ('冒烟·考务监考', ['tests/smoke/smoke_invig.py']),
    ('冒烟·考务分层分配', ['tests/smoke/smoke_affair_arrange.py']),
    ('冒烟·AI 识图排场次', ['tests/smoke/smoke_ai_schedule.py']),
    ('冒烟·全校对比口径', ['tests/smoke/smoke_global_compare.py']),
    ('冒烟·学生宿舍视图', ['tests/smoke/smoke_student_dorm_view.py']),
]
# 结果行样例：「===== 教务回归完成：通过 505 / 失败 0 =====」「结果：PASS 8 / FAIL 0」
_PATTERNS = (
    re.compile(r'通过\s*(\d+)\s*/\s*失败\s*(\d+)'),
    re.compile(r'PASS\s*(\d+)\s*/\s*FAIL\s*(\d+)'),
)


def _decode(raw):
    """子进程输出解码：utf-8 优先，失败退回本机编码（Windows 控制台多为 cp936）。

    两种编码都要试的原因：控制台/管道的默认编码取决于 PYTHONIOENCODING 与系统区域，
    单试一种会让结果行变成乱码、中文结果行匹配不上（退出码仍可判定，但信息丢失）。
    """
    if not raw:
        return ''
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors='replace')


def _parse_result(out):
    for pattern in _PATTERNS:
        match = None
        for match in pattern.finditer(out):
            pass                      # 取最后一个（有的脚本会打印多次）
        if match:
            return int(match.group(1)), int(match.group(2))
    return None, None


def _run(title, argv, python=None):
    """跑一项，返回 (ok, 摘要行, 失败片段)。"""
    exe = python or sys.executable
    print(f'\n──── {title} ' + '─' * max(4, 60 - len(title)))
    try:
        proc = subprocess.run([exe] + argv, cwd=BASE, capture_output=True)
    except OSError as exc:
        print(f'  无法启动：{exc}')
        return False, f'{title}：无法启动（{exc}）', ''
    out = _decode((proc.stdout or b'') + (proc.stderr or b''))
    tail = '\n'.join(out.strip().splitlines()[-12:])
    print(tail)

    passed, failed = _parse_result(out)
    if passed is None:                # 没有结果行：按退出码判定（scan_security_patterns 这类）
        ok = proc.returncode == 0
        mark = 'OK' if ok else '退出码 %s' % proc.returncode
        return ok, f'{title}：{mark}', '' if ok else tail
    ok = failed == 0 and proc.returncode == 0
    return ok, f'{title}：通过 {passed} / 失败 {failed}', '' if ok else tail


def main():
    try:
        sys.stdout.reconfigure(errors='replace')   # 控制台编码兜底：打印子进程输出不崩
    except Exception:                              # noqa: BLE001
        pass
    args = set(sys.argv[1:])
    quick = '--quick' in args
    with_mcp = '--with-mcp' in args

    print('StuLink 一键回归' + ('（--quick）' if quick else ''))
    results = []
    for title, argv in SELF_CHECKS:
        results.append(_run(title, argv))
    if not quick:
        for title, argv in SUITES:
            results.append(_run(title, argv))
        # 2026-10-14 第四轮审计（CONS-07）：smoke 端到端（临时库沙箱，约 30s）
        for title, argv in SMOKES:
            results.append(_run(title, argv))
        if with_mcp:
            if os.path.exists(MCP_PY):
                results.append(_run('MCP 端到端自测', ['mcp_server/smoke_scores.py'],
                                    python=MCP_PY))
            else:
                print('\n（跳过 MCP 自测：未找到 mcp_server\\.venv\\Scripts\\python.exe）')

    print('\n' + '=' * 64)
    print('汇总')
    print('=' * 64)
    bad = []
    for ok, summary, detail in results:
        print(('  [OK]   ' if ok else '  [FAIL] ') + summary)
        if not ok:
            bad.append((summary, detail))
    if bad:
        print('\n失败详情（末尾片段）：')
        for summary, detail in bad:
            print(f'\n---- {summary} ----\n{detail}')
        print(f'\n结果：有 {len(bad)} 项失败')
        return 1
    print('\n结果：全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
