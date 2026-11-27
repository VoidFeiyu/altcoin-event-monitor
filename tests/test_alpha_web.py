import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from klinebot.alpha_market_analysis import book_evidence, market_flow
from klinebot.alpha_radar import AlphaRadar, spot_catalog
from klinebot.alpha_web import RadarHandler
from klinebot.alt_monitor_core import BAR_MS, Store, parse_bar


class WebRadarTests(unittest.TestCase):
    def test_legacy_candles_migrate_without_fabricating_taker_amounts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with closing(sqlite3.connect(path/'monitor.sqlite3')) as db, db:
                db.execute('''CREATE TABLE bars(symbol TEXT,end_ms INTEGER,open REAL,high REAL,low REAL,
                    close REAL,base REAL,quote REAL,trades INTEGER,source TEXT,PRIMARY KEY(symbol,end_ms))''')
                db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)',('BTC',BAR_MS,1,1,1,1,50,50,2,'old'))
            store = Store(path)
            self.assertIsNone(store.bars('BTC').iloc[0].taker_quote)
            candle = parse_bar([BAR_MS,1,1,1,1,50,2*BAR_MS-1,50,2,30,30,0],2*BAR_MS)
            store.save_bars('BTC',[candle])
            self.assertEqual(store.bars('BTC').iloc[-1].taker_quote,30)

    def test_direction_requires_a_complete_window_and_benchmark_alignment(self):
        index = pd.date_range('2026-11-24',periods=13,freq='5min',tz='UTC')
        frame = pd.DataFrame(dict(quote=100,taker_quote=60,r1=.05),index=index)
        btc = pd.DataFrame(dict(close=[100,102],trades=[3,4]),index=index[[0,-1]])
        result = market_flow(frame,btc)
        self.assertEqual(result['taker_buy_15m'],.6)
        self.assertAlmostEqual(result['relative_1h'],.03)
        frame['taker_quote'] = frame['quote']
        self.assertNotIn('taker_buy_15m',market_flow(frame,btc,check_alpha_direction=True))
        frame.iloc[-2,1] = float('nan')
        self.assertNotIn('taker_buy_15m',market_flow(frame,btc))
        self.assertNotIn('btc_1h',market_flow(frame,btc.iloc[-1:]))

    def test_book_truncation_and_watchlist_are_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            radar = AlphaRadar(folder,threading.Event())
            radar.catalog = spot_catalog([dict(baseAsset='BTC',quoteAsset='USDT',symbol='BTCUSDT',
                                               status='TRADING',isSpotTradingAllowed=True)])
            radar.configure('watch',dict(pair='BTCUSDT',enabled=True))
            row = book_evidence({'bids':[['99.9','2']],'asks':[['100.1','1']]},'BTCUSDT',1,1000)
            self.assertFalse(row['bid_complete'])
            self.assertAlmostEqual(row['spread_bps'],20)
            radar.store.save_book('BTCUSDT',row)
            detail = radar.market_detail('BTCUSDT')
            self.assertTrue(detail['watched'])
            self.assertEqual(detail['books'],[row])
            self.assertEqual(AlphaRadar(folder,threading.Event()).prefs['watched'],['BTCUSDT'])

    def test_observation_routes_hide_strategy_api(self):
        radar = Mock()
        request = SimpleNamespace(radar=radar,local_host=lambda:'127.0.0.1:8769',
                                  reply=Mock(),send_error=Mock(),json=Mock(),path='/alpha')
        RadarHandler.do_GET(request)
        body = request.reply.call_args.args[0]
        self.assertIn(b'data-radar-only="true"',body)
        request.path = '/api/signals'
        RadarHandler.do_GET(request)
        request.send_error.assert_called_once_with(404)
        radar.snapshot.assert_not_called()


if __name__=='__main__':
    unittest.main()
