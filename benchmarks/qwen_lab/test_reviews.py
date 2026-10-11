import unittest
from score_reviews import score
class ReviewTests(unittest.TestCase):
 def base(self):return {'case_id':'M01','template':'protocol','profile':'p1','gold_status':'adjudicated','review_status':'adjudicated','expected':[{'id':'g1'}],'claims':[]}
 def test_no_claims_does_not_pass_as_perfect(self):
  r=score(self.base());self.assertIsNone(r['precision']);self.assertEqual(r['recall'],0);self.assertFalse(r['passes_factual_gate'])
 def test_duplicate_matches_do_not_inflate_recall(self):
  r=self.base();r['claims']=[{'verdict':'supported','category':'decision','matched_expected_ids':['g1'],'source_spans':['L1']}]*2
  self.assertEqual(score(r)['recall'],1)
 def test_critical_error_blocks_even_high_precision(self):
  r=self.base();r['claims']=[{'verdict':'supported','category':'topic','matched_expected_ids':['g1'],'source_spans':['L1']}]*99+[{'verdict':'unsupported','category':'deadline'}]
  self.assertFalse(score(r)['passes_factual_gate']);self.assertEqual(score(r)['precision'],0.99)
 def test_unreviewed_gold_cannot_select(self):
  r=self.base();r['gold_status']='unreviewed';self.assertFalse(score(r)['eligible_for_selection'])
 def test_unknown_match_rejected(self):
  r=self.base();r['claims']=[{'verdict':'supported','category':'topic','matched_expected_ids':['wrong'],'source_spans':['L1']}]
  with self.assertRaises(ValueError):score(r)
 def test_forged_source_quote_rejected(self):
  r=self.base();r['claims']=[{'verdict':'supported','category':'topic','matched_expected_ids':['g1'],'source_spans':[{'segment_id':'L00001','quote':'invented'}]}]
  with self.assertRaises(ValueError):score(r,[{'text':'real'}])
 def test_source_is_required_for_selection(self):
  r=self.base();r['claims']=[{'verdict':'supported','category':'decision','matched_expected_ids':['g1'],'source_spans':[{'segment_id':'L00001','quote':'real'}]}]
  self.assertFalse(score(r)['eligible_for_selection']);self.assertTrue(score(r,[{'text':'real'}])['passes_factual_gate'])
if __name__=='__main__':unittest.main()
