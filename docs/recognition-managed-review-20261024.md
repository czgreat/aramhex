# 识别时效、低负载与统一资料更新交互复核（2026-10-24）

## 范围与结论

本次复核当前工作树中的识别过时防护及官方资料整包集成，未操作游戏、未部署服务端、未修改生产云控。扫描防护由实现代理和主代理整合；最终由另一个子代理独立复核资料更新、锁顺序和扫描时效路径。

确认修复的机制是：在途 OCR 的会话代际、游戏窗口身份或客户区位置/尺寸发生变化时，拒绝旧结果，清理内存跟踪和校准，下一轮重新定位；磁盘校准原件保留。单纯切后台或同一窗口最小化、尚无尺寸变化证据时不清校准，由既有浮层前后台逻辑控制可见性。避免每次 AltTab 都发起昂贵全扫描。

这不是单次模型加速，也不证明 AltTab 是历史中框偏左的唯一触发条件。已捕获的左中框重叠由前一轮几何防护处理；本轮处理的是旧截图结果跨窗口变化发布的另一条确定可达路径。

## 发布链与待修事项

扫描结果带内部 context（会话 epoch、hwnd、客户区 Rect），捕获前、扫描后、写入 offer 前和评分完成后验证。会话清理增加 epoch，防止“旧局结束、新局又是 ingame 且同英雄”的 ABA 状态绕过单纯 phase 判断。context 不包含截图或 OCR 原文。

独立复核报告的以下事项，主代理已补修复及回归；具体以最终测试记录为准：

1. `_on_offer` 原有新卡分支先写新 offer，再于锁外计算评分，其间旧 evaluation 仍可被 GUI snapshot 读到。已在同一锁内将新 offer 与 evaluation=None 一起写入；评分经 revision/context 验证后再发布。相同卡片仅更新坐标的分支可保留已有评分。
2. 官方资料 `_activate` 在正式切换前重新检查 hold，可避免已下载包在选人/对局中启用。发现自采统计的手动检查和下载完成可能绕过 hold，现已改为下载到独立 pending 目录；手动检查仍受对局状态限制，结束后才切目录和内存资料。切换与 Engine 阶段状态共用锁，补下载中进入游戏的回归。
3. 官方快照切换与 DataStore.static 的衔接，应确保读取基础字典和单英雄资料来自同一版本。已改为 DataStore.static 直接读取同一 snapshot；面板和推荐持有资料读取锁，激活持有同一锁和阶段锁，避免一次面板构建混用不同官方版本。

GUI 读取英雄面板与推荐重算不应调用任何腾讯网络路径。正式 Engine 已启用 managed_official，IconCache 禁止网络下载，头像加载不再直接 ensure_icons；显式采集命令与旧的非 managed DataStore 兼容代码属于工具/历史接口，需由运行入口和测试证明确认不会被正式 GUI 调回。

## 性能边界

- 本轮扫描防护没有新增截图、OCR、线程或模型并发，没有缩短原有扫描间隔。
- 每次扫描增加捕获前后几何读取；有新卡时发布与评分阶段再核对。正常新卡最多四轮校验，每轮调用 find_game_window 和 client_rect；最小化等情况下额外查看前台。find_game_window 会枚举窗口，不能把这些调用说成零成本。
- 缓存同卡通常不重评分，负画面没有发布阶段检查。日志只在丢弃发生时输出，60 秒限频。
- 当前已有 ONNX 单线程、OpenCV 单线程和识别线程降优先级；不建议用三路 OCR 并行或开满 CPU 来换取速度。没有做本轮实战 FPS 或 GPU 争用测试，不能承诺具体帧数影响上限。
- 慢 full OCR 仍占用同一 vision 线程，期间标题检查与手动重扫无法执行。过时防护只阻止坏结果发布，没有缩短这段推理或让其可抢占。

## 开源源码核对与适用差异

实际浏览固定提交，参考机制，不复制未验证阈值或外部性能数字：

- [Sygnano offer.rs](https://github.com/Sygnano/aram-mayhem-tracker/blob/7c28471bf5b511e545d8d1c38b63707157edb134/crates/mayhem-vision/src/offer.rs)：先检查小区域按钮，再决定是否取卡片区域及识别变化标题；隐藏按钮和卡片可读并非同一状态。可借鉴独立的低成本存在性证据。
- [Sygnano vision.rs](https://github.com/Sygnano/aram-mayhem-tracker/blob/7c28471bf5b511e545d8d1c38b63707157edb134/apps/desktop/src-tauri/src/engine/vision.rs)：按钮未命中仍保留周期性卡框检查，防止模板失效后永久漏检。我们的标题指纹、按列更新和仅认字已存在，不能当成此次新增能力。
- [Sygnano frames.rs](https://github.com/Sygnano/aram-mayhem-tracker/blob/7c28471bf5b511e545d8d1c38b63707157edb134/crates/mayhem-vision/src/frames.rs)：用缩小画面的边缘轮廓判断卡片存在，并记录发光棱彩被颜色误判的历史问题。我们的颜色冲突拒绝仍需真实棱彩序列验证；不能仅凭代码阈值宣布不会误拒绝。
- [Clarinet 验证记录](https://github.com/Clarinet-koiyun/hex-companion/blob/70c33d952ab4e7273307e9020b1ccf0f08dc34d0/docs/recognition-feasibility.md)：预热与常驻引擎、串行不积压有参考价值；其回放明确不含实战 GPU 争用和屏幕捕获。不能把别人的速度当作我们的性能验收。

下一项性能实验宜用小 ROI 存在性检测先旁路记录，不先阻断现有 OCR；覆盖普通战斗负样本、白银/黄金/棱彩、遮挡、刷新、隐藏、缩放、失效校准后，比较漏检、尾部延迟、OCR 次数和 CPU，再决定是否控制调度。未验证前不合入按钮硬门或扩大模型线程数。

## 实际测试证据

VM197 的独立源码目录 `source-scan-freshness`：

```text
tests/test_scan_geometry_freshness.py
tests/test_vision_diagnostics.py
tests/test_session_transitions.py
tests/test_reroll_continuity.py
48 passed in 0.81s
exit 0
```

本地证据：`artifacts/test-evidence-vm197/tests-scan-freshness-targeted.log`。新增 13 项参数展开回归覆盖 full/strip 的位置、尺寸、窗口、会话变化，扫描到发布之间移动，评分期间移动，后台/最小化保持，以及无效窗口在截图前退出。用计数断言没有新增截图、无效窗口不进入 OCR。

上述为模拟 OCR/窗口的确定性回归，不含真实模型、真实切屏和 Windows 游戏负载，亦不等于官方资料新模块最终整合的全套验收。最终合并还需覆盖上面的发布原子性、统计 hold 和资料快照一致性。

## 待合并 PR 盘点证据

- PR1 `beta-4.0 -> main`：Gitea compare 的 `main...beta-4.0` 与 `release-1.1.2...beta-4.0` 均 0 commits / 0 files；API changed_files/additions/deletions 均为 0。是已被后续覆盖的历史 PR，不应将旧测试采集逻辑重新引入 1.1.2。
- PR12 `fix-summoner-spells -> release-1.1.3`：相对 release-1.1.2 仅独立提交 acdcace2b5ef6c67ffbda21ad7a8dd7946f3dd0d，改 panel.py、ui/buildview.py、tests/test_public.py 三文件。可整合召唤师技能 ≥3% 且 ≥1000 局、最多三组规则及测试，不应从旧分支整体覆盖当前面板的离线读取修复。多一组会增加浮窗高度，需纳入 GUI 验证。

主代理追加：在途扫描的校准写盘延迟到窗口一致性验证通过后，过时帧不再先覆盖磁盘原件；手动重新识别/重置校准也增加扫描代际，避免旧在途结果覆盖操作。新增文件字节保留和手动重扫回归。以上检查不新增截图、OCR 或并行推理。

## 最终整合验收

VM197 最终源码全量回归：456 passed, 4 skipped，23.12s；目标回归和 git diff --check 通过。独立复核额外发现合法 SHA 的语义坏包可能进入运行数据，已补逐字段校验和 29 项坏包回归；拒绝后保留旧快照，重启仍使用旧版本。

使用真实内置 173 英雄资料在隔离 loopback HTTP 模拟完整候选、审查、模拟批准和客户端更新：候选未批准时不发布；选人/对局中 pending，结束后激活；相同 revision 不重复下载。整包压缩 447957 字节，未请求腾讯，未改生产。Windows 全量和冻结包原生 Qt/OCR 验收另随测试包记录。
