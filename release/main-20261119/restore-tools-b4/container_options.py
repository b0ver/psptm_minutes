"""Attach binary streams without retaining a Docker logging copy."""
import re

def isolated_run_options(token):
 if not re.fullmatch(r'minutes-restore-[a-z0-9-]+',token):raise ValueError('owned_restore_token_required')
 return ['--rm','--log-driver','none','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--label','minutes.restore.owner='+token]

def assert_no_binary_log(container,token,image):
 if container['Config'].get('Labels',{}).get('minutes.restore.owner')!=token or container['Image']!=image:raise ValueError('unowned_binary_stream')
 if container['HostConfig']['LogConfig']['Type']!='none' or container.get('LogPath'):raise ValueError('binary_stream_logging_enabled')
