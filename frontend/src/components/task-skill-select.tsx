"use client";
import { useEffect, useState } from "react";
import { api, TaskSkill } from "@/lib/api";
import "./task-skill.css";

export function TaskSkillSelect({repositoryId, value, onChange, disabled}: {
  repositoryId:string; value:TaskSkill|null; onChange:(skill:TaskSkill|null)=>void; disabled:boolean;
}) {
  const [skills,setSkills]=useState<TaskSkill[]>([]),[error,setError]=useState(""),[loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError("");setSkills([]);
    void api<{skills:TaskSkill[]}>(`/repositories/${repositoryId}/task-skills`,{signal:controller.signal})
      .then(data=>{if(!controller.signal.aborted){setSkills(data.skills);setLoading(false);}})
      .catch(e=>{if(!controller.signal.aborted){setError(e.message);setLoading(false);}});
    return()=>controller.abort();
  },[repositoryId]);
  return <div className="task-skill-picker">
    <label>任务流程 <select aria-label="选择任务 Skill" disabled={disabled||loading} value={value?.id||""}
      onChange={e=>onChange(skills.find(s=>s.id===e.target.value)||null)}>
      <option value="">自由分析</option>{skills.map(s=><option key={s.id} value={s.id}>{s.name} · {s.version}</option>)}
    </select></label>
    {error&&<p className="governance-error" role="alert">任务流程加载失败：{error}</p>}
  </div>;
}

export function TaskSkillPreview({skill, demo}:{skill:TaskSkill;demo:boolean}) {
  return <details className="task-skill-preview" open><summary>{skill.name} · v{skill.version} · 查看流程要求</summary>
    <p>{skill.description}</p><ul>{skill.checklist.map(item=><li key={item}>{item}</li>)}</ul>
    <p className="small-muted">{demo?"演示模式使用规则引擎，不验证模型对流程要求的执行效果。":"模型按此流程要求分析，并保留缺少证据的检查项。"}检查项不表示已经完成。</p>
  </details>;
}
