"""Immutable mempool paging, strict complete-data projection and bounded diagnostics."""
import copy,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mempool_pages import collect,parameters,InvalidSnapshot,METHOD
from fees import project,FeeService
from diagnostic_log import DiagnosticLog
from agent_rpc import request,error_summary,METHODS
from nodes import NodeRegistry,create_agent_server
from test_build14 import Fixture,INFO

def entries(n):return {f'{i:064x}':{'vsize':150,'fees':{'modified':'0.00001234'},'depends':[f'{i-1:064x}'] if i%4 else []} for i in range(n)}
def fetcher(raw,mutate=lambda p:p):
 calls=[]
 def fetch(params):
  calls.append(params);offset=params.get('offset',0) if isinstance(params,dict) else 0;rows=list(sorted(raw.items()));end=min(offset+1000,len(rows))
  return mutate({'version':1,'snapshot':'b'*32,'created_at':1700000000,'expires_at':1700000300,'total':len(rows),'offset':offset,'next_offset':end if end<len(rows) else None,'entries':dict(rows[offset:end])})
 return fetch,calls

class Paging(unittest.TestCase):
 def test_38000_entries_are_complete_exact_and_dependencies_cross_page_boundaries(self):
  raw=entries(38000);raw[f'{1000:064x}']['depends']=[f'{999:064x}'];fetch,calls=fetcher(raw);progress=[]
  actual=collect(fetch,lambda *a:progress.append(a));self.assertEqual(actual,raw);self.assertEqual(len(calls),38);self.assertEqual(calls[1],{'snapshot':'b'*32,'offset':1000});self.assertEqual(progress[-1][:2],(38000,38000));self.assertTrue(project(actual))
 def test_changed_identity_metadata_missing_reordered_or_truncated_pages_are_refused(self):
  raw=entries(1001)
  changes=[lambda p:dict(p,snapshot='c'*32) if p['offset'] else p,lambda p:dict(p,total=1002) if p['offset'] else p,lambda p:dict(p,created_at=p['created_at']+1) if p['offset'] else p,lambda p:dict(p,offset=0) if p['offset'] else p,lambda p:dict(p,next_offset=2000),lambda p:dict(p,entries={})]
  for mutate in changes:
   with self.subTest(change=changes.index(mutate)),self.assertRaises(InvalidSnapshot):collect(fetcher(raw,mutate)[0])
 def test_invalid_fees_sizes_dependency_graphs_and_duplicate_transaction_are_refused(self):
  mutations=[lambda r:r[next(iter(r))].update(vsize=True),lambda r:r[next(iter(r))]['fees'].update(modified='NaN'),lambda r:r[next(iter(r))]['fees'].update(modified='1e999999999'),lambda r:r[next(iter(r))]['fees'].update(modified='0.000000001'),lambda r:r[next(iter(r))].update(depends=['f'*64]),lambda r:r[next(iter(r))].update(depends=[next(iter(r))]*2)]
  for mutate in mutations:
   raw=entries(3);mutate(raw)
   with self.assertRaises(InvalidSnapshot):collect(fetcher(raw)[0])
  raw=entries(1001)
  def duplicate(p):
   if p['offset']:p['entries']={next(iter(raw)):next(iter(raw.values()))}
   return p
  with self.assertRaises(InvalidSnapshot):collect(fetcher(raw,duplicate)[0])
 def test_expiry_error_and_local_expiry_never_return_partial_data(self):
  raw=entries(1001);fetch,_=fetcher(raw)
  def expired(p):
   if p:raise ValueError('Agent RPC local_rpc_mempool_expired')
   return fetch(p)
  with self.assertRaisesRegex(ValueError,'mempool_expired'):collect(expired)
  with self.assertRaisesRegex(InvalidSnapshot,'expired'):collect(fetch,clock=iter([0,301]).__next__)
 def test_empty_negative_modified_fees_and_cycles(self):
  self.assertEqual(collect(fetcher({})[0]),{});self.assertEqual(project({}),[])
  raw=entries(2);raw[f'{0:064x}']['fees']['modified']='-0.000001';self.assertEqual(sum(b['tx_count'] for b in project(collect(fetcher(raw)[0]))),2)
  raw[f'{1:064x}']['fees']['modified']='-0.000001';self.assertEqual(project(collect(fetcher(raw)[0])),[])
  raw=entries(2);raw[f'{0:064x}']['depends']=[f'{1:064x}']
  with self.assertRaisesRegex(ValueError,'Cyclic'):project(collect(fetcher(raw)[0]))
 def test_strict_read_only_request_parameters(self):
  for value in ([],{},{'snapshot':'a'*32,'offset':1000}):self.assertEqual(request(METHOD,value,None)[0],value)
  for value in ([True],{'snapshot':'a'*32,'offset':True},{'snapshot':'a'*32,'offset':1},{'snapshot':'a'*32,'offset':1000,'wallet':'x'},{'snapshot':'x','offset':1000}):
   with self.assertRaises(ValueError):request(METHOD,value,None)
  with self.assertRaises(ValueError):request(METHOD,[],'wallet')

class Service(unittest.TestCase):
 def test_failed_page_leaves_fee_rates_and_mined_blocks_but_no_projection(self):
  fixture=Fixture();original=fixture.call;raw=entries(1001);fetch,_=fetcher(raw)
  def call(node,method,params,wallet,**kw):
   if method==METHOD:
    if params:return {'id':'page-two','state':'failed','error':{'code':'local_rpc_response_limit','message':'Mempool page too large','data':{'stage':'rpc','category':'response_limit','boundary':'public_result','limit_bytes':16777216,'observed_bytes':16777217,'password':'hidden'}}}
    return {'id':'page-one','state':'succeeded','result':fetch(params)}
   return original(node,method,params,wallet,**kw)
  fixture.call=call;service=FeeService(fixture);service.fx=lambda:None;service.refresh()
  self.assertEqual(len(service.data['rates']),4);self.assertTrue(service.data['recent_blocks']);self.assertFalse(service.data['projected_blocks']);self.assertIn('limit_bytes=16777216',service.data['projection_error']);text=service.log_text();self.assertIn('request=page-two',text);self.assertIn('elapsed_ms=',text);self.assertNotIn('hidden',text)
 def test_rpc_failure_logs_have_exact_categories_and_no_payload_or_tokens(self):
  with tempfile.TemporaryDirectory() as d:
   reg=NodeRegistry(Path(d)/'config.cfg');reg.health.closed=True;n=reg.register('02:00:00:00:00:22','test-node',{'version':1,'methods':sorted(METHODS)},'2.0.0~dev20');q=reg.rpc.queue(n['node_id'],METHOD,[]);reg.rpc.poll(n['connection_token'],0);reg.rpc.ack(n['connection_token'],q['id']);reg.rpc.result(n['connection_token'],q['id'],error={'code':'local_rpc_response_limit','message':'not logged sensitive payload','data':{'boundary':'core_local','limit_bytes':67108864,'observed_bytes':67108865,'secret':'not logged'}})
   text=reg.log_text();self.assertIn('request='+q['id'],text);self.assertIn('method='+METHOD,text);self.assertIn('boundary=core_local',text);self.assertIn('Agent 2.0.0~dev20',text);self.assertIn('V1 Build 28',text);self.assertNotIn('sensitive payload',text);self.assertNotIn(n['connection_token'],text)
 def test_dashboard_validation_limit_and_agent_lower_bound_are_distinguished(self):
  with tempfile.TemporaryDirectory() as d:
   reg=NodeRegistry(Path(d)/'config.cfg');reg.health.closed=True;n=reg.register('02:00:00:00:00:23','limit-fixture',{'version':1,'methods':[METHOD]});q=reg.rpc.queue(n['node_id'],METHOD,[]);reg.rpc.poll(n['connection_token'],0);reg.rpc.ack(n['connection_token'],q['id'])
   with patch('agent_rpc.MAX_RESULT_BYTES',32),self.assertRaises(ValueError):reg.rpc.result(n['connection_token'],q['id'],result={'over':'x'*100})
   text=reg.log_text();self.assertIn('boundary=dashboard_result',text);self.assertIn('limit_bytes=32',text)
  self.assertIn('observed_bytes_at_least=17',error_summary({'code':'local_rpc_response_limit','data':{'observed_bytes_at_least':17,'observed_bytes':'not a number'}}))
 def test_connection_replacement_between_pages_is_not_mixed(self):
  fixture=Fixture();fixture.connections={'token-one':{'node_id':'node'}};original=fixture.call;fetch,_=fetcher(entries(1001))
  def call(node,method,params,wallet,**kw):
   if method==METHOD:
    page=fetch(params);fixture.connections={'token-two':{'node_id':'node'}};return {'state':'succeeded','result':page}
   return original(node,method,params,wallet,**kw)
  fixture.call=call;service=FeeService(fixture);service.fx=lambda:None;service.refresh();self.assertFalse(service.data['projected_blocks']);self.assertIn('connection changed',service.data['projection_error']);self.assertTrue(service.data['recent_blocks'])
 def test_credential_and_private_key_redaction_keeps_correlating_request_ids(self):
  from vendor.embit.bip32 import HDKey
  with tempfile.TemporaryDirectory() as d:
   log=DiagnosticLog(d);key=HDKey.from_seed(b'Test log redaction public fixture').to_base58();log.append('Test','request=12345 password=abc connection_token=def Authorization: Bearer xyz cookie="test:value" '+key);text=log.text();self.assertIn('request=12345',text)
   for secret in ('abc','def','xyz','test:value',key):self.assertNotIn(secret,text)

class Interop(unittest.TestCase):
 def test_actual_agent20_snapshot_and_transport_with_38000_transactions(self):
  source=os.environ.get('WALLABY_AGENT_SOURCE')
  if not source or not (Path(source)/'mempool_snapshot.py').exists():self.skipTest('Set WALLABY_AGENT_SOURCE to Agent Build20 for paging interoperability')
  with tempfile.TemporaryDirectory() as d:
   reg=NodeRegistry(Path(d)/'config.cfg');reg.health.closed=True;server=create_agent_server(reg,0);server.start();agent=subprocess.Popen([sys.executable,str(Path(__file__).with_name('agent_mempool_runner.py')),source,str(server.server_port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    self.assertEqual(agent.stdout.readline().strip(),'READY');service=FeeService(reg);service.fx=lambda:None;service.refresh();self.assertIsNone(service.error);self.assertIsNone(service.data['projection_error']);self.assertEqual(len(service.data['projected_blocks']),4);self.assertIn('entries=38000',service.log_text());self.assertIn('received=38000/38000',service.log_text());self.assertLess(service.registry.rpc.total_bytes,100000)
   finally:agent.terminate();_,err=agent.communicate(timeout=5);server.close()
   self.assertNotIn('Traceback',err)
