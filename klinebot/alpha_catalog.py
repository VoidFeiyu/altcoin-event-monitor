"""Public market directories for the desktop radar."""
import re


QUOTES = ('USDT', 'USDC', 'U')


def alpha_catalog(tokens, pairs):
    metadata = {row['alphaId']: row for row in tokens if isinstance(row, dict) and row.get('alphaId')}
    best = {}
    for row in pairs:
        base, quote, pair = row.get('baseAsset', ''), row.get('quoteAsset'), row.get('symbol')
        if (row.get('status') != 'TRADING' or quote not in QUOTES or not re.fullmatch(r'ALPHA_\d+', base)
                or pair != base + quote or base not in metadata):
            continue
        token = metadata[base]
        if token.get('offline') or token.get('fullyDelisted'):
            continue
        if base in best and QUOTES.index(best[base]['quote']) <= QUOTES.index(quote):
            continue
        try:
            listed = max(0, int(token.get('listingTime') or 0))
        except (TypeError, ValueError):
            listed = 0
        best[base] = {'pair': pair, 'source': 'alpha', 'alpha': base, 'quote': quote, 'listed_ms': listed,
                      'symbol': str(token.get('symbol') or base), 'name': str(token.get('name') or ''),
                      'contract': str(token.get('contractAddress') or ''), 'chain_id': str(token.get('chainId') or ''),
                      'chain': str(token.get('chainName') or token.get('chainId') or '')}
    return dict(sorted(((row['pair'], row) for row in best.values()), key=lambda item: (item[1]['symbol'].upper(), item[0])))


def spot_catalog(pairs):
    """Pick one active pair per spot base, preferring stablecoin quotes."""
    preference = {quote: rank for rank, quote in enumerate((*QUOTES, 'FDUSD', 'USD1', 'BTC', 'ETH', 'BNB'))}
    best = {}
    for row in pairs:
        base, quote, pair = row.get('baseAsset'), row.get('quoteAsset'), row.get('symbol')
        if (row.get('status') != 'TRADING' or not row.get('isSpotTradingAllowed') or not base or not quote
                or pair != base + quote or not re.fullmatch(r'[^\s:]{1,48}', pair)):
            continue
        order = preference.get(quote, len(preference)), quote
        if base not in best or order < best[base][0]:
            best[base] = order, {'pair': pair, 'source': 'spot', 'symbol': base, 'quote': quote, 'name': '',
                                 'chain': 'Binance Spot', 'chain_id': '', 'contract': '', 'listed_ms': 0}
    return {f'SPOT:{row["pair"]}': row for _, row in best.values()}
