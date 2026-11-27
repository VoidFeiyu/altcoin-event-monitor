"""Market selection, quote comparison and event audit for chain replays."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .alt_monitor_data import PublicDataError
from .alt_opportunity_scan import SCAN_ERRORS
from .chain_replay import ChainReplayStore, Market, NETWORKS, catalog_markets, market_from_alpha, run_replay

EVENT_LABELS = {'swap': '链上成交', 'mint': '增加流动性', 'burn': '移除流动性'}
FILTERS = ('流动性事件', '全部事件', '链上成交')


class ChainReplayPage:
    def __init__(self, root, frame, store, fmt_time, translate):
        self.root, self.frame, self.hk, self.t = root, frame, fmt_time, translate
        self.store, self.history = store, ChainReplayStore(store)
        self.queue = queue.Queue()
        self.worker_stop = None
        self.catalog_loading = False
        self.catalog = []
        self.closed = False
        self.report = None
        self.record_id = None
        self.anchor_id = None
        self.status_value = ('等待运行',)
        selection = store.get('chain_replay_market')
        try:
            self.market = Market(**selection) if selection else next(iter(self.history.saved_markets()), None)
        except SCAN_ERRORS:
            self.market = None
        self._build('5', '1000', 0)
        self.refresh_history()
        self.render()
        self.root.after(200, self._pump)

    def relabel(self):
        tab = self.views.index(self.views.select())
        inputs = self.minutes.get(), self.amount.get(), self.filter_box.current()
        for child in self.frame.winfo_children():
            child.destroy()
        self._build(*inputs)
        self.views.select(tab)
        self.refresh_history()
        self.render()

    def _row(self, parent=None, pady=6):
        row = ttk.Frame(parent or self.frame)
        row.pack(fill='x', pady=(0, pady))
        return row

    def _build(self, minutes, amount, active_filter):
        row = self._row()
        ttk.Label(row, text=self.t('标的')).pack(side='left', padx=(0, 5))
        self.market_box = ttk.Combobox(row, width=43)
        self.market_box.pack(side='left', padx=(0, 6))
        self.market_box.bind('<<ComboboxSelected>>', self.choose_market)
        self.market_box.bind('<Return>', self.choose_market)
        self.market_box.bind('<KeyRelease>', self.filter_markets)
        self.catalog_button = ttk.Button(row, text=self.t('加载 Alpha 币种'), command=self.load_catalog)
        self.catalog_button.pack(side='left', padx=(0, 6))
        self.custom_button = ttk.Button(row, text=self.t('自定义合约'), command=self.toggle_custom)
        self.custom_button.pack(side='left')
        self.quote_choice = tk.StringVar(value=self.market.quote if self.market else 'USDT')
        self.quote_box = ttk.Combobox(row, state='readonly', width=7, values=('USDT', 'USDC'), textvariable=self.quote_choice)
        self.quote_box.pack(side='right')
        self.quote_box.bind('<<ComboboxSelected>>', self.choose_quote)
        ttk.Label(row, text=self.t('计价币')).pack(side='right', padx=(0, 4))

        self.custom_row = ttk.Frame(self.frame)
        self.custom_chain = ttk.Combobox(self.custom_row, state='readonly', width=18, values=tuple(n.name for n in NETWORKS.values()))
        self.custom_chain.current(tuple(NETWORKS).index(self.market.chain) if self.market else 0)
        self.custom_chain.pack(side='left', padx=(0, 5))
        ttk.Label(self.custom_row, text=self.t('合约地址')).pack(side='left', padx=(0, 5))
        self.address = ttk.Entry(self.custom_row, width=44)
        self.address.pack(side='left', padx=(0, 5))
        self.address.bind('<Return>', self.use_custom)
        ttk.Button(self.custom_row, text=self.t('使用合约'), command=self.use_custom).pack(side='left')

        options = self._row()
        self.option_row = options
        self.anchor_box = ttk.Combobox(options, state='readonly', width=34)
        self.anchor_box.pack(side='left', padx=(0, 9))
        self.anchor_box.bind('<<ComboboxSelected>>', self.choose_anchor)
        self.minutes = tk.StringVar(value=minutes)
        ttk.Label(options, text=self.t('前后各分钟')).pack(side='left', padx=(0, 4))
        ttk.Combobox(options, state='readonly', width=4, values=('3', '5', '10'), textvariable=self.minutes).pack(side='left', padx=(0, 7))
        self.amount = tk.StringVar(value=amount)
        ttk.Label(options, text=self.t('参考金额')).pack(side='left', padx=(0, 4))
        ttk.Entry(options, width=8, textvariable=self.amount).pack(side='left', padx=(0, 9))
        self.run_button = ttk.Button(options, text=self.t('运行回放'), command=self.start, style='Primary.TButton')
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(options, text=self.t('取消回放'), command=self.cancel)
        self.cancel_button.pack(side='left', padx=5)

        actions = self._row()
        self.status = tk.StringVar(value=self.t(*self.status_value))
        ttk.Label(actions, textvariable=self.status, style='Muted.TLabel', wraplength=515).pack(side='left')
        self.export_button = ttk.Button(actions, text=self.t('导出所选证据'), command=self.export)
        self.export_button.pack(side='right', padx=(6, 0))
        self.history_box = ttk.Combobox(actions, state='readonly', width=27)
        self.history_box.pack(side='right')
        self.history_box.bind('<<ComboboxSelected>>', self.select_history)

        self.views = ttk.Notebook(self.frame)
        self.views.pack(fill='both', expand=True)
        quote_tab = ttk.Frame(self.views, padding=9)
        event_tab = ttk.Frame(self.views, padding=9)
        self.views.add(quote_tab, text=self.t('报价与汇总'))
        self.views.add(event_tab, text=self.t('事件明细'))
        self.details = tk.StringVar()
        ttk.Label(quote_tab, textvariable=self.details, style='Muted.TLabel', wraplength=920).pack(anchor='w', pady=(0, 5))
        self.quote_tree = self._tree(quote_tab, ('区块时间', '区块', '池', '池价', '买入成本', '卖出收入', '买入／卖出成本'),
                                     (112, 93, 159, 103, 108, 108, 148), 6)
        self.quote_tree.bind('<<TreeviewSelect>>', self.select_quote)
        self.summary_tree = self._tree(quote_tab, ('时间段', '池', '成交数', '净买入', '新增／移除', '有效撤出次数'),
                                       (100, 168, 86, 148, 140, 165), 4)
        filters = self._row(event_tab)
        self.filter_box = ttk.Combobox(filters, state='readonly', width=24, values=tuple(self.t(text) for text in FILTERS))
        self.filter_box.current(max(0, active_filter))
        self.filter_box.pack(side='left', padx=(0, 9))
        self.filter_box.bind('<<ComboboxSelected>>', self.render_events)
        self.event_count = tk.StringVar()
        ttk.Label(filters, textvariable=self.event_count, style='Muted.TLabel').pack(side='left')
        self.timeline = self._tree(event_tab, ('区块／日志', '池', '事件', '计价币', '代币', '价格刻度', '区间状态'),
                                   (129, 158, 132, 100, 100, 118, 108), 8, expand=True)
        self.timeline.bind('<<TreeviewSelect>>', self.select_event)
        self.event_detail = tk.StringVar()
        ttk.Label(event_tab, textvariable=self.event_detail, style='Muted.TLabel', wraplength=920).pack(anchor='w')
        self.refresh_markets()
        self.refresh_anchors()
        self._buttons()

    def _tree(self, parent, headers, widths, height, expand=False):
        frame = ttk.Frame(parent)
        frame.pack(fill='both' if expand else 'x', expand=expand, pady=(0, 5))
        names = tuple(f'c{i}' for i in range(len(headers)))
        tree = ttk.Treeview(frame, columns=names, show='headings', height=height)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for name, label, width in zip(names, headers, widths):
            tree.heading(name, text=self.t(label))
            tree.column(name, width=width, minwidth=65, anchor='w')
        return tree

    @staticmethod
    def _market_label(market):
        label = market.symbol or 'ERC-20'
        return f'{label} · {NETWORKS[market.chain].name} · {market.token[:8]}…{market.token[-4:]}'

    def refresh_markets(self):
        from_radar = [market_from_alpha(row) for row in self.store.get('alpha-market-catalog', {}).values()]
        listed = [*from_radar, *self.catalog, *self.history.saved_markets(), self.market]
        self.market_options = {self._market_label(market): market for market in listed if market}
        self.market_box.configure(values=tuple(self.market_options))
        self.market_box.set(self._market_label(self.market) if self.market else '')

    def filter_markets(self, event):
        if event.keysym in ('Up', 'Down', 'Return', 'Escape'):
            return
        text = self.market_box.get().strip().casefold()
        self.market_box.configure(values=tuple(label for label, market in self.market_options.items()
                                               if text in label.casefold() or text in market.token))

    def choose_market(self, event=None):
        text = self.market_box.get().strip()
        market = self.market_options.get(text)
        if market is None:
            matches = [m for m in self.market_options.values() if m.symbol.casefold() == text.casefold() or m.token == text.lower()]
            if len(matches) != 1:
                self.set_status('请从列表选择标的')
                return
            market = matches[0]
        self.show_market(market)

    def choose_quote(self, event=None):
        if self.market:
            self._set_market(self.market.chain, self.market.token, self.market.symbol, self.quote_choice.get())

    def toggle_custom(self):
        if self.custom_row.winfo_manager():
            self.custom_row.pack_forget()
        else:
            self.custom_row.pack(fill='x', pady=(0, 6), before=self.option_row)
            self.address.focus_set()

    def use_custom(self, event=None):
        chain = tuple(NETWORKS)[self.custom_chain.current()]
        if self._set_market(chain, self.address.get(), '', self.quote_choice.get()):
            self.custom_row.pack_forget()

    def _set_market(self, chain, token, symbol, quote):
        try:
            self.show_market(Market(chain, token, symbol, quote))
            return True
        except SCAN_ERRORS as exc:
            self.quote_choice.set(self.market.quote if self.market else 'USDT')
            self.set_status('数据不足：{0}', str(exc))
            return False

    def show_market(self, market, preferred=None):
        if self.worker_stop:
            if market.key != self.market.key:
                self.set_status('当前回放仍在运行')
            return
        if not self.market or market.key != self.market.key:
            self.record_id = None
            self.report = None
            self.anchor_id = None
            self.set_status('等待运行')
        self.market = market
        self.quote_choice.set(market.quote)
        self.store.set('chain_replay_market', vars(market))
        self.refresh_markets()
        self.refresh_anchors(preferred)
        self.refresh_history()
        self.render()

    def load_catalog(self):
        if self.catalog_loading or self.closed:
            return
        self.catalog_loading = True
        self.set_status('读取 Alpha 币种…')
        self._buttons()

        def run():
            try:
                self.queue.put(('catalog', catalog_markets()))
            except (PublicDataError, *SCAN_ERRORS) as exc:
                self.queue.put(('catalog_error', str(exc)))
            except Exception:
                logging.exception('Alpha catalog load failed')
                self.queue.put(('catalog_error', 'See the local log for details.'))

        threading.Thread(target=run, daemon=True, name='replay-catalog').start()

    def refresh_anchors(self, preferred=None):
        symbol = self.market.symbol if self.market else ''
        keys = []
        if self.market:
            market = self.market
            catalog = self.store.get('alpha-market-catalog', {})
            keys = [pair for pair, row in catalog.items()
                    if row['chain_id'] == str(market.chain) and row['contract'].lower() == market.token]
            if market.radar_symbol:
                keys.append(market.radar_symbol)
        alerts = [alert for key in keys for alert in self.store.alerts(symbol=key, limit=80)]
        self.anchors = [None, *sorted(alerts, key=lambda row: row['bar_ms'], reverse=True)[:80]]
        if preferred and not any(a and a['id'] == preferred for a in self.anchors):
            selected = next((a for a in self.store.alerts() if a['id'] == preferred), None)
            if selected:
                self.anchors.insert(1, selected)
        labels = [self.t('最近完整窗口')]
        for alert in self.anchors[1:]:
            model = self.t('完整版' if alert['model'] == 'core' else '价量版')
            labels.append(self.t('{0} 预警 {1} · {2}', symbol, self.hk(alert['bar_ms'], True), model))
        self.anchor_box.configure(values=labels)
        desired = preferred or self.anchor_id
        index = next((i for i, a in enumerate(self.anchors) if a and a['id'] == desired), 0)
        self.anchor_box.current(index)
        self.anchor_id = self.anchors[index]['id'] if index else None

    def choose_anchor(self, event=None):
        index = self.anchor_box.current()
        self.anchor_id = self.anchors[index]['id'] if index > 0 else None

    def refresh_history(self):
        self.records = self.history.runs(self.market) if self.market else []
        self.history_box.configure(values=(self.t('已保存回放'), *(f'#{a["id"]} · {self.hk(a["anchor"], True)}' for a in self.records)))
        index = next((i for i, a in enumerate(self.records, 1) if a['id'] == self.record_id), 0)
        if not index and self.records and not self.report:
            index = 1
        self.history_box.current(index)
        if index:
            self.select_history()

    def select_history(self, event=None):
        index = self.history_box.current()
        if index > 0:
            self.record_id = self.records[index - 1]['id']
            self.report = self.history.load(self.record_id)
            self.render()

    def set_status(self, key, *args):
        self.status_value = (key, *args)
        self.status.set(self.t(*self.status_value))

    def start(self):
        if self.worker_stop or self.closed:
            return
        if not self.market or self.market_box.get() != self._market_label(self.market):
            self.set_status('请从列表选择标的')
            return
        try:
            minutes, amount = int(self.minutes.get()), float(self.amount.get())
        except ValueError:
            self.set_status('请输入有效的参考金额')
            return
        index = self.anchor_box.current()
        row = self.anchors[index] if index > 0 else None
        anchor = ({'kind': 'alert', 'id': row['id'], 'ms': row['bar_ms'], 'observed_ms': row['observed_ms'],
                   'model': row['model']} if row else None)
        market, stop = self.market, threading.Event()
        self.worker_stop = stop
        self.set_status('正在读取公开数据…')
        self._buttons()

        def run():
            try:
                report = run_replay(market, anchor, minutes, amount, stop.is_set, lambda *data: self.queue.put(('progress', data)))
                if not stop.is_set():
                    self.queue.put(('result', self.history.save(report)))
            except SCAN_ERRORS as exc:
                if not stop.is_set():
                    self.queue.put(('error', str(exc)))
            except Exception:
                logging.exception('Chain replay failed')
                self.queue.put(('error', 'See the local log for details.'))
            finally:
                self.queue.put(('done', stop.is_set()))

        threading.Thread(target=run, daemon=True, name='chain-replay').start()

    def _buttons(self):
        busy = bool(self.worker_stop)
        self.run_button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.market_box.configure(state='disabled' if busy else 'normal')
        self.quote_box.configure(state='disabled' if busy else 'readonly')
        self.custom_button.configure(state='disabled' if busy else 'normal')
        self.catalog_button.configure(state='disabled' if busy or self.catalog_loading else 'normal')
        self.export_button.configure(state='normal' if self.report else 'disabled')

    def _pump(self):
        while not self.queue.empty():
            kind, value = self.queue.get_nowait()
            if kind == 'progress' and self.worker_stop and not self.worker_stop.is_set():
                self.set_status(*value)
            elif kind == 'result':
                self.record_id = value
                self.refresh_history()
                if not self.market.symbol:
                    actual = self.report['market']
                    self.market = Market(actual['chain'], actual['token'], actual['symbol'], actual['quote'])
                    self.store.set('chain_replay_market', vars(self.market))
                    self.refresh_markets()
                self.set_status('回放完成 · {0} 个报价缺口', self.report['quote_gaps'])
            elif kind == 'error':
                self.set_status('数据不足：{0}', value)
            elif kind == 'done':
                self.worker_stop = None
                if value:
                    self.set_status('已停止')
                self._buttons()
            elif kind in ('catalog', 'catalog_error'):
                self.catalog_loading = False
                if kind == 'catalog':
                    self.catalog = value
                    self.refresh_markets()
                    if not self.worker_stop:
                        self.set_status('已加载 {0} 个 Alpha 币种，可按名称或地址筛选', len(value))
                elif not self.worker_stop:
                    self.set_status('数据不足：{0}', value)
                self._buttons()
        if not self.closed:
            self.root.after(200, self._pump)

    @staticmethod
    def _clear(tree):
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def render(self):
        self._clear(self.quote_tree)
        self._clear(self.summary_tree)
        self.quote_rows = {}
        report = self.report
        symbol = report['market']['symbol'] if report else (self.market.symbol if self.market else '') or 'ERC-20'
        currency = self.market.quote if self.market else self.quote_choice.get()
        for col, label in enumerate(('池价', '买入成本', '卖出收入'), 3):
            self.quote_tree.heading(f'c{col}', text=f'{self.t(label)} {currency}')
        self.summary_tree.heading('c3', text=f'{self.t("净买入")} {currency}')
        self.timeline.heading('c3', text=currency)
        self.timeline.heading('c4', text=symbol)
        self.render_events()
        self._buttons()
        if not report:
            self.details.set(self.t('选择标的后运行回放。报价使用同一代币数量，包含池费与价格冲击。'))
            return
        qty = report['quantity_base']
        for idx, point in enumerate(report['points']):
            for p, (pool, row) in enumerate(point['pools'].items()):
                key = f'{idx}-{p}'
                self.quote_rows[key] = row
                if 'buy_quote' in row:
                    mid = row['price'] * qty
                    quote = (f'{row["buy_quote"]:,.3f}', f'{row["sell_quote"]:,.3f}',
                             f'{row["buy_quote"] / mid - 1:.2%} / {1 - row["sell_quote"] / mid:.2%}')
                else:
                    quote = (self.t('数据缺口'), '—', '—')
                price = f'{row["price"]:.6g}' if 'price' in row else '—'
                self.quote_tree.insert('', 'end', iid=key, values=(self.hk(point['ts']), point['number'], pool, price, *quote))
        for side, items in report['summary'].items():
            for pool, row in items.items():
                active = str(row['active_burns']) + (f' + {row["unknown_burns"]} ?' if row['unknown_burns'] else '')
                self.summary_tree.insert('', 'end', values=(self.t('前段' if side == 'before' else '后段'), pool,
                                         row['swaps'], f'{row["net_quote"]:+.2f}', f'{row["mints"]} / {row["burns"]}', active))
        self.details.set(self.quote_detail())

    def quote_detail(self):
        report = self.report
        qty = report['quantity_base']
        message = self.t('固定数量 {0} {1} · 正净买入表示 {2} 经成交净流入池内',
                         f'{qty:.7g}' if qty else '—', report['market']['symbol'], report['market']['quote'])
        if report.get('pool_selection_fallback'):
            message += ' · ' + self.t('交易池按最近区块选取')
        return message

    def render_events(self, event=None):
        self._clear(self.timeline)
        self.visible_events = []
        self.event_count.set('')
        self.event_detail.set('')
        if not self.report:
            return
        mode = self.filter_box.current()
        events = [row for row in self.report['events'] if mode == 1 or (row['kind'] == 'swap') == (mode == 2)]
        self.visible_events = events[-800:]
        self.event_count.set(self.t('显示 {0}/{1} 条 · JSON 含完整证据', len(self.visible_events), len(events)))
        for index, row in enumerate(self.visible_events):
            is_swap = row['kind'] == 'swap'
            tick = row['tick'] if is_swap else f'{row["tick_low"]} … {row["tick_high"]}'
            active = ('—' if is_swap else self.t('未知') if row['active'] is None
                      else self.t('区间内' if row['active'] else '区间外'))
            self.timeline.insert('', 'end', iid=str(index), values=(f'{row["block"]} / {row["log"]}', row['pool'],
                                 self.t(EVENT_LABELS[row['kind']]), f'{row["quote"]:+.2f}', f'{row["base"]:+.5g}', tick, active))

    def select_quote(self, event=None):
        selection = self.quote_tree.selection()
        if selection and selection[0] in self.quote_rows:
            self.details.set(self.quote_rows[selection[0]].get('error') or self.quote_detail())

    def select_event(self, event=None):
        selection = self.timeline.selection()
        if selection:
            row = self.visible_events[int(selection[0])]
            self.event_detail.set(self.t('交易哈希 {0}', row['tx']))

    def export(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title=self.t('导出所选证据'),
                                            defaultextension='.json', initialfile=f'chain-replay-{self.record_id}.json',
                                            filetypes=[(self.t('JSON 文件'), '*.json')])
        if path:
            try:
                Path(path).write_text(json.dumps(self.report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            except (OSError, ValueError) as exc:
                self.set_status('导出失败：{0}', str(exc))
            else:
                self.set_status('证据已导出')

    def cancel(self):
        if self.worker_stop:
            self.worker_stop.set()
            self.set_status('正在停止…')

    def close(self):
        self.closed = True
        if self.worker_stop:
            self.worker_stop.set()
