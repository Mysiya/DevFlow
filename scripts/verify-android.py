"""Verify delivered APK/signature/metadata and scan decompressed contents for secrets."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings

settings=Settings(_env_file=ROOT/'backend/.env')
fields=('github_token','llm_api_key','embedding_api_key','rerank_api_key','milvus_token','bootstrap_admin_password','mcp_access_token','github_webhook_secret')
secrets=[getattr(settings,name).get_secret_value().encode() for name in fields if getattr(settings,name).get_secret_value()]
parser=argparse.ArgumentParser()
parser.add_argument('--version',choices=['0.18.0','0.18.1'],default='0.18.1')
args=parser.parse_args()
label='v18' if args.version=='0.18.0' else 'v181'
code=18 if args.version=='0.18.0' else 19
file_version='0.18' if args.version=='0.18.0' else args.version
delivery=ROOT/'artifacts/mobile';tools=ROOT/'mobile/android/.local'
apk=delivery/f'DevFlow-{file_version}-debug.apk'
cloud_login=ROOT/'artifacts/deploy/login.json'
if cloud_login.exists():secrets.append(json.loads(cloud_login.read_text(encoding='utf-8'))['password'].encode())
java=Path(os.environ.get('JAVA_HOME','C:/develop/jdk'))/'bin/java.exe'
signature=subprocess.check_output([str(java),'-jar',str(tools/'sdk/build-tools/36.0.0/lib/apksigner.jar'),
                                  'verify','--verbose','--print-certs',str(apk)],text=True,encoding='utf-8')
assert 'Verified using v2 scheme (APK Signature Scheme v2): true' in signature
badging=subprocess.check_output([str(tools/'sdk/build-tools/36.0.0/aapt2.exe'),'dump','badging',str(apk)],text=True,encoding='utf-8')
assert f"name='com.mysiya.devflow' versionCode='{code}' versionName='{args.version}'" in badging
assert "minSdkVersion:'26'" in badging and "targetSdkVersion:'35'" in badging
permissions=set(re.findall(r"uses-permission: name='([^']+)'",badging))
assert permissions=={'android.permission.INTERNET','android.permission.ACCESS_NETWORK_STATE'}
assert 'application-debuggable' in badging
manifest=subprocess.check_output([str(tools/'sdk/build-tools/36.0.0/aapt2.exe'),'dump','xmltree',str(apk),
                                  '--file','AndroidManifest.xml'],text=True,encoding='utf-8')
assert re.search(r':allowBackup\(0x[0-9a-f]+\)=false',manifest)
assert re.search(r':usesCleartextTraffic\(0x[0-9a-f]+\)=false',manifest)
assert ':dataExtractionRules' in manifest and ':fullBackupContent' in manifest
lint=ET.parse(ROOT/'mobile/android/app/build/reports/lint-results-debug.xml').getroot()
issues=[{'id':item.get('id'),'severity':item.get('severity')} for item in lint.findall('issue')]
assert not any(item['severity'] in ('Error','Fatal') for item in issues)
with zipfile.ZipFile(apk) as package:
    names=package.namelist()
    assert 'AndroidManifest.xml' in names and 'classes.dex' in names
    assert not any(name.endswith(('.env','.keystore','.jks','.db')) for name in names)
    for name in names:
        assert not any(secret in package.read(name) for secret in secrets),'Configured credential in APK'
    if args.version=='0.18.1':
        server=json.loads((ROOT/'mobile/service.json').read_text(encoding='utf-8'))['server_url']
        assert any(server.encode() in package.read(name) for name in names if name.endswith('.dex')),'Default HTTPS service missing from delivered APK'
consent=json.loads((tools/'sdk-consent.json').read_text(encoding='utf-8'))
assert consent['accepted_explicitly'] is True
archive=next((tools/'downloads').glob('sdk-90ae805d2043.zip'))
with archive.open('rb') as source: assert hashlib.file_digest(source,'sha256').hexdigest()=='90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
certificate=re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-f]+)',signature).group(1)
proof={'package':'com.mysiya.devflow','version':args.version,'version_code':code,'variant':'debug','bytes':apk.stat().st_size,
       'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'min_android_api':26,'target_android_api':35,
       'permissions':sorted(permissions),'signature_valid':True,'signature_scheme':'v2','certificate_sha256':certificate,
       'lint_errors':0,'lint_warnings':len(issues),'lint_issues':issues,'configured_secret_matches':0,
       'sdk_license_approval_received':True,'sdk_archive_sha256_verified':True,
       'cleartext_disabled':True,'backup_rules_declared':True,'android_device_verified':False,'service_runtime_checked':False}
if args.version=='0.18.1':proof.update({'default_https_service':server,'default_service_embedded':True})
for name,text in [(f'apk-signature-{label}.txt',signature),(f'apk-badging-{label}.txt',badging),(f'apk-manifest-{label}.txt',manifest)]:
    (delivery/name).write_text(text,encoding='utf-8')
(delivery/f'android-apk-proof-{label}.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(proof))
