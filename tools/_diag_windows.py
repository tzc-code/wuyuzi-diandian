# -*- coding: utf-8 -*-
"""枚举 Weixin.exe 全部顶层窗口 + WeChatAppEx 进程，交叉确认抓取前置条件。"""
import ctypes, subprocess
from ctypes import wintypes

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
GW_OWNER = 4

def pid_of(h):
    p = wintypes.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value

def proc_name(pid):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10).stdout
        return out.split(",")[0].strip('"') if out.strip() else "?"
    except Exception:
        return "?"

rows = []
def cb(h, l):
    p = pid_of(h)
    n = proc_name(p)
    if n.lower() in ("weixin.exe", "wechatappex.exe"):
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        rows.append((n, p, h, (r.left, r.top, r.right - r.left, r.bottom - r.top),
                     user32.IsWindowVisible(h), user32.IsIconic(h),
                     hex(GW_OWNER & 0) or "", cls.value, buf.value))
    return True

EnumWindows(WNDENUMPROC(cb), 0)
print(f"{'exe':<16}{'pid':<8}{'hwnd':<12}{'rect(l,t,w,h)':<26}{'vis':<6}{'iconic':<8}{'class':<30}title")
for n, p, h, rect, vis, ico, _, cls, title in rows:
    print(f"{n:<16}{p:<8}{h:<12}{str(rect):<26}{int(vis):<6}{int(ico):<8}{cls:<30}{title}")

print("\n--- WeChatAppEx.exe / Weixin.exe 进程 ---")
for exe in ("Weixin.exe", "WeChatAppEx.exe"):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
    lines = [l for l in out.splitlines() if l.strip()]
    print(f"{exe}: {len(lines)} 个")
