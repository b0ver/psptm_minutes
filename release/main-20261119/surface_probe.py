"""Offline native inventory for applicability review. No models, DB or user data."""
import argparse,asyncio,importlib.metadata,json,mmap,os,re,ssl,struct,subprocess,sys,tempfile
from pathlib import Path
WANTED=re.compile(r'(?:^|_)(?:regcomp|regexec|regfree|re_compile_pattern|wscanf|fwscanf|swscanf|vfwscanf|vswscanf|vwscanf|CMS_encrypt|PEM_X509_INFO_read|curl_easy_perform|RTMP_Read)(?:$|_)')

def undefined_symbols(path):
 with Path(path).open('rb') as file:
  if file.read(6)!=b'\x7fELF\x02\x01':return []
  with mmap.mmap(file.fileno(),0,access=mmap.ACCESS_READ) as data:
   off=struct.unpack_from('<Q',data,40)[0];size,count=struct.unpack_from('<HH',data,58)
   if size<64 or off+size*count>len(data):raise RuntimeError('invalid_elf_sections')
   sections=[struct.unpack_from('<IIQQQQIIQQ',data,off+i*size) for i in range(count)];found=set()
   for section in sections:
    if section[1]!=11:continue
    offset,length,link,entry=section[4],section[5],section[6],section[9]
    if entry!=24 or link>=count or offset+length>len(data):raise RuntimeError('invalid_elf_symbols')
    strings=sections[link];start,end=strings[4],strings[4]+strings[5]
    for at in range(offset,offset+length,entry):
     name,_,_,index,_,_=struct.unpack_from('<IBBHQQ',data,at)
     if index or not name:continue
     pos=start+name
     if pos>=end:raise RuntimeError('invalid_elf_symbol_name')
     stop=data.find(b'\0',pos,end)
     if stop<0:raise RuntimeError('invalid_elf_string')
     value=data[pos:stop].decode('ascii',errors='replace')
     if WANTED.search(value):found.add(value)
   return sorted(found)

def paths(pid):
 result=set()
 for line in Path('/proc',str(pid),'maps').read_text().splitlines():
  parts=line.split(maxsplit=5)
  if len(parts)==6 and parts[5].startswith('/'):
   p=parts[5].removesuffix(' (deleted)')
   if Path(p).is_file():result.add(p)
 return result

def inventory():
 import av
 for name in ("cryptography","requests","defusedxml"):
  try:__import__(name)
  except ModuleNotFoundError:pass
 rows=subprocess.check_output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'],text=True).splitlines()
 packages=[line.split('\t') for line in rows]
 result={'machine':__import__('platform').machine(),'uid':os.getuid(),'python':sys.version.split()[0],'openssl_python':ssl.OPENSSL_VERSION,'default_tls_minimum':ssl.create_default_context().minimum_version.name,'packages':packages,'python_packages':sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions()),'pyav_libraries':av.library_versions,'gpu_devices_present':bool(list(Path('/dev').glob('nvidia*')))}
 assert result['machine']=='x86_64' and result['uid']==10001 and not result['gpu_devices_present']
 p=subprocess.run(['openssl','list','-cipher-algorithms'],capture_output=True,text=True)
 result['system_openssl_cipher_query_succeeded']=p.returncode==0
 result['system_openssl_aes_gcm_siv_listed']='GCM-SIV' in p.stdout
 return result,paths(os.getpid())

async def recorder():
 sys.path.insert(0,os.getcwd())
 from playwright.async_api import async_playwright
 from browser_session import launch_session
 with tempfile.TemporaryDirectory(prefix='minutes-surface-') as tmp:
  async with async_playwright() as p:
   context=await launch_session(p.chromium,Path(tmp)/'session')
   try:
    page=context.pages[0];cdp=await context.new_cdp_session(page)
    command=(await cdp.send('Browser.getBrowserCommandLine'))['arguments']
    expected=['--disable-background-networking','--disable-breakpad','--disable-component-update','--disable-sync','--no-first-run']
    flags={flag:flag in command for flag in expected};assert all(flags.values()),'missing_browser_privacy_switch'
    mapped=set()
    for proc in Path('/proc').iterdir():
     if not proc.name.isdecimal():continue
     try:
      if proc.stat().st_uid!=10001:continue
      cmd=(proc/'cmdline').read_bytes().split(b'\0')[0]
      if cmd.startswith(b'/opt/google/chrome/') or cmd.startswith(b'/usr/bin/chromium'):mapped.update(paths(proc.name))
     except (FileNotFoundError,ProcessLookupError,PermissionError):continue
    policy=json.loads(Path('/etc/opt/chrome/policies/managed/minutes-privacy.json').read_text())
    assert policy.get('BrowserSignin')==0 and policy.get('SyncDisabled') is True and policy.get('MetricsReportingEnabled') is False and policy.get('CloudReportingEnabled') is False
    assert not Path('/opt/google/chrome/WidevineCdm').exists()
    version=await cdp.send('Browser.getVersion')
    assert version['product'].endswith('/158.0.8127.36')
    return {'browser_version':version['product'],'privacy_switches':flags,'managed_privacy_policy':policy,'widevine_present':False},mapped
   finally:await context.close()

def main(kind):
 result,mapped=inventory()
 if kind=='telemost':
  browser,more=asyncio.run(recorder());result.update(browser);mapped.update(more)
 else:
  import faster_whisper,ctranslate2
  mapped.update(paths(os.getpid()))
 findings=[]
 for path in sorted(mapped):
  try:symbols=undefined_symbols(path)
  except (OSError,ValueError,struct.error):continue
  if symbols:findings.append({'path':path,'undefined_symbols':symbols})
 result.update({'kind':kind,'mapped_elf_paths':sorted(mapped),'selected_undefined_symbol_findings':findings,'models_loaded':False,'network':'none','production_data_mounted':False})
 print('MINUTES_SURFACE_JSON='+json.dumps(result,sort_keys=True))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('kind',choices=['runtime','gpu','telemost']);main(p.parse_args().kind)
