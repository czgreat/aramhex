# 开发

## 目录

```
lolhex/
  __main__.py          入口（python -m lolhex）
  engine.py            调度：窗口状态、选人识别、三选一识别、推荐
  config.py            配置文件和目录（安装版 %APPDATA%\AramHex，便携版 程序目录\userdata）
  update.py            程序更新：先查官网 aramhex.com/u/latest.json，连不上再查 GitHub Releases（1.1.7 起不再查 jsDelivr）
  patch.py             增量更新：只下载变了的文件（云控备用地址、官网 /u/<版本>/，再 GitHub 的 updates-v<版本>；1.1.7 起不再用 jsDelivr），退出后替换，失败还原
  recommend.py         三选一评分
  panel.py             单英雄面板（胜率、出装、加点、海克斯表）
  i18n.py              外服：按游戏语言认海克斯名、英雄名、选人界面的字（名字表 data/i18n.json）
  data/
    store.py           官方资料（英雄、海克斯、装备）的读取和缓存；1.1.2 起运行时不连腾讯
    official.py        官方资料整包：内置优先，按云控从自己的服务器更新，对局外切换
    public.py          统计数据：内置 + 从数据仓库下载更新，逐文件校验 SHA-256；分数段切换
    tencent.py         腾讯接口和解析
    bundled/           内置统计数据（scripts/update_data.py 生成，不要手改）
  vision/              截屏、选人界面识别、三选一识别、OCR
  ui/                  主窗口、托盘、游戏内浮窗
scripts/update_data.py 把数据仓库最新的一份放进 lolhex/data/bundled/
scripts/update_i18n.py 从拳头公开数据生成外服名字表 lolhex/data/i18n.json（海斗加了新海克斯后重跑）
scripts/update_client_icons.py 生成客户端英雄头像 lolhex/data/client_icons.json（出新英雄、拳头换头像后重跑）
packaging/             PyInstaller、Inno Setup、打包脚本
```

## 原则

- **不连接游戏客户端（不接 LCU、也不读游戏内 2999 接口）、不读内存、不注入、不模拟按键或鼠标**。只截屏识别、显示数据。
  - 浮窗点完以后把前台还给游戏，只调用 `SetForegroundWindow`，不发任何输入。
  - 1.1.1 起删掉了「对局中读取游戏接口」（2999）选项；老设置文件里的 `use_liveclient` 读到就丢掉。
- 统计数据只用数据仓库的公开格式，见 `lolhex/data/public.py` 顶部说明。改格式要升 `schema_version`，并且两个仓库一起改。
- 网络失败不能影响使用：官方资料用上次的缓存，统计数据用内置的那份，检查更新失败时不打扰用户。

## 核对已发布行为

- 排查某个已发布版本时，先核对该版本标签的源码和发布产物；开发分支或试验包的设置不能直接当作正式版操作入口。
- 设置是否存在，要沿配置定义、旧配置迁移、界面或托盘菜单、运行调用逐项核对。设置页和托盘是不同入口，文件里的旧键也可能已被迁移删除。
- 1.1.1 起删除了 `allow_capture`，不再使用防截图接口；识别截图通过 `capture.mask_drawn` 遮掉助手绘制区域。相关回归在 `tests/test_settings_extra.py`，不能依据过时注释判断运行行为。
- 性能反馈分别记录游戏 FPS、显示器刷新率、窗口模式、助手版本，以及退出、隐藏浮窗等对照状态。单次截图或 OCR 耗时不能替代游戏帧时间；公开项目的作者自测、玩家报告和本项目实测分开记录。
- 引用外部讨论或性能数字前核验原文并保留出处；未取得原文的具体反馈只记为未核实，不作为判断依据。

当前界面入口与对照步骤见 [游戏显示与性能排查](display-performance.md)。

## 测试

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv\Scripts\python -m pytest
```

测试不联网（`conftest.py` 的 `no_network`）。需要 HTTP 的测试在本机起临时服务。

## 打包

需要 Inno Setup 6（`winget install --id JRSoftware.InnoSetup -e`）：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-build.txt
.venv\Scripts\python scripts\update_data.py          # 更新内置数据
pwsh -NoProfile -File packaging\build.ps1
```

输出在 `build/release/`：
- `AramHex-Setup-<版本>.exe`
- `AramHex-Portable-<版本>.zip`
- `SHA256SUMS.txt`

打包时会先校验内置数据，再用真实的 Windows 窗口插件跑一遍 Qt 自检，自检不过不生成安装包。

## 云控项

云控（`aramhex.com/config.json`）里的开关一律写 JSON 的 `true` / `false`（如 `rank.enabled`、`update.paused`），写成 `0`、`"false"` 1.1.0 起不认；数字写成数字；网盘链接这类文字写字符串。写错的值程序按默认值处理，不会崩。

1.1.2 新增的云控项（旧版本不认、直接忽略；连不上官网时用上次存下的，再没有就用默认值）：

| 项 | 默认 | 说明 |
|---|---|---|
| `mirrors.official` | `[]` | 官方资料（英雄/海克斯/装备）发布目录，例如 `["https://aramhex.com/u/official"]` |
| `update.official_minutes` | `0` | 多久查一次官方资料：`0` 关闭、`60` 每小时（出新内容时用）、`43200` 每 30 天 |
| `logs.max_kb` | `512` | 点名上传最近多少 KB 日志（64–4096）；`aramhex.log` 不够长时接着读 `aramhex.log.1` |
| `logs.verbose` | `[]` | `["<匿名编号>:<单号>"]`：这台电脑临时记详细日志（DEBUG，不截图），每个单号最多 24 小时；之后用 `logs.request` 点名上传 |
| `features.title_priority` | `true` | 整块扫描先读标题；`false` 退回 1.1.1 全部认字 |
| `features.same_name_once` | `true` | 同一行同名海克斯只算一张 |
| `features.overlay_fit` | `true` | 分数缩放时浮层收回多出的 1 像素、不反复重摆 |
| `features.cards_drop_early` | `true` | 选完英雄卡马上撤掉卡下胜率 |
| `features.crash_log` | `true` | 闪退时记调用栈，下次启动写进日志；`false` 不开，下次启动生效。文件超过 1 MB 这次运行就不再记录 |
| `features.companion_last_fg` | `true` | 客户端被别的程序盖着时点浮窗，选人标注跟着隐藏；`false` 退回点浮窗一律按客户端在前台算 |

`features` 是应急开关：对所有认识它的版本（1.1.2 起）一起生效，不能按比例分批；新做法出问题时先关掉，修好发版后再开。

## 版本号 1.1.6 不用

2026-11-08 未经同意推送过 GitHub tag `v1.1.6`（提交 `ab68757`「AramHex 1.1.6：个人总分与样本提示」），Release 已撤，tag 还在。jsDelivr 的 `@latest` 认最新 tag，连不上官网和 GitHub 的 1.1.5 用户会看到「有新版本 1.1.6」。

- tag 只打一次：不删、不重打 `v1.1.6`，这条分支（`release-1.1.6`，名字不改，免得打断已开的 PR）正式发版用 **1.1.7**，tag 打 `v1.1.7`、`updates-v1.1.7`。
- `lolhex/config.py`、`docs/on-demand-overlay.md` 里「1.1.6 试用版」「1.1.6 早期试用」说的是当时发出去的测试包，照原样保留。
- 1.1.5 走镜像试探时只试 1.1.6、1.2.0、2.0.0，找不到 1.1.7；但同时问的 `@latest` 会读到 1.1.7，所以仍能提示。官网 `u/latest.json` 和 GitHub API 两条主路不受影响。

## 国内连不上 GitHub 时

- 1.1.7 起客户端不再访问 jsDelivr（程序更新、增量更新、统计数据都走官网和 GitHub，备用地址走云控 `mirrors`）。1.1.5 及更早的客户端仍会退到 jsDelivr 取增量更新，所以发版流程里的 jsDelivr 刷新和下面的大小限制暂时保留。
- jsDelivr 按分支名（`@main`、`@updates`）取文件会缓存好几个小时，刷新也不一定生效；按 tag 取是即时的。所以：
  - 程序：发版打 tag `updates-v<版本>`；助手按"下一个版本号"（补丁、次版本、主版本各加 1）试探这个 tag 是否存在，找到后从 tag 下载增量更新。
  - 统计数据：数据仓库每次更新打 tag `data-v<序号>`（`latest.json` 的 `seq`）；助手按"下一个序号"试探。
- jsDelivr 单个文件不能超过 20MB，整个分支或 tag 不能超过 150MB；`.exe` 文件会被拒绝（403），所以增量更新文件都另存一份 `.gz`。
- 完整安装包（86MB）放不进 jsDelivr，只能从 GitHub 下载，或者手动下载。
