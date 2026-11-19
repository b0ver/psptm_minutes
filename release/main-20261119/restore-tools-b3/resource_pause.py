"""Pause only an owned restore until two fresh resource admissions pass."""
import time
class ResourceDenied(RuntimeError):
 def __init__(self,code,sample):super().__init__(code);self.sample=sample

def wait_for_admission(guard,nas_check,pause,resume,lease,deadline,clock=time.monotonic,sleep=time.sleep,alive=lambda:None):
 pause();lease(False,None);passes=0
 while passes<2:
  if clock()>deadline:raise RuntimeError('resource_pause_deadline')
  alive();nas_check();lease(False,None);sleep(5)
  try:sample=guard();passes+=1
  except ResourceDenied:passes=0
 lease(True,sample);resume();return sample
