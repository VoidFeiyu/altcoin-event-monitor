# アルトコイン急変動モニター

[简体中文](README.md) · [English](README.en.md) · 日本語 · [한국어](README.ko.md) · [Español](README.es.md)

RAVE、BSB、EVAA、LABを監視するWindows用デスクトップツールです。Binance AlphaとBinanceの公開デリバティブデータを読み取り、アカウントやAPIキーは不要です。手動で開始すると5分ごとにスキャンし、出来高や価格の異常を保存して、3日・7日・14日後までの結果を追跡します。

## インストールと起動

ソースをダウンロードし、Tcl/Tk付きのPython 3.12をインストールしてください。プロジェクトのディレクトリでPowerShellを開き、次を実行します。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m klinebot.alt_monitor_app
```

初期設定後は `start-monitor.bat` をダブルクリックしても起動できます。言語メニューで日本語を選ぶと、設定が保存されます。「監視を開始」で定期スキャン、「今すぐスキャン」で単発スキャンを実行します。ウィンドウを閉じると停止します。

## 通知ルール

- **A · 現物出来高急増。** 直近1時間の売買代金が過去30日間の時間別中央値の2倍以上かつ95パーセンタイル以上。
- **E · 価格の急変動。** 直近1時間の現物価格の絶対変化率が過去30日間の95パーセンタイル以上。
- **OB · 建玉急増。** 4時間の建玉数量の増加率が10%以上かつ過去30日間の95パーセンタイル以上。
- **C · 資金調達率の異常。** 決済間隔で正規化した確定済み資金調達率が、正の場合は95パーセンタイル以上、負の場合は5パーセンタイル未満。

現物モデルにはAとE、完全モデルにはAまたはOBを含む2条件以上が必要です。再通知は銘柄・モデルごとに24時間の間隔を空けます。デリバティブデータが不足していても現物モデルは独立して動作します。

結果判定のしきい値は既定で20%です。10%、30%、50%にも変更できます。しきい値は通知ごとに保存され、実際に取引のある5分足が同方向に3本連続してしきい値に達して確定すると達成です。追跡中とデータ欠損は別々に表示されます。

## データとテスト

初回スキャンで直近32日間の現物データの補完を試みます。通知、設定、市場データのキャッシュ、ログは `data/alt_event_monitor/` に保存されます。通知はCSVに出力できます。過去のリプレイ一覧には、ローカルの調査アーカイブがある場合に事例が表示されます。

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_alt_event_monitor -v
```

## ライセンス

[MIT](LICENSE)

## 任意の寄付

このツールがお役に立ちましたら、任意で開発を支援できます。すべての機能を無料で利用できます。

| ネットワーク | 寄付先アドレス |
| --- | --- |
| Ethereum | `0xEbc48077968d7Afda810603194b0412C35e3B655` |
| Bitcoin | `bc1qjj9dx0fwmzevwjffa55z6wp76egaztjee8ml08` |
| Solana | `2y11NNPRiJpSkLHR4zW23M67KuqxmpdTmhpKim46WtBn` |
| Tron | `TBUzeZCAuqEFuh1rUGmCnmGQoiCKkgHk4Z` |
| Stellar | `GDA4A4GN5WCMADT3GXXBKUEVIUQQPOPDVRX5B23TOVK2AGRV5LBHV545` |

送金前に、ウォレットで表に記載されたネットワークを選んでいることを確認してください。
