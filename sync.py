# -*- coding: utf-8 -*-
"""
sync.py —— 增量同步「无语子点点」的全部文章到 GitHub

流程:
  1) harvest_list.py   (WYZ_INCREMENTAL=1) 微信界面里只抓「上次之后的新文章」的链接
  2) fetch_articles.py 用这些新链接 HTTP 直取正文(链接里的 key 是会话级, 必须紧接第 1 步)
  3) build_repo.py     生成 Markdown + README + state/index.json
  4) git commit & push

用法: python sync.py            # 完整同步
      python sync.py --no-push  # 只本地构建
"""
import os, sys, json, time, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

GIT = r'C:\Users\zhico\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin\git.exe'
GH = r'C:\Program Files\GitHub CLI\gh.exe'
for p in (r'C:\Users\zhico\.workbuddy\binaries\PortableGit\versions\1.2.0\mingw64\bin',
          r'C:\Users\zhico\.workbuddy\binaries\PortableGit\versions\1.2.0\usr\bin',
          r'C:\Program Files\GitHub CLI',
          r'C:\Windows\System32', r'C:\Windows'):
    if p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = p + os.pathsep + os.environ.get('PATH', '')

def log(*a):
    print('[sync %s]' % time.strftime('%H:%M:%S'), *a, flush=True)

def run(script, extra_env=None, timeout=3600):
    env = os.environ.copy()
    if extra_env: env.update(extra_env)
    log('-> %s %s' % (script, extra_env or ''))
    t0 = time.time()
    p = subprocess.run([PY, os.path.join(HERE, script)], cwd=HERE, env=env,
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

    # 1) 抓新文章的链接(增量)
    run('harvest_list.py', {'WYZ_INCREMENTAL': '1'})

    # 2) 抓正文
    run('fetch_articles.py')

    # 3) 构建仓库文件
    run('build_repo.py')

    # 4) 提交推送
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
