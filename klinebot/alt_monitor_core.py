"""Read-only altcoin event rules, persistence and observable outcomes.

No order/account client. Public market data only. Missing derivatives never become
zero-valued evidence. A&E and the full A/OB/C/E rule are separate models.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data' / 'alt_event_monitor'
BAR_MS = 300_000
DAY_MS = 86_400_000
WARM = 30 * 288
VERSION = 'alpha-events-v1'
TOKENS = {
    'RAVE': {'alpha': 'ALPHA_497', 'contract': '0x97693439ea2f0ecdeb9135881e49f354656a911c'},
    'BSB': {'alpha': 'ALPHA_790', 'contract': '0x595deaad1eb5476ff1e649fdb7efc36f1e4679cc'},
    'EVAA': {'alpha': 'ALPHA_409', 'contract': '0xaa036928c9c0df07d525b55ea8ee690bb5a628c1'},
    'LAB': {'alpha': 'ALPHA_428', 'contract': '0x7ec43cf65f1663f820427c62a5780b8f2e25593a'},
}
LABELS = {'A': '现货放量', 'OB': 'OI异常增加', 'C': '已结算费率异常', 'E': '短线涨跌异常'}


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def payload(value: Any) -> str:
    return json.dumps(safe(value), ensure_ascii=False, allow_nan=False)


def parse_bar(row: list, asof: int) -> dict | None:
    if len(row) < 11:
        raise ValueError('现货K线字段不完整')
    start = int(row[0])
    end = start + BAR_MS
    if int(row[6]) != end - 1 or start % BAR_MS:
        raise ValueError('现货K线时间边界异常')
    if end > asof:
        return None  # An unfinished candle never enters features or outcomes.
    values = [float(row[i]) for i in (1, 2, 3, 4, 5, 7)]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('现货K线包含无效数值')
    o, h, l, c, base, quote = values
    if min(o, h, l, c) <= 0 or h < max(o, l, c) or l > min(o, h, c) or min(base, quote) < 0:
        raise ValueError('现货OHLC或成交量异常')
    trades = int(row[8])
    if trades < 0:
        raise ValueError('成交笔数异常')
    return dict(end_ms=end, open=o, high=h, low=l, close=c, base=base, quote=quote, trades=trades)


class Store:
    def __init__(self, directory: Path = DIRECTORY, allow_market_pairs=False):
        self.directory = Path(directory)
        self.allow_market_pairs = allow_market_pairs
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'monitor.sqlite3'
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bars(
                    symbol TEXT, end_ms INTEGER, open REAL, high REAL, low REAL, close REAL,
                    base REAL, quote REAL, trades INTEGER, source TEXT,
                    PRIMARY KEY(symbol,end_ms));
                CREATE TABLE IF NOT EXISTS oi(symbol TEXT,time_ms INTEGER,quantity REAL,source TEXT,
                    PRIMARY KEY(symbol,time_ms));
                CREATE TABLE IF NOT EXISTS funding(symbol TEXT,time_ms INTEGER,rate REAL,hours REAL,source TEXT,
                    PRIMARY KEY(symbol,time_ms));
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE IF NOT EXISTS alerts(
                    id TEXT PRIMARY KEY,symbol TEXT,model TEXT,bar_ms INTEGER,observed_ms INTEGER,
                    price REAL,threshold REAL,evidence TEXT,version TEXT);
                CREATE INDEX IF NOT EXISTS alert_time ON alerts(bar_ms DESC);
                CREATE INDEX IF NOT EXISTS alert_symbol_time ON alerts(symbol,bar_ms DESC);
                CREATE TABLE IF NOT EXISTS outcomes(alert_id TEXT,hours INTEGER,result TEXT,
                    PRIMARY KEY(alert_id,hours));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def set(self, key: str, value: Any):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, payload(value)))

    def get(self, key: str, default=None):
        with self.connect() as db:
            r = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(r['value']) if r else default

    def snapshots(self):
        with self.connect() as db:
            rows = db.execute("SELECT key,value FROM state WHERE key GLOB 'snapshot:*'").fetchall()
        return {row['key'].removeprefix('snapshot:'): json.loads(row['value']) for row in rows}

    def save_bars(self, symbol: str, rows: list[dict], source='alpha-live'):
        if not rows:
            return
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)', [
                (symbol, r['end_ms'], r['open'], r['high'], r['low'], r['close'], r['base'], r['quote'], r['trades'], source)
                for r in rows])

    def save_oi(self, symbol: str, rows: list[tuple], source='public-api'):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO oi VALUES(?,?,?,?)', [(symbol, int(t), float(q), source) for t, q in rows])

    def save_funding(self, symbol: str, rows: list[tuple], source='settled-history'):
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO funding VALUES(?,?,?,?,?)', [
                (symbol, int(t), float(rate), float(hours), source) for t, rate, hours in rows])

    def maximum(self, table: str, symbol: str) -> int | None:
        if table not in ('bars', 'oi', 'funding'):
            raise ValueError('无效数据表')
        column = 'end_ms' if table == 'bars' else 'time_ms'
        with self.connect() as db:
            r = db.execute(f'SELECT MAX({column}) AS t FROM {table} WHERE symbol=?', (symbol,)).fetchone()
        return r['t']

    def bars(self, symbol: str, start: int = 0, end: int | None = None) -> pd.DataFrame:
        with self.connect() as db:
            rows = db.execute('SELECT end_ms,open,high,low,close,base,quote,trades FROM bars WHERE symbol=? AND end_ms>=? AND end_ms<=? ORDER BY end_ms',
                              (symbol, int(start), int(end or 9_000_000_000_000))).fetchall()
        cols = ['end_ms', 'open', 'high', 'low', 'close', 'base', 'quote', 'trades']
        d = pd.DataFrame([tuple(r) for r in rows], columns=cols)
        if d.empty:
            return pd.DataFrame(columns=cols[1:], index=pd.DatetimeIndex([], tz='UTC'))
        d.index = pd.to_datetime(d.pop('end_ms'), unit='ms', utc=True)
        return d

    def derivatives(self, symbol: str, start: int, end: int) -> tuple[pd.Series, pd.DataFrame]:
        with self.connect() as db:
            oi = db.execute('SELECT time_ms,quantity FROM oi WHERE symbol=? AND time_ms BETWEEN ? AND ? ORDER BY time_ms', (symbol, start, end)).fetchall()
            fu = db.execute('SELECT time_ms,rate,hours FROM funding WHERE symbol=? AND time_ms BETWEEN ? AND ? ORDER BY time_ms', (symbol, start, end)).fetchall()
        series = pd.Series([r['quantity'] for r in oi], index=pd.to_datetime([r['time_ms'] for r in oi], unit='ms', utc=True), dtype=float)
        fund = pd.DataFrame([tuple(r) for r in fu], columns=['time_ms', 'rate', 'hours'])
        return series, fund

    def record(self, symbol: str, model: str, at: int, observed: int, price: float, threshold: float, evidence: dict) -> str | None:
        valid_market = self.allow_market_pairs and re.fullmatch(r'(?:ALPHA_\d+(?:USDT|USDC|U)|SPOT:[^\s:]{1,48})', symbol)
        if (symbol not in TOKENS and not valid_market) or model not in ('ae', 'core') or at % BAR_MS or at > observed:
            raise ValueError('预警身份或时间无效')
        if not math.isfinite(price) or price <= 0 or threshold not in (.1, .2, .3, .5):
            raise ValueError('预警价格或门槛无效')
        key = f'{VERSION}:{symbol}:{model}:{at}'
        with self.connect() as db:
            previous = db.execute('SELECT MAX(bar_ms) AS t FROM alerts WHERE symbol=? AND model=? AND version=?', (symbol, model, VERSION)).fetchone()['t']
            if previous is not None and at < previous + DAY_MS:
                return None
            cur = db.execute('INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?,?,?,?)',
                             (key, symbol, model, at, observed, price, threshold, payload(evidence), VERSION))
        return key if cur.rowcount else None

    def alerts(self, limit=500, symbol=None, since=0) -> list[dict]:
        where, params = ('bar_ms>=?',(since,)) if symbol is None else ('symbol=? AND bar_ms>=?',(symbol,since))
        with self.connect() as db:
            rows = db.execute(f'SELECT * FROM alerts WHERE {where} ORDER BY bar_ms DESC LIMIT ?', (*params,limit)).fetchall()
            outcomes = db.execute(f'''SELECT * FROM outcomes WHERE alert_id IN
                (SELECT id FROM alerts WHERE {where} ORDER BY bar_ms DESC LIMIT ?)''',(*params,limit)).fetchall()
        results = {(r['alert_id'], r['hours']): json.loads(r['result']) for r in outcomes}
        out = []
        for r in rows:
            d = dict(r)
            d['evidence'] = json.loads(d['evidence'])
            d['outcomes'] = {str(h): results.get((r['id'], h), {'status': '待观察'}) for h in (72, 168, 336)}
            out.append(d)
        return out

    def track(self, asof: int, symbol=None):
        # Finished results are retained. Re-read all saved paths so late backfills can repair gaps.
        for alert in self.alerts(limit=10000,symbol=symbol,since=asof-16*DAY_MS if symbol else 0):
            if symbol and all(result.get('complete') for result in alert['outcomes'].values()):
                continue
            raw = self.bars(alert['symbol'], alert['bar_ms'] + BAR_MS, min(asof, alert['bar_ms'] + 14 * DAY_MS))
            for hours in (72, 168, 336):
                result = outcome(raw, alert['bar_ms'], alert['price'], alert['threshold'], hours, asof)
                with self.connect() as db:
                    db.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?,?)', (alert['id'], hours, payload(result)))

    def prune(self, symbol, before):
        with self.connect() as db:
            db.execute('DELETE FROM bars WHERE symbol=? AND end_ms<?', (symbol,before))

    def export(self, path: Path, *, translate=str):
        with Path(path).open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            headers = ('币种', '模型', '预警K线结束UTC', '实际扫描UTC', '预警价', '冻结幅度',
                       '触发依据', '3天结果', '7天结果', '14天结果', '规则版本', '市场标识', '计价币')
            writer.writerow(map(translate, headers))
            for a in self.alerts(100000):
                dt = lambda t: pd.Timestamp(t, unit='ms', tz='UTC').isoformat()
                market = a['evidence'].get('market', {})
                writer.writerow([market.get('symbol', a['symbol']), a['model'], dt(a['bar_ms']), dt(a['observed_ms']), a['price'], a['threshold'],
                                 translate('、').join(map(translate, a['evidence'].get('triggers', []))),
                                 *(translate(a['outcomes'][str(h)]['status']) for h in (72, 168, 336)), a['version'],
                                 a['symbol'], market.get('quote', 'USDT')])


def _asof(series: pd.Series, ix: pd.DatetimeIndex, prior=False):
    out = series.reindex(ix - pd.Timedelta(minutes=5) if prior else ix, method='ffill')
    out.index = ix
    return out


def feature_frame(bars: pd.DataFrame, oi: pd.Series | None = None, fund: pd.DataFrame | None = None) -> pd.DataFrame:
    if bars.empty:
        return bars.copy()
    ix = pd.date_range(bars.index.min(), bars.index.max(), freq='5min')
    f = bars.reindex(ix).copy()
    hourly = f.resample('1h', closed='right', label='right').agg({'close': 'last', 'quote': 'sum'})
    hourly.loc[f.close.resample('1h', closed='right', label='right').count() != 12, :] = np.nan
    rv = hourly.quote.rolling(720, min_periods=648)
    f['vmed'] = _asof(rv.median(), ix, True)
    f['vq95'] = _asof(rv.quantile(.95), ix, True)
    f['v1'] = f.quote.rolling(12, min_periods=12).sum()
    f['V'] = f.v1 / f.vmed
    f['r1'] = f.close.pct_change(12, fill_method=None)
    f['r24'] = f.close.pct_change(288, fill_method=None)
    f['r1q95'] = _asof(hourly.close.pct_change(fill_method=None).abs().rolling(720, min_periods=648).quantile(.95), ix, True)
    f['A'] = (f.V >= 2) & (f.v1 >= f.vq95)
    f['E'] = f.r1.abs() >= f.r1q95
    f['active'] = (f.base > 0) & (f.quote > 0) & (f.trades > 0)
    coverage = f.close.notna().shift().rolling(WARM, min_periods=WARM).mean() >= .9
    f['ae_ready'] = coverage & f.active & (f.vmed > 0) & np.isfinite(f[['V', 'r1q95']]).all(axis=1)
    f['dOI'], f['F'] = np.nan, np.nan
    f['OB'], f['C'], f['core_ready'] = False, False, False
    if oi is not None and not oi.empty:
        oi = oi.sort_index()[~oi.sort_index().index.duplicated(keep='last')].copy()
        oi.index += pd.Timedelta(minutes=5)
        f['oi'] = oi.reindex(ix, method='ffill', tolerance=pd.Timedelta(minutes=15))
        f['dOI'] = f.oi.pct_change(48, fill_method=None)
        f['oiq95'] = f.dOI.shift().rolling(WARM, min_periods=int(.9 * WARM)).quantile(.95)
        f['OB'] = (f.dOI >= .1) & (f.dOI >= f.oiq95)
        oi_ready = f.oi.notna().shift().rolling(WARM, min_periods=WARM).mean() >= .9
    else:
        oi_ready = pd.Series(False, index=ix)
    if fund is not None and not fund.empty:
        fu = fund.sort_values('time_ms').drop_duplicates('time_ms').copy()
        fu.index = pd.to_datetime(fu.time_ms, unit='ms', utc=True) + pd.Timedelta(minutes=5)
        fu['F'] = fu.rate / fu.hours
        fr = fu.F.rolling('30D', closed='left', min_periods=24)
        fu['q95'], fu['q05'] = fr.quantile(.95), fr.quantile(.05)
        fu['history_hours'] = fu.hours.rolling('30D', closed='left', min_periods=24).sum()
        fu['known_at'] = fu.index
        for name in ('F', 'q95', 'q05', 'hours', 'known_at', 'history_hours'):
            f[name] = fu[name].reindex(ix, method='ffill')
        fresh = (ix.to_series() - f.known_at).dt.total_seconds() <= f.hours * 5400
        f['C'] = ((f.F > 0) & (f.F >= f.q95)) | ((f.F < 0) & (f.F <= f.q05))
        needed = ['dOI', 'F', 'q95', 'q05'] + (['oiq95'] if 'oiq95' in f else [])
        f['core_ready'] = f.ae_ready & oi_ready & fresh & (f.history_hours >= 648) & np.isfinite(f[needed]).all(axis=1)
    f['score'] = f[['A', 'OB', 'C', 'E']].sum(axis=1)
    f['ae_signal'] = f.ae_ready & f.A & f.E
    f['core_signal'] = f.core_ready & (f.score >= 2) & (f.A | f.OB)
    for days in (7, 30):
        n = days * 288
        f[f'vwap{days}'] = f.quote.rolling(n, min_periods=int(.9 * n)).sum() / f.base.rolling(n, min_periods=int(.9 * n)).sum()
    f['prior7_high'] = f.high.shift().rolling(7 * 288, min_periods=int(.9 * 7 * 288)).max()
    f['prior7_low'] = f.low.shift().rolling(7 * 288, min_periods=int(.9 * 7 * 288)).min()
    return f


def evaluate_latest(frame: pd.DataFrame, at: int) -> dict:
    if frame.empty:
        return {'fresh': False, 'ae_ready': False, 'core_ready': False, 'status': '等待现货历史'}
    r = frame.iloc[-1]
    t = int(frame.index[-1].timestamp() * 1000)
    fresh = 0 <= at - t <= 2 * BAR_MS
    reasons = '正常观察' if bool(r.ae_ready) else '30天历史不足或数据不完整'
    if not fresh:
        reasons = '行情过期，停止预警'
    elif not r.active:
        reasons = '当前K线无实际成交，停止预警'
    elif bool(r.ae_signal) or bool(r.core_signal):
        reasons = '满足预警条件'
    poc, val, vah = market_profile(frame.tail(WARM))
    return safe(dict(bar_ms=t, price=float(r.close), fresh=fresh, active=bool(r.active),
                     ae_ready=bool(r.ae_ready) and fresh, core_ready=bool(r.core_ready) and fresh,
                     ae_signal=bool(r.ae_signal) and fresh, core_signal=bool(r.core_signal) and fresh,
                     status=reasons, V=r.V, r1=r.r1, dOI=r.dOI if bool(r.core_ready) else None,
                     F=r.F if bool(r.core_ready) else None, r24=r.r24,
                     vwap7=r.vwap7, vwap30=r.vwap30, poc30=poc, value_low=val, value_high=vah,
                     prior7_high=r.prior7_high, prior7_low=r.prior7_low,
                     triggers=[LABELS[k] for k in LABELS if bool(r[k]) and (k in ('A', 'E') or bool(r.core_ready))],
                     conditions={k: bool(r[k]) if k in ('A', 'E') or bool(r.core_ready) else None for k in LABELS}))


def market_profile(frame: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    """Approximate volume-at-price: entire5m volume at per-bar executed VWAP.

    At least90% of30d OHLC history and300 traded bars; zero-trade bars do not
    receive volume. The70% contiguous value area is not a statistical cost CI.
    """
    if len(frame) < WARM or frame.close.notna().sum() < .9 * WARM:
        return None, None, None
    valid = (frame.base > 0) & (frame.quote > 0)
    if int(valid.sum()) < 300:
        return None, None, None
    p = (frame.loc[valid, 'quote'] / frame.loc[valid, 'base']).to_numpy()
    v = frame.loc[valid, 'base'].to_numpy()
    step = math.log(1.005)
    bins = np.floor(np.log(p) / step).astype(int)
    first, last = int(bins.min()), int(bins.max())
    if last - first > 10000:
        return None, None, None
    weight = np.bincount(bins - first, weights=v, minlength=last-first+1)
    poc = int(weight.argmax())
    lo = hi = poc
    amount = weight[poc]
    while amount < weight.sum() * .7 and (lo > 0 or hi < len(weight)-1):
        left = weight[lo-1] if lo > 0 else -1
        right = weight[hi+1] if hi < len(weight)-1 else -1
        if right >= left:
            hi += 1; amount += weight[hi]
        else:
            lo -= 1; amount += weight[lo]
    return math.exp((first+poc+.5)*step), math.exp((first+lo)*step), math.exp((first+hi+1)*step)


def outcome(raw: pd.DataFrame, alert_ms: int, price: float, threshold: float, hours: int, asof: int) -> dict:
    target = alert_ms + hours * 3_600_000
    observed_end = min(target, asof // BAR_MS * BAR_MS)
    if observed_end <= alert_ms:
        return {'status': '待观察', 'complete': False, 'up': None, 'down': None}
    ix = pd.date_range(pd.Timestamp(alert_ms + BAR_MS, unit='ms', tz='UTC'), pd.Timestamp(observed_end, unit='ms', tz='UTC'), freq='5min')
    p = raw.reindex(ix)
    finite = p[['open', 'high', 'low', 'close']].notna().all(axis=1)
    active = (p.base > 0) & (p.quote > 0) & (p.trades > 0)
    found = {}
    upper, lower = price * (1 + threshold), price * (1 - threshold)
    for side, mask in [('up', p.close >= upper - abs(upper)*1e-12), ('down', p.close <= lower + abs(lower)*1e-12)]:
        m = (mask & active & finite).to_numpy(bool)
        triples = np.flatnonzero(m[:-2] & m[1:-1] & m[2:]) if len(m) >= 3 else []
        if len(triples):
            j = int(triples[0]) + 2
            found[side] = {'at': int(p.index[j].timestamp() * 1000), 'price': float(p.close.iloc[j]), 'change': float(p.close.iloc[j] / price - 1)}
        else:
            found[side] = None
    complete = observed_end == target and bool(finite.all())
    hit = bool(found['up'] or found['down'])
    status = '双向已达标' if found['up'] and found['down'] else '上涨已达标' if found['up'] else '下跌已达标' if found['down'] else '数据缺口' if not finite.all() else '未达标' if complete else '待观察'
    return dict(status=status, complete=complete, observed_until=observed_end, has_gaps=not bool(finite.all()), hit=hit, **found)
