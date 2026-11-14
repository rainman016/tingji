"""File/options/progress presentation over the existing pipeline state."""
from pathlib import Path
import tkinter as tk
import customtkinter as ctk

from core import timestamp
from studio_icons import glyph
from local_service import ACTIVE_STATUSES
from ui_theme import BG, PAPER, INK, MUTED, ACCENT, LINE, SOFT, FONT, native_font


class ImportView(ctk.CTkScrollableFrame):
    def __init__(self, master, app, label, button, combo):
        super().__init__(master, fg_color=BG, corner_radius=0, height=200,
                         scrollbar_fg_color=BG, scrollbar_button_color='#D0DAE8',
                         scrollbar_button_hover_color='#A9B9CE')
        self.app = app
        self._advanced = False
        self.stage = 'upload'
        self.paused = False
        self.grid_columnconfigure(0, weight=1)
        self.stepper = tk.Canvas(self, height=52, bg=BG, highlightthickness=0)
        self.stepper.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
        self.stepper.bind('<Configure>', lambda _: self._draw_steps())
        self.upload = ctk.CTkFrame(self, fg_color=SOFT, corner_radius=16,
                                  border_color='#B5C9E9', border_width=1)
        self.upload.grid(row=1, column=0, sticky='ew')
        ctk.CTkLabel(self.upload,text='',image=glyph('import',48),height=56).pack(pady=(26,10))
        label(self.upload, '导入一段音视频', size=18).pack(pady=(0, 8))
        label(self.upload, '视频或音频 · 自动识别中文与英语', size=13, color=MUTED).pack(pady=(0, 18))
        button(self.upload, '选择本地文件', app.choose_file, primary=True, width=148, height=42).pack()
        label(self.upload, 'MP4 / MOV / MKV / MP3 / WAV 等格式 · 单个文件最长 4 小时',
              size=12, color=MUTED,wraplength=300,justify='center').pack(pady=(18, 26))

        app.source_panel = self.options = ctk.CTkFrame(self, fg_color='transparent')
        self.options.grid(row=1, column=0, sticky='ew')
        self.options.grid_columnconfigure(0, weight=1)
        file_row = ctk.CTkFrame(self.options, fg_color=PAPER, corner_radius=14,
                               border_width=1, border_color=LINE)
        file_row.grid(row=0, column=0, sticky='ew', pady=(0, 20))
        file_row.grid_columnconfigure(0, weight=1)
        app.file_display = tk.StringVar(value='尚未选择音视频文件')
        app.file_path.trace_add('write', lambda *_: app.file_display.set(Path(app.file_path.get()).name if app.file_path.get() else '尚未选择音视频文件'))
        app.file_entry = ctk.CTkEntry(file_row, textvariable=app.file_display, state='readonly',
            border_width=0, fg_color=PAPER, text_color=INK, font=(FONT, 14), width=1, height=34)
        app.file_entry.grid(row=0, column=0, sticky='ew', padx=16, pady=(12, 0))
        self.file_meta = label(file_row, '所选文件保留在本机', size=13, color=MUTED)
        self.file_meta.grid(row=1, column=0,columnspan=3, sticky='w', padx=22, pady=(0, 12))
        app.choose_button = button(file_row, '更换', app.choose_file, quiet=True, width=60)
        app.choose_button.grid(row=0, column=2, padx=(0, 14),pady=(10,0))
        app.source_relink_button = button(file_row, '关联原文件', app.relink_source, quiet=True, width=90)
        app.source_relink_button.grid(row=0, column=1,pady=(10,0))
        self.fields = ctk.CTkFrame(self.options, fg_color='transparent')
        self.fields.grid(row=1, column=0, sticky='ew')
        self.fields.grid_columnconfigure(0, weight=1)
        label(self.fields, '文稿名称', size=14).grid(row=0, column=0, sticky='w', pady=(0, 7))
        label(self.fields, '音频语言', size=14).grid(row=2, column=0, sticky='w', pady=(14, 7))
        app.import_name = tk.StringVar()
        app.import_name_entry = ctk.CTkEntry(self.fields, textvariable=app.import_name,
            height=42, corner_radius=10, border_width=1, border_color=LINE, fg_color=PAPER,
            text_color=INK, font=(FONT, 14))
        app.import_name_entry.grid(row=1, column=0, sticky='ew')
        app.language_box = combo(self.fields, app.language, ['自动检测', '英文', '中文'], 190)
        app.language_box.grid(row=3, column=0, sticky='ew')
        self.advanced_button = button(self.options, '更多选项  ▾', self.toggle_advanced, quiet=True, width=110)
        self.advanced_button.grid(row=4, column=0, sticky='w', pady=(10, 0))
        self.advanced = ctk.CTkFrame(self.options, fg_color='transparent')
        self.advanced.grid(row=5, column=0, sticky='ew', pady=(6, 0))
        label(self.advanced, '说话人数', size=13, color=MUTED).pack(side='left', padx=(0, 8))
        app.count_box = combo(self.advanced, app.speaker_count, ['自动'] + list(map(str, range(1, 11))), 86)
        app.count_box.pack(side='left', padx=(0, 20))
        self.advanced.grid_remove()
        self.language_note = label(self.options, variable=app.language_status, size=13, color=MUTED,
                                  wraplength=650, justify='left')
        # Directly under the language selector: this is where a required
        # zh/en choice is explained when detection was inconclusive.
        self.language_note.grid(row=3, column=0, sticky='w', pady=(10, 0))
        self.recovery_note = label(self.options, variable=app.recovery_status, size=13, color=ACCENT,
                                   wraplength=650, justify='left')
        self.recovery_note.grid(row=7, column=0, sticky='w', pady=(4, 0))
        self.actions = ctk.CTkFrame(self.options, fg_color='transparent')
        self.actions.grid(row=8, column=0, sticky='ew', pady=(14, 10))
        app.start_button = button(self.actions, '开始转写', app.start, primary=True, width=120, height=42)
        app.start_button.pack(side='right')
        app.resume_button = button(self.actions, '继续转写', app.resume_transcription, primary=True, width=120, height=42)
        self.back_button = button(self.actions, '返回文稿', app.close_import_flow, width=86, height=42)
        self.back_button.pack(side='right', padx=(0, 10))

        self.processing = ctk.CTkFrame(self, fg_color=PAPER, corner_radius=14, border_width=1, border_color=LINE)
        self.processing.grid(row=1, column=0, sticky='ew')
        self.process_title = label(self.processing, '正在准备音频', size=18, bold=True)
        self.process_title.pack(fill='x', padx=24, pady=(24, 10))
        self.process_detail = label(self.processing, '', size=14, color=MUTED, wraplength=650, justify='left')
        self.process_detail.pack(fill='x', padx=24, pady=(0, 18))
        self.rows = []
        for name in ('准备音频', '识别主要语言', '转写与说话人分组', '进入文稿工作区'):
            row = ctk.CTkFrame(self.processing, fg_color='transparent')
            row.pack(fill='x', padx=24, pady=8)
            label(row, name, size=14).pack(side='left')
            value = label(row, '等待', size=13, color=MUTED)
            value.pack(side='right')
            self.rows.append(value)
        label(self.processing, '停止后保留已完成部分，可以继续处理。', size=13, color=MUTED).pack(anchor='w', padx=24, pady=(20, 24))
        self.process_stop=button(self.processing,'停止并保留进度',app.stop,width=170)
        self.process_stop.pack(anchor='w',padx=24,pady=(0,24))
        self.guide = label(self, '自动判断主要语言，不确定时请你确认。完成后直接进入逐字稿工作区。',
            size=13, color=MUTED, wraplength=720, justify='left')
        self.guide.grid(row=2, column=0, columnspan=2, sticky='w', pady=(22, 4))
        self.notice=label(self,'',size=12,color='#923C34',wraplength=330,justify='left')
        self.notice.grid(row=3,column=0,sticky='ew',pady=(10,5))
        tk.Frame.bind(self, '<Configure>', self._resize, add='+')
        app.bind('<FocusIn>', self._focus_in, add='+')

    def _focus_in(self, event):
        widget=event.widget
        ancestor=widget
        while ancestor is not None and ancestor is not self:
            ancestor=getattr(ancestor,'master',None)
        if ancestor is self:
            self.after_idle(lambda: self.reveal_widget(widget))

    def reveal_widget(self, widget):
        if not self.winfo_ismapped() or not widget.winfo_exists():
            return
        canvas=self._parent_canvas
        top=canvas.winfo_rooty()
        delta=min(0,widget.winfo_rooty()-top-4)
        if not delta:
            delta=max(0,widget.winfo_rooty()+widget.winfo_height()-top-canvas.winfo_height()+4)
        if delta:
            canvas.yview_moveto((canvas.canvasy(0)+delta)/max(1,self.winfo_height()))

    def toggle_advanced(self):
        self._advanced = not self._advanced
        self.advanced.grid() if self._advanced else self.advanced.grid_remove()
        self.advanced_button.configure(text='收起更多选项  ▴' if self._advanced else '更多选项  ▾')

    def _resize(self, event):
        if event.widget == self:
            self._layout_help()
            width = max(280, self._reverse_widget_scaling(self.options.winfo_width())-20)
            for widget in (self.process_detail, self.language_note, self.recovery_note, self.guide):
                widget.configure(wraplength=width)

    def _layout_help(self):
        self.grid_columnconfigure(1,minsize=0)

    def _draw_steps(self):
        canvas = self.stepper
        canvas.delete('all')
        scale = self._get_widget_scaling()
        canvas.configure(height=round(52*scale))
        width = canvas.winfo_width()
        xs = [width*(i+.5)/4 for i in range(4)]
        current = {'upload': 0, 'confirm': 1, 'processing': 2, 'work': 3}[self.stage]
        if self.paused:
            current = 2  # A stopped task is still at "生成逐字稿", not back at options.
        y, r = 14*scale, 11*scale
        canvas.create_line(xs[0], y, xs[-1], y, fill=LINE, width=3*scale)
        if current:
            canvas.create_line(xs[0], y, xs[current], y, fill=ACCENT, width=3*scale)
        for i, title in enumerate(('选择文件', '确认选项', '生成逐字稿', '阅读校对')):
            canvas.create_oval(xs[i]-r, y-r, xs[i]+r, y+r, fill=ACCENT if i<=current else BG,
                               outline=ACCENT if i<=current else LINE, width=2*scale)
            canvas.create_text(xs[i], y, text='✓' if i<current else str(i+1),
                fill=PAPER if i<=current else MUTED, font=native_font(self, 14))
            canvas.create_text(xs[i], y+26*scale, text=title, fill=ACCENT if i<=current else MUTED,
                               font=native_font(self, 11))

    def sync(self):
        app,doc=self.app,self.app.doc or {}
        has_text=bool(doc.get('segments'))
        running=(getattr(app,'_ui_busy',False) or app.busy) and doc.get('id')==app._job_document_id
        external=not running and doc.get('status') in ACTIVE_STATUSES
        previous=self.stage
        self.stage='work' if has_text else 'processing' if running or external else 'confirm' if doc else 'upload'
        if previous!=self.stage:
            self._parent_canvas.yview_moveto(0)
            self.notice.configure(text='')
        for widget in (self.upload,self.options,self.processing):widget.grid_remove()
        (self.options if has_text or self.stage=='confirm' else getattr(self,self.stage)).grid()
        self.stepper.grid_remove() if has_text else self.stepper.grid()
        status=doc.get('status')
        self.paused=self.stage=='confirm' and status in ('已停止','已中断','等待确认语言')
        app.set_page_title(doc.get('title') or '逐字稿工作台')
        app.page_subtitle.set(app.meta.get() if has_text else '导入音视频，搜索、回听、校对和导出。')
        app.import_drawer.title.configure(text='原文件与转写选项' if has_text else '正在转写' if self.stage=='processing' else '导入音视频')
        if self.paused and status!='等待确认语言':
            app.import_drawer.title.configure(text='已停止 · 可继续' if app._recovery.get('available') else '转写已停止')
        if status=='等待确认语言':app.import_drawer.title.configure(text='请确认音频语言')
        show=app._settings_open if has_text else app._import_requested
        if has_text and previous=='processing':
            app._settings_open=app._import_requested=False;show=False
        app.import_drawer.show() if show else app.import_drawer.hide()
        app.import_name_entry.configure(state='disabled' if getattr(app,'_ui_busy',False) or app.document_locked or has_text else 'normal')
        app.source_relink_button.grid() if doc and (doc.get('task_id') or doc.get('transcription_settings') or has_text) else app.source_relink_button.grid_remove()
        self.file_meta.configure(text=(timestamp(doc['duration_ms'])+' · ' if doc.get('duration_ms') else '')+'所选文件保留在本机')
        self.recovery_note.grid() if app.recovery_status.get() else self.recovery_note.grid_remove()
        self.process_title.configure(text='正在安全停止' if running and app.cancel.is_set() else doc.get('status','正在准备音频'))
        self.process_detail.configure(text='该任务由其他窗口处理，可以先浏览已完成的文稿。' if external else getattr(app,'_progress_message','') or '正在准备本地处理…')
        self.process_stop.configure(state='normal' if running and not app.cancel.is_set() else 'disabled')
        step={'校验恢复进度':0,'提取音频':0,'检测语言':1,'本地识别中':2}.get(doc.get('status'),0)
        for i,row in enumerate(self.rows):
            text='已完成' if i<step else '处理中' if i==step else '等待'
            if i==1 and doc.get('language'):text=('已识别 · ' if doc.get('language_mode')=='auto' else '已指定 · ')+{'en':'英语','zh':'中文'}.get(doc['language'],doc['language'])
            row.configure(text=text,text_color=ACCENT if i==step else MUTED)
        self._draw_steps()

    def set_notice(self,text):
        self.notice.configure(text=text)
        self.reveal_widget(self.notice)
