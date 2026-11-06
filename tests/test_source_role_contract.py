"""The role clarification must not change executable gates or schema contracts."""
import ast
import hashlib
import importlib
import json
import unittest
from pathlib import Path
from app.source_act_inventory import ROLE_RULES
from app.source_unit_protocol import SYSTEM
from app.status_scan import SCAN_SYSTEM, REVIEW_SYSTEM

ALLOWED_PROTOCOL_FUNCTIONS = {'status_scan': ['scan_all_windows'], 'status_packets': ['prepare'], 'status_finalize': ['finalize'], 'action_status': ['classify_actions']}
EXPECTED = {'source_act_inventory': '002b46e19fcb3e47441ccfdd0ec2dddec746b50b649e6cf52aabf00fa7b86e2e', 'status_scan': 'cacd05696d6bf7b0969ca3051f4d432c7a68845661e0d17940d3c78c5e66780c', 'status_packets': '55930422787a428199a272fb3c71462ed0924982b2bdb9d18240ddaf72d8348b', 'status_finalize': 'a8a28e5a45fcee00bbb295ac59009fb5ed366d6c42cfb507945116fa058c563d', 'source_wire': '3953aa74250738e25e16e548b6c2da84b6d12602e704baa5ebbe156188a8c42d', 'action_status': '4fea43b477acc0abf81aea83a0b1b576ee90c8fb9dd0e022eb14f8c46e6b38ba', 'reports': '87af817be45cd583574a5c1ecb6de3101fe9f597ff638a31da71dddf92172f40'}

class SourceRoleContract(unittest.TestCase):
    def test_unchanged_acceptance_functions_and_client_match_exact_v11_predecessor(self):
        for name, expected in EXPECTED.items():
            module = importlib.import_module('app.' + name)
            text = Path(module.__file__).read_text()
            tree = ast.parse(text)
            executable = [ast.get_source_segment(text, node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in ALLOWED_PROTOCOL_FUNCTIONS.get(name, [])]
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
