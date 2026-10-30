"""Fee rates must survive optional-data failures; transport remains read-only."""
import json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fees import FeeService
from nodes import NodeRegistry,create_agent_server
from agent_rpc import METHODS

INFO={'chain':'main','blocks':100,'headers':100,'initialblockdownload':False,'bestblockhash':'a'*64}
class Fixture:
 def __init__(self):self.calls=[];self.error=None;self.gate=None
 def snapshot(self):return {'nodes':[{'id':'node','hostname':'Example','connected':True,'rpc_ready':True}]}
 @property
 def rpc(self):return self
 def call(self,node,method,params,wallet,**kwargs):
  self.calls.append((method,kwargs))
  if method=='getblockchaininfo':result=dict(INFO)
  elif method=='getmempoolinfo':result={'size':0,'mempoolminfee':'0.000001','minrelaytxfee':'0.000001'}
  elif method=='estimatesmartfee':result={'feerate':'0.000002','blocks':params[0]}
  elif method=='getblock':result={'size':500000,'weight':1500000}
  elif method=='getrawmempool':
   if self.gate:self.gate.wait(3)
   if self.error:return {'state':'failed','error':{'message':self.error}}
   result={}
  elif method=='getblockheader':result={'height':100,'time':1700000000,'nTx':1}
  else:raise AssertionError(method)
  return {'state':'succeeded','result':result}

class Fees(unittest.TestCase):
 def service(self,fixture=None):
  fixture=fixture or Fixture();service=FeeService(fixture);service.fx=lambda:{'aud_per_btc':'100000','unix_time':service.clock(),'observed_utc':'2026-10-31T00:00:00Z'};return service,fixture
 def test_rates_publish_before_large_mempool_or_headers_and_without_whole_blocks(self):
  service,rpc=self.service();rpc.gate=threading.Event();service.refreshing=True;t=threading.Thread(target=service.refresh);t.start()
  try:
   deadline=time.monotonic()+2
   while service.data is None and time.monotonic()<deadline:time.sleep(.005)
   data=service.snapshot();self.assertEqual(len(data['rates']),4);self.assertEqual(data['rates'][0]['sat_per_vbyte'],'0.200000');self.assertTrue(data['loading']);self.assertFalse(data['stale']);self.assertIn('Four fee priorities ready',service.log_text())
   self.assertNotIn('getblock', [m for m,k in rpc.calls]);self.assertFalse(data['recent_blocks'])
  finally:rpc.gate.set();t.join(3)
  self.assertFalse(service.refreshing);self.assertEqual(len(service.data['recent_blocks']),1)
  self.assertTrue(all(k['timeout_seconds']==15 and k['priority']==100 for m,k in rpc.calls))
 def test_projection_and_quote_failures_leave_rates_visible(self):
  service,rpc=self.service();rpc.error='Mempool response exceeds its size limit';service.fx=lambda:(_ for _ in ()).throw(OSError('No quote connection'))
  service.refresh();self.assertEqual(len(service.data['rates']),4);self.assertIn('Mempool',service.data['projection_error']);self.assertIn('No quote',service.data['price_error']);self.assertFalse(service.refreshing)
 def test_no_nodes_is_actionable_and_appears_in_downloadable_diagnostics(self):
  service=FeeService(SimpleNamespace(snapshot=lambda:{'nodes':[]}));service.refresh();self.assertIn('Connect a Bitcoin Core node',service.error);self.assertIn('Connect a Bitcoin Core node',service.log_text())
 def test_two_minute_cadence_force_refresh_and_five_second_coalescing(self):
  service,_=self.service();clock=[0];service.clock=lambda:clock[0]
  with patch('fees.threading.Thread') as thread:
   service.snapshot();self.assertTrue(thread.called);service.refreshing=False;thread.reset_mock()
   service.data={'rates':[],'_retrieved':0};service.snapshot();self.assertFalse(thread.called)
   clock[0]=119;service.snapshot();self.assertFalse(thread.called)
   clock[0]=120;service.snapshot();self.assertTrue(thread.called);service.refreshing=False;thread.reset_mock()
   clock[0]=121;service.snapshot(force=True);self.assertFalse(thread.called)
   clock[0]=126;service.snapshot(force=True);self.assertTrue(thread.called)
 def test_failed_reads_and_downloading_nodes_stop_without_indefinite_loading(self):
  service,rpc=self.service();original=rpc.call
  def downloading(*args,**kwargs):
   out=original(*args,**kwargs)
   if args[1]=='getblockchaininfo':out['result']['headers']=101
   return out
  rpc.call=downloading;service.refresh();self.assertIsNone(service.data);self.assertIn('synchronise',service.error);self.assertFalse(service.refreshing)
  self.assertEqual([m for m,k in rpc.calls],['getblockchaininfo'])
 def test_synchronised_node_is_preferred_to_a_downloading_replica(self):
  service,rpc=self.service();rpc.snapshot=lambda:{'nodes':[{'id':'syncing','hostname':'Syncing','connected':True,'rpc_ready':True,'blockchain':{'state':'downloading'}},{'id':'ready','hostname':'Ready','connected':True,'rpc_ready':True,'blockchain':{'state':'ready'}}]};original=rpc.call
  def call(node,*a,**kw):self.assertEqual(node,'ready');return original(node,*a,**kw)
  rpc.call=call;service.refresh();self.assertEqual(service.data['source'],'Ready · Bitcoin Core RPC')
 def test_optional_header_failure_preserves_rates(self):
  service,rpc=self.service();original=rpc.call
  def call(*a,**kw):
   if a[1]=='getblockheader':return {'state':'failed','error':{'message':'Header not retained'}}
   return original(*a,**kw)
  rpc.call=call;service.refresh();self.assertEqual(len(service.data['rates']),4);self.assertIn('Header not retained',service.data['block_error'])

class Connections(unittest.TestCase):
 def test_missing_agent_version_does_not_break_node_list_or_permissions(self):
  with tempfile.TemporaryDirectory() as d:
   registry=NodeRegistry(Path(d)/'config.cfg');registry.health.closed=True
   n=registry.register('02:00:00:00:00:01','example',{'version':1,'methods':list(METHODS)})
   self.assertTrue(registry.snapshot()['nodes'][0]['connected']);self.assertIsNone(registry.hub.permissions.summary(n['node_id']));self.assertFalse(registry.hub.wallet_builder.available())
 def test_current_agent_real_http_register_poll_ack_result_and_fee_read(self):
  source=os.environ.get('WALLABY_AGENT_SOURCE')
  if not source:self.skipTest('Set WALLABY_AGENT_SOURCE for independent Agent transport validation')
  with tempfile.TemporaryDirectory() as d:
   registry=NodeRegistry(Path(d)/'config.cfg');registry.health.closed=True;server=create_agent_server(registry,0);server.start()
   agent=subprocess.Popen([sys.executable,str(Path(__file__).with_name('agent_readonly_runner.py')),source,str(server.server_port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    # Transport runner has a bounded lifetime even on a regression.
    line=agent.stdout.readline();self.assertEqual(line.strip(),'READY')
    service=FeeService(registry);service.fx=lambda:None;service.refresh()
    self.assertIsNone(service.error);self.assertEqual(len(service.data['rates']),4);self.assertTrue(registry.snapshot()['nodes'][0]['connected'])
   finally:
    agent.terminate();out,err=agent.communicate(timeout=5);server.close()
   self.assertNotIn('Traceback',err)

class Diagnostics(unittest.TestCase):
 def test_seven_utc_days_survive_restart_and_expire_on_read_without_new_events(self):
  from diagnostic_log import DiagnosticLog
  from datetime import datetime,timezone
  with tempfile.TemporaryDirectory() as d:
   clock=[datetime(2026,10,1,tzinfo=timezone.utc).timestamp()];log=DiagnosticLog(d,clock=lambda:clock[0])
   for i in range(10):
    log.append('Fee Estimates','Day '+str(i));clock[0]+=86400
   # October 11 retains October 5 onwards; dates older than seven UTC days expire.
   restart=DiagnosticLog(d,clock=lambda:clock[0]);text=restart.text()
   self.assertNotIn('Day 0\n',text);self.assertNotIn('Day 3\n',text);self.assertIn('Day 4\n',text);self.assertIn('Day 9\n',text)
   self.assertLessEqual(len(list(Path(d).glob('*.log'))),7)
   clock[0]+=8*86400;self.assertEqual(restart.text(),'');self.assertFalse(list(Path(d).glob('*.log')))
 def test_daily_size_cap_keeps_recent_utf8_and_does_not_split_records(self):
  from diagnostic_log import DiagnosticLog
  with tempfile.TemporaryDirectory() as d:
   log=DiagnosticLog(d);log.DAY_BYTES=2048
   for i in range(100):log.append('Fee Estimates','Latest '+str(i)+' · é '*30)
   data=next(Path(d).glob('*.log')).read_bytes();self.assertLessEqual(len(data),2048);self.assertIn('Latest 99',data.decode('utf-8'));self.assertEqual(next(Path(d).glob('*.log')).stat().st_mode&0o777,0o600)
 def test_fee_and_protocol_details_are_in_the_shared_persistent_log(self):
  with tempfile.TemporaryDirectory() as d:
   registry=NodeRegistry(Path(d)/'config.cfg');service=FeeService(registry);service.log('Refresh started.');registry.protocol_error('/agent/v1/poll',ValueError('Invalid poll fields.'))
   text=NodeRegistry(Path(d)/'config.cfg').log_text();self.assertIn('Fee Estimates',text);self.assertIn('Invalid poll fields',text);self.assertIn('Refresh started',text)

if __name__=='__main__':unittest.main()
