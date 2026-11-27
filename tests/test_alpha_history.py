import tempfile
import threading
import unittest

from klinebot.alpha_history import EventHistory
from klinebot.alpha_radar import AlphaRadar
from klinebot.alt_monitor_core import BAR_MS, DAY_MS, Store, now_ms, payload


def candle(at, price, traded=True):
    return dict(end_ms=at, open=price, high=price, low=price, close=price,
                base=int(traded), quote=price if traded else 0, trades=int(traded))


class EventHistoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(folder.name,allow_market_pairs=True)
        self.history = EventHistory(self.store)
        self.at = (now_ms()-5*DAY_MS)//BAR_MS*BAR_MS
        self.market = 'SPOT:BTCUSDT'
        self.evidence = dict(source='spot',display_symbol='BTC',pair='BTCUSDT',quote='USDT',triggers=['现货放量'])

    def record(self, model='ae'):
        return self.store.record(self.market,model,self.at,self.at+1000,100,.2,self.evidence)

    def test_groups_models_and_keeps_recent_results_after_raw_cleanup(self):
        key = self.record()
        self.record('core')
        samples = [candle(self.at+n*BAR_MS,122 if 2<=n<=4 else 90 if n==5 else 106 if n==12 else 100)
                   for n in range(1,289)]
        self.store.save_bars(self.market,samples)
        self.store.track(self.at+3*DAY_MS,symbol=self.market)
        self.history.refresh(self.market,self.at+DAY_MS)
        page = self.history.page(source='spot',model='core',threshold=.2,query='btc')
        self.assertEqual(page['total'],1)
        row = self.history.event(key)
        self.assertEqual(row['models'],['ae','core'])
        self.assertAlmostEqual(row['windows']['1']['change'],.06)
        self.assertAlmostEqual(row['windows']['24']['high'],.22)
        self.assertAlmostEqual(row['windows']['24']['low'],-.10)
        self.assertEqual(row['results'][72]['up']['at'],self.at+4*BAR_MS)
        self.assertTrue(row['results'][72]['has_gaps'])
        self.assertEqual(page['totals'][72],dict(matured=1,complete=0,reached=0))

        previous = row['path']
        self.store.prune(self.market,self.at+2*DAY_MS)
        self.history.refresh(self.market,self.at+3*DAY_MS)
        self.assertEqual(self.history.event(key)['path'],previous)

    def test_missing_and_zero_trade_endpoints_remain_distinct(self):
        key = self.record()
        self.store.save_bars(self.market,[candle(self.at+BAR_MS,125),candle(self.at+12*BAR_MS,125,False)])
        self.history.refresh(self.market,self.at+6*3600000)
        windows = self.history.event(key)['windows']
        self.assertTrue(windows['1']['terminal_no_trade'])
        self.assertIsNone(windows['1']['change'])
        self.assertFalse(windows['6']['terminal_no_trade'])
        self.assertIsNone(windows['6']['change'])
        self.assertEqual(windows['1']['available'],2)
        self.assertFalse(windows['24']['closed'])

    def test_archive_includes_cleared_live_events_and_counts_only_complete_windows(self):
        key = self.record()
        with self.store.connect() as db:
            db.execute('INSERT INTO outcomes VALUES(?,?,?)', (key,72,payload({'complete':True,'hit':True})))
        radar = AlphaRadar(self.store.directory,threading.Event())
        radar.configure('clear',True)
        self.assertEqual(radar.snapshot()['alerts'],[])
        page = radar.history.page()
        self.assertEqual(page['rows'][0]['id'],key)
        self.assertEqual(page['totals'][72],dict(matured=1,complete=1,reached=1))
        self.assertEqual(radar.history.page(source='alpha')['total'],0)


if __name__=='__main__':
    unittest.main()
