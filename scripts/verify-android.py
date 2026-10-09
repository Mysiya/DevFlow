"""Verify delivered APK/signature/metadata and scan decompressed contents for secrets."""
import hashlib
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
delivery=ROOT/'artifacts/mobile';tools=ROOT/'mobile/android/.local'
apk=delivery/'DevFlow-0.18-debug.apk'
java=Path(os.environ.get('JAVA_HOME','C:/develop/jdk'))/'bin/java.exe'
signature=subprocess.check_output([str(java),'-jar',str(tools/'sdk/build-tools/36.0.0/lib/apksigner.jar'),
                                  'verify','--verbose','--print-certs',str(apk)],text=True,encoding='utf-8')
assert 'Verified using v2 scheme (APK Signature Scheme v2): true' in signature
badging=subprocess.check_output([str(tools/'sdk/build-tools/36.0.0/aapt2.exe'),'dump','badging',str(apk)],text=True,encoding='utf-8')
assert "name='com.mysiya.devflow' versionCode='18' versionName='0.18.0'" in badging
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
consent=json.loads((tools/'sdk-consent.json').read_text(encoding='utf-8'))
assert consent['accepted_explicitly'] is True
archive=next((tools/'downloads').glob('sdk-90ae805d2043.zip'))
with archive.open('rb') as source: assert hashlib.file_digest(source,'sha256').hexdigest()=='90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
certificate=re.search(r'Signer #1 certificate SHA-256 digest: ([0-9a-f]+)',signature).group(1)
proof={'package':'com.mysiya.devflow','version':'0.18.0','variant':'debug','bytes':apk.stat().st_size,
       'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'min_android_api':26,'target_android_api':35,
       'permissions':sorted(permissions),'signature_valid':True,'signature_scheme':'v2','certificate_sha256':certificate,
       'lint_errors':0,'lint_warnings':len(issues),'lint_issues':issues,'configured_secret_matches':0,
       'sdk_license_approval_received':True,'sdk_archive_sha256_verified':True,
       'cleartext_disabled':True,'backup_rules_declared':True,'android_device_verified':False,'https_server_deployed':False}
for name,text in [('apk-signature-v18.txt',signature),('apk-badging-v18.txt',badging),('apk-manifest-v18.txt',manifest)]:
    (delivery/name).write_text(text,encoding='utf-8')
(delivery/'android-apk-proof-v18.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(proof))
