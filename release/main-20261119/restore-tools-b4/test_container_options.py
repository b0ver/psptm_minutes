import unittest
from container_options import isolated_run_options

class BinaryLoggingTests(unittest.TestCase):
 def test_raw_archive_has_no_daemon_log(self):
  options=isolated_run_options('minutes-restore-20261119-b4')
  self.assertEqual(options[options.index('--log-driver')+1],'none')
  self.assertEqual(options[options.index('--network')+1],'none')
  self.assertEqual(options[options.index('--label')+1],'minutes.restore.owner=minutes-restore-20261119-b4')
 def test_unowned_token_denied(self):
  with self.assertRaises(ValueError):isolated_run_options('production')

 def test_daemon_binary_logging_denied(self):
  from container_options import assert_no_binary_log
  good={'Config':{'Labels':{'minutes.restore.owner':'owned'}},'Image':'sha256:test','HostConfig':{'LogConfig':{'Type':'none'}},'LogPath':''}
  assert_no_binary_log(good,'owned','sha256:test')
  good['HostConfig']['LogConfig']['Type']='json-file'
  with self.assertRaisesRegex(ValueError,'binary_stream_logging_enabled'):assert_no_binary_log(good,'owned','sha256:test')
 def test_foreign_stream_denied(self):
  from container_options import assert_no_binary_log
  wrong={'Config':{'Labels':{'minutes.restore.owner':'foreign'}},'Image':'sha256:test','HostConfig':{'LogConfig':{'Type':'none'}},'LogPath':''}
  with self.assertRaisesRegex(ValueError,'unowned_binary_stream'):assert_no_binary_log(wrong,'owned','sha256:test')
