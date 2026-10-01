# -*- coding: utf-8 -*-
"""跑前预判：不跑全链路，10 秒内判断本次 sync 是否会失败。
输出：主窗口 hwnd / rect / is_logged_in / 全桌面 WeChatAppEx 页面的 url+__biz。
"""
import sys, ctypes
sys.path.insert(0, r"C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi\tools")
import harvest_list as H

TARGET_BIZ = "MzYzMTIxMjk4NQ=="  # 无语子点点

h = H.find_wechat_main()
print("find_wechat_main() =", h)
if h:
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
    print("rect =", (r.left, r.top, r.right, r.bottom))
    print("iconic =", ctypes.windll.user32.IsIconic(h))
    try:
        print("is_logged_in =", H.is_logged_in(h))
    except Exception as e:
        print("is_logged_in ERR:", e)

print("--- wake + scan ---")
try:
    if h:
        H.wake_main(h)
    docs = H.scan_wechat_docs()
    print("scan_wechat_docs() count =", len(docs))
    for i, d in enumerate(docs):
        u = d.get("url") if isinstance(d, dict) else str(d)
        print(f"[{i}] {u}")
        if u and "__biz=" in u:
            biz = u.split("__biz=")[1].split("&")[0]
            print("     biz =", biz, "MATCH" if biz == TARGET_BIZ else "OTHER-ACCOUNT")
        if u and "mp.weixin.qq.com/s" in u:
            print("     >>> 已是文章页")
except Exception as e:
    print("scan ERR:", repr(e))

# 还原最小化
if h:
    try:
        ctypes.windll.user32.ShowWindow(h, 6)  # SW_MINIMIZE
        print("已还原主窗口为最小化")
    except Exception as e:
        print("restore ERR:", e)
