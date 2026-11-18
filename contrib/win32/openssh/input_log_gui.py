"""Traditional Chinese desktop viewer for InputLog1 recordings. ISC licensed."""
import argparse
import io
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from input_log_reader import (decode_base64, decoded_powershell_events, decoded_record_stream,
                              powershell_commands, read_record_stream)

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
PAGE_SIZE = 500
ALL_CHANNELS = '全部頻道'
EVENTS = {
    '全部事件': None, '指令請求 (exec)': 'exec', '終端輸入 (stdin)': 'stdin',
    '強制指令 (effective-command)': 'effective-command', '開啟終端 (shell)': 'shell',
    '結束 (close)': 'close', '未完整字元 (decoder-tail)': 'decoder-tail',
}


def visible_text(value, multiline=False):
    """Keep Unicode readable while exposing controls, bidi marks and backslashes."""
    escapes = {'\\': '\\\\', '\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b'}
    result = []
    for character in str(value):
        if multiline and character in ('\\', '\n', '\t'):
            result.append(character)
        elif character in escapes:
            result.append(escapes[character])
        elif character.isprintable():
            result.append(character)
        else:
            code = ord(character)
            result.append(f'\\x{code:02x}' if code < 256 else
                          f'\\u{code:04x}' if code <= 0xffff else f'\\U{code:08x}')
    return ''.join(result)


class Recording:
    def __init__(self, path, raw):
        self.path = Path(path)
        self.raw = raw
        self.header = raw[0]
        for record in raw[1:]:
            channel = record.get('channel')
            if isinstance(channel, bool) or not isinstance(channel, int) or channel < 1:
                raise ValueError('紀錄中的頻道編號必須是正整數。')
        self.channels = sorted({r['channel'] for r in raw[1:]})
        self.events = list(decoded_record_stream(raw))[1:]
        self.text = [visible_text(r.get('text', '')) for r in self.events]
        self.decoded = {index: '\n\n'.join(visible_text(command, multiline=True)
                                          for command in commands)
                        for index, commands in decoded_powershell_events(self.events).items()}
        self.by_sequence = {r['sequence']: r for r in raw[1:]}
        self.stdin_bytes = sum(len(r['data']) for r in raw[1:] if r['event'] == 'stdin')

    @classmethod
    def load(cls, path):
        # Read one bounded snapshot: exports and the table refer to the same
        # data even when the server is still appending to the recording.
        with open(path, 'rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('圖形介面最多讀取 64 MiB 的單一紀錄；較大的檔案請使用 CLI 版本。')
        records = []
        for record in read_record_stream(io.StringIO(data.decode('ascii')), path):
            records.append(record)
            if len(records) > MAX_RECORDS + 1:
                raise ValueError('圖形介面最多讀取 100,000 筆事件；請使用 CLI 版本處理更大的紀錄。')
        return cls(path, records)

    def filter(self, channel=None, event=None, query=''):
        query = query.casefold()
        return [i for i, r in enumerate(self.events)
                if (channel is None or r.get('channel') == channel)
                and (event is None or r.get('event') == event)
                and (not query or query in self.text[i].casefold()
                     or query in self.decoded.get(i, '').casefold()
                     or query in str(r.get('time', '')).casefold()
                     or query in str(r.get('event', '')).casefold())]

    def export_text(self, path, indices):
        with open(path, 'x', encoding='utf-8', newline='\n') as output:
            output.write('SSH 輸入紀錄\n')
            output.write(f"使用者：{visible_text(self.header.get('user', ''))}\n")
            output.write(f"來源：{visible_text(self.header.get('remote', ''))}:{visible_text(self.header.get('remote_port', ''))}\n")
            output.write(f"開始時間（UTC）：{visible_text(self.header.get('time', ''))}\n\n")
            for i in indices:
                record = self.events[i]
                output.write(f"[{visible_text(record.get('time', ''))}] #{record.get('sequence', '—')} "
                             f"頻道 {record.get('channel', '—')}  {record['event']} "
                             f"({record.get('bytes', 0)} bytes)\n{self.text[i]}\n")
                if i in self.decoded:
                    output.write(f"Base64 解碼（UTF-16LE）\n{self.decoded[i]}\n")
                output.write('\n')

    def export_stdin(self, path, channel):
        if channel not in self.channels:
            raise ValueError('請先選取一個有效頻道。')
        with open(path, 'xb') as output:
            for record in self.raw[1:]:
                if record['event'] == 'stdin' and record['channel'] == channel:
                    output.write(record['data'])


class ReaderWindow:
    def __init__(self, root):
        self.root = root
        self.model = None
        self.path = None
        self.folder = None
        self.folder_files = []
        self.indices = []
        self.page = 0
        self.load_token = 0
        self.results = queue.Queue()
        self.filter_timer = None
        self.closed = False
        self.poll_timer = None
        root.title('SSH 輸入紀錄檢視器')
        root.geometry('1180x820')
        root.minsize(960, 680)
        root.configure(background='#f3f5f9')
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        available_fonts = set(tkfont.families(root))
        self.ui_font = next((name for name in ('Microsoft JhengHei UI', 'Microsoft JhengHei', 'Segoe UI')
                             if name in available_fonts), 'TkDefaultFont')
        style.configure('.', font=(self.ui_font, 10))
        style.configure('TFrame', background='#f3f5f9')
        style.configure('TLabel', background='#f3f5f9')
        style.configure('Treeview', rowheight=28, font=(self.ui_font, 10))
        style.configure('Treeview.Heading', font=(self.ui_font, 10, 'bold'))
        style.configure('Title.TLabel', font=(self.ui_font, 21, 'bold'), foreground='#14263e')
        style.configure('Muted.TLabel', foreground='#607086')

        outer = ttk.Frame(root, padding=20)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)
        titlebar = ttk.Frame(outer)
        titlebar.grid(row=0, column=0, sticky='ew')
        ttk.Label(titlebar, text='SSH 輸入紀錄', style='Title.TLabel').pack(side='left')
        ttk.Button(titlebar, text='開啟紀錄檔…', command=self.choose_file).pack(side='right')
        ttk.Button(titlebar, text='開啟資料夾…', command=self.choose_folder).pack(side='right', padx=(6, 0))
        ttk.Button(titlebar, text='Base64 解碼…', command=self.open_decoder).pack(side='right', padx=(6, 0))
        self.refresh_button = ttk.Button(titlebar, text='重新讀取', command=self.refresh, state='disabled')
        self.refresh_button.pack(side='right', padx=8)
        self.folder_bar = ttk.Frame(outer)
        self.folder_bar.columnconfigure(1, weight=1)
        ttk.Label(self.folder_bar, text='紀錄檔').grid(row=0, column=0, padx=(0, 8))
        self.folder_box = ttk.Combobox(self.folder_bar, state='readonly')
        self.folder_box.grid(row=0, column=1, sticky='ew')
        self.folder_box.bind('<<ComboboxSelected>>', self.select_folder_record)
        self.folder_count = ttk.Label(self.folder_bar, style='Muted.TLabel')
        self.folder_count.grid(row=0, column=2, padx=(8, 0))
        self.file_label = ttk.Label(outer, text='尚未開啟紀錄檔', style='Muted.TLabel', wraplength=1080)
        self.file_label.grid(row=2, column=0, sticky='ew', pady=(10, 12))

        metadata = ttk.Frame(outer)
        metadata.grid(row=3, column=0, sticky='ew', pady=(0, 14))
        self.metadata = []
        for column, label in enumerate(['使用者', '來源', '開始時間（UTC）']):
            card = ttk.Frame(metadata, padding=(12, 8))
            card.grid(row=0, column=column, sticky='nsew', padx=(0, 12 if column < 2 else 0))
            metadata.columnconfigure(column, weight=1)
            ttk.Label(card, text=label, style='Muted.TLabel').pack(anchor='w')
            value = tk.StringVar(value='—')
            ttk.Label(card, textvariable=value, font=(self.ui_font, 11, 'bold')).pack(anchor='w', pady=(3, 0))
            self.metadata.append(value)

        filters = ttk.Frame(outer)
        filters.grid(row=4, column=0, sticky='ew', pady=(0, 10))
        self.channel = tk.StringVar(value=ALL_CHANNELS)
        self.event = tk.StringVar(value='全部事件')
        self.query = tk.StringVar()
        ttk.Label(filters, text='頻道').pack(side='left', padx=(0, 6))
        self.channel_box = ttk.Combobox(filters, textvariable=self.channel, values=[ALL_CHANNELS], state='readonly', width=13)
        self.channel_box.pack(side='left', padx=(0, 14))
        ttk.Label(filters, text='事件').pack(side='left', padx=(0, 6))
        event_box = ttk.Combobox(filters, textvariable=self.event, values=list(EVENTS), state='readonly', width=23)
        event_box.pack(side='left', padx=(0, 14))
        ttk.Label(filters, text='搜尋').pack(side='left', padx=(0, 6))
        query_box = ttk.Entry(filters, textvariable=self.query, width=23)
        query_box.pack(side='left', fill='x', expand=True)
        ttk.Button(filters, text='清除篩選', command=self.clear_filters).pack(side='left', padx=(8, 0))
        self.channel_box.bind('<<ComboboxSelected>>', lambda e: self.apply_filters())
        event_box.bind('<<ComboboxSelected>>', lambda e: self.apply_filters())
        self.query.trace_add('write', self.schedule_filter)

        panes = ttk.Panedwindow(outer, orient='vertical')
        panes.grid(row=5, column=0, sticky='nsew')
        self.split_positioned = False
        def position_split(event):
            if not self.split_positioned and event.height > 160:
                panes.sashpos(0, int(event.height * .57))
                self.split_positioned = True
        panes.bind('<Configure>', position_split)
        table_frame = ttk.Frame(panes)
        panes.add(table_frame, weight=3)
        columns = ('sequence', 'time', 'channel', 'event', 'bytes', 'preview')
        self.table = ttk.Treeview(table_frame, columns=columns, show='headings', selectmode='browse')
        for name, label, width in zip(columns, ['序號', '時間（UTC）', '頻道', '事件', '位元組', '輸入預覽'], [62, 195, 56, 128, 68, 490]):
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=45, stretch=(name == 'preview'))
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.tag_configure('alternate', background='#f2f6fc')
        self.table.bind('<<TreeviewSelect>>', self.show_selection)
        self.empty_label = ttk.Label(table_frame, text='按「開啟紀錄檔」選取 .jsonl 檔案', padding=14)

        detail_frame = ttk.Frame(panes, padding=(0, 8, 0, 0))
        panes.add(detail_frame, weight=2)
        self.notebook = ttk.Notebook(detail_frame)
        self.notebook.pack(fill='both', expand=True)
        self.detail = self.text_tab(self.notebook, '輸入內容', font=(self.ui_font, 11))
        self.decoded_detail = self.text_tab(self.notebook, 'Base64 解碼', font=(self.ui_font, 11))
        self.hex_detail = self.text_tab(self.notebook, '原始位元組')

        bottom = ttk.Frame(outer)
        bottom.grid(row=6, column=0, sticky='ew', pady=(12, 0))
        self.status = tk.StringVar(value='開啟紀錄檔後，選取事件即可查看完整內容。')
        ttk.Label(bottom, textvariable=self.status, style='Muted.TLabel').pack(side='left')
        self.next_button = ttk.Button(bottom, text='下一頁', command=lambda: self.change_page(1), state='disabled')
        self.next_button.pack(side='right')
        self.page_label = ttk.Label(bottom, text='第 1 / 1 頁')
        self.page_label.pack(side='right', padx=8)
        self.previous_button = ttk.Button(bottom, text='上一頁', command=lambda: self.change_page(-1), state='disabled')
        self.previous_button.pack(side='right')
        exports = ttk.Frame(outer)
        exports.grid(row=7, column=0, sticky='ew', pady=(10, 0))
        self.text_button = ttk.Button(exports, text='匯出篩選結果為文字…', command=self.save_text, state='disabled')
        self.text_button.pack(side='left')
        self.raw_button = ttk.Button(exports, text='匯出頻道原始輸入…', command=self.save_raw, state='disabled')
        self.raw_button.pack(side='left', padx=8)
        ttk.Label(exports, text='原始輸入匯出會包含所選頻道的完整 stdin。', style='Muted.TLabel').pack(side='left', padx=6)
        self.render_page()
        root.bind('<Control-o>', lambda e: self.choose_file())
        root.bind('<Control-f>', lambda e: query_box.focus_set())
        root.bind('<F5>', lambda e: self.refresh())
        self.poll_timer = root.after(80, self.poll_results)
        root.protocol('WM_DELETE_WINDOW', self.close)

    def close(self):
        self.closed = True
        self.load_token += 1
        for timer in (self.filter_timer, self.poll_timer):
            if timer:
                self.root.after_cancel(timer)
        self.root.destroy()

    @staticmethod
    def text_tab(notebook, label, font=('Consolas', 11)):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=label)
        text = tk.Text(frame, wrap='word', font=font, background='white',
                       foreground='#17283d', borderwidth=0, padx=12, pady=12, state='disabled')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        return text

    @staticmethod
    def set_text(widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def start_folder(self):
        candidates = [Path.cwd() / 'input-logs', Path(sys.executable).parent.parent.parent / 'input-logs']
        if self.folder:
            return self.folder
        if self.path:
            return self.path.parent
        return next((p for p in candidates if p.is_dir()), Path.cwd())

    def choose_file(self):
        path = filedialog.askopenfilename(parent=self.root, title='選取 SSH 輸入紀錄', initialdir=str(self.start_folder()),
                                           filetypes=[('InputLog1 紀錄', '*.jsonl'), ('所有檔案', '*.*')])
        if path:
            self.open_path(path)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self.root, title='選取 SSH 紀錄資料夾',
                                       initialdir=str(self.start_folder()), mustexist=True)
        if path:
            self.open_folder(path)

    def open_folder(self, path, preferred=None):
        try:
            folder = Path(path).resolve()
            entries = []
            for file in folder.iterdir():
                if file.suffix.casefold() == '.jsonl' and file.is_file():
                    try:
                        entries.append((file.stat().st_mtime_ns, file))
                    except FileNotFoundError:
                        continue
        except OSError as error:
            messagebox.showerror('無法開啟資料夾', str(error), parent=self.root)
            return
        self.folder = folder
        self.folder_files = [file for _, file in sorted(entries, reverse=True)]
        self.folder_box.configure(values=[file.name for file in self.folder_files])
        self.folder_count.configure(text=f'{len(self.folder_files):,} 個檔案')
        self.folder_bar.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        if self.folder_files:
            index = self.folder_files.index(preferred) if preferred in self.folder_files else 0
            self.folder_box.current(index)
            self.select_folder_record()
        else:
            self.folder_box.set('')
            self.path = None
            self.reset_recording()
            self.refresh_button.configure(state='normal')
            self.file_label.configure(text=str(folder))
            self.status.set('資料夾中沒有 .jsonl 紀錄檔。')

    def select_folder_record(self, event=None):
        index = self.folder_box.current()
        if 0 <= index < len(self.folder_files):
            self.open_path(self.folder_files[index], keep_folder=True)

    def reset_recording(self):
        self.load_token += 1
        self.model = None
        self.indices = []
        self.channel.set(ALL_CHANNELS)
        self.channel_box.configure(values=[ALL_CHANNELS])
        self.event.set('全部事件')
        self.query.set('')
        for value in self.metadata:
            value.set('—')
        self.refresh_button.configure(state='disabled')
        self.text_button.configure(state='disabled')
        self.raw_button.configure(state='disabled')
        self.render_page()

    def open_path(self, path, keep_folder=False):
        if not keep_folder:
            self.folder = None
            self.folder_files = []
            self.folder_bar.grid_remove()
        self.path = Path(path).resolve()
        self.reset_recording()
        token = self.load_token
        selected_path = self.path
        self.file_label.configure(text=str(self.path))
        self.status.set('正在讀取與驗證紀錄…')

        def load():
            try:
                model, error = Recording.load(selected_path), None
            except Exception as exc:
                model, error = None, exc
            self.results.put((token, model, error))
        threading.Thread(target=load, daemon=True).start()

    def poll_results(self):
        self.poll_timer = None
        if self.closed:
            return
        try:
            while True:
                token, model, error = self.results.get_nowait()
                if token != self.load_token:
                    continue
                self.refresh_button.configure(state='normal')
                if error is not None:
                    message = '沒有讀取權限。請以系統管理員開啟此工具，或讀取具有權限的紀錄副本。' if isinstance(error, PermissionError) else str(error)
                    self.status.set('無法開啟紀錄檔')
                    messagebox.showerror('無法開啟紀錄', message, parent=self.root)
                    continue
                self.model = model
                header = model.header
                for variable, value in zip(self.metadata, [header.get('user', ''),
                        f"{header.get('remote', '')}:{header.get('remote_port', '')}", header.get('time', '—')]):
                    variable.set(visible_text(value))
                self.channel_box.configure(values=[ALL_CHANNELS, *map(str, model.channels)])
                self.text_button.configure(state='normal')
                self.apply_filters()
        except queue.Empty:
            pass
        if not self.closed:
            self.poll_timer = self.root.after(80, self.poll_results)

    def selected_channel(self):
        return None if self.channel.get() == ALL_CHANNELS else int(self.channel.get())

    def schedule_filter(self, *args):
        if self.closed:
            return
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
        self.filter_timer = self.root.after(220, self.apply_filters)

    def clear_filters(self):
        self.channel.set(ALL_CHANNELS)
        self.event.set('全部事件')
        self.query.set('')
        self.apply_filters()

    def apply_filters(self):
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
            self.filter_timer = None
        if self.model is None:
            return
        channel = self.selected_channel()
        self.indices = self.model.filter(channel, EVENTS[self.event.get()], self.query.get())
        self.page = 0
        self.raw_button.configure(state='normal' if channel is not None else 'disabled')
        self.status.set(f'共 {len(self.model.events):,} 筆事件 · 篩選後 {len(self.indices):,} 筆 · 原始輸入 {self.model.stdin_bytes:,} 位元組')
        self.render_page()

    def change_page(self, direction):
        self.page += direction
        self.render_page()

    def render_page(self):
        children = self.table.get_children()
        if children:
            self.table.delete(*children)
        self.set_text(self.detail, '選取一筆事件以查看完整輸入內容。')
        self.set_text(self.decoded_detail, '')
        self.set_text(self.hex_detail, '')
        pages = max(1, (len(self.indices) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = max(0, min(self.page, pages - 1))
        self.page_label.configure(text=f'第 {self.page + 1} / {pages} 頁')
        self.previous_button.configure(state='normal' if self.page > 0 else 'disabled')
        self.next_button.configure(state='normal' if self.page + 1 < pages else 'disabled')
        if not self.indices or self.model is None:
            self.empty_label.configure(text='沒有符合篩選條件的事件' if self.model else '開啟 .jsonl 紀錄檔或資料夾')
            self.empty_label.place(relx=.5, rely=.5, anchor='center')
            return
        self.empty_label.place_forget()
        for number, i in enumerate(self.indices[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]):
            record = self.model.events[i]
            text = self.model.text[i]
            if i in self.model.decoded:
                text = '[Base64] ' + visible_text(self.model.decoded[i][:180])
            self.table.insert('', 'end', iid=str(i), values=(record.get('sequence', '—'),
                visible_text(record.get('time', '')), record.get('channel', '—'), record['event'],
                record.get('bytes', 0), text[:180] + ('…' if len(text) > 180 else '')),
                tags=('alternate',) if number % 2 else ())
        first = self.table.get_children()[0]
        self.table.selection_set(first)
        self.table.focus(first)
        self.show_selection()

    def show_selection(self, event=None):
        selected = self.table.selection()
        if not selected or self.model is None:
            return
        i = int(selected[0])
        record = self.model.events[i]
        self.set_text(self.detail, self.model.text[i] or '（此事件沒有輸入文字）')
        decoded = self.model.decoded.get(i)
        self.set_text(self.decoded_detail, decoded if decoded is not None else '（此事件未辨識到 PowerShell Base64 指令）')
        if decoded is not None and self.notebook.index('current') == 0:
            self.notebook.select(self.decoded_detail.master)
        data = self.model.by_sequence.get(record.get('sequence'), {}).get('data', b'')
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            ascii_text = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
            lines.append(f'{offset:08x}  {chunk.hex(" "):<47}  {ascii_text}')
        self.set_text(self.hex_detail, '\n'.join(lines) or '（沒有對應的原始位元組）')

    def refresh(self):
        if str(self.refresh_button['state']) == 'disabled':
            return
        if self.folder:
            self.open_folder(self.folder, preferred=self.path)
        elif self.path:
            self.open_path(self.path)

    def open_decoder(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('Base64 解碼')
        dialog.geometry('720x520')
        dialog.minsize(490, 360)
        dialog.transient(self.root)
        content = ttk.Frame(dialog, padding=14)
        content.pack(fill='both', expand=True)
        ttk.Label(content, text='貼上 Base64 或完整的 PowerShell -EncodedCommand 指令').pack(anchor='w')
        payload = tk.Text(content, height=7, wrap='char', font=(self.ui_font, 10))
        payload.pack(fill='both', expand=True, pady=(5, 8))
        toolbar = ttk.Frame(content)
        toolbar.pack(fill='x', pady=(0, 8))
        ttk.Label(toolbar, text='純 Base64 文字編碼').pack(side='left', padx=(0, 6))
        encoding = ttk.Combobox(toolbar, values=('UTF-8', 'UTF-16LE'), state='readonly', width=10)
        encoding.current(0)
        encoding.pack(side='left')
        output = tk.Text(content, height=9, wrap='word', font=(self.ui_font, 11), state='disabled')

        def decode():
            self.set_text(output, '')
            value = payload.get('1.0', 'end-1c')
            try:
                commands = powershell_commands(value)
                result = '\n\n'.join(commands) if commands else decode_base64(
                    value, 'utf-16-le' if encoding.current() == 1 else 'utf-8')
            except ValueError as error:
                messagebox.showerror('無法解碼', str(error), parent=dialog)
                return
            self.set_text(output, visible_text(result, multiline=True))

        ttk.Button(toolbar, text='解碼', command=decode).pack(side='left', padx=8)
        output.pack(fill='both', expand=True)
        payload.focus_set()

    def save_text(self):
        if self.model is None:
            return
        target = filedialog.asksaveasfilename(parent=self.root, title='匯出篩選結果（建立新檔）',
            initialfile=self.path.stem + '-readable.txt', defaultextension='.txt',
            filetypes=[('UTF-8 文字', '*.txt')], confirmoverwrite=False)
        if target:
            self.export(target, raw=False)

    def save_raw(self):
        if self.model is None or self.selected_channel() is None:
            return
        channel = self.selected_channel()
        target = filedialog.asksaveasfilename(parent=self.root, title=f'匯出頻道 {channel} 的完整 stdin（建立新檔）',
            initialfile=f'{self.path.stem}-channel-{channel}-stdin.bin', defaultextension='.bin',
            filetypes=[('原始位元組', '*.bin')], confirmoverwrite=False)
        if target:
            self.export(target, raw=True)

    def export(self, target, raw):
        try:
            if raw:
                self.model.export_stdin(target, self.selected_channel())
            else:
                self.model.export_text(target, self.indices)
        except FileExistsError:
            messagebox.showerror('檔案已存在', '為避免覆蓋資料，請選擇新的檔名。', parent=self.root)
            return False
        except (OSError, ValueError) as error:
            messagebox.showerror('匯出失敗', str(error), parent=self.root)
            return False
        self.status.set('已匯出：' + visible_text(target))
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', nargs='?', type=Path)
    parser.add_argument('--check-runtime', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check_runtime:
        try:
            tk.Tcl().eval('info patchlevel')
        except tk.TclError:
            raise SystemExit(1)
        return
    root = tk.Tk()
    root.withdraw()
    window = ReaderWindow(root)
    if args.log:
        if args.log.is_dir():
            window.open_folder(args.log)
        else:
            window.open_path(args.log)
    root.after(0, root.deiconify)
    root.mainloop()
