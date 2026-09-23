# 自动化执行记忆 — 无语子点点公众号文章同步 GitHub

任务 ID: d41bb672-e230-437f-95bb-098b4c550161
工作目录: C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi
仓库: https://github.com/tzc-code/wuyuzi-diandian (main)
（同一任务的早期记录见 automations/53df7580-4eb8-476d-b218-05682d267027/memory.md）

## 执行历史

### 2026-09-23 08:00-08:04
- Weixin.exe 运行中（PID 31284，标题「微信X」）→ 未跳过。
- **harvest_list.py 失败**：`找不到微信内置浏览器窗口 (请先在微信里打开该号任意一篇文章)`，08:02:26。
- 独立探测复核：枚举全部 Chrome_WidgetWin 窗口共 56 个，**无任何属于 PID 31284 的窗口**，
  也无 mp.weixin.qq.com / weixin://resourceid 文档 → 前置条件确凿缺失，非偶发。
  未重试（失败为瞬时返回，条件未变，重试无意义；规程上限 2 次）。
- fetch_articles.py：76/76 全 OK（imgs=0）。build_repo.py：built=76 / skipped=0，index 未变。
- git：commit `1dabaac`（3 files changed，全部是 .workbuddy/memory 文件 + README 时间戳），push 成功，origin/main 一致。
- 结论：新增文章 0 篇。

### 2026-09-22 22:00-22:04
- 同样在 harvest 环节失败（缺微信文章窗口），重试 1 次结果一致。commit `a420798`，push 成功。新增 0 篇。

## 关键经验
- 判定「微信未启动」只看 Weixin.exe；但**进程在 ≠ 可抓取**。真正前置条件是
  「微信里已打开过该号任意一篇文章」（内置浏览器窗口存在）。二者需分开判断。
- harvest 失败时脚本 rc=0，不会中断 sync 流程；不能用退出码判断成败，**必须读 harvest.log 末行**。
  sync.py 末尾的 git 步骤仍会照常执行（只改 README 时间戳 + memory 文件）。
- 无新增文章的正常输出是「无变更, 跳过 commit/push」；若出现 commit 但只有 README/memory 变更，
  说明 harvest 静默失败了，不是真有新内容。
- 快速判别命令（不跑全流程）：枚举 Chrome_WidgetWin 窗口并比对 PID 31284 与文档 URL。
- 连续两天同一断点 → 属结构性缺陷，需大王决策：① 同步前保持一篇该号文章窗口打开；
  或 ② 改造 harvest_list.py 使其自身能打开文章窗口（当前脚本无此能力）。
