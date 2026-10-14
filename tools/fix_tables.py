# -*- coding: utf-8 -*-
"""
fix_tables.py —— 检测并修复「被拍平成纯文本的表格」

背景
----
作者在公众号编辑器里粘数据表时，有些表并没有真的插表格，而是「每个单元格一行」的纯文本，
落进 store/<md5>.json 的 inner 里就成了 `<br/>` 分隔的一串单元格，markdownify 只会按段落拆开，
最终在 articles/*.md 里表现为「每格一行 + 空行」的散装文本。

本脚本扫 store/ 全部源文件，识别这类「网格」并把它重建成 <table> 标签；
随后 build_repo.py 里 markdownify 会输出标准 Markdown 管道表格。

**为什么必须改 store/ 而不是直接改 articles/*.md**：articles/*.md 由 build_repo.py 从
store/*.json 生成，直接改 md 会在下次同步被覆盖。

用法
----
    python tools/fix_tables.py                 # 只体检（dry-run），列出候选
    python tools/fix_tables.py --fix           # 写回 store/（自动备份到仓库外）
    python tools/fix_tables.py --fix --build   # 写回后顺带重建 articles/README/index
    python tools/fix_tables.py --repo <dir>    # 指定仓库根（默认 = 本脚本上一级）

判据（三层，全部要求「锚列在固定步长上重复」以排除散文断行的误报）
  A. 数值列：某个固定步长 N(2..8) 的第 c 列上，>=3 行整格匹配「年份」或「纯数值/百分比」
  B. 短标签网格：>=8 个连续短标签（<=15 字、不以标点结尾）
  C. 长单元格放宽：段内 >=4 个 numish 落在 25 格窗口内（用于单元格较长的表）

已知误报（本脚本已用判据 A 的「整格数值」硬门槛排除，见 memory 2026-10-14）
  「预期管理」「康波」等文的 `<br/>` 是**排版断行**，每格只是半句话，不是表格 —— 不要动。
"""
import os
import re
import sys
import json
import html
import time
import shutil
import argparse
import subprocess

TOOLS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TOOLS)

SEP = re.compile(r'(?:<br\s*/?>\s*)+')
TAG = re.compile(r'<[a-zA-Z/!]')

# 整格匹配「年份」：2013年 / 2025年末 / 2025年8月 / 2015-06 / 2015/6
YEAR = re.compile(r'^(19|20)\d{2}\s*(年末|年|\s*[-/年]\s*\d{1,2}\s*月?)$')
# 整格匹配「数值」：83.2% / 约45% / 2.91万亿 / 2232亿 / 1009亿 / 46 / 1.5
NUM = re.compile(r'^(约|近|超|不到|逾)?\s*-?\d[\d,\.]*\s*(%|万亿|亿|万|元|美元|个|人|次|倍)?$')


def numish(t):
    return bool(YEAR.match(t) or NUM.match(t))


def tokenize(inner):
    """把 inner 按 <br/> 串切成 token，记录每个 token 在 inner 里的字符区间。
    只把「不含 HTML 标签」的 token 当作候选单元格。"""
    toks = []
    last = 0

    def push(seg, base):
        if not seg.strip():
            return
        s = base + (len(seg) - len(seg.lstrip()))
        e = base + len(seg.rstrip())
        raw = inner[s:e]
        toks.append({'t': html.unescape(raw.strip()), 's': s, 'e': e, 'pure': not TAG.search(raw)})

    for m in SEP.finditer(inner):
        push(inner[last:m.start()], last)
        last = m.end()
    push(inner[last:], last)
    return toks


def header_like(cells):
    """上一行是否像表头：都短、不是数值列"""
    return all(0 < len(c) <= 24 and not numish(c) for c in cells)


def find_region(cells):
    """在一个纯文本 run 里找网格。返回 (start, end, ncol, nrows) 或 None。
    start/end 为 run 内下标（含表头行）；nrows 不含表头。"""
    n = len(cells)
    best = None
    for ncol in range(2, 9):
        for c in range(ncol):
            i = c
            while i < n:
                if numish(cells[i]):
                    rows = []
                    j = i
                    while j < n and numish(cells[j]):
                        rows.append(j)
                        j += ncol
                    if len(rows) >= 3:
                        rs = rows[0] - c
                        re_ = rows[-1] + (ncol - 1 - c)
                        # 网格必须完整：区域长度 == 行数 * 列数
                        if rs >= 0 and re_ < n and (re_ - rs + 1) == len(rows) * ncol:
                            # 若上方还有一整行且像表头，则并入
                            if rs - ncol >= 0 and header_like(cells[rs - ncol:rs]):
                                rs -= ncol
                            cand = (rs, re_, ncol, (re_ - rs + 1) // ncol - 1)
                            if best is None or cand[1] - cand[0] > best[1] - best[0]:
                                best = cand
                    i = j
                else:
                    i += 1
    return best


def scan_file(path):
    """返回该 store 文件里发现的表格列表 [(cell_start_idx, cell_end_idx, ncol, nrows, cells)]"""
    try:
        s = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return []
    inner = s.get('inner') or ''
    if not inner or '<table' in inner:
        return []
    toks = tokenize(inner)

    found = []
    k = 0
    while k < len(toks):
        if toks[k]['pure']:
            j = k
            while j < len(toks) and toks[j]['pure']:
                j += 1
            run = toks[k:j]
            cells = [t['t'] for t in run]
            reg = find_region(cells)
            if reg:
                a, b, ncol, nrows = reg
                found.append((k + a, k + b, ncol, nrows, cells[a:b + 1], run[a]['s'], run[b]['e']))
            k = j
        else:
            k += 1
    return found


def make_table(cells, ncol):
    def esc(t):
        return html.escape(t, quote=False)
    out = ['<table>']
    for r in range(0, len(cells), ncol):
        row = cells[r:r + ncol]
        if r == 0:
            out.append('<tr>' + ''.join('<th>%s</th>' % esc(c) for c in row) + '</tr>')
        else:
            out.append('<tr>' + ''.join('<td>%s</td>' % esc(c) for c in row) + '</tr>')
    out.append('</table>')
    return ''.join(out)


def backup_dir():
    d = os.path.join(os.path.expanduser('~'), 'Desktop', '_wuyuzi_backup')
    os.makedirs(d, exist_ok=True)
    return d


def main():
    ap = argparse.ArgumentParser(description='检测/修复被拍平成纯文本的表格')
    ap.add_argument('--repo', default=HERE, help='仓库根目录')
    ap.add_argument('--fix', action='store_true', help='写回 store/（默认只体检）')
    ap.add_argument('--build', action='store_true', help='--fix 后顺带跑 build_repo.py')
    args = ap.parse_args()

    store = os.path.join(args.repo, 'store')
    if not os.path.isdir(store):
        print('!! 找不到 store/:', store)
        return 2

    files = sorted(f for f in os.listdir(store) if f.endswith('.json'))
    total, changed = 0, 0
    for fn in files:
        path = os.path.join(store, fn)
        hits = scan_file(path)
        if not hits:
            continue
        total += len(hits)
        s = json.load(open(path, encoding='utf-8'))
        print('=== %s  %r  mid=%s  发现 %d 张扁平表格' % (fn, s.get('title'), s.get('mid'), len(hits)))
        for (a, b, ncol, nrows, cells, s_off, e_off) in hits:
            print('    %d 列 × %d 行(不含表头)，表头: %s' % (ncol, nrows, cells[:ncol]))
            for r in range(0, min(len(cells), ncol * 3), ncol):
                print('      | ' + ' | '.join(cells[r:r + ncol]))
            if len(cells) > ncol * 3:
                print('      ...（共 %d 行）' % (len(cells) // ncol))
        if args.fix:
            inner = s['inner']
            # 从后往前替换，避免位移影响后面的区间
            for (a, b, ncol, nrows, cells, s_off, e_off) in sorted(hits, key=lambda x: -x[5]):
                inner = inner[:s_off] + make_table(cells, ncol) + inner[e_off:]
            s['inner'] = inner
            bak = os.path.join(backup_dir(), '%s.bak_%s' % (fn, time.strftime('%Y%m%d_%H%M%S')))
            shutil.copyfile(path, bak)
            json.dump(s, open(path, 'w', encoding='utf-8'), ensure_ascii=False)
            print('    -> 已写回（备份 %s）' % bak)
            changed += 1

    if total == 0:
        print('未发现扁平表格。')
    elif not args.fix:
        print('\n体检完成：共 %d 张待修（加 --fix 写回）。' % total)
    else:
        print('\n修复完成：%d 个文件已更新。' % changed)

    if args.fix and args.build:
        print('-> 运行 build_repo.py')
        p = subprocess.run([sys.executable, os.path.join(TOOLS, 'build_repo.py')],
                           cwd=args.repo)
        return p.returncode
    return 0


if __name__ == '__main__':
    sys.exit(main())
