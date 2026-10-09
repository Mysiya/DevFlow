"""Offline CPU embeddings, fixed loopback listener, independent local-service credential."""
import hmac
import os
import threading
from contextlib import asynccontextmanager
from typing import Annotated
import numpy as np
from dotenv import dotenv_values
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from vector_model import CACHE, MODEL, ROOT, validated_manifest, windows

os.environ['HF_HOME']=str(CACHE/'hub')
os.environ['HF_HUB_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'


def load_encoder():
    from fastembed import TextEmbedding
    data,path=validated_manifest()
    encoder=TextEmbedding(MODEL,cache_dir=str(CACHE),specific_model_path=str(path),local_files_only=True,threads=2,providers=['CPUExecutionProvider'])
    return data,encoder


def service_token():
    config=dotenv_values(ROOT/'backend/.env')
    token=os.environ.get('DEVFLOW_LOCAL_EMBEDDING_TOKEN') or config.get('EMBEDDING_API_KEY','')
    if len(token)<32 or token==config.get('LLM_API_KEY') or token==config.get('GITHUB_TOKEN'):raise ValueError('本地 Embedding 需要独立的至少 32 字符密钥。')
    return token


class Input(BaseModel):
    model: str=Field(min_length=1,max_length=200)
    input: list[Annotated[str,Field(min_length=1,max_length=6000)]]=Field(min_length=1,max_length=32)
    dimensions: int|None=None

    @field_validator('input')
    @classmethod
    def nonblank(cls,texts):
        if any(not text.strip() for text in texts):raise ValueError('输入不能只有空白。')
        return texts


def embed(encoder,texts):
    pieces=[windows(text) for text in texts]
    encoded=iter(encoder.embed([piece for group in pieces for piece in group],batch_size=8))
    result=[]
    for group in pieces:
        vector=np.mean([next(encoded) for _ in group],axis=0)
        norm=float(np.linalg.norm(vector))
        if len(vector)!=512 or not np.isfinite(vector).all() or not norm:raise ValueError('模型返回无效向量。')
        result.append((vector/norm).tolist())
    return result


def create_app(loader=load_encoder,token_reader=service_token):
    @asynccontextmanager
    async def lifespan(app):
        app.state.manifest,app.state.encoder=loader()
        app.state.token=token_reader()
        app.state.lock=threading.Lock()
        yield
    app=FastAPI(title='DevFlow local CPU embeddings',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

    @app.middleware('http')
    async def protect(request:Request,call_next):
        if request.headers.get('origin'):return JSONResponse({'detail':'仅供本机后端调用。'},status_code=403)
        if request.url.path=='/health' and request.method=='GET':return await call_next(request)
        if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+app.state.token):return JSONResponse({'detail':'本地服务授权无效。'},status_code=401)
        length=request.headers.get('content-length','')
        if not length.isdigit():return JSONResponse({'detail':'需要声明请求长度。'},status_code=411)
        if int(length)>1048576:return JSONResponse({'detail':'请求超过本地服务长度上限。'},status_code=413)
        return await call_next(request)

    @app.get('/health')
    def health():
        return {'status':'ok','model':app.state.manifest['api_model'],'fingerprint':app.state.manifest['fingerprint'],'dimension':512,'execution':'local-cpu','offline':True,'algorithm':app.state.manifest.get('algorithm')}

    @app.post('/v1/embeddings')
    def embeddings(body:Input):
        if body.model!=app.state.manifest['api_model'] or body.dimensions not in (None,512):return JSONResponse({'detail':'模型版本或维度不匹配，请检查本地配置。'},status_code=400)
        try:
            with app.state.lock:vectors=embed(app.state.encoder,body.input)
        except Exception:return JSONResponse({'detail':'本地模型计算失败；没有调用远程服务。'},status_code=503)
        return {'object':'list','model':body.model,'data':[{'object':'embedding','index':index,'embedding':vector} for index,vector in enumerate(vectors)]}
    return app


app=create_app()


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8001,access_log=False)
