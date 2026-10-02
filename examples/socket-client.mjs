// Run from the project root after npm install, with a ready model in the UI:
// MODELMAESTRO_TOKEN="$(cat .state/access-token)" node examples/socket-client.mjs "Your prompt"
import {io} from 'socket.io-client';
import {randomUUID} from 'node:crypto';

// Legacy CONSOLE_* aliases remain supported for existing client scripts.
const url=process.env.MODELMAESTRO_URL || process.env.CONSOLE_URL || 'http://127.0.0.1:3000';
const token=process.env.MODELMAESTRO_TOKEN || process.env.CONSOLE_TOKEN;
const prompt=process.argv.slice(2).join(' ') || 'Hello. Describe what you can help with.';
if(!token)throw new Error('Set MODELMAESTRO_TOKEN to the ModelMaestro access token.');
const socket=io(url,{path:'/socket.io/',auth:{token},transports:['websocket'],autoConnect:false});
let jobId=null,cursor=0,round=0,output='',done=false;
function apply(event){
  if(event.seq<=cursor)return;
  cursor=event.seq;
  if(event.job_id!==jobId)return;
  if(event.type==='chat.round'){round=event.data.round;output='';}
  if(event.type==='chat.delta' && event.data.channel==='raw'){
    if(event.data.round===round && event.data.offset===Array.from(output).length){
      output+=event.data.text;process.stdout.write(event.data.text);
    }
  }
  if(event.type==='tool.approval')console.error('\nApprove or deny this tool call in the web UI:',event.data.tool);
}
socket.on('event',apply);
socket.on('connect_error',error=>console.error('Socket connection:',error.message));
socket.on('connect',()=>{if(jobId)socket.emit('sync',{after:cursor},reply=>{if(reply.ok)for(const event of reply.events)apply(event);});});
const call=(name,payload)=>socket.timeout(15000).emitWithAck(name,payload).then(reply=>{
  if(!reply.ok)throw new Error(reply.error || 'Request rejected');return reply;
});
try{
  await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Connection deadline exceeded')),15000);
    socket.once('connect',()=>{clearTimeout(timer);resolve();});socket.connect();
  });
  const {state}=await call('sync',{after:0});
  if(state.model_state.status!=='ready' || !state.model_state.active)throw new Error('Load a model in the UI first.');
  const reply=await call('job:submit',{
    kind:'chat',request_key:randomUUID(),
    payload:{chat_id:randomUUID(),model_id:state.model_state.active.id,prompt,tools:true,
      settings:{context:state.model_state.context,max_tokens:512}}
  });
  jobId=reply.job.id;console.error('Queued job:',jobId);
  // HTTP snapshots remain authoritative even when the live socket disconnects.
  const deadline=Date.now()+2*60*60*1000;
  while(!done && Date.now()<deadline){
    const response=await fetch(url+'/api/jobs/'+jobId,{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Job lookup failed: HTTP '+response.status);
    const job=await response.json();
    if(['completed','failed','cancelled','interrupted'].includes(job.status)){
      done=true;console.log('\n\n--- Persisted final result ---');
      console.log(job.status==='completed'?job.result.content:job.error || job.status);
    }else await new Promise(resolve=>setTimeout(resolve,500));
  }
  if(!done)throw new Error('Client monitoring deadline exceeded; the server job has NOT been cancelled.');
}finally{socket.disconnect();}
