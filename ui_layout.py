"""Resizable notebook pages; action controls stay outside scrolling content."""
import tkinter as tk
from tkinter import ttk


def initial_window_size(screen_width,screen_height):
    width=min(1280,max(400,screen_width-80));height=min(900,max(300,screen_height-110))
    return width,height


class ScrollablePage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(self,highlightthickness=0,borderwidth=0)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview).grid(row=0,column=1,sticky='ns')
        ttk.Scrollbar(self,orient='horizontal',command=self.canvas.xview).grid(row=1,column=0,sticky='ew')
        self.canvas.configure(yscrollcommand=self._set_y,xscrollcommand=self._set_x)
        self.content=ttk.Frame(self.canvas,padding=12)
        self.window=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self._layout_pending=False
        self.content.bind('<Configure>',self.queue_layout)
        self.canvas.bind('<Configure>',self.queue_layout)
    def _set_y(self,*values):
        self.grid_slaves(row=0,column=1)[0].set(*values)
    def _set_x(self,*values):
        self.grid_slaves(row=1,column=0)[0].set(*values)
    def queue_layout(self,event=None):
        if not self._layout_pending:
            self._layout_pending=True;self.after_idle(self.layout)
    def layout(self):
        self._layout_pending=False
        if not self.winfo_exists():return
        width=max(1,self.canvas.winfo_width());height=max(1,self.canvas.winfo_height())
        # Text follows the viewport, while wide form rows remain horizontally reachable.
        self.wrap_labels(self.content,max(260,width-48))
        self.canvas.itemconfigure(self.window,width=max(width,self.content.winfo_reqwidth()),height=max(height,self.content.winfo_reqheight()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
    @staticmethod
    def wrap_labels(widget,width):
        for child in widget.winfo_children():
            if isinstance(child,ttk.Label):
                try:
                    current=child.cget('wraplength')
                    if float(current or 0)>0:child.configure(wraplength=width)
                except (tk.TclError,ValueError):pass
            ScrollablePage.wrap_labels(child,width)
    def scroll(self,event,horizontal=False):
        delta=getattr(event,'delta',0)
        direction=-1 if getattr(event,'num',None)==4 or delta>0 else 1
        if horizontal:self.canvas.xview_scroll(direction*3,'units')
        else:self.canvas.yview_scroll(direction*3,'units')
        return 'break'
