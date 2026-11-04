"""Actual portable-profile roundtrips and conservative builder export filtering."""
import copy,json,tempfile
from pathlib import Path
import test_build8 as fixtures
from profile_archive import ProfileArchive,portable_state
from preferences import FamilyStore
from wallet_renames import RenameQueue
from wallet_names import dashboard_name
from builder_workspace import BuilderWorkspace
from transaction_workflows import WorkflowStore
from balance_store import BalanceStore
from descriptor_store import DescriptorStore
from report_store import signature
from transaction_templates import TemplateStore
from salary_builder import SalaryStore
from storage import data_directory
from nodes import NodeRegistry

class Profiles(fixtures.Setup):
 def restore(self):
  raw=ProfileArchive(self.config).save();target=Path(self.tmp.name)/'restored/config.cfg';FamilyStore(target);ProfileArchive(target).load('profile.7z',raw);return target
 def test_rename_lineage_notices_templates_balances_and_nodes_survive(self):
  q=RenameQueue(self.hub);q.rename(self.user,self.rid,None,'Readable First');q.rename(self.user,self.rid,None,'Readable Final');q.create(self.user,self.rid,'new_core_filename');before=q.proposal(self.a['node_id'])
  workflows=WorkflowStore(self.config);workflows.vault_warning(self.user,'Preserved notice')
  tpl=TemplateStore(self.config).save({'name':'Saved template','diagram':{'nodes':[{'id':'network-fee','x':1,'y':1}],'edges':[]}})
  key=signature(self.family.snapshot(),DescriptorStore(self.config).summary()['fingerprint']);BalanceStore(self.config).save(key,{'balances':{'wallets':[]}})
  target=self.restore();record=FamilyStore(target).snapshot()['ownership'][0];history=record['dashboard_name_history']
  self.assertEqual(dashboard_name(record),'Readable Final');self.assertEqual([(e['old_name'],e['new_name']) for e in history],[('Example Wallet','Readable First'),('Readable First','Readable Final')]);self.assertEqual(history[0]['user_id'],self.user)
  self.assertEqual(TemplateStore(target).read(tpl['id']),tpl);self.assertTrue(BalanceStore(target).ready(key));self.assertEqual(WorkflowStore(target).notifications(self.user)['notifications'][0]['text'],'Preserved notice')
  registry=NodeRegistry(target);registry.health.refresh=lambda _:None;restored=RenameQueue(registry.hub);self.assertEqual(restored.proposal(self.a['node_id']),before)
  self.assertEqual(restored.read()['operations'],q.read()['operations']);self.assertEqual({n['id'] for n in registry._read()['nodes']},{self.a['node_id'],self.b['node_id']})
 def test_initial_transaction_and_salary_excluded_without_mutating_live_state(self):
  ws=BuilderWorkspace(self.config);tx=ws.start(self.user,'transaction');draft={'nodes':[{'id':'network-fee','x':1,'y':1}],'edges':[]};ws.save(self.user,'transaction',dict(id=tx['id'],revision=tx['revision'],draft=draft));SalaryStore(self.config).change(self.user,'start');before=ws.path.read_bytes();target=self.restore()
  self.assertFalse(BuilderWorkspace(target).read()['workspaces']);self.assertFalse(SalaryStore(target).path.exists());self.assertEqual(ws.path.read_bytes(),before);self.assertTrue(SalaryStore(self.config).path.exists())
 def test_later_transaction_keeps_diagram_psbt_and_workspace_after_restore(self):
  ws=BuilderWorkspace(self.config);tx=ws.start(self.user,'transaction');draft={'nodes':[{'id':self.rid,'x':10,'y':10},{'id':'output','x':50,'y':10},{'id':'network-fee','x':30,'y':50}],'edges':[{'from':self.rid,'to':'output','amount_sats':'99000','fee_rate':'0'}]};ws.save(self.user,'transaction',dict(id=tx['id'],revision=tx['revision'],draft=draft));job=self.job();store=WorkflowStore(self.config);s=store._read();s['jobs'][job].update(workspace_id=tx['id'],diagram=draft);store._write(s);ws.attach(self.user,'transaction',job)
  target=self.restore();restored=BuilderWorkspace(target).read()['workspaces']['transaction'];self.assertEqual(restored['draft'],draft);self.assertEqual(restored['operations'],[job]);self.assertEqual(WorkflowStore(target).job(self.user,job)['psbt'],self.unsigned)
 def test_wallet_builder_draft_survives(self):
  ws=BuilderWorkspace(self.config);w=ws.start(self.user,'wallet');ws.save(self.user,'wallet',dict(id=w['id'],revision=0,draft={'name':'Future wallet','type':'0','roles':[]}));target=self.restore();self.assertEqual(BuilderWorkspace(target).read()['workspaces']['wallet'],ws.read()['workspaces']['wallet'])
 def test_ownership_rename_adds_history_and_retains_prior_changes(self):
  q=RenameQueue(self.hub);q.rename(self.user,self.rid,None,'First Label');self.family.save_ownership([dict(id=self.rid,name='Ownership Label',owner_id=self.user,external=False,smsf=False)],self.family.snapshot()['revision'],[dict(id=self.rid,kind='descriptor',source=self.wallet['id'],name='Example Wallet')]);r=self.family.snapshot()['ownership'][0];self.assertEqual([e['new_name'] for e in r['dashboard_name_history']],['First Label','Ownership Label']);target=self.restore();self.assertEqual(FamilyStore(target).snapshot()['ownership'][0]['dashboard_name_history'],r['dashboard_name_history'])

class Filtering(fixtures.unittest.TestCase):
 def stage(self,root,tx,extra=None):
  data=root/'data';data.mkdir(exist_ok=True);path=data/'builder-workspaces.json';state={'version':1,'workspaces':{'transaction':copy.deepcopy(tx)},'cancellations':{}}
  if extra:state['workspaces'].update(extra)
  path.write_text(json.dumps(state));return data,path
 def tx(self):return {'id':'a'*32,'kind':'transaction','owner_id':'u','revision':0,'draft':{},'operations':[]}
 def test_cancel_import_and_linked_renewal_are_not_discarded(self):
  for mode in ('cancel','import','renewal','request'):
   with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);tx=self.tx();extra=None
    if mode=='cancel':tx['cancellation_id']='cancel'
    if mode=='import':tx['draft']['imported_id']='import'
    if mode=='renewal':extra={'wallet':{'renewal':{'transaction_workspace':tx['id']}}}
    data,path=self.stage(root,tx,extra)
    if mode=='request':(data/'agent-hub.json').write_text(json.dumps({'requests':{'r':{'workspace_id':tx['id'],'proposal':None}}}))
    portable_state(root);self.assertIn('transaction',json.loads(path.read_text())['workspaces'])
 def test_recover_unattached_operation_before_exporting_workspace(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);tx=self.tx();data,path=self.stage(root,tx);(data/'transactions').mkdir();(data/'transactions/workflows.json').write_text(json.dumps({'jobs':{'job':{'workspace_id':tx['id']}}}));portable_state(root);self.assertEqual(json.loads(path.read_text())['workspaces']['transaction']['operations'],['job'])
