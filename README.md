# AramHex 海克斯大乱斗助手

英雄联盟国服「海克斯大乱斗」桌面助手。官网：[aramhex.com](https://aramhex.com)

- **英雄胜率**：选英雄时自动认出你和队友的英雄，显示胜率和评级。
- **海克斯胜率**：三选一时按本英雄的海克斯胜率推荐。
- **出装与加点**：官方出装和技能加点，按胜率排序。
- **分段**：支持娱乐、中分、前 5%。当前统计未提供分段时，统一使用全体样本，并显示实际口径。
- **外服**：繁中客户端（台服等）不用设置直接能用；日服、韩服、美服等在设置里选游戏语言（韩文要先下载韩服识别数据，约 13MB）。

只截屏识别，不连接游戏客户端或对局接口，不读取游戏进程和内存，不注入、不模拟按键。

## 下载

- 官网：[aramhex.com](https://aramhex.com)
- GitHub：[Releases](https://github.com/czgreat/aramhex/releases)
  - `AramHex-Setup-<版本>.exe`：安装版，可以在软件里一键更新
  - `AramHex-Portable-<版本>.zip`：便携版，解压就能用

游戏要设成**无边框**或**窗口化**。Windows 提示「已保护你的电脑」时，点「更多信息」→「仍要运行」。

排查问题时，可在 `AramHex.exe` 旁或数据目录里放一个空的 `debug.txt`，从托盘退出后重新打开。它只是调试开关，日志写在数据目录的 `aramhex.log`：便携版是程序旁的 `userdata`，安装版是 `%APPDATA%\AramHex`。请确认文件不是 `debug.txt.txt`；删除标记并重启可关闭调试。

## 隐私

使用统计与诊断日志的处理方式，详见[隐私声明](PRIVACY.md)。

## 数据

- 胜率统计：自采对局，见 [aramhex-data](https://github.com/czgreat/aramhex-data)（CC BY-NC 4.0）。只统计采集到的对局，不是全服数据。
- 在线查询：[aramhex.com/data](https://aramhex.com/data/) 可以按英雄或按海克斯查看。某个英雄每个海克斯的胜率打开 `https://aramhex.com/data/hero/英雄名/`（如 [亚索](https://aramhex.com/data/hero/%E4%BA%9A%E7%B4%A2/)），某个海克斯在每个英雄上的胜率打开 `https://aramhex.com/data/augment/海克斯名/`（如 [秘术冲拳](https://aramhex.com/data/augment/%E7%A7%98%E6%9C%AF%E5%86%B2%E6%8B%B3/)）。
- 英雄、海克斯、装备、官方出装和加点：腾讯官方公开数据。

## 从源码运行

Windows 10/11 64 位，Python 3.13：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m lolhex
.venv\Scripts\python -m pytest
```

打包和发版见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 协议

程序代码 [MIT](LICENSE)；统计数据 [CC BY-NC 4.0](https://github.com/czgreat/aramhex-data/blob/main/LICENSE)；第三方组件见 [NOTICE.md](NOTICE.md)。

非官方工具，和腾讯、Riot Games 没有关系。
