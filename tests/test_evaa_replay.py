from tempfile import TemporaryDirectory
from unittest import TestCase

from klinebot.alt_monitor_core import Store
from klinebot.alt_opportunity_market import DataUnavailable, word
from klinebot.evaa_replay import (BURN, CAKE_SWAP, MINT, POOLS, UNIT, UNI_SWAP, EvaaReplayStore,
                                  decode_log, find_points, run_replay, summarize)


def make_log(pool, topic, number, index, values, ticks=None):
    topics = [topic, '0x' + word(12)]
    topics += ['0x' + word(tick) for tick in ticks] if ticks else ['0x' + word(13)]
    return {'address': POOLS[pool][0], 'topics': topics, 'blockNumber': hex(number), 'logIndex': hex(index),
            'blockHash': '0x' + word(number), 'transactionHash': '0x' + word(index),
            'data': '0x' + ''.join(word(value) for value in values)}


class ChainStub:
    tip = 10_000

    def block(self, number=None):
        number = self.tip if number is None else number
        return {'number': number, 'ts': number // 2 * 1000, 'hash': '0x' + word(number)}

    def rpc(self, method):
        assert method == 'eth_chainId'
        return '0x38'

    def state(self, pool, block):
        return {'price': 2, 'tick': -100}

    def quote(self, pool, block, quantity):
        if pool == 'Uniswap v3' and block == self.tip:
            raise DataUnavailable('archived quote unavailable')
        return {'buy_usdt': 2.03 * quantity / UNIT, 'sell_usdt': 1.97 * quantity / UNIT}

    def logs(self, start, end):
        return [make_log('Uniswap v3', UNI_SWAP, start, 0, (60*UNIT, -30*UNIT, 1, 100, -96)),
                make_log('Uniswap v3', BURN, start, 1, (12, 4*UNIT, 2*UNIT), (-98, -91)),
                make_log('PancakeSwap v3', CAKE_SWAP, end, 0, (-20*UNIT, 10*UNIT, 1, 100, -101, 0, 0))]


class EvaaReplayTests(TestCase):
    def test_selects_end_of_second_and_keeps_finalized_tip(self):
        reader = ChainStub()
        points = find_points(reader, reader.block(), (4_640_000, 4_820_000, 5_000_000), lambda *args: None)
        self.assertEqual([p['number'] for p in points], [9281, 9641, 10000])

    def test_decodes_both_swap_layouts_and_nets_only_swaps(self):
        buy = decode_log(make_log('Uniswap v3', UNI_SWAP, 20, 0, (60*UNIT, -30*UNIT, 1, 100, -96)))
        remove = decode_log(make_log('Uniswap v3', BURN, 20, 1, (12, 4*UNIT, 2*UNIT), (-98, -91)))
        sell = decode_log(make_log('PancakeSwap v3', CAKE_SWAP, 21, 0, (-20*UNIT, 10*UNIT, 1, 100, -101, 0, 0)))
        before = {'pools': {p: {'tick': -100} for p in POOLS}}
        result = summarize([buy, remove, sell], before, {'number': 20})
        self.assertTrue(remove['active'])
        self.assertEqual(result['before']['Uniswap v3']['net_usdt'], 60)
        self.assertEqual(result['before']['Uniswap v3']['active_burns'], 1)
        self.assertEqual(result['after']['PancakeSwap v3']['net_usdt'], -20)

    def test_mint_layout_zero_burn_and_unknown_activity(self):
        mint = decode_log(make_log('PancakeSwap v3', MINT, 20, 0, (12, 25, UNIT, 2*UNIT), (-98, -91)))
        self.assertEqual((mint['liquidity'], mint['usdt'], mint['evaa']), ('25', 1.0, 2.0))
        empty = decode_log(make_log('PancakeSwap v3', BURN, 20, 1, (0, 0, 0), (-98, -91)))
        self.assertIsNone(empty)
        burn = decode_log(make_log('Uniswap v3', BURN, 20, 2, (1, 0, 0), (-98, -91)))
        result = summarize([burn], {'pools': {}}, {'number': 20})
        self.assertIsNone(burn['active'])
        self.assertEqual(result['before']['Uniswap v3']['unknown_burns'], 1)

    def test_fixed_size_partial_quotes_and_persistence(self):
        anchor = {'kind': 'alert', 'id': 'test-alert', 'ms': 4_820_000, 'observed_ms': 4_825_000}
        result = run_replay(anchor, 3, 1000, reader=ChainStub())
        self.assertEqual(result['quantity_evaa'], 500)
        self.assertEqual(result['quote_gaps'], 1)
        self.assertEqual(result['anchor'], anchor)
        self.assertAlmostEqual(result['points'][0]['pools']['Uniswap v3']['buy_usdt'], 1015)
        self.assertEqual(result['events'][0]['tx'], '0x' + word(0))
        self.assertEqual(result['events'][0]['topics'][0], UNI_SWAP)
        self.assertTrue(result['events'][0]['data_hex'].startswith('0x' + word(60 * UNIT)))
        with TemporaryDirectory() as folder:
            db = EvaaReplayStore(Store(folder))
            ident = db.save(result)
            db = EvaaReplayStore(Store(folder))
            self.assertEqual(db.runs(), [{'id': ident, 'anchor': anchor['ms']}])
            self.assertEqual(db.load(ident), result)

    def test_rejects_incomplete_finalized_window(self):
        anchor = {'ms': 4_900_000}
        with self.assertRaisesRegex(DataUnavailable, 'complete replay window'):
            run_replay(anchor, 3, 1000, reader=ChainStub())
