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
import os, sys, time, json, re, traceback, ctypes
from collections import defaultdict, Counter
import win32gui, win32con, win32api, win32process
import uiautomation as auto

TOOLS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TOOLS)              # 仓库根
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

# ================================================================ 微信内置浏览器定位
# 2026-09-23 重写。原实现(只 EnumWindows 顶层 + 类名 Chrome_WidgetWin)在微信 4.0
# 上必然失败, 实测三条:
#   1) 内置浏览器(WebView)是**主窗口的子窗口**(WS_CHILD), EnumWindows 看不见;
#   2) 主窗口最小化到托盘时 WebView 不渲染, UIA 树里根本没有 DocumentControl
#      -> 必须先唤醒主窗口(SW_RESTORE, 结束时还原);
#   3) 同机还有别的"微信文章"窗口(如 Wind 的 wmain.exe), 必须按渲染进程
#      WeChatAppEx.exe 过滤, 否则会误抓别人家的页面。
WEIXIN_BROWSER_EXE = 'wechatappex.exe'      # RadiumWMPF 运行时, 微信内置浏览器专属
MAIN_TITLES = ('微信', 'Weixin')


def _exe_name(pid):
    """进程可执行文件名。注意: create_unicode_buffer 在 ctypes 下, 不在 ctypes.wintypes"""
    try:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return ''
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_uint32(1024)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        ctypes.windll.kernel32.CloseHandle(h)
        return os.path.basename(buf.value) if ok else ''
    except Exception:
        return ''


def _doc_value(ctrl):
    try: return ctrl.GetValuePattern().Value or ''
    except Exception: return ''


def _nearest_hwnd(ctrl, maxup=16):
    """沿父链找到第一个带真实窗口句柄的控件 = WebView 渲染窗口"""
    p = ctrl
    for _ in range(maxup):
        try: p = p.GetParentControl()
        except Exception: return 0
        if p is None: return 0
        try:
            if p.NativeWindowHandle: return p.NativeWindowHandle
        except Exception: pass
    return 0


def find_wechat_main():
    """定位微信主窗口。用 GetWindowPlacement 的还原尺寸评分, 最小化也不会误判小弹窗"""
    best = [None, 0]
    def cb(h, _):
        try:
            if win32gui.GetWindowText(h) not in MAIN_TITLES: return
            if not win32gui.GetClassName(h).startswith('Qt'): return
            r = win32gui.GetWindowPlacement(h)[4]
            area = (r[2] - r[0]) * (r[3] - r[1])
            if area > best[1]: best[0], best[1] = h, area
        except Exception: pass
    win32gui.EnumWindows(cb, None)
    return best[0]


def wake_main(h, settle=2.0):
    """托盘/最小化 -> 唤醒。
    坑: SW_SHOWNOACTIVATE 对最小化窗口**不生效**, 窗口仍是 iconic, WebView 不渲染,
    UIA 树里就没有 DocumentControl。必须用 SW_RESTORE。"""
    if not h: return False
    try:
        if win32gui.IsIconic(h) or not win32gui.IsWindowVisible(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            time.sleep(settle)
            return True
    except Exception: pass
    return False


def scan_wechat_docs(maxdepth=15):
    """全桌面 UIA 扫描, 返回 [(doc, url, hwnd)]; 只认 WeChatAppEx.exe 渲染的页面"""
    root = auto.GetRootControl()
    stack, out = [(root, 0)], []
    while stack:
        x, d = stack.pop()
        if d > maxdepth: continue
        try: kids = x.GetChildren()
        except Exception: continue
        for k in kids:
            try:
                if k.ControlTypeName == 'DocumentControl':
                    u = _doc_value(k)
                    if u and ('weixin://resourceid' in u or 'mp.weixin.qq.com' in u):
                        hw = _nearest_hwnd(k)
                        if hw:
                            pid = win32process.GetWindowThreadProcessId(hw)[1]
                            if _exe_name(pid).lower() == WEIXIN_BROWSER_EXE:
                                out.append((k, u, hw))
            except Exception: pass
            stack.append((k, d + 1))
    return out


class Browser:
    """定位并持有微信内置浏览器窗口"""
    def __init__(self):
        self.hwnd = None
        self.win = None
        self.doc = None
        self.main = None
        self.woke = False
        self.pids = set()

    def find(self):
        # 1) 唤醒微信主窗口(最小化时 WebView 不渲染, UIA 里没有 DocumentControl)
        self.main = find_wechat_main()
        self.woke = bool(wake_main(self.main))
        if self.woke:
            log('已唤醒微信主窗口 hwnd=%s (SW_RESTORE, 结束时会还原)' % self.main)
        # 2) 全桌面扫 DocumentControl(子窗口内的 WebView 也能命中)
        #    唤醒后 WebView 需要几秒重建渲染, 所以轮询等待
        hits, t0 = [], time.time()
        while time.time() - t0 < 25:
            hits = scan_wechat_docs()
            if hits: break
            time.sleep(1.5)
        if not hits:
            log('扫描 25s 未发现 WeChatAppEx 渲染的页面')
        # 主页优先, 其次是文章页(文章页能点名片回主页)
        hits.sort(key=lambda t: 0 if PROFILE_MARK in t[1] else 1)
        if hits:
            self.doc, u, self.hwnd = hits[0]
            self.win = self.doc
            _, pid = win32process.GetWindowThreadProcessId(self.hwnd)
            self.pids.add(pid)
            log('命中 WebView: hwnd=%s pid=%s cls=%s' %
                (self.hwnd, pid, win32gui.GetClassName(self.hwnd)))
            log('  url = %r' % u[:140])
            return True
        # 3) 兼容: 独立窗口形态(顶层 Chrome_WidgetWin)
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
                if _exe_name(pid).lower() != WEIXIN_BROWSER_EXE: continue
                self.hwnd, self.win = h, auto.ControlFromHandle(h)
                self.pids.add(pid)
                log('命中独立窗口 hwnd=%s pid=%s' % (h, pid))
                return True
        return False

    def url(self):
        u = _doc_value(self.doc) if self.doc is not None else ''
        if u: return u
        try:
            return doc_of(self.hwnd)[1] or ''
        except Exception:
            return ''

    def title(self):
        try:
            if self.doc is not None:
                return (self.doc.Name or '').strip()
        except Exception: pass
        try:
            c = doc_of(self.hwnd)[0]
            return (c.Name or '').strip() if c else ''
        except Exception:
            return ''

    def on_profile(self):
        return PROFILE_MARK in self.url()

    def focus(self):
        """把微信主窗口置前(WebView 才稳定吃滚动/点击)。self.hwnd 是子窗口,
        SetForegroundWindow 对它无效, 要用主窗口"""
        h = self.main
        if not h:
            try: h = win32gui.GetAncestor(self.hwnd, win32con.GA_ROOT)
            except Exception: h = self.hwnd
        try:
            if win32gui.IsIconic(h): win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(h)
            win32gui.SetForegroundWindow(h)
        except Exception: pass
        time.sleep(0.6)

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

    def click(self, x, y, method='real'):
        """点击 WebView。三种投递方式:
          real  = 真实鼠标事件(SetCursorPos + mouse_event)。滚轮本来就是真实事件且有效
          host  = PostMessage 直投渲染窗口 Chrome_RenderWidgetHostHWND
          point = PostMessage 投给 WindowFromPoint 命中的窗口(旧逻辑)
        2026-09-23: 内嵌 WebView 上 WindowFromPoint 常命中 Chromium 的
        Intermediate D3D Window, 消息会被丢弃 -> 旧逻辑必然 MISS, 故以 real 优先。"""
        x, y = int(x), int(y)
        if method == 'real':
            win32api.SetCursorPos((x, y)); time.sleep(0.25)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0); time.sleep(0.09)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            return True, 'real'
        target = self.hwnd if method == 'host' else win32gui.WindowFromPoint((x, y))
        if not target: return False, 'no-window'
        cx, cy = win32gui.ScreenToClient(target, (x, y))
        lp = win32api.MAKELONG(cx, cy)
        win32gui.PostMessage(target, win32con.WM_MOUSEMOVE, 0, lp); time.sleep(0.13)
        win32gui.PostMessage(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lp); time.sleep(0.10)
        win32gui.PostMessage(target, win32con.WM_LBUTTONUP, 0, lp)
        return True, method

    def _rebind(self, mark):
        """重扫所有 WeChatAppEx 页面, 发现含 mark 的页面就把 self 绑过去。
        实测: 微信**每跳一次页就新建一个 WebView 实例**(hwnd 每次都不同),
        旧 doc 的 URL 永远不变 -> 任何「跳转校验」都必须靠重扫, 否则必然误判。"""
        for d, u, h in scan_wechat_docs():
            if mark in u:
                if h != self.hwnd:
                    log('    (重绑到新 WebView 实例 hwnd=%s)' % h)
                self.doc, self.hwnd, self.win = d, h, d
                return u
        return ''

    def wait_page(self, mark, timeout=18.0, step=1.0):
        """等待页面切到含 mark 的 URL(原地导航 或 新实例都算)"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            u = self.url()
            if mark in u: return u
            u = self._rebind(mark)
            if u: return u
            time.sleep(step)
        return ''

    def open_item(self, x, y, timeout=10.0):
        """点开一篇列表文章。三种投递方式依次试, 用「是否出现文章页」验证
        (含新实例重扫)。返回 (ok, 生效方式, url)"""
        for method in ('real', 'host', 'point'):
            self.click(x, y, method)
            u = self.wait_page(ARTICLE_MARK, timeout)
            if u and 'index.html' not in u:
                return True, method, u
        return False, 'all-failed', self.url()

    # ---- 列表 ----
    def articles(self):
        texts = []
        for d, c in walk(self.win, maxdepth=30):
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

    def goto_profile(self, timeout=20.0):
        """唯一可靠的回主页方式: 点文章页顶部的公众号名片。
        2026-09-23: 名片在 UIA 里不一定暴露成 HyperlinkControl(实测文章页只把
        '全部/贴图/文章' 暴露成 Hyperlink, 账号名只是 TextControl), 所以两类都收。"""
        if self.on_profile(): return True
        t0 = time.time()
        while time.time() - t0 < timeout:
            cands = []
            for d, c in walk(self.win, maxdepth=30):
                if _type(c) not in ('HyperlinkControl', 'TextControl'): continue
                try:
                    if ACCOUNT not in (c.Name or '').strip(): continue
                    r = c.BoundingRectangle
                    if r and r.right > r.left and r.bottom > r.top:
                        cands.append((r.top, c))
                except Exception:
                    continue
            for _, c in sorted(cands, key=lambda t: t[0]):
                try:
                    c.GetInvokePattern().Invoke()
                except Exception:
                    r = c.BoundingRectangle
                    self.click((r.left + r.right) // 2, (r.top + r.bottom) // 2, 'real')
                # 回主页同样是新实例 -> 用重扫式等待
                if self.wait_page(PROFILE_MARK, 18): return True
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
    b = None
    try:
        log('=== harvest start %s ===' % time.strftime('%Y-%m-%d %H:%M:%S'))
        b = Browser()
        if not b.find():
            log('!! 找不到微信内置浏览器 WebView。前置条件: Weixin.exe 已登录, '
                '且本机打开过该号主页或任意一篇文章(WebView 才会被创建)')
            return
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
                t_click = time.time()
                ok, how, u1 = b.open_item((r.left + r.right) // 2, (r.top + r.bottom) // 2)
                dt = time.time() - t_click
                if not ok:
                    log('    MISS(real/host/point 三种点击均无效) url=%r' % u1[:100])
                    miss += 1; idx += 1; continue
                log('    click生效方式 = %s' % how)
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
            t_click = time.time()
            ok, how, u1 = b.open_item((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            dt = time.time() - t_click
            pt = b.title()
            if ok:
                log('    click生效方式 = %s' % how)
            if ok and ARTICLE_MARK in u1 and 'index.html' not in u1:
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
    finally:
        # 若本次是我们唤醒的, 还原为最小化(不改变大王原来的桌面状态)
        try:
            if b is not None and getattr(b, 'woke', False) and b.main:
                win32gui.ShowWindow(b.main, win32con.SW_MINIMIZE)
                log('已把微信主窗口还原为最小化')
        except Exception:
            pass

if __name__ == '__main__':
    main()
