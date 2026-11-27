"""Persistent, read-only scans for funding and on-chain/CEX spreads."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import re

from .alt_monitor_core import BAR_MS, now_ms, payload
from .alt_opportunity_market import (DataUnavailable, HOUR, MINUTE,
                                    Okx, WETH, check_age, finite, shared_quantity)
from .chain_replay import ChainReader, Market

NATIVE_WRAPPED = {1: WETH, 56: '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'}
EXIT_WINDOW = 20 * MINUTE
QUOTE_GAP = 3 * BAR_MS // 2
SCAN_ERRORS = (DataUnavailable, ValueError, TypeError, KeyError, IndexError, ArithmeticError)


@dataclass(frozen=True)
class ScanSettings:
    notional: float = 1000
    spot_bp: float = 10
    swap_bp: float = 5
    gas_units: int = 200_000

    @classmethod
    def from_values(cls, amount, spot_bp, swap_bp, gas):
        amount, spot_bp, swap_bp, gas = map(finite, (amount, spot_bp, swap_bp, gas))
        if not 10 <= amount <= 1_000_000 or not 0 <= spot_bp <= 100 or not 0 <= swap_bp <= 100:
            raise ValueError('名义金额须在 10–1,000,000，单边费率须在 0–100 bp')
        if not 21_000 <= gas <= 2_000_000 or gas != int(gas):
            raise ValueError('Gas 预算须为 21,000–2,000,000 的整数')
        return cls(amount, spot_bp, swap_bp, int(gas))


class OpportunityStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS opportunity_samples(
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT NOT NULL,
                    observed INTEGER NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS opportunity_samples_recent
                    ON opportunity_samples(kind, symbol, observed DESC, id DESC);
                CREATE TABLE IF NOT EXISTS funding_cases(
                    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, settlement INTEGER NOT NULL,
                    opened INTEGER NOT NULL, entry TEXT NOT NULL, exit TEXT, result TEXT,
                    UNIQUE(symbol, settlement));
                CREATE INDEX IF NOT EXISTS funding_cases_open ON funding_cases(symbol, result);
                CREATE TABLE IF NOT EXISTS lp_replays(
                    id INTEGER PRIMARY KEY, created INTEGER NOT NULL, data TEXT NOT NULL);
            ''')

    def sample(self, kind, symbol, row):
        with self.store.connect() as db:
            if kind == 'dex' and row.get('opportunities'):
                prior = db.execute('''SELECT data FROM opportunity_samples WHERE kind=? AND symbol=?
                                      ORDER BY observed DESC, id DESC LIMIT 1''', (kind, symbol)).fetchone()
                prior = json.loads(prior['data']) if prior else None
                for direction, value in row['opportunities'].items():
                    previous = (prior or {}).get('opportunities', {}).get(direction, {})
                    same_setup = prior and prior.get('setup') == row['setup']
                    continuous = (same_setup and 0 < row['observed'] - prior['observed'] <= QUOTE_GAP
                                  and previous.get('net', 0) > 0
                                  and row['block']['number'] >= prior['block']['number'])
                    if value['net'] > 0:
                        value['since'] = previous['since'] if continuous else row['observed']
                        value['samples'] = (previous['samples'] + (row['block']['number'] > prior['block']['number'])
                                            if continuous else 1)
            db.execute('INSERT INTO opportunity_samples(kind,symbol,observed,data) VALUES(?,?,?,?)',
                       (kind, symbol, row['observed'], payload(row)))
        return row

    def samples(self, kind, limit=200):
        with self.store.connect() as db:
            rows = db.execute('''SELECT id,data FROM opportunity_samples WHERE kind=?
                                 ORDER BY observed DESC, id DESC LIMIT ?''', (kind, limit)).fetchall()
        return [dict(json.loads(row['data']), sample_id=row['id']) for row in rows]

    def open_case(self, row):
        settlement = row['funding']['settlement']
        if row['net'] > 0 and settlement - row['observed'] >= 30_000:
            with self.store.connect() as db:
                db.execute('''INSERT OR IGNORE INTO funding_cases(symbol,settlement,opened,entry)
                              VALUES(?,?,?,?)''', (row['symbol'], settlement, row['observed'], payload(row)))

    def cases(self, symbol=None):
        sql = 'SELECT * FROM funding_cases'
        args = ()
        if symbol:
            sql += ' WHERE symbol=? AND result IS NULL'
            args = (symbol,)
        with self.store.connect() as db:
            rows = db.execute(sql + ' ORDER BY opened DESC LIMIT 200', args).fetchall()
        return [{**dict(row), **{key: json.loads(row[key]) if row[key] else None
                                for key in ('entry', 'exit', 'result')}} for row in rows]

    def due_symbols(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT DISTINCT symbol FROM funding_cases WHERE result IS NULL AND settlement<=?',
                              (now_ms(),)).fetchall()
        return [row['symbol'] for row in rows]

    def freeze_exit(self, ident, row):
        with self.store.connect() as db:
            db.execute('UPDATE funding_cases SET exit=? WHERE id=? AND exit IS NULL', (payload(row), ident))

    def finish(self, ident, result):
        with self.store.connect() as db:
            db.execute('UPDATE funding_cases SET result=? WHERE id=? AND result IS NULL', (payload(result), ident))

    def save_replay(self, result):
        with self.store.connect() as db:
            db.execute('INSERT INTO lp_replays(created,data) VALUES(?,?)', (now_ms(), payload(result)))

    def replays(self, limit=40):
        with self.store.connect() as db:
            rows = db.execute('SELECT data FROM lp_replays ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [json.loads(row['data']) for row in rows]


def funding_market(okx, symbol):
    spot_id, swap_id = f'{symbol}-USDT', f'{symbol}-USDT-SWAP'
    spot_spec = okx.instrument(spot_id, 'SPOT')
    swap_spec, multiplier = okx.linear_swap(symbol)
    if spot_spec.get('baseCcy') != symbol or spot_spec.get('quoteCcy') != 'USDT':
        raise DataUnavailable('OKX 现货计价资产不匹配')
    spot, swap = okx.book(spot_id), okx.book(swap_id, float(multiplier))
    if abs(spot.timestamp - swap.timestamp) > 12_000:
        raise DataUnavailable('现货与永续盘口时间差超过 12 秒')
    return spot_spec, swap_spec, multiplier, spot, swap


def funding_symbols(okx):
    spot = {row['baseCcy'] for row in okx.data('public/instruments', instType='SPOT')
            if row.get('state') == 'live' and row.get('quoteCcy') == 'USDT' and row.get('baseCcy')}
    swap = {row['ctValCcy'] for row in okx.data('public/instruments', instType='SWAP')
            if row.get('state') == 'live' and row.get('ctType') == 'linear' and row.get('settleCcy') == 'USDT'
            and row.get('ctValCcy') and row.get('instId') == f'{row["ctValCcy"]}-USDT-SWAP'}
    return sorted(spot & swap)


def funding_candidate(symbol, market, info, settings):
    spot_spec, swap_spec, multiplier, spot, swap = market
    qty = shared_quantity(settings.notional, spot.mid, spot_spec['lotSz'], Decimal(swap_spec['lotSz']) * multiplier)
    if qty < max(Decimal(spot_spec['minSz']), Decimal(swap_spec['minSz']) * multiplier):
        raise DataUnavailable('数量低于双腿最小订单量')
    observed = now_ms()
    for book in (spot, swap):
        check_age(book.timestamp, observed, 25_000, '现货或永续盘口')
    amount = float(qty)
    entry_spot, entry_swap = spot.value(amount, True), swap.value(amount, False)
    exit_spot, exit_swap = spot.value(amount, False), swap.value(amount, True)
    spot_fee, swap_fee = settings.spot_bp / 10_000, settings.swap_bp / 10_000
    fees = (entry_spot + exit_spot) * spot_fee + (entry_swap + exit_swap) * swap_fee
    convergence = exit_spot - entry_spot + entry_swap - exit_swap
    estimate = amount * swap.mid * info['rate']
    return {'symbol': symbol, 'observed': observed, 'venue': 'OKX', 'quantity': amount,
            'target': settings.notional, 'funding': info, 'spot_ts': spot.timestamp, 'swap_ts': swap.timestamp,
            'spot_buy': entry_spot, 'swap_sell': entry_swap, 'spot_sell': exit_spot, 'swap_buy': exit_swap,
            'spot_bp': settings.spot_bp, 'swap_bp': settings.swap_bp, 'funding_pnl': estimate,
            'basis': swap.mid / spot.mid - 1, 'fees': fees, 'price_pnl': convergence,
            'net': convergence + estimate - fees, 'spot_book': {'bids': spot.bids, 'asks': spot.asks},
            'swap_book': {'bids': swap.bids, 'asks': swap.asks}}


def settled_result(entry, exit_row, rate, mark):
    spot_move = exit_row['spot_sell'] - entry['spot_buy']
    swap_move = entry['swap_sell'] - exit_row['swap_buy']
    fees = ((entry['spot_buy'] + exit_row['spot_sell']) * entry['spot_bp']
            + (entry['swap_sell'] + exit_row['swap_buy']) * entry['swap_bp']) / 10_000
    funding_pnl = entry['quantity'] * mark * rate
    return {'status': 'settled', 'rate': rate, 'mark': mark, 'fees': fees, 'funding_pnl': funding_pnl,
            'spot_pnl': spot_move, 'swap_pnl': swap_move, 'net': spot_move + swap_move + funding_pnl - fees}


class Scanner:
    def __init__(self, store, okx=None):
        self.store = store
        self.okx = okx or Okx()
        self.funding_catalog = []
        self.funding_catalog_at = 0
        self.funding_cursor = 0

    def pending(self, symbol, market):
        settled = None
        for case in self.store.cases(symbol):
            now, settlement = now_ms(), case['settlement']
            if now < settlement + MINUTE:
                continue
            entry, exit_row = case['entry'], case['exit']
            try:
                if exit_row is None:
                    if now > settlement + EXIT_WINDOW:
                        self.store.finish(case['id'], {'status': 'gap', 'reason': '结算后 20 分钟内没有完整退出盘口'})
                        continue
                    if market is None:
                        continue
                    _, _, _, spot, swap = market
                    for book in (spot, swap):
                        check_age(book.timestamp, now, 25_000, '退出盘口')
                        if not settlement + MINUTE <= book.timestamp <= settlement + EXIT_WINDOW:
                            raise DataUnavailable('等待属于退出窗口的盘口')
                    exit_row = {'observed': now, 'spot_ts': spot.timestamp, 'swap_ts': swap.timestamp,
                                'spot_sell': spot.value(entry['quantity'], False),
                                'swap_buy': swap.value(entry['quantity'], True),
                                'spot_bids': spot.bids, 'swap_asks': swap.asks}
                    self.store.freeze_exit(case['id'], exit_row)
                swap_id = f'{symbol}-USDT-SWAP'
                if settled is None:
                    settled = self.okx.settled(swap_id)
                if settlement not in settled:
                    if settled and min(settled) > settlement:
                        self.store.finish(case['id'], {'status': 'gap', 'reason': '该次结算已超出公开历史覆盖'})
                    continue
                mark = self.okx.mark_before(swap_id, settlement)
                self.store.finish(case['id'], settled_result(entry, exit_row, settled[settlement], mark))
            except SCAN_ERRORS as exc:
                logging.info('Pending funding %s %s: %s', symbol, settlement, exc)
                if exit_row and now - settlement > 24 * HOUR:
                    self.store.finish(case['id'], {'status': 'gap', 'reason': f'结算资料缺口：{exc}'})

    def funding(self, settings, stopped, update, symbols=None):
        if not symbols:
            if not self.funding_catalog or now_ms() - self.funding_catalog_at > HOUR:
                self.funding_catalog = funding_symbols(self.okx)
                self.funding_catalog_at = now_ms()
            if not self.funding_catalog:
                raise DataUnavailable('No eligible OKX USDT spot and perpetual pair')
            start = self.funding_cursor % len(self.funding_catalog)
            symbols = (self.funding_catalog[start:] + self.funding_catalog[:start])[:16]
        due = self.store.due_symbols()
        for symbol in dict.fromkeys([*due, *symbols]):
            if stopped():
                return
            market = None
            try:
                market = funding_market(self.okx, symbol)
                if symbol in symbols:
                    info = self.okx.funding(f'{symbol}-USDT-SWAP')
                    row = funding_candidate(symbol, market, info, settings)
            except SCAN_ERRORS as exc:
                row = {'symbol': symbol, 'observed': now_ms(), 'error': str(exc)}
            if stopped():
                return
            self.pending(symbol, market)
            if symbol not in symbols:
                continue
            self.store.sample('funding', symbol, row)
            if 'net' in row:
                self.store.open_case(row)
            update(row)
        self.funding_cursor += len(symbols)

    def dex_market(self, settings, market, cex_symbol, stopped, update):
        reader = ChainReader(market.chain)
        symbol = market.symbol or market.token[:10]
        observed = now_ms()
        try:
            base_symbol = reader.symbol(market.token, reader.block()['number'])
            symbol = cex_symbol.strip().upper() or base_symbol.upper()
            if market.token == NATIVE_WRAPPED[market.chain].lower() and not cex_symbol:
                symbol = 'ETH' if market.chain == 1 else 'BNB'
            if not re.fullmatch(r'[A-Z0-9]{1,24}', symbol):
                raise DataUnavailable('Enter the matching OKX asset code')
            ident = f'{symbol}-{market.quote}'
            spec = self.okx.instrument(ident, 'SPOT')
            if spec.get('baseCcy') != symbol or spec.get('quoteCcy') != market.quote:
                raise DataUnavailable('OKX instrument currency mismatch')
            if base_symbol.upper() not in (symbol, 'W' + symbol):
                raise DataUnavailable(f'Contract symbol {base_symbol} differs from OKX {symbol}')

            def check():
                if stopped():
                    raise DataUnavailable('Sampling stopped')

            block = reader.block('latest')
            pools = reader.discover(market, block['number'], check)
            if not pools:
                raise DataUnavailable('No supported direct V3 pool')
            native = Market(market.chain, NATIVE_WRAPPED[market.chain], 'Gas', market.quote)
            gas_pool = pools[0] if native.token == market.token else next(iter(reader.discover(native, block['number'], check)), None)
            if gas_pool is None:
                raise DataUnavailable('Gas conversion pool is unavailable')
            gas_quote_unit = 10**reader.decimals(market.quote_address, block['number'])
            native_first = int(native.token, 16) < int(native.quote_address, 16)
            gas_conversion = reader.state(gas_pool, block['number'], native_first, 10**18, gas_quote_unit)['price']
            gas_price = int(reader.rpc('eth_gasPrice'), 16)
            # Refresh the block after discovery to keep the two venues' quotes reasonably aligned.
            block = reader.block('latest')
            book = self.okx.book(ident)
            base_unit = 10**reader.decimals(market.token, block['number'])
            quantity = shared_quantity(settings.notional, book.mid, spec['lotSz'], str(Decimal(1) / base_unit))
            if quantity < Decimal(spec['minSz']):
                raise DataUnavailable('Quantity is below the OKX minimum')
            qty = float(quantity)
            quotes = []
            for pool in pools:
                check()
                try:
                    result = reader.quote(market, pool, block['number'], int(quantity * base_unit), gas_quote_unit)
                    quotes.append((pool, result))
                except DataUnavailable:
                    continue
            if not quotes:
                raise DataUnavailable('No complete two-way quote for the requested quantity')
            buy, quotes_buy = min(quotes, key=lambda item: item[1]['buy_quote'])
            sell, quotes_sell = max(quotes, key=lambda item: item[1]['sell_quote'])
            observed = now_ms()
            check_age(book.timestamp, observed, 25_000, 'OKX book')
            check_age(block['ts'], observed, 60_000, 'DEX quote block')
            if abs(book.timestamp - block['ts']) > 45_000:
                raise DataUnavailable('OKX book and DEX block differ by more than 45 seconds')
            cex_buy, cex_sell = book.value(qty, True), book.value(qty, False)
            dex_buy, dex_sell = quotes_buy['buy_quote'], quotes_sell['sell_quote']
            gas_cost = settings.gas_units * gas_price / 10**18 * gas_conversion
            options = {}
            for side, gross, cex in (('dex_buy', cex_sell - dex_buy, cex_sell), ('cex_buy', dex_sell - cex_buy, cex_buy)):
                fee = cex * settings.spot_bp / 10_000
                options[side] = {'gross': gross, 'cex_fee': fee, 'gas_cost': gas_cost, 'net': gross - fee - gas_cost}
            row = {'symbol': symbol, 'market_key': market.key, 'quote': market.quote, 'observed': observed,
                   'block': block, 'spot_ts': book.timestamp, 'pool': buy.address, 'sell_pool': sell.address,
                   'market': {**vars(market), 'symbol': base_symbol}, 'target': settings.notional, 'quantity': qty,
                   'cex_buy': cex_buy, 'cex_sell': cex_sell, 'dex_buy': dex_buy, 'dex_sell': dex_sell,
                   'gas_price': gas_price, 'gas_conversion': gas_conversion, 'spot_book': {'bids': book.bids, 'asks': book.asks},
                   'opportunities': options, 'setup': [market.key, symbol, settings.notional, settings.spot_bp,
                                                       settings.gas_units, buy.address, sell.address]}
        except SCAN_ERRORS as exc:
            row = {'symbol': symbol, 'market_key': market.key, 'quote': market.quote, 'market': vars(market),
                   'observed': observed, 'error': str(exc)}
        if not stopped():
            update(self.store.sample('dex', market.key, row))
