"""Package ignored-secret-free server source, never local DB/model/cache data."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings

settings=Settings(_env_file=ROOT/'backend/.env')
fields=('github_token','llm_api_key','embedding_api_key','rerank_api_key','milvus_token','bootstrap_admin_password','mcp_access_token','github_webhook_secret')
secrets=[getattr(settings,name).get_secret_value().encode() for name in fields if getattr(settings,name).get_secret_value()]
cloud_login=ROOT/'artifacts/deploy/login.json'
if cloud_login.is_file():
    password=json.loads(cloud_login.read_text(encoding='utf-8')).get('password')
    if password:secrets.append(password.encode())
names=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
output=ROOT/'artifacts/mobile';output.mkdir(parents=True,exist_ok=True)
files=[]
for name in names:
    path=ROOT/name
    if not path.is_file():continue
    if '.env' in path.name and path.name!='.env.example':raise RuntimeError('Environment file must not be packaged')
    raw=path.read_bytes()
    if any(secret in raw for secret in secrets):raise RuntimeError('Configured credential found in source file')
    files.append((name,raw))
target=output/'DevFlow-server-source.zip'
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
    for name,raw in files:archive.writestr(name,raw)
proof={'file_count':len(files),'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'configured_secret_matches':0,'local_data_included':False}
(output/'server-package-proof.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
print(json.dumps(proof))
