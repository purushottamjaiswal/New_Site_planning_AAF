"""Native Tk geometry smoke check. Run under a desktop or Xvfb, not unittest discovery."""
import sys,json,time,platform
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import App
from ui_layout import ScrollablePage

app=App();errors=[];app.report_callback_exception=lambda kind,value,tb:errors.append(str(value))
records=[]
def settle():
    # Bound the event loop so a bad resize callback cannot hide an infinite resize loop.
    done=[False];app.after(100,lambda:done.__setitem__(0,True))
    while not done[0]:app.update()
def visible(widget):
    x=widget.winfo_rootx()-app.winfo_rootx();y=widget.winfo_rooty()-app.winfo_rooty()
    return widget.winfo_ismapped() and x>=0 and y>=0 and x+widget.winfo_width()<=app.winfo_width() and y+widget.winfo_height()<=app.winfo_height()
for scaling in (1.333333,2.0):
    app.tk.call('tk','scaling',scaling)
    for width,height in ((1280,900),(1200,650),(1000,560),(800,480),(680,420)):
        app.geometry(f'{width}x{height}+0+0');settle()
        for page in (app.inputs,app.rules_tab,app.building_tab,app.source_tab,app.mapping_page,app.results,app.logs_tab):
            app.select_page(page);settle();assert visible(app.run_button),(width,height,page)
            host=app.page_hosts[str(page)]
            if isinstance(host,ScrollablePage):
                host.canvas.yview_moveto(1);settle();assert visible(app.run_button)
        app.section_var.set('Location search');app.choose_section();settle()
        assert app.tabs.select()==str(app.page_hosts[str(app.location_tab)])
        records.append({'width':app.winfo_width(),'height':app.winfo_height(),'tk_scaling':scaling,'run_button_visible_all_tabs':True})
# Exercise large-result paging without populating the Treeview with every row.
from session_db import Session
from database_result import DatabaseResult
rules=app.get_rules();rules.max_sites=15000;rules.validate()
store=Session()
for i in range(10050):store.add('sites',{'rank':i+1,'candidate_id':f'TEST_{i+1}','lat':0.,'lon':0.})
store.db.commit();path=store.path
app.current_result=DatabaseResult(store,rules,{'proposed_sites':10050})
app.show_result_page();assert len(app.tree.get_children())==200
app.show_result_page(10000);assert len(app.tree.get_children())==50
app.on_close();assert not path.exists()
assert not errors,errors
print(json.dumps({'native_toolkit':'Tk on '+platform.system(),'checks':records,'callback_errors':errors,'large_results_rows':10050,'first_page_rows':200,'last_page_rows':50,'temporary_session_removed_on_close':True,'native_resize_automation':'passed', 'manual_interaction':'not tested'},indent=2))
