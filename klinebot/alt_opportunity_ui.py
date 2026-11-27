"""Research pages embedded in the existing Tk desktop program."""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .alt_monitor_core import BAR_MS, now_ms
from .alt_opportunity_lp import ReplaySettings, replay
from .alt_opportunity_scan import OpportunityStore, QUOTE_GAP, SCAN_ERRORS, ScanSettings, Scanner
from .chain_replay import NETWORKS

MUTED = '#a1b2c8'
GREEN = '#67d9b2'
BLUE = '#6fb6ff'
PANEL = '#141f2d'
TEXT = '#e5edf7'
DIRECTIONS = {'dex_buy': '链上买 → OKX 卖', 'cex_buy': 'OKX 买 → 链上卖'}


def cash(value):
    return f'{value:+,.3f}'


def ratio(value):
    return f'{value:+.4%}'


class OpportunityPages:
    def __init__(self, root, tabs, store, format_time, translate, market_provider, choose_market):
        self.root = root
        self.tabs = tabs
        self.hk = format_time
        self.t = translate
        self.market_provider = market_provider
        self.choose_market = choose_market
        self.store = OpportunityStore(store)
        self.config_store = store
        self.scanner = Scanner(self.store)
        self.events = queue.Queue()
        self.pages = {}
        self.jobs = {}
        self.scheduled = {}
        self.auto = {}
        self.current = {}
        self.records = {}
        self.statuses = {}
        self.chart_replay = None
        self.closed = False
        self._mount()
        self.root.after(200, self._pump)

    def _mount(self):
        tabs = self.tabs
        self._scan_page(tabs, 'funding', '费率与基差',
                        'OKX USDT 现货＋同币种线性永续。按指定金额扫双腿盘口，估算持有至下一结算的净额。',
                        ('标的', '盘口时间', '下次结算 / 周期', '本期预估', '折合 8h', '基差', '模拟净额 USDT'),
                        ('标的', '入场观测', '结算时刻', '模拟买入数量', '入场估算 USDT', '结果 USDT'))
        self._scan_page(tabs, 'dex', '链上价差',
                        '比较同一数量在链上 V3 池和 OKX 现货的双向价差。模拟假定两边已有库存。',
                        ('标的', '观测时间', '比较方向', '毛价差', 'Gas 成本', '净价差', '正差采样'),
                        ('标的', '采样时间', '比较方向', '净价差', '连续采样跨度', '区块'))
        self._lp_page(tabs)
        self.refresh_market()
        for kind in ('funding', 'dex'):
            self._refresh_scan(kind)
        self._refresh_lp()

    def relabel(self):
        """Recreate only the widgets. The workers, timers and event queue are shared."""
        active = self.tabs.select()
        selected_kind = next((kind for kind, page in self.pages.items() if str(page['frame']) == active), None)
        inputs = {kind: {key: field.get() for key, field in page['fields'].items()} for kind, page in self.pages.items()}
        selections = {kind: page.get('selected') for kind, page in self.pages.items()}
        for page in self.pages.values():
            page['frame'].destroy()
        self.pages.clear()
        self._mount()
        for kind, page in self.pages.items():
            for key, value in inputs[kind].items():
                page['fields'][key].set(value)
            if selections[kind]:
                name, ident = selections[kind]
                if page[name].exists(ident):
                    page[name].selection_set(ident)
                    self.select(kind, name)
            self._buttons(kind)
        if selected_kind:
            self.tabs.select(self.pages[selected_kind]['frame'])

    def _status(self, kind, key, *args):
        self.statuses[kind] = (key, *args)
        self.pages[kind]['status'].set(self.t(key, *args))

    def _page(self, tabs, kind, title, description):
        frame = ttk.Frame(tabs, padding=14)
        tabs.add(frame, text=self.t(title))
        ttk.Label(frame, text=self.t(description), style='Muted.TLabel', wraplength=980).pack(anchor='w', pady=(0, 9))
        status = tk.StringVar(value=self.t(*self.statuses.get(kind, ('等待运行',))))
        self.pages[kind] = {'frame': frame, 'status': status, 'details': tk.StringVar(), 'fields': {}}
        return frame

    def _fields(self, kind, frame, fields):
        page = self.pages[kind]
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 7))
        saved = self.config_store.get(f'opportunity_settings_{kind}', {})
        for index, (key, label, default, options) in enumerate(fields):
            if kind == 'lp' and index == 3:
                row = ttk.Frame(frame)
                row.pack(fill='x', pady=(0, 7))
            ttk.Label(row, text=self.t(label)).pack(side='left', padx=(0, 4))
            value = tk.StringVar(value=saved.get(key, str(default)))
            field = (ttk.Combobox(row, values=options, textvariable=value, width=5, state='readonly') if options
                     else ttk.Entry(row, textvariable=value, width=17 if key in ('symbols', 'cex') else 9))
            field.pack(side='left', padx=(0, 10))
            page['fields'][key] = value

    def _market_row(self, kind, frame):
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 7))
        self.pages[kind]['market'] = label = tk.StringVar()
        ttk.Label(row, textvariable=label).pack(side='left', padx=(0, 10))
        ttk.Button(row, text=self.t('选择链上标的'), command=self.choose_market).pack(side='left')

    def refresh_market(self):
        market = self.market_provider()
        label = (f'{market.symbol or "ERC-20"}/{market.quote} · {NETWORKS[market.chain].name} · {market.token[:10]}…'
                 if market else self.t('请选择链上标的'))
        for kind in ('dex', 'lp'):
            if kind in self.pages:
                self.pages[kind]['market'].set(label)

    def _controls(self, kind, frame):
        page = self.pages[kind]
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=(0, 9))
        once = ttk.Button(row, text=self.t('运行回放' if kind == 'lp' else '立即采样'), style='Primary.TButton',
                          command=lambda: self.start(kind))
        once.pack(side='left')
        text = '取消回放' if kind == 'lp' else '开启 5 分钟采样'
        toggle = ttk.Button(row, text=self.t(text), command=lambda: self.toggle(kind),
                            state='disabled' if kind == 'lp' else 'normal')
        toggle.pack(side='left', padx=7)
        ttk.Button(row, text=self.t('导出所选证据'), command=lambda: self.export(kind)).pack(side='left')
        ttk.Label(row, textvariable=page['status'], style='Muted.TLabel', wraplength=510).pack(side='left', padx=10)
        page.update(once=once, toggle=toggle)

    def _tree(self, kind, frame, key, headings, widths, height):
        box = ttk.Frame(frame)
        box.pack(fill='x', pady=(0, 7))
        columns = tuple(f'c{index}' for index in range(len(headings)))
        tree = ttk.Treeview(box, columns=columns, show='headings', height=height)
        scroll = ttk.Scrollbar(box, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for col, label, width in zip(columns, headings, widths):
            tree.heading(col, text=self.t(label))
            tree.column(col, width=width, minwidth=63, anchor='w', stretch=True)
        tree.bind('<<TreeviewSelect>>', lambda event: self.select(kind, key))
        self.pages[kind][key] = tree
        return tree

    def _scan_page(self, tabs, kind, title, description, current_headers, past_headers):
        frame = self._page(tabs, kind, title, description)
        fields = [('amount', '名义金额', 1000, None), ('spot', '现货费率 bp', 10, None)]
        if kind == 'funding':
            fields.extend([('swap', '永续费率 bp', 5, None), ('symbols', '币种（留空轮扫）', '', None)])
        else:
            self._market_row(kind, frame)
            fields.extend([('gas', 'Gas 单位预算', 200000, None), ('cex', 'OKX 币种（可选）', '', None)])
        self._fields(kind, frame, fields)
        self._controls(kind, frame)
        ttk.Label(frame, text=self.t('最近盘口采样')).pack(anchor='w', pady=(0, 4))
        self._tree(kind, frame, 'current', current_headers, (75, 125, 195, 129, 118, 120, 120), 3 if kind == 'funding' else 4)
        subhead = '正净额样本 · 结算后模拟核算' if kind == 'funding' else '历史采样 · 正差跨度按相邻有效采样统计'
        ttk.Label(frame, text=self.t(subhead)).pack(anchor='w', pady=(0, 4))
        self._tree(kind, frame, 'past', past_headers, (75, 153, 170, 135, 158, 135), 4)
        ttk.Label(frame, textvariable=self.pages[kind]['details'], style='Muted.TLabel', wraplength=965).pack(anchor='w')

    def _lp_page(self, tabs):
        kind = 'lp'
        frame = self._page(tabs, kind, 'LP 对冲回放',
                           '按历史区块核算 V3 区间手续费。开启对冲时，约每小时按代币持仓重算 OKX 永续空头。')
        self._market_row(kind, frame)
        self._fields(kind, frame, [('hours', '约小时', 12, ('6', '12', '24')), ('capital', 'LP 本金', 1000, None),
                                   ('width', '单侧区间 %', 5, None), ('hedge', '对冲比例 %', 100, None),
                                   ('cost', '对冲成本 bp', 7, None), ('chain', '链上总成本', 5, None),
                                   ('cex', 'OKX 币种（可选）', '', None)])
        self._controls(kind, frame)
        self._tree(kind, frame, 'past', ('标的', '历史起点', '历史终点', '价格区间', '手续费', 'LP 净额', '对冲后净额'),
                   (130, 120, 120, 150, 95, 90, 100), 2)
        ttk.Label(frame, textvariable=self.pages[kind]['details'], style='Muted.TLabel', wraplength=965).pack(fill='x', pady=(2, 4))
        chart = tk.Canvas(frame, background=PANEL, highlightthickness=0, height=96)
        chart.pack(fill='both', expand=True, pady=(3, 7))
        chart.bind('<Configure>', lambda event: self._paint_lp())
        self.pages[kind]['chart'] = chart
        self._tree(kind, frame, 'path', ('区块时间', '价格', '区间', 'LP 代币数量', '空头数量', '累计手续费', '对冲后净额'),
                   (154, 120, 85, 122, 122, 132, 135), 4)

    def start(self, kind):
        if kind in self.jobs or self.closed:
            return
        if kind in self.scheduled:
            self.root.after_cancel(self.scheduled.pop(kind))
        page = self.pages[kind]
        raw = {key: field.get() for key, field in page['fields'].items()}
        try:
            market = self.market_provider() if kind != 'funding' else None
            cex = raw.get('cex', '').strip().upper()
            symbols = list(dict.fromkeys(re.findall(r'[^\s,，]+', raw.get('symbols', '').upper())))
            if kind != 'funding' and market is None:
                raise ValueError('请选择链上标的')
            if any(not re.fullmatch(r'[A-Z0-9]{1,24}', symbol) for symbol in [*symbols, *([cex] if cex else [])]):
                raise ValueError('请输入 OKX 币种代码，多个用逗号分隔')
            if kind == 'lp':
                settings = ReplaySettings.from_values(*(raw[key] for key in ('hours', 'capital', 'width', 'hedge', 'cost', 'chain')))
            else:
                settings = ScanSettings.from_values(raw['amount'], raw['spot'], raw.get('swap', 5), raw.get('gas', 200000))
        except SCAN_ERRORS as exc:
            self._status(kind, str(exc))
            self.auto[kind] = False
            self._buttons(kind)
            return
        self.config_store.set(f'opportunity_settings_{kind}', raw)
        stop = threading.Event()
        self.jobs[kind] = stop
        self._status(kind, '正在读取公开数据…')
        self._buttons(kind)

        def work():
            outcome = ('回放完成' if kind == 'lp' else '采样已保存',)
            try:
                if kind == 'lp':
                    result = replay(settings, stop.is_set, lambda *args: self.events.put((kind, 'status', args)),
                                    selected=market, cex_symbol=cex)
                    if not stop.is_set():
                        self.store.save_replay(result)
                else:
                    def update(row):
                        self.events.put((kind, 'sample', row))

                    if kind == 'funding':
                        self.scanner.funding(settings, stop.is_set, update, symbols)
                    else:
                        self.scanner.dex_market(settings, market, cex, stop.is_set, update)
            except SCAN_ERRORS as exc:
                outcome = ('数据不足：{0}', str(exc))
            except Exception:
                logging.exception('Opportunity research %s failed', kind)
                outcome = ('处理失败，详细信息已写入本地日志',)
            self.events.put((kind, 'done', ('已停止',) if stop.is_set() else outcome))

        threading.Thread(target=work, daemon=True, name=f'opportunity-{kind}').start()

    def toggle(self, kind):
        if kind == 'lp' or self.auto.get(kind):
            self.auto[kind] = False
            if kind in self.jobs:
                self.jobs[kind].set()
            if kind in self.scheduled:
                self.root.after_cancel(self.scheduled.pop(kind))
            self._status(kind, '正在停止…' if kind in self.jobs else '已暂停')
        else:
            self.auto[kind] = True
            self.start(kind)
        self._buttons(kind)

    def _buttons(self, kind):
        page = self.pages[kind]
        active = kind in self.jobs
        page['once'].configure(state='disabled' if active else 'normal')
        if kind == 'lp':
            page['toggle'].configure(state='normal' if active else 'disabled')
        else:
            page['toggle'].configure(text=self.t('暂停采样' if self.auto.get(kind) else '开启 5 分钟采样'))

    def _pump(self):
        while not self.events.empty():
            kind, event, data = self.events.get_nowait()
            if event == 'status' and not self.jobs[kind].is_set():
                self._status(kind, *data)
            elif event == 'sample':
                self._refresh_scan(kind)
            elif event == 'done':
                self.jobs.pop(kind, None)
                self._status(kind, *data)
                self._buttons(kind)
                if kind == 'lp':
                    self._refresh_lp(select_latest=data[0] == '回放完成')
                else:
                    self._refresh_scan(kind)
                    if self.auto.get(kind):
                        self.scheduled[kind] = self.root.after(BAR_MS, lambda k=kind: self._scheduled(k))
        if not self.closed:
            self.root.after(200, self._pump)

    def _scheduled(self, kind):
        self.scheduled.pop(kind, None)
        if self.auto.get(kind):
            self.start(kind)

    def _paint_rows(self, kind, name, rows):
        tree = self.pages[kind][name]
        old = tree.selection()
        tree.delete(*tree.get_children())
        for ident, values in rows:
            tree.insert('', 'end', iid=ident, values=values)
        if old and tree.exists(old[0]):
            tree.selection_set(old[0])

    def _refresh_scan(self, kind):
        records = self.store.samples(kind)
        recent = {}
        for row in records:
            recent.setdefault(row.get('market_key', row['symbol']), row)
        current, past, values = [], [], {}
        for key, row in recent.items():
            symbol = row['symbol'] if kind == 'funding' else self.pair_label(row)
            if row.get('error'):
                values[key] = row
                current.append((key, (symbol, self.hk(row['observed']), self.t('数据不足'))))
                continue
            observed = row['observed']
            time = self.hk(observed) + (' · ' + self.t('历史') if now_ms() - observed > QUOTE_GAP else '')
            if kind == 'funding':
                info = row['funding']
                values[symbol] = row
                current.append((symbol, (symbol, time, f'{self.hk(info["settlement"])} / {info["hours"]:g}h',
                                         ratio(info['rate']), ratio(info['rate_8h']), ratio(row['basis']), cash(row['net']))))
            else:
                for side, item in row['opportunities'].items():
                    ident = f'{key}-{side}'
                    values[ident] = row
                    current.append((ident, (symbol, time, self.t(DIRECTIONS[side]), cash(item['gross']),
                                            f'{item["gas_cost"]:.3f}', cash(item['net']), self._span(row, item))))
        self.current[kind] = values
        history = {}
        if kind == 'funding':
            for case in self.store.cases():
                ident = str(case['id'])
                history[ident] = case
                result = case['result'] or {}
                status = (cash(result['net']) if result.get('status') == 'settled' else self.t(
                    '数据缺口' if result else '等待结算资料' if case['exit'] else '待退出采样'))
                past.append((ident, (case['symbol'], self.hk(case['opened']), self.hk(case['settlement']),
                                     f'{case["entry"]["quantity"]:.8g}', cash(case['entry']['net']), status)))
        else:
            for row in records[:100]:
                for side, item in row.get('opportunities', {}).items():
                    ident = f'{row["sample_id"]}-{side}'
                    history[ident] = row
                    past.append((ident, (self.pair_label(row), self.hk(row['observed']), self.t(DIRECTIONS[side]), cash(item['net']),
                                         self._span(row, item), row['block']['number'])))
        self.records[kind] = history
        self._paint_rows(kind, 'current', current)
        self._paint_rows(kind, 'past', past)
        page = self.pages[kind]
        selected = page.get('selected')
        if selected and page[selected[0]].selection():
            self.select(kind, selected[0])
        else:
            default = ('仅正净额入场样本会跟踪一次结算；模拟退出取结算后 1–20 分钟内首份有效盘口。'
                       if kind == 'funding' else '报价含池手续费和价格冲击；另外扣交易所费与估计 Gas。两次采样之间的走势未知。')
            page['details'].set(self.t(default))

    @staticmethod
    def pair_label(row):
        market = row.get('market', {})
        chain = market.get('chain', 1)
        suffix = ' · BNB' if chain == 56 else ' · ETH'
        return f'{market.get("symbol") or row["symbol"]}/{row.get("quote", "USDC")}{suffix}'

    def _span(self, row, item):
        return (self.t('{0:.0f} 分 / {1} 次', (row['observed'] - item['since']) / 60_000, item['samples'])
                if item.get('samples') else '—')

    def _refresh_lp(self, select_latest=False):
        runs = self.store.replays()
        history, rows = {}, []
        for index, run in enumerate(runs):
            first, last = run['points'][0], run['points'][-1]
            ident = str(index)
            history[ident] = run
            market = run.get('market', {'symbol': 'ETH', 'quote': 'USDC'})
            pair = f'{market["symbol"]}/{market["quote"]}'
            rows.append((ident, (pair, self.hk(first['ts']), self.hk(last['ts']),
                                 f'{run["price_low"]:.7g} – {run["price_high"]:.7g}',
                                 f'{last["fees"]:.3f}', cash(last['lp_net']), cash(last['net']))))
        self.records['lp'] = history
        self._paint_rows('lp', 'past', rows)
        tree = self.pages['lp']['past']
        if runs and (select_latest or not tree.selection()):
            tree.selection_set('0')
        if runs:
            self.select('lp', 'past')
        else:
            self.pages['lp']['details'].set(self.t('等待首次回放。盘口执行用标记价加自填成本模拟，USDT 与 USDC 按 1:1 计价。'))

    def select(self, kind, name):
        page = self.pages[kind]
        identifiers = page[name].selection()
        if not identifiers:
            return
        ident = identifiers[0]
        records = self.current[kind] if name == 'current' else self.records[kind]
        row = records.get(ident)
        page['selected'] = (name, ident)
        if not row:
            page['details'].set(self.t('等待采样'))
        elif row.get('error'):
            page['details'].set(row['error'])
        elif kind == 'funding':
            entry = row.get('entry', row)
            text = self.t('{0} · 现货买入 {1:.3f} · 永续卖出 {2:.3f} USDT · 预计资金费 {3} · 往返手续费 {4:.3f}',
                          entry['symbol'], entry['spot_buy'], entry['swap_sell'], cash(entry['funding_pnl']), entry['fees'])
            result = row.get('result')
            if result and result.get('status') == 'settled':
                text += self.t(' · 实际费率 {0} · 结算模拟净额 {1} USDT', ratio(result['rate']), cash(result['net']))
            elif result:
                text += f' · {result["reason"]}'
            page['details'].set(text)
        elif kind == 'dex':
            side = ident.rsplit('-', 1)[-1]
            pool = row.get('sell_pool', row['pool']) if side == 'cex_buy' else row['pool']
            page['details'].set(self.t('{0} · {1:.8g} 枚 · 区块 {2} · 池 {3}\n链上买 / 卖 {4:.3f} / {5:.3f} · OKX 买 / 卖 {6:.3f} / {7:.3f} {8}',
                                       self.pair_label(row), row['quantity'], row['block']['number'], pool,
                                       row['dex_buy'], row['dex_sell'], row['cex_buy'], row['cex_sell'], row.get('quote', 'USDC')))
        else:
            self.chart_replay = row
            end = row['points'][-1]
            page['details'].set(self.t('LP 手续费 {0:.3f} · 永续价差 {1} · 已结算资金费 {2} · 对冲成本 {3:.3f} · 链上成本 {4:.3f} {5}',
                                       end['fees'], cash(end['hedge_pnl']), cash(end['funding']),
                                       end['hedge_cost'], row['settings']['chain_cost'], row.get('market', {}).get('quote', 'USDC')))
            rows = [(str(i), (self.hk(p['ts']), f'{p["price"]:.8g}', self.t('区间内' if p['in_range'] else '区间外'),
                              f'{p.get("base", p.get("eth")):.8g}', f'{p["hedge"]:.8g}', f'{p["fees"]:.3f}', cash(p['net'])))
                    for i, p in enumerate(row['points'])]
            self._paint_rows('lp', 'path', rows)
            self._paint_lp()

    def _paint_lp(self):
        canvas = self.pages['lp']['chart']
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 110 or height < 65:
            return
        points = (self.chart_replay or {}).get('points')
        if not points:
            canvas.create_text(14, 16, text=self.t('回放完成后显示累计净额'), fill=MUTED, anchor='nw')
            return
        maximum = max(0, *(max(p['lp_net'], p['net']) for p in points))
        minimum = min(0, *(min(p['lp_net'], p['net']) for p in points))
        span = max(maximum - minimum, 0.001)
        x0, x1, y0, y1 = 64, width - 22, 30, height - 19
        first, last = points[0]['ts'], points[-1]['ts']
        y = lambda value: y1 - (value - minimum) / span * (y1 - y0)
        canvas.create_line(x0, y(0), x1, y(0), fill='#495b71', dash=(3, 3))
        for i, (key, color, label) in enumerate((('net', GREEN, '对冲后净额'), ('lp_net', BLUE, 'LP 净额'))):
            coords = [(x0 + (p['ts'] - first) / (last - first) * (x1 - x0), y(p[key])) for p in points]
            canvas.create_line(*coords, fill=color, width=2)
            canvas.create_text(x0 + i * 200, 12, text=self.t(label), fill=color, anchor='w')
        for value in (minimum, maximum):
            canvas.create_text(x0 - 6, y(value), text=f'{value:+.2f}', fill=MUTED, anchor='e')
        canvas.create_text(x0, height - 8, text=self.hk(first), fill=MUTED, anchor='w')
        canvas.create_text(x1, height - 8, text=self.hk(last), fill=MUTED, anchor='e')

    def export(self, kind):
        page = self.pages[kind]
        name, ident = page.get('selected', ('past', ''))
        records = self.current[kind] if name == 'current' else self.records[kind]
        record = records.get(ident)
        if not record:
            self._status(kind, '请选择要导出的记录')
            return
        path = filedialog.asksaveasfilename(parent=self.root, title=self.t('导出所选证据'), defaultextension='.json',
                                            filetypes=[(self.t('JSON 文件'), '*.json')], initialfile=f'{kind}-evidence.json')
        if path:
            try:
                Path(path).write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
                self._status(kind, '证据已导出')
            except (OSError, ValueError) as exc:
                self._status(kind, '导出失败：{0}', str(exc))

    def close(self):
        self.closed = True
        for job in self.jobs.values():
            job.set()
        for scheduled in self.scheduled.values():
            self.root.after_cancel(scheduled)
