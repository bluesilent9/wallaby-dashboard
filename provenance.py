"""Observe V107's actual lot movements without changing its tax arithmetic."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys


def make_engine(core):
    BaseEngine=core.Engine
    class VisualEngine(BaseEngine):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.visual_paths={};self.visual_refs={};self.visual_events={}
            self.visual_context=None;self.visual_origin=None;self.visual_move=None;self.visual_external_source=None
            self._original_replace=core.replace
            def observe_replace(obj,**changes):
                copy=self._original_replace(obj,**changes)
                if isinstance(obj,core.Lot):self.remember(copy,self.path(obj))
                return copy
            core.replace=observe_replace

        def close_observer(self):core.replace=self._original_replace
        def remember(self,lot,path):
            self.visual_refs[id(lot)]=lot  # retain identity across split/copy lifetimes
            self.visual_paths[id(lot)]=tuple(path)
        def path(self,lot):return self.visual_paths.get(id(lot),())
        def stamp(self,value):
            if isinstance(value,(core.TransactionDate,datetime)):return core.utc_time(value).isoformat().replace('+00:00','Z')
            return str(value) if value is not None else None

        def record(self,obj):
            if isinstance(obj,core.WalletTx):
                data={'record_type':'Wallet transaction','location':obj.sheet,'utc':self.stamp(obj.dt),
                      'btc':str(obj.tx_total),'principal_btc':str(obj.principal),'network_fee_btc':str(obj.fee),
                      'balance_btc':str(obj.balance),'onchain_fee_source':obj.onchain_fee_source or None,
                      'fee_known':obj.fee_known,'transaction_id':obj.txid or None,'address':obj.address or None,
                      'source_row':obj.row_num,'output_index':obj.output_index if obj.output_index>=0 else None,'source_type':'Historical ODS' if obj.is_historical_ods else 'Descriptor blockchain'}
                if obj.accounting_flow:data.update(accounting_flow=obj.accounting_flow,allocation_basis='Year-bounded collaborative-payment accounting convention; not an on-chain input/output attribution')
            elif isinstance(obj,core.OtherExchangeRow):
                data={'record_type':'Exchange acquisition' if obj.buy else 'Exchange disposal','location':obj.exchange,
                      'utc':self.stamp(obj.dt),'btc':str(obj.btc),'other_asset':obj.currency,'other_asset_amount':str(obj.fiat),
                      'source_row':obj.row_num,'network_fee':str(obj.network_fee),'network_fee_unit':obj.network_fee_unit}
            elif isinstance(obj,core.BitarooRow):
                data={'record_type':obj.tx_type,'location':'Bitaroo','utc':self.stamp(core.tax_date(obj.dt)),
                      'amount':str(obj.amount),'unit':obj.asset,'balance':str(obj.balance),
                      'source_file':Path(obj.source_file).name,'source_page':obj.page,
                      'transaction_id':self.bitaroo_transaction_reference(obj)}
            else:return {}
            return {k:v for k,v in data.items() if v is not None}

        def context_records(self):
            def flatten(value):
                if isinstance(value,(tuple,list)):
                    for item in value:yield from flatten(item)
                else:yield value
            return list(flatten(self.visual_context.payload)) if self.visual_context else []

        def process_locals(self):
            frame=sys._getframe(1)
            while frame:
                if frame.f_code is BaseEngine.process_event.__code__:return frame.f_locals
                frame=frame.f_back
            return {}

        def current_record(self):
            objects=self.context_records()
            for typ in (core.WalletTx,core.OtherExchangeRow,core.BitarooRow):
                for obj in objects:
                    if isinstance(obj,typ):return self.record(obj)
            return {}

        def node(self,label,kind,stage,when,details=None):
            return {'label':str(label),'kind':kind,'stage':stage,'utc':self.stamp(when),
                    'details':details or {},'incoming':None,'outgoing':None}

        def valuation_fields(self,dt,currency=None):
            key=core.utc_time(dt) if core.has_precise_time(dt) else dt
            data={}
            source=self.price.source_for_day.get(key)
            if source:data['valuation_source']=source
            if currency:
                for when,unit,rate,amount,fx_source in self.fx_usage:
                    if unit==currency and core.utc_time(when)==core.utc_time(dt):
                        data['fx_aud_per_unit']=str(rate);data['fx_source']=fx_source
                        break
            return data

        def root(self,lot):
            if self.visual_origin:
                row=self.visual_origin['row'];label=row.exchange;kind='exchange';details=self.record(row)
            elif self.visual_external_source is not None:
                row=self.visual_external_source;label=row.sheet;kind='wallet';details=self.record(row)
                details.pop('address',None)  # A send record contains the recipient address.
                identity=getattr(self.ownership,'family_records',{}).get(label,{})
                owner=getattr(self.ownership,'family_users',{}).get(identity.get('owner_id'))
                if owner:details['ownership']='Owned by '+owner
            elif self.visual_context and self.visual_context.kind=='bitaroo_buy':
                label='Bitaroo';kind='exchange';details=self.current_record()
            else:
                label='Acquisition' if lot.basis_known else 'Unresolved acquisition';kind='external';details=self.current_record()
            details=dict(details,acquisition_source=lot.source,acquired_utc=self.stamp(lot.acquired),
                         acquisition_btc=str(lot.qty),cost_base_aud=str(lot.cost),basis_known=lot.basis_known,
                         price_aud_per_btc=str(lot.cost/lot.qty) if lot.qty and not lot.deferred_market_price and lot.basis_known else None,
                         price_status='Deferred market value' if lot.deferred_market_price else 'Recorded cost base')
            details.update(self.valuation_fields(lot.acquired,details.get('other_asset')))
            node=self.node(label,kind,'acquisition',lot.acquired,details);node['incoming']=dict(details,record_type='Acquisition')
            return node

        def acquire_from_external_person(self,*args,**kwargs):
            previous=self.visual_external_source
            source=None
            row=next((r for r in self.context_records() if isinstance(r,core.WalletTx)),None)
            if row is not None:
                if row.incoming:
                    match=core.find_loaded_wallet_counterparty(self.wallets,row.sheet,row)
                    if match is not None:source=self.wallets[match[0]][match[1]]
                elif self.visual_context and self.visual_context.kind=='wallet_to_exchange':source=row
            self.visual_external_source=source
            try:return super().acquire_from_external_person(*args,**kwargs)
            finally:self.visual_external_source=previous

        def new_lot(self,*args,**kwargs):
            lot=super().new_lot(*args,**kwargs);self.remember(lot,[self.root(lot)]);return lot

        def _take(self,*args,**kwargs):
            # Read the original method's returned split lists and fraction. Its
            # arithmetic and dust decisions execute unchanged in the original core.
            captured={};previous=sys.getprofile()
            def profile(frame,event,arg):
                if event=='return' and frame.f_code is BaseEngine._take.__code__:captured.update(frame.f_locals)
                if previous:previous(frame,event,arg)
            sys.setprofile(profile)
            try:result=super()._take(*args,**kwargs)
            finally:sys.setprofile(previous)
            if 'fraction' in captured:
                fraction=captured['fraction'];moved=iter(captured['moved']);kept=iter(captured['kept'])
                for parent in captured['lots']:
                    take=parent.qty*fraction
                    if take<=core.BTC_EPS:next(kept);continue
                    self.remember(next(moved),self.path(parent))
                    if parent.qty-take>core.BTC_EPS:self.remember(next(kept),self.path(parent))
            return result

        def process_event(self,event):
            previous=self.visual_context;self.visual_context=event
            try:return super().process_event(event)
            finally:self.visual_context=previous

        def record_other_exchange_acquisition(self,row,wallet,aud_value,trace):
            previous=self.visual_origin;self.visual_origin={'row':row,'wallet':wallet}
            try:return super().record_other_exchange_acquisition(row,wallet,aud_value,trace)
            finally:self.visual_origin=previous

        def move(self,source,dest,qty,dt,fee=Decimal('0'),desc='Transfer',fee_dt=None):
            previous=self.visual_move;self.visual_move={'source':source,'dest':dest,'utc':dt}
            try:return super().move(source,dest,qty,dt,fee,desc,fee_dt)
            finally:self.visual_move=previous

        def add(self,wallet,lot):
            path=list(self.path(lot)) or [self.root(lot)]
            if path[-1]['label']!=wallet:
                info=self.current_record();when=self.visual_move['utc'] if self.visual_move else (self.visual_context.when if self.visual_context else lot.acquired)
                outgoing=dict(info);incoming=dict(info);ev=self.visual_context
                if ev and ev.kind=='wallet_from_exchange':
                    recipient,index,_=ev.payload
                    outgoing=self.record(self.reports.btc_rows[index]);incoming=self.record(recipient);when=recipient.dt
                elif self.visual_move:
                    if ev and ev.kind=='wallet_out':
                        local=self.process_locals();dest_index=local.get('dest_idx')
                        if dest_index is not None:
                            recipient=self.wallets[wallet][dest_index];incoming=self.record(recipient);when=recipient.dt
                    w=next((r for r in self.context_records() if isinstance(r,core.WalletTx)),None)
                    if w and wallet=='Bitaroo':
                        match=core.match_bitaroo_deposit_row(self.reports,w)
                        if match:incoming=self.record(match[2])
                elif self.visual_origin:
                    row=self.visual_origin['row'];when=row.dt
                    candidates=[w for w in self.wallets.get(wallet,[]) if w.incoming and core.utc_time(w.dt)==core.utc_time(row.dt) and abs(w.principal-row.btc)<=core.BTC_EPS]
                    if len(candidates)==1:incoming=self.record(candidates[0]);when=candidates[0].dt
                    else:incoming={'record_type':'Receipt linked by the core','location':wallet,'acquisition_utc':self.stamp(row.dt),'btc':str(row.btc),'exchange':row.exchange}
                    outgoing=self.record(row)
                if self.visual_external_source is not None and path[-1]['label']==self.visual_external_source.sheet:
                    outgoing=self.record(self.visual_external_source)
                previous=dict(path[-1]);previous['outgoing']=dict(outgoing,transfer_to=wallet);path[-1]=previous
                kind='exchange' if wallet=='Bitaroo' or any(r.exchange==wallet for r in self.other_exchange_rows) else 'wallet'
                node=self.node(wallet,kind,'receipt',when,dict(incoming,location=wallet))
                node['incoming']=dict(incoming,transfer_from=path[-1]['label']);path.append(node)
            self.remember(lot,path);return super().add(wallet,lot)

        def _resolve_deferred_wallet_ownership(self,lot):
            before=(lot.acquired,lot.source);result=super()._resolve_deferred_wallet_ownership(lot)
            if before!=(lot.acquired,lot.source):
                path=[n for n in self.path(lot) if n.get('utc') and n['utc']>=self.stamp(lot.acquired)]
                context,origin=self.visual_context,self.visual_origin
                self.visual_context=self.visual_origin=None
                try:root=self.root(lot)
                finally:self.visual_context,self.visual_origin=context,origin
                root['label']='External acquisition'
                self.remember(lot,[root]+path)
            return result

        def _resolve_deferred_market_price(self,lot,wallet,dt):
            deferred=lot.deferred_market_price;result=super()._resolve_deferred_market_price(lot,wallet,dt)
            if deferred and not lot.deferred_market_price:
                path=list(self.path(lot))
                if path:
                    node=dict(path[0]);node['details']=dict(node['details'],price_aud_per_btc=str(lot.cost/lot.qty) if lot.qty else None,price_status='Resolved historical market value',allocated_cost_base_aud=str(lot.cost))
                    node['details'].update(self.valuation_fields(lot.acquired))
                    node['incoming']=dict(node['details'],record_type='Acquisition')
                    path[0]=node;self.remember(lot,path)
            return result

        def _record_sale(self,dt,desc,wallet,lots,proceeds,pool_qty_before):
            first=len(self.events);result=super()._record_sale(dt,desc,wallet,lots,proceeds,pool_qty_before)
            for event in self.events[first:]:
                paths=[]
                for lot in lots:
                    if not lot.basis_known:continue
                    long=core.held_twelve_months(lot.acquired,dt)
                    if event.bucket=='held < 12 months' and long:continue
                    if event.bucket=='held >= 12 months' and not long:continue
                    if event.bucket.startswith('pre-reform deferred'):
                        if not (lot.acquired<=core.REFORM_EVE and lot.transition_mv is not None):continue
                        if ('short' in event.bucket)==long:continue
                    path=list(self.path(lot)) or [self.root(lot)]
                    detail=dict(self.current_record(),disposal_utc=self.stamp(dt),description=event.description,
                                disposed_btc=str(event.btc),proceeds_aud=str(event.proceeds),cost_base_aud=str(event.cost),
                                gain_loss_aud=str(event.gain_loss),holding_bucket=event.bucket,
                                price_aud_per_btc=str(event.proceeds/event.btc) if event.btc else None,
                                price_status='Disposal proceeds per BTC',source_lot_btc=str(lot.qty))
                    detail.update(self.valuation_fields(dt,detail.get('other_asset')))
                    label=wallet;kind='exchange' if wallet=='Bitaroo' or any(r.exchange==wallet for r in self.other_exchange_rows) else 'wallet';stage='disposal'
                    if 'fee' in desc.lower():label='Network fee';kind='fee';stage='fee'
                    else:
                        ev=self.visual_context
                        if ev and ev.kind=='bitaroo_withdrawal':
                            recipient=ev.payload[2];label=recipient.sheet if recipient else 'External destination';kind='external'
                        elif ev and ev.kind=='wallet_out':
                            label=self.process_locals().get('dest') or 'External destination';kind='external'
                            if label=='External destination':
                                describe=getattr(self.ownership,'describe_bitaroo_destination',lambda address:None)
                                account=describe(detail.get('address',''))
                                if account:
                                    label=account['label'];kind='exchange'
                                    detail.update(location=label,transfer_from=wallet,ownership=account['ownership'],owner_name=account['owner_name'],wallet_addresses=[account['address']])
                    previous=dict(path[-1]);previous['outgoing']=dict(detail,transfer_to=label);path[-1]=previous
                    node=self.node(label,kind,stage,dt,detail);node['incoming']=detail
                    if kind=='exchange' and detail.get('wallet_addresses'):node['wallet_identity']=True
                    path.append(node)
                    paths.append({'lot_id':lot.id,'allocated_btc':str(lot.qty),'allocated_cost_aud':str(lot.cost),
                                  'acquired_utc':self.stamp(lot.acquired),'nodes':path})
                self.visual_events[id(event)]=paths
            return result
    return VisualEngine
