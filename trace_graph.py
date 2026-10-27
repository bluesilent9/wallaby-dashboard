"""Shared evidence graph. Facts are reusable; tax allocations are never persisted here.

Stable identities merge converging paths. Edges express evidence, not a claim that
particular inputs funded particular outputs in a jointly funded transaction.
"""
import ast
import hashlib
import json
from pathlib import Path


def identity(kind, value):
    return kind + ':' + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class TraceGraph:
    def __init__(self, saved=None):
        saved=saved or {}
        if saved and saved.get('schema')!=1:raise ValueError('Unsupported trace evidence graph.')
        self.nodes=dict(saved.get('nodes',{}));self.edges=dict(saved.get('edges',{}));self.claims=dict(saved.get('claims',{}));self.address_owners={};self.statement_accounts={}

    def export(self):return {'schema':1,'nodes':self.nodes,'edges':self.edges,'claims':self.claims}

    def node(self, kind, key, **fields):
        ident=identity(kind,key);item=self.nodes.setdefault(ident,{'kind':kind,'key':key})
        for name,value in fields.items():
            if value is not None:item[name]=value
        return ident

    def edge(self, source, relation, target, **fields):
        ident=identity('edge',[source,relation,target])
        item=self.edges.setdefault(ident,{'source':source,'relation':relation,'target':target})
        item.update(fields);return ident

    def record(self, core, row):
        if isinstance(row,core.WalletTx):
            # Physical records only: accounting_flow is an annual optimisation,
            # not a new on-chain ownership fact.
            if row.accounting_flow:return None
            key=['wallet',row.sheet,str(row.ref)]
            fields={'location':row.sheet,'utc':core.utc_time(row.dt).isoformat(),'btc':str(row.tx_total),
                    'principal_btc':str(row.principal),'fee_btc':str(row.fee),'txid':row.txid or None,
                    'source_type':'historical_ods' if row.is_historical_ods else 'descriptor_history'}
        elif isinstance(row,core.BitarooRow):
            key=['statement',self.statement_accounts.get(str(row.source_file),Path(row.source_file).parent.name),Path(row.source_file).name,row.page,
                 str(row.dt),row.tx_type,row.asset,str(row.amount),str(row.balance),row.trade_occurrence]
            fields={'location':'Bitaroo','utc':str(row.dt),'amount':str(row.amount),'unit':row.asset,'source_type':'statement'}
        elif isinstance(row,core.OtherExchangeRow):
            key=['exchange',row.exchange,str(row.ref)]
            fields={'location':row.exchange,'utc':core.utc_time(row.dt).isoformat(),'btc':str(row.btc),
                    'other_asset':row.currency,'other_amount':str(row.fiat),'source_type':'exchange_ods'}
        else:return None
        result=self.node('record',key,**fields)
        location=self.node('location',[key[0],fields['location']])
        self.edge(result,'recorded_at',location)
        if fields.get('txid'):
            transaction=self.node('transaction',fields['txid']);self.edge(result,'documents',transaction)
        return result

    def seed(self,core,wallets,chain,snapshot,accounts,other):
        for uid,account in accounts.items():
            for row in account['reports'].btc_rows+account['reports'].aud_rows:self.statement_accounts[str(row.source_file)]=uid
        for entry in snapshot.get('ownership',[]):
            resource=self.node('resource',entry['id'],kind_name=entry['kind'],source=entry['source'],name=entry['name'],
                               owner_id=entry.get('owner_id'),smsf=bool(entry.get('smsf')),confirmed=bool(entry.get('confirmed')))
            target=self.node('descriptor_wallet',entry['source']) if entry['kind']=='descriptor' else self.node('location',['wallet' if entry['kind']=='wallet' else 'exchange',entry['source']])
            self.edge(target,'identified_by',resource,evidence='ownership_assignment')
            if entry.get('owner_id'):
                owner=self.node('profile',entry['owner_id']);self.edge(resource,'assigned_to',owner,evidence='ownership_assignment')
        for rows in wallets.values():
            for row in rows:self.record(core,row)
        for uid,account in accounts.items():
            for row in account['reports'].btc_rows+account['reports'].aud_rows:
                record=self.record(core,row)
                if record:self.edge(record,'account_of',self.node('profile',uid))
        for row in other:self.record(core,row)
        transactions=dict(chain.get('transactions',{}))
        for txid,raw in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('transactions',{}).items():
            if txid in transactions:continue
            inputs=[{'txid':v['txid'],'vout':v['vout'],'value':v.get('prevout',{}).get('value'),
                     'script':v.get('prevout',{}).get('scriptpubkey')} for v in raw.get('vin',[]) if not v.get('is_coinbase')]
            outputs=[{'value':v['value'],'script':v.get('scriptpubkey'),'address':v.get('scriptpubkey_address')} for v in raw.get('vout',[])]
            transactions[txid]={'inputs':inputs,'outputs':outputs,'fee_sats':raw.get('fee'),'height':raw.get('status',{}).get('block_height')}
        for txid,tx in transactions.items():
            transaction=self.node('transaction',txid,utc=tx.get('utc'),height=tx.get('height'),fee_sats=tx.get('fee_sats'))
            for index,output in enumerate(tx.get('outputs',[])):
                out=self.node('outpoint',[txid,index],value_sats=output['value'],script=output.get('script'))
                self.edge(transaction,'creates',out)
                if output.get('address'):self.edge(out,'pays_address',self.node('address',output['address']))
            for value in tx.get('inputs',[]):
                out=self.node('outpoint',[value['txid'],value['vout']],value_sats=value['value'],script=value.get('script'))
                self.edge(out,'spent_by',transaction)
        assignments={(r['kind'],r['source']):r for r in snapshot.get('ownership',[]) if r.get('confirmed')}
        def assign(txid,index,entry):
            outputs=transactions.get(txid,{}).get('outputs',[])
            if entry and 0<=index<len(outputs) and outputs[index].get('address'):
                address=outputs[index]['address'];value=(entry.get('owner_id'),bool(entry.get('smsf')))
                if address in self.address_owners and self.address_owners[address]!=value:raise ValueError('Conflicting ownership assignments for an address.')
                self.address_owners[address]=value
        for outpoint,wallet in chain.get('wallet_outputs',{}).items():
            txid,index=outpoint.rsplit(':',1)
            assign(txid,int(index),assignments.get(('descriptor',wallet)))
            self.edge(self.node('outpoint',[txid,int(index)]),'belongs_to',self.node('descriptor_wallet',wallet),evidence='descriptor_history')
        # Extinct ownership uses exact supplied outputs, never a sender heuristic.
        for name,outputs in getattr(core,'HISTORICAL_OUTPOINTS',{}).get('wallets',{}).items():
            for txid,index in outputs:
                assign(txid,index,assignments.get(('wallet',name)))
                self.edge(self.node('outpoint',[txid,index]),'belongs_to',self.node('location',['wallet',name]),evidence='supplied_outpoint')

    def _scope(self,profile,kind,key):
        if kind=='source':
            txid,addresses=key
            return ['addresses',sorted(set(addresses))] if addresses else ['source_transaction',txid]
        if kind=='destination':
            txid,origin,address,amount,ref=key
            return ['addresses',[address]] if address else ['destination_record',txid,origin,amount,ref]
        raise ValueError('Unsupported ownership relationship')

    def remember(self,profile,kind,key,answer):
        scope=self._scope(profile,kind,key);ident=identity('claim',[profile,scope])
        previous=self.claims.get(ident)
        if previous is not None and previous['owned']!=bool(answer):raise ValueError('Conflicting confirmed ownership evidence. Review Ownership and source records.')
        subject=self.node('ownership_subject',scope)
        for address in scope[1] if scope[0]=='addresses' else []:
            self.edge(subject,'includes_address',self.node('address',address))
        self.claims[ident]={'profile':profile,'subject':subject,'scope':scope,'owned':bool(answer),'evidence':'user_confirmation'}
        self.edge(subject,'ownership_question_for',self.node('profile',profile))
        self.edge(self.node('transaction',key[0]),'source_ownership' if kind=='source' else 'destination_ownership',subject)

    def lookup(self,profile,kind,key):
        scope=self._scope(profile,kind,key)
        claim=self.claims.get(identity('claim',[profile,scope]))
        if claim is not None:return claim['owned']
        if scope[0]=='addresses' and scope[1]:
            known=[self.address_owners.get(a) for a in scope[1]]
            if any(v is not None and v==(profile,False) for v in known):return True
            if all(v is not None for v in known):return False
        if scope[0]=='addresses' and len(scope[1])>1:
            # A group No is never split into individual No claims: a mixed group
            # may contain an owned address. Only explicit singleton facts combine.
            values=[self.claims.get(identity('claim',[profile,['addresses',[a]]])) for a in scope[1]]
            if all(v is not None and not v['owned'] for v in values):return False
        return None

    def migrate(self,answers):
        for profile,kind,key,answer in answers:
            if kind in ('source','destination'):self.remember(profile,kind,ast.literal_eval(key),answer)

    def install(self,core,console):
        """Observe existing dependencies; never add a second search or skip replay."""
        graph=self;enqueue=core.HistoryPlanner.enqueue_wallet
        def visit(planner,wallet,row_idx,require_ownership=False,trace_source='Exchange Tracer'):
            result=enqueue(planner,wallet,row_idx,require_ownership,trace_source)
            if wallet in planner.wallets and 0<=row_idx<len(planner.wallets[wallet]):
                row=planner.wallets[wallet][row_idx]
                if row.ref in planner.required_wallet_rows:
                    record=graph.record(core,row)
                    if record:
                        trace=graph.node('trace',[console.profile,trace_source])
                        graph.edge(trace,'explored',record)
            return result
        core.HistoryPlanner.enqueue_wallet=visit
        match=core.HistoryPlanner.find_loaded_wallet_source_for_incoming
        def link(planner,wallet,row):
            found=match(planner,wallet,row)
            if found is not None:
                source=graph.record(core,planner.wallets[found[0]][found[1]]);dest=graph.record(core,row)
                if source and dest:graph.edge(source,'matched_receipt',dest,evidence='existing_transaction_matcher')
            return found
        core.HistoryPlanner.find_loaded_wallet_source_for_incoming=link
        process=core.Engine.process_event
        def observe(engine,event):
            result=process(engine,event)
            if not hasattr(engine,'visual_paths'):return result
            def flatten(value):
                if isinstance(value,(tuple,list)):
                    for item in value:yield from flatten(item)
                else:yield value
            records=[graph.record(core,row) for row in flatten(event.payload)]
            records=sorted(set(r for r in records if r))
            if records:
                # This is a replay observation, not evidence that an inferred
                # parcel allocation is on-chain or a reusable tax result.
                ident=graph.node('interpretation',[console.profile,engine.fy_start,event.kind,str(event.when),records],
                                 event_kind=event.kind,utc=str(event.when),status='derived_recomputed')
                for record in records:graph.edge(ident,'uses_record',record)
            return result
        core.Engine.process_event=observe
