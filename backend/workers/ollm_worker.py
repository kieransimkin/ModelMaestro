"""One offloaded oLLM model per process. JSON lines are the private IPC protocol.

No vanilla AutoModel fallback: all loading goes through the custom fork. Killing
and reaping this process also releases its module-global loaders and GPU context.
"""
from __future__ import annotations
import contextlib
import gc
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.tool_format import generic_history,parse_generic
WIRE=sys.stdout

def emit(kind,**data):
    WIRE.write(json.dumps({'type':kind,**data},ensure_ascii=False)+'\n'); WIRE.flush()

def main():
    cfg=json.loads(sys.stdin.readline())
    # Library progress, warnings and generated text must never corrupt protocol stdout.
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from transformers import TextStreamer
        from ollm import Inference
        device=os.getenv('OLLM_DEVICE') or ('cuda:0' if torch.cuda.is_available() else
            ('mps' if hasattr(torch.backends,'mps') and torch.backends.mps.is_available() else 'cpu'))
        if device.startswith('cuda') and os.getenv('OLLM_CUDA_MEMORY_FRACTION'):
            fraction=float(os.environ['OLLM_CUDA_MEMORY_FRACTION'])
            if not 0<fraction<=1: raise ValueError('Invalid CUDA memory fraction')
            torch.cuda.set_per_process_memory_fraction(fraction,torch.device(device))
        model=cfg['model']
        inference=Inference(model['alias'],device=device,logging=False,multimodality=False)
        inference.load_model(model['path'])
        layers=int(os.getenv('OLLM_CPU_LAYERS','0'))
        if layers>0: inference.offload_layers_to_cpu(layers_num=layers)
        adapter=None
        if model.get('adapter')=='qwen':
            from ollm.tools.adapters import QwenAdapter
            adapter=QwenAdapter()
        elif model.get('adapter')=='harmony':
            from ollm.tools.adapters import GPTOSSAdapter
            adapter=GPTOSSAdapter()
        class WireStreamer(TextStreamer):
            def on_finalized_text(self,text,stream_end=False):
                if text: emit('delta',text=text,channel='raw')
        emit('ready',device=device,pid=os.getpid(),adapter=model.get('adapter','generic'))
        for line in sys.stdin:
            request=json.loads(line); settings=request['settings']
            try:
                if adapter:
                    prompt=adapter.prepare(request['messages'],request['tools'],inference.tokenizer,
                        reasoning_effort=settings['reasoning_effort'],enable_thinking=settings['thinking'])
                    tokens,stop=prompt.input_ids,prompt.stop_ids
                else:
                    history=generic_history(request['messages'],request['tools'],system_supported=not model['alias'].startswith('gemma'))
                    tokens=inference.tokenizer.apply_chat_template(history,tokenize=True,add_generation_prompt=True)
                    eos=inference.tokenizer.eos_token_id
                    stop=eos if isinstance(eos,list) else [eos]
                stop=[i for i in stop if isinstance(i,int)]
                if not tokens or not stop: raise ValueError('Tokenizer did not supply a valid prompt/end token')
                model_config=inference.model.config
                architecture_limit=getattr(model_config,'max_position_embeddings',None)
                if architecture_limit is None:
                    architecture_limit=getattr(getattr(model_config,'text_config',None),'max_position_embeddings',None)
                limits=[settings['context'],cfg['context']]
                if isinstance(architecture_limit,int) and architecture_limit>0: limits.append(architecture_limit)
                if len(tokens)+settings['max_tokens']>min(limits):
                    raise ValueError('Prompt plus requested output exceeds the context limit. Start a new chat, shorten it, or reload with a larger context.')
                ids=torch.tensor([tokens],dtype=torch.long,device=inference.device)
                kwargs={'input_ids':ids,'attention_mask':torch.ones_like(ids),'max_new_tokens':settings['max_tokens'],
                        'do_sample':settings['temperature']>0,'use_cache':True,'eos_token_id':stop,'pad_token_id':stop[0],
                        'return_dict_in_generate':False,'num_beams':1,
                        'streamer':WireStreamer(inference.tokenizer,skip_prompt=True,skip_special_tokens=False)}
                if settings['temperature']>0: kwargs.update(temperature=settings['temperature'],top_p=settings['top_p'])
                started=time.monotonic()
                cache_root=Path(cfg['cache']); cache_root.mkdir(parents=True,exist_ok=True)
                with TemporaryDirectory(prefix='turn-',dir=cache_root) as cache_dir:
                    if model['alias']!='gpt-oss-20B':
                        cache=inference.DiskCache(cache_dir=cache_dir)
                        if cache is None: raise ValueError('Selected offloaded model did not supply its disk cache')
                        kwargs['past_key_values']=cache
                    with torch.inference_mode():
                        generated=inference.model.generate(**kwargs)
                    completion=generated[0,len(tokens):].detach().cpu().tolist()
                    del generated,ids
                    kwargs.pop('past_key_values',None)
                    if 'cache' in locals(): del cache
                    gc.collect()
                complete=bool(completion and completion[-1] in stop)
                usage={'prompt_tokens':len(tokens),'completion_tokens':len(completion),'seconds':time.monotonic()-started}
                raw=inference.tokenizer.decode(completion,skip_special_tokens=True)
                if adapter and complete:
                    turn=adapter.parse(completion,inference.tokenizer)
                    answer={'content':turn.content,'thinking':turn.thinking,
                            'tool_calls':[c.to_dict() for c in turn.tool_calls]}
                else:
                    calls=parse_generic(raw,complete) if not adapter and request['tools'] else []
                    answer={'content':'' if calls else raw,'thinking':'','tool_calls':calls}
                answer.update(usage=usage,finish_reason='stop' if complete else 'length')
                emit('result',result=answer)
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                emit('error',message=f'{type(exc).__name__}: {exc}')
                # OOM / decode exceptions can invalidate caches. Exit; never reuse a
                # potentially damaged model object for the next queued request.
                return 1
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit('error',message=f'{type(exc).__name__}: {exc}'); sys.exit(1)
