"""Historical Uniswap v3 ETH/USDC LP with a simulated OKX linear hedge."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor, log1p

from .alt_opportunity_market import (BURN, MINT, DataUnavailable, ETH_POOL, Ethereum, HOUR, MINUTE, WETH,
                                    Okx, finite, signed, unpack_words, word)
from .chain_replay import ChainReader, find_points

U256 = 1 << 256
Q128 = 1 << 128
SPACING = 10
BLOCKS_PER_POINT = 300


@dataclass(frozen=True)
class LpMarket:
    pool: str = ETH_POOL
    symbol: str = 'ETH'
    quote: str = 'USDC'
    base_unit: int = 10**18
    quote_unit: int = 10**6
    base_first: bool = False
    spacing: int = SPACING
    chain: int = 1

    def price(self, sqrt):
        return (sqrt**2 if self.base_first else 1 / sqrt**2) * self.base_unit / self.quote_unit

    def amounts(self, token0, token1):
        base, quote = (token0, token1) if self.base_first else (token1, token0)
        return quote / self.quote_unit, base / self.base_unit


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


def tick_bounds(chain, block, tick, width, market=LpMarket()):
    # USDC is token0, so human ETH/USDC price moves opposite to the raw pool tick.
    width /= 100
    low_gap, high_gap = (log1p(-width), log1p(width)) if market.base_first else (-log1p(width), -log1p(-width))
    lower = floor(tick + low_gap / log1p(0.0001))
    upper = ceil(tick + high_gap / log1p(0.0001))
    spacing = market.spacing
    start, end = ((lower - 450) // spacing) >> 8, ((upper + 450) // spacing) >> 8
    positions = range(start, end + 1)
    masks = chain.calls(market.pool, ['5339c296' + word(pos) for pos in positions], block)
    initialized = [((pos << 8) + bit) * spacing for pos, mask in zip(positions, masks)
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


def pool_point(chain, block, lower, upper, market=LpMarket()):
    selectors = ['3850c7bd', 'f3058399', '46141319', '1a686502',
                 'f30dba93' + word(lower), 'f30dba93' + word(upper)]
    slot, fee0, fee1, liquidity, low, high = chain.calls(market.pool, selectors, block['number'])
    if len(low) < 8 or len(high) < 8 or not low[7] or not high[7] or not low[0] or not high[0]:
        raise DataUnavailable('历史区间边界刻度未持续初始化，可调整区间百分比')
    sqrt, tick = slot[0] / 2**96, signed(slot[1])
    return {**block, 'sqrt': sqrt, 'tick': tick, 'liquidity': liquidity[0], 'gross': (low[0], high[0]),
            'fee0': fee_inside(fee0[0], low[2], high[2], tick, lower, upper),
            'fee1': fee_inside(fee1[0], low[3], high[3], tick, lower, upper)}


def verify_boundary_history(chain, points, lower, upper, market=LpMarket(), check=lambda: None):
    """Check Mint/Burn deltas so fee-growth baselines cannot silently reset between samples."""
    gross = dict(zip((lower, upper), points[0]['gross']))
    bounds = ['0x' + word(tick) for tick in gross]
    events = {}
    for left in range(points[0]['number'] + 1, points[-1]['number'] + 1, 2400):
        check()
        right = min(left + 2399, points[-1]['number'])
        base = {'address': market.pool, 'fromBlock': hex(left), 'toBlock': hex(right)}
        for prefix in ([[MINT, BURN], None], [[MINT, BURN], None, None]):
            for event in chain.rpc('eth_getLogs', {**base, 'topics': [*prefix, bounds]}):
                if event.get('removed'):
                    raise DataUnavailable('历史区块已重组，请重试')
                key = int(event['blockNumber'], 16), int(event['logIndex'], 16)
                if not left <= key[0] <= right:
                    raise DataUnavailable('RPC 返回了请求范围外的日志')
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


def position(sqrt, lower_sqrt, upper_sqrt, liquidity, market=LpMarket()):
    clamped = max(lower_sqrt, min(sqrt, upper_sqrt))
    token0 = liquidity * (upper_sqrt - clamped) / (clamped * upper_sqrt)
    token1 = liquidity * (clamped - lower_sqrt)
    return market.amounts(token0, token1)


def simulate(points, funding, lower, upper, settings, hedge_lot, hedge_min, market=LpMarket()):
    if len(points) < 2 or not lower < upper:
        raise DataUnavailable('回放数据点或价格区间无效')
    if not (hedge_lot.is_finite() and hedge_min.is_finite() and hedge_min >= hedge_lot > 0):
        raise DataUnavailable('永续合约数量精度无效')
    lo, hi = 1.0001**(lower / 2), 1.0001**(upper / 2)
    initial = points[0]
    unit_quote, unit_base = position(initial['sqrt'], lo, hi, 1, market)
    start_price = market.price(initial['sqrt'])
    liquidity = settings.capital / (unit_quote + unit_base * start_price)
    start_quote, start_base = position(initial['sqrt'], lo, hi, liquidity, market)
    hedge_pnl = hedge_cost = funding_pnl = 0.0
    held = Decimal(0)
    fees_quote = fees_base = 0.0
    rate_events = iter(sorted(funding))
    rate = next(rate_events, None)
    result = []
    previous = initial
    for index, point in enumerate(points):
        if index:
            if point['ts'] <= previous['ts']:
                raise DataUnavailable('回放时间序列不连续')
            deltas = [(point[key] - previous[key]) % U256 * liquidity / Q128 for key in ('fee0', 'fee1')]
            quote_delta, base_delta = market.amounts(*deltas)
            if quote_delta + base_delta * market.price(point['sqrt']) >= settings.capital:
                raise DataUnavailable('链上手续费累计异常')
            fees_quote += quote_delta
            fees_base += base_delta
            hedge_pnl += float(held) * (previous['mark'] - point['mark'])
            while rate and rate[0] <= point['ts']:
                if rate[0] > previous['ts']:
                    funding_pnl += float(held) * rate[1] * rate[2]
                rate = next(rate_events, None)
        in_range = lower <= point['tick'] < upper
        if in_range and point['liquidity'] < liquidity * 100:
            raise DataUnavailable('模拟仓位超过采样时活跃流动性的 1%，小仓位近似失效')
        quote, base = position(point['sqrt'], lo, hi, liquidity, market)
        price = market.price(point['sqrt'])
        lp = quote + base * price
        fees = fees_quote + fees_base * price
        requested = Decimal(str(base * settings.hedge_percent / 100))
        target = requested // hedge_lot * hedge_lot
        if index == len(points) - 1 or target < hedge_min:
            target = Decimal(0)
        if abs(target - held) >= hedge_min:
            hedge_cost += float(abs(target - held)) * point['mark'] * settings.hedge_bp / 10_000
            held = target
        hold = start_quote + start_base * price
        lp_net = lp + fees - settings.capital - settings.chain_cost
        result.append({'ts': point['ts'], 'number': point['number'], 'hash': point['hash'], 'tick': point['tick'],
                       'in_range': in_range, 'price': price, 'mark': point['mark'], 'base': base, 'quote': quote,
                       'fees_base': fees_base, 'fees_quote': fees_quote, 'fees': fees, 'lp': lp, 'hold': hold,
                       'lp_net': lp_net, 'hedge': float(held), 'hedge_pnl': hedge_pnl, 'hedge_cost': hedge_cost,
                       'funding': funding_pnl, 'net': lp_net + hedge_pnl + funding_pnl - hedge_cost})
        previous = point
    bounds = sorted((market.price(lo), market.price(hi)))
    return {'pool': market.pool, 'market': vars(market), 'source': 'Public RPC + OKX if hedged',
            'lower': lower, 'upper': upper, 'price_low': bounds[0], 'price_high': bounds[1],
            'funding_events': sorted(funding), 'points': result, 'settings': vars(settings),
            'hedge_lot': str(hedge_lot), 'hedge_min': str(hedge_min)}


def replay(settings, stopped=lambda: False, progress=lambda *args: None, chain=None, okx=None, *, selected=None, cex_symbol=''):
    okx = okx or Okx()
    market = LpMarket()

    def check_stop():
        if stopped():
            raise DataUnavailable('回放已取消')

    if selected is None:
        chain = chain or Ethereum()
        end = chain.block('finalized')
        start = chain.block(end['number'] - settings.hours * BLOCKS_PER_POINT)
        blocks = [start, *range(start['number'] + BLOCKS_PER_POINT, end['number'], BLOCKS_PER_POINT), end]
        tick = signed(unpack_words(chain.call(market.pool, '3850c7bd', start['number']), 2)[1])
        symbol = 'ETH'
    else:
        chain = chain or ChainReader(selected.chain)
        if int(chain.rpc('eth_chainId'), 16) != selected.chain:
            raise DataUnavailable('Unexpected chain ID')
        end = chain.block()
        targets = [end['ts'] - n * HOUR for n in range(settings.hours, -1, -1)]
        blocks = find_points(chain, end, targets, progress, block_ms=250 if selected.chain == 56 else 8000)
        start = blocks[0]
        pools = chain.discover(selected, start['number'], check_stop)
        if not pools:
            raise DataUnavailable('No supported direct V3 pool in the requested window')
        address = pools[0].address
        asset_symbol = chain.symbol(selected.token, end['number']) or selected.symbol
        symbol = cex_symbol.strip().upper() or asset_symbol.upper()
        if not cex_symbol and selected.token == WETH.lower() and selected.chain == 1:
            symbol = 'ETH'
        if settings.hedge_percent and asset_symbol.upper() not in (symbol, 'W' + symbol):
            raise DataUnavailable(f'Contract symbol {asset_symbol} differs from OKX {symbol}')
        tick = signed(chain.call(address, '3850c7bd', start['number'])[1])
        spacing = signed(chain.call(address, 'd0c93a7c', start['number'])[0])
        if spacing <= 0:
            raise DataUnavailable('Invalid pool tick spacing')
        market = LpMarket(address, asset_symbol, selected.quote, 10**chain.decimals(selected.token, end['number']),
                          10**chain.decimals(selected.quote_address, end['number']),
                          int(selected.token, 16) < int(selected.quote_address, 16), spacing, selected.chain)
    lower, upper = tick_bounds(chain, start['number'], tick, settings.width_percent, market)
    swap_id = f'{symbol}-USDT-SWAP'
    if settings.hedge_percent:
        spec, multiplier = okx.linear_swap(symbol)
        hedge_lot, hedge_min = Decimal(spec['lotSz']) * multiplier, Decimal(spec['minSz']) * multiplier
    else:
        hedge_lot = hedge_min = Decimal(1)

    def load(entry):
        check_stop()
        block = chain.block(entry) if isinstance(entry, int) else entry
        row = pool_point(chain, block, lower, upper, market)
        row['mark'] = okx.mark_before(swap_id, row['ts']) if settings.hedge_percent else market.price(row['sqrt'])
        return row

    points = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for point in pool.map(load, blocks):
            points.append(point)
            progress('读取链上状态与参考价 {0}/{1}', len(points), len(blocks))
    progress('核对区间边界刻度的历史流动性事件')
    for index in range(0, len(points) - 1, 3):
        check_stop()
        verify_boundary_history(chain, points[index:index + 4], lower, upper, market, check_stop)
    rates = okx.settled(swap_id) if settings.hedge_percent else {}
    if settings.hedge_percent and (not rates or min(rates) > start['ts'] or end['ts'] - max(rates) > 24 * HOUR):
        raise DataUnavailable('已结算费率历史无法覆盖回放窗口')
    funding = []
    for at, rate in sorted(rates.items()):
        if start['ts'] < at <= end['ts']:
            check_stop()
            funding.append((at, okx.mark_before(swap_id, at), rate))
    check_stop()
    return simulate(points, funding, lower, upper, settings, hedge_lot, hedge_min, market)
