"""One isolated, interactive calculation. JSON lines connect it to the GUI."""
import builtins
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from portfolio import build_balances,replay_current_basis
from preferences import MissingOwnership
from ownership import inventory_for
from watch import watch_wallets,ChainClient
from explorer import enrich_history
from address_book import build_address_book
from descriptor_chain import HistoryClient,scan,wallet_rows,install_evidence,annotate_balances as descriptor_balances
from bitaroo_inputs import ledger_snapshot
from descriptor_snapshot import shared_history,saved_history
from matching_policy import matching_window, DEFAULT_WINDOW
from family_inputs import prepare_family,declarations,tax_contexts
from family_tax import FamilyCalculation,year_cutoff
from joint_allocations import collect,validate_sources

OUTPUT=sys.stdout

def emit(event_type,**data):
    OUTPUT.write(json.dumps(dict(type=event_type,**data),ensure_ascii=True)+'\n');OUTPUT.flush()

class GuiConsole:
    def __init__(self):self.pending='';self.lines=[];self.prompt_start=0;self.silent=False;self.profile=None
    def write(self,text):
        self.pending+=str(text)
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            self.lines.append(line)
            if not self.silent:emit('log',text=line)
        return len(str(text))
    def flush(self):
        if self.pending:
            self.lines.append(self.pending)
            if not self.silent:emit('log',text=self.pending)
            self.pending=''
    def isatty(self):return False


def question_context(lines,prompt):
    """Show the current question block; retain all activity in the separate log."""
    if prompt.lstrip().startswith('The ') and '\n' in prompt:return ''
    while lines and not lines[-1].strip():lines=lines[:-1]
    unresolved=[i for i,line in enumerate(lines) if 'UNRESOLVED BITCOIN' in line]
    if unresolved:return '\n'.join(lines[unresolved[-1]:])
    blanks=[i for i,line in enumerate(lines) if not line.strip()]
    return '\n'.join(lines[blanks[-1]+1:] if blanks else lines)


def load_core():
    name='bitaroo_tax_core'
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name('tax_core_v107.py'))
    core=importlib.util.module_from_spec(spec);sys.modules[name]=core;spec.loader.exec_module(core)
    return core


def install_trace_answers(core,console):
    """Only explicit answers from this worker run, scoped to the tax profile."""
    answers={}
    core.WalletOwnership._run_trace_lookup=lambda owner,kind,key:answers.get((console.profile,kind,key))
    core.WalletOwnership._run_trace_remember=lambda owner,kind,key,answer:answers.__setitem__((console.profile,kind,key),bool(answer))
    return answers


def install_gui_ownership(core,request=None,preferences=None):
    """Ownership is now resolved before a run; interactive identity prompts are forbidden."""
    class Fixed(core.WalletOwnership):
        def _ask_wallet_name(self,*args,**kwargs):raise MissingOwnership('Error: Missing wallet or exchange ownership information. Run the Ownership Feature to fix this')
        def _ask_wallet_status(self,*args,**kwargs):raise MissingOwnership('Error: Missing wallet or exchange ownership information. Run the Ownership Feature to fix this')
    core.WalletOwnership=Fixed;return Fixed

def annotate_balances(balances,ownership):
    for entry in balances['wallets']:
        row=ownership.family_records.get(entry['name'])
        if row:entry.update(owner_name=ownership.family_users.get(row['owner_id'],'Someone else / external'),smsf=row['smsf'])
        elif entry['name']=='Bitaroo Exchange':entry.update(owner_name=ownership.taxpayer,smsf=False)


def run(config):
    console=GuiConsole();sys.stdout=sys.stderr=console
    settings=config['settings'];paths=config['paths'];mode=config.get('mode','cgt');user=config.get('user');snapshot=config.get('family_snapshot')
    if not user or not snapshot:raise ValueError('Choose a family user before starting a job.')
    if mode not in ('cgt','inventory','descriptors'):raise ValueError('Balances are produced by a completed CGT run. Rebuild the report to refresh them.')
    def gui_input(prompt):
        console.flush()
        context=question_context(console.lines[max(console.prompt_start,len(console.lines)-28):],prompt)
        console.prompt_start=len(console.lines)
        kind='yesno' if prompt.strip().startswith('Enter Y') else 'text'
        emit('prompt',prompt='' if kind=='yesno' else prompt,context=context,kind=kind)
        reply=sys.stdin.readline()
        if not reply:raise EOFError('Calculation cancelled')
        data=json.loads(reply)
        answer=str(data.get('answer',''))
        if len(answer)>1000:raise ValueError('Answer is too long')
        emit('log',text=('Answer: '+({'y':'Yes','yes':'Yes','n':'No','no':'No'}.get(answer.strip().lower(),answer))) if kind=='yesno' else prompt+answer)
        return answer
    def rank_users(fy,users):
        console.flush()
        emit('prompt',kind='ranking',prompt='',context=f'Place in order from highest to lowest the users with the highest and lowest predicted income for the {fy} - {fy+1} Financial Year',choices=[{'id':u['id'],'name':u['name']} for u in users])
        reply=sys.stdin.readline()
        if not reply:raise EOFError('Calculation cancelled')
        order=json.loads(json.loads(reply)['answer'])
        if not isinstance(order,list) or sorted(order)!=sorted(u['id'] for u in users):raise ValueError('Rank every listed user exactly once.')
        emit('log',text='Income ranking for '+str(fy)+' - '+str(fy+1)+': '+' > '.join(next(u['name'] for u in users if u['id']==uid) for uid in order))
        return order
    builtins.input=gui_input
    core=load_core()
    install_trace_answers(core,console)
    def request_asset_basis(row,quantity):
        console.flush()
        context=f"{row.exchange} — {row.currency} acquisition details\nSold at {core.display_time(row.dt)}; untraced acquisition quantity {quantity:f} {row.currency}, including any fee in that asset. The exchange spreadsheet already establishes the disposal proceeds. Identify the acquisition cost and holding period below. For an investment received from a chain split, select Chain split and enter its receipt date; the cost base is zero."
        from asset_disposals import validate_basis
        while True:
            emit('prompt',kind='asset_basis',prompt='',context=context,asset_unit=row.currency,quantity=str(quantity),disposal_utc=core.display_time(row.dt),needs_transition=row.dt>=core.REFORM_DATE)
            reply=sys.stdin.readline()
            if not reply:raise EOFError('Calculation cancelled')
            try:
                answer=json.loads(json.loads(reply)['answer'])
                validate_basis(core,answer,quantity,row.dt)
            except (ValueError,KeyError,TypeError,ArithmeticError) as error:
                context='Please correct the acquisition details: '+str(error)+'\n\n'+context.split('\n\n')[-1]
                continue
            emit('log',text=f"Acquisition details confirmed for {quantity:f} {row.currency}; disposal consideration comes from the supplied BTC receipt.")
            return answer
    core.request_asset_basis=request_asset_basis
    window=matching_window(settings.get('matching_window',DEFAULT_WINDOW))
    sys.argv=['Wallaby Bitcoin Dashboard','--cache',config['cache'],'--match-window',window['key']]
    tax_zone=settings.get('timezone') or settings.get('browser_timezone')
    if tax_zone:sys.argv+=['--timezone',tax_zone]
    if settings.get('carried_losses'):sys.argv+=['--carried-losses',settings['carried_losses']]
    if settings.get('include_fiat_fees'):sys.argv+=['--include-fiat-disposal-fees']
    for key,flag in [('quotes','--quotes'),('cpi','--cpi')]:
        if paths.get(key):sys.argv += [flag,paths[key]]
    engine=None
    try:
        core.configure();emit('stage',stage='loading',label='Reading your statements and wallet records')
        core.FAMILY_TAX_CONTEXTS=tax_contexts(core,paths.get('tax_context'),snapshot['users'])
        print('Wallaby Bitcoin Dashboard · V1 Build 1 calculation engine')
        print('Transaction matching tolerance: '+window['label'])
        if mode=='descriptors':
            emit('stage',stage='discovering',label='Discovering descriptor wallet blockchain history')
            client=HistoryClient(core,str(Path(config['cache']).with_name('descriptor-chain.sqlite3')))
            try:chain,_=shared_history(config['preferences'],config['descriptor_catalog'],lambda:scan(config['descriptor_catalog'],client),force=True)
            finally:client.close()
            Path(config['result']).write_text(json.dumps({'descriptor_fingerprint':chain['catalog_fingerprint'],'scanned_utc':chain['scanned_utc']}))
            emit('complete',event_count=0,review_count=0);return 0
        taxpayer=user['name']
        historical=paths['historical'];exchanges=paths['exchanges']
        combined=Path(config['result']).parent/'bitaroo_combined'
        accounts=prepare_family(core,snapshot['users'],settings,paths,combined)
        active=declarations(snapshot['users'],settings,paths)
        core.FAMILY_ACCOUNT_USERS=[u for u in snapshot['users'] if active[u['id']]]
        coverage=accounts[user['id']]['coverage'];reports=accounts[user['id']]['reports']
        other=core.load_other_exchange_ods(exchanges)
        if mode=='inventory':
            emit('stage',stage='discovering',label='Discovering receiving/change addresses and blockchain history')
            def discover():
                client=HistoryClient(core,str(Path(config['cache']).with_name('descriptor-chain.sqlite3')))
                try:return scan(config['descriptor_catalog'],client)
                finally:client.close()
            chain,reused=shared_history(config['preferences'],config['descriptor_catalog'],discover,force=settings.get('refresh_descriptor_history',False),require_existing=not settings.get('refresh_descriptor_history',False),allow_stale=True)
            print(('Reusing shared descriptor blockchain history from ' if reused else 'Descriptor blockchain history refreshed at ')+chain['scanned_utc'])
        else:
            chain=saved_history(config['preferences'],config['descriptor_catalog'])
            if chain['catalog_fingerprint']!=config['descriptor_fingerprint']:raise ValueError('Wallet descriptors changed. Run Ownership again before CGT.')
        descriptor_rows=wallet_rows(core,chain)
        chain_client=ChainClient(core)
        historical_rows=core.load_historical_wallet_ods(historical,client=chain_client,defer_joint=True)
        validate_sources(core,chain)
        wallets=core.merge_wallets(descriptor_rows,historical_rows)
        install_evidence(core,chain)
        print('Confirmed block timestamps UTC are used for descriptor history; unconfirmed movements are excluded from CGT.')
        for name,rows in wallets.items():print(f'  {name}: {len(rows)} confirmed records')
        if mode=='inventory':
            chain_path=Path(config['result']).with_name('descriptor_history.json');chain_path.write_text(json.dumps(chain))
            emit('stage',stage='addresses',label='Discovering receiving addresses from your supplied records')
            books=[build_address_book(core,wallets,accounts[u['id']]['reports'],other,u,client=chain_client) for u in snapshot['users'] if active[u['id']]]
            if books:
                book=books[0]
                for extra in books[1:]:book['entries'].extend(e for e in extra['entries'] if e['kind']=='bitaroo')
            else:
                book=build_address_book(core,wallets,core.ReportSet(),other,user,client=chain_client)
                book['entries']=[e for e in book['entries'] if e['kind']!='bitaroo']
            result={'inventory':inventory_for(core,wallets,other,user),'address_book':book,'chain_path':str(chain_path),
                    'bitaroo_data_by_user':{uid:ledger_snapshot(core,a['reports']) for uid,a in accounts.items()},
                    'coverage_by_user':{uid:a['coverage'] for uid,a in accounts.items()},'family_accounts':active,
                    'historical_balances':{name:str(rows[-1].balance) if rows else '0' for name,rows in historical_rows.items()},**coverage}
            Path(config['result']).write_text(json.dumps(result));emit('complete',event_count=0,review_count=0);return 0
        print('Ownership loaded from config.cfg. SMSF ownership is recorded separately and excluded from personal CGT.')
        emit('stage',stage='tracing',label='Preparing all family financial years and joint payments')
        joints=collect(core,chain,chain_client,accounts,snapshot['users'],snapshot=snapshot)
        calculation=FamilyCalculation(core,chain,historical_rows,accounts,other,snapshot,settings,console,emit,rank_users)
        report,engines=calculation.run(joints,user)
        engine=engines[user['id']]
        ownership=engine.ownership
        emit('stage',stage='reporting',label='Building all annual interactive reports')
        report['matching_window']=window
        report['bitaroo_as_of_date']=coverage.get('bitaroo_as_of_date')
        for annual in report['annual_reports'].values():annual['matching_window']=window
        report['descriptor_scan']={'scanned_utc':chain['scanned_utc'],'timestamp_basis':chain['timestamp_basis'],'coverage':chain['coverage']}
        emit('stage',stage='balances',label='Reading latest balances and current BTC/AUD value')
        snapshots={};quote=None
        emit('stage',stage='watching',label='Checking live balances of historical wallet addresses')
        watched=watch_wallets(core,engine,client=chain_client)
        for profile in calculation.users:
            uid=profile['id'];current=engines[uid];console.profile=uid;basis_error=None
            emit('stage',stage='balances',label='Tracing current acquisition costs · '+profile['name'])
            try:current_basis=replay_current_basis(core,current)
            except (core.MissingDestinationDataError,core.MissingWalletDataError):raise
            except (RuntimeError,ValueError,OSError) as exc:
                current_basis={};basis_error=str(exc)
                print(profile['name']+' current acquisition costs could not all be established: '+basis_error)
            balance=build_balances(core,current,basis=current_basis,watched=watched,quote=quote)
            if balance.get('price_aud_per_btc'):quote=(balance['price_aud_per_btc'],balance['quote_source'])
            balance['basis_replay_error']=basis_error
            annotate_balances(balance,current.ownership)
            descriptor_balances(balance,core.DESCRIPTOR_SNAPSHOT)
            snapshots[uid]=balance
        merged=snapshots[user['id']]
        # Reuse the owning profile's complete cost basis for every family wallet.
        # SMSF/external/unknown ownership remains explicitly uncosted.
        owners={r['name']:r for r in snapshot['ownership'] if r.get('confirmed')}
        for entry in merged['wallets']:
            identity=owners.get(entry['name'])
            if not identity or identity.get('smsf') or identity.get('external'):continue
            source=next((w for w in snapshots.get(identity.get('owner_id'),{}).get('wallets',[]) if w['name']==entry['name']),None)
            if source:
                for key in ('cost_basis_aud','unrealised_gain_aud','state','cost_basis_note'):entry[key]=source[key]
        for profile in calculation.users:
            if profile['id']==user['id']:continue
            entry=next((w for w in snapshots[profile['id']]['wallets'] if w['kind']=='exchange'),None)
            if entry:merged['wallets'].append(dict(entry,name='Bitaroo Exchange · '+profile['name'],ownership='external',owner_name=profile['name']))
        report['balances']=merged
        report['balance_report']={'taxpayer':user['name'],'balances':merged}
        emit('stage',stage='explorer',label='Corroborating transaction links in every financial year')
        for profile in calculation.users:
            for key,annual in report['family_reports'][profile['id']].items():
                console.profile=profile['id']
                _,_,historical_engine=calculation.replay(profile,int(key),calculation.allocations,calculation.policies,core.D(annual['annual_tax']['prior_year_losses']),year_cutoff(core,int(key),calculation.now))
                chain_client.wallet_evidence=dict(getattr(core,'HISTORICAL_OUTPOINTS',{}).get('evidence',{}))
                try:enrich_history(core,historical_engine,annual,client=chain_client)
                finally:historical_engine.close_observer()
                annual['matching_window']=window
                annual['bitaroo_as_of_date']=accounts[profile['id']]['coverage'].get('bitaroo_as_of_date')
        selected=report['annual_reports'][str(report['selected_year'])]
        report['events']=selected['events']
        report['profiles_calculated']=len(calculation.users)
        result_path=Path(config['result']);result_path.write_text(json.dumps(report,ensure_ascii=True))
        emit('complete',event_count=len(report['events']),review_count=len(report['review']))
        return 0
    except MissingOwnership as exc:
        console.flush();emit('error',message=str(exc));return 2
    except (core.MissingDestinationDataError,core.MissingWalletDataError) as exc:
        console.flush();emit('error',message='Error: '+str(exc));return 2
    except Exception as exc:
        console.flush();emit('error',message=str(exc) or type(exc).__name__)
        traceback.print_exc(file=console);console.flush();return 2
    finally:
        if engine and hasattr(engine,'close_observer'):engine.close_observer()
        console.flush();sys.stdout=sys.stderr=OUTPUT

if __name__=='__main__':
    config=json.loads(Path(sys.argv[1]).read_text())
    raise SystemExit(run(config))
