# Altcoin Opportunity Research

[简体中文](README.md) · English · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

A read-only Windows app for market alerts, funding and basis, on-chain spreads, and LP hedge replay. Search active assets on Binance Spot and Alpha, scan a watchlist or the full catalog in rotation, and track alert outcomes over 3, 7 and 14 days. No account or trading API key is required.

## Install and run

Download the source and install Python 3.12 with Tcl/Tk. From the project directory, run these commands in PowerShell.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

After setup, you can also double-click `start-monitor.bat`. Choose English in the language menu. The app remembers your selection. Select **Start monitor** to scan on a schedule or **Scan now** to run once. Closing the window stops monitoring.

## Opportunity research

- **Funding & basis.** Leave the asset field blank to rotate through OKX assets with both USDT spot and linear perps, or enter comma-separated symbols. Samples compare equal quantities and use the actual settlement interval.
- **On-chain spreads.** Compare equal quantities between a selected V3 pool and matching OKX spot, including trading fees and estimated gas. The simulation assumes inventory on both sides.
- **LP hedge replay.** Use a selected V3 pool over roughly 6, 12 or 24 hours. A 0% hedge uses pool data alone. Hedged replay requires a matching OKX USDT perp and values USDT and USDC 1:1.

The first two pages support one-off and five-minute sampling. All three export selected evidence as JSON. Spreads and hedges match the contract ticker against OKX and exports preserve pool and market details.

## On-chain event replay

Open from a radar asset or alert, choose a token, or enter an ERC-20 contract on Ethereum or BNB Smart Chain. The app finds direct USDT or USDC pools on Uniswap v3 and PancakeSwap v3. Choose 3, 5 or 10 minutes on each side of the alert close or use the latest complete window. Three blocks use the same quantity for quotes. Quote and event views are separate and events retain transaction hashes. Missing liquidity or historical data is marked. Results export as JSON.

## Alert rules

- **A, volume spike.** The latest hour’s spot quote volume reaches both twice the previous 30-day hourly median and its 95th percentile.
- **E, price swing.** The absolute one-hour spot price change reaches the previous 30-day 95th percentile.
- **OB, open interest spike.** Four-hour growth in open interest quantity reaches both 10% and the previous 30-day 95th percentile.
- **C, funding anomaly.** Settled funding, normalized by settlement interval, reaches the positive 95th-percentile threshold or falls below the negative 5th-percentile threshold.

The full-catalog radar uses an active pair per asset on each market, preferring stablecoin quotes, and requires A and E on closed five-minute candles. Each asset has a 24-hour cooldown. Earlier alerts using OB and C remain available.

The default outcome threshold is 20%. The menu also offers 10%, 30% and 50%. The threshold is saved with each alert. Three consecutive traded five-minute candles must close at or beyond it in the same direction. The app tracks open windows and data gaps separately.

## Data and testing

Full-market scanning processes up to 24 assets per batch and resumes from the saved position about one minute after a batch finishes. Watchlist monitoring runs about every five minutes. The app tries to recover the last 32 days of spot data on the first scan. Alerts, settings, cached market data and logs live under `data/alt_event_monitor/`. You can export alerts to CSV. The historical replay tab has no entries until a local research archive is supplied.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor tests.test_alt_opportunities tests.test_chain_replay -v
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
