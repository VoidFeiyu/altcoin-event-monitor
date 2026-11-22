"""Native Windows four-token read-only event monitor. Run with -m klinebot.alt_monitor_app."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import queue
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .alt_monitor_core import BAR_MS, DAY_MS, DIRECTORY, ROOT, TOKENS, Store, now_ms, payload
from .alt_monitor_data import Monitor, seed_local

BG = '#101824'
PANEL = '#192535'
TEXT = '#e5edf7'
MUTED = '#a1b2c8'
BLUE = '#6fb6ff'
GREEN = '#67d9b2'
AMBER = '#f2c47b'
RED = '#f39a9d'


def number(value, digits=5):
    return '—' if value is None or not math.isfinite(float(value)) else f'{value:.{digits}g}'


def percent(value):
    return '—' if value is None or not math.isfinite(float(value)) else f'{value:+.2%}'


def hk(ms, full=False):
    if not ms:
        return '—'
    return pd.Timestamp(int(ms), unit='ms', tz='UTC').tz_convert('Asia/Hong_Kong').strftime('%Y-%m-%d %H:%M' if full else '%m-%d %H:%M')


def research_cases():
    path = ROOT / 'reports' / 'alt_event_four_tokens_20261118' / 'selected_cases.csv'
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


class App:
    def __init__(self, root: tk.Tk, directory: Path = DIRECTORY):
        self.root = root
        self.store = Store(directory)
        self.events = queue.Queue()
        self.monitor = Monitor(self.store, self.events.put)
        self.prepared = False
        self.history = research_cases()
        self.case_map = {}
        self.alert_map = {}
        self.current_symbol = 'RAVE'
        self.chart_points = []
        self.chart_title = ''
        self.rows = dict(self.monitor.rows)
        self.root.title('四币异动观察站 · 测试版')
        width = min(1380, max(1060, root.winfo_screenwidth() - 80))
        height = min(920, max(720, root.winfo_screenheight() - 100))
        self.root.geometry(f'{width}x{height}')
        self.root.minsize(1060, 720)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self._style()
        self._build()
        self.refresh_alerts()
        self._render_all()
        self.monitor.prepare()
        self.root.after(250, self.pump)

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('.', font=('Microsoft YaHei UI', 10), background=BG, foreground=TEXT)
        s.configure('TFrame', background=BG)
        s.configure('Panel.TFrame', background=PANEL)
        s.configure('TLabel', background=BG, foreground=TEXT)
        s.configure('Muted.TLabel', foreground=MUTED)
        s.configure('TButton', background='#2a3d53', foreground=TEXT, borderwidth=0, padding=(14, 8))
        s.map('TButton', background=[('active', '#365674')], foreground=[('disabled', '#728399')])
        s.configure('Primary.TButton', background='#296da6', foreground='white')
        s.map('Primary.TButton', background=[('active', '#3686c8')])
        s.configure('TNotebook', background=BG, borderwidth=0)
        s.configure('TNotebook.Tab', padding=(18, 8), background='#213046', foreground=MUTED)
        s.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', TEXT)])
        s.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=31, borderwidth=0)
        s.configure('Treeview.Heading', background='#25364c', foreground=MUTED, padding=(5, 8))
        s.map('Treeview', background=[('selected', '#294e70')])
        s.configure('TCheckbutton', background=BG, foreground=MUTED)
        s.configure('TCombobox', fieldbackground='#223248', background='#223248', foreground=TEXT)
        s.map('TCombobox', fieldbackground=[('readonly', '#223248')], foreground=[('readonly', TEXT)])
        s.configure('Treeview.Heading', bordercolor='#384b62', lightcolor='#384b62', darkcolor='#384b62')
        self.root.option_add('*TCombobox*Listbox.background', '#223248')
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)

    def _build(self):
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='异动观察站', font=('Microsoft YaHei UI', 23, 'bold')).pack(side='left')
        ttk.Label(header, text='  RAVE / BSB / EVAA / LAB  ·  本地测试版', style='Muted.TLabel').pack(side='left', pady=(9, 0))
        self.mode_text = tk.StringVar(value='已暂停 · 关闭窗口即停止')
        ttk.Label(header, textvariable=self.mode_text, foreground=GREEN).pack(side='right', pady=(9, 0))
        ttk.Label(outer, text='观察后续是否真的出现大变动  ·  现货量价与完整版分别判断  ·  时间均为香港时间', style='Muted.TLabel').pack(anchor='w', pady=(5, 15))

        controls = ttk.Frame(outer)
        controls.pack(fill='x', pady=(0, 14))
        self.start_button = ttk.Button(controls, text='开始监控', style='Primary.TButton', command=self.toggle, state='disabled')
        self.start_button.pack(side='left')
        self.scan_button = ttk.Button(controls, text='立即扫描', command=self.scan, state='disabled')
        self.scan_button.pack(side='left', padx=8)
        ttk.Button(controls, text='导出实时记录', command=self.export).pack(side='left')
        ttk.Label(controls, text='  事件门槛', style='Muted.TLabel').pack(side='left', padx=(15, 5))
        self.threshold = tk.StringVar(value=f'{self.monitor.settings["threshold"]:.0%}')
        self.threshold_box = ttk.Combobox(controls, textvariable=self.threshold, values=('10%', '20%', '30%', '50%'), state='readonly', width=5)
        self.threshold_box.pack(side='left')
        self.threshold_box.bind('<<ComboboxSelected>>', self.configure)
        self.sound = tk.BooleanVar(value=bool(self.monitor.settings['sound']))
        ttk.Checkbutton(controls, text='本机提示音', variable=self.sound, command=self.configure).pack(side='left', padx=12)
        ttk.Label(controls, text='5分钟扫描 · 每币每模型冷却24小时', style='Muted.TLabel').pack(side='right')

        self.card_frame = ttk.Frame(outer)
        self.card_frame.pack(fill='x')
        self.cards = {}
        for col, symbol in enumerate(TOKENS):
            self.card_frame.columnconfigure(col, weight=1)
            box = tk.Frame(self.card_frame, background=PANEL, padx=14, pady=12, highlightthickness=1, highlightbackground='#2b3b50', cursor='hand2')
            box.grid(row=0, column=col, sticky='nsew', padx=(0 if col == 0 else 8, 0))
            name = tk.Label(box, text=symbol, background=PANEL, foreground=TEXT, font=('Segoe UI', 14, 'bold'), anchor='w')
            name.pack(fill='x')
            price = tk.Label(box, text='—', background=PANEL, foreground=TEXT, font=('Segoe UI', 23, 'bold'), anchor='w')
            price.pack(fill='x', pady=(7, 5))
            state = tk.Label(box, text='等待扫描', background=PANEL, foreground=AMBER, font=('Microsoft YaHei UI', 10), anchor='w')
            state.pack(fill='x')
            metrics = tk.Label(box, text='量比 —   24h —', background=PANEL, foreground=MUTED, anchor='w')
            metrics.pack(fill='x', pady=(6, 0))
            at = tk.Label(box, text='K线 —', background=PANEL, foreground=MUTED, anchor='w', font=('Microsoft YaHei UI', 9))
            at.pack(fill='x', pady=(4, 0))
            for widget in (box, name, price, state, metrics, at):
                widget.bind('<Button-1>', lambda event, s=symbol: self.select_symbol(s))
            self.cards[symbol] = dict(box=box, price=price, state=state, metrics=metrics, at=at)

        self.health = tk.StringVar(value='正在读取已有历史基准…')
        ttk.Label(outer, textvariable=self.health, foreground=AMBER, wraplength=1240).pack(fill='x', pady=(10, 12))
        self.progress = tk.StringVar(value='初始化…')
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        ttk.Label(footer, textvariable=self.progress, style='Muted.TLabel').pack(side='left')
        self.countdown = tk.StringVar(value='')
        ttk.Label(footer, textvariable=self.countdown, style='Muted.TLabel').pack(side='right')
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        self.live_tab = ttk.Frame(self.tabs, padding=10)
        self.case_tab = ttk.Frame(self.tabs, padding=10)
        self.guide_tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(self.live_tab, text='实时记录与结果')
        self.tabs.add(self.case_tab, text='历史回放案例')
        self.tabs.add(self.guide_tab, text='规则与数据状态')
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed)

        cols = ('coin', 'model', 'at', 'price', 'evidence', 'd3', 'd7', 'd14')
        self.alert_tree = self._tree(self.live_tab, cols, ('币种', '预警模型', '预警时间', '冻结价格', '触发依据', '3天', '7天', '14天'),
                                     (65, 85, 115, 90, 280, 90, 90, 90), height=4)
        self.alert_tree.bind('<<TreeviewSelect>>', self.select_alert)
        self.empty = ttk.Label(self.live_tab, text='尚无本机实时预警。历史案例在另一个页面；未触发时不会填造记录。', style='Muted.TLabel')
        self.empty.pack(anchor='w', pady=5)
        self.detail = tk.StringVar(value='点击上方币种卡片查看已保存行情与市场参考价。')
        ttk.Label(self.live_tab, textvariable=self.detail, style='Muted.TLabel', wraplength=1200).pack(fill='x', pady=6)
        self.chart = tk.Canvas(self.live_tab, background='#141f2d', highlightthickness=0, height=195)
        self.chart.pack(fill='both', expand=True, pady=(3, 0))
        self.chart.bind('<Configure>', lambda event: self.paint_chart())

        self.case_tree = self._tree(self.case_tab, ('coin', 'kind', 'at', 'price', 'confirm', 'change', 'complete'),
                                    ('币种', '实际事件', '回放预警时间', '当时价格', '确认价', '后续变化', '7天完整'),
                                    (80, 100, 160, 120, 120, 120, 100), height=6)
        self.case_tree.bind('<<TreeviewSelect>>', self.select_case)
        ttk.Label(self.case_tab, text='历史回放，不是当时实时发送的预警。案例按后续表现挑选，不能代表整体命中率。', foreground=AMBER, wraplength=1200).pack(anchor='w', pady=8)
        self.case_detail = tk.StringVar(value='选择案例查看冻结价格、触发条件及实际结果。')
        ttk.Label(self.case_tab, textvariable=self.case_detail, wraplength=1200, style='Muted.TLabel').pack(fill='x')
        self.case_chart = tk.Canvas(self.case_tab, background='#141f2d', highlightthickness=0, height=185)
        self.case_chart.pack(fill='both', expand=True, pady=(8, 0))
        self.case_points = []
        self.case_chart.bind('<Configure>', lambda event: self.paint_on(self.case_chart, self.case_points, '历史Alpha现货价格 · 回放展示'))
        for n, r in enumerate(self.history):
            up = r['case_side'] in ('up', 'recent_up')
            p = float(r['up_price'] if up else r['down_price'])
            at = pd.Timestamp(r['time']).timestamp() * 1000
            key = str(n)
            self.case_map[key] = r
            self.case_tree.insert('', 'end', iid=key, values=(r['symbol'].removesuffix('USDT'), '上涨已确认' if up else '下跌已确认', hk(at, True),
                                                            number(float(r['price'])), number(p), percent(p / float(r['price']) - 1), '是' if r['complete'] == 'True' else '尚未结束'))
        guide = (
            '价量版 A&E：现货最近1小时成交额至少为此前30天中位数的2倍，且达到P95；同时1小时绝对涨跌达到此前30天P95。\n\n'
            '完整版 A / OB / C / E：加入4小时OI数量变化至少10%且P95、按实际结算间隔归一化的已结算费率极端状态；至少满足两项且包含A或OB。OI和费率按5分钟延迟使用。资料缺失时只显示“完整版不足”，不会把价量版冒充完整版。\n\n'
            '结果：相对预警价格达到冻结门槛，同方向连续3根实际成交5分钟K线收盘确认。3 / 7 / 14天分别追踪，已发生可以确认；窗口没走完不会判未达标。数据缺口会明确标出。\n\n'
            '市场参考价：7天、30天成交金额÷成交币数量；前7天高低点；近似30天POC与70%成交密集带。成交分布将每根5分钟的全部成交量放到该根实际均价所在的0.5%对数价桶，仅为近似，区间不是成本置信区间。它们不是庄家或当前持有者的成本。\n\n'
            '价格来自Alpha USDT现货。零成交旧价不触发预警；未收盘K线不参与计算。每条预警保存行情时刻和实际扫描时刻。程序仅判断最近收盘，不把停机期间的历史信号当作实时提醒。\n\n'
            '程序不会开机自动启动。关闭窗口停止监控；最小化后仍可运行。需要电脑保持运行、网络正常。历史补齐以最近32天为主，长期停机的旧缺口不保证全部补回。\n\n'
            '部分官方合约接口可能返回451；软件显示限制，不更换地区出口或代理绕过。缺少OI时价量版可独立运行。没有交易、账户、私钥或对外消息接口。\n\n'
            f'本机数据目录：{self.store.directory}\n规则版本：alpha-events-v1  ·  预警是观察线索，不确定涨跌方向。'
        )
        text = tk.Text(self.guide_tab, background=PANEL, foreground=TEXT, relief='flat', wrap='word', padx=15, pady=15, font=('Microsoft YaHei UI', 11))
        text.insert('1.0', guide); text.configure(state='disabled');text.pack(fill='both', expand=True)

    def _tree(self, parent, columns, labels, widths, height):
        frame = ttk.Frame(parent)
        frame.pack(fill='x')
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=height)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True);scroll.pack(side='right', fill='y')
        for col, label, width in zip(columns, labels, widths):
            tree.heading(col, text=label);tree.column(col, width=width, minwidth=55, anchor='w', stretch=col in ('evidence', 'at'))
        return tree

    def configure(self, event=None):
        self.monitor.configure(int(self.threshold.get().removesuffix('%')) / 100, self.sound.get())
        self.progress.set('新门槛仅用于后续新预警，已有记录保持原门槛。')

    def toggle(self):
        if self.monitor.running:
            self.monitor.pause();self.start_button.configure(text='开始监控');self.mode_text.set('已暂停 · 当前请求结束后停止')
        else:
            self.monitor.start();self.start_button.configure(text='暂停监控');self.mode_text.set('监控中 · 关闭窗口即停止')

    def scan(self):
        self.monitor.scan()

    def pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event['kind']
                if kind == 'prepared':
                    self.prepared = True
                    self.start_button.configure(state='normal');self.scan_button.configure(state='normal')
                elif kind == 'progress':
                    self.progress.set(event['text'])
                elif kind == 'snapshot':
                    self.rows[event['state']['symbol']] = event['state'];self._render_all()
                elif kind == 'alert':
                    self.progress.set(f'新预警：{event["symbol"]} · {"完整版" if event["model"] == "core" else "价量版"}')
                    if event.get('sound'):
                        self.root.bell()
                    self.refresh_alerts()
                elif kind == 'finished':
                    self.refresh_alerts();self._render_all()
                    self.progress.set('本轮完成 · ' + hk(event['at'], True))
                    if self.tabs.index(self.tabs.select()) == 0:
                        self.select_symbol(self.current_symbol)
                elif kind == 'idle':
                    self.scan_button.configure(state='normal' if self.prepared else 'disabled')
                    if not self.monitor.running:self.mode_text.set('已暂停 · 关闭窗口即停止')
        except queue.Empty:
            pass
        if self.prepared:
            self.monitor.tick()
            self.scan_button.configure(state='disabled' if self.monitor.busy else 'normal')
        if not hasattr(self, '_paint_at') or time.monotonic() - self._paint_at > 2:
            self._render_all();self._paint_at = time.monotonic()
        if self.monitor.running and self.monitor.next_scan and not self.monitor.busy:
            self.countdown.set(f'下轮扫描 {max(0, (self.monitor.next_scan-now_ms())//1000)} 秒后')
        else:
            self.countdown.set('扫描中…' if self.monitor.busy else '按需启动 · 本机保存')
        self.root.after(300, self.pump)

    def _render_all(self):
        fresh = ready = 0
        errors = []
        for symbol, widgets in self.cards.items():
            r = self.rows.get(symbol, {})
            actual_fresh = r.get('fresh', False) and 0 <= now_ms() - r.get('bar_ms', 0) <= 2 * BAR_MS
            fresh += bool(actual_fresh)
            ready += bool(actual_fresh and r.get('core_ready'))
            widgets['price'].configure(text=number(r.get('price')))
            text = r.get('status', '尚未扫描')
            if r.get('bar_ms') and not actual_fresh:
                text = '历史缓存 · 等待新鲜行情'
            widgets['state'].configure(text=text, foreground=GREEN if actual_fresh and r.get('ae_ready') else AMBER)
            widgets['metrics'].configure(text=f'量比 {number(r.get("V"), 3)}    24h {percent(r.get("r24"))}')
            widgets['at'].configure(text=f'K线 {hk(r.get("bar_ms"))}  ·  {"完整版就绪" if actual_fresh and r.get("core_ready") else "完整版不足"}')
            if r.get('spot_error'):
                errors.append(f'{symbol}现货：{r["spot_error"]}')
            elif r.get('derivative_error'):
                errors.append(r['derivative_error'])
        reason = next(iter(dict.fromkeys(errors)), '初次使用需要30天历史基准；扫描会读取缓存并补齐。')
        self.health.set(f'新鲜现货 {fresh}/4   ·   完整版就绪 {ready}/4   ·   {reason}')

    def refresh_alerts(self):
        selected = self.alert_tree.selection()
        for item in self.alert_tree.get_children():self.alert_tree.delete(item)
        self.alert_map = {}
        alerts = self.store.alerts()
        for a in alerts:
            self.alert_map[a['id']] = a
            self.alert_tree.insert('', 'end', iid=a['id'], values=(a['symbol'], '完整版' if a['model']=='core' else '价量版', hk(a['bar_ms']), number(a['price']),
                                   '、'.join(a['evidence'].get('triggers', [])), *[a['outcomes'][str(h)]['status'] for h in (72,168,336)]))
        self.empty.configure(text=f'已保存 {len(alerts)} 条本机实时预警；结果按各自冻结门槛判断。' if alerts else '尚无本机实时预警。历史案例在另一个页面；未触发时不会填造记录。')
        if selected and selected[0] in self.alert_map:self.alert_tree.selection_set(selected[0])

    def select_symbol(self, symbol):
        self.current_symbol = symbol
        r = self.rows.get(symbol, {})
        self.detail.set(f'{symbol}  ·  成交均价 7天 {number(r.get("vwap7"))} / 30天 {number(r.get("vwap30"))}  ·  近似POC {number(r.get("poc30"))} / 70%成交带 {number(r.get("value_low"))}—{number(r.get("value_high"))}\n前7天高低 {number(r.get("prior7_high"))} / {number(r.get("prior7_low"))}  ·  市场历史参考，非庄家成本')
        raw = self.store.bars(symbol, now_ms()-3*DAY_MS)
        self.set_chart(raw, f'{symbol} · 最近3天已保存Alpha现货收盘')

    def set_chart(self, frame, title):
        self.chart_points = [] if frame.empty else [(t.timestamp()*1000, float(p)) for t,p in frame.close.items() if pd.notna(p)]
        self.chart_title = title
        self.paint_chart()

    def paint_chart(self):
        self.paint_on(self.chart, self.chart_points, self.chart_title)

    def paint_on(self, c, pts, title):
        c.delete('all');w=c.winfo_width();h=c.winfo_height()
        if w<100 or h<60:return
        c.create_text(14,14,text=title or '选择币种查看已保存行情',fill=MUTED,anchor='nw',font=('Microsoft YaHei UI',9))
        if len(pts)<2:
            c.create_text(w/2,h/2,text='等待可用的真实行情记录',fill=MUTED,font=('Microsoft YaHei UI',11));return
        lo=min(p for _,p in pts);hi=max(p for _,p in pts);delta=hi-lo or max(abs(hi)*.01,.000001)
        left,right,top,bottom=72,w-18,36,h-28
        t0,t1=pts[0][0],pts[-1][0]
        for n in range(4):
            y=top+(bottom-top)*n/3
            c.create_line(left,y,right,y,fill='#243449')
            c.create_text(left-8,y,text=number(hi-delta*n/3),fill=MUTED,anchor='e',font=('Segoe UI',8))
        coords=[]
        # A large historical gap remains a break rather than a fabricated connecting candle.
        segments=[];prev=None
        for t,p in pts:
            if prev is not None and t-prev>2*BAR_MS:
                if len(coords)>=4:segments.append(coords)
                coords=[]
            coords.extend([left+(right-left)*(t-t0)/max(1,t1-t0),bottom-(p-lo)/delta*(bottom-top)]);prev=t
        if len(coords)>=4:segments.append(coords)
        for coords in segments:c.create_line(*coords,fill=BLUE,width=1.5)
        c.create_text(left,bottom+14,text=hk(t0),fill=MUTED,anchor='w',font=('Segoe UI',8))
        c.create_text(right,bottom+14,text=hk(t1),fill=MUTED,anchor='e',font=('Segoe UI',8))

    def select_alert(self, event=None):
        selected=self.alert_tree.selection()
        if not selected:return
        a=self.alert_map[selected[0]]
        self.current_symbol=a['symbol']
        self.detail.set(f'{a["symbol"]} · {"完整版" if a["model"]=="core" else "价量版"} · 行情 {hk(a["bar_ms"], True)} · 实际扫描 {hk(a["observed_ms"], True)} · 冻结门槛 ±{a["threshold"]:.0%} · 确认方向为事后结果')
        raw=self.store.bars(a['symbol'],a['bar_ms']-DAY_MS,min(now_ms(),a['bar_ms']+14*DAY_MS))
        self.set_chart(raw,f'{a["symbol"]} · 该预警前1天至后14天已保存路径')

    def select_case(self, event=None):
        selected=self.case_tree.selection()
        if not selected:return
        r=self.case_map[selected[0]];up=r['case_side'] in ('up','recent_up')
        t=pd.Timestamp(r['up_time'] if up else r['down_time'])
        p=float(r['up_price'] if up else r['down_price']);base=float(r['price'])
        self.case_detail.set(f'历史回放 {r["symbol"]} · {r["triggers"]} · 预警价 {number(base)} → 确认价 {number(p)} ({percent(p/base-1)}) · 确认 {hk(t.timestamp()*1000, True)} · 预警前24h {percent(float(r["prior24h_return"]))}。仅展示既有研究，不写入实时记录。')
        symbol=r['symbol'].removesuffix('USDT')
        start=int(pd.Timestamp(r['time']).timestamp()*1000)-2*DAY_MS
        end=start+9*DAY_MS
        points={}
        folder=ROOT/'data'/'alt_event_four_20261118'/'alpha'/symbol
        for path in sorted(folder.glob('*.json')):
            try:
                page=int(path.stem)*1000
                if page>end or page+1500*BAR_MS<start:continue
                for bar in json.loads(path.read_text()).get('data') or []:
                    at=int(bar[0])+BAR_MS
                    if start<=at<=end:points[at]=float(bar[4])
            except (OSError, ValueError, KeyError):continue
        self.case_points=sorted(points.items())
        self.paint_on(self.case_chart,self.case_points,f'{symbol} · 历史Alpha现货路径（非实时）')

    def tab_changed(self, event=None):
        if self.tabs.index(self.tabs.select())==0:self.select_symbol(self.current_symbol)

    def export(self):
        path=filedialog.asksaveasfilename(parent=self.root,title='导出本机实时预警',defaultextension='.csv',filetypes=[('CSV文件','*.csv')],initialfile='四币异动预警.csv')
        if path:
            self.store.export(Path(path));self.progress.set('已导出：'+path)

    def close(self):
        self.monitor.close()
        self.root.destroy()


def main():
    parser=argparse.ArgumentParser(description='四币异动观察站 · 仅公开行情')
    parser.add_argument('--data-dir',type=Path,default=DIRECTORY)
    parser.add_argument('--scan-once',action='store_true',help='执行一次真实联网扫描后退出，不启动GUI')
    args=parser.parse_args()
    args.data_dir.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(args.data_dir/'monitor.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')
    if args.scan_once:
        store=Store(args.data_dir);seed_local(store,now_ms())
        m=Monitor(store,lambda event: print(payload(event),flush=True))
        m.scan()
        while m.busy:time.sleep(.2)
        return
    # A native local file lock prevents duplicate scanners. No auto-start service is installed.
    lock=(args.data_dir/'instance.lock').open('a+b')
    if os.name=='nt':
        import msvcrt
        if lock.seek(0,2)==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            root=tk.Tk();root.withdraw();messagebox.showinfo('四币异动观察站','程序已经运行，请从任务栏打开现有窗口。');root.destroy();return
    try:
        root=tk.Tk()
        App(root,args.data_dir)
        root.mainloop()
    except Exception:
        logging.exception('Application failed')
        raise
    finally:
        lock.close()


if __name__=='__main__':main()
