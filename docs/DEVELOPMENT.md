# 开发与发版

## 目录

```
lolhex/
  __main__.py          入口（python -m lolhex）
  engine.py            调度：窗口状态、选人识别、三选一识别、推荐
  config.py            配置文件和目录（安装版 %APPDATA%\AramHex，便携版 程序目录\userdata）
  update.py            程序更新：先查官网 aramhex.com/u/latest.json，连不上再查 GitHub Releases、jsDelivr
  patch.py             增量更新：只下载变了的文件（官网 /u/<版本>/，再 GitHub / jsDelivr 的 updates-v<版本>），退出后替换，失败还原
  recommend.py         三选一评分
  panel.py             单英雄面板（胜率、出装、加点、海克斯表）
  i18n.py              外服：按游戏语言认海克斯名、英雄名、选人界面的字（名字表 data/i18n.json）
  langpack.py          韩服识别数据（韩文识别模型，选了韩文再下载，不进安装包）
  data/
    store.py           官方资料（英雄、海克斯、装备）的读取和缓存；1.1.2 起运行时不连腾讯
    official.py        官方资料整包：内置优先，按云控从自己的服务器更新，对局外切换（见 docs/OFFICIAL-DATA.md）
    public.py          自采统计数据：内置 + 从数据仓库下载更新，逐文件校验 SHA-256；分数段切换
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

## 发版

开发在局域网 Gitea（每个版本一条 `release-x.y.z` 分支，功能分支开 PR 合进去）；GitHub 上每个版本是一条单独的提交，内容和 Gitea 发布分支相同。
服务器、云控、官网这些不在本仓库的步骤，见内部交接文档「AramHex 发布链路」。

### 测试包

发给群里或局域网试用的包，一律带测试号打包：

```powershell
pwsh -NoProfile -File packaginguild.ps1 -PortableOnly -TestTag test.1   # 每次加 1
```

版本号会变成 `1.1.3-test.1`，比正式版 `1.1.3` 旧，正式版发布后能收到更新提示。不带测试号的包和正式版同号，装了的人收不到正式版（1.1.2 时有 32 台这样）。

### 正式版

1. 更新内置数据：`python scripts/update_data.py`，然后提交。注意第 4 步 Actions 打包时还会再跑一次，取数据仓库当时最新的一份；要换数据就先发数据，再打 tag。
2. 改 `lolhex/__init__.py` 的 `__version__`，在 `CHANGELOG.md` 写这一版的说明。
   - 说明是写给玩家看的（发布页和软件里的「有新版本」提示都显示它）：一两句大白话，只写更新了什么、修了什么问题，比如「更新时能看到下载进度了」「修复：……」。不写技术细节、文件名、数字口径。
3. 把发布分支的内容合成一条提交推到 GitHub `main`（作者 czgreat，提交说明「AramHex x.y.z」，不带 AI 署名），等「测试」工作流通过；再打附注 tag `vX.Y.Z` 并推送。GitHub 上有人直接改过的文件（例如文档），先合回 Gitea。
4. GitHub Actions（`.github/workflows/release.yml`）自动完成：
   - 核对 tag 和 `__version__` 一致；
   - 更新内置数据、跑测试、打包；
   - 把增量更新的清单和文件（另有 .gz 压缩版，jsDelivr 不给下 .exe）推到 `updates` 分支，并打 tag `updates-v<版本>`（先于建 Release）；
   - 建 Release，上传安装版、便携版和 `SHA256SUMS.txt`（文件名不能改，老版本按名字找）；
   - Release 说明取 `CHANGELOG.md` 里这一版的内容。
5. 把 Release 里的安装包下载下来再检查一遍：Actions 打的和本机打的不是同一份字节（Actions 机器自带 VC 运行库，多一个 `_internal/msvcp140.dll`）。看 SHA-256、自检（`AramHex.exe --self-test-gui <report.json>`）、杀毒、从上一版增量升级。
6. 软件先问官网 `aramhex.com/u/latest.json`，官网每 5 分钟从 GitHub 同步一次；只要官网连得上，就只听官网的。所以 GitHub Release 建好后，要等官网同步了，大多数人才会收到提示。发布时可以先暂停官网同步，检查完再放开。
7. 已安装的助手启动时、再次打开时，以及每隔一段时间（1.1.0 起由云控 `update.check_minutes` 决定，默认 3 小时；1.0.0 是 30 分钟）查一次新版本，有新版本就提示。云控 `update.paused`、`update.rollout_version` + `update.rollout_percent` 可以暂停提示或只给一部分人提示（1.1.0 起）。

一个版本号只打一次 tag（jsDelivr 永久缓存 tag）：发布失败也不要删了重打，直接发下一个版本号。

只更新统计数据、不改程序时，不用发版：数据仓库更新后，助手会自己下载新数据。
定期发版只是为了让新下载的用户拿到较新的内置数据。

## 新版本海斗上线当天：过渡数据

新海克斯上线第一天还没有正式服统计，先发一份过渡数据，第二天再换成新版本的正式数据。

规则（1.1.0 起）：
- 老海克斯用正式服统计（上一个版本）为主；
- 新海克斯用测试服对局，只显示测试服胜率和局数，**不参与排名**；样本少也给分，不显示「无数据」；
- 删掉的海克斯从所有英雄表里去掉（排名的分母跟着变）。以测试服游戏数据为准（`Enabled=false`），再用测试服实际对局补充（按比例该出现 ≥30 次却一次没有），对照官方更新公告核对。

步骤：
1. 先把上一个版本最新的正式数据发布到数据仓库，用它当底子。
2. 补采测试服到最新，生成过渡数据，**发布时必须给新目录和新序号**（不给就沿用底子的，客户端会去旧目录下载，全部校验失败）：

   ```
   python scripts/pbe_overlay.py --raw <测试服原始对局> --build <测试服构建号> --base <底子目录> \
       --out <输出> --kiwi kiwi_augments.json --game-data <CommunityDragon pbe kiwi.bin.json> \
       --path patches/<版本>-pbe/normal --seq <底子 seq + 1>
   ```
   脚本会打印删除清单；「游戏数据标了停用、测试服里还出现」的会单独列出，要人工核对。
3. 按数据仓库的发布流程放进 `<path>`，更新 `latest.json`、`index.json`，打 tag `data-v<seq>`。
4. 发布前拿一个客户端实际同步一次（看日志里「统计数据有更新」和校验通过），再放开。
5. 发了过渡数据之后，当天不要再发上一个版本的正式数据（样本时间更新，会把过渡数据换掉）。
6. 想让用户当天更快拿到，提前四五个小时把云控 `update.data_minutes` 调小（默认 6 小时）；1.0.0 固定 6 小时，调不了。
7. 第二天用新版本第一天的正式对局出快照发布，样本时间更新，客户端自动换掉过渡数据。**序号要接着过渡数据再加 1**（过渡数据已经用掉 `data-v<底子+1>`），确认数据仓库的发布工具知道这个号已经用过，不然打 tag 会冲突。

云控（`aramhex.com/config.json`）里的开关一律写 JSON 的 `true` / `false`（如 `rank.enabled`、`update.paused`），写成 `0`、`"false"` 1.1.0 起不认；数字写成数字；网盘链接这类文字写字符串。写错的值程序按默认值处理，不会崩。

1.1.2 新增的云控项（旧版本不认、直接忽略；连不上官网时用上次存下的，再没有就用默认值）：

| 项 | 默认 | 说明 |
|---|---|---|
| `mirrors.official` | `[]` | 官方资料（英雄/海克斯/装备）发布目录，例如 `["https://aramhex.com/u/official"]`，见 `docs/OFFICIAL-DATA.md` |
| `update.official_minutes` | `0` | 多久查一次官方资料：`0` 关闭、`60` 每小时（出新内容时用）、`43200` 每 30 天 |
| `logs.max_kb` | `512` | 点名上传最近多少 KB 日志（64–4096）；`aramhex.log` 不够长时接着读 `aramhex.log.1` |
| `logs.verbose` | `[]` | `["<匿名编号>:<单号>"]`：这台电脑临时记详细日志（DEBUG，不截图），每个单号最多 24 小时；之后用 `logs.request` 点名上传 |
| `features.title_priority` | `true` | 整块扫描先读标题；`false` 退回 1.1.1 全部认字 |
| `features.same_name_once` | `true` | 同一行同名海克斯只算一张 |
| `features.overlay_fit` | `true` | 分数缩放时浮层收回多出的 1 像素、不反复重摆 |
| `features.cards_drop_early` | `true` | 选完英雄卡马上撤掉卡下胜率 |
| `features.companion_last_fg` | `true` | 客户端被别的程序盖着时点浮窗，选人标注跟着隐藏；`false` 退回点浮窗一律按客户端在前台算 |

`features` 是应急开关：对所有认识它的版本（1.1.2 起）一起生效，不能按比例分批；新做法出问题时先关掉，修好发版后再开。

## 国内连不上 GitHub 时

- jsDelivr 按分支名（`@main`、`@updates`）取文件会缓存好几个小时，刷新也不一定生效；按 tag 取是即时的。所以：
  - 程序：发版打 tag `updates-v<版本>`；助手按"下一个版本号"（补丁、次版本、主版本各加 1）试探这个 tag 是否存在，找到后从 tag 下载增量更新。
  - 统计数据：数据仓库每次更新打 tag `data-v<序号>`（`latest.json` 的 `seq`）；助手按"下一个序号"试探。
- jsDelivr 单个文件不能超过 20MB，整个分支或 tag 不能超过 150MB；`.exe` 文件会被拒绝（403），所以增量更新文件都另存一份 `.gz`。
- 完整安装包（86MB）放不进 jsDelivr，只能从 GitHub 下载，或者手动下载。
