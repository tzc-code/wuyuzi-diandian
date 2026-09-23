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

## 关键经验
- 判定「微信未启动」只看 Weixin.exe；但**进程在 ≠ 可抓取**。真正前置条件是
  「微信里已打开过该号任意一篇文章」（内置浏览器窗口存在）。二者需分开判断。
- harvest 失败时脚本 rc=0，不会中断 sync 流程；因此不能用退出码判断成败，
  必须读 harvest.log 末行。sync.py 末尾的 git 步骤仍会照常执行（只改 README 时间戳）。
- 失败时不要超过 2 次重试（本任务规程上限）。
- 无新增文章的正常输出是「无变更, 跳过 commit/push」；若出现了 commit 但只有
  README.md 1 项变更，说明 harvest 静默失败了，不是真有新内容。
