import {request} from './client.js';
export const loadAll=async()=>Object.fromEntries(await Promise.all(['sources','periods','policies','runs','schedules','deliveries','audits','department-outputs','departments','evaluations'].map(async k=>[k,await request('/'+k)])));
export const saveSource=(data,id)=>request('/sources'+(id?'/'+id:''),id?'PUT':'POST',{data});
export const savePolicy=data=>request('/policies','POST',{data});
export const submitRun=data=>request('/runs','POST',data);
export const approveRun=(id,note)=>request('/runs/'+id+'/approve','POST',{note});
export const saveOutputs=rows=>request('/department-outputs','PUT',{rows});
export const submitEvaluation=data=>request('/evaluations','POST',data);
export const publishEvaluation=id=>request('/evaluations/'+id+'/publish','POST',{});
