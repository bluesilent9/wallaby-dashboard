"""Profile replacement must leave the node-backed fee service usable."""
import http.client,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fees import FeeService
from launch_wallaby_bitcoin_dashboard import create_server
from test_build14 import Fixture

class ProfileFees(unittest.TestCase):
 def test_clear_and_load_replace_closed_fee_worker_and_allow_refresh_again(self):
  made=[]
  class TrackedFees(FeeService):
   def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);made.append(self)
   def refresh(self):
    # Use the real fee pipeline with a deterministic local RPC fixture.
    registry=self.registry;self.registry=Fixture();self.fx=lambda:None
    try:super().refresh()
    finally:self.registry=registry
  with tempfile.TemporaryDirectory() as d,patch('launch_wallaby_bitcoin_dashboard.FeeService',TrackedFees):
   server,sessions=create_server(Path(d)/'cache',0,Path(d)/'config.cfg');sessions.nodes.health.closed=True
   thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   def request(path,method='GET',body=None,token=None):
    c=http.client.HTTPConnection('::1',server.server_port,timeout=5);headers={'Content-Type':'application/json'}
    if token:headers['X-Session-Token']=token
    c.request(method,path,body,headers);r=c.getresponse();data=json.loads(r.read());c.close();self.assertEqual(r.status,200,data);return data
   def ready():
    request('/api/fees?refresh=1');until=time.monotonic()+3
    while made[-1].refreshing and time.monotonic()<until:time.sleep(.005)
    result=request('/api/fees');self.assertEqual(len(result['rates']),4);self.assertFalse(made[-1].closed);return made[-1]
   try:
    original=ready()
    for endpoint in ('clear','load'):
     session=sessions.create()
     with patch('launch_wallaby_bitcoin_dashboard.ProfileArchive.load'),patch('launch_wallaby_bitcoin_dashboard.ProfileArchive.replace'):
      out=request('/api/profile/'+endpoint,'POST',b'{}',session.token)
     self.assertTrue(out['reset']);self.assertTrue(original.closed);self.assertIsNot(made[-1],original);self.assertIsNone(made[-1].data);self.assertFalse(made[-1].closed)
     original=ready()
    self.assertEqual(len(made),3)
   finally:made[-1].close();sessions.close_all();server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':unittest.main()
