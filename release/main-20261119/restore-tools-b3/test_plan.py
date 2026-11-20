import unittest
from plan import plan
D = ['dir', 0o40700, 0, 1, None]
F = ['dir/file', 0o100600, 10, 1, None]
class Tests(unittest.TestCase):
    def test_unchanged(self):
        self.assertEqual(plan([D,F], [D,F])['packages'], [])
    def test_changed_and_deleted(self):
        n = ['dir/new', 0o100600, 20, 1, None]
        p=plan([D,F],[D,n]); self.assertEqual(p['deleted'], ['dir/file']); self.assertEqual(p['logical_delta_bytes'],20)
    def test_deterministic(self):
        self.assertEqual(plan([], [D,F]),plan([], [D,F]))
    def test_pack_limit_large_single(self):
        rows=[D]+[[f'dir/{i}',0o100600,n,1,None] for i,n in enumerate([8,8,20,1])]
        self.assertEqual([p['logical_bytes'] for p in plan([],rows,10)['packages']],[8,8,20,1])
    def test_traversal(self):
        for name in ['../bad','/bad','a/../bad','./bad','a//b','a/','a\x00b']:
            with self.subTest(name=name), self.assertRaises(ValueError): plan([],[[name,0o100600,1,1,None]])
    def test_link_and_special(self):
        for mode,target in [(0o120777,'/tmp'),(0o010600,None),(0o020600,None)]:
            with self.subTest(mode=mode), self.assertRaises(ValueError): plan([],[['x',mode,0,1,target]])
    def test_missing_parent(self):
        with self.assertRaises(ValueError): plan([],[F])
    def test_file_parent(self):
        with self.assertRaises(ValueError): plan([],[['dir',0o100600,1,1,None],F])
    def test_duplicate(self):
        with self.assertRaises(ValueError): plan([],[D,D])
    def test_type_change(self):
        self.assertEqual(len(plan([['dir',0o100600,1,1,None]], [D,F])['directories']),1)
    def test_bad_stat(self):
        with self.assertRaises(ValueError): plan([],[['x',0o100600,-1,1,None]])
    def test_bad_limit(self):
        with self.assertRaises(ValueError): plan([],[],0)
if __name__=='__main__': unittest.main()
