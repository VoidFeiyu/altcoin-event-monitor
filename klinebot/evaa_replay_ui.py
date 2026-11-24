"""Read-only replay page reached from the EVAA alert radar."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from .alt_opportunity_scan import SCAN_ERRORS
from .evaa_replay import EvaaReplayStore, run_replay

EVENT_LABELS = {'swap': '链上成交', 'mint': '增加流动性', 'burn': '移除流动性'}
FILTERS = ('流动性事件', '全部事件', '链上成交')


class EvaaReplayPage:
    def __init__(self, root, frame, store, fmt_time, translate):
        self.root, self.frame, self.hk, self.t = root, frame, fmt_time, translate
        self.store, self.history = store, EvaaReplayStore(store)
        self.queue = queue.Queue()
        self.worker_stop = None
        self.closed = False
        self.report = None
        self.record_id = None
        self.anchor_id = None
        self.status_value = ('等待运行',)
        self._build('5', '1000', 0)
        self.refresh_history()
        self.root.after(200, self._pump)

    def relabel(self):
        inputs = self.minutes.get(), self.amount.get(), self.filter_box.current()
        for child in self.frame.winfo_children():
            child.destroy()
        self._build(*inputs)
        self.refresh_history()
        self.render()

    def _build(self, minutes, amount, active_filter):
        ttk.Label(self.frame, text=self.t('BNB Chain · EVAA/USDT 双池。前后用同一 EVAA 数量报价，盘口成本包含池费与价格冲击。'),
                  style='Muted.TLabel', wraplength=970).pack(anchor='w', pady=(0, 6))
        controls = ttk.Frame(self.frame)
        controls.pack(fill='x', pady=(0, 6))
        self.anchor_box = ttk.Combobox(controls, state='readonly', width=40)
        self.anchor_box.pack(side='left', padx=(0, 10))
        self.anchor_box.bind('<<ComboboxSelected>>', self.choose_anchor)
        self.minutes = tk.StringVar(value=minutes)
        ttk.Label(controls, text=self.t('前后各分钟')).pack(side='left', padx=(0, 4))
        ttk.Combobox(controls, state='readonly', width=4, values=('3', '5', '10'), textvariable=self.minutes).pack(side='left', padx=(0, 8))
        self.amount = tk.StringVar(value=amount)
        ttk.Label(controls, text=self.t('参考金额 USDT')).pack(side='left', padx=(0, 4))
        ttk.Entry(controls, width=9, textvariable=self.amount).pack(side='left')

        actions = ttk.Frame(self.frame)
        actions.pack(fill='x', pady=(0, 4))
        self.run_button = ttk.Button(actions, text=self.t('运行回放'), command=self.start, style='Primary.TButton')
        self.run_button.pack(side='left')
        self.cancel_button = ttk.Button(actions, text=self.t('取消回放'), command=self.cancel)
        self.cancel_button.pack(side='left', padx=6)
        ttk.Button(actions, text=self.t('导出所选证据'), command=self.export).pack(side='left')
        self.history_box = ttk.Combobox(actions, state='readonly', width=30)
        self.history_box.pack(side='right')
        self.history_box.bind('<<ComboboxSelected>>', self.select_history)
        self.status = tk.StringVar(value=self.t(*self.status_value))
        ttk.Label(self.frame, textvariable=self.status, style='Muted.TLabel', wraplength=960).pack(anchor='w', pady=(0, 4))

        self.quote_tree = self._tree(('区块时间', '区块', '池', '池价 USDT', '买入 USDT', '卖出 USDT', '买入／卖出成本'),
                                     (126, 95, 125, 100, 110, 110, 142), 3)
        self.quote_tree.bind('<<TreeviewSelect>>', self.select_quote)
        self.summary_tree = self._tree(('时间段', '池', '成交数', '净买入 USDT', '新增／移除', '有效撤出次数'),
                                       (100, 137, 95, 175, 145, 160), 3)
        filters = ttk.Frame(self.frame)
        filters.pack(fill='x', pady=(0, 4))
        self.filter_box = ttk.Combobox(filters, state='readonly', width=24, values=tuple(self.t(text) for text in FILTERS))
        self.filter_box.current(max(0, active_filter))
        self.filter_box.pack(side='left', padx=(0, 9))
        self.filter_box.bind('<<ComboboxSelected>>', self.render_events)
        self.event_count = tk.StringVar()
        ttk.Label(filters, textvariable=self.event_count, style='Muted.TLabel').pack(side='left')
        self.timeline = self._tree(('区块／日志', '池', '事件', 'USDT', 'EVAA', '价格刻度', '区间状态'),
                                   (132, 120, 144, 110, 110, 134, 120), 4, expand=True)
        self.timeline.bind('<<TreeviewSelect>>', self.select_event)
        self.detail = tk.StringVar()
        ttk.Label(self.frame, textvariable=self.detail, style='Muted.TLabel', wraplength=965).pack(anchor='w')
        self.refresh_anchors()
        self._buttons()

    def _tree(self, headers, widths, height, expand=False):
        frame = ttk.Frame(self.frame)
        frame.pack(fill='both' if expand else 'x', expand=expand, pady=(0, 5))
        names = tuple(f'c{i}' for i in range(len(headers)))
        tree = ttk.Treeview(frame, columns=names, show='headings', height=height)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        for name, label, width in zip(names, headers, widths):
            tree.heading(name, text=self.t(label))
            tree.column(name, width=width, minwidth=60, anchor='w')
        return tree

    def refresh_anchors(self, preferred=None):
        self.anchors = [None, *self.store.alerts(symbol='EVAA', limit=80)]
        self.anchor_box.configure(values=(self.t('最近完整窗口'), *(self._anchor_label(a) for a in self.anchors[1:])))
        desired = preferred or self.anchor_id
        index = next((i for i, a in enumerate(self.anchors) if a and a['id'] == desired), 0)
        self.anchor_box.current(index)
        self.anchor_id = self.anchors[index]['id'] if index else None

    def _anchor_label(self, row):
        model = self.t('完整版' if row['model'] == 'core' else '价量版')
        return self.t('EVAA 预警 {0} · {1}', self.hk(row['bar_ms'], True), model)

    def choose_anchor(self, event=None):
        index = self.anchor_box.current()
        self.anchor_id = self.anchors[index]['id'] if index > 0 else None

    def refresh_history(self):
        self.records = self.history.runs()
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
            self.record_id = self.records[index-1]['id']
            self.report = self.history.load(self.record_id)
            self.render()

    def set_status(self, key, *args):
        self.status_value = (key, *args)
        self.status.set(self.t(*self.status_value))

    def start(self):
        if self.worker_stop or self.closed:
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
        stop = threading.Event()
        self.worker_stop = stop
        self.set_status('正在读取公开数据…')
        self._buttons()

        def run():
            try:
                report = run_replay(anchor, minutes, amount, stop.is_set, lambda *data: self.queue.put(('progress', data)))
                if not stop.is_set():
                    self.queue.put(('result', self.history.save(report)))
            except SCAN_ERRORS as exc:
                if not stop.is_set():
                    self.queue.put(('error', str(exc)))
            except Exception:
                logging.exception('EVAA replay failed')
                self.queue.put(('error', 'See the local log for details.'))
            finally:
                self.queue.put(('done', stop.is_set()))

        threading.Thread(target=run, daemon=True, name='evaa-replay').start()

    def _buttons(self):
        self.run_button.configure(state='disabled' if self.worker_stop else 'normal')
        self.cancel_button.configure(state='normal' if self.worker_stop else 'disabled')

    def _pump(self):
        while not self.queue.empty():
            kind, value = self.queue.get_nowait()
            if kind == 'progress' and self.worker_stop and not self.worker_stop.is_set():
                self.set_status(*value)
            elif kind == 'result':
                self.record_id = value
                self.refresh_history()
                self.set_status('回放完成 · {0} 个报价缺口', self.report['quote_gaps'])
            elif kind == 'error':
                self.set_status('数据不足：{0}', value)
            elif kind == 'done':
                self.worker_stop = None
                if value:
                    self.set_status('已停止')
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
        if not self.report:
            return
        report = self.report
        qty = report['quantity_evaa']
        self.quote_rows = {}
        for idx, point in enumerate(report['points']):
            for p, (pool, row) in enumerate(point['pools'].items()):
                key = f'{idx}-{p}'
                self.quote_rows[key] = row
                if 'buy_usdt' in row:
                    mid = row['price'] * qty
                    quote = (f'{row["buy_usdt"]:.2f}', f'{row["sell_usdt"]:.2f}',
                             f'{row["buy_usdt"] / mid - 1:.2%} / {1 - row["sell_usdt"] / mid:.2%}')
                else:
                    quote = (self.t('数据缺口'), '—', '—')
                price = f'{row["price"]:.5g}' if 'price' in row else '—'
                self.quote_tree.insert('', 'end', iid=key, values=(self.hk(point['ts']), point['number'], pool, price, *quote))
        for side, items in report['summary'].items():
            for pool, row in items.items():
                active = str(row['active_burns']) + (f' + {row["unknown_burns"]} ?' if row['unknown_burns'] else '')
                self.summary_tree.insert('', 'end', values=(self.t('前段' if side == 'before' else '后段'), pool,
                                         row['swaps'], f'{row["net_usdt"]:+.2f}', f'{row["mints"]} / {row["burns"]}', active))
        self.detail.set(self.t('固定数量 {0} EVAA。正净买入表示 USDT 净流入该池。', f'{qty:.6g}' if qty else '—'))
        self.render_events()

    def render_events(self, event=None):
        self._clear(self.timeline)
        if not self.report:
            return
        mode = self.filter_box.current()
        events = [row for row in self.report['events'] if mode == 1 or (row['kind'] == 'swap') == (mode == 2)]
        shown = events[-800:]
        self.event_count.set(self.t('显示 {0}/{1} 条 · JSON 含完整证据', len(shown), len(events)))
        self.visible_events = shown
        for index, row in enumerate(shown):
            is_swap = row['kind'] == 'swap'
            tick = row['tick'] if is_swap else f'{row["tick_low"]} … {row["tick_high"]}'
            active = ('—' if is_swap else self.t('未知') if row['active'] is None
                      else self.t('区间内' if row['active'] else '区间外'))
            self.timeline.insert('', 'end', iid=str(index), values=(f'{row["block"]} / {row["log"]}', row['pool'],
                                 self.t(EVENT_LABELS[row['kind']]), f'{row["usdt"]:+.2f}', f'{row["evaa"]:+.4f}', tick, active))

    def select_quote(self, event=None):
        selection = self.quote_tree.selection()
        if selection and selection[0] in self.quote_rows:
            row = self.quote_rows[selection[0]]
            qty = self.report['quantity_evaa']
            self.detail.set(row.get('error') or self.t('固定数量 {0} EVAA。正净买入表示 USDT 净流入该池。', f'{qty:.6g}' if qty else '—'))

    def select_event(self, event=None):
        selection = self.timeline.selection()
        if selection:
            row = self.visible_events[int(selection[0])]
            self.detail.set(self.t('交易哈希 {0}', row['tx']))

    def export(self):
        if not self.report:
            self.set_status('请选择要导出的记录')
            return
        path = filedialog.asksaveasfilename(parent=self.root, title=self.t('导出所选证据'),
                                            defaultextension='.json', initialfile=f'evaa-replay-{self.record_id}.json',
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
