"""Read-only admission after all six original application containers stop.

Never admits a different container, a running application, an active agent state,
or processing queues. PostgreSQL stays running. No production writes.
"""
import json, shutil, subprocess
from pathlib import Path
from production_probe import AGENT_CODE, SQL, ResourceDenied, probe_json, pressure, validate_resources

B = Path('/home/epopov/projects/psptm_minutes-ops/main-release-20261119')

def validate_frozen(baseline, current):
    if set(current) != set(baseline):
        raise ResourceDenied('frozen_service_inventory', {})
    for role, previous in baseline.items():
        actual = current[role]
        if actual['Id'] != previous['Id'] or actual['Image'] != previous['Image']:
            raise ResourceDenied('frozen_container_identity', {'role': role})
        state = actual['State']
        if bool(state.get('Running')) != (role == 'db') or state.get('Paused') or state.get('Restarting'):
            raise ResourceDenied('frozen_container_state', {'role': role})
    return True

def observe_frozen(canonical, root, target, run=subprocess.run):
    details = validate_resources(canonical, pressure(), shutil.disk_usage(root).free, shutil.disk_usage(target).free)
    baseline = json.loads((B/'baseline-metadata/running-services.json').read_text())['services']
    raw = probe_json(['docker', 'inspect', *[r['Id'] for r in baseline.values()]], 'frozen_inventory', 10, run)
    by_id = {r['Id']: r for r in raw}
    current = {role: by_id.get(r['Id'], {}) for role, r in baseline.items()}
    try:
        validate_frozen(baseline, current)
    except (KeyError, TypeError):
        raise ResourceDenied('frozen_inventory_invalid', {}) from None
    image = json.loads((B/'registry-images/runtime-installed.json').read_text())['registry_ref']
    agent = probe_json(['docker', 'run', '--rm', '--log-driver', 'none', '--runtime', 'runc',
        '--network', 'none', '--read-only', '--cpus', '.1', '--memory', '128m', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--volumes-from', baseline['api']['Id']+':ro',
        '--entrypoint', 'python', image, '-c', AGENT_CODE], 'frozen_agent_states', 15, run)
    if set(agent) != {'total', 'blocked'} or any(type(v) is not int or v < 0 for v in agent.values()) or agent['blocked']:
        raise ResourceDenied('frozen_agent_work_or_invalid', {})
    queues = probe_json(['docker', 'exec', '-e', 'PGOPTIONS=-c statement_timeout=2000 -c default_transaction_read_only=on',
        baseline['db']['Id'], 'psql', '-qAt', '-U', 'minutes', '-d', 'minutes', '-c', SQL], 'frozen_processing', 8, run)
    if set(queues) != {'meetings', 'analysis', 'reports'} or any(type(v) is not int or v < 0 for v in queues.values()) or sum(queues.values()):
        raise ResourceDenied('frozen_queue_work_or_invalid', {})
    # Close the observation interval against an application restart.
    raw2 = probe_json(['docker', 'inspect', *[r['Id'] for r in baseline.values()]], 'frozen_recheck', 10, run)
    ids2 = {r['Id']:r for r in raw2}
    validate_frozen(baseline, {role:ids2.get(r['Id'], {}) for role,r in baseline.items()})
    return {**details, 'agent':agent, 'processing':queues, 'all_original_applications_stopped':True}
