"""Exact editable heights keep the calendar model and future-height guards."""
import unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from vault_estimator import estimate_height,poisson_cdf_left,from_nodes

class EditableVaultHeight(unittest.TestCase):
    def test_exact_height_mean_and_ninety_nine_percent_crossing(self):
        now=datetime(2026,11,4,tzinfo=timezone.utc)
        for distance in (1,6,100,10000,500000):
            r=estimate_height(974000,974000+distance,now)
            self.assertEqual(r['height'],974000+distance)
            self.assertEqual(datetime.fromisoformat(r['most_likely_utc']),now+timedelta(seconds=distance*600))
            seconds=(datetime.fromisoformat(r['target_utc'])-now).total_seconds()
            self.assertGreater(seconds,distance*600)
            self.assertLessEqual(poisson_cdf_left(distance,seconds/600),.0100000001)
            self.assertGreater(poisson_cdf_left(distance,(seconds-.01)/600),.01)
            self.assertTrue(r['manual_height'])
    def test_rejects_non_integer_past_timestamp_and_excessively_distant_heights(self):
        for height in (None,True,'974001',974001.0,0,-1,973999,974000,500000000,974000+6000000):
            with self.assertRaises(ValueError):estimate_height(974000,height)
    def test_node_estimate_uses_selected_height_not_the_calendar_date(self):
        info={'chain':'main','initialblockdownload':False,'blocks':974000,'headers':974000}
        registry=SimpleNamespace(snapshot=lambda:{'nodes':[{'id':'node','hostname':'Example','connected':True,'rpc_ready':True}]},rpc=SimpleNamespace(call=lambda *a,**k:{'state':'succeeded','result':info}))
        r=from_nodes(registry,None,'Australia/Darwin',datetime(2026,11,4,tzinfo=timezone.utc),selected_height=974006)
        self.assertEqual(r['height'],974006);self.assertEqual(r['target_timezone'],'Australia/Darwin');self.assertEqual(r['source'],'Example')
        with self.assertRaises(ValueError):from_nodes(registry,None,'bad/timezone',selected_height=974006)
        info['blocks']=974006;info['headers']=974006
        with self.assertRaisesRegex(ValueError,'above the current'):from_nodes(registry,None,'UTC',selected_height=974006)
