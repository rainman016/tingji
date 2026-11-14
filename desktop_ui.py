"""Approved 14px transcript studio, built on the existing native desktop stack."""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

from transcript_reader import TranscriptReader
from ui_controls import RoundedComboBox, RoundedMenu
from import_view import ImportView
from studio_widgets import DocumentLibrary, SideDrawer, TranscriptWorkspace
from studio_icons import glyph
from ui_theme import (BG,PAPER,INK,MUTED,ACCENT,HOVER,LINE,SOFT,SIDE,SIDE_TEXT,
                      SIDE_MUTED,DOCK,FONT,UI_BODY,UI_CAPTION,native_font)


def label(parent,text=None,*,variable=None,size=12,color=INK,bold=False,**kwargs):
    return ctk.CTkLabel(parent,text=text or '',textvariable=variable,
        font=(FONT,max(size,UI_CAPTION),'bold' if bold else 'normal'),text_color=color,anchor='w',**kwargs)


def button(parent,text,command,*,primary=False,quiet=False,width=100,height=36,icon=None,dark=False,anchor='center'):
    border=0 if primary or quiet else 1
    background=ACCENT if primary else ('transparent' if quiet else PAPER)
    foreground=PAPER if primary else SIDE_TEXT if dark else INK
    widget=ctk.CTkButton(parent,text=text,command=command,width=width,height=max(height,34),
        corner_radius=10,border_width=border,border_color='#35404E' if dark else LINE,
        fg_color=background,hover_color=HOVER if primary else '#354351' if dark else '#EAF0F3',
        text_color=foreground,text_color_disabled='#8C9CAA',font=(FONT,13),
        image=glyph(icon,20,dark=dark,primary=primary) if icon else None,compound='left',anchor=anchor)
    widget.tk.call(widget._w,'configure','-takefocus',1)
    widget.focus_set=lambda:widget.tk.call('focus',widget._w)
    widget.bind('<Return>',lambda _:widget.invoke())
    widget.bind('<space>',lambda _:widget.invoke())
    widget.bind('<FocusIn>',lambda _:widget.configure(border_width=2,border_color='#83B9C1'))
    widget.bind('<FocusOut>',lambda _:widget.configure(border_width=border,border_color='#35404E' if dark else LINE))
    return widget


def combo(parent,variable,values,width):
    return RoundedComboBox(parent,variable=variable,values=values,width=width,height=38,
        state='readonly',corner_radius=11,border_width=1,border_color=LINE,
        fg_color=PAPER,button_color=PAPER,button_hover_color=SOFT,text_color=INK,
        text_color_disabled=MUTED,dropdown_fg_color=PAPER,dropdown_text_color=INK,
        dropdown_hover_color=SOFT,font=(FONT,13),dropdown_font=(FONT,13))


def placeholder(app,entry,variable,text,*,background,color,size=13):
    """CTkEntry drops its placeholder once a textvariable is bound; draw one."""
    hint=tk.Label(entry,text=text,bg=background,fg=color,bd=0,padx=0,pady=0,
        font=native_font(app,size),cursor='xterm')
    state={'focused':False}
    def sync(*_):
        if variable.get() or state['focused']:hint.place_forget()
        else:hint.place(x=round(12*ctk.ScalingTracker.get_widget_scaling(app)),rely=.5,anchor='w')
    def focus(value):
        state['focused']=value;sync()
    variable.trace_add('write',sync)
    entry._entry.bind('<FocusIn>',lambda _:focus(True),add='+')
    entry._entry.bind('<FocusOut>',lambda _:focus(False),add='+')
    hint.bind('<Button-1>',lambda _:entry.focus_set())
    app._placeholders=getattr(app,'_placeholders',[])+[(hint,size)]
    sync()
    return hint


def configure_theme(app):
    ctk.set_appearance_mode('light')
    ctk.ThemeManager.theme['CTkFont'].update(family=FONT,size=13,weight='normal')
    ctk.ThemeManager.theme['CTkEntry']['corner_radius']=10
    app.option_add('*Font',native_font(app,13))
    style=ttk.Style(app);style.theme_use('clam')
    style.configure('Quiet.Horizontal.TProgressbar',troughcolor=LINE,background=ACCENT,
        lightcolor=ACCENT,darkcolor=ACCENT,bordercolor=LINE,borderwidth=0,thickness=4)


def build_ui(app):
    app.grid_columnconfigure(1,weight=1);app.grid_rowconfigure(0,weight=1)
    sidebar=ctk.CTkFrame(app,width=210,corner_radius=0,fg_color=SIDE)
    sidebar.grid(row=0,column=0,sticky='nsew');sidebar.grid_propagate(False)
    sidebar.grid_columnconfigure(0,weight=1);sidebar.grid_rowconfigure(3,weight=1)
    brand=ctk.CTkFrame(sidebar,fg_color='transparent')
    brand.grid(row=0,column=0,sticky='ew',padx=20,pady=(24,22))
    mark=ctk.CTkLabel(brand,text='',image=glyph('voice',38,dark=True),width=40)
    mark.pack(side='left',padx=(0,11))
    brand_copy=ctk.CTkFrame(brand,fg_color='transparent');brand_copy.pack(side='left')
    label(brand_copy,'听记',size=19,color=PAPER,bold=True).pack(anchor='w')
    label(brand_copy,'T R A N S C R I P T',size=10,color='#ACC4D4').pack(anchor='w')
    app.new_button=button(sidebar,'导入音视频',app.new_transcription,icon='import',height=41)
    app.new_button.configure(fg_color='#C6E8EB',hover_color='#DDF3F5',text_color='#204E5A',border_width=0)
    app.new_button.grid(row=1,column=0,sticky='ew',padx=14,pady=(0,22))
    library_head=ctk.CTkFrame(sidebar,fg_color='transparent');library_head.grid(row=2,column=0,sticky='ew',padx=18)
    row=ctk.CTkFrame(library_head,fg_color='transparent');row.pack(fill='x')
    label(row,'文稿库',size=12,color=SIDE_MUTED).pack(side='left')
    app.library_count=label(row,'0',size=12,color=SIDE_MUTED);app.library_count.pack(side='right')
    app.history_search_entry=ctk.CTkEntry(library_head,textvariable=app.history_query,placeholder_text='搜索文稿',
        height=32,border_width=1,border_color='#35404E',fg_color='#202B37',text_color=SIDE_TEXT,font=(FONT,12))
    app.history_search_entry.pack(fill='x',pady=(7,9))
    app.history_search_hint=placeholder(app,app.history_search_entry,app.history_query,'搜索文稿',
        background='#202B37',color=SIDE_MUTED,size=12)
    app.history=DocumentLibrary(sidebar,app.open_history)
    app.history.grid(row=3,column=0,sticky='nsew',padx=8)
    app.history_empty=label(app.history,'还没有保存的文稿',size=12,color=SIDE_MUTED)
    app.history_empty.place(x=12,y=16)
    utility=ctk.CTkFrame(sidebar,fg_color='transparent');utility.grid(row=4,column=0,sticky='ew',padx=14,pady=(0,17))
    # A rule separates the scrolling library from the fixed actions, which
    # share the library's left edge instead of floating in the centre.
    ctk.CTkFrame(utility,fg_color='#2A3440',height=1).pack(fill='x',pady=(0,8))
    app.example_button=button(utility,'演示文稿',app.open_example,quiet=True,dark=True,icon='document',anchor='w')
    app.example_button.pack(fill='x')
    button(utility,'文稿文件夹',app.open_results,quiet=True,dark=True,icon='folder',anchor='w').pack(fill='x',pady=(0,8))
    label(utility,'文件与文稿留在本机',size=11,color=SIDE_MUTED).pack(anchor='w',padx=9,pady=(0,9))
    app.environment_button=button(utility,'偏好设置',app.show_preferences,quiet=True,dark=True,icon='settings',height=42,anchor='w')
    app.environment_button.configure(border_width=1,border_color='#35404E')
    app.environment_button.pack(fill='x')

    main=ctk.CTkFrame(app,fg_color=BG,corner_radius=0)
    main.grid(row=0,column=1,sticky='nsew',padx=24,pady=(20,12))
    app.main_panel=main;main.grid_columnconfigure(0,weight=1);main.grid_rowconfigure(4,weight=1)
    heading=ctk.CTkFrame(main,fg_color='transparent');heading.grid(row=0,column=0,sticky='ew',pady=(0,14))
    heading.grid_columnconfigure(0,weight=1)
    label(heading,'文稿库  ›  逐字稿',size=12,color=MUTED).grid(row=0,column=0,sticky='w',pady=(0,11))
    label(heading,variable=app.save_status,size=11,color='#52746B').grid(row=0,column=1,sticky='e',pady=(0,11))
    app.page_title=tk.StringVar(value='逐字稿工作台');app.page_subtitle=tk.StringVar()
    title_area=ctk.CTkFrame(heading,fg_color='transparent');title_area.grid(row=1,column=0,sticky='ew')
    title_area.grid_columnconfigure(0,weight=1)
    app.title_line=ctk.CTkFrame(title_area,fg_color='transparent');app.title_line.grid(row=0,column=0,sticky='ew')
    app.heading_label=label(app.title_line,variable=app.page_title,size=20,wraplength=560,justify='left')
    app.heading_label.pack(side='left',padx=(0,6))
    app.rename_document_button=button(app.title_line,'',app.rename_document,quiet=True,width=34,icon='edit')
    app.rename_document_button.pack(side='left');app.rename_document_button.configure(state='disabled')
    app.rename_area=ctk.CTkFrame(title_area,fg_color='transparent')
    app.rename_value=tk.StringVar()
    app.rename_entry=ctk.CTkEntry(app.rename_area,textvariable=app.rename_value,height=38,font=(FONT,16),
        border_color=LINE,fg_color=PAPER,text_color=INK)
    app.rename_entry.pack(side='left',fill='x',expand=True,padx=(0,5))
    button(app.rename_area,'保存',app.save_document_name,primary=True,width=56).pack(side='left',padx=(0,3))
    button(app.rename_area,'取消',app.cancel_document_name,quiet=True,width=52).pack(side='left')
    app.rename_entry.bind('<Return>',lambda _:app.save_document_name())
    app.rename_entry.bind('<Escape>',lambda _:app.cancel_document_name())
    app.heading_actions=actions=ctk.CTkFrame(heading,fg_color='transparent')
    actions.grid(row=1,column=1,sticky='ne',padx=(8,0))
    app.settings_button=button(actions,'选项',app.toggle_settings,quiet=True,width=70,icon='settings')
    app.settings_button.pack(side='left',padx=(0,7))
    app.export_menu=RoundedMenu(app,min_width=210)
    for title,kind in [('纯文本 · TXT','txt'),('带时间字幕 · SRT','srt'),('排版文稿 · Markdown','md'),('逐字稿数据 · JSON','json')]:
        app.export_menu.add_command(label=title,command=lambda k=kind:app.export(k))
    def show_export():
        app.export_menu.tk_popup(app.export_button.winfo_rootx(),app.export_button.winfo_rooty()+app.export_button.winfo_height())
    app.export_button=button(actions,'导出文稿',show_export,primary=True,width=120,height=39,icon='export')
    app.export_button.pack(side='left');app.export_button.configure(state='disabled');app.export_buttons=[app.export_button]
    app.page_subtitle_label=label(heading,variable=app.page_subtitle,size=12,color=MUTED,wraplength=800,justify='left')
    app.page_subtitle_label.grid(row=2,column=0,columnspan=2,sticky='w',pady=(5,0))
    def fit_heading(event):
        app.heading_label.configure(wraplength=max(200,heading._reverse_widget_scaling(event.width)-260))
        app.refit_page_title()
    heading.bind('<Configure>',fit_heading)

    app.task_bar=ctk.CTkFrame(main,fg_color=SOFT,corner_radius=10)
    app.task_bar.grid(row=2,column=0,sticky='ew',pady=(0,8))
    task_label=label(app.task_bar,variable=app.task_status,size=12,color=ACCENT,wraplength=650,justify='left')
    task_label.pack(fill='x',padx=12,pady=(8,0))
    task_actions=ctk.CTkFrame(app.task_bar,fg_color='transparent');task_actions.pack(fill='x',padx=8,pady=(0,6))
    app.return_task_button=button(task_actions,'查看任务',app.return_to_task,quiet=True,width=82)
    app.return_task_button.pack(side='left')
    app.stop_button=button(task_actions,'停止转写',app.stop,quiet=True,width=82);app.stop_button.pack(side='left')
    app.stop_button.configure(state='disabled')
    app.progress=ttk.Progressbar(task_actions,mode='indeterminate',length=100,style='Quiet.Horizontal.TProgressbar')
    app.task_bar.grid_remove()
    app.error_panel=ctk.CTkFrame(main,fg_color='#FFF0EF',border_color='#E8BAB5',border_width=1,corner_radius=10)
    app.error_panel.grid(row=3,column=0,sticky='ew',pady=(0,8))
    detail=label(app.error_panel,variable=app.notice_text,size=12,color='#923C34',wraplength=750,justify='left')
    detail.pack(fill='x',padx=14,pady=9)
    app.reload_button=button(app.error_panel,'重新载入已保存版本',app.reload_current_document,width=165)
    app.error_panel.bind('<Configure>',lambda e:detail.configure(wraplength=max(220,app.error_panel._reverse_widget_scaling(e.width)-28)))
    app.error_panel.grid_remove()

    app.tabs=TranscriptWorkspace(main);app.tabs.grid(row=4,column=0,sticky='nsew')
    _build_reader(app,app.tabs)
    app.workspace_footer=ctk.CTkFrame(main,fg_color='transparent')
    app.workspace_footer.grid(row=5,column=0,sticky='ew',pady=(8,2))
    label(app.workspace_footer,'Ctrl F 搜索  ·  Ctrl Enter 回听  ·  Ctrl E 校对',size=11,color=MUTED).pack(side='left')
    app.status_label=label(main,variable=app.status,size=11,color=MUTED,wraplength=800,justify='left')
    app.status_label.grid(row=6,column=0,sticky='ew')
    main.bind('<Configure>',lambda e:app.status_label.configure(wraplength=max(280,main._reverse_widget_scaling(e.width))),add='+')

    app.import_drawer=SideDrawer(app,'导入音视频',app.close_import_flow)
    app.import_flow=ImportView(app.import_drawer,app,label,button,combo)
    app.import_flow.grid(row=1,column=0,sticky='nsew',padx=16,pady=(0,16))
    app.preferences_drawer=SideDrawer(app,'偏好设置')
    body=app.environment_panel=ctk.CTkScrollableFrame(app.preferences_drawer,fg_color=BG,corner_radius=0)
    body.grid(row=1,column=0,sticky='nsew',padx=20,pady=(0,20))
    label(body,'本地识别',size=14).pack(anchor='w',pady=(8,9))
    label(body,variable=app.models_label,size=13,color=MUTED,justify='left',wraplength=330).pack(fill='x')
    label(body,'计算设备',size=13).pack(anchor='w',pady=(25,9))
    app.device_box=combo(body,app.device,['自动（优先显卡）','仅 CPU'],330);app.device_box.pack(fill='x')
    label(body,'正文 14px · 带时间戳 · 修改后保存到本机',size=12,color=MUTED,wraplength=330).pack(fill='x',pady=(24,14))
    button(body,'重新检查模型',app.check_models,icon='settings').pack(fill='x',pady=(5,6))
    button(body,'使用说明',app.show_help,icon='document').pack(fill='x')
    app.apply_reading_size();sync_document_ui(app)


def _build_reader(app,parent):
    search=ctk.CTkFrame(parent,fg_color='transparent');search.pack(fill='x',pady=(0,10))
    search.grid_columnconfigure(1,weight=1)
    ctk.CTkLabel(search,text='',image=glyph('search',19),width=26).grid(row=0,column=0,padx=(0,6))
    app.search_entry=ctk.CTkEntry(search,textvariable=app.search_query,placeholder_text='搜索逐字稿…',
        height=35,border_width=1,border_color=LINE,fg_color=PAPER,text_color=INK,font=(FONT,13),width=100)
    app.search_entry.grid(row=0,column=1,sticky='ew')
    app.search_hint=placeholder(app,app.search_entry,app.search_query,'搜索逐字稿…',
        background=PAPER,color='#7C8795',size=13)
    label(search,variable=app.search_result,size=11,color=MUTED,width=75).grid(row=0,column=2,padx=4)
    app.search_prev_button=button(search,'',app.search_previous,quiet=True,width=30,icon='up')
    app.search_prev_button.grid(row=0,column=3)
    app.search_next_button=button(search,'',app.search_next,quiet=True,width=30,icon='down')
    app.search_next_button.grid(row=0,column=4)
    app.search_prev_button.configure(state='disabled');app.search_next_button.configure(state='disabled')
    app.reading_size_button=button(search,'放大阅读',app.toggle_reading_size,quiet=True,width=80)
    app.reading_size_button.grid(row=0,column=5,padx=(10,0))
    ctk.CTkFrame(search,fg_color=LINE,height=1).grid(row=1,column=0,columnspan=6,sticky='ew',pady=(6,0))

    audio=app.audio_panel=ctk.CTkFrame(parent,fg_color=DOCK,corner_radius=13)
    audio.pack(side='bottom',fill='x',pady=(10,2))
    top=ctk.CTkFrame(audio,fg_color='transparent');top.pack(fill='x',padx=12,pady=(12,3))
    app.previous_segment_button=button(top,'',app.previous_segment,quiet=True,dark=True,width=32,icon='previous')
    app.previous_segment_button.pack(side='left')
    app.play_button=button(top,'回听',app.toggle_audio,width=78,height=40,icon='play')
    app.play_button.configure(fg_color='#C6E8EB',hover_color='#DDF3F5',text_color='#174C58',border_width=0)
    app.play_button.pack(side='left',padx=5)
    app.next_segment_button=button(top,'',app.next_segment,quiet=True,dark=True,width=32,icon='next')
    app.next_segment_button.pack(side='left')
    app.audio_position=tk.StringVar(value='00:00 / 00:00')
    label(top,variable=app.audio_position,size=14,color=PAPER).pack(side='left',padx=(12,0))
    app.relink_button=button(top,'关联原音',app.relink_source,quiet=True,dark=True,width=88,icon='link')
    app.relink_button.pack(side='right')
    app.audio_stop_button=button(top,'停止',app.stop_audio,quiet=True,dark=True,width=48)
    app.audio_stop_button.pack(side='right')
    app.replay_button=button(top,'重播',app.play_current_segment,quiet=True,dark=True,width=48)
    app.replay_button.pack(side='right')
    hint=label(audio,variable=app.audio_hint,size=11,color='#B2C2D0',wraplength=650,justify='left')
    hint.pack(fill='x',padx=16,pady=(0,6))
    rail=ctk.CTkFrame(audio,fg_color='transparent');rail.pack(fill='x',padx=14,pady=(0,10))
    rail.grid_columnconfigure((0,1,2,3),weight=1,uniform='cue')
    app.segment_cues=[]
    for i in range(4):
        cue=button(rail,'—',lambda:None,quiet=True,dark=True,width=50,height=28)
        cue.configure(font=(FONT,11),corner_radius=6)
        cue.grid(row=0,column=i,sticky='ew',padx=3);app.segment_cues.append(cue)
    for widget in (app.previous_segment_button,app.play_button,app.next_segment_button,app.replay_button,app.audio_stop_button,app.relink_button):widget.configure(state='disabled')
    audio.bind('<Configure>',lambda e:hint.configure(wraplength=max(200,audio._reverse_widget_scaling(e.width)-32)))

    app.reader=TranscriptReader(parent,on_select=app.select_segment,on_play=app.play_segment,
        on_edit=app.open_segment_editor,on_search_change=app.search_results_changed,
        background=BG,foreground=INK,muted_color=MUTED,accent_color=ACCENT,
        selected_background=SOFT,font=native_font(app,14),metadata_font=native_font(app,12))
    app.reader.pack(fill='both',expand=True)
    app.selection_hint=tk.StringVar()
    app.editor_body=ctk.CTkFrame(app.reader.text,fg_color=PAPER,border_width=1,border_color=LINE,corner_radius=10,height=245)
    app.editor_body.pack_propagate(False)
    speaker=ctk.CTkFrame(app.editor_body,fg_color='transparent');speaker.pack(fill='x',padx=12,pady=(10,4))
    label(speaker,'说话人',size=12,color=MUTED).pack(side='left',padx=(0,8))
    app.segment_speaker=tk.StringVar(value='未选择')
    app.speaker_box=combo(speaker,app.segment_speaker,['未选择'],145)
    app.speaker_box.configure(command=app.assign_speaker,state='disabled');app.speaker_box.pack(side='left')
    app.rename_button=button(speaker,'改名',app.rename_speaker,quiet=True,width=55,icon='edit');app.rename_button.pack(side='left',padx=4)
    app.merge_button=button(speaker,'合并',app.show_merge_menu,quiet=True,width=50);app.merge_button.pack(side='left')
    app.speaker_name_row=ctk.CTkFrame(app.editor_body,fg_color='transparent')
    app.speaker_name_value=tk.StringVar()
    app.speaker_name_entry=ctk.CTkEntry(app.speaker_name_row,textvariable=app.speaker_name_value,font=(FONT,14),height=34)
    app.speaker_name_entry.pack(side='left',fill='x',expand=True)
    button(app.speaker_name_row,'应用',app.save_speaker_name,primary=True,width=56).pack(side='left',padx=5)
    app.speaker_name_entry.bind('<Return>',lambda _:app.save_speaker_name() or 'break')
    app.speaker_name_entry.bind('<Control-Return>',lambda _:app.save_speaker_name() or 'break')
    app.speaker_name_entry.bind('<Escape>',app.cancel_speaker_name)
    # The action row is packed first at the bottom so a short editor clips
    # the text area, never the save/cancel buttons.
    edit_actions=ctk.CTkFrame(app.editor_body,fg_color='transparent');edit_actions.pack(side='bottom',fill='x',padx=12,pady=(0,10))
    label(edit_actions,'Ctrl Enter 保存 · Esc 放弃 · 切换段落自动保存',size=11,color=MUTED).pack(side='left')
    app.editor_toggle_button=button(edit_actions,'保存修改',app.finish_segment_edit,primary=True,width=85)
    app.editor_toggle_button.pack(side='right')
    button(edit_actions,'取消',app.cancel_segment_edit,quiet=True,width=56).pack(side='right',padx=4)
    app.segment_editor=tk.Text(app.editor_body,height=1,width=1,wrap='word',undo=True,
        borderwidth=0,highlightthickness=0,bg=PAPER,fg=INK,insertbackground=ACCENT,
        padx=12,pady=8,font=native_font(app,14),selectbackground='#C8E7E9',selectforeground=INK)
    app.segment_editor.pack(fill='both',expand=True,padx=8,pady=(0,6));app.segment_editor.bind('<<Modified>>',app.segment_changed)
    app.segment_editor.bind('<Control-Return>',lambda _:app.finish_segment_edit() or 'break')
    app.segment_editor.bind('<Escape>',lambda _:app.cancel_segment_edit() or 'break')
    app.segment_editor.configure(state='disabled')

    app.empty_state=ctk.CTkFrame(app.reader,fg_color=BG,corner_radius=0)
    content=ctk.CTkFrame(app.empty_state,fg_color='transparent');content.place(relx=.5,rely=.4,anchor='center')
    ctk.CTkLabel(content,text='',image=glyph('voice',64),height=72).pack(pady=(0,18))
    app.empty_title=label(content,'把声音变成可查阅的文字',size=20);app.empty_title.pack(anchor='center')
    app.empty_caption=label(content,'导入音视频，搜索、回听和校对逐字稿。',size=13,color=MUTED,wraplength=330,justify='center')
    app.empty_caption.pack(anchor='center',pady=(8,20))
    button(content,'导入音视频',app.new_transcription,primary=True,icon='import',width=145,height=40).pack()


def sync_document_ui(app):
    app.import_flow.sync()
    if app.doc and app.doc.get('segments'):
        app.empty_state.place_forget()
        app.audio_panel.pack(side='bottom',fill='x',pady=(10,2),before=app.reader)
    else:
        app.audio_panel.pack_forget()
        app.empty_state.place(relx=0,rely=0,relwidth=1,relheight=1)
        app.empty_title.configure(text='正在生成逐字稿' if app.busy else '把声音变成可查阅的文字')
        app.empty_caption.configure(text='已完成的识别进度会保存，可以安全停止后继续。' if app.busy else '导入音视频，搜索、回听和校对逐字稿。')
