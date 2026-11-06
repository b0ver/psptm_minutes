"""The role clarification must not change executable gates or schema contracts."""
import ast
import hashlib
import importlib
import json
import unittest
from pathlib import Path
from app.source_act_inventory import ROLE_RULES, SYSTEM
from app.status_scan import SCAN_SYSTEM, REVIEW_SYSTEM

EXPECTED = {'source_act_inventory': '002b46e19fcb3e47441ccfdd0ec2dddec746b50b649e6cf52aabf00fa7b86e2e', 'status_scan': 'a41eea31fb85dc1f2101947b1bbc7ffdc99785dcc3a7724839520f71b9cc7d17', 'status_packets': '44b82ca937d23853b1227452716d2d293b0cadc1742e3e22517d5084cc5fd343', 'status_finalize': '7249f4d9b5aafcfc6fdc2d062cd25a0050070d34f39d4d6e85fffa23ec959262', 'source_wire': '3953aa74250738e25e16e548b6c2da84b6d12602e704baa5ebbe156188a8c42d', 'action_status': '7139a4b539b6a8f9082104e857805d6f4b40a00bbd0a7f7eb21f41f091d18617', 'reports': '87af817be45cd583574a5c1ecb6de3101fe9f597ff638a31da71dddf92172f40'}

class SourceRoleContract(unittest.TestCase):
    def test_all_executable_functions_and_classes_match_exact_v9_predecessor(self):
        for name, expected in EXPECTED.items():
            module = importlib.import_module('app.' + name)
            text = Path(module.__file__).read_text()
            tree = ast.parse(text)
            executable = [ast.get_source_segment(text, node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
            self.assertEqual(hashlib.sha256(json.dumps(executable, ensure_ascii=False).encode()).hexdigest(), expected, name)

    def test_all_three_stages_share_explicit_work_completion_and_commitment_definitions(self):
        for prompt in [SYSTEM, SCAN_SYSTEM, REVIEW_SYSTEM]:
            self.assertIn(ROLE_RULES, prompt)
        self.assertIn('фактически выполненной работе', ROLE_RULES)
        self.assertIn('обещание не доказывают completion', ROLE_RULES)
        self.assertIn('не заменяй сомнение набором возможных ролей', ROLE_RULES)
        self.assertIn('отдельное свидетельство поручения', ROLE_RULES)

    def test_original_role_enums_schema_and_final_state_rules_are_unchanged(self):
        names = {'CLASSIFY_SCHEMA', 'SCHEMA', 'SUPPORT', 'REVIEW_SCHEMA', 'CHECK_SCHEMA', 'ROLES', 'STATES', 'SCAN_SCHEMA', 'ROW', 'STATE_RULES', 'REVIEW_ROW'}
        expected = {'source_act_inventory': '65ee049c1582866c389774f6c393f7671eeb61e0ae598b6ff21c98ed6a843e3f', 'status_scan': '82d21c8feb558836fdeacc0866fb15d7670d1f22876a09633f8e9727cf18543b', 'action_status': 'c902030131f85efc684ef5c2a20f2e4be40c1960a18d129d4f7846098b83a162'}
        for name, value in expected.items():
            module = importlib.import_module('app.' + name)
            text = Path(module.__file__).read_text()
            tree = ast.parse(text)
            selected = [ast.get_source_segment(text, n) for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
            self.assertEqual(hashlib.sha256(json.dumps(selected, ensure_ascii=False).encode()).hexdigest(), value)
