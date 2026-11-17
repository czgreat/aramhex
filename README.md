# AramHex 海克斯大乱斗助手

英雄联盟国服「海克斯大乱斗」桌面助手。官网：[aramhex.com](https://aramhex.com)

- **英雄胜率**：选英雄时自动认出你和队友的英雄，显示胜率和评级。
- **海克斯胜率**：三选一时按本英雄的海克斯胜率推荐。
- **出装与加点**：官方出装和技能加点，按胜率排序。选到命运之武器时显示装备参考。
- **详情快捷键**：默认不绑定，可在设置中自定义或清空禁用，用于展开或收起出装、加点详情。也可点击浮窗；海克斯推荐仍自动出现。
- **分段**：支持娱乐、中分、前 5%。当前统计未提供分段时，统一使用全体样本，并显示实际口径。
- **外服**：繁中客户端（台服等）不用设置直接能用；日服、美服、欧服等在设置里选游戏语言（韩服不支持）。

只截屏识别，不连接游戏客户端或对局接口，不读取游戏进程和内存，不注入、不模拟按键。

## 下载

- 官网：[aramhex.com](https://aramhex.com)
- GitHub：[Releases](https://github.com/czgreat/aramhex/releases)
  - `AramHex-Setup-<版本>.exe`：安装版，可以在软件里一键更新
  - `AramHex-Portable-<版本>.zip`：便携版，解压就能用

游戏要设成**无边框**或**窗口化**。Windows 提示「已保护你的电脑」时，点「更多信息」→「仍要运行」。

排查问题时，可在 `AramHex.exe` 旁或数据目录里放一个空的 `debug.txt`，从托盘退出后重新打开。它只是调试开关，日志写在数据目录的 `aramhex.log`：便携版是程序旁的 `userdata`，安装版是 `%APPDATA%\AramHex`。请确认文件不是 `debug.txt.txt`；删除标记并重启可关闭调试。

调试模式会额外保存选英雄和海克斯选择截图，比较游戏性能时先不要开启。

## 游戏浮窗与性能排查

浮窗可以正常出现在截图和录屏里。旧的“录屏、截图时显示浮窗”设置已在 1.1.1 删除；助手只在用于识别的截图里遮掉自己绘制的区域，避免认到自己的文字。

设置页提供浮窗位置和海克斯标签位置，改了马上生效。1.1.7 起对局中的按钮在第一次选完海克斯后隐藏，鼠标移到原位置才出现。托盘右键菜单的“显示游戏浮窗”控制浮窗显示。

遇到掉帧、黑闪或无边框下帧率封顶，需要分别检查浮窗、截图和 OCR。完全退出助手会同时停止这些工作，仅凭“退出后恢复”还不能确定原因。对照步骤和版本差异见 [游戏显示与性能排查](docs/display-performance.md)。

## 隐私

使用统计与诊断日志的处理方式，详见[隐私声明](PRIVACY.md)。

## 数据

- 胜率统计和命运之武器装备参考：见 [aramhex-data](https://github.com/czgreat/aramhex-data)（CC BY-NC 4.0）。统计的是部分对局，不是全服数据。
- 在线查询：[aramhex.com/data](https://aramhex.com/data/) 可以按英雄或按海克斯查看。某个英雄每个海克斯的胜率打开 `https://aramhex.com/data/hero/英雄名/`（如 [亚索](https://aramhex.com/data/hero/%E4%BA%9A%E7%B4%A2/)），某个海克斯在每个英雄上的胜率打开 `https://aramhex.com/data/augment/海克斯名/`（如 [秘术冲拳](https://aramhex.com/data/augment/%E7%A7%98%E6%9C%AF%E5%86%B2%E6%8B%B3/)）。
- 英雄、海克斯、装备的名称、图标和说明，以及官方出装和加点：腾讯官方公开数据。

## 从源码运行

Windows 10/11 64 位，Python 3.13：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m lolhex
.venv\Scripts\python -m pytest
```

打包和发版见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

1.1.7 对局中按钮的显示规则见 [对局中按需显示浮窗按钮](docs/on-demand-overlay.md)。

## 协议

程序代码 [MIT](LICENSE)；统计数据 [CC BY-NC 4.0](https://github.com/czgreat/aramhex-data/blob/main/LICENSE)；第三方组件见 [NOTICE.md](NOTICE.md)。

非官方工具，和腾讯、Riot Games 没有关系。
