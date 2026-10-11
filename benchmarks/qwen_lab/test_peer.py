import unittest
from peer_review import validate_spans
class PeerTests(unittest.TestCase):
 def test_exact_quote_and_id_required(self):
  good={'claims':[{'verdict':'supported','source_spans':[{'segment_id':'L00001','quote':'источник'}]}],'missing_facts':[]}
  validate_spans(good,[{'text':'Полный источник'}])
  bad={'claims':[{'verdict':'supported','source_spans':[{'segment_id':'L99999','quote':'источник'}]}],'missing_facts':[]}
  with self.assertRaises(ValueError):validate_spans(bad,[{'text':'Полный источник'}])
 def test_missing_fact_without_evidence_rejected(self):
  with self.assertRaises(ValueError):validate_spans({'claims':[],'missing_facts':[{'text':'Выдумка','source_spans':[]}]},[{'text':'источник'}])
if __name__=='__main__':unittest.main()
