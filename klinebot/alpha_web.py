"""Serve the existing crypto radar on localhost with only observation routes."""
from __future__ import annotations

import argparse
from errno import EADDRINUSE
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .alpha_radar import AlphaRadar
from .alt_monitor_core import ROOT, payload
from .alt_opportunity_market import DataUnavailable
from .chain_replay import market_from_alpha, run_replay


PAGE = ROOT/'tradingview'/'alpha_market_radar.html'


class RadarHandler(BaseHTTPRequestHandler):
    chain_lock = threading.Lock()

    def __init__(self, *args, radar, **kwargs):
        self.radar = radar
        super().__init__(*args, **kwargs)

    def reply(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, data, status=200):
        self.reply(payload(data).encode('utf-8'),'application/json; charset=utf-8',status)

    def local_host(self):
        port = self.server.server_port
        host = self.headers.get('Host')
        return host if host in (f'127.0.0.1:{port}',f'localhost:{port}') else None

    def do_GET(self):
        if not self.local_host():
            self.send_error(403)
            return
        url = urlsplit(self.path)
        if url.path in ('/','/alpha','/coin'):
            page = PAGE.read_bytes().replace(b'<body>',b'<body data-radar-only="true">',1)
            self.reply(page,'text/html; charset=utf-8')
            return
        try:
            if url.path=='/api/alpha' and not url.query:
                self.json(self.radar.snapshot())
                return
            if url.path=='/api/alpha/chain':
                self.chain_evidence(url.query)
                return
            if url.path in ('/api/alpha/history','/api/alpha/history/event'):
                self.history_response(url.path,url.query)
                return
            if url.path not in ('/api/alpha/market','/api/coin'):
                self.send_error(404)
                return
            query = parse_qs(url.query,max_num_fields=4,strict_parsing=True)
            key = 'pair' if url.path=='/api/alpha/market' else 'coin'
            required = {key}
            allowed = required | ({'days'} if key=='pair' else {'days','market','signal'})
            if not required<=query.keys()<=allowed or any(len(values)!=1 for values in query.values()):
                raise ValueError('币种详情请求无效')
            identifier,days = query[key][0],int(query.get('days',['3'])[0])
            if key=='pair':
                self.json(self.radar.market_detail(identifier,days))
                return
            market = self.radar.coin_market(identifier)
            if market is None:
                raise KeyError(identifier)
            detail = self.radar.market_detail(market['pair'],days)
            self.json(dict(coin=identifier,symbol=market['symbol'],selected='spot',spot=detail['market'],perp=None,
                           records=[],alerts=detail['alerts'],signal_missing=False,data=detail))
        except ValueError as error:
            self.json({'error':str(error)},400)
        except KeyError:
            error = '找不到该历史事件' if url.path=='/api/alpha/history/event' else '当前目录中找不到此交易对'
            self.json({'error':error},404)
        except (OSError,sqlite3.Error):
            self.json({'error':'本机行情历史暂时不可读'},503)

    def history_response(self, path, query_string):
        query = parse_qs(query_string,max_num_fields=5,strict_parsing=True)
        if any(len(values)!=1 for values in query.values()):
            raise ValueError('异常历史请求无效')
        if path.endswith('/event'):
            if set(query)!={'id'} or not 1<=len(query['id'][0])<=160:
                raise ValueError('事件标识无效')
            self.json(self.radar.history.event(query['id'][0]))
            return
        if set(query)-{'page','source','model','threshold','q'}:
            raise ValueError('历史筛选无效')
        page = int(query.get('page',['1'])[0])
        source = query.get('source',[''])[0]
        model = query.get('model',[''])[0]
        threshold = float(query['threshold'][0]) if 'threshold' in query else None
        search = query.get('q',[''])[0].strip()
        if (not 1<=page<=100000 or source not in ('','spot','alpha') or model not in ('','ae','core')
                or threshold not in (None,.1,.2,.3,.5) or len(search)>80):
            raise ValueError('历史筛选无效')
        self.json(self.radar.history.page(page,source,model,threshold,search))

    def chain_evidence(self, query_string):
        query = parse_qs(query_string,max_num_fields=2,strict_parsing=True)
        if not {'pair'}<=query.keys()<={'pair','alert'} or any(len(values)!=1 for values in query.values()):
            raise ValueError('链上事件请求无效')
        pair = query['pair'][0]
        with self.radar.lock:
            row = self.radar.catalog.get(pair)
        if row is None:
            raise KeyError(pair)
        market = market_from_alpha(row)
        if market is None:
            self.json({'error':'该交易对暂无可用的链上市场映射'},422)
            return
        anchor = None
        if 'alert' in query:
            selected = next((alert for alert in self.radar.coin_alerts(row) if alert['id']==query['alert'][0]),None)
            if selected is None:
                raise KeyError(query['alert'][0])
            anchor = {'ms':selected['bar_ms'],'kind':'alert','id':selected['id']}
        if not self.chain_lock.acquire(blocking=False):
            self.json({'error':'正在读取另一条链上记录，请稍后再试'},409)
            return
        try:
            report = run_replay(market,anchor,3,100,cancelled=self.radar.stop.is_set)
            self.json(dict(market=report['market'],anchor=report['anchor'],minutes=report['minutes'],
                           pools=report['pool_addresses'],summary=report['summary'],
                           event_count=len(report['events']),events=report['events'][-30:]))
        except DataUnavailable as error:
            self.json({'error':str(error)},503)
        finally:
            self.chain_lock.release()

    def do_POST(self):
        if self.path!='/api/alpha':
            self.send_error(404)
            return
        host = self.local_host()
        if not host or self.headers.get('Origin')!=f'http://{host}':
            self.json({'error':'请在本机观察页面修改设置'},403)
            return
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0<length<=512 or self.headers.get_content_type()!='application/json':
                raise ValueError('无效的监测请求')
            data = json.loads(self.rfile.read(length).decode('utf-8'))
            if not isinstance(data,dict):
                raise ValueError('无效的监测请求')
            self.radar.configure(data.get('action'),data.get('value'))
            self.json(self.radar.snapshot())
        except ValueError as error:
            self.json({'error':str(error)},400)
        except (OSError,sqlite3.Error):
            self.json({'error':'本机监测设置暂时无法保存'},503)

    def log_message(self,*args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8769)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data'/'alpha_radar')
    parser.add_argument('--open',action='store_true')
    args = parser.parse_args()
    stop = threading.Event()
    try:
        server = ThreadingHTTPServer(('127.0.0.1',args.port),RadarHandler)
    except OSError as error:
        if error.errno in (EADDRINUSE,10048):
            parser.exit(1,f'端口 {args.port} 已被占用。请关闭旧监控服务后重启。\n')
        raise
    with server:
        radar = AlphaRadar(args.data_dir,stop)
        server.RequestHandlerClass = partial(RadarHandler,radar=radar)
        worker = threading.Thread(target=radar.run,daemon=True,name='crypto-radar')
        worker.start()
        url = f'http://127.0.0.1:{server.server_port}/alpha'
        print(url,flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            worker.join(timeout=15)


if __name__=='__main__':
    main()
