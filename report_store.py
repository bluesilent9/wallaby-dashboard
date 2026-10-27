"""Latest family calculation, with private per-profile responses and atomic publication."""
from copy import deepcopy
from datetime import datetime,timezone
from preferences import FamilyStore
import hashlib,json,os,re,shutil,uuid
from pathlib import Path
from storage import data_directory,storage_lock


def signature(snapshot,fingerprint):
    fields=('id','kind','source','name','owner_id','external','smsf','confirmed')
    ownership=sorted(({k:r.get(k) for k in fields} for r in snapshot.get('ownership',[])),key=lambda r:r['id'])
    inputs={uid:{'files':m.get('files'), 'settings':m.get('settings'), 'family_accounts':m.get('family_accounts')} for uid,m in snapshot.get('ownership_runs',{}).items()}
    payload=[fingerprint,sorted((u['id'],u['name']) for u in snapshot['users']),ownership,inputs,snapshot.get('bitaroo_addresses',{})]
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def personal_report(family,uid,profile=None):
    years=family.get('family_reports',{}).get(uid)
    if not years:
        if family.get('owner_id')!=uid:raise ValueError('The calculation contains no report for this profile.')
        return {k:deepcopy(v) for k,v in family.items() if k!='family_reports'}
    year=str(family['selected_year']);selected=deepcopy(years[year])
    if selected.get('owner_id')!=uid or any(r.get('owner_id')!=uid for r in years.values()):raise ValueError('Invalid personal report ownership.')
    for key in ('selected_year','maximum_year','minimum_year','calculation_scope','allocation_algorithm','profiles_calculated','descriptor_scan','matching_window'):
        if key in family:selected[key]=deepcopy(family[key])
    selected['annual_reports']=deepcopy(years)
    if family.get('owner_id')==uid:
        for key in ('balances','balance_report','bitaroo_as_of_date'):
            if key in family:selected[key]=deepcopy(family[key])
    elif 'balances' in family:
        balance=deepcopy(family['balances']);name=profile['name'] if profile else selected['taxpayer']
        for w in balance.get('wallets',[]):
            own=w.get('owner_name')==name and not w.get('smsf')
            if w.get('kind')=='exchange':w['name']='Bitaroo Exchange' if own else 'Bitaroo Exchange · '+w.get('owner_name','Unknown')
            w['ownership']='personal' if own else 'external'
        selected['balances']=balance;selected['balance_report']={'taxpayer':name,'balances':balance}
    for annual in selected['annual_reports'].values():
        if 'matching_window' in selected:annual['matching_window']=selected['matching_window']
        if 'balances' in selected:annual['balances']=selected['balances']
    return selected


class ReportStore:
    def __init__(self,config):self.config=Path(config);self.root=data_directory(self.config)/'reports';self.index=self.root/'index.json'
    def _index(self):
        try:
            value=json.loads(self.index.read_text());return value if isinstance(value,dict) else {}
        except (OSError,ValueError):return {}
    def _write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=path.with_suffix('.tmp')
        try:tmp.write_text(json.dumps(data,ensure_ascii=True));tmp.chmod(0o600);os.replace(tmp,path)
        finally:tmp.unlink(missing_ok=True)
    def begin(self,signature):
        with storage_lock(self.config):
            # Replacement begins by discarding every prior profile report. A
            # failed/cancelled new run cannot silently restore an older result.
            shutil.rmtree(self.root,ignore_errors=True);generation=uuid.uuid4().hex
            self._write(self.index,{'generation':generation,'signature':signature,'state':'calculating'})
            return generation
    def publish(self,generation,signature,family,profiles):
        with storage_lock(self.config):
            index=self._index()
            if index.get('generation')!=generation or index.get('signature')!=signature:raise ValueError('This calculation was superseded by a newer CGT run.')
            users={p['id']:p for p in profiles};reports={uid:personal_report(family,uid,p) for uid,p in users.items()}
            for uid,report in reports.items():
                if not re.fullmatch('[0-9a-f]{32}',uid):raise ValueError('Invalid report profile.')
                self._write(self.root/(uid+'.json'),report)
            stamp=datetime.now(timezone.utc).isoformat()
            index.update(state='ready',users=sorted(reports),completed_utc=stamp);self._write(self.index,index)
            with FamilyStore(self.config).transaction() as data:data['cgt_completed_utc']=stamp
    def load(self,uid,signature,known=None):
        with storage_lock(self.config):
            index=self._index();generation=index.get('generation')
            if index.get('state')!='ready' or index.get('signature')!=signature or uid not in index.get('users',[]):return generation,None
            if generation==known:return generation,True
            path=self.root/(uid+'.json')
            if not path.is_file() or path.is_symlink():return generation,None
            try:report=json.loads(path.read_text())
            except (OSError,ValueError):return generation,None
            if not isinstance(report,dict) or report.get('owner_id')!=uid:return generation,None
            return generation,report
