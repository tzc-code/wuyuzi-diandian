# -*- coding: utf-8 -*-
"""唤醒主窗口 → 等待渲染 → 读 UIA 文本 → 还原最小化。用于区分「掉登录」与「面板没开」。"""
import sys, time, ctypes
sys.path.insert(0, r"C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi\tools")
import harvest_list as H

h = H.find_wechat_main()
print("hwnd =", h)
r = ctypes.wintypes.RECT()
ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
print("before wake rect =", (r.left, r.top, r.right, r.bottom),
      "iconic =", ctypes.windll.user32.IsIconic(h))

H.wake_main(h, settle=3.0)
time.sleep(3)
ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
print("after  wake rect =", (r.left, r.top, r.right, r.bottom),
      "iconic =", ctypes.windll.user32.IsIconic(h))
try:
    print("is_logged_in =", H.is_logged_in(h))
except Exception as e:
    print("is_logged_in ERR:", repr(e))

try:
    import uiautomation as auto
    ctrl = auto.ControlFromHandle(h)
    acc = H.walk(ctrl)
    out, seen = [], set()
    for c in acc:
        n = (getattr(c, "Name", "") or "").strip()
        v = ""
        try:
            v = (H._doc_value(c) or "").strip()
        except Exception:
            pass
        for t in (n, "[V]" + v if v else ""):
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    print("--- UIA 文本 (%d) ---" % len(out))
    for t in out[:80]:
        print(t[:180])
    joined = " | ".join(out)
    hits = [k for k in ("你已退出微信", "进入微信", "切换账号", "我知道了", "扫码登录", "登录", "微信") if k in joined]
    print("!! 命中关键词:", hits)
except Exception as e:
    print("UIA ERR:", repr(e))

docs = H.scan_wechat_docs()
print("scan_wechat_docs() =", len(docs))
for d in docs:
    u = d.get("url") if isinstance(d, dict) else str(d)
    print("   ", u)

ctypes.windll.user32.ShowWindow(h, 6)
print("已还原最小化")
