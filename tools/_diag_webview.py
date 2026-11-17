import sys, win32gui, win32process
import uiautomation as auto

TARGET_BIZ = "MzYzMTIxMjk4NQ=="

def walk(ctrl, depth=0, out=None):
    if out is None:
        out = []
    try:
        for c in ctrl.GetChildren():
            out.append((depth, c))
            if depth < 12:
                walk(c, depth + 1, out)
    except Exception:
        pass
    return out

root = auto.GetRootControl()
docs = []
def scan(ctrl, d=0):
    try:
        for c in ctrl.GetChildren():
            try:
                if c.ControlTypeName == "DocumentControl":
                    docs.append(c)
            except Exception:
                pass
            if d < 12:
                scan(c, d + 1)
    except Exception:
        pass
scan(root)

print("DOC COUNT:", len(docs))
for d in docs:
    try:
        val = d.GetValuePattern().Value if d.GetValuePattern() else ""
    except Exception:
        val = ""
    try:
        name = d.Name
    except Exception:
        name = "?"
    print("--- name=%r value=%r" % (name[:60], str(val)[:220]))

print("=== Weixin top-level windows ===")
def cb(hwnd, _):
    try:
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if "QWindow" in cls or "QWidget" in cls or "WeChat" in cls or "Chrome" in cls:
                r = win32gui.GetWindowRect(hwnd)
                print("hwnd=%s pid=%s cls=%s title=%r rect=%s size=%sx%s" % (
                    hwnd, pid, cls, title[:30], r, r[2]-r[0], r[3]-r[1]))
    except Exception:
        pass
win32gui.EnumWindows(cb, None)

print("=== biz match ===")
hits = 0
for d in docs:
    try:
        val = d.GetValuePattern().Value if d.GetValuePattern() else ""
    except Exception:
        val = ""
    if TARGET_BIZ in str(val):
        hits += 1
        print("TARGET HIT:", str(val)[:200])
print("target_biz_hits =", hits)
