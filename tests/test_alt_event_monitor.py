from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from klinebot.alt_monitor_core import (BAR_MS, DAY_MS, ROOT, TOKENS, Store, evaluate_latest,
                                      feature_frame, market_profile, now_ms, outcome, parse_bar)
from klinebot.alt_monitor_data import Monitor, PublicDataError
from klinebot.alpha_catalog import alpha_catalog, spot_catalog


def sample(days=34, end=None, jump=False):
    end=end or now_ms()//BAR_MS*BAR_MS
    ix=pd.date_range(pd.Timestamp(end-(days*288-1)*BAR_MS,unit='ms',tz='UTC'),periods=days*288,freq='5min')
    rng=np.random.default_rng(12)
    c=100*np.exp(np.cumsum(rng.normal(0,.0003,len(ix))))
    v=rng.uniform(80,120,len(ix))
    if jump:
        c[-12:]*=1.3;v[-12:]*=20
    return pd.DataFrame({'open':c,'high':c*1.001,'low':c*.999,'close':c,'base':v,'quote':v*c,'trades':10},index=ix)


def rows(d):
    return [dict(end_ms=int(t.timestamp()*1000),**r.to_dict()) for t,r in d.iterrows()]


class RuleTests(unittest.TestCase):
    def test_closed_bar_filter_and_validation(self):
        raw=[0,'100','101','99','100','2',BAR_MS-1,'200',2,'1','100','0']
        self.assertIsNone(parse_bar(raw,BAR_MS-1))
        self.assertEqual(parse_bar(raw,BAR_MS)['quote'],200)
        broken=raw.copy();broken[2]='90'
        with self.assertRaises(ValueError):parse_bar(broken,BAR_MS)
        broken=raw.copy();broken[4]='nan'
        with self.assertRaises(ValueError):parse_bar(broken,BAR_MS)

    def test_price_volume_signals_do_not_require_or_fabricate_derivatives(self):
        d=sample(jump=True);f=feature_frame(d)
        self.assertTrue(f.ae_ready.iloc[-1]);self.assertTrue(f.ae_signal.iloc[-1])
        self.assertFalse(f.core_ready.iloc[-1]);self.assertFalse(f.core_signal.iloc[-1])
        state=evaluate_latest(f,int(d.index[-1].timestamp()*1000))
        self.assertIsNone(state['conditions']['OB']);self.assertIsNone(state['conditions']['C'])
        self.assertIsNone(state['dOI']);self.assertIsNone(state['F'])

    def test_prefix_invariance(self):
        d=sample(days=36);cut=d.index[-288]
        full=feature_frame(d)
        prefix=feature_frame(d.loc[:cut])
        pd.testing.assert_frame_equal(full.loc[:cut],prefix)
        changed=d.copy();changed.loc[changed.index>cut,['open','high','low','close','base','quote']]*=20
        pd.testing.assert_frame_equal(full.loc[:cut],feature_frame(changed).loc[:cut])

    def test_stale_and_zero_trade_do_not_alert(self):
        d=sample(jump=True);f=feature_frame(d);t=int(d.index[-1].timestamp()*1000)
        self.assertFalse(evaluate_latest(f,t+3*BAR_MS)['ae_signal'])
        d.iloc[-1,d.columns.get_loc('base')]=0
        d.iloc[-1,d.columns.get_loc('quote')]=0
        d.iloc[-1,d.columns.get_loc('trades')]=0
        self.assertFalse(evaluate_latest(feature_frame(d),t)['ae_ready'])

    def test_old_fee_cannot_appear_as_fresh_evidence(self):
        d=sample(jump=True);f=feature_frame(d)
        f.loc[f.index[-1],'C']=True
        f.loc[f.index[-1],'F']=.01
        state=evaluate_latest(f,int(d.index[-1].timestamp()*1000))
        self.assertNotIn('已结算费率异常',state['triggers'])
        self.assertIsNone(state['F'])

    def test_oi_delay(self):
        d=sample();oi=pd.Series(np.arange(len(d))+1000.,index=d.index)
        f=feature_frame(d,oi)
        self.assertEqual(f.oi.iloc[-1],oi.iloc[-2])

    def test_vwap_exact_weighting_and_profile(self):
        d=sample()
        d['base']=1.;d['quote']=100.;d[['open','high','low','close']]=100.
        d.iloc[-7*288//2:,d.columns.get_loc('base')]=3.
        d.iloc[-7*288//2:,d.columns.get_loc('quote')]=600.
        f=feature_frame(d)
        self.assertAlmostEqual(f.vwap7.iloc[-1],175.)
        poc,lo,hi=market_profile(d.tail(30*288))
        self.assertIsNotNone(poc);self.assertLessEqual(lo,poc);self.assertLessEqual(poc,hi)

    def test_historical_rule_parity_if_cache_available(self):
        p=ROOT/'data/alt_event_four_20261118/RAVEUSDT_features.pkl'
        if not p.exists():self.skipTest('Optional verified historical cache unavailable')
        original=pd.read_pickle(p).tail(34*288)
        actual=feature_frame(original[['open','high','low','close','base','quote','trades']])
        cols=['V','A','E','r1q95']
        pd.testing.assert_frame_equal(actual[cols].tail(288),original[cols].tail(288),check_freq=False)


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.t=1793491200000  # completed5m boundary
        self.f=sample(days=1,end=self.t+288*BAR_MS)
        self.f[['open','high','low','close']]=100.

    def call(self,hours=3,asof=None,q=.2):
        return outcome(self.f,self.t,100.,q,hours,asof or self.t+hours*3_600_000)

    def test_wick_alone_not_event(self):
        self.f.iloc[3,self.f.columns.get_loc('high')]=150
        self.assertEqual(self.call()['status'],'未达标')

    def test_three_positive_closes_and_known_partial_success(self):
        self.f.iloc[3:6,self.f.columns.get_loc('close')]=121
        r=self.call(asof=self.t+10*BAR_MS)
        self.assertEqual(r['status'],'上涨已达标');self.assertFalse(r['complete'])
        self.assertEqual(r['up']['at'],int(self.f.index[5].timestamp()*1000))

    def test_stale_carried_prices_not_confirmation(self):
        self.f['close']=130.
        self.f[['base','quote','trades']]=0
        self.assertFalse(self.call()['hit'])

    def test_gap_never_becomes_false_negative(self):
        self.f=self.f.drop(self.f.index[6])
        r=self.call()
        self.assertEqual(r['status'],'数据缺口');self.assertFalse(r['complete'])

    def test_ongoing_no_hit_is_pending(self):
        self.assertEqual(self.call(asof=self.t+BAR_MS)['status'],'待观察')

    def test_exact_threshold_and_window_boundary(self):
        self.f.iloc[33:36,self.f.columns.get_loc('close')]=110.
        r=self.call(q=.1)
        self.assertTrue(r['hit']);self.assertTrue(r['complete'])
        self.f.iloc[33,self.f.columns.get_loc('close')]=100.
        self.assertFalse(self.call(q=.1)['hit'])

    def test_two_sided_events(self):
        self.f.iloc[3:6,self.f.columns.get_loc('close')]=121.
        self.f.iloc[9:12,self.f.columns.get_loc('close')]=79.
        self.assertEqual(self.call()['status'],'双向已达标')


class PersistenceTests(unittest.TestCase):
    def test_restart_dedup_freeze_and_model_separation(self):
        with tempfile.TemporaryDirectory() as temp:
            t=1793491200000
            a=Store(Path(temp));a.record('RAVE','ae',t,t+1000,100,.2,{'triggers':['现货放量']})
            b=Store(Path(temp))
            self.assertIsNone(b.record('RAVE','ae',t,t+2000,101,.1,{}))
            self.assertIsNone(b.record('RAVE','ae',t+DAY_MS-BAR_MS,t+DAY_MS,100,.1,{}))
            self.assertIsNotNone(b.record('RAVE','core',t,t+2000,100,.3,{}))
            self.assertIsNotNone(b.record('RAVE','ae',t+DAY_MS,t+DAY_MS+1000,100,.1,{}))
            first=[r for r in b.alerts() if r['bar_ms']==t and r['model']=='ae'][0]
            self.assertEqual(first['threshold'],.2);self.assertEqual(first['price'],100)
            export=Path(temp)/'alerts.csv';b.export(export)
            self.assertTrue(export.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_invalid_or_future_alert_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp))
            with self.assertRaises(ValueError):s.record('RAVE','ae',BAR_MS,BAR_MS-1,100,.2,{})
            with self.assertRaises(ValueError):s.record('RAVE','ae',BAR_MS,BAR_MS,float('nan'),.2,{})

    def test_tracker_preserves_gap_and_completes_after_backfill(self):
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));t=1793491200000
            s.record('RAVE','ae',t,t+1000,100,.2,{})
            d=sample(days=3,end=t+3*DAY_MS);d[['open','high','low','close']]=100.
            missing=d.index[5];s.save_bars('RAVE',rows(d.drop(missing)))
            s.track(t+3*DAY_MS)
            self.assertEqual(s.alerts()[0]['outcomes']['72']['status'],'数据缺口')
            s.save_bars('RAVE',rows(d.loc[[missing]]));s.track(t+3*DAY_MS)
            self.assertEqual(s.alerts()[0]['outcomes']['72']['status'],'未达标')


class FakeClient:
    def __init__(self,on_alpha=None):self.on_alpha=on_alpha
    def identities(self):return {s:dict(alpha=v['alpha']) for s,v in TOKENS.items()}
    def alpha(self,*args):
        if self.on_alpha:self.on_alpha()
    def derivatives(self,*args):raise PublicDataError('HTTP451',451)


class ServiceTests(unittest.TestCase):
    def test_market_directories_preserve_source_chain_and_quote(self):
        def pair(base, quote, **changes):
            return dict(symbol=base + quote, baseAsset=base, quoteAsset=quote,
                        status='TRADING', isSpotTradingAllowed=True) | changes

        tokens = [dict(alphaId='ALPHA_50', symbol='LIVE', chainId='SOL', contractAddress='SolanaAddress')]
        alpha = alpha_catalog(tokens, [pair('ALPHA_50', 'USDC'), pair('ALPHA_50', 'USDT')])
        spot = spot_catalog([pair('LIVE', 'USDC'), pair('LIVE', 'USDT'), pair('ALT', 'BTC'),
                             pair('INACTIVE', 'USDT', status='BREAK')])
        self.assertEqual(set(alpha) | set(spot), {'ALPHA_50USDT', 'SPOT:LIVEUSDT', 'SPOT:ALTBTC'})
        self.assertEqual(alpha['ALPHA_50USDT']['contract'], 'SolanaAddress')
        self.assertEqual(spot['SPOT:LIVEUSDT']['source'], 'spot')

    def test_market_rotation_resumes_and_spot_alerts_use_market_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp), allow_market_pairs=True)
            pairs = {f'SPOT:COIN{i}USDT': {'symbol': f'COIN{i}'} for i in range(27)}
            store.set('alpha-market-catalog', pairs)
            store.set('alpha-scan-cursor', 24)
            monitor = Monitor(store, universe=True)
            monitor.identity_at = now_ms()
            keys = list(monitor.catalog)
            batch, cursor = monitor._universe_targets(None)
            self.assertEqual(cursor, 24)
            self.assertEqual(batch, keys[24:] + keys[:21])
            at = now_ms() // BAR_MS * BAR_MS
            alert_id = store.record(keys[24], 'ae', at, at, 1, .2, {'triggers': ['现货放量']})
            self.assertEqual(store.alerts(symbol=keys[24])[0]['id'], alert_id)

    def test_derivatives_failure_leaves_ae_independent_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));end=(now_ms()-20000)//BAR_MS*BAR_MS
            d=sample(end=end,jump=True)
            data=rows(d)
            for symbol in TOKENS:store.save_bars(symbol,data)
            events=[];m=Monitor(store,events.append,FakeClient())
            m.scan();m.worker.join(timeout=10)
            alerts=store.alerts()
            self.assertEqual(len(alerts),4)
            self.assertTrue(all(a['model']=='ae' for a in alerts))
            self.assertTrue(all(not r['core_ready'] for r in m.rows.values()))
            m.scan();m.worker.join(timeout=10);self.assertEqual(len(store.alerts()),4)

    def test_pause_during_request_cancels_new_alerts(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp));m=Monitor(store,client=FakeClient())
            m.client.on_alpha=m.pause
            m.lock.acquire();m._scan()
            self.assertFalse(store.alerts());self.assertFalse(m.running)


if __name__=='__main__':unittest.main()
