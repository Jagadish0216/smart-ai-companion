"""Content-free latency report. Missing physical/backend measurements stay unknown."""

def summarize(report):
    events=report.get('events',[])
    def first(name): return next((event for event in events if event['name']==name),None)
    def interval(start,end):
        a,b=first(start),first(end)
        return round(b['at_ms']-a['at_ms'],3) if a and b else None
    def spans(name):
        pending={};values=[]
        for event in events:
            lane=event.get('lane','main')
            if event['name']==name+'_start': pending[lane]=event['at_ms']
            elif event['name']==name+'_end' and lane in pending: values.append(event['at_ms']-pending.pop(lane))
        return round(sum(values),3) if values else None
    metrics=first('ollama_complete') or {}
    return {'synthetic':report.get('synthetic',True), 'capture_vad_ms':spans('capture'),
            'stt_cli_ms':spans('stt_cli'), 'stt_server_ms':spans('stt_server'),
            'document_resolution_ms':spans('document_resolve'), 'sqlite_retrieval_ms':spans('document_retrieve'),
            'rag_context_ms':spans('rag_context'), 'requested_output_budget':(first('rag_context_end') or {}).get('num_predict'),
            'context_chars':(first('rag_context_end') or {}).get('chars'), 'prompt_evaluation_ms':metrics.get('prompt_eval_duration_ms'),
            'backend_first_token_ms':interval('ollama_request_ready','llm_first_alphanumeric'),
            'generation_ms':metrics.get('eval_duration_ms'), 'output_tokens':metrics.get('eval_count'),
            'done_reason':metrics.get('done_reason'), 'first_audio_ready_ms':(first('first_audio_ready') or {}).get('at_ms'),
            'first_playback_launch_ms':(first('first_playback_launch') or {}).get('at_ms'),
            'first_pcm_accepted_ms':(first('first_speech_pcm_accepted') or {}).get('at_ms'),
            'spoken_response_ms':interval('first_speech_pcm_accepted','spoken_response_complete') if first('first_speech_pcm_accepted') else interval('first_playback_launch','spoken_response_complete'),
            'spoken_response_clock_basis':'pcm_acceptance' if first('first_speech_pcm_accepted') else 'playback_launch' if first('first_playback_launch') else 'unobserved',
            'total_cycle_ms':report.get('elapsed_ms'),
            'interpretation':'Overlapping stages are not additive. PCM acceptance/playback launch are not measured acoustic onset. Unknown values are null.'}
