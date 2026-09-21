# -*- coding: utf-8 -*-
"""
harvest_list.py —— 抓取「无语子点点」公众号全部文章链接

原理
====
微信 PC 端内置浏览器(WMPF/Radium 运行时) 是 Chromium 内核, DOM 通过 UI Automation 暴露。
公众号主页 profile.html 每个文章项 DOM: [日期] -> [标题] -> [阅读 N 赞 N]
列表项**没有 Hyperlink**(拿不到 href), 必须逐篇点进去读 Document.Value 拿 URL。

踩坑结论(全部实测)
==================
1) 微信**吞掉** mouse_event / SetCursorPos 合成的鼠标事件 -> 用 PostMessage 直投
   Chrome_RenderWidgetHostHWND 渲染窗口才有效。
2) 键盘事件不拦, 滚轮有效; 但 Ctrl+Home/End 在页面已滚动时**不可靠** -> 只用滚轮。
3) 找不到浏览器窗口时不要用 uiautomation 的 GetRootControl().GetChildren(),
   要 EnumWindows + auto.ControlFromHandle(hwnd)。
4) 返回主页: Alt+Left / VK_BROWSER_BACK / 后退按钮(Invoke/PostMessage/真实鼠标)
   **全部无效**; 唯一可靠办法 = 点文章页顶部的公众号名片 Hyperlink「无语子点点」。
   代价: 主页重新加载, 列表回到顶部 -> 每篇都要重新滚下去(本脚本已做批量滚轮优化)。
5) Ctrl+左键 / 中键 都不会开新窗口(WMPF 单窗口)。
6) 正文不需要在这里抓 —— 裸 requests 可直接 GET 文章页(见 fetch_articles.py)。

输出: articles.json  [ {idx,title,page_title,date,url} ]
"""
import os, sys, time, json, re, traceback
from collections import defaultdict, Counter
import win32gui, win32con, win32api, win32process
import uiautomation as auto

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'harvest.log')
OUT = os.path.join(HERE, 'articles.json')

_buf = []
def log(*a):
    s = ' '.join(str(x) for x in a)
    _buf.append(s)
    try: open(LOG, 'w', encoding='utf-8').write('\n'.join(_buf))
    except Exception: pass

auto.SetGlobalSearchTimeout(2)

PROFILE_MARK = 'SubscriptionProfile/profile.html'
ARTICLE_MARK = 'mp.weixin.qq.com/s'
READ_RE = re.compile(r'^阅读')
ROW_PX = 331.0        # 列表项行距
UNIT_PX = 102.0       # 滚轮 1 单位像素
ACCOUNT = '无语子点点'
LIMIT = int(os.environ.get('WYZ_LIMIT', '0') or 0)
INCREMENTAL = os.environ.get('WYZ_INCREMENTAL') == '1'

def load_known_mids():
    """已入库文章的 mid 集合(来自 build_repo.py 生成的 state/index.json)"""
    try:
        d = json.load(open(os.path.join(HERE, 'state', 'index.json'), encoding='utf-8'))
        return set(str(k) for k in d.keys())
    except Exception:
        return set()

# ---------------------------------------------------------------- 基础
def walk(ctrl, depth=0, acc=None, maxdepth=24):
    if acc is None: acc = []
    if depth > maxdepth: return acc
    try: kids = ctrl.GetChildren()
    except Exception: return acc
    for c in kids:
        acc.append((depth, c)); walk(c, depth + 1, acc, maxdepth)
    return acc

def _type(c):
    try: return c.ControlTypeName
    except Exception: return ''

def doc_of(h):
    ctrl = auto.ControlFromHandle(h)
    if ctrl is None: return None, None
    for d, c in walk(ctrl, maxdepth=10):
        if _type(c) == 'DocumentControl':
            try: return c, c.GetValuePattern().Value or ''
            except Exception: return c, None
    return None, None

class Browser:
    """定位并持有微信内置浏览器窗口"""
    def __init__(self):
        self.hwnd = None
        self.win = None
        self.pids = set()

    def find(self):
        cands = []
        win32gui.EnumWindows(
            lambda h, _: (cands.append(h)
                          if 'Chrome_WidgetWin' in win32gui.GetClassName(h) else None) is None,
            None)
        for h in cands:
            _, u = doc_of(h)
            if not u: continue
            if 'weixin://resourceid' in u or 'mp.weixin.qq.com' in u:
                _, pid = win32process.GetWindowThreadProcessId(h)
                self.pids.add(pid)
                if self.hwnd is None:
                    self.hwnd, self.win = h, auto.ControlFromHandle(h)
        return self.hwnd is not None

    def url(self):
        return doc_of(self.hwnd)[1] or ''

    def title(self):
        return (doc_of(self.hwnd)[0].Name or '').strip() if doc_of(self.hwnd)[0] else ''

    def on_profile(self):
        return PROFILE_MARK in self.url()

    def focus(self):
        h = self.hwnd
        try:
            if win32gui.IsIconic(h): win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(h)
            win32gui.SetForegroundWindow(h)
        except Exception: pass
        time.sleep(0.5)

    def rect(self):
        return win32gui.GetWindowRect(self.hwnd)

    # ---- 输入 ----
    def wheel_units(self, n):
        """批量滚轮; 微信不拦截 mouse_event 的 WHEEL"""
        L, T, R, B = self.rect()
        win32api.SetCursorPos(((L + R) // 2, (T + B) // 2)); time.sleep(0.04)
        step = -120 if n > 0 else 120
        n = abs(int(n))
        while n > 0:
            k = min(n, 25)
            for _ in range(k):
                win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, step, 0)
                time.sleep(0.015)
            n -= k
            time.sleep(0.12)

    def click(self, x, y):
        """PostMessage 到命中窗口(渲染窗口)"""
        x, y = int(x), int(y)
        h_at = win32gui.WindowFromPoint((x, y))
        if not h_at: return False, 'no-window'
        cls = win32gui.GetClassName(h_at)
        cx, cy = win32gui.ScreenToClient(h_at, (x, y))
        lp = win32api.MAKELONG(cx, cy)
        win32gui.PostMessage(h_at, win32con.WM_MOUSEMOVE, 0, lp); time.sleep(0.13)
        win32gui.PostMessage(h_at, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp); time.sleep(0.10)
        win32gui.PostMessage(h_at, win32con.WM_LBUTTONUP, 0, lp)
        return True, cls

    # ---- 列表 ----
    def articles(self):
        texts = []
        for d, c in walk(self.win):
            if _type(c) == 'TextControl': texts.append(c)
        res = []
        for j, c in enumerate(texts):
            nm = (c.Name or '').strip()
            if not READ_RE.match(nm): continue
            k = j - 1
            while k >= 0:
                t = (texts[k].Name or '').strip()
                if t:
                    res.append((t, texts[k])); break
                k -= 1
        return res

    def visible(self, c, margin=6):
        try: r = c.BoundingRectangle
        except Exception: return False, None
        if r is None or r.bottom - r.top < 1 or r.right - r.left < 1:
            return False, None
        L, T, R, B = self.rect()
        return (r.top >= T + margin and r.bottom <= B - margin), r

    # ---- 导航 ----
    def wait_url(self, pred, timeout=18.0, step=0.4):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred(self.url()): return time.time() - t0
            time.sleep(step)
        return None

    def goto_profile(self, timeout=14.0):
        """唯一可靠的回主页方式: 点文章页顶部公众号名片"""
        if self.on_profile(): return True
        t0 = time.time()
        while time.time() - t0 < timeout:
            for d, c in walk(self.win):
                if _type(c) != 'HyperlinkControl': continue
                try:
                    if (c.Name or '').strip() != ACCOUNT: continue
                except Exception:
                    continue
                try:
                    c.GetInvokePattern().Invoke()
                except Exception:
                    r = c.BoundingRectangle
                    if r and r.right > r.left:
                        self.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                if self.wait_url(lambda u: PROFILE_MARK in u, 10): return True
            time.sleep(0.5)
        return self.on_profile()

    def scroll_to_index(self, idx, budget=70):
        """把第 idx 项滚进视野. 假设当前在列表顶部(每次回主页后都是).
        策略: 目标已加载 -> 用它的矩形算精确位移; 未加载 -> 用已加载末项的矩形
        滚到「加载触发线」附近, 促使分页加载."""
        for step in range(budget):
            a = self.articles()
            if not a:
                time.sleep(0.5); continue
            L, T, R, B = self.rect()
            if len(a) > idx:
                c = a[idx][1]
                ok, r = self.visible(c)
                if ok: return r, a[idx][0]
                if r is not None and r.top > B:
                    mv = int((r.top - (B - 300)) / UNIT_PX)
                    self.wheel_units(min(max(mv, 1), 20))
                elif r is not None and r.bottom < T:
                    mv = int(((T + 300) - r.bottom) / UNIT_PX)
                    self.wheel_units(-min(max(mv, 2), 20))
                else:
                    # 矩形 0x0(未渲染), 向前推一屏
                    self.wheel_units(6)
            else:
                # 目标未加载: 把末项推到加载触发线
                last = a[-1][1]
                ok, r = self.visible(last)
                if r is not None and r.bottom > T:
                    mv = int((r.bottom - (B - 320)) / UNIT_PX)
                    self.wheel_units(min(max(mv, 2), 18))
                else:
                    self.wheel_units(12)
            time.sleep(0.42)
        return None, None

    def scroll_to_top(self, budget=40):
        """滚回列表顶部(用第 1 项的矩形判断, 拿不到就盲滚)"""
        for _ in range(budget):
            a = self.articles()
            if a:
                ok, r = self.visible(a[0][1])
                if ok and r.top < self.rect()[1] + 420:
                    return True
            self.wheel_units(-15); time.sleep(0.45)
        return False

    def load_all_titles(self, max_round=40):
        """滚到底把所有标题读出来(仅用于确定顺序)"""
        seen, order = defaultdict(int), []
        prev, stable = -1, 0
        for i in range(max_round):
            a = self.articles()
            r0 = None
            if a:
                try: r0 = a[0][1].BoundingRectangle.top
                except Exception: r0 = None
            if i % 4 == 0 or len(a) == prev:
                log('   round %d n=%d top0=%s fg=%s' % (i, len(a), r0, win32gui.GetForegroundWindow()))
            if len(a) == prev: stable += 1
            else: stable = 0
            if stable >= 5 and i >= 3: break
            prev = len(a)
            self.wheel_units(14); time.sleep(0.7)
        a = self.articles()
        for t, c in a:
            order.append((t, seen[t])); seen[t] += 1
        return order


# ---------------------------------------------------------------- 主流程
def main():
    try:
        log('=== harvest start %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
        b = Browser()
        if not b.find():
            log('!! 找不到微信内置浏览器窗口 (请先在微信里打开该号任意一篇文章)'); return
        log('browser hwnd=%s pid=%s rect=%s' % (b.hwnd, sorted(b.pids), b.rect()))
        b.focus()
        log('url = %r' % b.url()[:130])
        if not b.on_profile():
            log('>>> 回主页 ...')
            if not b.goto_profile():
                log('!! 无法进入公众号主页, 退出'); return
        log('on profile, url=%r' % b.url()[:110])

        # ---------------- 增量模式 ----------------
        if INCREMENTAL:
            known = load_known_mids()
            log('>>> 增量模式: 已知 %d 篇' % len(known))
            results = []
            if os.path.exists(OUT):
                try: results = json.load(open(OUT, encoding='utf-8'))
                except Exception: results = []
            n_ok = len(results); idx = 0; miss = 0
            while idx <= 90:
                if LIMIT and n_ok >= LIMIT:
                    log('  LIMIT reached'); break
                if miss >= 6:
                    log('  !! 连续失败过多, 中止'); break
                if not b.on_profile():
                    if not b.goto_profile():
                        log('  !! 回不了主页, 中止'); break
                time.sleep(0.7)
                r, got_title = b.scroll_to_index(idx)
                if r is None:
                    log('  列表到底 (idx=%d), 结束' % idx); break
                log('>>> [idx %d] %r y=%d' % (idx, got_title, r.top))
                ok, cls = b.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                if not ok:
                    log('    click 失败(%s)' % cls); miss += 1; idx += 1; continue
                dt = b.wait_url(lambda u: ARTICLE_MARK in u and 'index.html' not in u, 18)
                u1 = b.url()
                if dt is None or ARTICLE_MARK not in u1 or 'index.html' in u1:
                    log('    MISS url=%r' % u1[:100]); miss += 1; idx += 1; continue
                mm = re.search(r'[?&]mid=(\d+)', u1)
                mid = mm.group(1) if mm else None
                if mid and mid in known:
                    log('    命中已知文章 mid=%s -> 增量结束' % mid); break
                results.append({'idx': n_ok, 'title': got_title,
                                'page_title': b.title() or None, 'date': None, 'url': u1})
                n_ok += 1; miss = 0; idx += 1
                log('    OK (%.1fs) mid=%s' % (dt, mid))
                json.dump(results, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            json.dump(results, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            log('=== incremental collected %d ===' % len(results))
            return

        # ---------------- 全量模式 ----------------
        log('>>> 加载全部标题')
        order = b.load_all_titles()
        total = len(order)
        log('  total = %d' % total)
        log('  head: %s' % [t for t, _ in order[:4]])
        log('  tail: %s' % [t for t, _ in order[-4:]])
        if total == 0:
            log('!! 未解析到标题'); return
        b.scroll_to_top()

        # 断点续跑
        results = []
        if os.path.exists(OUT):
            try: results = json.load(open(OUT, encoding='utf-8'))
            except Exception: results = []
        have = {(r['title'], r.get('url', '')[:60]) for r in results}
        titles_done = Counter(r['title'] for r in results)
        counter = Counter()
        n_ok, miss = len(results), 0

        for idx, (title, nth) in enumerate(order):
            counter[title] += 1
            if counter[title] <= titles_done[title]:
                continue
            if LIMIT and n_ok >= LIMIT:
                log('  LIMIT reached'); break
            if miss >= 8:
                log('  !! 连续失败过多, 中止'); break

            # 每次都回主页 -> 列表在顶部
            if not b.on_profile():
                if not b.goto_profile():
                    log('  !! 回不了主页, 中止'); break
            time.sleep(0.8)

            r, got_title = b.scroll_to_index(idx)
            if r is None:
                log('  [%d/%d] %r 滚不到, skip' % (idx + 1, total, title))
                miss += 1; continue

            log('>>> [%d/%d] %r  y=%d' % (n_ok + 1, total, got_title, r.top))
            u0 = b.url()
            ok, cls = b.click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            if not ok:
                log('    click 失败(%s)' % cls); miss += 1; continue
            dt = b.wait_url(lambda u: ARTICLE_MARK in u and 'index.html' not in u, 18)
            u1 = b.url()
            pt = b.title()
            if dt is not None and ARTICLE_MARK in u1 and 'index.html' not in u1:
                results.append({'idx': n_ok, 'title': got_title,
                                'page_title': pt or None, 'date': None, 'url': u1})
                n_ok += 1; miss = 0
                log('    OK (%.1fs) %s' % (dt, u1[:100]))
            else:
                miss += 1
                log('    MISS url=%r' % u1[:100])
            json.dump(results, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

        json.dump(results, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        log('=== collected %d / %d ===' % (len(results), total))
    except Exception:
        log('FATAL\n' + traceback.format_exc())

if __name__ == '__main__':
    main()
