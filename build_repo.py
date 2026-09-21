# -*- coding: utf-8 -*-
"""
build_repo.py —— 把 store/ 里的已抓正文构建成 GitHub 仓库内容

产出:
  articles/<YYYY-MM-DD> <标题>.md   每篇一个 Markdown(含副标题+发表日期)
  images/<hash>.<ext>               本地化图片
  state/index.json                  mid -> {title, subtitle, date, url, file}
  README.md                         文章总览表
"""
import os, re, sys, json, time, hashlib, datetime
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_articles as F

ART_JSON = os.path.join(HERE, 'articles.json')
STORE = os.path.join(HERE, 'store')
ART_DIR = os.path.join(HERE, 'articles')
STATE_DIR = os.path.join(HERE, 'state')
INDEX = os.path.join(STATE_DIR, 'index.json')
os.makedirs(ART_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

# ---------------- 摘要(副标题) ----------------
LLM_BASE = 'https://open.bigmodel.cn/api/coding/paas/v4'
LLM_KEY = os.environ.get('GLM_API_KEY', '')
if not LLM_KEY:
    try:
        mj = json.load(open(os.path.expanduser('~/.workbuddy/models.json'), encoding='utf-8'))
        LLM_KEY = (mj[0] or {}).get('apiKey', '')
    except Exception:
        LLM_KEY = ''
LLM_MODEL = os.environ.get('WYZ_LLM_MODEL', 'glm-4-flash')

SYS = ('你在给一位公众号作者的文章写「副标题」。要求：\n'
       '1) 一句话，25-45 个汉字；\n'
       '2) 准确概括文章的核心论点/结论，不要空泛套话，不要出现"本文""这篇文章"等词；\n'
       '3) 不要引号、不要书名号、不要结尾句号；\n'
       '4) 只输出副标题本身，不要任何解释或前缀。')

def _clean_snippet(text, n=2600):
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text or '')
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:n]

def llm_subtitle(title, body):
    if not LLM_KEY:
        return None
    try:
        r = requests.post(LLM_BASE + '/chat/completions',
                          headers={'Authorization': 'Bearer ' + LLM_KEY,
                                   'Content-Type': 'application/json'},
                          json={'model': LLM_MODEL, 'temperature': 0.3, 'max_tokens': 200,
                                'messages': [{'role': 'system', 'content': SYS},
                                             {'role': 'user',
                                              'content': '标题：%s\n\n正文节选：\n%s' % (title, body)}]},
                          timeout=90)
        j = r.json()
        c = ((j.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
        c = c.strip().strip('"“”\'')
        c = re.sub(r'^(副标题[:：]?\s*)', '', c)
        c = c.split('\n')[0].strip().strip('。.')
        if 8 <= len(c) <= 80:
            return c
    except Exception as e:
        print('    llm err %r' % e)
    return None

def fallback_subtitle(title, body):
    """无 LLM 时的兜底: 取正文首个实义句片段"""
    t = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', body or '')
    t = re.sub(r'\s+', '', t)
    t = re.sub(r'^[（(].*?[）)]', '', t)
    if not t:
        return '（无正文，仅图片/短帖）'
    cut = re.split(r'[。！？；]', t)
    s = ''
    for seg in cut:
        if len(s) + len(seg) > 44: break
        s += seg
    return (s or t[:40]).strip()

def get_subtitle(store, title, body, cache_file):
    if store.get('subtitle'):
        return store['subtitle'], store.get('subtitle_src', 'cache')
    plain = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', body or '')
    plain = re.sub(r'[\s\-*>|#]+', '', plain)
    if len(plain) < 40:
        sub, src = '纯图片 / 短帖，无正文', 'auto'
    else:
        sub = llm_subtitle(title, _clean_snippet(body))
        src = 'llm'
        if not sub:
            sub = fallback_subtitle(title, body)
            src = 'fallback'
    store['subtitle'] = sub
    store['subtitle_src'] = src
    try:
        json.dump(store, open(cache_file, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass
    return sub, src

# ---------------- 文件命名 ----------------
def file_name(date, title, used):
    base = '%s %s' % (date, F.safe_name(title))
    n, name = 1, base + '.md'
    while name in used:
        n += 1
        name = '%s (%d).md' % (base, n)
    used.add(name)
    return name

def local_part(url):
    """保留可长期访问的参数: biz/mid/idx/sn/chksm (去掉 key/uin/pass_ticket 等会话凭据)"""
    q = re.search(r'\?(.*)$', url)
    if not q:
        return url
    keep = ('__biz', 'mid', 'idx', 'sn', 'chksm', 'scene')
    parts = []
    for kv in q.group(1).split('&'):
        k = kv.split('=')[0]
        if k in keep:
            parts.append(kv)
    return 'https://mp.weixin.qq.com/s?' + '&'.join(parts)

def main():
    arts = json.load(open(ART_JSON, encoding='utf-8'))
    idx = {}
    if os.path.exists(INDEX):
        try: idx = json.load(open(INDEX, encoding='utf-8'))
        except Exception: idx = {}
    used = set()
    built = skipped = 0
    rows = []
    print('articles =', len(arts))

    for it in arts:
        url = F.sanitize_url(it['url'])
        key = hashlib.md5(url.encode()).hexdigest()[:16]
        cf = os.path.join(STORE, key + '.json')
        if not os.path.exists(cf):
            print('  skip (未抓正文) %r' % it.get('title'))
            skipped += 1
            continue
        s = json.load(open(cf, encoding='utf-8'))
        if not s.get('ok'):
            print('  skip (抓取失败) %r' % it.get('title'))
            skipped += 1
            continue

        title = s.get('title') or it.get('title') or 'untitled'
        ts = s.get('ts')
        if ts:
            dt = datetime.datetime.fromtimestamp(ts)
            date = dt.strftime('%Y-%m-%d')
            dtime = dt.strftime('%Y-%m-%d %H:%M')
        else:
            date, dtime = '1970-01-01', ''

        body = F.to_markdown(s.get('inner') or '', s.get('imgs') or [])
        sub, src = get_subtitle(s, title, body, cf)

        mid = s.get('mid') or key
        prev = idx.get(mid) or {}
        if prev.get('file') and os.path.exists(os.path.join(HERE, prev['file'])):
            fname = os.path.basename(prev['file'])      # 复用旧文件名, 保证跨轮稳定
            used.add(fname)
        else:
            fname = file_name(date, title, used)
        fpath = os.path.join(ART_DIR, fname)
        src_url = local_part(url)
        fm = ['---',
              'title: %s' % json.dumps(title, ensure_ascii=False),
              'subtitle: %s' % json.dumps(sub, ensure_ascii=False),
              'date: %s' % date,
              'datetime: %s' % dtime,
              'account: %s' % json.dumps(s.get('account') or '无语子点点', ensure_ascii=False),
              'source: %s' % json.dumps(src_url, ensure_ascii=False),
              '---', '']
        doc = '\n'.join(fm)
        doc += '# %s\n\n' % title
        doc += '> **副标题**：%s  \n> **发表日期**：%s  \n> **原文**：%s\n\n---\n\n' % (sub, dtime, src_url)
        doc += body + '\n'
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(doc)

        mid = s.get('mid') or key
        idx[mid] = {'title': title, 'subtitle': sub, 'subtitle_src': src, 'date': date,
                    'datetime': dtime, 'url': src_url, 'file': 'articles/' + fname,
                    'imgs': len(s.get('imgs') or []), 'chars': len(body)}
        rows.append(idx[mid])
        built += 1
        print('  [%2d] %s  <- %r' % (built, fname, sub[:40]))

    # 清理孤儿 md(改名/删文后残留)
    keep = {os.path.basename(r['file']) for r in rows}
    for fn in os.listdir(ART_DIR):
        if fn.endswith('.md') and fn not in keep:
            try:
                os.remove(os.path.join(ART_DIR, fn))
                print('  rm orphan', fn)
            except Exception:
                pass

    json.dump(idx, open(INDEX, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    # README
    rows.sort(key=lambda r: (r['date'], r['datetime']), reverse=True)
    lines = ['# 无语子点点 · 文章归档', '',
             '本仓库自动同步微信公众号「无语子点点」的全部文章。',
             '每篇文章提供标题、AI 生成的**副标题**（概括正文要点）与**发表日期**，正文转为 Markdown，图片已本地化。',
             '', '共 **%d** 篇。' % len(rows), '',
             '| 发表日期 | 标题 | 副标题 |', '| --- | --- | --- |']
    for r in rows:
        lines.append('| %s | [%s](%s) | %s |' % (
            r['datetime'] or r['date'], r['title'].replace('|', '\\|'),
            r['file'].replace(' ', '%20'), r['subtitle'].replace('|', '\\|')))
    lines += ['', '---', '',
              '*最后同步：%s*' % time.strftime('%Y-%m-%d %H:%M:%S'),
              '', '*本仓库由自动化脚本生成：使用微信 PC 端内置浏览器取文章链接，再用 HTTP 直取正文。*']
    open(os.path.join(HERE, 'README.md'), 'w', encoding='utf-8').write('\n'.join(lines))

    print('built = %d, skipped = %d, total index = %d' % (built, skipped, len(idx)))

if __name__ == '__main__':
    main()
