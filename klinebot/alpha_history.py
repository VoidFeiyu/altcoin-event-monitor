"""Long-lived results and price paths for the web radar's recorded anomalies."""
from __future__ import annotations

import json

import pandas as pd

from .alt_monitor_core import BAR_MS, DAY_MS, now_ms, payload

HOUR_MS = 3_600_000
HORIZONS = (1, 6, 24, 72, 168, 336)
PAGE_SIZE = 30
SOURCE = "COALESCE(json_extract(evidence,'$.source'), CASE WHEN symbol LIKE 'SPOT:%' THEN 'spot' ELSE 'alpha' END)"


def summarize_path(raw, at, price, asof):
    end = min(asof // BAR_MS * BAR_MS, at + 14 * DAY_MS)
    expected = pd.date_range(pd.Timestamp(at,unit='ms',tz='UTC'),pd.Timestamp(end,unit='ms',tz='UTC'),freq='5min')[1:]
    bars = raw.reindex(expected)
    observed = bars[['open','high','low','close']].notna().all(axis=1)
    active = observed & bars.trades.gt(0) & bars.base.gt(0) & bars.quote.gt(0)
    closes = bars.close.where(active)

    def change(value):
        return float(value)/price-1 if pd.notna(value) else None

    windows = {}
    for hours in HORIZONS:
        target = at + hours * HOUR_MS
        endpoint = pd.Timestamp(target,unit='ms',tz='UTC')
        window = closes.loc[:endpoint]
        usable = observed.loc[:endpoint]
        windows[str(hours)] = dict(closed=end>=target,expected=len(usable),available=int(usable.sum()),
                                  change=change(closes.get(endpoint)),high=change(window.max()),low=change(window.min()),
                                  terminal_no_trade=bool(endpoint in observed.index and observed.get(endpoint) and not active.get(endpoint)))

    path = [[at,0,False]]
    if not active.any():
        return dict(windows=windows,path=path,through=end)
    for series,interval in ((closes.iloc[:288],'15min'),(closes.iloc[288:],'1h')):
        for _,batch in series.resample(interval,closed='right',label='right',origin=pd.Timestamp(at,unit='ms',tz='UTC')):
            last = batch.dropna()
            partial = not observed.loc[batch.index].all()
            if not batch.empty:
                timestamp = batch.index[-1] if last.empty else last.index[-1]
                value = None if last.empty else change(last.iloc[-1])
                path.append([int(timestamp.timestamp()*1000),value,bool(partial)])
    return dict(windows=windows,path=path,through=end)


class EventHistory:
    def __init__(self, store):
        self.store = store
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS radar_event_history(
                symbol TEXT,bar_ms INTEGER,through_ms INTEGER,available INTEGER,data TEXT,
                PRIMARY KEY(symbol,bar_ms))''')

    def backfill(self, asof, stop):
        with self.store.connect() as db:
            symbols = db.execute('SELECT DISTINCT symbol FROM alerts WHERE bar_ms>=?',(asof-34*DAY_MS,)).fetchall()
        for row in symbols:
            if stop.is_set():
                break
            self.store.track(asof,symbol=row['symbol'],lookback_days=34)
            self.refresh(row['symbol'],asof)

    def refresh(self, symbol, asof):
        with self.store.connect() as db:
            rows = db.execute('''SELECT a.bar_ms, MIN(a.price) AS price, h.through_ms,h.available
                              FROM alerts a LEFT JOIN radar_event_history h USING(symbol,bar_ms)
                              WHERE a.symbol=? AND a.bar_ms BETWEEN ? AND ?
                              GROUP BY a.bar_ms ORDER BY a.bar_ms''',(symbol,asof-34*DAY_MS,asof)).fetchall()
        pending = []
        for row in rows:
            at,through = row['bar_ms'],row['through_ms']
            if through is None:
                pending.append(row)
                continue
            finished = through>=at+14*DAY_MS
            if finished and (row['available']==14*DAY_MS//BAR_MS or asof>=at+16*DAY_MS):
                continue
            if asof>=through+(BAR_MS if through<at+DAY_MS else HOUR_MS):
                pending.append(row)
        if not pending:
            return
        raw = self.store.bars(symbol,pending[0]['bar_ms']+BAR_MS,asof)
        archived = []
        for row in pending:
            at = row['bar_ms']
            data = summarize_path(raw,at,row['price'],asof)
            available = data['windows']['336']['available']
            if available or row['through_ms'] is None:
                archived.append((symbol,at,data['through'],available,payload(data)))
        with self.store.connect() as db:
            db.executemany('''INSERT INTO radar_event_history VALUES(?,?,?,?,?)
                ON CONFLICT(symbol,bar_ms) DO UPDATE SET through_ms=excluded.through_ms,
                available=excluded.available,data=excluded.data
                WHERE excluded.through_ms>=radar_event_history.through_ms
                  AND excluded.available>=radar_event_history.available''',archived)

    def page(self, page=1, source='', model='', threshold=None, query=''):
        match = ["(?='' OR "+SOURCE+"=?)", "(?='' OR model=?)", '(? IS NULL OR threshold=?)']
        args = [source,source,model,model,threshold,threshold]
        if query:
            match.append("instr(lower(symbol||' '||COALESCE(json_extract(evidence,'$.display_symbol'),'')||' '||COALESCE(json_extract(evidence,'$.pair'),'')),?)>0")
            args.append(query.lower())
        matched = 'SELECT symbol,bar_ms,MIN(id) AS id FROM alerts WHERE '+' AND '.join(match)+' GROUP BY symbol,bar_ms'
        with self.store.connect() as db:
            total = db.execute('SELECT count(*) FROM ('+matched+')',args).fetchone()[0]
            page = min(page,max(1,(total+PAGE_SIZE-1)//PAGE_SIZE))
            records = db.execute(matched+' ORDER BY bar_ms DESC,symbol LIMIT ? OFFSET ?',
                                 (*args,PAGE_SIZE,(page-1)*PAGE_SIZE)).fetchall()
            totals = db.execute('''WITH horizons(hours) AS (VALUES(72),(168),(336))
                SELECT h.hours,count(*) AS matured,
                  SUM(CASE WHEN json_extract(o.result,'$.complete')=1 THEN 1 ELSE 0 END) AS complete,
                  SUM(CASE WHEN json_extract(o.result,'$.complete')=1 AND json_extract(o.result,'$.hit')=1
                           THEN 1 ELSE 0 END) AS reached
                FROM ('''+matched+''') e CROSS JOIN horizons h
                LEFT JOIN outcomes o ON o.alert_id=e.id AND o.hours=h.hours
                WHERE e.bar_ms+h.hours*3600000<=? GROUP BY h.hours''',(*args,now_ms())).fetchall()
            rows = [self._load(db,row,detail=False) for row in records]
        return dict(rows=rows,total=total,page=page,page_size=PAGE_SIZE,
                    totals={row['hours']:dict(matured=row['matured'],complete=row['complete'],reached=row['reached']) for row in totals})

    def event(self, key):
        with self.store.connect() as db:
            row = db.execute('SELECT symbol,bar_ms,id FROM alerts WHERE id=?',(key,)).fetchone()
            if row is None:
                raise KeyError(key)
            return self._load(db,row,detail=True)

    def _load(self, db, key, *, detail):
        identity = key['symbol'],key['bar_ms']
        alerts = db.execute('SELECT * FROM alerts WHERE symbol=? AND bar_ms=? ORDER BY model',identity).fetchall()
        event = next(alert for alert in alerts if alert['id']==key['id'])
        evidence = json.loads(event['evidence'])
        outcomes = db.execute('SELECT hours,result FROM outcomes WHERE alert_id=?',(event['id'],)).fetchall()
        snapshot = db.execute('SELECT data FROM radar_event_history WHERE symbol=? AND bar_ms=?',identity).fetchone()
        archive = json.loads(snapshot['data']) if snapshot else {}
        result = dict(id=event['id'], market=event['symbol'],symbol=evidence.get('display_symbol',event['symbol']),
                      source=evidence.get('source','spot' if event['symbol'].startswith('SPOT:') else 'alpha'),
                      pair=evidence.get('pair') or event['symbol'].removeprefix('SPOT:'),quote=evidence.get('quote','USDT'),
                      at=event['bar_ms'],observed=event['observed_ms'],price=event['price'],threshold=event['threshold'],
                      version=event['version'],models=sorted({alert['model'] for alert in alerts}),
                      triggers=list(dict.fromkeys(reason for alert in alerts for reason in json.loads(alert['evidence']).get('triggers',[]))),
                      results={row['hours']:json.loads(row['result']) for row in outcomes},windows=archive.get('windows',{}))
        if detail:
            result.update(evidence=evidence,path=archive.get('path',[]),through=archive.get('through'))
            if not snapshot and event['bar_ms']>now_ms()-34*DAY_MS:
                result['archive_pending'] = True
        return result
