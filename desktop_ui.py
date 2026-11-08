"""Visual layer for the local transcription workbench."""
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import customtkinter as ctk

from transcript_reader import TranscriptReader
from workspace_view import WorkspaceView

from ui_theme import BG, PAPER, INK, MUTED, ACCENT, HOVER, LINE, SOFT, SIDE, FONT


def label(parent, text=None, *, variable=None, size=13, color=INK, bold=False, **kwargs):
    return ctk.CTkLabel(parent, text=text or "", textvariable=variable,
                        font=(FONT, size, "bold" if bold else "normal"),
                        text_color=color, anchor="w", **kwargs)


def button(parent, text, command, *, primary=False, quiet=False, width=100, height=36):
    border = 0 if primary or quiet else 1
    widget = ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                          corner_radius=8, border_width=border, border_color=LINE,
                          fg_color=ACCENT if primary else ("transparent" if quiet else PAPER),
                          hover_color=HOVER if primary else "#E2EBE5",
                          text_color=PAPER if primary else INK,
                          text_color_disabled="#9BA79F", font=(FONT, 13))
    # CTk buttons are frames; give keyboard users the same visible focus affordance.
    widget.tk.call(widget._w, "configure", "-takefocus", 1)
    widget.bind("<Return>", lambda _event: widget.invoke())
    widget.bind("<space>", lambda _event: widget.invoke())
    widget.bind("<FocusIn>", lambda _event: widget.configure(border_width=2, border_color="#7CA38D"))
    widget.bind("<FocusOut>", lambda _event: widget.configure(border_width=border, border_color=LINE))
    return widget


def combo(parent, variable, values, width):
    return ctk.CTkComboBox(parent, variable=variable, values=values, width=width, height=32,
                           state="readonly", corner_radius=7, border_width=1,
                           border_color=LINE, fg_color=PAPER, button_color=SOFT,
                           button_hover_color="#DCE8E0", text_color=INK,
                           text_color_disabled=MUTED, dropdown_fg_color=PAPER,
                           dropdown_text_color=INK, dropdown_hover_color=SOFT,
                           font=(FONT, 12), dropdown_font=(FONT, 12))


def configure_theme(app):
    ctk.set_appearance_mode("light")
    ctk.ThemeManager.theme["CTkFont"].update(family=FONT, size=13, weight="normal")
    app.option_add("*Font", (FONT, 10))
    app.option_add("*TCombobox*Listbox.font", (FONT, 10))
    style = ttk.Style(app)
    style.theme_use("clam")
    style.configure("Quiet.Horizontal.TProgressbar", troughcolor=LINE, background=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, bordercolor=LINE, borderwidth=0,
                    thickness=4)


def build_ui(app):
    app.grid_columnconfigure(1, weight=1)
    app.grid_rowconfigure(0, weight=1)
    sidebar = ctk.CTkFrame(app, width=216, corner_radius=0, fg_color=SIDE)
    sidebar.grid(row=0, column=0, sticky="nsw")
    sidebar.grid_propagate(False)
    sidebar.grid_columnconfigure(0, weight=1)
    sidebar.grid_rowconfigure(3, weight=1)

    brand = ctk.CTkFrame(sidebar, fg_color="transparent")
    brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))
    mark = ctk.CTkFrame(brand, width=34, height=34, corner_radius=9, fg_color=ACCENT)
    mark.pack(side="left", padx=(0, 10))
    mark.pack_propagate(False)
    wave = tk.Canvas(mark, width=28, height=28, bg=ACCENT, highlightthickness=0)
    wave.place(relx=.5, rely=.5, anchor="center")
    for x, height in zip((5, 11, 17, 23), (9, 20, 15, 7)):
        wave.create_line(x, 14-height/2, x, 14+height/2, fill=PAPER, width=3, capstyle="round")
    label(brand, "听记", size=23, bold=True).pack(side="left")
    app.new_button = button(sidebar, "新建转写", app.choose_file, primary=True, height=36)
    app.new_button.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 16))

    library_head = ctk.CTkFrame(sidebar, fg_color="transparent")
    library_head.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
    label(library_head, "我的文稿", size=12, color=MUTED, bold=True).pack(anchor="w", padx=3, pady=(0, 8))
    app.history_search_entry = ctk.CTkEntry(library_head, textvariable=app.history_query,
                                          placeholder_text="搜索文稿", height=32,
                                          border_width=1, border_color=LINE, fg_color=BG,
                                          text_color=INK, font=(FONT, 12))
    app.history_search_entry.pack(fill="x")
    history_area = ctk.CTkFrame(sidebar, fg_color="transparent")
    history_area.grid(row=3, column=0, sticky="nsew", padx=14)
    app.history = tk.Listbox(history_area, bg=SIDE, fg=INK, selectbackground="#DDE9E1",
                             selectforeground=ACCENT, borderwidth=0, highlightthickness=1,
                             highlightbackground=SIDE, highlightcolor="#7CA38D", activestyle="dotbox",
                             exportselection=False, font=(FONT, 10), relief="flat")
    history_scroll = ctk.CTkScrollbar(history_area, command=app.history.yview, width=10,
                                     fg_color=SIDE, button_color="#D2DDD5", button_hover_color="#AFBFB3")
    app.history.configure(yscrollcommand=history_scroll.set)
    history_scroll.pack(side="right", fill="y")
    app.history.pack(side="left", fill="both", expand=True)
    app.history.bind("<<ListboxSelect>>", app.open_history)
    app.history_empty = label(history_area, "还没有保存的文稿", size=12, color=MUTED)
    app.history_empty.place(x=8, y=8)

    utility = ctk.CTkFrame(sidebar, fg_color="transparent")
    utility.grid(row=4, column=0, sticky="ew", padx=14, pady=(12, 16))
    app.example_button = button(utility, "演示文稿", app.open_example, quiet=True, height=30)
    app.example_button.pack(fill="x")
    button(utility, "打开文稿文件夹", app.open_results, quiet=True, height=30).pack(fill="x", pady=(0, 10))
    ctk.CTkFrame(utility, fg_color=LINE, height=1).pack(fill="x", padx=4, pady=(0, 10))
    label(utility, "●  本地处理", size=11, color=ACCENT, bold=True).pack(anchor="w", padx=6)
    label(utility, variable=app.models_label, size=10, color=MUTED, justify="left").pack(anchor="w", padx=6, pady=(3, 6))
    app.device_box = combo(utility, app.device, ["自动（优先显卡）", "仅 CPU"], 174)
    app.device_box.pack(fill="x", padx=2, pady=(2, 6))
    links = ctk.CTkFrame(utility, fg_color="transparent")
    links.pack(fill="x")
    button(links, "检查模型", app.check_models, quiet=True, width=86, height=28).pack(side="left")
    button(links, "使用说明", app.show_help, quiet=True, width=86, height=28).pack(side="right")

    main = ctk.CTkFrame(app, fg_color="transparent")
    main.grid(row=0, column=1, sticky="nsew", padx=20, pady=(20, 12))
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(4, weight=1)
    heading = ctk.CTkFrame(main, fg_color="transparent")
    heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    heading.grid_columnconfigure(0, weight=1)
    app.heading_label = label(heading, variable=app.title_text, size=22, bold=True, wraplength=650, justify="left")
    app.heading_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
    heading_actions = ctk.CTkFrame(heading, fg_color="transparent")
    heading_actions.grid(row=0, column=1, sticky="ne")
    app.rename_document_button = button(heading_actions, "重命名", app.rename_document, quiet=True, width=68, height=32)
    app.rename_document_button.pack(side="left", padx=(0, 4))
    app.rename_document_button.configure(state="disabled")
    app.settings_button = button(heading_actions, "转写设置", app.toggle_settings, width=86, height=32)
    app.settings_button.pack(side="left")
    label(heading, variable=app.meta, size=11, color=MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
    heading.bind("<Configure>", lambda e: app.heading_label.configure(wraplength=max(240, e.width-180)))

    app.source_panel = source = ctk.CTkFrame(main, fg_color=PAPER, corner_radius=10, border_width=1, border_color=LINE)
    source.grid(row=1, column=0, sticky="ew", pady=(0, 10))
    source.grid_columnconfigure(0, weight=1)
    file_row = ctk.CTkFrame(source, fg_color="transparent")
    file_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 6))
    file_row.grid_columnconfigure(0, weight=1)
    app.file_display = tk.StringVar(value="尚未选择音视频文件")
    app.file_path.trace_add("write", lambda *_: app.file_display.set(app.file_path.get() or "尚未选择音视频文件"))
    app.file_entry = ctk.CTkEntry(file_row, textvariable=app.file_display, state="readonly",
                                 fg_color=PAPER, border_width=0, text_color=INK,
                                 font=(FONT, 12), height=30, corner_radius=0, width=1)
    app.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 12))
    app.choose_button = button(file_row, "选择文件", app.choose_file, width=96, height=32)
    app.choose_button.grid(row=0, column=2)
    app.source_relink_button = button(file_row, "关联原文件", app.relink_source, quiet=True, width=92, height=32)
    app.source_relink_button.grid(row=0, column=1, padx=(0, 8))
    opts = ctk.CTkFrame(source, fg_color="transparent")
    opts.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 5))
    label(opts, "原音语言", size=12, color=MUTED).pack(side="left", padx=(0, 8))
    app.language_box = combo(opts, app.language, ["自动检测", "中文", "英文"], 96)
    app.language_box.pack(side="left", padx=(0, 14))
    label(opts, "说话人数", size=12, color=MUTED).pack(side="left", padx=(0, 8))
    app.count_box = combo(opts, app.speaker_count, ["自动"] + list(map(str, range(1, 11))), 76)
    app.count_box.pack(side="left", padx=(0, 14))
    app.summary_check = ctk.CTkSwitch(opts, text="生成摘要", variable=app.auto_summary,
                                     font=(FONT, 12), text_color=INK, progress_color=ACCENT,
                                     fg_color=LINE, button_color=PAPER, button_hover_color=SOFT,
                                     switch_width=30, switch_height=18, width=98)
    app.summary_check.pack(side="left", padx=(0, 12))
    app.style_box = combo(opts, app.summary_style, ["通用摘要", "会议纪要"], 108)
    app.style_box.pack(side="left")
    start_row = ctk.CTkFrame(source, fg_color="transparent")
    start_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(5, 10))
    app.start_button = button(start_row, "开始转写", app.start, primary=True, width=112, height=34)
    app.start_button.pack(side="right")
    app.resume_button = button(start_row, "继续转写", app.resume_transcription, primary=True, width=100, height=34)
    label(start_row, "摘要默认中文 · 最长 4 小时", size=11, color=MUTED,
          wraplength=290, justify="left").pack(side="left", padx=(0, 10))
    label(source, variable=app.language_status, size=11, color=MUTED,
          wraplength=620, justify="left").grid(row=3, column=0, sticky="w", padx=16, pady=(0, 5))
    label(source, variable=app.recovery_status, size=11, color=ACCENT,
          wraplength=620, justify="left").grid(row=4, column=0, sticky="w", padx=16, pady=(0, 8))

    app.task_bar = ctk.CTkFrame(main, fg_color=SOFT, corner_radius=9)
    app.task_bar.grid(row=2, column=0, sticky="ew", pady=(0, 10))
    task_label = label(app.task_bar, variable=app.task_status, size=11, color=ACCENT,
                       wraplength=750, justify="left")
    task_label.pack(fill="x", padx=12, pady=(8, 2))
    task_actions = ctk.CTkFrame(app.task_bar, fg_color="transparent")
    task_actions.pack(fill="x", padx=10, pady=(0, 6))
    app.return_task_button = button(task_actions, "返回任务文稿", app.return_to_task, quiet=True, width=106, height=28)
    app.return_task_button.pack(side="left")
    app.stop_button = button(task_actions, "停止计算", app.stop, quiet=True, width=80, height=28)
    app.stop_button.pack(side="left", padx=(6, 0))
    app.stop_button.configure(state="disabled")
    app.progress = ttk.Progressbar(task_actions, mode="indeterminate", length=80,
                                   style="Quiet.Horizontal.TProgressbar")
    app.task_bar.bind("<Configure>", lambda e: task_label.configure(wraplength=max(260, e.width-24)))
    app.task_bar.grid_remove()

    app.error_panel = ctk.CTkFrame(main, fg_color="#FFF0EF", border_color="#E8BAB5", border_width=1, corner_radius=9)
    app.error_panel.grid(row=3, column=0, sticky="ew", pady=(0, 10))
    label(app.error_panel, "需要处理", size=12, color="#9D352D", bold=True).pack(anchor="w", padx=14, pady=(8, 0))
    error_detail = label(app.error_panel, variable=app.notice_text, size=11, color="#923C34", justify="left", wraplength=800)
    error_detail.pack(fill="x", padx=14, pady=(2, 8))
    app.reload_button = button(app.error_panel, "重新载入已保存版本", app.reload_current_document, width=160, height=30)
    app.reload_button.pack(anchor="w", padx=14, pady=(0, 10))
    app.reload_button.pack_forget()
    app.error_panel.bind("<Configure>", lambda e: error_detail.configure(wraplength=max(260, e.width-32)))
    app.error_panel.grid_remove()

    app.tabs = WorkspaceView(main)
    app.tabs.grid(row=4, column=0, sticky="nsew")
    transcript = app.tabs.add("文字稿")
    summary = app.tabs.add("摘要与要点")
    _build_reader(app, transcript)
    _build_summary(app, summary)

    footer = ctk.CTkFrame(main, fg_color="transparent")
    footer.grid(row=5, column=0, sticky="ew", pady=(10, 6))
    label(footer, "导出", size=11, color=MUTED).pack(side="left", padx=(0, 10))
    app.export_buttons = []
    for title, kind, width in [("TXT", "txt", 54), ("SRT 字幕", "srt", 84), ("Markdown", "md", 94), ("JSON", "json", 60)]:
        item = button(footer, title, lambda k=kind: app.export(k), width=width, height=30)
        item.pack(side="left", padx=(0, 6))
        item.configure(state="disabled")
        app.export_buttons.append(item)
    label(footer, variable=app.save_status, size=10, color=MUTED,
          width=150, wraplength=150, justify="right").pack(side="right")
    app.status_label = label(main, variable=app.status, size=10, color=MUTED,
                              wraplength=900, justify="left")
    app.status_label.grid(row=6, column=0, sticky="ew")
    main.bind("<Configure>", lambda e: app.status_label.configure(wraplength=max(300, e.width)), add="+")


def _build_reader(app, transcript):
    search_row = ctk.CTkFrame(transcript, fg_color="transparent")
    search_row.pack(fill="x", padx=12, pady=(12, 8))
    search_row.grid_columnconfigure(1, weight=1)
    label(search_row, "查找", size=11, color=MUTED).grid(row=0, column=0, padx=(0, 8))
    app.search_entry = ctk.CTkEntry(search_row, textvariable=app.search_query,
                                   placeholder_text="搜索文字  Ctrl+F", height=32, width=120,
                                   fg_color=BG, border_color=LINE, text_color=INK, font=(FONT, 12))
    app.search_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
    label(search_row, variable=app.search_result, size=10, color=MUTED, width=56).grid(row=0, column=2, padx=(0, 6))
    app.search_prev_button = button(search_row, "上个", app.search_previous, quiet=True, width=44, height=30)
    app.search_prev_button.grid(row=0, column=3, padx=(0, 2))
    app.search_next_button = button(search_row, "下个", app.search_next, quiet=True, width=44, height=30)
    app.search_next_button.grid(row=0, column=4)
    app.search_prev_button.configure(state="disabled")
    app.search_next_button.configure(state="disabled")

    audio = ctk.CTkFrame(transcript, fg_color=SOFT, corner_radius=8)
    audio.pack(fill="x", padx=12, pady=(0, 8))
    audio_actions = ctk.CTkFrame(audio, fg_color="transparent")
    audio_actions.pack(fill="x", padx=8, pady=(6, 0))
    app.play_button = button(audio_actions, "回听本段", app.toggle_audio, primary=True, width=84, height=30)
    app.play_button.pack(side="left", padx=(0, 6))
    app.replay_button = button(audio_actions, "重播", app.play_current_segment, quiet=True, width=54, height=30)
    app.replay_button.pack(side="left", padx=(0, 4))
    app.audio_stop_button = button(audio_actions, "停止回听", app.stop_audio, quiet=True, width=76, height=30)
    app.audio_stop_button.pack(side="left")
    app.relink_button = button(audio_actions, "重新关联", app.relink_source, quiet=True, width=76, height=30)
    app.relink_button.pack(side="right")
    for item in (app.play_button, app.replay_button, app.audio_stop_button, app.relink_button):
        item.configure(state="disabled")
    audio_hint = label(audio, variable=app.audio_hint, size=10, color=MUTED,
                       wraplength=480, justify="left")
    audio_hint.pack(fill="x", padx=10, pady=(1, 6))
    audio.bind("<Configure>", lambda e: audio_hint.configure(wraplength=max(200, e.width-20)))

    editor_area = ctk.CTkFrame(transcript, fg_color="#F5F8F6", corner_radius=8)
    editor_area.pack(side="bottom", fill="x", padx=12, pady=(8, 12))
    edit_heading = ctk.CTkFrame(editor_area, fg_color="transparent")
    edit_heading.pack(fill="x", padx=10, pady=5)
    edit_heading.grid_columnconfigure(0, weight=1)
    app.selection_hint = tk.StringVar(value="选择段落，校对文字")
    hint = label(edit_heading, variable=app.selection_hint, size=10, color=MUTED,
                  width=1, wraplength=0, justify="left")
    hint.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    app.previous_segment_button = button(edit_heading, "上一段", app.previous_segment, quiet=True, width=58, height=26)
    app.previous_segment_button.grid(row=0, column=1, padx=(0, 2))
    app.next_segment_button = button(edit_heading, "下一段", app.next_segment, quiet=True, width=58, height=26)
    app.next_segment_button.grid(row=0, column=2, padx=(0, 6))
    app.editor_toggle_button = button(edit_heading, "校对本段", app.toggle_editor, width=80, height=28)
    app.editor_toggle_button.grid(row=0, column=3)
    for item in (app.previous_segment_button, app.next_segment_button, app.editor_toggle_button):
        item.configure(state="disabled")
    app.editor_body = ctk.CTkFrame(editor_area, fg_color="transparent")
    app.editor_body.pack(fill="x")
    speaker_row = ctk.CTkFrame(app.editor_body, fg_color="transparent")
    speaker_row.pack(fill="x", padx=10, pady=(3, 4))
    label(speaker_row, "本段归属", size=10, color=MUTED).pack(side="left", padx=(0, 8))
    app.segment_speaker = tk.StringVar(value="未选择")
    app.speaker_box = combo(speaker_row, app.segment_speaker, ["未选择"], 138)
    app.speaker_box.configure(command=app.assign_speaker, state="disabled")
    app.speaker_box.pack(side="left", padx=(0, 4))
    app.rename_button = button(speaker_row, "整组改名", app.rename_speaker, quiet=True, width=76, height=28)
    app.rename_button.pack(side="left", padx=(0, 2))
    app.rename_button.configure(state="disabled")
    app.merge_button = button(speaker_row, "合并…", app.show_merge_menu, quiet=True, width=64, height=28)
    app.merge_button.pack(side="left")
    app.merge_button.configure(state="disabled")
    app.segment_editor = ScrolledText(app.editor_body, height=3, width=1, wrap="word", undo=True,
                                       borderwidth=0, highlightthickness=0, relief="flat",
                                       bg="#F5F8F6", fg=INK, insertbackground=ACCENT,
                                       padx=4, pady=5, font=(FONT, 11), spacing3=4,
                                       selectbackground="#D8E9DD", selectforeground=INK)
    app.segment_editor.pack(fill="x", padx=8, pady=(0, 6))
    app.segment_editor.bind("<<Modified>>", app.segment_changed)
    app.segment_editor.configure(state="disabled")
    modern_scrollbar(app.segment_editor, "#F5F8F6")
    app.reader = TranscriptReader(transcript, on_select=app.select_segment,
                                   on_play=app.play_segment, on_search_change=app.search_results_changed,
                                   background=PAPER, foreground=INK, muted_color=MUTED,
                                   accent_color=ACCENT, font=(FONT, 12), metadata_font=(FONT, 10))
    app.reader.pack(fill="both", expand=True, padx=12)

    app.empty_state = ctk.CTkFrame(transcript, fg_color=PAPER, corner_radius=8)
    app.empty_state.place(relx=0, rely=0, relwidth=1, relheight=1)
    empty_content = ctk.CTkFrame(app.empty_state, fg_color="transparent")
    empty_content.place(relx=.5, rely=.45, anchor="center")
    empty_wave = tk.Canvas(empty_content, width=120, height=48, bg=PAPER, highlightthickness=0)
    empty_wave.pack(pady=(0, 12))
    for x, height in zip(range(24, 104, 12), (12, 24, 37, 20, 43, 28, 14)):
        empty_wave.create_line(x, 24-height/2, x, 24+height/2, fill="#B3CDBC", width=5, capstyle="round")
    app.empty_title = label(empty_content, "从一段音视频开始", size=19, bold=True)
    app.empty_title.pack(anchor="center")
    app.empty_caption = label(empty_content, "选择文件后，在这里阅读和校对文字稿。", size=12,
                               color=MUTED, wraplength=320, justify="center")
    app.empty_caption.pack(anchor="center", pady=(7, 0))


def _build_summary(app, summary):
    summary_head = ctk.CTkFrame(summary, fg_color="transparent")
    summary_head.pack(fill="x", padx=16, pady=(12, 4))
    label(summary_head, "摘要与要点", size=18, bold=True).pack(anchor="w")
    state_label = label(summary_head, variable=app.summary_state, size=10, color=MUTED,
                        wraplength=310, justify="left")
    state_label.pack(fill="x", pady=(3, 0))
    summary_head.bind("<Configure>", lambda e: state_label.configure(wraplength=max(180, e.width)))
    label(summary_head, "可直接编辑 · Ctrl + 点击引用核对原文", size=11, color=MUTED).pack(anchor="w", pady=(6, 4))
    app.restart_summary_button = button(summary_head, "放弃续算，重新生成", app.restart_summary,
                                        quiet=True, width=160, height=28)
    summary_actions = ctk.CTkFrame(summary, fg_color="transparent")
    summary_actions.pack(side="bottom", fill="x", padx=12, pady=(6, 12))
    app.restore_summary_button = button(summary_actions, "恢复上一版", app.restore_summary, quiet=True, width=94, height=32)
    app.restore_summary_button.pack(side="left")
    app.restore_summary_button.configure(state="disabled")
    app.summary_button = button(summary_actions, "生成摘要", app.summarize, width=124, height=32)
    app.summary_button.pack(side="right")
    app.summary_button.configure(state="disabled")
    app.summary_editor = ScrolledText(summary, wrap="char", width=1, height=1, undo=True,
                                       borderwidth=0, highlightthickness=0, bg=PAPER, fg=INK,
                                       insertbackground=ACCENT, padx=10, pady=8, spacing3=9,
                                       font=(FONT, 11), selectbackground="#D8E9DD", selectforeground=INK)
    app.summary_editor.pack(fill="both", expand=True, padx=8, pady=(0, 2))
    app.summary_editor.bind("<<Modified>>", app.summary_changed)
    app.summary_editor.configure(state="disabled")
    modern_scrollbar(app.summary_editor, PAPER)
    app.summary_empty = label(app.summary_editor.frame, '完成转写后，在这里整理摘要。',
                               size=13, color=MUTED, wraplength=230, justify='center')
    app.summary_empty.place(relx=.5, rely=.35, anchor='center')


def modern_scrollbar(editor, background):
    editor.vbar.pack_forget()
    editor.frame.configure(bg=background)
    scroll = ctk.CTkScrollbar(editor.frame, command=editor.yview, width=10, height=16,
                              fg_color=background, button_color="#D2DDD5",
                              button_hover_color="#AFBFB3")
    scroll.pack(side="right", fill="y", before=editor._w)
    editor.configure(yscrollcommand=scroll.set)


def sync_document_ui(app):
    has_text = bool(app.doc and app.doc["segments"])
    if has_text:
        app.empty_state.place_forget()
    else:
        app.empty_state.place(relx=0, rely=0, relwidth=1, relheight=1)
        app.empty_title.configure(text="可继续上次转写" if app._recovery.get("available") else "文件已准备好" if app.doc else "从一段音视频开始")
        app.empty_caption.configure(text="点击「继续转写」恢复进度，或开始新的转写。" if app.doc and app._recovery.get("available") else "默认自动检测原音语言，点击「开始转写」。" if app.doc
                                    else "选择文件后，在这里阅读和校对文字稿。")
    job = (app.doc or {}).get('summary_job', {})
    continuing = bool(job and not job.get('complete'))
    app.summary_button.configure(text="继续摘要" if continuing else "重新生成摘要" if app.doc and app.doc.get("summary") else "生成摘要")
    if continuing and not app.document_locked:
        app.restart_summary_button.pack(anchor='w', pady=(2, 0))
    else:
        app.restart_summary_button.pack_forget()
