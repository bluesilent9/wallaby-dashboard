"""Cooperative, server-owned public wallet discovery. Never navigates a browser.

Only complete history is published. Offline replicas remain in the catalog; a
missing historical transaction defers publication rather than accepting a partial ledger.
"""
import copy,json,threading,time
from collections import deque
from datetime import datetime,timezone
from descriptor_store import DescriptorStore
from descriptor_snapshot import history_ready,publish_history
from node_wallets import NodeWalletLoader,REQUIRED
from node_chain import RegistryRPC,configure
from descriptor_chain import HistoryClient,scan
from preferences import FamilyStore,resource_id
from wallet_names import suggest_wallet_name
from transaction_workflows import WorkflowStore

class BackgroundDiscovery:
    def __init__(self,sessions,busy=lambda:False,start=True):
        self.sessions=sessions;self.registry=sessions.nodes;self.config=sessions.preferences_path;self.busy=busy
        self.stop=threading.Event();self.lock=threading.RLock();self.lines=deque(maxlen=100);self.active=False;self.phase='';self.last_key=None;self.last_run=0;self.epoch=0
        self.thread=threading.Thread(target=self.run,name='wallet-discovery',daemon=True)
        if start:self.thread.start()
    def close(self):self.stop.set()
    def reset(self):
        with self.lock:self.epoch+=1;self.last_key=None;self.last_run=0
    def activity(self):
        with self.lock:return {'active':self.active,'phase':self.phase,'lines':list(self.lines)}
    def log(self,text):
        with self.lock:self.phase=str(text);self.lines.append(str(text)+'\n')
        self.registry.diagnostic_log.append('Wallet discovery',str(text))
    def pause(self):
        while self.busy():
            if self.stop.wait(.2):raise ValueError('Wallet discovery stopped.')
        if self.stop.is_set():raise ValueError('Wallet discovery stopped.')
    def eligible(self):
        return [n for n in self.registry.snapshot()['nodes'] if n['connected'] and n.get('rpc_ready') and n.get('blockchain',{}).get('state')=='ready' and REQUIRED<=set(n.get('rpc_methods',[]))]
    @staticmethod
    def semantic(wallets):
        rows=copy.deepcopy(wallets)
        for w in rows:
            for key in ('exported_utc','lastblock'):w.get('public_history',{}).pop(key,None)
            w['node_sources']=sorted(w.get('node_sources',[]),key=lambda r:(r['node_id'],r['wallet_name']))
            for row in w.get('node_history',[]):
                if int(row.get('confirmations',0))>0:row['confirmations']=1
            w['node_history']=sorted(w.get('node_history',[]),key=lambda r:(r['txid'],r.get('vout',-1),r.get('category','')))
        return json.dumps(sorted(rows,key=lambda r:r['id']),sort_keys=True)
    def notify(self,catalog):
        users=FamilyStore(self.config).snapshot()['users']
        reading={s.user_id for s in list(self.sessions.sessions.values()) if getattr(s,'current_page','')=='ownership' and s.presence_connected and time.monotonic()-s.presence_seen<90}
        WorkflowStore(self.config).discovered([u['id'] for u in users],[w['id'] for w in catalog['wallets']],reading)
    def cycle(self):
        nodes=self.eligible()
        if not nodes:return
        with getattr(self.registry,'lock',self.lock):claims=set(getattr(self.registry,'connections',{}))
        epoch=self.epoch
        self.pause();store=DescriptorStore(self.config);old=store.read()
        with self.lock:self.active=True
        self.log('Checking connected nodes for public wallets at idle priority.')
        try:
            loader=NodeWalletLoader(self.registry,self.log,self.stop,priority=100,pause=self.pause,nodes=nodes,tolerate_errors=True)
            fresh=loader.collect();superseded={};merged={w['id']:copy.deepcopy(w) for w in old['wallets']}
            # Match locking policies, not Core names (or key origin annotations).
            for w in fresh:
                keys={loader.descriptor_key(r['desc']) for r in w['raw']['descriptors']}
                from policy_lineage import related
                matches=[uid for uid,known in merged.items() if keys & {loader.descriptor_key(r['desc']) for r in known['raw']['descriptors']} or related(self.registry.hub,w['raw'],known['raw'])]
                for uid in matches:
                    known=merged.pop(uid)
                    live={n['id'] for n in nodes}
                    w['node_sources'] += [r for r in known.get('node_sources',[]) if r['node_id'] not in live and r not in w['node_sources']]
                    prior_keys={loader.descriptor_key(r['desc']):r for r in known['raw']['descriptors']}
                    if not set(prior_keys)<=keys:raise ValueError('Earlier policies of a refreshed wallet are missing from connected nodes. Load its prior policy monitors or complete archive on an Agent. Saved history has been retained; no partial replacement was published.')
                    w['name']=known['name'];w['raw']['wallet_name']=known['name']
                    if keys==set(prior_keys):
                        # Reuse the established export spelling for equivalent
                        # origin annotations. Preserve the durable identity.
                        for row in w['raw']['descriptors']:row['desc']=prior_keys[loader.descriptor_key(row['desc'])]['desc']
                        w['id']=known['id']
                    # The complete fresh wallet ledger is authoritative; old
                    # confirmations must not resurrect a conflicted transaction.
                for uid in matches:
                    if uid!=w['id']:superseded[uid]=w['id']
                merged[w['id']]=w
            wallets=sorted(merged.values(),key=lambda w:(w['id'] not in {r['id'] for r in old['wallets']},w['name'].casefold(),w['id']))
            used=set()
            for w in wallets:
                name=w['name']
                if name.casefold() in used:name+=' · '+w['id'][:8]
                w['name']=name;w['raw']['wallet_name']=name;used.add(name.casefold())
            wallets.sort(key=lambda w:w['name'].casefold())
            if epoch!=self.epoch:raise ValueError('Profile replaced; stale discovery discarded.')
            if self.semantic(wallets)==self.semantic(old['wallets']) and history_ready(self.config,old):self.notify(old);return
            catalog={'schema':132,'wallets':wallets,'loaded_utc':datetime.now(timezone.utc).isoformat()}
            service=self
            class IdleRPC(RegistryRPC):
                def nodes(self):return service.eligible()
                def call(self,node,method,params=None,wallet=None):
                    service.pause()
                    state=self.registry.rpc.call(node,method,params or [],wallet,timeout_seconds=120,consume=True,priority=100,cancel=service.stop)
                    if state['state']!='succeeded':raise ValueError(str((state.get('error') or {}).get('message',state['state'])))
                    return state['result']
            configure(IdleRPC(self.registry,priority=100),catalog)
            snapshot=scan(catalog,HistoryClient(None),self.log)
            self.pause()
            with self.sessions.profile_gate.access():
                if epoch!=self.epoch:raise ValueError('Profile replaced; stale discovery discarded.')
                # A profile reset disconnects all claims. Never publish its old scan.
                if not claims<=set(getattr(self.registry,'connections',{})):raise ValueError('Agent connections changed; discovery will retry.')
                if not {n['id'] for n in nodes}<={n['id'] for n in self.eligible()}:raise ValueError('Node availability changed; discovery will retry.')
                if DescriptorStore.fingerprint(store.read())!=DescriptorStore.fingerprint(old):raise ValueError('Wallet catalog changed; discovery will retry.')
                publish_history(self.config,catalog,snapshot)
                store.replace_node_wallets(wallets)
                from builder_identity import migrate
                migrate(self.config,superseded)
                fp=DescriptorStore.fingerprint(catalog)
                with FamilyStore(self.config).transaction() as data:
                    for record in data['ownership']:
                        if record.get('kind')=='descriptor' and record['source'] in superseded:
                            record['source']=superseded[record['source']];record['id']=resource_id('descriptor',record['source'])
                    for meta in data.get('ownership_runs',{}).values():
                        meta['inventory']=[r for r in meta['inventory'] if not (r.get('kind')=='descriptor' and r['source'] in superseded)]
                        previous={r['id']:r for r in meta['inventory']}
                        for w in wallets:
                            rid=resource_id('descriptor',w['id'])
                            item={'id':rid,'kind':'descriptor','source':w['id'],'source_name':w['name'],'name':w['name'] if w.get('extinct') else suggest_wallet_name(w['name']),'extinct':bool(w.get('extinct')),'has_vault':bool(w.get('has_vault')),'watch_only':bool(w.get('watch_only'))}
                            if rid not in previous:meta['inventory'].append(item)
                        meta['descriptor_fingerprint']=fp
                self.notify(catalog)
            self.log('Public wallet discovery complete. Ownership is ready for review.')
        finally:
            with self.lock:self.active=False
    def run(self):
        while not self.stop.wait(3):
            try:
                nodes=self.eligible();key=tuple(n['id'] for n in nodes)
                if not nodes or self.busy():continue
                if key==self.last_key and time.monotonic()-self.last_run<120:
                    self.notify(DescriptorStore(self.config).read());continue
                self.last_run=time.monotonic();self.last_key=key;self.cycle()
            except Exception as exc:
                if not self.stop.is_set():self.log('Wallet discovery deferred: '+str(exc))
