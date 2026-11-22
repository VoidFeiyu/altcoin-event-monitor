"""Public-only collectors and a stoppable four-token monitoring service."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from .alt_monitor_core import (BAR_MS, DAY_MS, ROOT, TOKENS, Store, evaluate_latest,
                               feature_frame, now_ms, parse_bar, safe)


class PublicDataError(RuntimeError):
    def __init__(self, text, status=None):
        super().__init__(text)
        self.status = status


class PublicClient:
    def __init__(self, *, shared_rate_limits=False):
        self.shared_rate_limits = shared_rate_limits
        self.futures_blocked_until = 0.0
        self.futures_reason = ''
        self.futures_status = None

    def get(self, route: str, params=None, derivatives=False):
        if derivatives and time.monotonic() < self.futures_blocked_until:
            raise PublicDataError(self.futures_reason, self.futures_status)
        # Every route is a fixed public-data endpoint. No alternate-region routing or credentials.
        allowed = {
            'tokens': 'https://www.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list',
            'pairs': 'https://www.binance.com/bapi/defi/v1/public/alpha-trade/get-exchange-info',
            'alpha': 'https://www.binance.com/bapi/defi/v1/public/alpha-trade/klines',
            'perp_klines': 'https://fapi.binance.com/fapi/v1/klines',
            'oi': 'https://fapi.binance.com/futures/data/openInterestHist',
            'funding': 'https://fapi.binance.com/fapi/v1/fundingRate',
        }
        url = allowed[route]
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'AltEventMonitor/0.1 public-market-data'})
            with urllib.request.urlopen(request, timeout=12) as response:
                obj = json.load(response)
        except urllib.error.HTTPError as exc:
            if derivatives and (exc.code==451 or (not self.shared_rate_limits and exc.code in (418,429))):
                retry = exc.headers.get('Retry-After','').strip() if exc.headers else ''
                delay = max(1,int(retry)) if retry.isdecimal() else {418:300,429:60,451:1800}[exc.code]
                self.futures_blocked_until = time.monotonic() + delay
                self.futures_status = exc.code
                self.futures_reason = f'官方合约接口 HTTP {exc.code}，完整版等待重试'
                raise PublicDataError(self.futures_reason,exc.code) from exc
            raise PublicDataError(f'行情接口 HTTP {exc.code}', exc.code) from exc
        except (OSError, ValueError) as exc:
            raise PublicDataError(f'行情请求失败：{type(exc).__name__}') from exc
        if isinstance(obj,dict) and route=='pairs':
            data = obj.get('data')
            if obj.get('code')=='000000' and isinstance(data,dict) and isinstance(data.get('symbols'),list):
                return data['symbols']
            raise PublicDataError('Alpha 交易对目录格式异常')
        if isinstance(obj, dict) and route in ('tokens', 'alpha'):
            if obj.get('code') == '-1000' and obj.get('message') == 'No records found.':
                return []
            if obj.get('code') != '000000' or not isinstance(obj.get('data'), list):
                raise PublicDataError('Alpha响应不完整，保留原数据等待重试')
            return obj['data']
        if not isinstance(obj, list):
            raise PublicDataError('合约公开数据格式异常')
        return obj

    def identities(self):
        rows = self.get('tokens')
        result = {}
        for name, expected in TOKENS.items():
            hits = [r for r in rows if r.get('symbol', '').upper() == name and
                    r.get('contractAddress', '').lower() == expected['contract']]
            if len(hits) != 1 or hits[0].get('offline') or hits[0].get('fullyDelisted'):
                result[name] = {'error': '代币标识或状态变化，需要复核'}
            else:
                result[name] = {'alpha': hits[0]['alphaId'], 'contract': expected['contract']}
        return result

    def alpha(self, symbol: str, alpha: str, store: Store, asof: int, stop: threading.Event, *, pair=None, listed_ms=0):
        latest = store.maximum('bars', symbol)
        start = max(asof - 32 * DAY_MS, latest or 0, listed_ms)
        start = start // BAR_MS * BAR_MS
        count = 0
        # Cold installation needs about7 requests/coin. A resume rechecks the last closed bar.
        for _ in range(10):
            if start >= asof or stop.is_set():
                break
            end = min(start + 1500 * BAR_MS, asof)
            raw = self.get('alpha', dict(symbol=pair or alpha + 'USDT', interval='5m', startTime=start, endTime=end - 1, limit=1500))
            parsed = []
            for item in raw:
                if not start <= int(item[0]) < end:
                    raise PublicDataError('Alpha未遵守请求区间，停止本轮更新')
                row = parse_bar(item, asof)
                if row is not None:
                    parsed.append(row)
            store.save_bars(symbol, parsed)
            count += len(parsed)
            start = end
        return count

    def perpetual_bars(self, symbol, store, asof, stop):
        key = 'PERP:' + symbol
        earliest = asof - 8 * DAY_MS
        start = max(earliest, store.maximum('bars', key) or 0) - BAR_MS
        while start < asof and not stop.is_set():
            end = min(start + 1500 * BAR_MS, asof)
            rows = self.get('perp_klines', dict(symbol=symbol, interval='5m', startTime=start,
                                              endTime=end-1, limit=1500), True)
            parsed = [parse_bar(row, asof) for row in rows if start <= int(row[0]) < end]
            store.save_bars(key, [row for row in parsed if row is not None], 'binance-usdm')
            start = end
        store.prune(key, earliest)

    def recent_open_interest(self, symbol, asof):
        rows = self.get('oi', dict(symbol=symbol, period='5m', startTime=asof-5*3_600_000,
                                   endTime=asof, limit=100), True)
        stamps, quantities = [], []
        for row in rows:
            t, q = int(row['timestamp']), float(row['sumOpenInterest'])
            if t <= asof and q >= 0:
                stamps.append(t)
                quantities.append(q)
        return pd.Series(quantities, index=pd.to_datetime(stamps, unit='ms', utc=True), dtype=float)

    def derivatives(self, symbol: str, store: Store, asof: int, stop: threading.Event):
        contract = symbol + 'USDT'
        earliest = asof - 30 * DAY_MS + BAR_MS
        begin = max(earliest, (store.maximum('oi', symbol) or earliest) - BAR_MS)
        for _ in range(20):
            if stop.is_set() or begin >= asof:
                break
            end = min(begin + 500 * BAR_MS, asof)
            rows = self.get('oi', dict(symbol=contract, period='5m', startTime=begin, endTime=end - 1, limit=500), True)
            parsed = []
            for r in rows:
                t, q = int(r['timestamp']), float(r['sumOpenInterest'])
                if begin <= t < end and q >= 0:
                    parsed.append((t, q))
            store.save_oi(symbol, parsed)
            begin = end
        previous = store.maximum('funding', symbol)
        begin = max(asof - 32 * DAY_MS, (previous or 0) - 8 * 3_600_000)
        for _ in range(3):
            if stop.is_set():
                break
            rows = self.get('funding', dict(symbol=contract, startTime=begin, endTime=asof, limit=1000), True)
            rows = sorted(rows, key=lambda r: int(r['fundingTime']))
            parsed = []
            # Infer only near-exact1/2/4/8h settlement gaps; do not assume8h for every contract.
            for prior, row in zip(rows, rows[1:]):
                gap = (int(row['fundingTime']) - int(prior['fundingTime'])) / 3_600_000
                hours = min((1, 2, 4, 8), key=lambda x: abs(x - gap))
                if abs(hours - gap) <= 1 / 60:
                    parsed.append((int(row['fundingTime']), float(row['fundingRate']), hours))
            store.save_funding(symbol, parsed, 'settled-api;interval-from-adjacent-settlements')
            if len(rows) < 1000:
                break
            begin = int(rows[-1]['fundingTime'])  # overlap supplies the next row's interval reference


def seed_local(store: Store, asof: int, notify=lambda text: None):
    """Optional data-only import. It never imports research warnings as live alerts."""
    if store.get('seed_version') == 1:
        return
    begin = asof - 34 * DAY_MS
    base = ROOT / 'data' / 'alt_event_four_20261118'
    for symbol in TOKENS:
        notify(f'读取 {symbol} 已有历史，准备30天基准…')
        collected = []
        for path in sorted((base / 'alpha' / symbol).glob('*.json')):
            try:
                if int(path.stem) * 1000 + 1500 * BAR_MS < begin:
                    continue
                for item in json.loads(path.read_text()).get('data') or []:
                    if int(item[0]) + BAR_MS >= begin:
                        row = parse_bar(item, asof)
                        if row:
                            collected.append(row)
            except (OSError, ValueError, TypeError, KeyError):
                logging.exception('History seed skipped: %s', path.name)
        store.save_bars(symbol, collected, 'verified-research-cache')
        for folder in (base / 'metrics' / (symbol + 'USDT'), ROOT / 'data' / 'altcoin_metrics' / (symbol + 'USDT')):
            for path in folder.glob('*.zip'):
                try:
                    day = pd.Timestamp(path.stem[-10:], tz='UTC')
                    if day.value // 1_000_000 + DAY_MS < begin:
                        continue
                    with zipfile.ZipFile(path) as archive:
                        raw = archive.read(archive.namelist()[0]).decode('utf-8-sig')
                    rows = [(int(pd.Timestamp(r['create_time'], tz='UTC').timestamp() * 1000), float(r['sum_open_interest']))
                            for r in csv.DictReader(io.StringIO(raw))]
                    store.save_oi(symbol, [(t, q) for t, q in rows if begin <= t <= asof], 'historical-public-archive')
                except (OSError, ValueError, KeyError):
                    logging.exception('OI seed skipped: %s', path.name)
        # Archived funding intervals are explicit. November public-API records are paired by actual timestamps.
        funding = {}
        for folder in (base / 'funding' / (symbol + 'USDT'), ROOT / 'data' / 'alt_universe' / 'funding' / (symbol + 'USDT')):
            for path in folder.glob('*.zip'):
                try:
                    month = pd.Timestamp(path.stem[-7:] + '-01', tz='UTC')
                    if month.value // 1_000_000 + 32 * DAY_MS < begin:
                        continue
                    with zipfile.ZipFile(path) as archive:
                        raw = archive.read(archive.namelist()[0]).decode('utf-8-sig')
                    for r in csv.DictReader(io.StringIO(raw)):
                        t = int(r['calc_time'])
                        if begin <= t <= asof:
                            funding[t] = (float(r['last_funding_rate']), float(r['funding_interval_hours']))
                except (OSError, ValueError, KeyError):
                    logging.exception('Funding seed skipped: %s', path.name)
        path = ROOT / 'reports' / 'alt_event_four_tokens_20261118' / f'{symbol}USDT_nov_funding.json'
        try:
            rows = json.loads(path.read_text())
            for prior, row in zip(rows, rows[1:]):
                t = int(row['fundingTime'])
                gap = (t - int(prior['fundingTime'])) / 3_600_000
                h = min((1, 2, 4, 8), key=lambda x: abs(x - gap))
                if begin <= t <= asof and abs(h - gap) < 1 / 60:
                    funding[t] = (float(row['fundingRate']), h)
        except (OSError, ValueError, KeyError):
            pass
        store.save_funding(symbol, [(t, rate, h) for t, (rate, h) in funding.items()], 'historical-settled-history')
    store.set('seed_version', 1)


class Monitor:
    def __init__(self, store: Store, callback=lambda event: None, client=None):
        self.store, self.callback = store, callback
        self.client = client or PublicClient()
        self.running = False
        self.busy = False
        self.closed = threading.Event()
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.worker = None
        self.next_scan = 0
        self.identities = store.get('identities', {})
        self.identity_at = 0
        self.settings = {'threshold': .2, 'sound': False}
        self.settings.update(store.get('settings', {}))
        self.rows = {s: store.get('snapshot:' + s, {'symbol': s, 'status': '尚未扫描'}) for s in TOKENS}

    def notify(self, kind, **data):
        if not self.closed.is_set():
            self.callback(safe({'kind': kind, **data}))

    def prepare(self):
        def job():
            try:
                seed_local(self.store, now_ms(), lambda text: self.notify('progress', text=text))
                self.notify('progress', text='历史基准就绪，点击“开始监控”或“立即扫描”')
            except Exception:
                logging.exception('Seed failed')
                self.notify('progress', text='本地历史读取失败；联网扫描会重新建立基准')
            self.notify('prepared')
        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()

    def start(self):
        self.running = True
        self.next_scan = 0
        self.scan()

    def pause(self):
        self.running = False
        self.next_scan = 0
        self.cancel.set()

    def scan(self, subscriptions=None, record_alert=None):
        if self.closed.is_set() or not self.lock.acquire(blocking=False):
            return False
        self.busy = True
        self.cancel.clear()
        self.worker = threading.Thread(target=self._scan, args=(subscriptions, record_alert), daemon=True)
        self.worker.start()
        return True

    def _scan(self, subscriptions=None, record_alert=None):
        begun = now_ms()
        settings = dict(self.settings)
        # Give publication20s; unfinished/current bars are filtered once more in parse_bar.
        asof = (begun - 20_000) // BAR_MS * BAR_MS
        try:
            self.notify('progress', text='核对代币标识与已收盘行情…')
            if not self.identity_at or begun - self.identity_at > DAY_MS:
                self.identities = self.client.identities()
                self.identity_at = begun
                self.store.set('identities', self.identities)
            for symbol in TOKENS if subscriptions is None else subscriptions:
                if self.closed.is_set() or self.cancel.is_set():
                    break
                self.notify('progress', text=f'正在扫描 {symbol} · 已收盘5分钟K线')
                identity = self.identities.get(symbol, {})
                subscription = None if subscriptions is None else subscriptions[symbol]
                use_derivatives = subscription is None or 'core' in subscription['alpha']
                spot_error = derivative_error = ''
                try:
                    if identity.get('error') or not identity.get('alpha'):
                        raise PublicDataError(identity.get('error', '代币身份未核验'))
                    self.client.alpha(symbol, identity['alpha'], self.store, asof, self.cancel)
                except Exception as exc:
                    spot_error = str(exc)
                    logging.warning('%s spot: %s', symbol, exc)
                if self.closed.is_set() or self.cancel.is_set():
                    break
                if use_derivatives:
                    try:
                        self.client.derivatives(symbol, self.store, asof, self.cancel)
                    except Exception as exc:
                        derivative_error = str(exc)
                if self.closed.is_set() or self.cancel.is_set():
                    break
                raw = self.store.bars(symbol, asof - 33 * DAY_MS, asof)
                oi, fund = self.store.derivatives(symbol, asof - 33 * DAY_MS, asof) if use_derivatives else (None,None)
                f = feature_frame(raw, oi, fund)
                observed = now_ms()
                state = evaluate_latest(f, observed)
                # A stale cache must never be promoted to live evidence after a failed request.
                state.update(symbol=symbol, scanned_ms=observed, spot_error=spot_error, derivative_error=derivative_error)
                if spot_error:
                    state.update(ae_signal=False, core_signal=False, ae_ready=False, core_ready=False, status='现货获取失败，停止预警')
                elif derivative_error:
                    state.update(core_ready=False, core_signal=False, dOI=None, F=None)
                    state['conditions'].update(OB=None, C=None)
                    state['triggers'] = [text for key, text in (('A', '现货放量'), ('E', '短线涨跌异常')) if state['conditions'].get(key)]
                models = [('ae', state.get('ae_signal')), ('core', state.get('core_signal'))]
                new = []
                for model, trigger in models:
                    if trigger and (subscription is None or (model in subscription['alpha'] and state.get('bar_ms') == asof)):
                        threshold = float(settings['threshold'] if subscription is None else subscription['threshold'])
                        save = record_alert or self.store.record
                        key = save(symbol, model, state['bar_ms'], observed, state['price'], threshold, state)
                        if key:
                            new.append(key)
                            self.notify('alert', symbol=symbol, model=model, price=state['price'], sound=bool(settings['sound']))
                state['new_alerts'] = len(new)
                self.rows[symbol] = state
                self.store.set('snapshot:' + symbol, state)
                self.notify('snapshot', state=state)
            if not self.closed.is_set() and not self.cancel.is_set():
                self.notify('progress', text='追踪已保存预警的3/7/14天实际结果…')
                self.store.track(asof)
                self.notify('finished', at=begun)
        except Exception as exc:
            logging.exception('Scan failed')
            for symbol in TOKENS if subscriptions is None else subscriptions:
                self.rows[symbol] = {'symbol': symbol, 'status': '本轮连接失败', 'fresh': False,
                                     'ae_ready': False, 'core_ready': False, 'spot_error': str(exc), 'scanned_ms': now_ms()}
                self.notify('snapshot', state=self.rows[symbol])
            self.notify('progress', text=f'本轮未完成：{exc}；已有记录已保留')
        finally:
            self.busy = False
            self.next_scan = now_ms() + BAR_MS if self.running else 0
            self.lock.release()
            self.notify('idle')

    def tick(self):
        if self.running and not self.busy and now_ms() >= self.next_scan:
            self.scan()

    def configure(self, threshold: float, sound: bool):
        if threshold not in (.1, .2, .3, .5):
            raise ValueError('事件门槛只能选择10/20/30/50%')
        self.settings = {'threshold': threshold, 'sound': bool(sound)}
        self.store.set('settings', self.settings)

    def close(self):
        self.running = False
        self.closed.set()
        self.cancel.set()
