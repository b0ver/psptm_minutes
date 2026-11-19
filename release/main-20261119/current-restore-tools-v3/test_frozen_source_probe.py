import copy, unittest
from frozen_source_probe import validate_frozen, ResourceDenied

class FrozenSourceTests(unittest.TestCase):
    def setUp(self):
        self.base={role:{'Id':role, 'Image':'image-'+role} for role in ['api','telegram','calendar-scheduler','worker','analysis-worker','telemost-agent','db']}
        self.current={role:{**v, 'State':{'Running':role=='db','Paused':False,'Restarting':False}} for role,v in self.base.items()}
    def test_complete_frozen_set_passes(self):
        self.assertTrue(validate_frozen(self.base,self.current))
    def test_every_running_application_blocks(self):
        for role in self.base.keys()-{'db'}:
            with self.subTest(role=role), self.assertRaises(ResourceDenied):
                rows=copy.deepcopy(self.current); rows[role]['State']['Running']=True
                validate_frozen(self.base,rows)
    def test_database_must_stay_running(self):
        self.current['db']['State']['Running']=False
        with self.assertRaises(ResourceDenied):validate_frozen(self.base,self.current)
    def test_replacement_identity_or_image_blocks(self):
        for field in ['Id','Image']:
            with self.subTest(field=field),self.assertRaises(ResourceDenied):
                rows=copy.deepcopy(self.current);rows['api'][field]='replacement'
                validate_frozen(self.base,rows)
    def test_missing_service_blocks(self):
        del self.current['worker']
        with self.assertRaises(ResourceDenied):validate_frozen(self.base,self.current)
    def test_paused_or_restarting_container_blocks(self):
        for field in ['Paused','Restarting']:
            with self.subTest(field=field),self.assertRaises(ResourceDenied):
                rows=copy.deepcopy(self.current);rows['api']['State'][field]=True
                validate_frozen(self.base,rows)

if __name__=='__main__':unittest.main()
