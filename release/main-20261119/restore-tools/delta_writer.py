"""Guard-leased, single-writer tar packaging. Run ONLY in isolated backup container.
No secrets. Private plans/receipts/output stay outside repository.
"""
import argparse, fcntl, hashlib, json, os, shutil, stat, tarfile, time
from pathlib import Path
from plan import canonical, digest, entries

class Stopped(Exception): pass

class Guard:
    """Own restore only: host monotonic lease plus boot identity, no wall-clock TTL."""
    def __init__(self,out,rate=8*1024*1024,reserve=60*1024**3):
        self.out=Path(out);self.rate=rate;self.reserve=reserve;self.next_read=time.monotonic();self.last_check=0
    def check(self):
        if time.monotonic()-self.last_check<0.5:return
        try:lease=json.loads(Path(os.environ['BACKUP_LEASE_PATH']).read_text())
        except Exception:raise Stopped('lease_unreadable') from None
        now=time.monotonic();issued=lease.get('monotonic_time');age=now-issued if isinstance(issued,(float,int)) else None
        if lease.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():raise Stopped('lease_boot_mismatch')
        if lease.get('allowed') is not True:raise Stopped('lease_denied')
        if age is None or not 0<=age<=30:
            error=Stopped('lease_expired');error.safe_details={'lease_age_seconds':age};raise error
        free=shutil.disk_usage(self.out).free
        if free<self.reserve:
            error=Stopped('low_space');error.safe_details={'free_bytes':free,'reserve_bytes':self.reserve};raise error
        self.last_check=now
    def pace(self,n):
        self.check();until=max(self.next_read,time.monotonic())+n/self.rate
        while time.monotonic()<until:
            time.sleep(min(.1,max(0,until-time.monotonic())));self.check()
        self.next_read=until

class Reader:
    def __init__(self, f, guard): self.f=f; self.guard=guard; self.hash=hashlib.sha256()
    def read(self, n):
        self.guard.pace(n); data=self.f.read(n); self.hash.update(data); return data

class Sink:
    def __init__(self, f, guard): self.f=f; self.guard=guard; self.hash=hashlib.sha256(); self.bytes=0
    def write(self, b): self.guard.pace(len(b)); self.hash.update(b); self.bytes+=len(b); return self.f.write(b)
    def tell(self): return self.bytes


def source_fd(root_fd, name):
    parts=name.split('/'); fd=os.dup(root_fd)
    try:
        for part in parts[:-1]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=new
        return os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    finally: os.close(fd)


def matches(s,row):
    return [s.st_mode,s.st_size,s.st_mtime_ns,None] == row[1:] and stat.S_ISREG(s.st_mode)


def atomic(path, value):
    p=Path(str(path)+'.tmp')
    with open(p,'xb') as f: f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
    os.replace(p,path)
    fd=os.open(Path(path).parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def verify_archive(path, receipt, guard):
    h=hashlib.sha256(); size=0
    with open(path,'rb') as f:
        while True:
            guard.pace(1024*1024); b=f.read(1024*1024)
            if not b: break
            h.update(b); size+=len(b)
    if h.hexdigest()!=receipt['archive_sha256'] or size!=receipt['archive_bytes']:
        raise Stopped('completed_archive_integrity_mismatch')


def package(root, out, package, plan_hash, guard):
    idx=package['index']; base=Path(out)/f'part-{idx:06d}'
    archive=Path(str(base)+'.tar'); receipt=Path(str(base)+'.json')
    if receipt.exists():
        r=json.loads(receipt.read_text())
        if r['plan_sha256']!=plan_hash or r['selection_sha256']!=package['selection_sha256']:
            raise Stopped('receipt_binding_mismatch')
        verify_archive(archive,r,guard); return 'verified_existing'
    if archive.exists(): raise Stopped('archive_without_receipt')
    partial=Path(str(base)+'.partial')
    # A partial is never silently reused or overwritten. Explicit operator handling required.
    rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        hashes=[]
        with open(partial,'xb') as raw:
            os.chmod(partial,0o600); sink=Sink(raw,guard)
            with tarfile.open(fileobj=sink,mode='w|',format=tarfile.PAX_FORMAT) as tf:
                for row in package['rows']:
                    guard.check(); fd=source_fd(rootfd,row[0])
                    with os.fdopen(fd,'rb') as source:
                        before=os.fstat(source.fileno())
                        if not matches(before,row): raise Stopped('source_changed_before_read')
                        info=tarfile.TarInfo(row[0]); info.size=row[2]; info.mode=stat.S_IMODE(row[1]); info.mtime=row[3]/1e9
                        reader=Reader(source,guard); tf.addfile(info,reader)
                        after=os.fstat(source.fileno())
                        if not matches(after,row) or (before.st_ino,before.st_dev,before.st_ctime_ns)!=(after.st_ino,after.st_dev,after.st_ctime_ns):
                            raise Stopped('source_changed_during_read')
                        hashes.append([row[0],reader.hash.hexdigest()])
            raw.flush(); os.fsync(raw.fileno())
        guard.check(); os.rename(partial,archive)
        atomic(receipt,{'schema':1,'plan_sha256':plan_hash,'selection_sha256':package['selection_sha256'],
                        'archive_sha256':sink.hash.hexdigest(),'archive_bytes':sink.bytes,'file_sha256':hashes})
        return 'created'
    finally: os.close(rootfd)


def run(root,out,plan_path):
    os.umask(0o077); out=Path(out)
    with open(out/'writer.lock','a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        p=json.loads(Path(plan_path).read_text()); claimed=p.pop('plan_sha256')
        if digest(p)!=claimed: raise Stopped('plan_hash_mismatch')
        p['plan_sha256']=claimed
        # Validate path/type selections independent of planner.
        rows=p['directories']+[r for pack in p['packages'] for r in pack['rows']]
        # Ancestors may be unchanged and not selected; validate names/types individually,
        # full parent topology was validated in planner. source_fd never follows links.
        for row in rows:
            name=row[0]
            if not name or name.startswith('/') or any(x in ('','.','..') for x in name.split('/')) or '\x00' in name:
                raise Stopped('unsafe_path')
            if row[4] is not None or not (stat.S_ISREG(row[1]) or stat.S_ISDIR(row[1])): raise Stopped('unsafe_type')
        guard=Guard(out); guard.check()
        for pack in p['packages']:
            if digest(pack['rows'])!=pack['selection_sha256']: raise Stopped('selection_hash_mismatch')
            result=package(root,out,pack,claimed,guard)
            print(json.dumps({'package':pack['index'],'result':result}),flush=True)
        atomic(out/'archive-set-complete.json',{'plan_sha256':claimed,'packages':len(p['packages']),'status':'ARCHIVED_NOT_OFFHOST_OR_RESTORE_VERIFIED'})

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('root'); parser.add_argument('out'); parser.add_argument('plan')
    a=parser.parse_args()
    try: run(a.root,a.out,a.plan)
    except Exception as e:
        # Do not expose source filenames/contents via exception text.
        print(json.dumps({'status':'STOPPED','reason':str(e) if isinstance(e,Stopped) else type(e).__name__}),flush=True)
        raise SystemExit(1)
