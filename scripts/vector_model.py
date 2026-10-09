"""Shared immutable local-model manifest. No user text is sent to download endpoints."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'backend/data/vector-models'
MANIFEST=CACHE/'manifest.json'
MODEL='BAAI/bge-small-zh-v1.5'
ALGORITHM='character-window350-overlap70-mean-normalize-v1'


def file_digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def identity(files):
    return hashlib.sha256(json.dumps({'model':MODEL,'algorithm':ALGORITHM,'files':files},sort_keys=True).encode()).hexdigest()


def validated_manifest():
    data=json.loads(MANIFEST.read_text(encoding='utf-8'))
    model_dir=(CACHE/data['directory']).resolve()
    if not model_dir.is_relative_to(CACHE.resolve()):raise ValueError('模型目录超出项目缓存。')
    if data['model']!=MODEL or data['algorithm']!=ALGORITHM or data['fingerprint']!=identity(data['files']) or data['api_model']!='bge-small-zh-v1.5-'+data['fingerprint'][:16] or 'model_optimized.onnx' not in data['files']:raise ValueError('模型清单与处理算法不符，请重新准备。')
    for name,expected in data['files'].items():
        path=(model_dir/name).resolve()
        if not path.is_relative_to(model_dir) or file_digest(path)!=expected:raise ValueError('本地模型文件版本变化，请重新准备并重建索引。')
    return data,model_dir


def windows(text):
    result=[]
    for offset in range(0,len(text),280):
        result.append(text[offset:offset+350])
        if offset+350>=len(text):break
    return result
