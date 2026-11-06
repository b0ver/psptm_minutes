import copy,json,unittest
from app.source_wire import pack,unpack,WireError

class LosslessSourceWire(unittest.TestCase):
 def row(self,ref='S1',text='Текст  е\u0308 中文 🙂\t\n'):
  return {'span_ref':ref,'source_id':'L1','start_char':5,'end_char':5+len(text),'start':1.125,'end':2.5,'text':text}
 def test_all_characters_times_and_optional_hint_presence_preserved(self):
  rows=[self.row('S1'),dict(self.row('S2'),speaker_hint=None),dict(self.row('S3'),speaker_hint='Synthetic hint')]
  self.assertEqual(unpack(json.loads(json.dumps(pack(rows),ensure_ascii=False))),rows)
  self.assertNotIn('speaker_hint',unpack(pack(rows))[0])
 def test_original_order_and_namespaces_not_coerced_or_merged(self):
  rows=[self.row('S2'),self.row('S1')];rows[0]['source_id']=1
  self.assertEqual(unpack(pack(rows)),rows)
 def test_unknown_metadata_refuses_instead_of_silent_omission(self):
  row=self.row();row['new_metadata']='retain-me'
  with self.assertRaises(WireError):pack([row])
 def test_duplicate_spans_and_clipped_offsets_refuse(self):
  row=self.row()
  with self.assertRaises(WireError):pack([row,row])
  row['end_char']-=1
  with self.assertRaises(WireError):pack([row])
 def test_header_and_truncated_rows_cannot_change_field_meaning(self):
  for mode in ('header','row'):
   packet=pack([self.row()])
   if mode=='header':packet['source_columns'][0]='source_id'
   else:packet['source_spans'][0].pop()
   with self.assertRaises(WireError):unpack(packet)
 def test_short_segments_have_smaller_wire_without_source_loss(self):
  rows=[self.row('S'+str(i),'Exact source word.') for i in range(30)]
  self.assertLess(len(json.dumps(pack(rows))),len(json.dumps({'source_spans':rows})))
  self.assertEqual(unpack(pack(rows)),rows)
 def test_empty_packet_cannot_be_coverage(self):
  with self.assertRaises(WireError):pack([])
