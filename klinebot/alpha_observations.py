"""Research observations from closed candles and verified perpetual markets."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .alt_monitor_core import BAR_MS, TOKENS, safe


PAIRED_TOKENS = {
    **TOKENS,
    'IRYS': {'alpha': 'ALPHA_479', 'contract': '0x91152b4ef635403efbae860edd0f8c321d7c035d'},
    'AIN': {'alpha': 'ALPHA_260', 'contract': '0x9558a9254890b2a8b057a789f413631b9084f4a3'},
    'Q': {'alpha': 'ALPHA_349', 'contract': '0xc07e1300dc138601fa6b0b59f8d0fa477e690589'},
}
VERSION = 'alpha-observations-v1'
BARS_PER_DAY = 288
BASELINE = 7 * BARS_PER_DAY
STEP = pd.Timedelta(milliseconds=BAR_MS)
RULES = (
    'low_range', 'breakout_up', 'breakout_down', 'spot_volume', 'perp_volume',
    'both_volume', 'spot_thin', 'price_oi_divergence', 'high_pullback', 'perp_discount',
)


def _window(frame, size, before=0):
    selected = frame.iloc[len(frame) - before - size:len(frame) - before]
    if len(selected) != size or selected[['high', 'low', 'close', 'quote']].isna().any().any():
        return None
    return selected


def _turnover(frame, bars):
    sums = frame.quote.rolling(bars).sum()
    previous = sums.iloc[-BASELINE - bars:-bars]
    current = sums.iloc[-1]
    if len(previous) != BASELINE or not previous.notna().all():
        return current, np.nan, np.nan
    median = previous.median()
    return current, current / median if median > 0 else np.nan, previous.quantile(.9)


def observations(spot, state, perp=None, oi=None, paired_symbol=None, paired_error=''):
    rules = dict.fromkeys(RULES)
    metrics = {}
    aligned = False
    research = dict(version=VERSION, rules=rules, metrics=metrics, paired_symbol=paired_symbol,
                    paired_fresh=False, paired_error=paired_error)
    if spot.empty or not state.get('fresh') or not state.get('active'):
        return research

    spot = spot.tail(8 * BARS_PER_DAY)
    at = spot.index[-1]
    price = float(spot.close.iloc[-1])
    spot_1h, spot_ratio, _ = _turnover(spot, 12)
    metrics.update(spot_quote_1h=spot_1h, spot_ratio=spot_ratio)
    box = _window(spot, 24, before=1)
    v15, ratio15, p90 = _turnover(spot, 3)
    metrics.update(spot_quote_15m=v15, spot_ratio_15m=ratio15, spot_quote_15m_p90=p90)
    if box is not None:
        top, bottom = box.high.max(), box.low.min()
        metrics.update(breakout_high=top, breakout_low=bottom)
        if np.isfinite(ratio15) and np.isfinite(p90):
            volume_ok = ratio15 >= 2 and v15 >= p90
            rules['breakout_up'] = volume_ok and top < price <= top * 1.03
            rules['breakout_down'] = volume_ok and bottom * .97 <= price < bottom
            target = 2 * top - bottom if rules['breakout_up'] else 2 * bottom - top if rules['breakout_down'] else None
            if target and target > 0 and (target > price if rules['breakout_up'] else target < price):
                metrics['projection'] = target

    recent_box = _window(spot, 48)
    prior = _window(spot, BASELINE, before=48)
    if recent_box is not None and prior is not None:
        upper, lower = recent_box.high.max(), recent_box.low.min()
        width = upper / lower - 1
        highs = prior.high.to_numpy().reshape(-1, 48).max(axis=1)
        lows = prior.low.to_numpy().reshape(-1, 48).min(axis=1)
        hist_width = np.quantile(highs / lows - 1, .30)
        prior_high, prior_low = highs.max(), lows.min()
        if prior_high > prior_low:
            position = ((upper + lower) / 2 - prior_low) / (prior_high - prior_low)
            box_quote = recent_box.quote.sum()
            metrics.update(range_high=upper, range_low=lower, range_width=width,
                           range_width_limit=min(.03, hist_width), range_position=position,
                           range_active_bars=int(recent_box.active.sum()),
                           range_largest_trade_share=recent_box.quote.max() / box_quote if box_quote > 0 else np.nan)
            rules['low_range'] = (0 <= position <= .25 and 0 < width <= min(.03, hist_width)
                                  and recent_box.active.sum() >= 36 and recent_box.quote.max() <= .4 * box_quote)

    day = _window(spot, BARS_PER_DAY + 1)
    if day is not None:
        high = day.close.iloc[1:].where(day.active.iloc[1:]).max()
        rise = high / day.close.iloc[0] - 1
        pullback = price / high - 1
        metrics.update(rally_from_prior_close=rise, pullback_from_high=pullback)
        rules['high_pullback'] = rise >= .20 and pullback <= -.08

    if perp is not None and not perp.empty:
        perp = perp.reindex(spot.index)
        row = perp.iloc[-1]
        aligned = bool(row.close > 0 and row.quote > 0 and row.trades > 0)
        if aligned:
            perp_1h, perp_ratio, _ = _turnover(perp, 12)
            basis = row.close / price - 1
            metrics.update(perp_quote_1h=perp_1h, perp_ratio=perp_ratio,
                           volume_ratio=perp_1h / spot_1h if spot_1h > 0 else np.nan, basis=basis)
            rules['perp_discount'] = basis <= -.03
            if np.isfinite(perp_ratio) and np.isfinite(spot_ratio):
                rules.update(spot_volume=spot_ratio >= 2 and perp_ratio < 2,
                             perp_volume=perp_ratio >= 2 and spot_ratio < 2,
                             both_volume=spot_ratio >= 2 and perp_ratio >= 2,
                             spot_thin=spot_ratio <= .5 and perp_ratio >= 1)

    if paired_symbol and oi is not None and not oi.empty and len(spot) >= 49:
        enough = _window(spot, 49)
        if enough is not None:
            ends = pd.DatetimeIndex([at - 48 * STEP, at]) - STEP
            quantities = oi.loc[~oi.index.duplicated(keep='last')].sort_index()
            prev, last = quantities.reindex(ends, method='ffill', tolerance=3 * STEP)
            if prev > 0 and last > 0:
                price_change = price / enough.close.iloc[0] - 1
                oi_change = last / prev - 1
                metrics.update(price_4h=price_change, oi_4h=oi_change)
                rules['price_oi_divergence'] = price_change >= .05 and oi_change <= -.05
    research['paired_fresh'] = aligned
    return safe(research)
