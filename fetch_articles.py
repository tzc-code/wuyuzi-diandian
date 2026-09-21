# -*- coding: utf-8 -*-
"""
fetch_articles.py —— 抓取公众号文章正文, 转 Markdown, 图片本地化

为什么不需要登录:
  https://mp.weixin.qq.com/s?... 是可公开访问的 HTML。但实测有坑:
    - 形如 s?__biz=..&mid=..&idx=..&sn=.. 且**不带 key/chksm** 的链接会被返回
      17KB 空壳页(软封锁), 必须用微信内置浏览器里点出来的**完整链接**
      (带 chksm + key + uin + pass_ticket), 或官方短码 s/<code> 形式。
    - 完整链接里的 key 是会话级凭据, 会过期 -> 必须"抓链接"和"抓正文"同一轮做。
  所以本模块只吃 articles.json 里浏览器抓出来的完整 URL。

产出:
  store/<md5>.html        原始 HTML 缓存
  store/<md5>.json        元数据 + 正文 HTML 片段(供后续生成 Markdown)
  images/<hash>.<ext>     本地化图片
"""
import os, re, json, sys, time, hashlib, html as htmlmod
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

HERE = os.path.dirname(os.path.abspath(__file__))
ART_JSON = os.path.join(HERE, 'articles.json')
STORE = os.path.join(HERE, 'store')
IMG_DIR = os.path.join(HERE, 'images')
os.makedirs(STORE, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Referer': 'https://mp.weixin.qq.com/',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def warmup():
    try:
        SESSION.get('https://mp.weixin.qq.com/', timeout=20)
    except Exception:
        pass

def is_good(h):
    return bool(h) and 'id="js_content"' in h and len(h) > 100000

def get_article(url, tries=4, base_delay=3.0):
    """带校验与退避的抓取"""
    last = ''
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=40)
            h = r.text
            if is_good(h):
                return h, None
            last = 'len=%d status=%s (空壳页/软封锁)' % (len(h), r.status_code)
        except Exception as e:
            last = repr(e)
        time.sleep(base_delay * (i + 1))
    return '', last

def sanitize_url(u):
    return u.split('#')[0]

def meta_of(h):
    def g(pat, flags=re.S):
        m = re.search(pat, h, flags)
        return m.group(1).strip() if m else None

    title = (g(r'var\s+msg_title\s*=\s*[\'"](.*?)[\'"]')
             or g(r'<meta\s+property="og:title"\s+content="(.*?)"')
             or g(r'rich_media_title[^>]*>(.*?)<'))
    if title:
        title = htmlmod.unescape(re.sub(r'<[^>]+>', '', title)).strip()

    desc = (g(r'var\s+msg_desc\s*=\s*[\'"](.*?)[\'"]')
            or g(r'<meta\s+name="description"\s+content="(.*?)"'))
    if desc:
        desc = htmlmod.unescape(desc).strip()

    nick = (g(r'var\s+nickname\s*=\s*[\'"](.*?)[\'"]')
            or g(r'id="js_name"[^>]*>(.*?)<'))
    if nick:
        nick = htmlmod.unescape(nick).strip()

    ct = g(r'var\s+ct\s*=\s*"(\d+)"') or g(r'var\s+create_time\s*=\s*"(\d+)"')
    msgid = g(r'var\s+mid\s*=\s*"(\d+)"') or g(r'mid=(\d+)')
    sn = g(r'var\s+sn\s*=\s*"([0-9a-f]+)"') or g(r'sn=([0-9a-f]+)')
    return {'title': title, 'desc': desc, 'account': nick,
            'ts': int(ct) if ct else None, 'mid': msgid, 'sn': sn}

def content_html(h):
    """取 js_content 的 innerHTML; 返回 (html, [图片url])"""
    soup = BeautifulSoup(h, 'lxml')
    node = soup.find(id='js_content')
    if node is None:
        return '', []
    imgs = []
    for img in node.find_all('img'):
        src = img.get('data-src') or img.get('src') or ''
        if src:
            imgs.append(src)
            img['src'] = '\x00IMG%d\x00' % (len(imgs) - 1)
        for a in list(img.attrs):
            if a.startswith('data-'):
                del img.attrs[a]
    for tag in node.find_all(['script', 'style']):
        tag.decompose()
    return node.decode_contents(), imgs

def download_img(url, referer='https://mp.weixin.qq.com/'):
    try:
        ext = '.jpg'
        m = re.search(r'wx_fmt=([a-zA-Z0-9]+)', url)
        if m:
            ext = '.' + m.group(1).lower()
        else:
            p = url.split('?')[0]
            if '.' in p[-6:]:
                ext = os.path.splitext(p)[1].lower() or '.jpg'
        if ext == '.jpeg' or ext == '.jfif':
            ext = '.jpg'
        hn = hashlib.md5(url.encode()).hexdigest()[:16]
        fn, path, rel = hn + ext, os.path.join(IMG_DIR, hn + ext), 'images/' + hn + ext
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return rel
        r = requests.get(url, headers={**HEADERS, 'Referer': referer}, timeout=30)
        if r.status_code == 200 and r.content:
            with open(path, 'wb') as f:
                f.write(r.content)
            return rel
    except Exception as e:
        print('    img fail %s %r' % (url[:70], e))
    return url

def to_markdown(inner, imgs, localize=True):
    text = md(inner, heading_style='ATX', strip=['span', 'section'])
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not localize:
        return text

    def repl(m):
        i = int(m.group(1))
        if i >= len(imgs):
            return ''
        u = download_img(imgs[i])
        return '\n\n![图](%s)\n\n' % u
    return re.sub(r'\x00IMG(\d+)\x00', repl, text)

def safe_name(s, maxlen=60):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', s or '').strip().strip('.')
    s = re.sub(r'\s+', ' ', s)
    return s[:maxlen] or 'untitled'

def fetch_one(item, force=False):
    """抓一篇; 返回 store dict"""
    url = sanitize_url(item['url'])
    key = hashlib.md5(url.encode()).hexdigest()[:16]
    if not force:
        cm = os.path.join(STORE, key + '.json')
        if os.path.exists(cm):
            try:
                s = json.load(open(cm, encoding='utf-8'))
                if s.get('ok'):
                    return s
            except Exception:
                pass
    store = {'key': key, 'url': url, 'ok': False}
    h, err = get_article(url)
    if not h:
        store['err'] = err
        json.dump(store, open(os.path.join(STORE, key + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False)
        return store
    if os.environ.get('WYZ_KEEP_HTML') == '1':
        with open(os.path.join(STORE, key + '.html'), 'w', encoding='utf-8') as f:
            f.write(h)
    m = meta_of(h)
    inner, imgs = content_html(h)
    store.update(m)
    store['imgs'] = imgs
    store['inner'] = inner
    store['ok'] = bool(inner)
    if not inner:
        store['err'] = 'no js_content'
    json.dump(store, open(os.path.join(STORE, key + '.json'), 'w', encoding='utf-8'),
              ensure_ascii=False)
    return store

def main():
    arts = json.load(open(ART_JSON, encoding='utf-8'))
    only_new = os.environ.get('WYZ_ONLY_NEW') == '1'
    print('articles =', len(arts), 'only_new =', only_new)
    warmup()
    ok = 0
    for i, it in enumerate(arts):
        s = fetch_one(it)
        print('[%3d/%d] %s %-20r -> %r  ts=%s imgs=%d' % (
            i + 1, len(arts), 'OK ' if s.get('ok') else 'ERR',
            (it.get('title') or '')[:18], (s.get('title') or '')[:26],
            s.get('ts'), len(s.get('imgs') or [])))
        if s.get('ok'):
            ok += 1
        time.sleep(1.2)
    print('ok = %d / %d' % (ok, len(arts)))

if __name__ == '__main__':
    main()
