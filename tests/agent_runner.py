"""Run the real Agent in a separate interpreter so its policy modules stay isolated."""
import sys,threading,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import node_client
node_client.adapter_mac=lambda:'02:80:00:00:00:01'
node_client.socket.gethostname=lambda:'integration-node'
stop=threading.Event();signal=Path(sys.argv[5])
def watch():
    while not stop.wait(.05):
        if signal.exists():stop.set()
threading.Thread(target=watch,daemon=True).start()
client=node_client.NodeClient({'cookie':sys.argv[2],'rpc_port':int(sys.argv[3])},int(sys.argv[4]),stop,lambda *_:None)
try:client.run()
finally:stop.set()
