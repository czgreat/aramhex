# AramHex 海克斯大乱斗助手

英雄联盟国服「海克斯大乱斗」桌面助手。官网：[aramhex.com](https://aramhex.com)

- **英雄胜率**：选英雄时自动认出你和队友的英雄，显示胜率和评级。
- **海克斯胜率**：三选一时按本英雄的海克斯胜率推荐。
- **出装与加点**：官方出装和技能加点，按胜率排序。
- **分段**：可以切换娱乐、中分、高分、顶尖局的数据。
- **外服**：繁中客户端（台服等）不用设置直接能用；日服、韩服、美服等在设置里选游戏语言（韩文要先下载韩服识别数据，约 13MB）。

只截屏识别，不接入客户端、不读内存、不模拟按键。

## 下载

- 官网：[aramhex.com](https://aramhex.com)
- GitHub：[Releases](https://github.com/czgreat/aramhex/releases)
  - `AramHex-Setup-<版本>.exe`：安装版，可以在软件里一键更新
  - `AramHex-Portable-<版本>.zip`：便携版，解压就能用

游戏要设成**无边框**或**窗口化**。Windows 提示「已保护你的电脑」时，点「更多信息」→「仍要运行」。

## 隐私

- 帮助改善软件（匿名统计）：1.1.0 起加入匿名统计，方便分批推送新版本和测试版。第一次打开时随机生成一个编号（和电脑硬件、游戏账号无关，重装就换新的），分批推送按它在本机分组；只在读取官网配置（aramhex.com/config.json）时带上，用来统计大约有多少台电脑在用、用的哪个版本。
- 不收集游戏账号、对局内容和截图。要关掉：把设置文件 `config.json` 里的 `send_usage` 改成 `false`（安装版在 `%APPDATA%\AramHex`，便携版在程序目录的 `userdata`）。

## 数据

- 胜率统计：自采对局，见 [aramhex-data](https://github.com/czgreat/aramhex-data)（CC BY-NC 4.0）。只统计采集到的对局，不是全服数据。
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
