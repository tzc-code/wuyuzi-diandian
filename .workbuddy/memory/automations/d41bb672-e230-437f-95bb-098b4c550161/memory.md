# 自动化执行记忆 — 无语子点点公众号文章同步 GitHub

任务 ID: d41bb672-e230-437f-95bb-098b4c550161
工作目录: C:\Users\zhico\WorkBuddy\2026-09-21-14-58-24\wuyuzi
仓库: https://github.com/tzc-code/wuyuzi-diandian (main)
（同一任务的早期记录见 automations/53df7580-4eb8-476d-b218-05682d267027/memory.md）

## 执行历史

### 2026-09-24 13:36-14:00（大王报「已更新新文章」→ 手工排障 → 成功归档 1 篇）
- **新增 1 篇**：`。。` — 2026-09-24 12:59（副标题「房地产回暖不等于公司盈利，消费医药或成新热点」）。
  最终 commit `b2681aa`（+ `3eccfe6` 修 README 篇数），push 成功，`HEAD == origin/main == 3eccfe6`。
- 第一次 sync（13:36）harvest 失败：日志 `MISS(real/host/point 三种点击均无效)` + 「列表到底 (idx=1)」。
  根因不是点击技术，而是 **`find()` 命中了 2026-09-23 11:43 打开的陈旧 profile 页**（72 篇，不含新文）。
  当时微信里同时存在两个 profile 实例：旧的（72 篇，屏幕内）+ 新的（73 篇，`localOpenTime`=今天 13:36，屏幕外）。
- 13:42 重跑：命中了**新页面**且 idx0 标题正确为 `。。`，但取回 mid=`2247484330`（=旧文「石油和粮食」）
  → 被判「已知」→ 0 新增。根因 = **`_rebind()` 命中了仍开着的旧文章页**（模式 B）。
- 手工定位新文 mid=**2247484333**：因全屏的 WorkBuddy 窗口盖住微信页面窗口，`real` 合成点击静默失效，
  改用 **`host`（PostMessage 直投 `Chrome_RenderWidgetHostHWND`）一次点中**。
- 13:48 带 `| head -12` 跑 sync（**这是我犯的错**：管道提前关闭 → BrokenPipe → build 之后、commit 之前中断）；
  同时 harvest 出现**模式 C 重复收集**：`incremental collected 78`，两条都是 mid=2247484333（标题错位）。
- 收尾：从备份恢复 `articles.json` 76 条 + 追加 1 条正确记录（标题 `。。`、完整 URL 含 chksm）→ 77 条去重；
  重跑 `build_repo.py`（77 篇，README 篇数由 78 修正为 77）→ commit + push。
- `articles.json` / `store/` 均在 `.gitignore` 中（仓库只收 `articles/*.md`、`README.md`、`state/index.json`）。
- 已把这 3 个失败模式写进 skill `wechat-mp-archive-github` 第 9 节。

### 2026-09-24 08:00-08:02（全链路正常，0 新增）
- Weixin.exe 运行中（PID 31284）→ 未跳过。
- harvest **成功**：唤醒主窗口 → 命中 WebView hwnd=2033582(pid 5428) → 已在 profile 主页
  → 增量为 0 → 点首条「石油和粮食」→ 重绑 hwnd=2888510 → 命中已知 mid=2247484330 → 增量结束 ✓
  （耗时 11s，无失败、无重试）
- fetch 76/76 全 OK（92s）；build 76 篇。
- 0 新文章 → commit `9b28b7d`（3 files：README 仅同步时间戳 + 2 个 memory 文件），push 成功，
  `HEAD == origin/main == 9b28b7da`。
- 注意：**只有在 harvest.log 显示「命中已知文章 mid → 增量结束」时**，README-only 的 commit 才算正常；
  若 harvest.log 无此链而只有 README 变更，才是静默失败。

### 2026-09-23 09:18-09:35（根因定位 + 修复）
- **07:59 结论修正**：08:00 那次判定的「前置条件缺失」是**误判**。真正根因是脚本找窗口的方式失效——
  微信 4.0 的内置浏览器**是主窗口的子窗口**（`Chrome_WidgetWin_0` / `Chrome_RenderWidgetHostHWND`，
  渲染进程 `WeChatAppEx.exe`，PID ≠ 微信主进程），而原 `find()` 只 `EnumWindows` **顶层**窗口。
- 另外两条致命细节：
  1. **主窗口最小化到托盘时 WebView 不渲染**，UIA 树里没有 `DocumentControl` → 必须先唤醒主窗口。
     坑：`SW_SHOWNOACTIVATE` 对最小化窗口**不生效**（窗口仍 iconic），必须 `SW_RESTORE`；脚本结束时还原为最小化。
  2. 唤醒后 WebView 需数秒重建渲染 → 必须轮询等待（脚本上限 25s）。
  3. 同机存在**别家客户端的微信文章窗口**（Wind 的 `wmain.exe`）→ 必须按渲染进程 `WeChatAppEx.exe` 过滤。
- 另修：内嵌 WebView 上 `WindowFromPoint` 常命中 Chromium 的 `Intermediate D3D Window`，`PostMessage` 被丢弃
  → 旧 click 必然 MISS。改为 real(真实鼠标) → host(直投渲染窗口) → point 三策略 + 页面校验。
  并放宽 `goto_profile`（名片在文章页不一定暴露成 HyperlinkControl，账号名只是 TextControl）。
- **第 6 个坑（最隐蔽）**：微信**每跳一次页就新建一个 WebView 实例**（hwnd 每次都不同，
  实测 8851090 → 18418420 → 24511464 → 86573558）。因此**旧 doc 的 URL 永远不变**，
  所有「跳转是否成功」的校验都必须**重扫全部 WeChatAppEx 页面并重绑**（`_rebind()` / `wait_page()`），
  否则会把成功的点击误判成失败。这一条是最后卡住的点。
- **改后全链路实测通过**（最小化状态起测，2026-09-23 09:40:55）：
  唤醒 → 命中 WebView → 读 profile URL → `articles()` 解析列表（10 项，行距 331px，最新「石油和粮食」y=818）
  → 点击（生效方式 real/host 都出现过）→ 重绑新实例 → 取到 `mid=2247484330`（已知）→ **增量结束** ✓
- 结论：**今天确实没有新文章**（最新仍是 2026-09-18 的「石油和粮食」，已在库里），0 新增是正确结果而非失败。
- 09:42 复跑 sync：harvest 9s / fetch 76 篇 92s / build 76 → commit `5fd2844`（含脚本修复）已 push，origin/main 一致。

### 2026-09-23 08:00-08:04
- Weixin.exe 运行中（PID 31284）→ 未跳过。harvest 失败（报「找不到微信内置浏览器窗口」），0 新文章。
- commit `1dabaac` 已 push，origin/main 一致。

### 2026-09-22 22:00-22:04
- 同样在 harvest 环节失败，重试 1 次结果一致。commit `a420798`，push 成功。新增 0 篇。

## 已实测封死「不依赖微信」的所有路径（2026-09-23）
结论：**列表发现必须持有微信会话凭据，没有免登录的第二条路。**
| 路径 | 结果 |
|---|---|
| 无会话 `profile_ext?action=home` | ❌ 返回「验证」页 |
| `action=getmsg` + 旧 key | ❌ `ret:-3 no session` |
| 裸 HTTP 取单篇正文 | ✅ 可行 |
| 去掉 `chksm` | ❌ 17KB 空壳页（chksm 硬门槛，无法本地推算 → mid 递增探测不可行） |
| 移动 UA(`MicroMessenger`) + 完整参数 | ❌ 「请在微信客户端打开链接」 |
| 合集 `appmsgalbum` / `album_id` | ❌ 该号未开合集（只在 JS 模板里出现） |
| 搜狗微信 | ❌ 「暂无与该号相关的官方认证订阅号」 |
| 普通浏览器(无微信 cookie) | ❌ 同 profile_ext |

## 关键经验
- 判定「微信未启动」只看 Weixin.exe；但**进程在 ≠ 可抓取**。真正前置条件是
  **微信里该号的 WebView(主页或文章页)存在**。三者分开判断。
- harvest 失败时脚本 rc=0，不中断 sync；不能用退出码判断成败，**必须读 harvest.log 末行**。
- 无新增文章的**正常**输出是「无变更, 跳过 commit/push」；若出现 commit 但只有 README/memory 变更，
  说明 harvest 静默失败了。
- 微信 4.0 = Qt 自绘主界面（UIA 只暴露导航栏/标题栏按钮，**聊天列表与搜索框完全不可读**），
  但**内置 WebView 的 DOM 完全可读**（能拿 URL + 全部文本节点，含 阅读N 赞N）。
- 快速判别：全桌面扫 `DocumentControl`，看 Value 是否含 `SubscriptionProfile/profile.html` / `mp.weixin.qq.com/s`。
- 残留依赖：该号 WebView 面板必须存在；若微信重启后面板丢失，需人工打开一次（脚本会明确报错）。
  要彻底无人值守，下一步需加「截图 + OCR 视觉驱动点开公众号」（UIA 做不到）。

## 2026-09-23 11:45 追加：降低微信依赖 + 端到端验证收官
- 新增「微信没开就自己拉起来」：`weixin_exe()` / `launch_weixin()`（`os.startfile` 完全脱离本进程）/ `is_logged_in()`。
  找不到主窗口 → 启动 Weixin.exe → 等最多 90s；抓不到 WebView 时日志**区分「未登录」与「面板没开」**。
  可用 `WYZ_WEIXIN_EXE` 指定路径、`WYZ_DRY_LAUNCH=1` 空跑。
  **边界：脚本能启动微信但没法登录（需扫码）。**
- **全链路端到端验证通过**（11:43:25 起跑，含此前唯一未验证的「名片回主页」）：
  命中时页面在**独立窗口形态**(rect=(0,42,1079,883)) → 点名片 → 重绑新实例 hwnd=2033582 → 回主页
  → 点击(real) → 重绑 hwnd=2888510 → mid=2247484330(已知) → 增量结束 → 自动还原最小化 ✓
- commit `4bcf547`（tools/harvest_list.py +75/-7）已 push，origin/main 一致。
- 免微信最终裁定（应大王之问）：**正文 ✅**（最简 6 参 `biz+mid+idx+sn+chksm+scene`）；
  **发现 ❌**（`chksm` 每次打开都变=服务端会话凭据，`sn`+`chksm` 均每篇独有 → mid 探测废；
  新增封死：文章页只有作者旧文内链、`getprofilebizrecommend`/`getbizbanner` 验证页、`homepage` 错误页、搜索引擎未收录）。
  ⇒ 「不依赖微信 PC 客户端」可行（第三方持 cookie 服务，需一次扫码）；「不依赖微信登录」不可能。
