"""Ragas 0.4.3 non-LLM context metrics; read JSON from stdin, no external calls."""
import os
os.environ["RAGAS_DO_NOT_TRACK"]="true"
os.environ["HF_HUB_OFFLINE"]="1"
os.environ["HF_DATASETS_OFFLINE"]="1"
os.environ["LANGCHAIN_TRACING_V2"]="false"
import asyncio
import json
import sys
from importlib.metadata import version
from ragas import SingleTurnSample
from ragas.metrics import NonLLMContextRecall, NonLLMContextPrecisionWithReference


async def main():
    samples=json.load(sys.stdin)
    if not isinstance(samples,list) or len(samples)>32:raise ValueError("评测最多 32 条固定样例。")
    recall=NonLLMContextRecall();precision=NonLLMContextPrecisionWithReference()
    rows=[]
    for row in samples:
        sample=SingleTurnSample(user_input=row["user_input"],retrieved_contexts=row["retrieved_contexts"] or [""],reference_contexts=row["reference_contexts"])
        rows.append({"variant":row["variant"],"case_id":row["case_id"],
            "context_recall":await recall.single_turn_ascore(sample),
            "context_precision":await precision.single_turn_ascore(sample)})
    print(json.dumps({"provider":"ragas-nonllm","version":version("ragas"),"rows":rows},ensure_ascii=False))


if __name__=="__main__":asyncio.run(main())
