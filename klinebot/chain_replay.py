"""Reconstruct direct V3 pool activity around a saved alert or finalized window."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
import json
import re

from .alt_monitor_core import DAY_MS, TOKENS, now_ms, payload
from .alt_monitor_data import PublicClient
from .alt_opportunity_market import BURN, MINT, DataUnavailable, Ethereum, MINUTE, address_word, finite, request_json, signed, unpack_words, word

UNI_SWAP = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
CAKE_SWAP = '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83'
MULTICALL3 = '0xcA11bde05977b3631167028862bE2a173976CA11'
BLOCKS_PER_LOG_PAGE = 600
RECENT = 'recent'
ADDRESS = re.compile(r'0x[0-9a-fA-F]{40}\Z')


@dataclass(frozen=True)
class Venue:
    name: str
    factory: str
    quoter: str
    fees: tuple
    swap: str


@dataclass(frozen=True)
class Network:
    name: str
    rpc: str
    alternate: str
    quotes: dict
    venues: tuple


PANCAKE = Venue('PancakeSwap v3', '0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865',
                '0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997', (100, 500, 2500, 10000), CAKE_SWAP)
NETWORKS = {
    56: Network('BNB Smart Chain', 'https://bsc-rpc.publicnode.com', 'https://bsc.drpc.org',
                {'USDT': '0x55d398326f99059ff775485246999027b3197955', 'USDC': '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d'},
                (PANCAKE, Venue('Uniswap v3', '0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7',
                                '0x78D78E420Da98ad378D7799bE8f4AF69033EB077', (100, 500, 3000, 10000), UNI_SWAP))),
    1: Network('Ethereum', 'https://ethereum-rpc.publicnode.com', 'https://eth.drpc.org',
               {'USDT': '0xdac17f958d2ee523a2206206994597c13d831ec7', 'USDC': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'},
               (Venue('Uniswap v3', '0x1F98431c8aD98523631AE4a59f267346ea31F984',
                      '0x61fFE014bA17989E743c5F6cB21bF9697530B21e', (100, 500, 3000, 10000), UNI_SWAP), PANCAKE)),
}


@dataclass(frozen=True)
class Market:
    chain: int
    token: str
    symbol: str
    quote: str = 'USDT'

    def __post_init__(self):
        object.__setattr__(self, 'token', self.token.strip().lower())
        if self.chain not in NETWORKS or self.quote not in NETWORKS[self.chain].quotes:
            raise DataUnavailable('Unsupported chain or quote currency')
        if not ADDRESS.fullmatch(self.token) or int(self.token, 16) == 0:
            raise DataUnavailable('Invalid ERC-20 contract address')
        if self.token == self.quote_address:
            raise DataUnavailable('Base and quote tokens must differ')

    @property
    def quote_address(self):
        return NETWORKS[self.chain].quotes[self.quote]

    @property
    def key(self):
        return f'{self.chain}:{self.token}:{self.quote}'

    @property
    def radar_symbol(self):
        return next((name for name, info in TOKENS.items() if self.chain == 56 and info['contract'] == self.token), None)


@dataclass(frozen=True)
class Pool:
    venue: Venue
    address: str
    fee: int

    @property
    def name(self):
        return f'{self.venue.name} {self.fee / 10_000:g}%'


def catalog_markets(client=None):
    markets = {}
    for item in (client or PublicClient()).get('tokens'):
        if item.get('offline') or item.get('fullyDelisted'):
            continue
        chain = str(item.get('chainId', ''))
        token = str(item.get('contractAddress', '')).strip().lower()
        label = str(item.get('symbol', '')).strip()[:24]
        if not chain.isdecimal() or int(chain) not in NETWORKS or not label or not ADDRESS.fullmatch(token):
            continue
        network = NETWORKS[int(chain)]
        quote = next((name for name, address in network.quotes.items() if address != token), None)
        if quote and int(token, 16):
            market = Market(int(chain), token, label, quote)
            markets[(market.chain, market.token)] = market
    return sorted(markets.values(), key=lambda m: (m.symbol.upper(), m.chain, m.token))


def market_from_alpha(row):
    try:
        chain = int(row['chain_id'])
        quote = row['quote'] if row['quote'] in ('USDT', 'USDC') else 'USDT'
        return Market(chain, row['contract'], row['symbol'], quote)
    except (TypeError, ValueError, KeyError, DataUnavailable):
        return None


class ChainReader:
    def __init__(self, chain, fetch=request_json):
        self.fetch = fetch
        self.network = NETWORKS[chain]

    def rpc(self, method, *params):
        body = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
        failures = []
        for host in (self.network.rpc, self.network.alternate):
            try:
                reply = self.fetch(host, f'{self.network.name} RPC', body)
                if not isinstance(reply, dict) or reply.get('result') is None:
                    error = reply.get('error') if isinstance(reply, dict) else None
                    message = error.get('message') if isinstance(error, dict) else None
                    raise DataUnavailable(message or 'missing result')
                return reply['result']
            except DataUnavailable as exc:
                failures.append(str(exc))
        raise DataUnavailable(f'{method}: {" / ".join(failures)}')

    def block(self, number='finalized'):
        tag = hex(number) if isinstance(number, int) else number
        raw = self.rpc('eth_getBlockByNumber', tag, False)
        return {'number': int(raw['number'], 16), 'ts': int(raw['timestamp'], 16) * 1000, 'hash': raw['hash']}

    def call(self, address, data, block):
        return unpack_words(self.raw_call(address, data, block))

    def raw_call(self, address, data, block):
        args = ({'to': address, 'data': '0x' + data}, hex(block))
        try:
            return self.rpc('eth_call', *args)
        except DataUnavailable as exc:
            raise DataUnavailable(f'Block {block}: {exc}') from exc

    def calls(self, address, selectors, block):
        return Ethereum.calls(self, address, selectors, block, multicall=MULTICALL3, raw_call=self.raw_call)

    def decimals(self, address, block):
        places = self.call(address, '313ce567', block)[0]
        if not 0 <= places <= 36:
            raise DataUnavailable('Unsupported ERC-20 decimals')
        return places

    def symbol(self, address, block):
        try:
            values = self.call(address, '95d89b41', block)
            data = b''.join(value.to_bytes(32, 'big') for value in values)
            if len(data) >= 64 and values[0] == 32:
                data = data[64:64 + min(values[1], 24)]
            elif len(data) == 32:
                data = data.rstrip(b'\0')
            else:
                return ''
            return ''.join(ch for ch in data.decode('utf-8', 'ignore') if ch.isprintable()).strip()[:24]
        except DataUnavailable:
            return ''

    def state(self, pool, block, base_first, base_unit, quote_unit):
        values = self.call(pool.address, '3850c7bd', block)
        if len(values) < 2 or values[0] <= 0:
            raise DataUnavailable('Invalid pool slot0')
        ratio = (values[0] / 2**96)**2
        price = (ratio if base_first else 1 / ratio) * base_unit / quote_unit
        return {'price': price, 'tick': signed(values[1])}

    def quote(self, market, pool, block, quantity, quote_unit):

        def amount(selector, token_in, token_out, size):
            data = selector + address_word(token_in) + address_word(token_out) + word(size) + word(pool.fee) + word(0)
            return self.call(pool.venue.quoter, data, block)[0]

        buy = amount('bd21704a', market.quote_address, market.token, quantity)
        sell = amount('c6a5026a', market.token, market.quote_address, quantity)
        if not buy or not sell:
            raise DataUnavailable('Insufficient liquidity for the requested quantity')
        # Some exact-input quotes can reach the price limit before consuming all input.
        used = amount('bd21704a', market.token, market.quote_address, sell)
        if abs(used - quantity) > max(1, quantity // 100_000):
            raise DataUnavailable('Incomplete sell quote for the requested quantity')
        return {'buy_quote': buy / quote_unit, 'sell_quote': sell / quote_unit}

    def discover(self, market, block, check):
        args = address_word(market.token) + address_word(market.quote_address)

        def inspect(job):
            check()
            venue, fee = job
            address = self.call(venue.factory, '1698ee82' + args + word(fee), block)[0]
            if not address:
                return None
            pool = Pool(venue, f'0x{address:040x}', fee)
            liquidity = self.call(pool.address, '1a686502', block)[0]
            return pool, liquidity

        jobs = [(venue, fee) for venue in self.network.venues for fee in venue.fees]
        with ThreadPoolExecutor(max_workers=2) as workers:
            candidates = [result for result in workers.map(inspect, jobs) if result]
        active = sorted((row for row in candidates if row[1] > 0), key=lambda row: (-row[1], row[0].fee, row[0].address))
        if not active:
            # Existing empty pools can still contain historical withdrawals worth reviewing.
            return [pool for pool, _ in candidates[:2]]
        first = active[0][0]
        preferred = next((pool for pool, _ in active[1:] if pool.venue != first.venue), None)
        second = preferred or (active[1][0] if len(active) > 1 else None)
        return [first, second] if second else [first]

    def logs(self, pools, start, end):
        filters = {'fromBlock': hex(start), 'toBlock': hex(end), 'address': list(pools),
                   'topics': [[UNI_SWAP, CAKE_SWAP, MINT, BURN]]}
        events = self.rpc('eth_getLogs', filters)
        if not isinstance(events, list):
            raise DataUnavailable('Malformed pool log response')
        if len(events) >= 5000:
            if end == start:
                raise DataUnavailable('Too many logs in one block to verify completeness')
            middle = (start + end) // 2
            return self.logs(pools, start, middle) + self.logs(pools, middle + 1, end)
        return events


def find_points(reader, tip, targets, progress, block_ms=250):
    cache = {tip['number']: tip}

    def block(number):
        if number not in cache:
            cache[number] = reader.block(number)
        return cache[number]

    distance = max(128, (tip['ts'] - targets[0]) // block_ms)
    low = block(max(0, tip['number'] - distance))
    while low['ts'] > targets[0] and low['number']:
        distance *= 2
        low = block(max(0, tip['number'] - distance))
    if low['ts'] > targets[0] or targets[-1] > tip['ts']:
        raise DataUnavailable('The complete replay window is outside available finalized blocks')
    results = []
    for index, target in enumerate(targets):
        high = tip
        attempts = 0
        while high['number'] - low['number'] > 1:
            size = high['number'] - low['number']
            if attempts < 8 and high['ts'] > low['ts']:
                offset = size * (target - low['ts']) // (high['ts'] - low['ts'])
                offset = max(1, min(size - 1, offset))
            else:
                offset = size // 2
            candidate = block(low['number'] + offset)
            if candidate['ts'] <= target:
                low = candidate
            else:
                high = candidate
            attempts += 1
        point = high if high['ts'] <= target else low
        results.append(point)
        low = point
        progress('定位区块 {0}/{1}', index + 1, len(targets))
    return results


def decode_log(row, pools, base_first, base_unit, quote_unit):
    if row.get('removed'):
        raise DataUnavailable('A pool log was reorganized. Run the replay again.')
    pool = pools.get(row['address'].lower())
    if pool is None:
        raise DataUnavailable('Log from an unexpected pool')
    topics = row['topics']
    kind = topics[0].lower()
    values = unpack_words(row['data'])
    data = {'pool': pool.name, 'block': int(row['blockNumber'], 16), 'log': int(row['logIndex'], 16),
            'tx': row['transactionHash'], 'block_hash': row['blockHash'],
            'topics': topics, 'data_hex': row['data']}
    if row.get('blockTimestamp'):
        data['ts'] = int(row['blockTimestamp'], 16) * 1000
    if kind == pool.venue.swap:
        if len(values) != (7 if kind == CAKE_SWAP else 5):
            raise DataUnavailable('Unexpected Swap event layout')
        amount0, amount1 = map(signed, values[:2])
        data.update(kind='swap', tick=signed(values[4]))
    elif kind in (MINT, BURN):
        offset = int(kind == MINT)
        if len(topics) != 4 or len(values) != 3 + offset:
            raise DataUnavailable('Unexpected liquidity event layout')
        if not values[offset]:  # Fee accounting can emit a Burn with zero liquidity removed.
            return None
        amount0, amount1 = values[offset + 1:offset + 3]
        data.update(kind='mint' if offset else 'burn', liquidity=str(values[offset]),
                    tick_low=signed(int(topics[2], 16)), tick_high=signed(int(topics[3], 16)),
                    pool_owner='0x' + topics[1][-40:])
    else:
        raise DataUnavailable('Unrecognized pool event')
    base, quote = (amount0, amount1) if base_first else (amount1, amount0)
    data.update(base=base / base_unit, quote=quote / quote_unit)
    return data


def summarize(events, start, anchor):
    ticks = {pool: row.get('tick') for pool, row in start['pools'].items()}
    summary = {side: {pool: {'swaps': 0, 'net_quote': 0, 'mints': 0, 'burns': 0, 'active_burns': 0,
                            'unknown_burns': 0} for pool in start['pools']} for side in ('before', 'after')}
    for event in events:
        pool, kind = event['pool'], event['kind']
        part = 'before' if event['block'] <= anchor['number'] else 'after'
        counts = summary[part][pool]
        if kind == 'swap':
            counts['swaps'] += 1
            counts['net_quote'] += event['quote']
            ticks[pool] = event['tick']
        else:
            tick = ticks.get(pool)
            event['active'] = None if tick is None else event['tick_low'] <= tick < event['tick_high']
            counts[kind + 's'] += 1
            if kind == 'burn' and event['active'] is None:
                counts['unknown_burns'] += 1
            if kind == 'burn' and event['active']:
                counts['active_burns'] += 1
    return summary


def run_replay(market, anchor, minutes, nominal, cancelled=lambda: False, progress=lambda *args: None, reader=None):
    reader = reader or ChainReader(market.chain)
    amount = finite(nominal)
    if minutes not in (3, 5, 10) or not 10 <= amount <= 25_000:
        raise DataUnavailable('Choose a 3, 5 or 10 minute window and a nominal size of 10–25,000.')

    def check():
        if cancelled():
            raise DataUnavailable('Replay cancelled')

    def status(*args):
        check()
        progress(*args)

    if int(reader.rpc('eth_chainId'), 16) != market.chain:
        raise DataUnavailable('Unexpected chain ID')
    tip = reader.block()
    margin = minutes * MINUTE
    center = int(anchor['ms']) if anchor else tip['ts'] - margin
    if center < tip['ts'] - 31 * DAY_MS:
        raise DataUnavailable('This replay window is more than 31 days old')
    begin, middle, end = find_points(reader, tip, (center - margin, center, center + margin), status,
                                     block_ms=250 if market.chain == 56 else 8_000)
    if end['number'] - begin['number'] > 12_000:
        raise DataUnavailable('This replay window spans too many blocks')
    points = [dict(block, pools={}) for block in (begin, middle, end)]
    base_unit = 10**reader.decimals(market.token, tip['number'])
    quote_unit = 10**reader.decimals(market.quote_address, tip['number'])
    symbol = market.symbol or reader.symbol(market.token, tip['number']) or f'{market.token[:8]}…{market.token[-4:]}'
    market_data = {**vars(market), 'symbol': symbol, 'base_unit': str(base_unit), 'quote_unit': str(quote_unit)}
    base_first = int(market.token, 16) < int(market.quote_address, 16)
    status('查找交易池…')
    pool_selection = begin
    try:
        pools = reader.discover(market, begin['number'], check)
    except DataUnavailable:
        check()
        pool_selection = tip
        pools = reader.discover(market, tip['number'], check)
    if not pools:
        raise DataUnavailable(f'No supported direct V3 pool for {symbol}/{market.quote} on {NETWORKS[market.chain].name}')
    by_address = {pool.address: pool for pool in pools}

    def state(job):
        point, pool = job
        check()
        try:
            return point, pool, reader.state(pool, point['number'], base_first, base_unit, quote_unit)
        except DataUnavailable as exc:
            return point, pool, {'error': str(exc)}

    jobs = [(point, pool) for point in points for pool in pools]
    status('读取预警前后池状态与报价…')
    with ThreadPoolExecutor(max_workers=2) as workers:
        for point, pool, row in workers.map(state, jobs):
            point['pools'][pool.name] = row
    # The common quantity is frozen using the first available checkpoint, and is never resized on later quotes.
    reference = next((p for p in points if any('price' in row for row in p['pools'].values())), None)
    quantity = None
    if reference:
        prices = [row['price'] for row in reference['pools'].values() if 'price' in row]
        quantity = int(Decimal(str(amount)) * base_unit / Decimal(str(sum(prices) / len(prices))))
        if quantity <= 0:
            raise DataUnavailable('Reference amount is too small for the base token precision')

        def quote(job):
            point, pool = job
            check()
            row = point['pools'][pool.name]
            if 'price' in row:
                try:
                    return point, pool, {**row, **reader.quote(market, pool, point['number'], quantity, quote_unit)}
                except DataUnavailable as exc:
                    return point, pool, {**row, 'error': str(exc)}
            return point, pool, row

        with ThreadPoolExecutor(max_workers=2) as workers:
            for point, pool, row in workers.map(quote, jobs):
                point['pools'][pool.name] = row
    events = {}
    start_blocks = range(begin['number'] + 1, end['number'] + 1, BLOCKS_PER_LOG_PAGE)
    for index, start in enumerate(start_blocks, 1):
        status('读取池事件 {0}/{1}', index, len(start_blocks))
        last = min(start + BLOCKS_PER_LOG_PAGE - 1, end['number'])
        for raw in reader.logs(by_address, start, last):
            event = decode_log(raw, by_address, base_first, base_unit, quote_unit)
            if event:
                if not start <= event['block'] <= last:
                    raise DataUnavailable('RPC returned a pool log outside the requested block range')
                key = event['block'], event['log']
                if key in events and events[key] != event:
                    raise DataUnavailable('Conflicting pool logs for the same block and log index')
                events[key] = event
    ordered = [event for _, event in sorted(events.items())]
    summary = summarize(ordered, points[0], middle)
    check()
    return {'anchor': anchor or {'ms': center, 'kind': RECENT}, 'minutes': minutes, 'nominal_quote': amount,
            'market': market_data, 'quantity_base': quantity / base_unit if quantity else None,
            'quantity_raw': str(quantity) if quantity else None, 'pool_selection_block': pool_selection['number'],
            'pool_selection_fallback': pool_selection == tip and pool_selection != begin,
            'quantity_reference_block': reference['number'] if reference else None,
            'points': points, 'events': ordered, 'summary': summary,
            'pool_addresses': {pool.name: pool.address for pool in pools},
            'quote_gaps': sum('buy_quote' not in row for p in points for row in p['pools'].values())}


class ChainReplayStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS evaa_replays(
                id INTEGER PRIMARY KEY, created INTEGER NOT NULL, anchor INTEGER NOT NULL, evidence TEXT NOT NULL)''')
            if 'market_key' not in {row['name'] for row in db.execute('PRAGMA table_info(evaa_replays)')}:
                db.execute('ALTER TABLE evaa_replays ADD COLUMN market_key TEXT')
                db.execute('UPDATE evaa_replays SET market_key=?', (legacy_market().key,))
            db.execute('CREATE INDEX IF NOT EXISTS chain_replays_market ON evaa_replays(market_key, id DESC)')

    def save(self, evidence):
        data = evidence['market']
        market = Market(data['chain'], data['token'], data['symbol'], data['quote'])
        with self.store.connect() as db:
            cursor = db.execute('INSERT INTO evaa_replays(created,anchor,evidence,market_key) VALUES(?,?,?,?)',
                                (now_ms(), evidence['anchor']['ms'], payload(evidence), market.key))
            return cursor.lastrowid

    def runs(self, market):
        with self.store.connect() as db:
            rows = db.execute('SELECT id, anchor FROM evaa_replays WHERE market_key=? ORDER BY id DESC LIMIT 40',
                              (market.key,)).fetchall()
        return [dict(row) for row in rows]

    def saved_markets(self):
        with self.store.connect() as db:
            rows = db.execute('''SELECT market_key, json_extract(evidence,'$.market.symbol') AS symbol
                FROM evaa_replays WHERE id IN (SELECT MAX(id) FROM evaa_replays GROUP BY market_key)
                ORDER BY id DESC LIMIT 100''').fetchall()
        markets = []
        for row in rows:
            chain, token, quote = row['market_key'].split(':')
            symbol = row['symbol'] or (legacy_market().symbol if row['market_key'] == legacy_market().key else '')
            markets.append(Market(int(chain), token, symbol, quote))
        return markets

    def load(self, ident):
        with self.store.connect() as db:
            row = db.execute('SELECT evidence FROM evaa_replays WHERE id=?', (ident,)).fetchone()
        if not row:
            return None
        result = json.loads(row['evidence'])
        if 'market' not in result:
            result['market'] = vars(legacy_market())
            result['quantity_base'] = result['quantity_evaa']
            result['nominal_quote'] = result['nominal_usdt']
            for point in result['points']:
                for quote in point['pools'].values():
                    if 'buy_usdt' in quote:
                        quote.update(buy_quote=quote['buy_usdt'], sell_quote=quote['sell_usdt'])
            for event in result['events']:
                event.update(base=event['evaa'], quote=event['usdt'])
            for pools in result['summary'].values():
                for summary in pools.values():
                    summary['net_quote'] = summary['net_usdt']
        return result


def legacy_market():
    return Market(56, TOKENS['EVAA']['contract'], 'EVAA')
