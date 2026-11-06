import copy,json,unittest
from unittest.mock import Mock
from app.reports import validate_json
from app.source_act_inventory import discover_window,InventoryError

class SourceOnlyDiscovery(unittest.TestCase):
 def setUp(self):
  self.window=[{'source_id':'L9','span_ref':'S12','start_char':10,'end_char':32,'text':'Отменяем работу Альфа. '}]
  self.window[0]['end_char']=10+len(self.window[0]['text'])
  self.value={'source_reading':'complete','acts':[{'span_ref':'S12','quote':'Отменяем работу Альфа.','roles':['cancellation']}]}
  self.client=Mock();self.client.native_calls=0;self.client.chat.return_value=self.value
  def chat(*args):
   self.client.native_calls+=1
   return self.client.chat.return_value
  self.client.chat.side_effect=chat
 def run_window(self,**kw):return discover_window(self.window,self.client,validate_json,lambda *x:True,kw.get('used',self.client.native_calls))
 def test_cancellation_is_inventory_not_active_and_packet_has_no_candidate(self):
  value=self.run_window();self.assertEqual(value['source_acts'][0]['roles'],['cancellation'])
  self.assertEqual(value['source_acts'][0]['start_char'],10)
  self.assertFalse(value['action_quality_accepted']);self.assertTrue(value['candidate_matching_still_required'])
  self.assertEqual(set(json.loads(self.client.chat.call_args.args[1])),{'source_spans'})
 def test_no_mentions_is_not_proof_of_absence_or_action_acceptance(self):
  self.value['acts']=[];value=self.run_window()
  self.assertFalse(value['absence_accepted']);self.assertTrue(value['independent_window_review_still_required'])
 def test_forged_or_repeated_quote_or_foreign_span_ref_is_refused(self):
  for mode in ('forged','duplicate','foreign'):
   value=copy.deepcopy(self.value)
   if mode=='forged':value['acts'][0]['quote']='Invented unrelated work'
   elif mode=='duplicate':value['acts'].append(copy.deepcopy(value['acts'][0]))
   else:value['acts'][0]['span_ref']='A00001'
   self.client.chat.return_value=value
   with self.assertRaises(InventoryError):self.run_window()
 def test_uncertain_reading_or_role_is_never_complete(self):
  for mode in ('reading','role'):
   v=copy.deepcopy(self.value)
   if mode=='reading':v['source_reading']='uncertain'
   else:v['acts'][0]['roles']=['uncertain']
   self.client.chat.return_value=v
   with self.assertRaises(InventoryError):self.run_window()
 def test_global_budget_and_actual_packet_fit_refuse_before_model_call(self):
  for used,fits in ((64,lambda *x:True),(0,lambda *x:False)):
   with self.assertRaises(InventoryError):discover_window(self.window,self.client,validate_json,fits,used)
  self.client.chat.assert_not_called()
 def test_source_coordinates_cannot_be_silently_clipped(self):
  self.window[0]['end_char']-=1
  with self.assertRaises(InventoryError):self.run_window()
  self.client.chat.assert_not_called()

 def test_act_refs_from_different_source_packets_cannot_share_local_index(self):
  first=self.run_window()['source_acts'][0]['source_act_ref']
  self.window[0]['span_ref']='S13';self.value['acts'][0]['span_ref']='S13'
  second=self.run_window()['source_acts'][0]['source_act_ref']
  self.assertNotEqual(first,second)

 def test_actual_retries_consume_shared_native_counter(self):
  def retries(*args):
   self.client.native_calls+=2
   return self.value
  self.client.chat.side_effect=retries
  self.assertEqual(self.run_window()['calls_consumed'],2)
 def test_forged_or_nonincrementing_native_counter_refuses(self):
  self.client.chat.side_effect=lambda *args:self.value
  with self.assertRaises(InventoryError):self.run_window()
