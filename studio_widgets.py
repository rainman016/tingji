"""Native, keyboard-accessible components for the approved transcript studio."""
from datetime import datetime
from pathlib import Path
import tkinter as tk
import customtkinter as ctk

from core import timestamp
from studio_icons import glyph
from ui_theme import BG, PAPER, INK, MUTED, LINE, SIDE, SIDE_ACTIVE, SIDE_TEXT, SIDE_MUTED, FONT


class DocumentLibrary(ctk.CTkScrollableFrame):
    def __init__(self, master, command):
        super().__init__(master, fg_color=SIDE, corner_radius=0, width=180,
                         scrollbar_button_color='#354453', scrollbar_button_hover_color='#506478')
        self.command = command
        self.items, self.rows = [], []
        self.selected = None
        self.grid_columnconfigure(0, weight=1)

    def set_documents(self, documents, current=None):
        self.items = list(documents)
        for row in self.rows:
            row.destroy()
        self.rows.clear()
        self.selected = None
        for index, doc in enumerate(documents):
            row = ctk.CTkFrame(self, fg_color=SIDE, corner_radius=10, border_width=1, border_color=SIDE)
            row.grid(row=index, column=0, sticky='ew', pady=2)
            row.grid_columnconfigure(1, weight=1)
            source = Path(doc.get('source') or '').suffix.lower()
            icon = ctk.CTkLabel(row, text='', image=glyph('audio' if source in ('.mp3','.m4a','.wav','.flac','.aac','.ogg') else 'video', 20, dark=True), width=24)
            icon.grid(row=0, column=0, rowspan=2, padx=(10,8), pady=(11,8), sticky='n')
            title = str(doc.get('title') or '未命名文稿')
            title = title if len(title)<32 else title[:30]+'…'
            name = ctk.CTkLabel(row, text=title, font=(FONT,13), text_color=SIDE_TEXT, anchor='w', justify='left', wraplength=130)
            name.grid(row=0, column=1, sticky='ew', padx=(0,8), pady=(10,0))
            language = {'en':'英文','zh':'中文'}.get(doc.get('language'),'待识别')
            duration = timestamp(doc.get('duration_ms') or 0)
            if duration.startswith('00:'):
                duration=duration[3:]
            date = str(doc.get('created_at') or '')[5:10].replace('-', '/')
            meta=ctk.CTkLabel(row, text=f'{language} · {duration}'+(f' · {date}' if date else ''), font=(FONT,11), text_color=SIDE_MUTED, anchor='w')
            meta.grid(row=1,column=1,sticky='ew',padx=(0,6),pady=(0,9))
            for widget in (row, icon, name, meta):
                widget.bind('<Button-1>', lambda _, i=index: self.choose(i))
            row.tk.call(row._w, 'configure', '-takefocus', 1)
            row.bind('<Return>', lambda _, i=index: self.choose(i))
            row.bind('<space>', lambda _, i=index: self.choose(i))
            row.bind('<Up>', lambda _, i=index: self._key_select(i-1))
            row.bind('<Down>', lambda _, i=index: self._key_select(i+1))
            row.bind('<FocusIn>', lambda _, r=row: r.configure(border_color='#8FCBD1'))
            row.bind('<FocusOut>', lambda _, r=row: r.configure(border_color=SIDE))
            self.rows.append(row)
            if doc.get('id') == current:
                self.selected=index
        self._paint()

    def _paint(self):
        for i,row in enumerate(self.rows):
            row.configure(fg_color=SIDE_ACTIVE if i==self.selected else SIDE)

    def choose(self, index):
        self.selection_set(index)
        self.command()

    def _key_select(self,index):
        if self.rows:
            index=max(0,min(index,len(self.rows)-1))
            self.choose(index)
            if index<len(self.rows):
                self.rows[index].focus_set()
        return 'break'

    def selection_set(self,index):
        self.selected=int(index) if 0<=int(index)<len(self.items) else None
        self._paint()

    def selection_clear(self,*_):
        self.selected=None
        self._paint()

    def curselection(self):
        return () if self.selected is None else (self.selected,)

    def configure(self, **kwargs):
        kwargs.pop('state',None); kwargs.pop('font',None)
        return super().configure(**kwargs)


class TranscriptWorkspace(ctk.CTkFrame):
    """Single transcript surface; retains the document navigation API."""
    def __init__(self,master):
        super().__init__(master,fg_color=BG,corner_radius=0)
        self.wide_mode=False

    def add(self,name):
        if name!='文字稿':
            raise ValueError('Only the transcript workspace is available')
        return self

    def get(self):
        return '文字稿'

    def set(self,name):
        if name!='文字稿':
            raise ValueError('Only the transcript workspace is available')


class SideDrawer(ctk.CTkFrame):
    """App-owned modal panel. Does not create another OS window or process."""
    def __init__(self,app,title,on_close=None):
        super().__init__(app,fg_color=BG,border_width=1,border_color=LINE,corner_radius=15,width=442)
        self.app,self.on_close=app,on_close
        self.grid_propagate(False)
        self.shown=False
        self._last_focus=None
        self.grid_rowconfigure(1,weight=1);self.grid_columnconfigure(0,weight=1)
        head=ctk.CTkFrame(self,fg_color='transparent')
        head.grid(row=0,column=0,sticky='ew',padx=22,pady=(20,14))
        self.title=ctk.CTkLabel(head,text=title,font=(FONT,20),text_color=INK,anchor='w')
        self.title.pack(side='left')
        self.close_button=ctk.CTkButton(head,text='',image=glyph('close',18),width=34,height=34,
            fg_color='transparent',hover_color='#E8EEF2',command=self.close)
        self.close_button.pack(side='right')
        self.close_button.bind('<Return>',lambda _:self.close())
        self.close_button.bind('<space>',lambda _:self.close())
        self.bind('<Button-1>',self._outside)
        app.bind('<Configure>',self._resize,add='+')
        self._tag='Drawer'+str(id(self))
        app.bind_class(self._tag,'<Escape>',lambda _:self.close() or 'break')
        app.bind_class(self._tag,'<Tab>',self._tab)
        app.bind_class(self._tag,'<Shift-Tab>',lambda e:self._tab(e,True))

    def _children(self,widget):
        for child in widget.winfo_children():
            yield child
            yield from self._children(child)

    def _focusables(self):
        found=[]
        for widget in self._children(self):
            if not widget.winfo_viewable():
                continue
            if isinstance(widget,ctk.CTkButton):
                if widget.cget('state')!='disabled': found.append(widget)
            elif isinstance(widget,(tk.Entry,tk.Text)):
                if widget.cget('state')!='disabled': found.append(widget)
        return found

    def _tab(self,event,reverse=False):
        items=self._focusables()
        if items:
            focus=self.focus_get()
            index=items.index(focus) if focus in items else (-1 if not reverse else 0)
            target=items[(index+(-1 if reverse or event.state&1 else 1))%len(items)]
            self.tk.call('focus',target._w)
        return 'break'

    def _resize(self,event=None):
        if not self.shown or (event and event.widget is not self.app): return
        scale=ctk.ScalingTracker.get_widget_scaling(self.app)
        self.configure(width=min(442,max(340,self.app.winfo_width()/scale-32)),height=max(350,self.app.winfo_height()/scale-32))
        self.place(relx=1,x=-16,y=16,anchor='ne')

    def show(self):
        if self.shown:return
        for drawer in getattr(self.app,'_studio_drawers',[]):
            if drawer is not self and drawer.shown:drawer.hide()
        self.app._studio_drawers=list(dict.fromkeys(getattr(self.app,'_studio_drawers',[])+[self]))
        self._last_focus=self.focus_get()
        self.shown=True
        self._resize();self.lift()
        for widget in [self,*self._children(self)]:
            if self._tag not in widget.bindtags():widget.bindtags((self._tag,*widget.bindtags()))
        self.grab_set();self.tk.call('focus',self.close_button._w)

    def hide(self):
        if not self.shown:return
        self.shown=False
        if self.grab_current() is self:self.grab_release()
        self.place_forget()
        if self._last_focus and self._last_focus.winfo_exists():self._last_focus.focus_set()

    def close(self):
        self.hide()
        if self.on_close:self.on_close()

    def _outside(self,event):
        if self.shown and not (0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height()):self.close()
