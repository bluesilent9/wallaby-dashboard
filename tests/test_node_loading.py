"""Wallet loading/error visibility and a real Agent-to-Core transport regression."""
import sys,unittest,tempfile,threading,json,time,subprocess,os
from pathlib import Path
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from launch_wallaby_bitcoin_dashboard import create_server,Session
from preferences import FamilyStore
from descriptor_store import DescriptorStore
from agent_rpc import METHODS
from vendor.embit import bip32
from vendor.embit.descriptor.checksum import checksum

def public_wallet(name,index):
    public=bip32.HDKey.from_seed(bytes([index+1])*32).to_public().to_base58();body='wpkh('+public+'/0/*)'
    return {'wallet_name':name,'descriptors':[{'desc':body+'#'+checksum(body),'range':[0,99],'next_index':1,'active':True,'internal':False,'timestamp':0}]}

class Loading(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.cfg=self.root/'config.cfg';store=FamilyStore(self.cfg);store.save_user('Example user','teal',revision=store.snapshot()['revision']);self.user=store.snapshot()['users'][0]['id'];self.server,self.pool=create_server(self.root/'runtime',0,self.cfg,ipv6=False);self.addCleanup(lambda:self.server.server_close())
    self.session=self.pool.create();self.session.select_user(self.user)
 def test_fast_failure_remains_available_to_ownership_even_after_terminal_locks(self):
    with patch('launch_wallaby_bitcoin_dashboard.NodeWalletLoader.collect',side_effect=ValueError('Node / wallet · getwalletinfo: Requested wallet does not exist or is not loaded')):
      self.session.start_node_wallets()
      for _ in range(100):
        if self.session.status=='error':break
        time.sleep(.01)
    self.assertEqual(self.session.status,'error');data=self.session.family();self.assertIn('Requested wallet',data['last_job_error']['message']);self.assertEqual(data['last_job_error']['mode'],'descriptors');self.assertFalse(data['process_active']);self.assertFalse(data['descriptors']['ready']);self.session.select_user(self.user);self.assertIsNone(self.session.last_job_error)
 def test_broker_limits_delivered_work_and_resumes_after_a_result(self):
    reg=self.pool.nodes.register('02:80:00:00:00:02','bounded',{'version':1,'methods':list(METHODS)});rpc=self.pool.nodes.rpc;token=reg['connection_token'];queued=[rpc.queue(reg['node_id'],'getblockchaininfo') for _ in range(3)]
    for row in queued[:2]:self.assertEqual(rpc.poll(token,0)['commands'][0]['id'],row['id']);rpc.ack(token,row['id'])
    self.assertEqual(rpc.poll(token,0)['commands'],[]);rpc.result(token,queued[0]['id'],result={});self.assertEqual(rpc.poll(token,0)['commands'][0]['id'],queued[2]['id'])
 def test_packaging_starts_and_enables_service_without_removing_legacy_unit(self):
    post=(ROOT/'packaging/postinst').read_text();unit=(ROOT/'packaging/wallaby-dashboard.service').read_text();self.assertIn('systemctl enable --now wallaby-dashboard.service',post);self.assertIn('WantedBy=multi-user.target',unit);self.assertIn('--config /var/lib/wallaby-dashboard/config.cfg',unit);self.assertFalse((ROOT/'packaging/preinst').exists())
 def test_real_agent_loads_all_twelve_wallets_using_exact_core_routes(self):
    source=os.environ.get('WALLABY_AGENT_SOURCE')
    if not source:self.skipTest('Set WALLABY_AGENT_SOURCE to the matching Agent src directory for this integration test.')
    names=['','Family Wallet/Watch','wallet/','name # percent%','épargne']+['sample_'+str(i) for i in range(7)];wallets={name:public_wallet(name,i) for i,name in enumerate(names)};seen=[];errors=[]
    class Handler(BaseHTTPRequestHandler):
      def log_message(self,*args):pass
      def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));method=body['method'];name=unquote(self.path[8:]) if self.path.startswith('/wallet/') else None;seen.append((method,name));reply={'jsonrpc':'2.0','id':body['id']}
        if method=='getblockchaininfo':reply['result']={'chain':'main','blocks':900000,'headers':900000,'initialblockdownload':False}
        elif method=='listwallets':reply['result']=names
        elif method=='listwalletdir':reply['result']={'wallets':[{'name':name} for name in names]}
        elif name not in wallets:reply['error']={'code':-18,'message':'Requested wallet does not exist or is not loaded'}
        elif method=='getwalletinfo':reply['result']={'walletname':name,'scanning':False,'private_keys_enabled':name!='sample_0'}
        elif method=='listdescriptors':reply['result']=wallets[name]
        elif method=='listsinceblock':reply['result']={'transactions':[],'lastblock':'1'*64}
        else:reply['error']={'code':-32601,'message':'Unknown fixture method'}
        payload=json.dumps(reply).encode();self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    core=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=core.serve_forever);thread.start();cookie=self.root/'.cookie';cookie.write_text('fixture:fixture');stop=self.root/'stop';agent=__import__('nodes').create_agent_server(self.pool.nodes,0,self.pool.profile_gate,ipv6=True);agent.start();done=threading.Event()
    def scan(session,preserve_events=False):session.job_result={'ok':True};session.emit({'type':'complete'});done.set()
    process=subprocess.Popen([sys.executable,str(ROOT/'tests/agent_runner.py'),str(Path(source).resolve()),str(cookie),str(core.server_port),str(agent.server_port),str(stop)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
      for _ in range(200):
        if any(n['connected'] for n in self.pool.nodes.snapshot()['nodes']):break
        if process.poll() is not None:self.fail(process.communicate()[1])
        time.sleep(.01)
      with patch.object(Session,'start_descriptor_scan',scan):
        self.session.start_node_wallets();self.assertTrue(done.wait(15),self.session.events)
      saved=DescriptorStore(self.cfg).read()['wallets'];self.assertEqual(len(saved),12);self.assertEqual([w['name'] for w in saved if w.get('watch_only')],['sample_0']);self.assertEqual({n for m,n in seen if m=='getwalletinfo'},set(names));self.assertEqual(self.session.status,'complete');self.assertIsNone(self.session.last_job_error);self.assertEqual(process.poll(),None)
    finally:
      stop.touch()
      try:process.communicate(timeout=3)
      except subprocess.TimeoutExpired:process.terminate();process.communicate(timeout=3)
      agent.close();core.shutdown();core.server_close();thread.join()
if __name__=='__main__':unittest.main(verbosity=2)
