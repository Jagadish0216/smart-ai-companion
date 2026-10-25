"""Deliberately narrow exact fact-card extraction; no arbitrary snippet answers."""
import re


def exact_offline_fact(query, results):
    if not re.fullmatch(r'what (?:cpu|processor|soc) does (?:the )?raspberry pi 5 (?:use|have)[?. ]*', query.strip(), re.I):
        return None
    if not results:
        return None
    values = set()
    for result in results:
        if result.source_identifier.startswith('online:'):
            return None
        # Whole chunks only: no instructions, extra claims, truncated context or
        # title-derived facts. Unsupported document formats use grounded generation.
        match = re.fullmatch(r'Raspberry Pi 5 CPU:\s*(?:Broadcom\s+)?(BCM\d{4})\.?', result.content.strip(), re.I)
        if not match:
            return None
        values.add(match[1].upper())
    if len(values) != 1:
        return 'The local sources disagree about the Raspberry Pi 5 CPU, so I cannot verify an answer.'
    return 'The Raspberry Pi 5 uses the ' + next(iter(values)) + ' SoC.'
