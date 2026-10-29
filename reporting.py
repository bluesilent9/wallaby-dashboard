from version import VERSION, BUILD, LABEL
"""Serialize the existing core's results and exact totals for the HTML views."""
import builtins
import base64
import hashlib
from copy import deepcopy
import io
import json
from datetime import datetime,timezone
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent

def select_report_year(report,year=None):
    """A saved personal report opens on the year the user selected."""
    if year is None:return report
    key=str(year)
    if not re.fullmatch(r'\d{4}',key) or not report.get('minimum_year',0)<=int(key)<=report.get('maximum_year',0):raise ValueError('This financial year is not available in your report.')
    entry=report.get('annual_reports',{}).get(key)
    if not entry or entry.get('owner_id')!=report.get('owner_id'):raise ValueError('This financial year is not available in your personal report.')
    selected=dict(report);selected.update(entry);selected['selected_year']=int(key)
    return selected

class MemoryPaper(io.StringIO):
    def __exit__(self,*args):return False


def disposal_node(event,paths):
    """One event-level endpoint; parcel amounts belong on upstream branches."""
    if not paths:return None
    node=deepcopy(paths[0]['nodes'][-1])
    for side in ('details','incoming','outgoing'):
        if node.get(side) is None:continue
        fields=node[side]
        fields.pop('source_lot_btc',None)
        fields.update(**({'disposed_asset_quantity':event['btc'],'asset_unit':event['asset_unit']} if event.get('asset_unit','BTC')!='BTC' else {'disposed_btc':event['btc']}),proceeds_aud=event['proceeds'],
                      cost_base_aud=event['cost'],gain_loss_aud=event['gain'],
                      holding_bucket=event['bucket'],acquisition_path_count=len(paths))
    return node


def build_report(core,engine):
    buffer=MemoryPaper()
    previous=getattr(core,'open',None)
    def memory_open(path,*args,**kwargs):
        if path=='__HTML_WORKING_PAPER__':return buffer
        return builtins.open(path,*args,**kwargs)
    core.open=memory_open
    try:core.write_report('__HTML_WORKING_PAPER__',engine)
    finally:
        if previous is None:del core.open
        else:core.open=previous
    paper=buffer.getvalue()
    totals_text=paper.split('\\------/\n',1)[1].split('\n\n',1)[0]
    totals=[]
    for line in totals_text.splitlines():
        if ' = ' in line:
            label,value=line.split(' = ',1);totals.append({'label':label,'value':value})
    events=sorted((e for e in engine.events if engine.period_start<=e.date<=engine.period_end),
                  key=lambda e:(core.utc_time(e.date),e.description,e.bucket,e.lot_source))
    def money(v):
        rounded=core.qmoney(v)
        return ('-' if rounded<0 else '')+f'${abs(rounded):,.2f}'
    result=[]
    for i,e in enumerate(events,1):
        kind='loss' if e.bucket=='CGT event C1' else 'fee' if 'fee' in e.description.lower() else ('sale' if 'sale' in e.description.lower() else 'transfer')
        result.append({'id':i,'utc':core.display_time(e.date),'description':e.description,'wallet':e.wallet,
                       'kind':kind,'bucket':e.bucket,'btc':str(e.btc),'btc_display':core.qb(e.btc),'asset_unit':e.asset_unit,
                       'proceeds':str(e.proceeds),'cost':str(e.cost),'gain':str(e.gain_loss),
                       'proceeds_display':money(e.proceeds),'cost_display':money(e.cost),'gain_display':money(e.gain_loss),
                       'notes':list(e.notes),'lot_source':e.lot_source,'paths':engine.visual_events.get(id(e),[])})
    for event in result:event['disposal']=disposal_node(event,event['paths'])
    def stamp(v):return core.display_time(v)
    return {'schema':1,'application':'Wallaby Bitcoin Dashboard','version':VERSION,'build':BUILD,'version_label':LABEL,'core_version':'116',
            'taxpayer':engine.taxpayer,'financial_year':core.fmt_fy(engine.fy_start),
            'created_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
            'totals':totals,'annual_tax':{k:str(v) for k,v in core.annual_summary(events,core.CARRIED_LOSSES).items()},
            'events':result,'review':[],'asset_acquisition_facts':getattr(engine,'asset_acquisition_facts',[]),
            'prices':[{'utc':stamp(d),'context':c,'aud_per_btc':str(p),'source':s} for d,c,p,s in engine.price_usage],
            'fx':[{'utc':stamp(d),'currency':c,'aud_per_unit':str(r),'aud_value':str(a),'source':s} for d,c,r,a,s in engine.fx_usage],
            'ownership':[{'wallet':w,'status':engine.ownership.wallet_statuses.get(w,'unknown')} for w in sorted(engine.ownership.report_wallets)],
            'performance':{'http_attempts':core.NETWORK_CALLS,'http_seconds':round(core.NETWORK_SECONDS,3),
                           'cache_hits':core.CACHE_HITS,'provider_pacing_seconds':round(core.THROTTLE_SECONDS,3)},
            'conventions':['UTC transaction timestamps; local financial-year calendar.',
                           'Acquisition and disposal prices use the existing core and its disclosed source precision.',
                           'Each source path shows its allocated lot; original transaction amounts can be larger.']}


def render_html(report=None):
    """Use one bundled renderer for the live app and every saved/example report."""
    template=(ROOT/'index.html').read_text()
    template=template.replace('href="/assets/logo.svg"','href="data:image/svg+xml;base64,'+base64.b64encode((ROOT/'assets/logo.svg').read_bytes()).decode()+'"')
    css=(ROOT/'assets/style.css').read_text()+'\n'+(ROOT/'assets/mempool_blocks.css').read_text();js=(ROOT/'assets/vaults.js').read_text()+'\n'+(ROOT/'assets/builder_values.js').read_text()+'\n'+(ROOT/'assets/builder.js').read_text()+'\n'+(ROOT/'assets/history.js').read_text()+'\n'+(ROOT/'assets/fees.js').read_text()+'\n'+(ROOT/'assets/node_blocks.js').read_text()+'\n'+(ROOT/'assets/family.js').read_text()+'\n'+(ROOT/'assets/descriptors.js').read_text()+'\n'+(ROOT/'assets/app.js').read_text()
    build=hashlib.sha256((template+css+js).encode()).hexdigest()[:16]
    template=template.replace('</head>','<meta name="bitaroo-build" content="'+build+'">\n</head>')
    data=json.dumps(report,ensure_ascii=True,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=template.replace('<link rel="stylesheet" href="/assets/style.css">','<style>'+css+'</style>')
    template=template.replace('<script src="/assets/app.js" defer></script>',('<script id="report-data" type="application/json">'+data+'</script>') if report is not None else '')
    template=template.replace('<script src="/assets/descriptors.js" defer></script>','')
    template=template.replace('</body>','<script>'+js+'</script>\n</body>')
    return template


def export_html(report):
    return render_html(report)
