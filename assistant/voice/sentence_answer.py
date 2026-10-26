"""Complete-sentence gate for physical document answers; canonical stream and stored text agree."""
import re


class SentenceAnswer:
    def __init__(self):
        self.pending=''
        self.text=''
        self.truncated=False

    def feed(self, text):
        self.pending+=text
        boundaries=list(re.finditer(r'[.!?][\"\u201d\u2019)]*\s+',self.pending))
        if not boundaries:
            return ''
        end=boundaries[-1].end()
        part,self.pending=self.pending[:end],self.pending[end:]
        self.text+=part
        return part

    def finish(self, reason):
        tail=self.pending
        complete=bool(re.search(r'[.!?][\"\u201d\u2019)]*\s*$',tail))
        self.truncated=reason=='length'
        if tail.strip() and complete:
            part=tail
        elif tail.strip() and reason!='length' and not re.search(r'\b(?:and|or|to|of|the|is|are|because|with|for|a|an)\s*$',tail,re.I):
            part=tail.rstrip()+'.'
        else:
            part=''
            self.truncated |= bool(tail.strip())
        if not self.text.strip() and not part.strip():
            part='The response was cut short. Please ask about one specific point in the document.'
        elif self.truncated:
            part=part.rstrip()+' Ask me to continue with more detail.'
        self.pending='';self.text+=part
        return part
