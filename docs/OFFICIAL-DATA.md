# 官方资料：内置、候选审核与自有服务器更新

本功能面向 1.1.2。实现和本地模拟不代表已部署生产服务器；生产目录、任务和云控修改仍须用户另行批准。

客户端安装包包含完整英雄资料、基础字典、头像和图标。运行时不向腾讯获取这些内容，网络失败仍能正常使用内置或上次已验证的资料。后台官方资料更新只检查云控指定的自有服务器。游戏内不切换资料版本。

## 授权边界

采集、发布、玩家检查是三个独立步骤：

1. 默认按用户指令手动采集。未来即使另行允许自动采集，也只能产生隔离候选，不能更新正式数据。
2. 候选须完整校验，并提供来源日期、游戏版本、与正式版的差异和 SHA-256。只有用户批准具体候选，才能执行发布步骤。
3. 玩家只按云控检查正式清单，采集成功或调整检查间隔都不会自动批准/发布候选。

`scripts/official_data.py` 不在导入时采集，也没有定时任务。`collect` 只接收候选目录，不接收发布目录；客户端不调用它。`publish` 必须显式给出与候选包完全相符的 SHA-256 和批准人。该参数记录操作批准，不能代替会话中获得用户授权。

## 本地离线模拟

下面所有路径都是新建的本地演示目录，不能替换为生产目录而自行执行。

```powershell
python scripts/official_data.py candidate --static lolhex/data/static_bundled.json --heroes lolhex/data/hero_bundled.json --output demo-official/candidate
python scripts/official_data.py init-preview demo-official/public
python scripts/official_data.py report demo-official/candidate --publication demo-official/public
```

此时 `public/latest.json` 不存在，采集候选不会使客户端发现新版。审查报告后，模拟批准明确的候选哈希：

```powershell
python scripts/official_data.py publish demo-official/candidate --publication demo-official/public --approve-sha256 REVIEWED_SHA256 --approved-by local-simulation
```

`REVIEWED_SHA256` 必须替换为刚审核的 `candidate.json` 中 `sha256` 值，工具不会自动替用户批准。候选目录不能在正式目录里面，发布目录必须通过空目录初始化，不能借此接管有其他文件的目录。

本地端到端测试使用临时 `127.0.0.1` 静态 HTTP 服务，验证：候选尚未批准时继续用内置资料；批准后只在对局外激活；新版损坏时保留旧资料；恢复有效包后可重试。测试专门允许 loopback HTTP，正常客户端不放宽 HTTPS 要求。

```powershell
python -m pytest -q tests/test_official_publisher.py tests/test_official_end_to_end.py tests/test_official_data.py
```

## 生产采集入口（尚未部署或运行）

下列命令只有在用户明确授权采集后才执行，且只生成候选：

```powershell
python scripts/official_data.py collect --fetch-from-tencent --output ISOLATED_CANDIDATE_DIRECTORY
```

该步骤复用腾讯解析器，不使用旧内置英雄资料填补失败请求。英雄集合、资料版本和来源日期不完整就失败；不会创建正式清单。候选采集器当前生成基础字典与英雄文字资料包，头像、装备和召唤师技能图标继续由完整内置资源提供；需要新增资源时，须另外准备和审核自有服务器资源包，不能恢复客户端腾讯回源。

## 清单协议与发布一致性

`latest.json` 包括：

- `schema_version: 1`、递增的整数 `revision`、`patch`、`source_date`、带时区的 `generated_at` 和 `min_client`。
- `package.path` 是安全相对路径 `revisions/<revision>/official.json.gz`；另含压缩长度、解压长度和 SHA-256。
- gzip 包内容是 `{static: <static_bundled.json对象>, heroes: <hero_bundled.json对象>}`，英雄集合须与基础字典完全一致。

压缩限制为 16 MiB，解压限制为 64 MiB。内容一致不产生新版本。先写不可变版本目录和批准记录，重新验证包，再最后原子切换 `latest.json`。失败时旧清单仍有效，重试跳过未完成的版本目录，不覆盖旧内容。发布使用独占锁避免两次发布争抢。旧日期或旧游戏版本不能由普通发布覆盖；需要回退时另行审查明确的回退流程。

## 云控语义

拟使用 `mirrors.official` 指定自有资料源，`update.official_minutes` 单独控制官方资料检查：

| 配置值 | 含义 |
|---|---|
| `60` | 每小时检查正式清单 |
| `43200` | 每 30 天检查正式清单 |
| `0` | 关闭官方资料自动检查 |

缺省值为 `0`；没有有效官方资料源时不联网。检查仅下载小清单，资料版本没变就不重复下载整包。现有 `update.data_minutes` 继续控制自采统计，不因官方资料一版不动而暂停统计更新。

云控自身刷新独立运行。收到新云控后重新计算官方资料检查时间，不能保留旧的 30 天等待。离线玩家上线后才可能收到新云控，因此“设为 1 小时”不等于承诺全部玩家一小时内更新。资料下载失败或数据不兼容不影响本地使用；完整下载后也须等选英雄和游戏结束再激活。
