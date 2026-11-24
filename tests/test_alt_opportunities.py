from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from klinebot.alt_monitor_core import BAR_MS, Store
from klinebot.alt_opportunity_lp import (BURN, MINT, Q128, ReplaySettings, fee_inside, simulate,
                                        verify_boundary_history)
from klinebot.alt_opportunity_market import Book, DataUnavailable, MINUTE, shared_quantity, word
from klinebot.alt_opportunity_scan import (EXIT_WINDOW, OpportunityStore, Scanner, ScanSettings,
                                          funding_candidate)


class FundingTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = OpportunityStore(Store(self.folder.name))
        self.start = 10 * BAR_MS
        self.settlement = self.start + 2 * BAR_MS

    def candidate(self, rate=0.02):
        spot = Book(self.start, ((99, 5), (98, 10)), ((100, 5), (101, 10)))
        swap = Book(self.start, ((103, 15),), ((104, 15),))
        market = ({'lotSz': '0.003', 'minSz': '0.003'}, {'lotSz': '1', 'minSz': '1'},
                  Decimal('0.002'), spot, swap)
        settings = ScanSettings(1000, 0, 0)
        with patch('klinebot.alt_opportunity_scan.now_ms', return_value=self.start):
            row = funding_candidate('ETH', market, {'settlement': self.settlement, 'rate': rate}, settings)
        return row

    def test_matched_lots_and_depth(self):
        self.assertEqual(shared_quantity(10, 3, '0.3', '0.2'), Decimal('3.0'))
        row = self.candidate()
        self.assertEqual(str(row['quantity']), '10.05')
        self.assertAlmostEqual(row['spot_buy'], 5 * 100 + 5.05 * 101)
        with self.assertRaises(DataUnavailable):
            Book(self.start, ((9, 1),), ((10, 1),)).value(2, True)

    def test_first_positive_signal_frozen_and_actual_settlement_used(self):
        row = self.candidate(0.1)
        self.assertGreater(row['net'], 0)
        self.store.open_case(row)
        self.store.open_case(dict(row, observed=row['observed'] + 1, spot_buy=5))
        cases = self.store.cases()
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['entry']['spot_buy'], row['spot_buy'])
        now = self.settlement + 2 * MINUTE
        spot = Book(now, ((110, 20),), ((111, 20),))
        swap = Book(now, ((111, 20),), ((112, 20),))

        class OkxStub:
            def settled(_, ident):
                return {self.settlement: -0.01}

            def mark_before(_, ident, timestamp):
                self.assertEqual(timestamp, self.settlement)
                return 111

        with patch('klinebot.alt_opportunity_scan.now_ms', return_value=now):
            Scanner(self.store, OkxStub()).pending('ETH', (None, None, None, spot, swap))
        result = self.store.cases()[0]['result']
        self.assertEqual(result['status'], 'settled')
        self.assertAlmostEqual(result['funding_pnl'], -0.01 * 111 * row['quantity'])
        self.assertNotEqual(result['net'], row['net'])

    def test_missed_exit_window_is_a_gap(self):
        self.store.open_case(self.candidate(0.1))
        with patch('klinebot.alt_opportunity_scan.now_ms', return_value=self.settlement + EXIT_WINDOW + 1):
            Scanner(self.store).pending('ETH', None)
        self.assertEqual(self.store.cases()[0]['result']['status'], 'gap')


class DexTests(TestCase):
    def test_positive_sequence_survives_restart_but_not_missing_data(self):
        with TemporaryDirectory() as folder:
            db = Store(folder)
            store = OpportunityStore(db)

            def take(at, block, net=1, setup=1):
                return store.sample('dex', 'LINK', {'symbol': 'LINK', 'observed': at, 'block': {'number': block},
                                    'setup': setup, 'opportunities': {'dex_buy': {'net': net}}})['opportunities']['dex_buy']

            start = BAR_MS
            self.assertEqual(take(start, 10)['samples'], 1)
            store = OpportunityStore(Store(folder))
            self.assertEqual(take(start + 1000, 10)['samples'], 1)
            self.assertEqual(take(start + BAR_MS, 30)['samples'], 2)
            self.assertEqual(take(start + 2 * BAR_MS, 60, setup=2)['samples'], 1)
            store.sample('dex', 'LINK', {'symbol': 'LINK', 'observed': start + 3 * BAR_MS, 'error': 'gap'})
            self.assertEqual(take(start + 4 * BAR_MS, 115, setup=2)['since'], start + 4 * BAR_MS)
            self.assertEqual(take(start + 6 * BAR_MS, 170, setup=2)['samples'], 1)


class ReplayTests(TestCase):
    def test_fee_growth_tracks_lower_upper_and_out_of_range(self):
        self.assertEqual(fee_inside(100, 20, 30, 0, -10, 10), 50)
        self.assertEqual(fee_inside(100, 70, 20, -20, -10, 10), 50)
        self.assertEqual(fee_inside(100, 10, 60, 20, -10, 10), 50)

    def test_hedge_rebalances_and_books_funding_on_preexisting_position(self):
        lo, hi = 200_000, 202_000

        def point(index, tick, mark, fee):
            return {'ts': 400_000 + index * MINUTE, 'number': index + 500, 'hash': str(index), 'tick': tick,
                    'sqrt': 1.0001**(tick / 2), 'liquidity': 10**28, 'fee0': fee, 'fee1': 0, 'mark': mark}

        # The last move crosses the range and turns the LP entirely into ETH.
        points = [point(0, 201_000, 1830, 0), point(1, 201_100, 1810, Q128 // 10**10),
                  point(2, 202_001, 1720, Q128 // 10**10)]
        settings = ReplaySettings(hedge_bp=10, chain_cost=8)
        settlement = points[1]['ts']
        run = simulate(points, [(settlement, 1810, -0.01)], lo, hi, settings,
                       Decimal('0.001'), Decimal('0.001'))
        first, mid, last = run['points']
        self.assertGreater(first['hedge'], 0)
        self.assertGreater(mid['hedge'], first['hedge'])
        self.assertFalse(last['in_range'])
        self.assertEqual(last['hedge'], 0)
        self.assertGreater(last['fees_usdc'], 0)
        self.assertEqual(last['fees_usdc'], mid['fees_usdc'])
        self.assertAlmostEqual(last['funding'], first['hedge'] * 1810 * -0.01)
        self.assertAlmostEqual(last['hedge_pnl'], first['hedge'] * 20 + mid['hedge'] * 90)
        self.assertAlmostEqual(last['net'], last['lp_net'] + last['hedge_pnl'] + last['funding'] - last['hedge_cost'])

    def test_mint_burn_history_rejects_reset_even_if_endpoint_gross_matches(self):
        lo, hi = 30, 60

        class ChainStub:
            def rpc(self, method, filters):
                topics = ['0x0', '0x' + word(lo), '0x' + word(hi)]
                return [{'topics': [BURN] + topics, 'blockNumber': '0x10', 'logIndex': '0x0',
                         'data': '0x' + word(3) + word(0) * 2},
                        {'topics': [MINT] + topics, 'blockNumber': '0x10', 'logIndex': '0x1',
                         'data': '0x' + word(0) + word(3) + word(0) * 2}]

        points = ({'number': 15, 'gross': (3, 3)}, {'number': 16, 'gross': (3, 3)})
        with self.assertRaisesRegex(DataUnavailable, '基准重置'):
            verify_boundary_history(ChainStub(), points, lo, hi)
