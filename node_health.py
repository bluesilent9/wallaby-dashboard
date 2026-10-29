"""Nonblocking, bounded Core health polling; transport presence is not chain readiness."""
import threading,time

def classify(info):
    if not isinstance(info,dict) or info.get('chain')!='main':raise ValueError('Bitcoin mainnet status is required.')
    blocks=info.get('blocks');headers=info.get('headers')
    if type(blocks) is not int or type(headers) is not int or min(blocks,headers)<0:raise ValueError('Invalid Core block heights.')
    downloading=bool(info.get('initialblockdownload')) or blocks<headers
    return {'state':'downloading' if downloading else 'ready','label':'Downloading Blockchain' if downloading else 'Connected','blocks':blocks,'headers':headers,'chain':'main','pruned':bool(info.get('pruned')),'pruneheight':info.get('pruneheight',0),'bestblockhash':info.get('bestblockhash'),'checked_at':time.monotonic()}

class Health:
    def __init__(self,registry):self.registry=registry;self.values={};self.pending=set();self.closed=False
    def refresh(self,claims):
        for c in claims:
            token=c['token'];last=self.values.get(token,{})
            if self.registry.hub.transfers.is_busy(c['node_id']):continue
            if self.closed or token in self.pending or time.monotonic()-last.get('checked_at',0)<15:continue
            self.pending.add(token);threading.Thread(target=self.poll,args=(dict(c),),daemon=True).start()
    def poll(self,c):
        token=c['token'];node=c['node_id']
        try:
            row=self.registry.rpc.call(node,'getblockchaininfo',[],timeout_seconds=25,consume=True,priority=1)
            if (row.get('error') or {}).get('code')=='synchronisation_busy':return
            if row['state']!='succeeded':raise ValueError(str((row.get('error') or {}).get('message',row['state'])))
            value=classify(row['result'])
            if value['pruned'] and value['pruneheight']:
                try:
                    h=self.registry.hub.rpc(node,'getblockhash',[value['pruneheight']]);header=self.registry.hub.rpc(node,'getblockheader',[h]);value['prune_time']=header['time']
                except (ValueError,KeyError):value['prune_time']=None
            with self.registry.lock:
                if token not in self.registry.connections:return
                self.values[token]=value
            threading.Thread(target=self.registry.hub.refresh_inventory,args=(node,token),daemon=True).start()
        except (ValueError,OSError,KeyError,TypeError) as exc:
            if 'synchronisation_busy' in str(exc) or 'synchronisation is' in str(exc):return
            with self.registry.lock:
                if token in self.registry.connections:self.values[token]={'state':'error','label':'Node status unavailable','error':str(exc),'checked_at':time.monotonic()}
        finally:
            with self.registry.lock:self.pending.discard(token)
    def status(self,c):
        value=dict(self.values.get(c.get('token'),{}))
        if self.registry.hub.transfers.is_busy(c['node_id']):
            value.pop('checked_at',None);return dict(value,state='synchronising',label='Synchronising Wallet')
        if not value:return {'state':'checking','label':'Checking Node'}
        if time.monotonic()-value.get('checked_at',0)>60:return dict(value,state='checking',label='Checking Node')
        value.pop('checked_at',None);return value
