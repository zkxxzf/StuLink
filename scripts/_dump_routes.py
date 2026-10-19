"""临时脚本：导出成绩模块页面路由的 权限/函数（用完即删）"""
import glob
import re

SKIP = ('/api/', 'template', 'export', 'pdf', 'stream', 'xlsx')
out = []
for path in glob.glob('app/modules/grades/routes/*.py'):
    text = open(path, encoding='utf-8').read()
    for m in re.finditer(r"@bp\.route\('([^']*)'", text):
        rule = m.group(1)
        if any(s in rule for s in SKIP):
            continue
        head = text[m.start():m.start() + 500]
        perm = re.findall(r"perm_required\('([a-z_.]+)'", head)
        func = re.findall(r'def (\w+)', head)
        out.append('\t'.join([path.split('\\')[-1], rule,
                              perm[0] if perm else '-',
                              func[0] if func else '-']))
open('scripts/_routes_perm.txt', 'w', encoding='utf-8').write('\n'.join(out))
print(len(out))
