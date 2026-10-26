/* Small safe presentation layer: content is always inserted with textContent. */
(() => {
    function inline(parent, value) {
        const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
        let end = 0;
        for (const match of value.matchAll(pattern)) {
            parent.append(document.createTextNode(value.slice(end, match.index)));
            const part = match[0];
            let node;
            if (part.startsWith('`')) { node = document.createElement('code'); node.textContent = part.slice(1,-1); }
            else if (part.startsWith('**')) { node = document.createElement('strong'); node.textContent = part.slice(2,-2); }
            else {
                const pieces = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
                node = document.createElement('a'); node.textContent = pieces[1];
                node.href = pieces[2]; node.rel = 'noopener noreferrer'; node.target = '_blank';
            }
            parent.append(node); end = match.index + part.length;
        }
        parent.append(document.createTextNode(value.slice(end)));
    }
    function renderText(container, text) {
        container.replaceChildren();
        let code = null, list = null;
        for (const line of String(text).split('\n')) {
            if (line.startsWith('```')) {
                if (code) code = null;
                else { const pre=document.createElement('pre');code=document.createElement('code');pre.append(code);container.append(pre); }
                list=null; continue;
            }
            if(code) { code.append(document.createTextNode(line+'\n')); continue; }
            const item=/^\s*(?:([-*]) |(\d+)\. )(.+)$/.exec(line);
            if(item) { const tag=item[2]?'OL':'UL'; if(!list || list.tagName!==tag) {list=document.createElement(tag.toLowerCase());container.append(list);} const li=document.createElement('li');inline(li,item[3]);list.append(li); continue; }
            list=null;
            if(!line.trim()) continue;
            const heading=/^#{1,3}\s+(.+)$/.exec(line);
            const block=document.createElement(heading?'h3':'p');inline(block,heading?heading[1]:line);container.append(block);
        }
    }
    function renderSources(parent, event) {
        parent.querySelector('.message-sources')?.remove();
        const sources=[...(Array.isArray(event.rag_sources)?event.rag_sources:[]),...(Array.isArray(event.online_sources)?event.online_sources:[])];
        if(!sources.length) return;
        const group=document.createElement('div');group.className='message-sources';
        const heading=document.createElement('span');heading.className='eyebrow';heading.textContent='Sources';group.append(heading);
        for(const source of sources) {
            const label=source.title || source.filename || source.document_name || 'Memory source';
            let node=document.createElement('span');
            if(typeof source.url==='string') {
                try {const url=new URL(source.url);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password){node=document.createElement('a');node.href=url.href;node.target='_blank';node.rel='noopener noreferrer';}} catch(_) {}
            }
            const page=Number.isInteger(source.page_number)&&source.page_number>0?' | p. '+source.page_number:'';
            const verified=source.verified_at?' | Last verified '+String(source.verified_at):'';
            const saved=source.last_synced_at?' | Saved offline evidence | Last synced '+String(source.last_synced_at):'';
            const historical=['HISTORICAL','SUPERSEDED'].includes(source.freshness) || source.historical===true || /offline|cache/.test(event.fallback_reason || '')?' | Historical/offline evidence':'';
            node.textContent=label+page+verified+saved+historical+(source.freshness?' | '+String(source.freshness):'');group.append(node);
        }
        parent.append(group);
    }
    window.AssistantUI={renderText,renderSources};
})();
