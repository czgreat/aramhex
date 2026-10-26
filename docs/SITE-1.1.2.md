# 官网跟 1.1.2 一起改的两处

这两个文件不在仓库里，在 DMIT 服务器上。发 1.1.2 时一起改，改之前先备份。

## 1. 查分页去掉看不见的字符

依据：10-23 晚上的日志里，有 5 个人查分时名字带了 U+2066/U+2069（方向控制符，从游戏客户端复制名字时会带上），一直返回 404，反复重试后被限流（429）。软件这边已经在 `lolhex/rank.py` 的 `normalize()` 里处理了，网页也要改成一样的规则。

`/srv/aramhex/www/rank/rank.js` 第 11 行：

```js
// 改前
function norm(s) { return s.replace(/＃/g, "#").replace(/\s*#\s*/g, "#").trim(); }
// 改后（从游戏客户端复制的名字带看不见的方向控制符，不去掉永远查不到）
function norm(s) { return s.replace(/[​-‏‪-‮⁠-⁩﻿]/g, "").replace(/＃/g, "#").replace(/\s*#\s*/g, "#").trim(); }
```

同时把 `rank/index.html` 里的 `rank.js?v=3` 改成 `v=4`，不然浏览器会继续用缓存里的旧文件。

可选：查分后端（家里服务器）在查之前也做一次同样的清理，这样 1.1.1 及更早的软件也能查到。

## 2. `/data/%23h=…` 跳回 `/data/#h=…`

依据：10-23 有 1 个 IP（`113.100.47.47`，浏览器标识只有 `Mozilla/5.0`，没有来源页）打开数据页时 `#` 被转成了 `%23`，66 次请求都是先 308、再 404。

在 `/etc/caddy/Caddyfile` 的 aramhex.com 站点里、`file_server` 前面加：

```caddy
# 有的 App 内置浏览器把 /data/#h=106 转成 /data/%23h=106，跳回正确地址
@datahash path_regexp datahash ^/data/#(.*)$
redir @datahash /data/#{re.datahash.1} 302
```

Caddy 是拿解码后的路径去匹配的（`%23` 已经变成 `#`），不过这一点还没在线上实测。改完先跑一遍：

```bash
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
curl -sI 'https://aramhex.com/data/%23h=106' | grep -i '^location'   # 应为 /data/#h=106
curl -sI 'https://aramhex.com/data/' | head -1                        # 正常页面不受影响：200
```

如果还是先返回 308，说明前面有其他规则先处理了这个地址，要把这一段挪到那条规则前面。
