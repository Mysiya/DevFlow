"""Download only public model artifacts, test CPU execution, save a content manifest."""
import json
import os
from vector_model import CACHE, MANIFEST, MODEL, ALGORITHM, file_digest, identity

os.environ['HF_HOME']=str(CACHE/'hub')
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
os.environ['HF_HUB_DISABLE_XET']='1'
for key in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN'):os.environ.pop(key,None)
from fastembed import TextEmbedding


def main():
    encoder=TextEmbedding(MODEL,cache_dir=str(CACHE),threads=2,providers=['CPUExecutionProvider'])
    model_dir=encoder.model._model_dir.resolve()
    if not model_dir.is_relative_to(CACHE.resolve()):raise ValueError('模型不在项目缓存。')
    files={str(path.relative_to(model_dir)).replace('\\','/'):file_digest(path) for path in sorted(model_dir.rglob('*')) if path.is_file() and not any(part.startswith('.') for part in path.relative_to(model_dir).parts)}
    fingerprint=identity(files)
    vector=list(encoder.embed(['关闭页面不会取消后台分析'],batch_size=1))[0]
    if len(vector)!=512:raise ValueError('模型维度不符。')
    data={'model':MODEL,'algorithm':ALGORITHM,'directory':str(model_dir.relative_to(CACHE)).replace('\\','/'),'files':files,'fingerprint':fingerprint,'api_model':'bge-small-zh-v1.5-'+fingerprint[:16], 'dimension':512}
    MANIFEST.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ready':True,'model':MODEL,'api_model':data['api_model'],'fingerprint':fingerprint,'dimension':512,'files':len(files)}))


if __name__=='__main__':main()
