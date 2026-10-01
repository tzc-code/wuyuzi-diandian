# -*- coding: utf-8 -*-
"""确诊：读微信主窗口 UIA 文本，判断是否掉登录。"""
import sys
sys.path.insert(0, r"C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi\tools")
import harvest_list as H

h = H.find_wechat_main()
print("hwnd =", h)
if h:
    try:
        import uiautomation as auto
        ctrl = auto.ControlFromHandle(h)
        acc = H.walk(ctrl)
        texts = []
        for c in acc:
            t = (H._type(c) or "")
            n = getattr(c, "Name", "") or ""
            v = ""
            try:
                v = H._doc_value(c)
            except Exception:
                pass
            if n.strip():
                texts.append(n.strip())
            if v:
                texts.append("[V]" + v.strip())
        # 去重保序
        seen, out = set(), []
        for t in texts:
            if t not in seen:
                seen.add(t)
                out.append(t)
        print("--- UIA 文本 ---")
        for t in out[:60]:
            print(t[:200])
        joined = " | ".join(out)
        for kw in ("你已退出微信", "进入微信", "切换账号", "我知道了", "扫码", "登录"):
            if kw in joined:
                print(f"!! 命中关键词: {kw}")
    except Exception as e:
        print("UIA ERR:", repr(e))
