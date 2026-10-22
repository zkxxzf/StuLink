# -*- coding: utf-8 -*-
"""验证考场分层分配算法（桩对象，不需要数据库）

覆盖：
  A 容量累积分段：1~3 名进 01 考场、4~6 名进 02 考场
  B 固定座位优先：固定者不被挤走，且自动分配跳过其座位
  C 选科隔离：史政地只进对应的考场
  D 无排名数据：不报错，退化为随机（仍能全部分配成功）
"""
import os as _os
import sys as _sys
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_SCRIPTS = _os.path.dirname(_HERE)
_ROOT = _os.path.dirname(_SCRIPTS)
for _p in (_ROOT, _SCRIPTS):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import ast
import io
import os
import sys

sys.path.insert(0, os.getcwd())
ast.parse(io.open('app/modules/grades/routes/exam_affairs.py', encoding='utf-8').read())
from app.modules.grades.routes.exam_affairs import _arrange


class Stub(object):
    """任意未知属性返回 None，避免桩对象缺字段导致误报"""
    def __getattr__(self, k):
        return None


class Room(Stub):
    def __init__(self, no, cap, subject='物化生', prefix='', univ=False):
        self.room_no, self.capacity, self.subject = no, cap, subject
        self.prefix, self.is_universal = prefix, univ


class Stu(Stub):
    def __init__(self, no, sel='物化生', cls='01班', fx=None, fs=None):
        self.student_no, self.subject_selection, self.class_name = no, sel, cls
        self.name = 'S' + no
        self.is_attend = True
        self.fixed_room, self.fixed_seat = fx, fs
        self.room_no = self.seat_no = self.exam_number = None
        self.custom_number = ''


class Aff(Stub):
    selection_mode = 'selected'
    default_prefix = '1701'
    mode = 0

    def get_subject_prefixes(self):
        return {}


def run(tag, rooms, sts, rank_map, layered=True, mode=0):
    log = []
    n, f = _arrange(Aff(), sts, rooms, mode, log, layered=layered, rank_map=rank_map)
    print('%s: 成功%d 失败%d' % (tag, n, f))
    for s in sts:
        print('     %-6s 选科%-5s -> 考场%-3s 座%-3s 考号%s'
              % (s.name, s.subject_selection, s.room_no, s.seat_no, s.exam_number))
    return n, f


ok_all = True

# A 容量累积分段：6 人排名打乱，2 个考场各 3 座
rooms = [Room('01', 3), Room('02', 3)]
sts = [Stu('A'), Stu('B'), Stu('C'), Stu('D'), Stu('E'), Stu('F')]
rank = {'A': 3, 'B': 1, 'C': 6, 'D': 2, 'E': 5, 'F': 4}
n, f = run('A 容量累积分段', rooms, sts, rank)
by_rank = {rank[s.student_no]: s for s in sts}
seg_ok = (all(by_rank[r].room_no == '01' for r in (1, 2, 3))
          and all(by_rank[r].room_no == '02' for r in (4, 5, 6))
          and f == 0)
print('   -> 分段正确:', seg_ok)
ok_all = ok_all and seg_ok

# B 固定座位优先：FIX 固定 01/2，自动分配须跳过该座
rooms2 = [Room('01', 3), Room('02', 3)]
sts2 = [Stu('X1'), Stu('X2'), Stu('X3'), Stu('FIX', fx='01', fs=2)]
n2, f2 = run('B 固定座位优先', rooms2, sts2, {'X1': 1, 'X2': 2, 'X3': 3, 'FIX': 4})
fix = [s for s in sts2 if s.name == 'SFIX'][0]
seat_taken = {(s.room_no, s.seat_no) for s in sts2}
b_ok = (fix.room_no == '01' and fix.seat_no == 2 and f2 == 0
        and len(seat_taken) == 4)
print('   -> 固定座位保持且不冲突:', b_ok)
ok_all = ok_all and b_ok

# C 选科隔离：物化生→01，史政地→02
rooms3 = [Room('01', 3, '物化生'), Room('02', 3, '史政地')]
sts3 = [Stu('P1', '物化生'), Stu('P2', '物化生'), Stu('H1', '史政地'), Stu('H2', '史政地')]
n3, f3 = run('C 选科隔离', rooms3, sts3,
             {'P1': 1, 'P2': 2, 'H1': 1, 'H2': 2})
c_ok = (all(s.room_no == '01' for s in sts3 if s.subject_selection == '物化生')
        and all(s.room_no == '02' for s in sts3 if s.subject_selection == '史政地')
        and f3 == 0)
print('   -> 选科隔离正确:', c_ok)
ok_all = ok_all and c_ok

# D 无排名数据（首次考试）→ 退化随机但全部分配成功
rooms4 = [Room('01', 4)]
sts4 = [Stu('N1'), Stu('N2'), Stu('N3')]
n4, f4 = run('D 无排名退化', rooms4, sts4, {}, layered=True)
d_ok = (n4 == 3 and f4 == 0 and all(s.room_no == '01' for s in sts4))
print('   -> 无排名不报错且全部分配:', d_ok)
ok_all = ok_all and d_ok

# E 关闭分层（layered=False）仍走原随机逻辑，不应报错
rooms5 = [Room('01', 3), Room('02', 3)]
sts5 = [Stu('R%d' % i) for i in range(5)]
n5, f5 = run('E 原随机模式', rooms5, sts5, {}, layered=False)
e_ok = (n5 == 5 and f5 == 0)
print('   -> 原随机模式未受影响:', e_ok)
ok_all = ok_all and e_ok

# F 混编排考场（2026-10-14）：只填固定考场、座号留空
#   把史政地考生固定进「物化生」的 01 考场 → 不同选科混编进同一考场，座号自动取靠前空位
rooms6 = [Room('01', 6, '物化生'), Room('02', 4, '史政地')]
sts6 = [Stu('P1', '物化生'), Stu('P2', '物化生'), Stu('P3', '物化生'),
        Stu('H1', '史政地', fx='01'), Stu('H2', '史政地', fx='01')]
n6, f6 = run('F 混编排考场（只固定考场）', rooms6, sts6,
             {'P1': 1, 'P2': 2, 'P3': 3, 'H1': 4, 'H2': 5})
mix_ok = (n6 == 5 and f6 == 0
          and all(s.room_no == '01' for s in sts6 if s.name in ('SH1', 'SH2'))
          and len({(s.room_no, s.seat_no) for s in sts6}) == 5)
print('   -> 不同选科混编进同一考场且座位不冲突:', mix_ok)
ok_all = ok_all and mix_ok

# F2 固定考场不存在 / 已满：跳过并写日志，其余分配不受影响
rooms7 = [Room('01', 1, '物化生'), Room('02', 4, '史政地')]
sts7 = [Stu('H1', '史政地', fx='99'), Stu('H2', '史政地', fx='01'), Stu('H3', '史政地', fx='01')]
log7 = []
n7, f7 = _arrange(Aff(), sts7, rooms7, 0, log7, layered=True,
                  rank_map={'H1': 1, 'H2': 2, 'H3': 3})
f2_ok = (sts7[1].room_no == '01'                                    # 先到者进固定考场
         and sts7[2].room_no == '02'                                # 满员者退回按选科分配
         and sts7[0].room_no == '02'                                # 考场不存在者退回按选科分配
         and any('不存在' in x for x in log7) and any('已无空位' in x for x in log7))
print('F2 固定考场异常回退: 成功%d 失败%d' % (n7, f7))
print('   -> 不存在/满员的固定考场被跳过并记日志:', f2_ok)
ok_all = ok_all and f2_ok

print('')
print('====== 全部通过 ======' if ok_all else '====== 有失败项 ======')
