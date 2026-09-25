# 自动化执行记忆 — 无语子点点公众号文章同步 GitHub

任务 ID: 53df7580-4eb8-476d-b218-05682d267027
工作目录: C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi
仓库: https://github.com/tzc-code/wuyuzi-diandian (main)

## 执行历史

### 2026-09-22 22:00-22:04（首次记录）
- 微信进程：Weixin.exe 运行中（PID 31284）。
- **harvest_list.py 环节失败**：`找不到微信内置浏览器窗口 (请先在微信里打开该号任意一篇文章)`。
  - 原因：微信主窗口在运行，但没有任何 Chrome_WidgetWin 承载 mp.weixin.qq.com 页面的内置浏览器窗口。
    脚本自身不具备「打开文章窗口」的能力，必须由外部先开一篇该号文章才能进入 profile 主页。
  - 重试 1 次（共 2 次执行）结果一致，确认为持续性条件，非偶发。
- fetch_articles.py：正常，76/76 全部校验通过（imgs=0，无新增图片）。
- build_repo.py：正常，built=76 / skipped=0。
- git：仅有 README 时间戳变更 → commit `a420798`，push 成功，origin/main 一致。
- 结论：新增文章 0 篇；链路本身健康，唯一断点在 harvest（缺微信文章窗口）。

## ⚠️ 2026-09-23 更正（重要，勿再按旧结论诊断）
上面「失败原因 = 微信里没打开该号文章窗口」是**误判**。真正的根因（已实测确认）：
1. 微信 4.0 内置浏览器是**主窗口的子窗口**（`Chrome_WidgetWin_0`，渲染进程 `WeChatAppEx.exe`），
   而旧 `find()` 只 `EnumWindows` 顶层窗口 → **即使窗口存在也找不到**；
2. 微信**最小化到托盘时 WebView 不渲染**，UIA 里没有 DocumentControl → 必须先 `SW_RESTORE` 唤醒
   （`SW_SHOWNOACTIVATE` 无效），抓完还原最小化；
3. 必须按渲染进程 `WeChatAppEx.exe` 过滤，否则会误抓 Wind(`wmain.exe`) 的微信文章窗口。
已改 `tools/harvest_list.py`（commit `b60fc6d`）。完整记录见
`automations/d41bb672-e230-437f-95bb-098b4c550161/memory.md`。

## 关键经验
- 判定「微信未启动」只看 Weixin.exe；但**进程在 ≠ 可抓取**。真正前置条件是
  「微信里已打开过该号任意一篇文章」（内置浏览器窗口存在）。二者需分开判断。
- harvest 失败时脚本 rc=0，不会中断 sync 流程；因此不能用退出码判断成败，
  必须读 harvest.log 末行。sync.py 末尾的 git 步骤仍会照常执行（只改 README 时间戳）。
- 失败时不要超过 2 次重试（本任务规程上限）。
- 无新增文章的正常输出是「无变更, 跳过 commit/push」；若出现了 commit 但只有
  README.md 1 项变更，说明 harvest 静默失败了，不是真有新内容。

### 2026-09-23 22:00（修复后首次无人干预夜间实跑，成功）
- 微信运行中；harvest **成功**（唤醒最小化微信 → 命中 WebView pid=5428 → 已在 profile 页）。
- 增量模式已知 76 篇，idx0 即最新一篇 mid=2247484330（2026-09-18「石油和粮食」）→ 增量结束。
- fetch 76/76 OK、build 76/76、commit `8b8eefa`（1 file changed: README.md）、**push 成功**，origin/main 一致。
- 结果：**新增 0 篇**（内容侧最新仍是 2026-09-18 23:36），正常。

### ⚠️ 上面「README 单变更 = harvest 静默失败」的判据已作废
- harvest 修好后，0 新文章的正常跑批也会因 README 时间戳重写而 commit+push（只有 README 1 项变更）。
- 正确自检方式：读 `harvest.log` 末几行，确认有 `browser hwnd=...` / `on profile` / `增量模式: 已知 N 篇`，
  且 `[idx 0]` 的标题 == `state/index.json` **首键**对应标题（index.json 是 mid→文章 的 dict，首键即最新一篇）。
- 出现 `命中已知文章 mid=... -> 增量结束` 于 idx 0 = 无新文章的正确路径，不是失败。
- 只有 harvest.log 出现 `!! 找不到该号的 WebView` 或 `!! 无法进入公众号主页` 才算真失败。

### 2026-09-24 22:00-22:06（harvest 失败，非登录态问题的新变体）
- 微信运行中（PID 31284）；WeChatAppEx.exe **有 3 个进程在跑**，但仍抓不到。
- harvest 失败：`扫描 25s 未发现 WeChatAppEx 渲染的页面` + `登录态: 未登录(或仍在登录窗口)`。
  - 判据来源：脚本 `is_logged_in()` = 主窗口能否 UIA 到 ToolBarControl「导航」。这次判为 False。
  - 即「有渲染进程」≠「有可抓的 mp.weixin.qq.com 页面」；主窗口唤醒后 UIA 仍无导航栏/无 DocumentControl。
- 重试 1 次（共 2 次，达上限）结果一致 → 持续性条件。**本次新增 0 篇**。
- 09-24 新文「。。」是当日 13:55 手工排障那轮入库（`b2681aa`），本轮未新增。
- git：`4eb4948` / `a62be21` 两次 README 时间戳提交均 push 成功，`HEAD == origin/main == a62be21`。
- 新增经验（本变体）：诊断时不要只看 WeChatAppEx 是否存在；还要看 harvest.log 里
  `命中 WebView` 一行是否出现。仅出现 `扫描 25s 未发现` = 无承载目标号的页面（或微信停在登录/未渲染态）。
  恢复靠人工：微信里打开该号任意一篇文章即可。

