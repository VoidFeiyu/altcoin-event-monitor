"""Reconstruct EVAA/USDT pool activity around a saved radar alert."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

from .alt_monitor_core import DAY_MS, now_ms, payload
from .alt_opportunity_market import DataUnavailable, MINUTE, address_word, finite, request_json, signed, unpack_words, word
from .alt_opportunity_lp import BURN, MINT

PUBLICNODE = 'https://bsc-rpc.publicnode.com'
DRPC = 'https://bsc.drpc.org'
USDT = '0x55d398326f99059ff775485246999027b3197955'
EVAA = '0xaa036928c9c0df07d525b55ea8ee690bb5a628c1'
UNIT = 10**18
UNI_SWAP = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
CAKE_SWAP = '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83'
POOLS = {
    'Uniswap v3': ('0xccb5e080c38e4e7a1c6437db53bcbe016e45b9db', '0x78D78E420Da98ad378D7799bE8f4AF69033EB077', 3000, UNI_SWAP),
    'PancakeSwap v3': ('0x26deb24a2623cf54452ab5183e2c34551831d54d', '0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997', 100, CAKE_SWAP),
}
POOL_NAMES = {spec[0]: name for name, spec in POOLS.items()}
BLOCKS_PER_LOG_PAGE = 600
RECENT = 'recent'


class BnbReader:
    def __init__(self, fetch=request_json):
        self.fetch = fetch

    def rpc(self, method, *params, host=PUBLICNODE):
        body = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}
        reply = self.fetch(host, 'BNB RPC', body)
        if not isinstance(reply, dict) or reply.get('result') is None:
            error = reply.get('error') if isinstance(reply, dict) else None
            raise DataUnavailable(f'{method}: {(error or {}).get("message", "missing result")}')
        return reply['result']

    def block(self, number='finalized'):
        tag = hex(number) if isinstance(number, int) else number
        raw = self.rpc('eth_getBlockByNumber', tag, False)
        return {'number': int(raw['number'], 16), 'ts': int(raw['timestamp'], 16) * 1000, 'hash': raw['hash']}

    def call(self, address, data, block):
        args = ({'to': address, 'data': '0x' + data}, hex(block))
        try:
            return unpack_words(self.rpc('eth_call', *args))
        except DataUnavailable as exc:
            try:
                return unpack_words(self.rpc('eth_call', *args, host=DRPC))
            except DataUnavailable as fallback:
                raise DataUnavailable(f'Block {block}: {exc} / {fallback}') from fallback

    def state(self, pool, block):
        address = POOLS[pool][0]
        values = self.call(address, '3850c7bd', block)
        if len(values) < 2 or values[0] <= 0:
            raise DataUnavailable('Invalid pool slot0')
        return {'price': 1 / (values[0] / 2**96)**2, 'tick': signed(values[1])}

    def quote(self, pool, block, quantity):
        _, quoter, tier, _ = POOLS[pool]

        def amount(selector, token_in, token_out, size):
            data = selector + address_word(token_in) + address_word(token_out) + word(size) + word(tier) + word(0)
            return self.call(quoter, data, block)[0]

        buy = amount('bd21704a', USDT, EVAA, quantity)
        sell = amount('c6a5026a', EVAA, USDT, quantity)
        if not buy or not sell:
            raise DataUnavailable('Insufficient liquidity for requested EVAA quantity')
        # Some exact-input quotes can reach the price limit before consuming all input.
        used = amount('bd21704a', EVAA, USDT, sell)
        if abs(used - quantity) * 100_000 > quantity:
            raise DataUnavailable('Incomplete sell quote for requested EVAA quantity')
        return {'buy_usdt': buy / UNIT, 'sell_usdt': sell / UNIT}

    def logs(self, start, end):
        filters = {'fromBlock': hex(start), 'toBlock': hex(end), 'address': list(POOL_NAMES),
                   'topics': [[UNI_SWAP, CAKE_SWAP, MINT, BURN]]}
        events = self.rpc('eth_getLogs', filters)
        if not isinstance(events, list):
            raise DataUnavailable('Malformed pool log response')
        if len(events) >= 5000:
            if end == start:
                raise DataUnavailable('Too many logs in one block to verify completeness')
            middle = (start + end) // 2
            return self.logs(start, middle) + self.logs(middle + 1, end)
        return events


def find_points(reader, tip, targets, progress):
    cache = {tip['number']: tip}

    def block(number):
        if number not in cache:
            cache[number] = reader.block(number)
        return cache[number]

    distance = max(128, (tip['ts'] - targets[0]) // 250)
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


def decode_log(row):
    if row.get('removed'):
        raise DataUnavailable('A pool log was reorganized. Run the replay again.')
    name = POOL_NAMES.get(row['address'].lower())
    if name is None:
        raise DataUnavailable('Log from an unexpected pool')
    topics = row['topics']
    kind = topics[0].lower()
    values = unpack_words(row['data'])
    data = {'pool': name, 'block': int(row['blockNumber'], 16), 'log': int(row['logIndex'], 16),
            'tx': row['transactionHash'], 'block_hash': row['blockHash'],
            'topics': topics, 'data_hex': row['data']}
    if row.get('blockTimestamp'):
        data['ts'] = int(row['blockTimestamp'], 16) * 1000
    if kind == POOLS[name][3]:
        if len(values) != (7 if kind == CAKE_SWAP else 5):
            raise DataUnavailable('Unexpected Swap event layout')
        data.update(kind='swap', usdt=signed(values[0]) / UNIT, evaa=signed(values[1]) / UNIT,
                    tick=signed(values[4]))
    elif kind in (MINT, BURN):
        offset = int(kind == MINT)
        if len(topics) != 4 or len(values) != 3 + offset:
            raise DataUnavailable('Unexpected liquidity event layout')
        if not values[offset]:  # Fee accounting can emit a Burn with zero liquidity removed.
            return None
        data.update(kind='mint' if offset else 'burn', liquidity=str(values[offset]),
                    usdt=values[offset + 1] / UNIT, evaa=values[offset + 2] / UNIT,
                    tick_low=signed(int(topics[2], 16)), tick_high=signed(int(topics[3], 16)),
                    pool_owner='0x' + topics[1][-40:])
    else:
        raise DataUnavailable('Unrecognized pool event')
    return data


def summarize(events, start, anchor):
    ticks = {pool: row.get('tick') for pool, row in start['pools'].items()}
    summary = {side: {pool: {'swaps': 0, 'net_usdt': 0, 'mints': 0, 'burns': 0, 'active_burns': 0,
                            'unknown_burns': 0} for pool in POOLS} for side in ('before', 'after')}
    for event in events:
        pool, kind = event['pool'], event['kind']
        part = 'before' if event['block'] <= anchor['number'] else 'after'
        counts = summary[part][pool]
        if kind == 'swap':
            counts['swaps'] += 1
            counts['net_usdt'] += event['usdt']
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


def run_replay(anchor, minutes, nominal, cancelled=lambda: False, progress=lambda *args: None, reader=None):
    reader = reader or BnbReader()
    amount = finite(nominal)
    if minutes not in (3, 5, 10) or not 10 <= amount <= 25_000:
        raise DataUnavailable('Choose a 3, 5 or 10 minute window and 10–25,000 USDT.')

    def check():
        if cancelled():
            raise DataUnavailable('Replay cancelled')

    def status(*args):
        check()
        progress(*args)

    if int(reader.rpc('eth_chainId'), 16) != 56:
        raise DataUnavailable('Unexpected chain ID')
    tip = reader.block()
    margin = minutes * MINUTE
    center = int(anchor['ms']) if anchor else tip['ts'] - margin
    if center < tip['ts'] - 31 * DAY_MS:
        raise DataUnavailable('This replay window is more than 31 days old')
    begin, middle, end = find_points(reader, tip, (center - margin, center, center + margin), status)
    if end['number'] - begin['number'] > 12_000:
        raise DataUnavailable('This replay window spans too many blocks')
    points = [dict(block, pools={}) for block in (begin, middle, end)]

    def state(job):
        point, pool = job
        check()
        try:
            return point, pool, reader.state(pool, point['number'])
        except DataUnavailable as exc:
            return point, pool, {'error': str(exc)}

    jobs = [(point, pool) for point in points for pool in POOLS]
    status('读取预警前后池状态与报价…')
    with ThreadPoolExecutor(max_workers=2) as workers:
        for point, pool, row in workers.map(state, jobs):
            point['pools'][pool] = row
    # The common quantity is frozen using the first available checkpoint, and is never resized on later quotes.
    reference = next((p for p in points if any('price' in row for row in p['pools'].values())), None)
    quantity = None
    if reference:
        prices = [row['price'] for row in reference['pools'].values() if 'price' in row]
        quantity = int(amount / (sum(prices) / len(prices)) * 10**8) * 10**10

        def quote(job):
            point, pool = job
            check()
            row = point['pools'][pool]
            if 'price' in row:
                try:
                    return point, pool, {**row, **reader.quote(pool, point['number'], quantity)}
                except DataUnavailable as exc:
                    return point, pool, {**row, 'error': str(exc)}
            return point, pool, row

        with ThreadPoolExecutor(max_workers=2) as workers:
            for point, pool, row in workers.map(quote, jobs):
                point['pools'][pool] = row
    events = {}
    start_blocks = range(begin['number'] + 1, end['number'] + 1, BLOCKS_PER_LOG_PAGE)
    for index, start in enumerate(start_blocks, 1):
        status('读取池事件 {0}/{1}', index, len(start_blocks))
        last = min(start + BLOCKS_PER_LOG_PAGE - 1, end['number'])
        for raw in reader.logs(start, last):
            event = decode_log(raw)
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
    return {'anchor': anchor or {'ms': center, 'kind': RECENT}, 'minutes': minutes, 'nominal_usdt': amount,
            'quantity_evaa': quantity / UNIT if quantity else None, 'quantity_raw': str(quantity) if quantity else None,
            'quantity_reference_block': reference['number'] if reference else None,
            'points': points, 'events': ordered, 'summary': summary,
            'pool_addresses': {name: spec[0] for name, spec in POOLS.items()},
            'quote_gaps': sum('buy_usdt' not in row for p in points for row in p['pools'].values())}


class EvaaReplayStore:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS evaa_replays(
                id INTEGER PRIMARY KEY, created INTEGER NOT NULL, anchor INTEGER NOT NULL, evidence TEXT NOT NULL)''')

    def save(self, evidence):
        with self.store.connect() as db:
            cursor = db.execute('INSERT INTO evaa_replays(created,anchor,evidence) VALUES(?,?,?)',
                                (now_ms(), evidence['anchor']['ms'], payload(evidence)))
            return cursor.lastrowid

    def runs(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT id, anchor FROM evaa_replays ORDER BY id DESC LIMIT 40').fetchall()
        return [dict(row) for row in rows]

    def load(self, ident):
        with self.store.connect() as db:
            row = db.execute('SELECT evidence FROM evaa_replays WHERE id=?', (ident,)).fetchone()
        return json.loads(row['evidence']) if row else None
