"""Historical Uniswap v3 ETH/USDC LP with a simulated OKX linear hedge."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor, log1p

from .alt_opportunity_market import (DataUnavailable, ETH_POOL, Ethereum, HOUR, MINUTE,
                                    Okx, finite, signed, unpack_words, word)

SWAP = 'ETH-USDT-SWAP'
MINT = '0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde'
BURN = '0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c'
U256 = 1 << 256
Q128 = 1 << 128
SPACING = 10
BLOCKS_PER_POINT = 300


@dataclass(frozen=True)
class ReplaySettings:
    hours: int = 12
    capital: float = 1000
    width_percent: float = 5
    hedge_percent: float = 100
    hedge_bp: float = 7
    chain_cost: float = 5

    @classmethod
    def from_values(cls, hours, capital, width, hedge, cost, chain):
        hours, capital, width, hedge, cost, chain = map(finite, (hours, capital, width, hedge, cost, chain))
        if hours not in (6, 12, 24) or not 100 <= capital <= 1_000_000 or not 1 <= width <= 30:
            raise ValueError('回放可选约 6/12/24 小时，LP 本金 100–1,000,000，单侧区间 1%–30%')
        if not 0 <= hedge <= 100 or not 0 <= cost <= 100 or not 0 <= chain <= 10_000:
            raise ValueError('对冲比例须为 0%–100%，对冲单边成本 0–100 bp，链上成本 0–10,000')
        return cls(int(hours), capital, width, hedge, cost, chain)


def tick_bounds(chain, block, tick, width):
    # USDC is token0, so human ETH/USDC price moves opposite to the raw pool tick.
    width /= 100
    lower = floor(tick - log1p(width) / log1p(0.0001))
    upper = ceil(tick - log1p(-width) / log1p(0.0001))
    start, end = ((lower - 450) // SPACING) >> 8, ((upper + 450) // SPACING) >> 8
    positions = range(start, end + 1)
    masks = chain.calls(ETH_POOL, ['5339c296' + word(pos) for pos in positions], block)
    initialized = [((pos << 8) + bit) * SPACING for pos, mask in zip(positions, masks)
                   for bit in range(256) if mask[0] & (1 << bit)]
    lows = [value for value in initialized if lower - 450 <= value <= lower]
    highs = [value for value in initialized if upper <= value <= upper + 450]
    if not lows or not highs:
        raise DataUnavailable('指定价格区间附近没有成对的已初始化刻度，可调整区间百分比')
    return max(lows), min(highs)


def fee_inside(global_fee, outside_low, outside_high, tick, lower, upper):
    below = outside_low if tick >= lower else global_fee - outside_low
    above = outside_high if tick < upper else global_fee - outside_high
    return (global_fee - below - above) % U256


def pool_point(chain, block, lower, upper):
    selectors = ['3850c7bd', 'f3058399', '46141319', '1a686502',
                 'f30dba93' + word(lower), 'f30dba93' + word(upper)]
    slot, fee0, fee1, liquidity, low, high = chain.calls(ETH_POOL, selectors, block['number'])
    if len(low) < 8 or len(high) < 8 or not low[7] or not high[7] or not low[0] or not high[0]:
        raise DataUnavailable('历史区间边界刻度未持续初始化，可调整区间百分比')
    sqrt, tick = slot[0] / 2**96, signed(slot[1])
    return {**block, 'sqrt': sqrt, 'tick': tick, 'liquidity': liquidity[0], 'gross': (low[0], high[0]),
            'fee0': fee_inside(fee0[0], low[2], high[2], tick, lower, upper),
            'fee1': fee_inside(fee1[0], low[3], high[3], tick, lower, upper)}


def verify_boundary_history(chain, points, lower, upper):
    """Check Mint/Burn deltas so fee-growth baselines cannot silently reset between samples."""
    gross = dict(zip((lower, upper), points[0]['gross']))
    bounds = ['0x' + word(tick) for tick in gross]
    base = {'address': ETH_POOL, 'fromBlock': hex(points[0]['number'] + 1), 'toBlock': hex(points[-1]['number'])}
    events = {}
    for prefix in ([[MINT, BURN], None], [[MINT, BURN], None, None]):
        for event in chain.rpc('eth_getLogs', {**base, 'topics': [*prefix, bounds]}):
            if event.get('removed'):
                raise DataUnavailable('历史区块已重组，请重试')
            key = int(event['blockNumber'], 16), int(event['logIndex'], 16)
            if not points[0]['number'] < key[0] <= points[-1]['number']:
                raise DataUnavailable('Ethereum 返回了请求范围外的日志')
            events[key] = event
    ordered = iter(sorted(events.items()))
    item = next(ordered, None)
    for current in points[1:]:
        while item is not None and item[0][0] <= current['number']:
            _, event = item
            topics = event['topics']
            minted = topics[0].lower() == MINT
            if not minted and topics[0].lower() != BURN:
                raise DataUnavailable('Uniswap 流动性事件类型异常')
            values = unpack_words(event['data'], 4 if minted else 3)
            delta = values[1] if minted else -values[0]
            for tick in (signed(int(topics[2], 16)), signed(int(topics[3], 16))):
                if tick in gross:
                    gross[tick] += delta
                    if gross[tick] <= 0:
                        raise DataUnavailable('回放期间边界刻度曾被清除，费增长基准重置；请调整区间')
            item = next(ordered, None)
        if tuple(gross.values()) != tuple(current['gross']):
            raise DataUnavailable('Ethereum 日志与历史刻度状态不一致，无法完成回放')


def position(sqrt, lower_sqrt, upper_sqrt, liquidity):
    clamped = max(lower_sqrt, min(sqrt, upper_sqrt))
    usdc = liquidity * (upper_sqrt - clamped) / (clamped * upper_sqrt) / 10**6
    eth = liquidity * (clamped - lower_sqrt) / 10**18
    return usdc, eth


def simulate(points, funding, lower, upper, settings, hedge_lot, hedge_min):
    if len(points) < 2 or not lower < upper:
        raise DataUnavailable('回放数据点或价格区间无效')
    if not (hedge_lot.is_finite() and hedge_min.is_finite() and hedge_min >= hedge_lot > 0):
        raise DataUnavailable('永续合约数量精度无效')
    lo, hi = 1.0001**(lower / 2), 1.0001**(upper / 2)
    initial = points[0]
    unit_usdc, unit_eth = position(initial['sqrt'], lo, hi, 1)
    start_price = 10**12 / initial['sqrt']**2
    liquidity = settings.capital / (unit_usdc + unit_eth * start_price)
    start_usdc, start_eth = position(initial['sqrt'], lo, hi, liquidity)
    hedge_pnl = hedge_cost = funding_pnl = 0.0
    held = Decimal(0)
    fees_usdc = fees_eth = 0.0
    rate_events = iter(sorted(funding))
    rate = next(rate_events, None)
    result = []
    previous = initial
    for index, point in enumerate(points):
        if index:
            if point['ts'] <= previous['ts']:
                raise DataUnavailable('回放时间序列不连续')
            usdc_delta = (point['fee0'] - previous['fee0']) % U256 * liquidity / Q128 / 10**6
            eth_delta = (point['fee1'] - previous['fee1']) % U256 * liquidity / Q128 / 10**18
            if usdc_delta + eth_delta * 10**12 / point['sqrt']**2 >= settings.capital:
                raise DataUnavailable('链上手续费累计异常')
            fees_usdc += usdc_delta
            fees_eth += eth_delta
            hedge_pnl += float(held) * (previous['mark'] - point['mark'])
            while rate and rate[0] <= point['ts']:
                if rate[0] > previous['ts']:
                    funding_pnl += float(held) * rate[1] * rate[2]
                rate = next(rate_events, None)
        in_range = lower <= point['tick'] < upper
        if in_range and point['liquidity'] < liquidity * 100:
            raise DataUnavailable('模拟仓位超过采样时活跃流动性的 1%，小仓位近似失效')
        usdc, eth = position(point['sqrt'], lo, hi, liquidity)
        price = 10**12 / point['sqrt']**2
        lp = usdc + eth * price
        fees = fees_usdc + fees_eth * price
        requested = Decimal(str(eth * settings.hedge_percent / 100))
        target = requested // hedge_lot * hedge_lot
        if index == len(points) - 1 or target < hedge_min:
            target = Decimal(0)
        if abs(target - held) >= hedge_min:
            hedge_cost += float(abs(target - held)) * point['mark'] * settings.hedge_bp / 10_000
            held = target
        hold = start_usdc + start_eth * price
        lp_net = lp + fees - settings.capital - settings.chain_cost
        result.append({'ts': point['ts'], 'number': point['number'], 'hash': point['hash'], 'tick': point['tick'],
                       'in_range': in_range, 'price': price, 'mark': point['mark'], 'eth': eth, 'usdc': usdc,
                       'fees_eth': fees_eth, 'fees_usdc': fees_usdc, 'fees': fees, 'lp': lp, 'hold': hold,
                       'lp_net': lp_net, 'hedge': float(held), 'hedge_pnl': hedge_pnl, 'hedge_cost': hedge_cost,
                       'funding': funding_pnl, 'net': lp_net + hedge_pnl + funding_pnl - hedge_cost})
        previous = point
    return {'pool': ETH_POOL, 'source': 'Ethereum dRPC / PublicNode + OKX 历史标记价与已结算费率',
            'lower': lower, 'upper': upper, 'price_low': 10**12 / hi**2, 'price_high': 10**12 / lo**2,
            'funding_events': sorted(funding), 'points': result, 'settings': vars(settings),
            'hedge_lot_eth': str(hedge_lot), 'hedge_min_eth': str(hedge_min)}


def replay(settings, stopped=lambda: False, progress=lambda *args: None, chain=None, okx=None):
    chain, okx = chain or Ethereum(), okx or Okx()
    spec, multiplier = okx.linear_swap('ETH')

    def check_stop():
        if stopped():
            raise DataUnavailable('回放已取消')

    end = chain.block('finalized')
    start = chain.block(end['number'] - settings.hours * BLOCKS_PER_POINT)
    tick = signed(unpack_words(chain.call(ETH_POOL, '3850c7bd', start['number']), 2)[1])
    lower, upper = tick_bounds(chain, start['number'], tick, settings.width_percent)
    block_numbers = list(range(start['number'], end['number'] + 1, BLOCKS_PER_POINT))

    def load(number):
        check_stop()
        block = {start['number']: start, end['number']: end}.get(number) or chain.block(number)
        row = pool_point(chain, block, lower, upper)
        row['mark'] = okx.mark_before(SWAP, row['ts'])
        return row

    points = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for point in pool.map(load, block_numbers):
            points.append(point)
            progress('读取链上状态与永续标记价 {0}/{1}', len(points), len(block_numbers))
    progress('核对区间边界刻度的历史流动性事件')
    for index in range(0, len(points) - 1, 3):
        check_stop()
        verify_boundary_history(chain, points[index:index + 4], lower, upper)
    rates = okx.settled(SWAP)
    if not rates or min(rates) > start['ts'] or end['ts'] - max(rates) > 24 * HOUR:
        raise DataUnavailable('已结算费率历史无法覆盖回放窗口')
    funding = []
    for at, rate in sorted(rates.items()):
        if start['ts'] < at <= end['ts']:
            check_stop()
            funding.append((at, okx.mark_before(SWAP, at), rate))
    check_stop()
    return simulate(points, funding, lower, upper, settings,
                    Decimal(spec['lotSz']) * multiplier, Decimal(spec['minSz']) * multiplier)
