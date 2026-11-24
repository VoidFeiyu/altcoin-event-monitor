# Altcoin Event Monitor

[简体中文](README.md) · English · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

A Windows desktop monitor for RAVE, BSB, EVAA and LAB. It reads Binance Alpha and public Binance derivatives data and uses no account or API key. Start it manually to scan every five minutes. The app saves volume and price alerts and follows observed outcomes over 3, 7 and 14 days.

## Install and run

Download the source and install Python 3.12 with Tcl/Tk. From the project directory, run these commands in PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

After setup, you can also double-click `start-monitor.bat`. Choose English in the language menu. The app remembers your selection. Select **Start monitor** to scan on a schedule or **Scan now** to run once. Closing the window stops monitoring.

## Opportunity research

- **Funding & basis.** Compare OKX spot and linear perpetuals for ETH, SOL and LINK. The estimate uses equal token quantities, the actual funding interval, and round-trip fees. The first positive estimate is tracked through settlement using the settled rate and a later order book sample.
- **On-chain spreads.** Compare both directions for ETH and LINK between Ethereum Uniswap v3 and OKX USDC spot, including exchange fees and estimated gas. The model assumes inventory on both venues. Positive spans reflect consecutive valid observations.
- **LP hedge replay.** Replay the Uniswap v3 ETH/USDC 0.05% pool with an optional OKX ETH perpetual hedge. Configure roughly 6, 12 or 24 hours, capital, price range, hedge ratio and costs. The replay values USDT and USDC 1:1.

The first two pages support one-off or five-minute sampling. Each page exports selected evidence as JSON. Public sources are OKX, Ethereum dRPC and PublicNode. Results are simulations and omit margin capital and the cost of moving inventory between venues.

## Alert rules

- **A, volume spike.** The latest hour’s spot quote volume reaches both twice the previous 30-day hourly median and its 95th percentile.
- **E, price swing.** The absolute one-hour spot price change reaches the previous 30-day 95th percentile.
- **OB, open interest spike.** Four-hour growth in open interest quantity reaches both 10% and the previous 30-day 95th percentile.
- **C, funding anomaly.** Settled funding, normalized by settlement interval, reaches the positive 95th-percentile threshold or falls below the negative 5th-percentile threshold.

The spot model requires A and E. The full model requires at least two conditions, including A or OB. Each model has a 24-hour cooldown per token. The full model depends on current derivatives data. The spot model can run independently.

The default outcome threshold is 20%. The menu also offers 10%, 30% and 50%. The threshold is saved with each alert. Three consecutive traded five-minute candles must close at or beyond it in the same direction. The app tracks open windows and data gaps separately.

## Data and testing

The app tries to recover the last 32 days of spot data on the first scan. Alerts, settings, cached market data and logs live under `data/alt_event_monitor/`. You can export alerts to CSV. The historical replay tab has no entries until a local research archive is supplied.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities -v
```

## License

[MIT](LICENSE)

## Donate

If this project saves you time, you are welcome to make an optional donation. All features are free to use.

| Network | Donation address |
| --- | --- |
| Ethereum | `0xEbc48077968d7Afda810603194b0412C35e3B655` |
| Bitcoin | `bc1qjj9dx0fwmzevwjffa55z6wp76egaztjee8ml08` |
| Solana | `2y11NNPRiJpSkLHR4zW23M67KuqxmpdTmhpKim46WtBn` |
| Tron | `TBUzeZCAuqEFuh1rUGmCnmGQoiCKkgHk4Z` |
| Stellar | `GDA4A4GN5WCMADT3GXXBKUEVIUQQPOPDVRX5B23TOVK2AGRV5LBHV545` |

Before sending, make sure the wallet is using the network listed next to the address.
