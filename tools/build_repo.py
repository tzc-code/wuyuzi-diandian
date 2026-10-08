# -*- coding: utf-8 -*-
"""
build_repo.py —— 把 store/ 里的已抓正文构建成 GitHub 仓库内容

产出:
  articles/<YYYY-MM-DD> <标题>.md   每篇一个 Markdown(含副标题+发表日期)
  images/<hash>.<ext>               本地化图片
  state/index.json                  mid -> {title, subtitle, date, url, file}
  README.md                         文章总览表
"""
import os, re, sys, json, time, hashlib, datetime, random
import requests

TOOLS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TOOLS)              # 仓库根
sys.path.insert(0, TOOLS)
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

# ---------------- 文内链接(相关文章) ----------------
# 该号有三种引用方式, 都要处理:
#   ① <a href> 内链 (mp.weixin 带 mid)      -- 2026-10-08 前唯一处理的
#   ② 裸贴 mp.weixin 长链 (带 mid)          -- 纯文本
#   ③ 裸贴 mp.weixin 短链 (/s/<hash>)       -- 纯文本, 需 HTTP 解析出 mid
#   ④ 外部平台裸链 (小红书 xhslink / 知乎 / B站 b23.tv ...) -- 只能给外链
GITHUB_BLOB = 'https://github.com/tzc-code/wuyuzi-diandian/blob/main/'
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_HREF_ALL = re.compile(r'<a\b[^>]*>', re.I)
_URL = re.compile(r'https?://[^\s<>"\'）)】\]，,。；;、]+', re.I)
_MP_SHORT = re.compile(r'^https?://mp\.weixin\.qq\.com/s/([A-Za-z0-9_\-]+)$', re.I)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
_short_cache = {}          # hash -> mid|None, 跨轮同进程复用

def resolve_short(h):
    """把 mp.weixin 短链 /s/<hash> 解析出 mid; 失败返回 None(空壳页/已删)"""
    if h in _short_cache:
        return _short_cache[h]
    mid = None
    try:
        r = requests.get('https://mp.weixin.qq.com/s/' + h,
                         headers={'User-Agent': UA}, timeout=30, allow_redirects=True)
        m = (re.search(r'[?&]mid=(\d+)', r.url)
             or re.search(r'var\s+mid\s*=\s*"?(\d+)', r.text)
             or re.search(r'"mid"\s*:\s*"?(\d+)', r.text))
        if m:
            mid = m.group(1)
    except Exception as e:
        print('    (短链解析失败) %s %r' % (h, e))
    _short_cache[h] = mid
    return mid

def extract_refs(inner, self_mid, resolve_shorts=True):
    """提取文内引用。

    返回 (mp_mids, ext_urls):
      mp_mids  : 本号文章 mid(保序去重, 含 href 内链 + 裸贴长链 + 短链解析结果)
      ext_urls : 外部平台链接(小红书/知乎/B站等)完整 URL(保序去重)
    """
    if not inner:
        return [], []
    import html as _h
    mids, ext = [], []
    seen_m, seen_e = set(), set()

    def add_mid(m):
        if m and m != str(self_mid) and m not in seen_m:
            seen_m.add(m); mids.append(m)

    def add_ext(u):
        if u not in seen_e:
            seen_e.add(u); ext.append(u)

    # ① <a href> 内链: 里面可能有 mp 长链, 也可能有外部平台链接
    for raw in _HREF.findall(inner):
        u = _h.unescape(raw)
        m = re.search(r'[?&]mid=(\d+)', u)
        if m:
            add_mid(m.group(1)); continue
        ms = _MP_SHORT.match(u.split('#')[0])
        if ms:
            add_mid(resolve_short(ms.group(1)) if resolve_shorts else None); continue
        if u.startswith('http'):
            add_ext(u.split('#')[0])

    # ② 纯文本里的裸链接(含 mp 长链/短链/外部平台)
    text = _h.unescape(re.sub(r'<[^>]+>', ' ', inner))
    for u in _URL.findall(text):
        u = u.rstrip('，,。;；、')
        m = re.search(r'[?&]mid=(\d+)', u)
        if m:
            add_mid(m.group(1)); continue
        ms = _MP_SHORT.match(u)
        if ms:
            add_mid(resolve_short(ms.group(1)) if resolve_shorts else None); continue
        add_ext(u)

    return mids, ext

def md_link(path):
    """md 相对链接: 空格等需转义, 否则 GitHub 上点不动(中文/全角可保留)"""
    return path.replace(' ', '%20')

PLATFORM = [('xhslink', '小红书'), ('xiaohongshu', '小红书'),
            ('zhihu', '知乎'), ('b23.tv', 'B站'), ('bilibili', 'B站'),
            ('weibo', '微博'), ('douyin', '抖音'), ('kuaishou', '快手')]

def platform_of(u):
    for k, name in PLATFORM:
        if k in u.lower():
            return name
    return '外部链接'

# ---------------- 外部链接标题抓取(防封) ----------------
# 设计原则(避免被小红书等封禁):
#   1) 磁盘缓存: 抓过的永久存 state/ext_titles.json, 同一 URL 永不重复请求
#   2) 串行 + 随机延时 3~6s: 远低于人工浏览频率, 不触发风控
#   3) 失败即停: 遇 403/429/异常, 立即终止该平台本轮抓取, 不重试不硬撑
#   4) 单轮总量上限 MAX_FETCH, 超出的留到下轮(缓存会逐步补齐)
#   5) 不登录、不带 cookie, 只用公开 UA 拿页面自带的分享摘要
#   6) 小红书用**移动端 UA** + 解析 __INITIAL_STATE__ 的 noteData.data.noteData.title
#      (桌面 UA 会跳登录页; og:title 在笔记页是站点名「小红书」, 不可用)
EXT_CACHE = os.path.join(STATE_DIR, 'ext_titles.json')
MAX_FETCH = int(os.environ.get('WYZ_EXT_MAX', '12'))      # 单轮最多抓几个
UA_MOBILE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
             'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')
NOISE = ('小红书', '小红书 - 你的生活兴趣社区', '你访问的页面不见了', '小红书 - 你访问的页面不见了',
         '知乎', '哔哩哔哩', '你的生活兴趣社区')
_ext_cache = None
_fail_ban = set()                                          # 本轮已判定失败的平台

def _load_ext_cache():
    global _ext_cache
    if _ext_cache is None:
        try:
            _ext_cache = json.load(open(EXT_CACHE, encoding='utf-8'))
        except Exception:
            _ext_cache = {}
    return _ext_cache

def _save_ext_cache():
    try:
        json.dump(_ext_cache or {}, open(EXT_CACHE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass

def _clean_title(t):
    """清理抓来的标题: 去 emoji/特殊符号/多余空白, 让列表可读"""
    if not t:
        return ''
    # 去 emoji 与各类符号(Misc Symbols/Emoticons/Transport/Supplemental 等)
    t = re.sub(r'[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF'
               r'\U00002190-\U000021FF\U00002B00-\U00002BFF\U0000FE0F\U0000200D]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def _extract_title(text):
    """从 HTML 取页面标题(通用: og:title -> <title>)"""
    m = (re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']*)', text)
         or re.search(r'content=["\']([^"\']*)["\'][^>]*property=["\']og:title["\']', text))
    t = m.group(1) if m else None
    if not t:
        m2 = re.search(r'<title>(.*?)</title>', text, re.S)
        t = m2.group(1) if m2 else ''
    t = re.sub(r'\s+', ' ', (t or '')).strip()
    for suf in (' - 小红书', ' - 知乎', '_哔哩哔哩_bilibili', ' - 哔哩哔哩'):
        if t.endswith(suf):
            t = t[:-len(suf)]
    return _clean_title(t)

def _xhs_title(text):
    """小红书: 从 __INITIAL_STATE__ 的 noteData 里取笔记标题(og:title 不可用)"""
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', text, re.S)
    if not m:
        return ''
    try:
        d = json.loads(m.group(1).replace('undefined', 'null'))
    except Exception:
        return ''
    nd = (d.get('noteData') or {})
    for path in ((nd.get('data') or {}).get('noteData'),
                 nd.get('normalNotePreloadData')):
        if isinstance(path, dict):
            t = (path.get('title') or '').strip()
            if t and t not in NOISE:
                return _clean_title(t)
            if not t:                              # 无标题取正文首句
                dsc = re.sub(r'\s+', ' ', (path.get('desc') or '')).strip()
                if dsc:
                    return _clean_title(dsc[:60])
    return ''

def fetch_ext_title(u, budget):
    """抓单个外部链接标题。返回 (title|None, 是否触发了封禁)"""
    plat = platform_of(u)
    if plat in _fail_ban:
        return None, False
    if budget['n'] >= MAX_FETCH:
        return None, False
    cache = _load_ext_cache()
    if u in cache:
        return (cache[u] or None), False
    budget['n'] += 1
    try:
        time.sleep(random.uniform(3.0, 6.0))       # 慢速: 防封核心
        ua = UA_MOBILE if plat == '小红书' else UA
        r = requests.get(u, headers={'User-Agent': ua, 'Accept-Language': 'zh-CN,zh;q=0.9'},
                         timeout=25, allow_redirects=True)
        if r.status_code in (403, 429):
            print('    [防封] %s 返回 %d, 停止该平台抓取' % (plat, r.status_code))
            _fail_ban.add(plat)
            return None, True
        t = _xhs_title(r.text) if plat == '小红书' else ''
        if not t:
            t = _extract_title(r.text)
        if t in NOISE:                              # 站点名/登录页 -> 视为失败
            t = ''
        cache[u] = t or ''
        _save_ext_cache()
        return (t or None), False
    except Exception as e:
        print('    [防封] %s 请求异常 %r, 停止该平台抓取' % (plat, e))
        _fail_ban.add(plat)
        return None, True

def refs_block(refs, ext=None, unresolved=None):
    """生成 md 末尾的「相关文章」区块

    refs       : [{'title','date','subtitle','file_rel','github'}] 本号往期文(已归档)
    ext        : [url, ...] 外部平台链接(小红书/知乎/B站等)
    unresolved : [url_or_mid, ...] 本号文章但未能解析/未归档, 列出原始链接供人工查看
    标题多为「。。」「1」这类无意义短串, 故附上日期 + 副标题便于辨认。
    """
    refs = refs or []
    ext = ext or []
    unresolved = unresolved or []
    if not (refs or ext or unresolved):
        return ''
    lines = ['', '---', '', '## 相关文章', '']
    if refs:
        lines += ['> 本文中提到的往期文章（已归档到本仓库）：', '']
        for r in refs:
            t = (r.get('title') or '未命名').replace(']', '\\]').replace('[', '\\[')
            date = r.get('date') or ''
            sub = (r.get('subtitle') or '').replace(']', '\\]').replace('[', '\\[')
            if sub and sub not in ('纯图片 / 短帖，无正文',):
                t += '（%s）' % sub
            lines.append('- %s [%s](%s) · [GitHub](%s)' % (
                (date + ' ') if date else '', t, md_link(r['file_rel']), r['github']))
        lines.append('')
    if ext:
        lines += ['> 文中引用的外部平台内容（原文链接）：', '']
        for item in ext:
            # 兼容旧格式(纯 url 字符串)与新格式({'url','title'})
            u = item if isinstance(item, str) else item.get('url', '')
            t = '' if isinstance(item, str) else (item.get('title') or '')
            uu = u.replace(')', '%29')
            plat = platform_of(u)
            if t:
                tt = t.replace(']', '\\]').replace('[', '\\[')
                if len(tt) > 48:
                    # 在标点处收尾, 避免截断在句子中间
                    seg = re.split(r'[。！？；，,\n]', tt[:56])[0].strip()
                    tt = (seg if 8 <= len(seg) <= 56 else tt[:48]) + '…'
                lines.append('- [%s · %s](%s)' % (plat, tt, uu))
            else:
                lines.append('- [%s](%s)' % (plat, uu))
        lines.append('')
    if unresolved:
        lines += ['> 文中提到但**未归档到本仓库**的本号文章（原始链接）：', '']
        for u in unresolved:
            lines.append('- %s' % u)
        lines.append('')
    return '\n'.join(lines)

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

    # ---------- pass 1: 载入全部 store, 确定文件名 & mid->file 映射 ----------
    items = []
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

        mid = str(s.get('mid') or key)
        prev = idx.get(mid) or {}
        if prev.get('file') and os.path.exists(os.path.join(HERE, prev['file'])):
            fname = os.path.basename(prev['file'])      # 复用旧文件名, 保证跨轮稳定
            used.add(fname)
        else:
            fname = file_name(date, title, used)
        items.append({'s': s, 'cf': cf, 'it': it, 'url': url, 'mid': mid,
                      'title': title, 'date': date, 'dtime': dtime, 'fname': fname})
    mid2file = {x['mid']: x['fname'] for x in items}
    ext_budget = {'n': 0}          # 本轮外部链接抓取计数(防封限流)

    # ---------- pass 2: 渲染 ----------
    for x in items:
        s, cf, mid, title = x['s'], x['cf'], x['mid'], x['title']
        date, dtime, fname = x['date'], x['dtime'], x['fname']
        url = x['url']

        body = F.to_markdown(s.get('inner') or '', s.get('imgs') or [])
        sub, src = get_subtitle(s, title, body, cf)

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

        # 文内引用 -> 相关文章区块(本号已归档 / 本号未归档 / 外部平台)
        ref_mids, ext_urls = extract_refs(s.get('inner') or '', mid)
        refs, unresolved = [], []
        for rm in ref_mids:
            rf = mid2file.get(rm)
            if not rf:
                print('    (ref 不在库内) %s -> mid=%s' % (fname, rm))
                unresolved.append('https://mp.weixin.qq.com/s?__biz=%s&mid=%s&idx=1'
                                  % (s.get('biz') or 'MzYzMTIxMjk4NQ==', rm))
                continue
            rmeta = idx.get(rm) or {}
            refs.append({'mid': rm, 'title': rmeta.get('title') or '',
                         'date': rmeta.get('date') or '',
                         'subtitle': rmeta.get('subtitle') or '',
                         'file_rel': 'articles/' + rf,
                         'github': GITHUB_BLOB + 'articles/' + rf.replace(' ', '%20')})
        # 注意: ext_items 必须每轮无条件重置。之前只在 if 分支内初始化,
        # 导致无引用的文章沿用上一轮的 ext_items(残留泄漏到 index.json)。
        ext_items = []
        if refs or ext_urls or unresolved:
            # 外部链接补充标题(带缓存+限速, 防封)
            for u in ext_urls:
                t, _ = fetch_ext_title(u, ext_budget)
                ext_items.append({'url': u, 'title': t or ''})
            doc += refs_block(refs, ext_items, unresolved)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(doc)

        idx[mid] = {'title': title, 'subtitle': sub, 'subtitle_src': src, 'date': date,
                    'datetime': dtime, 'url': src_url, 'file': 'articles/' + fname,
                    'imgs': len(s.get('imgs') or []), 'chars': len(body),
                    'refs': [{'mid': r['mid'], 'title': r['title'], 'file': r['file_rel']}
                             for r in refs],
                    'ext': ext_items,
                    'unresolved': unresolved}
        rows.append(idx[mid])
        built += 1
        n_ref = len(refs) + len(ext_items) + len(unresolved)
        print('  [%2d] %s  <- %r%s' % (built, fname, sub[:40],
                                       ('  [refs:%d ext:%d unk:%d]'
                                        % (len(refs), len(ext_items), len(unresolved))) if n_ref else ''))

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
             '文中提到的往期文章会在文末以**相关文章**列出，并可跳转到本仓库内对应归档。',
             '', '共 **%d** 篇。' % len(rows), '',
             '| 发表日期 | 标题 | 副标题 |', '| --- | --- | --- |']
    for r in rows:
        lines.append('| %s | [%s](%s) | %s |' % (
            r['datetime'] or r['date'], r['title'].replace('|', '\\|'),
            r['file'].replace(' ', '%20'), r['subtitle'].replace('|', '\\|')))
    nref = sum(len(r.get('refs') or []) for r in rows)
    next_ = sum(len(r.get('ext') or []) for r in rows)
    nunk = sum(len(r.get('unresolved') or []) for r in rows)
    if nref or next_ or nunk:
        lines += ['', '> 文内引用：**%d** 处链接到本仓库归档；**%d** 处外部平台内容（小红书/知乎等）；'
                  '**%d** 处本号文章暂未归档。' % (nref, next_, nunk)]
    lines += ['', '---', '',
              '*最后同步：%s*' % time.strftime('%Y-%m-%d %H:%M:%S'),
              '', '*本仓库由自动化脚本生成：使用微信 PC 端内置浏览器取文章链接，再用 HTTP 直取正文。*']
    open(os.path.join(HERE, 'README.md'), 'w', encoding='utf-8').write('\n'.join(lines))

    print('built = %d, skipped = %d, total index = %d' % (built, skipped, len(idx)))

if __name__ == '__main__':
    main()
