(() => {
    'use strict';
    const mode = document.getElementById('knowledge-mode');
    const scope = document.getElementById('knowledge-scope');
    const answer = document.getElementById('knowledge-answer');
    const summary = document.getElementById('knowledge-library-status');
    let conversation = null;
    try { mode.value = localStorage.getItem('knowledge-mode') || 'online_preferred'; } catch (_) {}
    mode.addEventListener('change', () => { try { localStorage.setItem('knowledge-mode', mode.value); } catch (_) {} });
    async function refresh() {
        const responses = await Promise.all([fetch('/api/knowledge/documents/'), fetch('/api/knowledge/library/')]);
        if (responses.some(r => !r.ok)) throw new Error('Library status unavailable.');
        const [docs, library] = await Promise.all(responses.map(r => r.json()));
        const selected = scope.value;
        scope.replaceChildren(new Option('Automatic sources', ''), new Option('All local documents only', 'all'));
        docs.documents.filter(d => d.source_type === 'MANUAL' && d.status === 'INDEXED').forEach(d => scope.add(new Option(d.source_title || d.filename, d.id)));
        if (Array.from(scope.options).some(o => o.value === selected)) scope.value = selected;
        const sync = library.refresh || {};
        summary.textContent = `Library ${library.version}: ${library.indexed_references} indexed references; ${library.saved_versions} saved evidence versions (${library.fresh_versions} fresh); ${library.document_bytes} document bytes. ${library.coverage} Last successful refresh: ${sync.knowledge_last_success || 'not run'}. Next: ${sync.knowledge_next_refresh ? new Date(Number(sync.knowledge_next_refresh)*1000).toLocaleString() : 'not scheduled'}. Status: ${sync.knowledge_refresh_status || 'not run'}.`;
    }
    document.getElementById('knowledge-question-form').addEventListener('submit', async event => {
        event.preventDefault();
        const button = event.target.querySelector('button'); button.disabled = true;
        answer.textContent = 'Looking for supporting evidence…';
        const payload = {query: document.getElementById('knowledge-question').value, knowledge_mode: mode.value, conversation_id: conversation};
        if (scope.value) payload.document_ids = scope.value === 'all' ? [] : [Number(scope.value)];
        try {
            const response = await fetch('/api/assistant/chat/', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRFToken':csrftoken}, body:JSON.stringify(payload)});
            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || data.error || 'Question failed.');
            conversation = data.conversation_id;
            answer.textContent = data.response;
            for (const source of [...(data.rag_sources || []), ...(data.online_sources || [])]) {
                const line = document.createElement('p');
                line.textContent = `${source.title || 'Source'}${source.page_number ? ', page '+source.page_number : ''}${source.verified_at ? ' — verified '+source.verified_at : ''}`;
                if (source.url && /^https?:\/\//.test(source.url)) { const link = document.createElement('a'); link.href=source.url; link.textContent=' Source'; link.rel='noopener noreferrer'; link.target='_blank'; line.append(link); }
                answer.append(line);
            }
        } catch (error) { answer.textContent = error.message; }
        finally { button.disabled = false; }
    });
    const bootstrap = document.getElementById('knowledge-bootstrap');
    if (bootstrap) bootstrap.addEventListener('click', async () => {
        bootstrap.disabled = true;
        try {
            const response = await fetch('/api/knowledge/library/', {method:'POST', headers:{'X-CSRFToken':csrftoken}});
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Some reference sources failed; inspect library status.');
            await refresh();
        } catch (error) { summary.textContent = error.message; }
        finally { bootstrap.disabled = false; }
    });
    scope.addEventListener('focus', () => refresh().catch(error => { summary.textContent=error.message; }));
    refresh().catch(error => { summary.textContent=error.message; });
})();
