"""V2.2 protocol evidence. A reply proves command parsing, never TFT pixel rendering."""
from collections import Counter, deque
import time
from .serial_display import SerialCompanionDisplay


class AcknowledgedSerialDisplay(SerialCompanionDisplay):
    def __init__(self, *, capture=False, **kwargs):
        self.counts=Counter();self.audit=deque(maxlen=512);self.capture=capture
        self.protocol_supported=False;self.banner=None;self._rx=b''
        super().__init__(**kwargs)

    def _lines(self):
        if self._serial is None: return []
        waiting=getattr(self._serial,'in_waiting',0)
        if type(waiting) is not int: waiting=0
        if waiting: self._rx=(self._rx+self._serial.read(min(waiting,4096)))[-8192:]
        parts=self._rx.split(b'\n');self._rx=parts.pop()
        lines=[part.strip().decode('ascii',errors='replace') for part in parts]
        for line in lines:
            if line.startswith('Smart AI Companion Display Firmware'): self.banner=line
        return lines

    def _send(self, command):
        with self._lock:
            self.counts['attempted']+=1
            try: self._lines()  # Drop earlier uncorrelated replies before this serialized command.
            except (OSError,ValueError): pass
            written=super()._send(command)
            record={'kind':command.split(' ',1)[0],'written':written,'acknowledged':False}
            if self.capture: record['command']=command
            if written:
                self.counts['written']+=1
                if command.startswith('STATE '): expected='STATE: '+command[6:]
                elif command.startswith('MODE '): expected='MODE: '+command[5:]
                elif command.startswith('TEXT '): expected='TEXT: '+command[5:]
                elif command.startswith('LEVEL '): expected='LEVEL: '+command[6:]
                else: expected={'QUERY':'QUERY: OK','CLEAR QUERY':'QUERY: cleared','CLEAR MODE':'MODE: cleared','CLEAR TEXT':'TEXT:'}.get(command.split(' ',1)[0] if command.startswith('QUERY ') else command)
                deadline=time.monotonic()+.2
                while expected is not None and time.monotonic()<deadline:
                    try: lines=self._lines()
                    except (OSError,ValueError): break
                    if expected in lines or command=='CLEAR TEXT' and any(line.startswith('TEXT:') for line in lines):
                        record['acknowledged']=True;break
                    if any(line.startswith('ERR:') for line in lines):
                        self.counts['rejected']+=1;break
                    time.sleep(.005)
                self.counts['acknowledged' if record['acknowledged'] else 'ack_timeout']+=1
            if written: self.counts[record['kind']+'_written']+=1
            if record['acknowledged']: self.counts[record['kind']+'_acknowledged']+=1
            if record['kind']!='LEVEL': self.audit.append(record)
            return written

    def probe(self):
        with self._lock:
            if not super()._send('HELP'): return False
            deadline=time.monotonic()+.5;lines=[]
            while time.monotonic()<deadline:
                lines.extend(self._lines())
                if all(any(token in line for line in lines) for token in ('STATE ','TEXT ','QUERY ','MODE ','LEVEL ')):
                    self.protocol_supported=True;return True
                time.sleep(.01)
            return False

    def evidence(self):
        return {'counts':dict(self.counts),'audit':list(self.audit),'protocol_supported':self.protocol_supported,
                'firmware_banner':self.banner,'rendering':'unobserved',
                'ack_limit':'Legacy replies have no sequence IDs; timeouts make delivery assurance partial.'}
