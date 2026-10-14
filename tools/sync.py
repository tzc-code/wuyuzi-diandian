# -*- coding: utf-8 -*-
"""
sync.py —— 增量同步「无语子点点」的全部文章到 GitHub

流程:
  1) harvest_list.py   (WYZ_INCREMENTAL=1) 微信界面里只抓「上次之后的新文章」的链接
  2) fetch_articles.py 用这些新链接 HTTP 直取正文(链接里的 key 是会话级, 必须紧接第 1 步)
  3) fix_tables.py --fix  把「被拍平成纯文本的表格」在 store/ 里重建成 <table>
  4) build_repo.py     生成 Markdown + README + state/index.json
  5) git commit & push

用法: python sync.py            # 完整同步
      python sync.py --no-push  # 只本地构建

注意: 第 3 步必须在第 4 步之前 —— articles/*.md 由 store/*.json 生成,
      直接改 md 会在下次构建被覆盖, 因此表格修复只能落在 store/ 源头。
"""
import os, sys, json, time, subprocess, shutil

TOOLS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TOOLS)              # 仓库根
PY = sys.executable

PORTABLE_GIT = r'C:\Users\zhico\.workbuddy\binaries\PortableGit'


def _resolve_git():
    """动态定位 git.exe。

    历史上这里硬编码 versions\\1.2.0\\mingw64\\bin\\git.exe, 该目录被改名
    (现为 1.2.0.old.5424 / 1.3.0) 后 git 段每天必崩。改为三级解析:
      1) versions/current 指针文件 (内含版本号, 如 "1.3.0", 与历史硬编码同一根)
      2) versions/* 目录扫描, 新版优先
      3) PATH 里可用的 git (兜底, 如 WorkBuddy 自带 vendor PortableGit)
    """
    versions = os.path.join(PORTABLE_GIT, 'versions')
    cands = []
    ptr = os.path.join(versions, 'current')
    if os.path.isfile(ptr):
        try:
            v = open(ptr, encoding='utf-8', errors='replace').read().strip()
            if v:
                cands.append(os.path.join(versions, v))
        except OSError:
            pass
    if os.path.isdir(versions):
        for d in sorted(os.listdir(versions), reverse=True):
            p = os.path.join(versions, d)
            if os.path.isdir(p):
                cands.append(p)
    for base in cands:
        g = os.path.join(base, 'mingw64', 'bin', 'git.exe')
        if os.path.isfile(g):
            return g
    return shutil.which('git') or 'git'     # 兜底: PATH, 再退化为裸名


GIT = _resolve_git()
GH = r'C:\Program Files\GitHub CLI\gh.exe'

_paths = []
if os.path.isfile(GIT):
    _gbin = os.path.dirname(GIT)                                          # .../mingw64/bin
    _paths.append(_gbin)
    _paths.append(os.path.join(os.path.dirname(os.path.dirname(_gbin)), 'usr', 'bin'))
_paths += [r'C:\Program Files\GitHub CLI', r'C:\Windows\System32', r'C:\Windows']
for p in _paths:
    if p and p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = p + os.pathsep + os.environ.get('PATH', '')

def log(*a):
    print('[sync %s]' % time.strftime('%H:%M:%S'), *a, flush=True)

def run(script, extra_env=None, timeout=3600, args=None):
    env = os.environ.copy()
    if extra_env: env.update(extra_env)
    cmd = [PY, os.path.join(TOOLS, script)] + list(args or [])
    log('-> %s %s %s' % (script, args or '', extra_env or ''))
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE, env=env,
                       capture_output=True, text=True, errors='replace', timeout=timeout)
    dt = time.time() - t0
    tail = (p.stdout or '')[-3000:]
    log('   %s rc=%s %.0fs' % (script, p.returncode, dt))
    if tail.strip():
        print(tail)
    if (p.stderr or '').strip():
        print('STDERR:', (p.stderr or '')[-1500:])
    return p.returncode

def git(*args, check=False):
    p = subprocess.run([GIT, '-C', HERE] + list(args), capture_output=True,
                       text=True, errors='replace')
    o = (p.stdout or '').strip()
    e = (p.stderr or '').strip()
    if o: print('   git %s: %s' % (args[0], o[:600]))
    if e and p.returncode != 0: print('   git %s ERR: %s' % (args[0], e[:600]))
    return p.returncode

def main():
    no_push = '--no-push' in sys.argv
    log('=== sync start, repo=%s ===' % HERE)
    log('git = %s' % GIT)

    # 1) 抓新文章的链接(增量)
    run('harvest_list.py', {'WYZ_INCREMENTAL': '1'})

    # 2) 抓正文
    run('fetch_articles.py')

    # 3) 表格体检 + 自动修复: 把被拍平成纯文本的表格在 store/ 里重建成 <table>
    #    必须早于 build —— articles/*.md 由 store/ 生成, 只改 md 会被下次构建覆盖。
    run('fix_tables.py', args=['--fix'])

    # 4) 构建仓库文件
    run('build_repo.py')

    # 5) 提交推送
    if not os.path.isdir(os.path.join(HERE, '.git')):
        log('!! 不是 git 仓库, 跳过推送'); return
    git('add', '-A')
    st = subprocess.run([GIT, '-C', HERE, 'status', '--porcelain'],
                        capture_output=True, text=True, errors='replace').stdout.strip()
    if not st:
        log('无变更, 跳过 commit/push'); return
    n = len([l for l in st.splitlines() if l.strip()])
    msg = 'sync: %s (%d 项变更)' % (time.strftime('%Y-%m-%d %H:%M'), n)
    git('commit', '-m', msg)
    if no_push:
        log('--no-push, 不推送'); return
    git('push')
    log('=== sync done ===')

if __name__ == '__main__':
    main()
