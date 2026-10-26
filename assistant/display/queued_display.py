"""One bounded display worker; serial waits never run in capture or audio callbacks."""
from collections import deque
from threading import Condition, Thread
import time
from .base import CompanionDisplay, DisplayState, DisplayMode, validate_level


class QueuedCompanionDisplay(CompanionDisplay):
    def __init__(self, provider, *, probe=True):
        self.provider=provider;self.enabled=provider.enabled;self.reason=getattr(provider,'reason','')
        self._condition=Condition();self._queue=deque();self._level=None
        self._desired={'set_state':DisplayState.READY,'set_text':'','clear_query':None,'clear_mode':None}
        self._closed=False;self._busy=False;self._retry=False;self.dropped=0
        self._probe=probe;self._worker=Thread(target=self._run,name='companion-display',daemon=True);self._worker.start()

    def _submit(self, method, value=None):
        with self._condition:
            if self._closed: return False
            if method=='set_state':
                self._desired['set_text']='';self._desired['set_level']=0;self._level=None
                if value not in (DisplayState.THINKING,DisplayState.SPEAKING):
                    self._desired.pop('set_query',None);self._desired.pop('set_mode',None)
                    self._desired['clear_query']=None;self._desired['clear_mode']=None
            if method=='set_query': self._desired.pop('clear_query',None)
            if method=='set_mode': self._desired.pop('clear_mode',None)
            self._desired[method]=value
            if method=='set_level': self._level=value
            else:
                if len(self._queue)>=32:
                    self._queue.clear();self._retry=True;self.dropped+=1
                self._queue.append((method,value))
            self._condition.notify_all();return True

    def _call(self,method,value):
        try: return getattr(self.provider,method)() if value is None else getattr(self.provider,method)(value)
        except Exception: return False

    def _run(self):
        if self._probe and hasattr(self.provider,'probe'):
            try: self.provider.probe()
            except Exception: pass
        while True:
            with self._condition:
                if self._closed: break
                if self._retry:
                    batch=list(self._desired.items());self._queue.clear();self._level=None
                elif self._queue: batch=[self._queue.popleft()]
                elif self._level is not None: batch=[('set_level',self._level)];self._level=None
                else:
                    self._condition.wait(.1);continue
                self._busy=True
            ok=all(self._call(method,value) for method,value in batch)
            with self._condition:
                self._retry=not ok;self._busy=False;self._condition.notify_all()
                if not ok and not self._closed: self._condition.wait(.1)
        self.provider.close()

    def set_state(self,value): return self._submit('set_state',DisplayState(value))
    def set_text(self,value): return self._submit('set_text',str(value))
    def clear_text(self): return self.set_text('')
    def set_query(self,value): return self._submit('set_query',str(value))
    def clear_query(self): return self._submit('clear_query')
    def set_mode(self,value): return self._submit('set_mode',DisplayMode(value))
    def clear_mode(self): return self._submit('clear_mode')
    def set_level(self,value): return self._submit('set_level',validate_level(value))

    def flush(self,timeout=2):
        deadline=time.monotonic()+timeout
        with self._condition:
            while self._queue or self._busy or self._retry or self._level is not None:
                remaining=deadline-time.monotonic()
                if remaining<=0: return False
                self._condition.wait(min(.05,remaining))
            return True

    def evidence(self):
        result=self.provider.evidence() if hasattr(self.provider,'evidence') else {'rendering':'unobserved'}
        return dict(result,queue_dropped=self.dropped,worker_alive=self._worker.is_alive())

    def close(self):
        self.flush(timeout=1)
        with self._condition:
            self._closed=True;self._condition.notify_all()
        self._worker.join(timeout=2)
        if self._worker.is_alive(): raise RuntimeError('Display worker did not release serial ownership')
