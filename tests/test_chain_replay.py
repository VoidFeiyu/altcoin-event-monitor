from tempfile import TemporaryDirectory
from unittest import TestCase

from klinebot.alt_monitor_core import Store
from klinebot.alt_opportunity_market import DataUnavailable, word
from klinebot.chain_replay import (BURN, CAKE_SWAP, MINT, NETWORKS, UNI_SWAP, ChainReplayStore, Market, Pool,
                                  catalog_markets, decode_log, find_points, legacy_market, run_replay, summarize)

UNIT = 10**18
VENUES = {v.name: v for v in NETWORKS[56].venues}
POOL_SPECS = {
    'Uniswap v3': Pool(VENUES['Uniswap v3'], '0x' + '1'*40, 3000),
    'PancakeSwap v3': Pool(VENUES['PancakeSwap v3'], '0x' + '2'*40, 100),
}
POOLS = {pool.address: pool for pool in POOL_SPECS.values()}
UNI, CAKE = POOL_SPECS['Uniswap v3'].name, POOL_SPECS['PancakeSwap v3'].name


def make_log(pool, topic, number, index, values, ticks=None):
    topics = [topic, '0x' + word(12)]
    topics += ['0x' + word(tick) for tick in ticks] if ticks else ['0x' + word(13)]
    return {'address': POOL_SPECS[pool].address, 'topics': topics, 'blockNumber': hex(number), 'logIndex': hex(index),
            'blockHash': '0x' + word(number), 'transactionHash': '0x' + word(index),
            'data': '0x' + ''.join(word(value) for value in values)}


def decode(row):
    return decode_log(row, POOLS, False, UNIT, UNIT)


class ChainStub:
    tip = 10_000

    def block(self, number=None):
        number = self.tip if number is None else number
        return {'number': number, 'ts': number // 2 * 1000, 'hash': '0x' + word(number)}

    def rpc(self, method):
        assert method == 'eth_chainId'
        return '0x38'

    def decimals(self, address, block):
        return 18

    def discover(self, market, block, check):
        return list(POOLS.values())

    def state(self, pool, block, base_first, base_unit, quote_unit):
        return {'price': 2, 'tick': -100}

    def quote(self, market, pool, block, quantity, quote_unit):
        if pool.name == UNI and block == self.tip:
            raise DataUnavailable('archived quote unavailable')
        return {'buy_quote': 2.03 * quantity / UNIT, 'sell_quote': 1.97 * quantity / UNIT}

    def logs(self, pools, start, end):
        return [make_log('Uniswap v3', UNI_SWAP, start, 0, (60*UNIT, -30*UNIT, 1, 100, -96)),
                make_log('Uniswap v3', BURN, start, 1, (12, 4*UNIT, 2*UNIT), (-98, -91)),
                make_log('PancakeSwap v3', CAKE_SWAP, end, 0, (-20*UNIT, 10*UNIT, 1, 100, -101, 0, 0))]


class ChainReplayTests(TestCase):
    def test_selects_end_of_second_and_keeps_finalized_tip(self):
        reader = ChainStub()
        points = find_points(reader, reader.block(), (4_640_000, 4_820_000, 5_000_000), lambda *args: None)
        self.assertEqual([p['number'] for p in points], [9281, 9641, 10000])

    def test_decodes_both_swap_layouts_and_nets_only_swaps(self):
        buy = decode(make_log('Uniswap v3', UNI_SWAP, 20, 0, (60*UNIT, -30*UNIT, 1, 100, -96)))
        remove = decode(make_log('Uniswap v3', BURN, 20, 1, (12, 4*UNIT, 2*UNIT), (-98, -91)))
        sell = decode(make_log('PancakeSwap v3', CAKE_SWAP, 21, 0, (-20*UNIT, 10*UNIT, 1, 100, -101, 0, 0)))
        before = {'pools': {p.name: {'tick': -100} for p in POOLS.values()}}
        result = summarize([buy, remove, sell], before, {'number': 20})
        self.assertTrue(remove['active'])
        self.assertEqual(result['before'][UNI]['net_quote'], 60)
        self.assertEqual(result['before'][UNI]['active_burns'], 1)
        self.assertEqual(result['after'][CAKE]['net_quote'], -20)

    def test_mint_layout_zero_burn_and_unknown_activity(self):
        mint = decode(make_log('PancakeSwap v3', MINT, 20, 0, (12, 25, UNIT, 2*UNIT), (-98, -91)))
        self.assertEqual((mint['liquidity'], mint['quote'], mint['base']), ('25', 1.0, 2.0))
        empty = decode(make_log('PancakeSwap v3', BURN, 20, 1, (0, 0, 0), (-98, -91)))
        self.assertIsNone(empty)
        burn = decode(make_log('Uniswap v3', BURN, 20, 2, (1, 0, 0), (-98, -91)))
        result = summarize([burn], {'pools': {UNI: {}}}, {'number': 20})
        self.assertIsNone(burn['active'])
        self.assertEqual(result['before'][UNI]['unknown_burns'], 1)

    def test_fixed_size_partial_quotes_and_persistence(self):
        anchor = {'kind': 'alert', 'id': 'test-alert', 'ms': 4_820_000, 'observed_ms': 4_825_000}
        market = legacy_market()
        result = run_replay(market, anchor, 3, 1000, reader=ChainStub())
        self.assertEqual(result['quantity_base'], 500)
        self.assertEqual(result['quote_gaps'], 1)
        self.assertEqual(result['anchor'], anchor)
        self.assertAlmostEqual(result['points'][0]['pools'][UNI]['buy_quote'], 1015)
        self.assertEqual(result['events'][0]['tx'], '0x' + word(0))
        self.assertEqual(result['events'][0]['topics'][0], UNI_SWAP)
        self.assertTrue(result['events'][0]['data_hex'].startswith('0x' + word(60 * UNIT)))
        with TemporaryDirectory() as folder:
            db = ChainReplayStore(Store(folder))
            ident = db.save(result)
            db = ChainReplayStore(Store(folder))
            self.assertEqual(db.runs(market), [{'id': ident, 'anchor': anchor['ms']}])
            self.assertEqual(db.runs(Market(56, market.token, market.symbol, 'USDC')), [])
            self.assertEqual(db.load(ident), result)

    def test_rejects_incomplete_finalized_window(self):
        anchor = {'ms': 4_900_000}
        with self.assertRaisesRegex(DataUnavailable, 'complete replay window'):
            run_replay(legacy_market(), anchor, 3, 1000, reader=ChainStub())

    def test_base_token_zero_and_different_quote_decimals(self):
        row = make_log('Uniswap v3', UNI_SWAP, 23, 0, (-2 * UNIT, 100 * 10**6, 1, 100, 4))
        event = decode_log(row, POOLS, True, UNIT, 10**6)
        self.assertEqual((event['base'], event['quote']), (-2, 100))

    def test_old_evaa_records_are_migrated_and_readable(self):
        import json
        old = {'anchor': {'ms': 12}, 'quantity_evaa': 50, 'nominal_usdt': 100,
               'points': [{'pools': {UNI: {'buy_usdt': 101, 'sell_usdt': 99}}}],
               'events': [{'evaa': -2, 'usdt': 4}], 'summary': {'before': {UNI: {'net_usdt': 4}}}}
        with TemporaryDirectory() as folder:
            store = Store(folder)
            with store.connect() as db:
                db.execute('CREATE TABLE evaa_replays(id INTEGER PRIMARY KEY, created INTEGER, anchor INTEGER, evidence TEXT)')
                db.execute('INSERT INTO evaa_replays VALUES (1,12,12,?)', (json.dumps(old),))
            ledger = ChainReplayStore(store)
            market = legacy_market()
            self.assertEqual(ledger.saved_markets(), [market])
            self.assertEqual(ledger.runs(market), [{'id': 1, 'anchor': 12}])
            result = ledger.load(1)
        self.assertEqual(result['market']['symbol'], 'EVAA')
        self.assertEqual(result['quantity_base'], 50)
        self.assertEqual(result['points'][0]['pools'][UNI]['buy_quote'], 101)
        self.assertEqual(result['events'][0]['base'], -2)

    def test_alpha_catalog_preserves_contract_identity(self):
        class Client:
            def get(self, route):
                return [{'symbol': 'DEMO', 'contractAddress': '0x' + 'a'*40, 'chainId': '1'},
                        {'symbol': 'DEMO', 'contractAddress': '0x' + 'b'*40, 'chainId': '56'},
                        {'symbol': 'DEMO', 'contractAddress': '0x' + 'c'*40, 'chainId': '999'},
                        {'symbol': 'OLD', 'contractAddress': '0x' + 'd'*40, 'chainId': '56', 'offline': True}]

        rows = catalog_markets(Client())
        self.assertEqual({row.chain for row in rows if row.symbol == 'DEMO'}, {1, 56})
        self.assertFalse(any(row.symbol == 'OLD' for row in rows))
        self.assertEqual(len(rows), 2)
