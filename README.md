# 山寨币机会研究台

简体中文 · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

Windows 桌面研究工具。可搜索币安现货与 Alpha 的活跃标的，使用自选或全市场轮扫，追踪预警后 3、7、14 天表现，并研究资金费率、链上价差与 LP 对冲。程序读取公开 API，无需账户或交易密钥。

## 浏览器观察平台

安装依赖后双击 `start-monitor.bat`，打开原有「山寨币暴涨暴跌观察」的本地网页。也可运行 `python -m klinebot.alpha_web --open`，访问 [本机观察页](http://127.0.0.1:8769/alpha)。页面使用公开行情，默认数据目录是 `data/alpha_radar/`。可用 `--data-dir` 打开已有网页观察数据库。

币安现货与 Alpha 共用价量扫描。币种详情展示数据覆盖、当前和冻结证据、主动成交与 BTC 同期对照。自选和新触发预警的标的采集盘口快照。Ethereum 与 BNB Smart Chain 对应币种可按需读取直连 V3 池事件。桌面界面仍可通过下方命令启动。

「异常历史」汇总同一市场同一时间的预警，显示触发后 1／6／24 小时收盘表现、首日收盘高低、3／7／14 天结果和冻结证据。本机可用行情会自动回填，归档价格路径随历史保留。清空实时预警只影响实时视图。

## 安装与运行

下载本仓库源码并解压。安装带 Tcl/Tk 的 Python 3.12，然后在项目目录打开 PowerShell，执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

右上角可以切换简体中文、英语、日语、韩语和西班牙语，程序会记住你的选择。点击「开始监控」定时扫描，或点击「立即扫描」执行一轮。最小化后会继续运行，关闭窗口即停止。

只扫描一次并输出 JSON 事件：

```powershell
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app --scan-once
```

## 机会研究

- **费率与基差。** 币种留空时，轮扫所有同时有 OKX USDT 现货和线性永续的标的。也可输入以逗号分隔的代码。使用同币数量和实际费率周期估算，记录首次正净额样本并追踪结算。
- **链上价差。** 比较所选 V3 池和 OKX 同计价现货的双向报价，含手续费和估算 Gas。模型假定两边已有库存。
- **LP 对冲回放。** 回放所选 V3 池约 6、12 或 24 小时的手续费和损益。对冲比例可设为 0%，开启对冲时需要 OKX 对应的 USDT 永续。USDT 与 USDC 按 1:1 计价。

前两页支持手动或每 5 分钟采样。三页均可导出所选记录的 JSON。价差与对冲通过合约代码匹配 OKX 标的，证据记录含池和市场信息。

## 链上事件回放

从雷达币种或预警进入，也可直接选币或输入 Ethereum、BNB Smart Chain 的 ERC-20 合约。系统查找 Uniswap v3 和 PancakeSwap v3 的 USDT 或 USDC 直连池。选择预警前后各 3、5 或 10 分钟，也可回放最近完整窗口。三个区块按同一数量计算双向报价，包含池费和价格冲击。报价和含交易哈希的事件分开展示。缺少流动性或历史状态时标记缺口，结果可导出 JSON。

## 异动规则

| 条件 | 默认规则 |
| --- | --- |
| A · 现货放量 | 最近 1 小时成交额至少为此前 30 天小时成交额中位数的 2 倍，并达到第 95 百分位。 |
| E · 价格波动 | 最近 1 小时绝对涨跌幅达到此前 30 天的第 95 百分位。 |
| OB · 持仓增加 | 4 小时合约持仓数量增加至少 10%，并达到此前 30 天的第 95 百分位。 |
| C · 资金费率 | 已结算费率按结算周期归一化后，正值达到第 95 百分位或负值低于第 5 百分位。 |

全市场雷达为每个市场的标的选取一个活跃交易对，优先使用稳定币计价，以已收盘 5 分钟 K 线独立计算各币种的 A、E。两个条件同时满足才发出预警，冷却期为 24 小时。旧版含 OB、C 的预警仍可查看。

涨跌追踪的默认门槛为 20%，可在界面选择 10%、20%、30% 或 50%。连续三根有实际成交的 5 分钟收盘同方向达到门槛才确认结果。每条预警保存当时的门槛，行情缺口会单独标记。预警标记的是异常行情，涨跌方向是后续观察结果。

## 数据和测试

全市场每批最多扫描 24 个标的，批次完成后约 1 分钟继续。自选监控约每 5 分钟更新。两种模式都需要先点击「开始监控」。 首次扫描尝试从行情接口回补最近 32 天的现货数据。官方接口若限制访问、代币状态发生变化或历史不足，界面会显示对应状态。预警、设置、行情缓存和日志保存在本机 `data/alt_event_monitor/`，预警可从界面导出 CSV。开源仓库未附带原项目的本地研究归档，历史回放页在未安装归档时为空。

运行现有测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities tests.test_chain_replay -v
```

本项目采用 [MIT 许可证](LICENSE)。

## 自愿捐赠

如果这个工具对你有帮助，欢迎自愿支持。所有功能均可免费使用。

| 网络 | 捐赠收款地址 |
| --- | --- |
| Ethereum | `0xEbc48077968d7Afda810603194b0412C35e3B655` |
| Bitcoin | `bc1qjj9dx0fwmzevwjffa55z6wp76egaztjee8ml08` |
| Solana | `2y11NNPRiJpSkLHR4zW23M67KuqxmpdTmhpKim46WtBn` |
| Tron | `TBUzeZCAuqEFuh1rUGmCnmGQoiCKkgHk4Z` |
| Stellar | `GDA4A4GN5WCMADT3GXXBKUEVIUQQPOPDVRX5B23TOVK2AGRV5LBHV545` |

转账前请确认钱包选择的网络与表中对应。
