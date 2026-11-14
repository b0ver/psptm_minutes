import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('recorder_hotfix',Path(__file__).parents[1]/'scripts/ops/recorder_hotfix_20261107.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_pin_changes_only_recorder_image_and_preserves_other_service():
    source='services:\r\n  api:\r\n    image: untouched\r\n  telemost-agent:\r\n    image: old\r\n    read_only: true\r\n  worker:\r\n    image: keep\r\n'
    assert module.pin_image(source)==source.replace('    image: old\r\n','    image: '+module.IMAGE+'\r\n')


def test_pin_rejects_nonimmutable_or_ambiguous_input():
    with pytest.raises(module.GateError):module.pin_image('services:\n  telemost-agent:\n    image: old\n','psptm-minutes:local')
    with pytest.raises(module.GateError):module.pin_image('services:\n  telemost-agent:\n    image: old\n    image: second\n')


def test_legacy_partial_is_preserved_but_new_or_active_state_blocks():
    expected=[{'id':'one','status':'partial_saved','state_sha256':'retained'}]
    module.validate_states(expected,expected)
    for current in ([{'id':'one','status':'recording','state_sha256':'retained'}], expected+[{'id':'new','status':'uploaded','state_sha256':'x'}], [{'id':'one','status':'partial_saved','state_sha256':'changed'}]):
        with pytest.raises(module.GateError):module.validate_states(expected,current)


def test_only_recorder_identity_may_change():
    expected={'api':{'id':'api-old','image':'same'},'telemost-agent':{'id':'recorder-old','image':'old'}}
    current={'api':{'id':'api-old','image':'same'},'telemost-agent':{'id':'recorder-new','image':'new'}}
    module.validate_identity(expected,current,recorder_may_change=True)
    current['api']['id']='api-recreated'
    with pytest.raises(module.GateError):module.validate_identity(expected,current,recorder_may_change=True)
