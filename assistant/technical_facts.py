"""Small reviewed public hardware reference, separate from personal/project facts."""
import re

PI5_SOURCE = 'https://www.raspberrypi.com/documentation/computers/processors.html'
PI5_FACTS = (
    'Verified public Raspberry Pi 5 reference: BCM2712 SoC; quad-core 64-bit Arm '
    'Cortex-A76 CPU at 2.4 GHz; VideoCore VII GPU. RP1 is the I/O controller, '
    'not the main CPU. Source: ' + PI5_SOURCE + '. '
    'These product specifications do not establish the user\'s installed board or wiring.'
)


def specification_instruction(query):
    # Only a relevant compact reference; never inject this into project evidence.
    if re.search(r'\braspberry\s+pi\s*5\b', query, re.I) and re.search(
            r'\b(cpu|processor|soc|gpu|specifications?|specs|works?|explain)\b', query, re.I):
        return PI5_FACTS
    if re.search(r'\b(specifications?|specs|processor|cpu|gpu)\b', query, re.I):
        return 'Give exact hardware specifications only when supported by supplied references or confidently known; omit uncertain numbers. Do not confuse CPU, GPU and I/O controller.'
    return None
