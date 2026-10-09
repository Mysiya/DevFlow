"""Bounded HTTPS range transfers; callers verify the complete pinned digest."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess

BLOCK=1024*1024


def download_ranges(curl, url, expected, partial, total, seed=None):
    parts=partial.parent/(expected[:12]+'.parts');parts.mkdir(parents=True,exist_ok=True)
    bounds=[(start,min(start+BLOCK,total)-1) for start in range(0,total,BLOCK)]
    # Keep complete ranges from the previous single-stream download.
    if seed is not None and seed.is_file():
        with seed.open('rb') as source:
            for start,end in bounds:
                raw=source.read(end-start+1)
                if len(raw)!=end-start+1:break
                piece=parts/str(start)
                if not piece.exists() or piece.stat().st_size!=len(raw):piece.write_bytes(raw)

    def fetch(bound):
        start,end=bound;piece=parts/str(start);length=end-start+1
        if piece.exists() and piece.stat().st_size==length:return length
        for attempt in range(3):
            result=subprocess.run([curl,'--fail','--location','--silent','--show-error',
                '--proto','=https','--proto-redir','=https','--connect-timeout','20',
                '--max-time','120','--speed-limit','1024','--speed-time','45',
                '--range',f'{start}-{end}','--output',str(piece),'--write-out','%{http_code}',url],
                capture_output=True,text=True,timeout=125)
            if result.returncode==0 and result.stdout.strip()=='206' and piece.stat().st_size==length:return length
        raise RuntimeError(f'HTTPS range download failed at byte {start}; completed ranges retained')

    completed=0;last_report=-1
    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in as_completed([pool.submit(fetch,bound) for bound in bounds]):
            completed+=future.result();percent=int(completed*100/total)
            if percent//10>last_report:
                print(f'Archive ranges: {percent}% ({completed}/{total} bytes)',flush=True);last_report=percent//10
    with partial.open('wb') as output:
        for start,end in bounds:
            with (parts/str(start)).open('rb') as source:output.write(source.read())
