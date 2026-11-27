"""Scan Binance Alpha and Binance Spot with the existing event rules."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import sqlite3
import threading
import time

from .alt_monitor_core import (BAR_MS, DAY_MS, TOKENS, Store, evaluate_latest,
                               feature_frame, now_ms, safe)
from .alt_monitor_data import PublicClient, PublicDataError, seed_local
from .alt_event_score import latest_research_scores
from .alpha_market_analysis import CHART_MINUTES, book_evidence, chart_series, market_analysis, market_flow
from .alpha_observations import PAIRED_TOKENS, observations
from .public_request_gate import MarketRateLimit, PublicRequestGate


QUOTES = ('USDT', 'USDC', 'U')
SPOT_QUOTES = (*QUOTES, 'FDUSD', 'USD1', 'BTC', 'ETH', 'BNB')
SPOT_NAME = re.compile(r'[^\s:/?#]{1,48}')
SOURCE_LABELS = {'alpha':'币安 Alpha', 'spot':'币安现货'}
WORKERS = 6
PUBLIC_INTERVAL = .16
DEPTH_INTERVAL = 60
DEPTH_LIMIT = 100


def alpha_catalog(tokens, pairs):
    metadata = {row['alphaId']:row for row in tokens if row.get('alphaId')}
    best = {}
    for row in pairs:
        base, quote, pair = row.get('baseAsset',''), row.get('quoteAsset'), row.get('symbol')
        if (row.get('status')!='TRADING' or quote not in QUOTES or not re.fullmatch(r'ALPHA_\d+',base)
                or pair!=base+quote):
            continue
        token = metadata.get(base,{})
        if token.get('offline') or token.get('fullyDelisted'):
            continue
        if base in best and QUOTES.index(best[base]['quote'])<=QUOTES.index(quote):
            continue
        symbol = str(token.get('symbol') or base)
        known = TOKENS.get(symbol.upper(),{})
        verified = (base==known.get('alpha')
                    and str(token.get('contractAddress') or '').lower()==known.get('contract'))
        enriched = quote=='USDT' and verified
        observed_pair = PAIRED_TOKENS.get(symbol.upper(), {})
        paired = (quote=='USDT' and base==observed_pair.get('alpha')
                  and str(token.get('contractAddress') or '').lower()==observed_pair.get('contract'))
        best[base] = dict(id=base,pair=pair,quote=quote,symbol=symbol,name=token.get('name') or '',source='alpha',
                          coin=symbol.upper()+'USDT' if verified else base,
                          contract=str(token.get('contractAddress') or ''),
                          chain_id=str(token.get('chainId') or ''),
                          chain=str(token.get('chainName') or token.get('chainId') or ''),
                          listed_ms=int(token.get('listingTime') or 0),
                          key=symbol.upper() if enriched else pair, core_supported=enriched,
                          paired_perp=symbol.upper()+'USDT' if paired else None)
    return {market['pair']:market for market in best.values()}


def spot_catalog(pairs):
    ranks = {quote:rank for rank,quote in enumerate(SPOT_QUOTES)}
    best = {}
    for row in pairs:
        base, quote, pair = row.get('baseAsset'), row.get('quoteAsset'), row.get('symbol')
        if (row.get('status')!='TRADING' or row.get('isSpotTradingAllowed') is not True or not base or not quote
                or pair!=base+quote or not SPOT_NAME.fullmatch(pair) or re.fullmatch(r'ALPHA_\d+(USDT|USDC|U)',pair)):
            continue
        rank = ranks.get(quote,len(ranks)),quote
        if base not in best or rank < best[base][0]:
            perp = base+'USDT' if re.fullmatch(r'[A-Z0-9]{1,24}',base) else None
            best[base] = rank,dict(id=base,pair=pair,quote=quote,symbol=base,name='',source='spot',coin='SPOT:'+base,
                                   key='SPOT:'+pair,chain='',contract='',listed_ms=0,core_supported=False,perp_symbol=perp)
    return {row['pair']:row for _,row in best.values()}


class PacedAlphaClient(PublicClient):
    def __init__(self, stop, spot_requests, futures_requests, exchange_spot_requests=None):
        super().__init__(shared_rate_limits=True)
        self.stop = stop
        self.spot_requests = spot_requests
        self.futures_requests = futures_requests
        self.exchange_spot_requests = exchange_spot_requests or spot_requests

    def get(self, route, params=None, derivatives=False):
        if self.stop.is_set():
            raise PublicDataError('扫描已停止')
        requests = (self.futures_requests if derivatives else self.exchange_spot_requests
                    if route in ('spot_pairs','spot','spot_depth') else self.spot_requests)
        return requests.call(super().get,route,params,derivatives)


class AlphaRadar:
    def __init__(self, directory, stop, client=None, cleared=0, futures_requests=None):
        self.store = Store(directory,allow_market_pairs=True)
        self.stop = stop
        self.spot_requests = PublicRequestGate(PUBLIC_INTERVAL,Path(directory)/'alpha.cooldown')
        self.exchange_spot_requests = PublicRequestGate(PUBLIC_INTERVAL,Path(directory)/'spot.cooldown')
        self.futures_requests = futures_requests or PublicRequestGate(.20)
        self.client = client or PacedAlphaClient(stop,self.spot_requests,self.futures_requests,self.exchange_spot_requests)
        self.lock = threading.Lock()
        self.prefs = self.store.get('radar-prefs')
        if self.prefs is None:
            self.prefs = dict(paused=False,threshold=.2,cleared=cleared)
            self.store.set('radar-prefs',self.prefs)
        self.started = now_ms()
        self.generation = 0
        self.catalog = {}
        self.states = {}
        self.catalog_errors = {}
        self.inflight = set()
        self.book_errors = {}
        self.book_priority = {}

    def configure(self, action, value):
        with self.lock:
            prefs = self.prefs.copy()
            if action=='pause' and type(value) is bool:
                prefs['paused'] = value
            elif action=='threshold' and type(value) in (int,float) and value in (.1,.2,.3,.5):
                prefs['threshold'] = value
            elif action=='clear' and value is True:
                prefs['cleared'] = max(prefs['cleared'],now_ms())
            elif action=='watch' and isinstance(value,dict) and isinstance(value.get('pair'),str) and type(value.get('enabled')) is bool:
                pair = value['pair']
                if pair not in self.catalog:
                    raise ValueError('该交易对不在当前市场目录中')
                watched = set(prefs.get('watched',()))
                (watched.add if value['enabled'] else watched.discard)(pair)
                prefs['watched'] = sorted(watched)
            else:
                raise ValueError('异动观察设置无效')
            self.store.set('radar-prefs',prefs)
            self.prefs = prefs
            if action=='pause' and not value:
                self.started = now_ms()
            if action!='watch':
                self.generation += 1

    def snapshot(self):
        with self.lock:
            watched = set(self.prefs.get('watched',()))
            markets = [{**market, **self.states.get(pair,{}), 'scanning':pair in self.inflight, 'watched':pair in watched}
                       for pair,market in self.catalog.items()]
            prefs = self.prefs.copy()
            source_errors = self.catalog_errors.copy()
        alerts = self.store.alerts(limit=500,since=prefs['cleared']+1)
        for row in alerts:
            evidence = row['evidence']
            row['display_symbol'] = evidence.get('display_symbol',row['symbol'])
            row['quote'] = evidence.get('quote','USDT')
            row['pair'] = evidence.get('pair',TOKENS.get(row['symbol'],{}).get('alpha','')+'USDT')
            row['source'] = evidence.get('source','alpha')
            row['coin'] = evidence.get('coin')
        return safe(dict(markets=markets,alerts=alerts,paused=prefs['paused'],threshold=prefs['threshold'],
                         error=' · '.join(source_errors.values()),now=now_ms(),retry_at=dict(spot=self.spot_requests.retry_at(),
                                                               exchange_spot=self.exchange_spot_requests.retry_at(),
                                                               perp=self.futures_requests.retry_at())))

    def refresh_catalog(self, source='alpha'):
        markets = (alpha_catalog(self.client.get('tokens'),self.client.get('pairs')) if source=='alpha'
                   else spot_catalog(self.client.get('spot_pairs',{'permissions':'SPOT','symbolStatus':'TRADING'})))
        if not markets:
            raise PublicDataError('未取得活跃交易对目录')
        with self.lock:
            previous = {pair:row for pair,row in self.catalog.items() if row.get('source','alpha')!=source}
            self.catalog = previous | markets
            self.states = {pair:state for pair,state in self.states.items() if pair in self.catalog}
            self.catalog_errors.pop(source,None)

    def market_detail(self, pair, days=3):
        if days not in CHART_MINUTES or not SPOT_NAME.fullmatch(pair):
            raise ValueError('币种或价格区间无效')
        with self.lock:
            if pair not in self.catalog:
                raise KeyError('该交易对不在当前市场目录中')
            market = {**self.catalog[pair], **self.states.get(pair, {})}
            cleared = self.prefs['cleared']
            watched = pair in self.prefs.get('watched',())
            book_error = self.book_errors.get(pair,'')
        observed = now_ms()
        end = min(market.get('bar_ms') or observed//BAR_MS*BAR_MS, observed//BAR_MS*BAR_MS)
        raw = self.store.bars(market['key'], end-days*DAY_MS+BAR_MS, end)
        alerts = self.store.alerts(limit=100, symbol=market['key'], since=cleared+1)
        return safe(dict(market=market, days=days, now=observed, chart=chart_series(raw, days), alerts=alerts,
                         watched=watched,book_error=book_error,books=self.store.books(pair)))

    def coin_market(self, coin):
        with self.lock:
            market = next((item for item in self.catalog.values() if item['coin']==coin),None)
            if market is None and re.fullmatch(r'[A-Z0-9]{1,24}USDT',coin):
                market = next((item for item in self.catalog.values()
                               if item.get('source')=='spot' and item.get('perp_symbol')==coin),None)
            return {**market, **self.states.get(market['pair'],{})} if market else None

    def coin_alerts(self, market):
        with self.lock:
            cleared = self.prefs['cleared']
        return self.store.alerts(limit=100,symbol=market['key'],since=cleared+1)

    def scan_market(self, market, generation):
        pair, key = market['pair'],market['key']
        asof = (now_ms()-20_000)//BAR_MS*BAR_MS
        try:
            source = market.get('source','alpha')
            if source=='spot':
                self.client.spot_bars('spot',key,pair,self.store,asof,self.stop)
            else:
                self.client.alpha(key,market['id'],self.store,asof,self.stop,pair=pair,listed_ms=market['listed_ms'])
            derivative_error = ''
            oi, funding = None, None
            if market['core_supported']:
                try:
                    self.client.derivatives(key,self.store,asof,self.stop)
                    oi,funding = self.store.derivatives(key,asof-33*DAY_MS,asof)
                except MarketRateLimit:
                    pass
                except (PublicDataError,OSError,ValueError) as error:
                    derivative_error = str(error)
            raw = self.store.bars(key,asof-33*DAY_MS,asof)
            features = feature_frame(raw,oi,funding)
            state = evaluate_latest(features,now_ms())
            observed = now_ms()
            state.update(scanned_ms=observed,derivative_error=derivative_error,display_symbol=market['symbol'],
                         quote=market['quote'],pair=pair,source=source,coin=market.get('coin'))
            state['research_scores'] = latest_research_scores(features, state)
            state['analysis'] = market_analysis(features, state)
            end = state.get('bar_ms')
            btc = None
            if end and market['quote']=='USDT':
                reference = self.store.bars('SPOT:BTCUSDT',end-12*BAR_MS,end)
                if len(reference)>=2 and reference.index[-1].value//1_000_000==end:
                    btc = reference.iloc[[0,-1]]
            state['flow'] = market_flow(features,btc,check_alpha_direction=source=='alpha')
            state['observations'] = self.observe(market, features, state, oi, asof)
            observed = state['scanned_ms'] = now_ms()
            with self.lock:
                cutoff = max(self.started,self.prefs['cleared'])
                if (not self.stop.is_set() and generation==self.generation and not self.prefs['paused']
                        and state.get('bar_ms')==(observed-20_000)//BAR_MS*BAR_MS and state['bar_ms']>cutoff):
                    for model in ('ae','core'):
                        if state.get(model+'_signal') and self.store.record(key,model,state['bar_ms'],observed,state['price'],self.prefs['threshold'],state):
                            self.book_priority[pair] = observed + 60*60_000
            self.store.track(asof,symbol=key)
            self.store.prune(key,asof-34*DAY_MS)
            return safe(state)
        except MarketRateLimit:
            return None
        except (PublicDataError,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            return dict(scanned_ms=now_ms(),status='行情读取失败',spot_error=str(error))

    def observe(self, market, features, state, oi, asof):
        symbol = market.get('paired_perp')
        perp = None
        error = ''
        if symbol and state.get('fresh') and state.get('active') and not self.stop.is_set():
            try:
                self.client.perpetual_bars(symbol, self.store, asof, self.stop)
                if not market['core_supported']:
                    oi = self.client.recent_open_interest(symbol, asof)
            except MarketRateLimit:
                pass
            except (PublicDataError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                error = str(exc)
            try:
                perp = self.store.bars('PERP:' + symbol, asof-8*DAY_MS, asof)
            except sqlite3.Error as exc:
                error = error or str(exc)
        return observations(features, state, perp, oi, symbol, error)

    def sample_book(self, market):
        pair = market['pair']
        response = self.client.get(market['source']+'_depth',{'symbol':pair,'limit':DEPTH_LIMIT})
        snapshot = book_evidence(response,pair,DEPTH_LIMIT,now_ms())
        self.store.save_book(pair,snapshot)
        return snapshot

    def run_books(self):
        next_at = {}
        while not self.stop.is_set():
            at = now_ms()
            with self.lock:
                self.book_priority = {pair:end for pair,end in self.book_priority.items() if end>at}
                targets = set(self.prefs.get('watched',())) | self.book_priority.keys()
                targets = [self.catalog[pair] for pair in targets if pair in self.catalog and next_at.get(pair,0)<=at]
                paused = self.prefs['paused']
            if paused or not targets:
                self.stop.wait(1)
                continue
            market = min(targets,key=lambda row:(next_at.get(row['pair'],0),row['pair']))
            pair = market['pair']
            next_at[pair] = at + DEPTH_INTERVAL*1000
            try:
                self.sample_book(market)
            except MarketRateLimit:
                error = '等待盘口接口限频解除'
            except (PublicDataError,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
                error = str(exc)
            else:
                error = ''
            with self.lock:
                self.book_errors[pair] = error

    def run(self):
        try:
            seed_local(self.store,now_ms())
        except (OSError,ValueError,sqlite3.Error):
            pass
        threading.Thread(target=self.run_books,daemon=True,name='radar-books').start()
        pending, due = {}, {}
        feeds = {'alpha':self.spot_requests, 'spot':self.exchange_spot_requests}
        refresh_at = dict.fromkeys(feeds,0)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            while not self.stop.is_set():
                now = time.monotonic()
                for source,requests in feeds.items():
                    if now<refresh_at[source] or requests.retry_at():
                        continue
                    try:
                        self.refresh_catalog(source)
                    except MarketRateLimit:
                        pass
                    except (PublicDataError,ValueError,KeyError,TypeError) as error:
                        with self.lock:
                            self.catalog_errors[source] = SOURCE_LABELS[source]+' · '+str(error)
                        refresh_at[source] = now+60
                    else:
                        refresh_at[source] = now+900
                for future,(pair,slot) in list(pending.items()):
                    if not future.done():
                        continue
                    result = future.result()
                    with self.lock:
                        if result is not None and pair in self.catalog:
                            self.states[pair] = result
                        self.inflight.discard(pair)
                    del pending[future]
                    due[pair] = slot if result is None else slot+1
                slot = (now_ms()-20_000)//BAR_MS
                ready_feeds = {source for source,requests in feeds.items() if not requests.retry_at()}
                with self.lock:
                    if not self.prefs['paused'] and ready_feeds:
                        available = [pair for pair,market in self.catalog.items() if pair not in self.inflight
                                     and due.get(pair,0)<=slot and market.get('source','alpha') in ready_feeds]
                        available.sort(key=lambda pair:(pair!='BTCUSDT',due.get(pair,0),self.catalog[pair]['symbol'].casefold(),pair))
                        for pair in available[:WORKERS-len(pending)]:
                            job = pool.submit(self.scan_market,self.catalog[pair],self.generation)
                            pending[job] = (pair,slot)
                            self.inflight.add(pair)
                self.stop.wait(.35)
