# 山寨币异动观察站

用币安公开行情监控 RAVE、BSB、EVAA、LAB 的 Windows 桌面工具。手动启动后每 5 分钟扫描一次，记录异常放量和价格波动，并追踪预警后的 3、7、14 天涨跌情况。无需登录币安或配置 API 密钥，程序只读行情。

## 安装与运行

下载本仓库源码并解压。安装带 Tcl/Tk 的 Python 3.12，然后在项目目录打开 PowerShell，执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

配置完成后也可以双击 `start-monitor.bat`。进入窗口后点击「开始监控」定时扫描，或点击「立即扫描」执行一轮。最小化后会继续运行，关闭窗口即停止。

只扫描一次并输出 JSON 事件：

```powershell
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app --scan-once
```

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
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor -v
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
