"""Public OKX and Ethereum inputs used by the opportunity research pages."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import math
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .alt_monitor_core import now_ms

MINUTE = 60_000
HOUR = 60 * MINUTE
OKX = 'https://www.okx.com/api/v5'
ETH_RPC = 'https://eth.drpc.org'
ETH_LOG_RPC = 'https://ethereum-rpc.publicnode.com'
USDC = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'
WETH = '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2'
LINK = '0x514910771AF9Ca656af840dff83E8264EcF986CA'
ETH_POOL = '0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640'
FACTORY = '0x1F98431c8aD98523631AE4a59f267346ea31F984'
QUOTER = '0x61fFE014bA17989E743c5F6cB21bF9697530B21e'
MULTICALL = '0x5BA1e12693Dc8F9c48aAD8770482f4739bEeD696'


class DataUnavailable(RuntimeError):
    pass


def request_json(url, source, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Accept': 'application/json', 'User-Agent': 'AltOpportunityResearch/1.0'}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=data, headers=headers)
    for attempt in range(2):
        try:
            with urlopen(request, timeout=8) as response:
                return json.load(response)
        except HTTPError as exc:
            retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
            wait = float(retry_after) if retry_after.isdecimal() else 0.5
            if attempt or exc.code not in (408, 429, 500, 502, 503, 504) or wait > 3:
                raise DataUnavailable(f'{source} HTTP {exc.code}') from exc
            sleep(wait)
        except (URLError, OSError, ValueError) as exc:
            if attempt or isinstance(exc, ValueError):
                reason = getattr(exc, 'reason', exc)
                raise DataUnavailable(f'{source} 请求失败（{str(reason)[:150]}）') from exc
            sleep(0.5)


def finite(value, *, positive=False):
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise DataUnavailable('行情数值无效')
    return result


def check_age(timestamp, observed, max_ms, source):
    age = observed - timestamp
    if not -5_000 <= age <= max_ms:
        raise DataUnavailable(f'{source} 时间过期或本机时钟偏差（{age / 1000:+.1f} 秒）')


@dataclass(frozen=True)
class Book:
    timestamp: int
    bids: tuple
    asks: tuple

    @property
    def mid(self):
        return (self.bids[0][0] + self.asks[0][0]) / 2

    def value(self, qty, buy):
        """Sweep a fixed base quantity and return total quote currency."""
        if qty <= 0 or not math.isfinite(qty):
            raise DataUnavailable('标的数量无效')
        remaining, value = qty, 0.0
        for price, available in self.asks if buy else self.bids:
            take = min(remaining, available)
            value += take * price
            remaining -= take
            if remaining <= qty * 1e-12:
                return value
        raise DataUnavailable('盘口深度不足，无法覆盖双腿指定数量')


class Okx:
    name = 'OKX'

    def __init__(self, fetch=request_json):
        self.fetch = fetch

    def data(self, route, **params):
        data = self.fetch(f'{OKX}/{route}?{urlencode(params)}', self.name)
        if not isinstance(data, dict):
            raise DataUnavailable(f'OKX {route} 响应格式异常')
        if data.get('code') != '0' or not isinstance(data.get('data'), list) or not data['data']:
            raise DataUnavailable(f'OKX {route} 无可用数据（{data.get("code", "格式异常")}）')
        return data['data']

    def instrument(self, ident, kind):
        item = self.data('public/instruments', instType=kind, instId=ident)[0]
        if item['instId'] != ident or item['state'] != 'live':
            raise DataUnavailable(f'{ident} 未处于正常交易状态')
        return item

    def linear_swap(self, symbol):
        spec = self.instrument(f'{symbol}-USDT-SWAP', 'SWAP')
        if (spec.get('ctType') != 'linear' or spec.get('ctValCcy') != symbol
                or spec.get('settleCcy') != 'USDT'):
            raise DataUnavailable(f'{symbol} USDT 线性合约面值不匹配')
        multiplier = Decimal(spec['ctVal'])
        if not multiplier.is_finite() or multiplier <= 0:
            raise DataUnavailable('合约面值无效')
        return spec, multiplier

    def book(self, ident, multiplier=1):
        item = self.data('market/books', instId=ident, sz=50)[0]
        ts = int(item['ts'])
        check_age(ts, now_ms(), 25_000, ident)
        bids, asks = (tuple((finite(level[0], positive=True), finite(level[1], positive=True) * multiplier)
                            for level in item[side]) for side in ('bids', 'asks'))
        if (not bids or not asks or bids[0][0] >= asks[0][0]
                or any(a[0] < b[0] for a, b in zip(bids, bids[1:]))
                or any(a[0] > b[0] for a, b in zip(asks, asks[1:]))):
            raise DataUnavailable(f'{ident} 盘口不完整或无效')
        return Book(ts, bids, asks)

    def funding(self, ident):
        row = self.data('public/funding-rate', instId=ident)[0]
        if row.get('instId') != ident or row.get('method') != 'current_period':
            raise DataUnavailable(f'{ident} 费率结算机制待核实')
        ts, settlement = int(row['ts']), int(row['fundingTime'])
        observed = now_ms()
        check_age(ts, observed, 90_000, '预估资金费率')
        hours = (settlement - int(row['prevFundingTime'])) / HOUR
        if not 0 < settlement - observed <= 24 * HOUR or not 0 < hours <= 24:
            raise DataUnavailable('未取得有效的下一结算时刻及本期长度')
        rate = finite(row['fundingRate'])
        return {'ts': ts, 'settlement': settlement, 'hours': hours, 'rate': rate,
                'rate_8h': rate * 8 / hours}

    def settled(self, ident):
        rows = self.data('public/funding-rate-history', instId=ident, limit=100)
        return {int(row['fundingTime']): finite(row['realizedRate']) for row in rows
                if row.get('instId') == ident and row.get('realizedRate') not in (None, '')}

    def mark_before(self, ident, timestamp):
        rows = self.data('market/history-mark-price-candles', instId=ident, bar='1m',
                         after=timestamp, limit=5)
        points = {int(row[0]) + MINUTE: finite(row[4], positive=True) for row in rows
                  if row[-1] == '1' and int(row[0]) + MINUTE <= timestamp}
        if not points or timestamp - max(points) >= MINUTE:
            raise DataUnavailable(f'{ident} 缺少对应的已收盘 1 分钟标记价')
        return points[max(points)]


def word(number):
    return f'{number % (1 << 256):064x}'


def address_word(address):
    return word(int(address, 16))


def unpack_words(raw, minimum=1):
    if not isinstance(raw, str) or not raw.startswith('0x') or (len(raw) - 2) % 64:
        raise DataUnavailable('Ethereum 合约响应格式异常')
    values = [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]
    if len(values) < minimum:
        raise DataUnavailable('Ethereum 合约响应字段不足')
    return values


def signed(number):
    return number if number < (1 << 255) else number - (1 << 256)


class Ethereum:
    def __init__(self, fetch=request_json):
        self.fetch = fetch

    def rpc(self, method, *params):
        host, source = (ETH_LOG_RPC, 'Ethereum PublicNode') if method == 'eth_getLogs' else (ETH_RPC, 'Ethereum dRPC')
        data = self.fetch(host, source,
                          {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        if not isinstance(data, dict):
            raise DataUnavailable(f'Ethereum {method} 响应格式异常')
        if 'error' in data or data.get('result') is None:
            error = data.get('error') or {}
            raise DataUnavailable(f'Ethereum {method}：{str(error.get("message", "响应缺失"))[:150]}')
        return data['result']

    def block(self, tag='latest'):
        block = self.rpc('eth_getBlockByNumber', hex(tag) if isinstance(tag, int) else tag, False)
        return {'number': int(block['number'], 16), 'ts': int(block['timestamp'], 16) * 1000,
                'hash': block['hash']}

    def call(self, address, data, block):
        return self.rpc('eth_call', {'to': address, 'data': '0x' + data}, hex(block))

    def calls(self, address, selectors, block):
        """Multicall2 aggregate. All reads come from the same pinned historical block."""
        head, tail = [], []
        offset = 32 * len(selectors)
        for selector in selectors:
            call = address_word(address) + word(64) + word(len(selector) // 2)
            call += selector.ljust((len(selector) + 63) // 64 * 64, '0')
            head.append(word(offset))
            tail.append(call)
            offset += len(call) // 2
        raw = bytes.fromhex(self.call(MULTICALL, '252dba42' + word(32) + word(len(selectors))
                                      + ''.join(head + tail), block)[2:])

        def at(pos):
            if pos < 0 or pos + 32 > len(raw):
                raise DataUnavailable('Ethereum Multicall 响应截断')
            return int.from_bytes(raw[pos:pos + 32], 'big')

        array = at(32)
        if at(0) != block or at(array) != len(selectors):
            raise DataUnavailable('Ethereum Multicall 区块或返回数量不符')
        result = []
        array += 32
        for index in range(len(selectors)):
            entry = array + at(array + index * 32)
            end = entry + 32 + at(entry)
            if end > len(raw):
                raise DataUnavailable('Ethereum Multicall 返回长度不足')
            result.append(unpack_words('0x' + raw[entry + 32:end].hex()))
        return result

    def pool(self, token, tier, block):
        raw = self.call(FACTORY, '1698ee82' + address_word(token) + address_word(USDC) + word(tier), block)
        address = unpack_words(raw)[0]
        if not 0 < address < 1 << 160:
            raise DataUnavailable('Uniswap v3 指定费率池不存在')
        return f'0x{address:040x}'

    def quote(self, token, tier, quantity, buy, block):
        token_in, token_out = (USDC, token) if buy else (token, USDC)
        pair = address_word(token_in) + address_word(token_out)

        def quoter(exact_output, size):
            selector = 'bd21704a' if exact_output else 'c6a5026a'
            data = selector + pair + word(size) + word(tier) + word(0)
            return unpack_words(self.call(QUOTER, data, block), 4)

        amount, _, _, gas = quoter(buy, quantity)[:4]
        if not amount or not gas:
            raise DataUnavailable('Uniswap 报价未覆盖指定数量')
        if not buy:
            used, *_ = quoter(True, amount)
            if abs(used - quantity) * 100_000 > quantity:
                raise DataUnavailable('Uniswap 卖出报价未完整覆盖指定数量')
        return amount / 10**6, gas

    def eth_usdc(self, block):
        sqrt = unpack_words(self.call(ETH_POOL, '3850c7bd', block))[0]
        return 10**12 / (sqrt / 2**96)**2


def shared_quantity(notional, price, *steps):
    increments = [Decimal(step) for step in steps]
    if any(not step.is_finite() or step <= 0 for step in increments):
        raise DataUnavailable('交易所数量步长无效')
    scale = 10**max(0, *(-step.as_tuple().exponent for step in increments))
    common = Decimal(math.lcm(*(int(step * scale) for step in increments))) / scale
    qty = (Decimal(str(notional)) / Decimal(str(price)) // common) * common
    if qty <= 0:
        raise DataUnavailable('金额低于双腿共同最小数量步长')
    return qty
