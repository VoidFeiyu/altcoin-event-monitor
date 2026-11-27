"""Causal, descriptive market details from already-collected public candles."""
import math

import numpy as np
import pandas as pd

from .alt_monitor_core import BAR_MS, safe


def market_flow(frame, btc, *, check_alpha_direction=False):
    if frame.empty:
        return {}
    result = {}
    for count, label in ((3,'15m'),(12,'1h')):
        bars = frame.tail(count)
        if len(bars)==count and 'taker_quote' in bars and bars[['quote','taker_quote']].notna().all().all():
            total = bars.quote.sum()
            if total>0:
                buy = bars.taker_quote.sum()
                if check_alpha_direction and math.isclose(buy,total,rel_tol=1e-9):
                    result['direction_note'] = 'Alpha 主动买入字段与总成交额一致，方向待核对'
                else:
                    result['taker_buy_'+label] = float(buy/total)
    if btc is not None and len(btc)==2 and btc.index[-1]-btc.index[0]==pd.Timedelta(hours=1) and (btc.trades>0).all():
        benchmark = btc.close.iloc[-1]/btc.close.iloc[0]-1
        result['btc_1h'] = float(benchmark)
        if 'r1' in frame and np.isfinite(frame.r1.iloc[-1]):
            result['relative_1h'] = float(frame.r1.iloc[-1]-benchmark)
    return result


def book_evidence(book, pair, limit, received):
    if book.get('symbol',pair)!=pair:
        raise ValueError('盘口交易对不匹配')
    sides = []
    for side in ('bids','asks'):
        levels = [(float(price),float(quantity)) for price,quantity in book[side]]
        if not levels or any(not math.isfinite(p) or not math.isfinite(q) or min(p,q)<=0 for p,q in levels):
            raise ValueError('盘口档位缺失或无效')
        sides.append(sorted(levels,reverse=side=='bids'))
    bids,asks = sides
    bid,ask = bids[0][0],asks[0][0]
    if bid>=ask:
        raise ValueError('盘口买卖报价交叉')
    at = int(book.get('E') or received)
    if at<=0 or at>received+5000:
        raise ValueError('盘口时间无效')
    mid = (bid+ask)/2
    low,high = mid*.99,mid*1.01
    return dict(at=at,received=received,spread_bps=(ask-bid)/mid*10000,bid=bid,ask=ask,
                bid_depth=sum(p*q for p,q in bids if p>=low),ask_depth=sum(p*q for p,q in asks if p<=high),
                bid_complete=len(bids)<limit or bids[-1][0]<=low,
                ask_complete=len(asks)<limit or asks[-1][0]>=high)

CHART_MINUTES = {1: 5, 3: 15, 7: 30, 30: 120}


def market_analysis(f, state):
    if f.empty:
        return {'metrics': {}, 'rules': [], 'coverage': {}}
    r = f.iloc[-1]

    def number(key):
        value = r.get(key)
        return float(value) if value is not None and pd.notna(value) and math.isfinite(float(value)) else None

    def boundary(a, b):
        return max(a, b) if a is not None and b is not None else None

    spot_ready = bool(state.get('fresh') and state.get('active') and state.get('ae_ready'))
    core_ready = spot_ready and bool(state.get('core_ready')) and not state.get('derivative_error')
    vmed = number('vmed')
    volume_line = boundary(2*vmed if vmed is not None else None, number('vq95'))
    oi_line = max(.1, number('oiq95')) if number('oiq95') is not None else None
    conditions = state.get('conditions', {})
    metrics = {key: number(key) for key in ('open', 'high', 'low', 'close', 'base', 'quote', 'trades',
                                           'v1', 'vmed', 'vq95', 'r1q95')}
    metrics.update(volume_threshold=volume_line,
                   trades1h=float(f.trades.tail(12).sum()) if len(f) >= 12 and f.trades.tail(12).notna().all() else None)
    for key in ('oi', 'dOI', 'oiq95', 'F', 'q95', 'q05', 'hours'):
        metrics[key] = number(key) if core_ready else None
    metrics['oi_threshold'] = oi_line if core_ready else None
    metrics['settled_rate'] = metrics['F']*metrics['hours'] if metrics['F'] is not None and metrics['hours'] is not None else None
    known = r.get('known_at')
    metrics['funding_known_ms'] = int(known.timestamp()*1000) if core_ready and pd.notna(known) else None
    tail = f.tail(30*288)
    coverage = dict(ohlc_fraction_30d=float(np.isfinite(tail[['open', 'high', 'low', 'close']]).all(axis=1).sum()/(30*288)),
                    active_fraction_available=float(tail.active.fillna(False).mean()),
                    available_5m=int(tail.close.notna().sum()), expected_30d_5m=30*288)
    rules = []
    for key, name, observed, threshold, ready, unit in [
        ('A', '现货放量', metrics['v1'], volume_line, spot_ready, 'quote'),
        ('E', '价格异常', abs(number('r1')) if number('r1') is not None else None, number('r1q95'), spot_ready, 'percent'),
        ('OB', 'OI 数量异常增加', metrics['dOI'], metrics['oi_threshold'], core_ready, 'percent'),
        ('C', '已结算费率尾部异常', metrics['F'], None, core_ready, 'funding'),
    ]:
        rules.append(dict(key=key, name=name, observed=observed, threshold=threshold, unit=unit,
                          passed=bool(conditions.get(key)) if ready and conditions.get(key) is not None else None))
    return safe(dict(metrics=metrics, rules=rules, coverage=coverage))


def chart_series(raw, days):
    """Aggregate only observed closed candles; timestamps never extend into future."""
    interval = CHART_MINUTES[days]
    expected = interval//5
    points = []
    if not raw.empty:
        previous = None
        for _, g in raw.resample(f'{interval}min', closed='right', label='right'):
            g = g.dropna(subset=['open', 'high', 'low', 'close'])
            if g.empty:
                continue
            at = int(g.index[-1].timestamp()*1000)
            inside_gap = bool(g.index.to_series().diff().dropna().gt(pd.Timedelta(minutes=5)).any())
            gap = inside_gap or (previous is not None and int(g.index[0].timestamp()*1000)-previous > BAR_MS)
            points.append(dict(time=at, open=float(g.open.iloc[0]), high=float(g.high.max()),
                               low=float(g.low.min()), close=float(g.close.iloc[-1]),
                               quote=float(g.quote.sum()), trades=int(g.trades.sum()),
                               gap=gap, partial=len(g) < expected))
            previous = at
    return safe(dict(points=points, interval_minutes=interval,
                     has_gaps=any(p['gap'] for p in points),
                     actual_first_ms=int(raw.index[0].timestamp()*1000) if len(raw) else None,
                     actual_last_ms=int(raw.index[-1].timestamp()*1000) if len(raw) else None))
