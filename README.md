# 山寨币机会研究台

简体中文 · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

Windows 桌面研究工具，使用公开行情监测山寨币异动、比较资金费率与基差和链上价差，并回放 LP 与永续合约的组合对冲。异动雷达跟踪 RAVE、BSB、EVAA、LAB，以及预警后的 3、7、14 天表现。各模块只读，无需账户或交易 API 密钥。

## 安装与运行

下载本仓库源码并解压。安装带 Tcl/Tk 的 Python 3.12，然后在项目目录打开 PowerShell，执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

配置完成后也可以双击 `start-monitor.bat`。右上角可以切换简体中文、英语、日语、韩语和西班牙语，程序会记住你的选择。点击「开始监控」定时扫描，或点击「立即扫描」执行一轮。最小化后会继续运行，关闭窗口即停止。

只扫描一次并输出 JSON 事件：

```powershell
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app --scan-once
```

## 机会研究

| 页面 | 数据与结果 |
| --- | --- |
| 费率与基差 | OKX 的 ETH、SOL、LINK USDT 现货和线性永续。按实际结算周期比较费率，以相同币数量读取两边盘口，扣往返费用。首个正净额样本会跟踪至下一结算，并以实际费率和结算后盘口模拟退出。 |
| 链上价差 | Ethereum 主网的 Uniswap v3 与 OKX USDC 现货。按相同数量比较 ETH、LINK 的双向报价，扣除交易所费和估算 Gas，记录正价差在有效采样中持续的时间。模型假定两边已有库存。 |
| LP 对冲回放 | 用 Uniswap v3 ETH/USDC 0.05% 池的历史状态和已结算资金费率，比较 LP 单独持有与 OKX 永续对冲。可设置约 6/12/24 小时、LP 本金、区间宽度、对冲比例和成本。回放按 USDT 与 USDC 1:1 计价。 |

前两页支持立即采样或每 5 分钟采样。三页都能导出所选记录的 JSON 证据。只读数据源包括 OKX、Ethereum dRPC 和 PublicNode。净额是模拟结果。费用、盘口深度、数据可用性和观察间隔会影响结果，模型未计保证金占用或跨平台资金调拨成本。

## EVAA 事件回放

选中一条 EVAA 预警，再点击 EVAA 卡片上的「链上回放」。也可直接打开回放页，查看最近一个完整窗口。前后各 3、5 或 10 分钟的区块按预警 K 线收盘时间对齐。页面比较 BNB Chain 上 Uniswap v3 0.30% 与 PancakeSwap v3 0.01% 两个 EVAA/USDT 池。双向报价在三个区块使用同一 EVAA 数量，成本含池费和价格冲击，未计 Gas。

时间轴保留交易哈希、区块和日志序号，可查看成交与新增、移除流动性。净买入按各池 Swap 的 USDT 净变化计算。撤出计数区分是否发生在当时价格所在的区间。历史区块状态读不到时，报价单独标记为缺口。回放结果保存在本机，可导出完整 JSON。

## 异动规则

| 条件 | 默认规则 |
| --- | --- |
| A · 现货放量 | 最近 1 小时成交额至少为此前 30 天小时成交额中位数的 2 倍，并达到第 95 百分位。 |
| E · 价格波动 | 最近 1 小时绝对涨跌幅达到此前 30 天的第 95 百分位。 |
| OB · 持仓增加 | 4 小时合约持仓数量增加至少 10%，并达到此前 30 天的第 95 百分位。 |
| C · 资金费率 | 已结算费率按结算周期归一化后，正值达到第 95 百分位或负值低于第 5 百分位。 |

价量版要求 A、E 同时满足。完整版要求四项中至少两项满足，并包含 A 或 OB。两种预警各自按币种设置 24 小时冷却。程序只用已收盘 K 线判断，合约数据不足时会显示完整版不可用，价量版仍可独立运行。

涨跌追踪的默认门槛为 20%，可在界面选择 10%、20%、30% 或 50%。连续三根有实际成交的 5 分钟收盘同方向达到门槛才确认结果。每条预警保存当时的门槛，行情缺口会单独标记。预警标记的是异常行情，涨跌方向是后续观察结果。

## 数据和测试

首次扫描尝试从币安 Alpha 回补最近 32 天的现货数据。官方接口若限制访问、代币状态发生变化或历史不足，界面会显示对应状态。预警、设置、行情缓存和日志保存在本机 `data/alt_event_monitor/`，预警可从界面导出 CSV。开源仓库未附带原项目的本地研究归档，历史回放页在未安装归档时为空。

运行现有测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities tests.test_evaa_replay -v
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
