"""Verified project-local JDK/Gradle/SDK archives. No machine-wide installation."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from archive_download import download_ranges

ROOT=Path(__file__).resolve().parents[1]/'mobile/android/.local'
PACKAGES={
    'jdk':('https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip','e53a79c3c3d86865bd7e787903884331068e71321714ffd44f145785affc7cb0'),
    'gradle':('https://services.gradle.org/distributions/gradle-9.3.1-bin.zip','b266d5ff6b90eada6dc3b20cb090e3731302e553a27c5d3e4df1f0d76beaff06'),
    'sdk':('https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip','90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'),
}

def curl_binary():
    # Git's OpenSSL transport avoids stalled Schannel transfers on this host.
    candidate=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/mingw64/bin/curl.exe'
    return str(candidate) if candidate.is_file() else (shutil.which('curl.exe') or shutil.which('curl') or 'curl.exe')

def prepare(name):
    url,expected=PACKAGES[name];cache=ROOT/'downloads';cache.mkdir(parents=True,exist_ok=True);archive=cache/(name+'-'+expected[:12]+'.zip')
    legacy=cache/(name+'.zip')
    if not archive.exists() and legacy.exists():
        with legacy.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()==expected:legacy.replace(archive)
    actual=None
    if archive.exists():
        with archive.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected:
        print('Downloading '+name,flush=True)
        partial=archive.with_suffix('.partial')
        # A new curl process resumes from persisted bytes after a network error.
        # Only a complete archive with the pinned digest is installed.
        if name=='sdk':
            download_ranges(curl_binary(),url,expected,partial,155655386,seed=cache/'sdk.partial')
        else:
            for attempt in range(3):
                result=subprocess.run([curl_binary(),'--fail','--location','--silent','--show-error','--connect-timeout','30','--max-time','1800','--speed-limit','1024','--speed-time','60','--continue-at','-','--output',str(partial),url],timeout=1850)
                if result.returncode==0:break
                if attempt==2:result.check_returncode()
                print('Retrying '+name+' from saved bytes',flush=True)
        with partial.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:raise RuntimeError('Archive checksum failed: '+name)
        partial.replace(archive)
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:raise RuntimeError('Archive checksum failed: '+name)
    destination=ROOT/name if name!='sdk' else ROOT/'sdk/cmdline-tools/latest'
    destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            relative=member.filename
            if name=='sdk':
                if not relative.startswith('cmdline-tools/'):raise RuntimeError('Unexpected SDK archive layout')
                relative=relative[len('cmdline-tools/'):]
            target=(destination/relative).resolve()
            if not target.is_relative_to(destination.resolve()):raise RuntimeError('Archive path outside project')
            if member.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with package.open(member) as source,target.open('wb') as output:
                    while chunk:=source.read(1024*1024):output.write(chunk)
    print('Ready '+name,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--accept-sdk-license',action='store_true');parser.add_argument('--non-sdk-only',action='store_true');parser.add_argument('--sdk-only',action='store_true');args=parser.parse_args()
    if args.sdk_only and args.non_sdk_only:parser.error('Choose sdk-only or non-sdk-only, not both')
    if not args.non_sdk_only and not args.accept_sdk_license:parser.error('Read and explicitly accept the Android SDK agreement before downloading SDK tools: https://developer.android.com/studio#downloads')
    if args.accept_sdk_license:
        ROOT.mkdir(parents=True,exist_ok=True)
        (ROOT/'sdk-consent.json').write_text(json.dumps({'accepted_explicitly':True,'agreement':'https://developer.android.com/studio#downloads'}),encoding='utf-8')
    names=['sdk'] if args.sdk_only else ['jdk','gradle'] if args.non_sdk_only else list(PACKAGES)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(prepare,names))
    if args.accept_sdk_license:(ROOT/'sdk-consent.json').write_text(json.dumps({'accepted_explicitly':True,'agreement':'https://developer.android.com/studio#downloads'}),encoding='utf-8')
