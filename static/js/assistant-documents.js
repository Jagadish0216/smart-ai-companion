(() => {
    'use strict';
    const endpoint = '/api/knowledge/documents/';
    const mode = document.getElementById('knowledge-mode');
    const list = document.getElementById('assistant-documents');
    const chips = document.getElementById('selected-documents');
    const status = document.getElementById('document-status');
    const picker = document.getElementById('assistant-file-picker');
    const attach = document.getElementById('attach-document');
    const progress = document.getElementById('document-progress');
    let documents = [], selected = [], key = 'new', busy = false;
    const storageKey = id => 'companion_document_scope_' + id;
    function save(id = key) {
        key = String(id);
        try { sessionStorage.setItem(storageKey(key), JSON.stringify({document_ids: selected, knowledge_mode: mode.value})); } catch (_) {}
    }
    function render() {
        list.replaceChildren(); chips.replaceChildren();
        for (const doc of documents) {
            const label = document.createElement('label');
            const input = document.createElement('input'); input.type = 'checkbox';
            input.checked = selected.includes(doc.id); input.disabled = doc.status !== 'INDEXED';
            input.addEventListener('change', () => { selected = input.checked ? [...selected, doc.id] : selected.filter(id => id !== doc.id); save(); render(); });
            label.append(input, document.createTextNode(doc.filename + ' · ' + doc.status + (doc.error_message ? ' · ' + doc.error_message : ''))); list.append(label);
        }
        for (const id of selected) {
            const doc = documents.find(d => d.id === id);
            const button = document.createElement('button'); button.type = 'button'; button.className = 'btn attachment-chip';
            const name = doc ? doc.filename : 'Document ' + id;
            button.textContent = name + ' ×'; button.setAttribute('aria-label', 'Deselect ' + name);
            button.addEventListener('click', () => { selected = selected.filter(value => value !== id); save(); render(); }); chips.append(button);
        }
        if (!busy) status.textContent = selected.length ? 'Selected documents take priority over online sources.' : 'Automatic knowledge retrieval';
    }
    async function refresh() {
        try {
            const response = await fetch(endpoint, {cache: 'no-store'});
            if (!response.ok) throw new Error('Documents could not be loaded. Use Refresh to retry.');
            documents = (await response.json()).documents; render();
        } catch (error) { status.textContent = error.message; }
    }
    function restore(id) {
        key = String(id); selected = []; mode.value = '';
        try {
            const stored = JSON.parse(sessionStorage.getItem(storageKey(key)) || '{}');
            selected = Array.isArray(stored.document_ids) ? stored.document_ids.filter(Number.isSafeInteger) : [];
            mode.value = stored.knowledge_mode || '';
        } catch (_) {}
        if (key === 'new') { selected = []; mode.value = ''; save(); }
        render();
    }
    function upload(file) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest(); xhr.open('POST', endpoint); xhr.timeout = 180000;
            xhr.setRequestHeader('X-CSRFToken', csrftoken);
            xhr.upload.onprogress = event => { if (event.lengthComputable) progress.value = event.loaded / event.total * 100; };
            xhr.upload.onload = () => { status.textContent = 'Extracting text and indexing ' + file.name + '…'; progress.removeAttribute('value'); };
            xhr.onerror = xhr.ontimeout = () => reject(new Error('Upload interrupted. Refresh to check indexing status before retrying.'));
            xhr.onload = () => {
                try {
                    const data = JSON.parse(xhr.responseText);
                    if (xhr.status < 200 || xhr.status >= 300) throw new Error(data.error || data.document?.error_message || 'Upload failed. Sign in with an administrator account to upload.');
                    resolve(data.document);
                } catch (error) { reject(error); }
            };
            const body = new FormData(); body.append('file', file); xhr.send(body);
        });
    }
    attach.addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
        if (busy) return;
        busy = true; attach.disabled = true; progress.hidden = false;
        try {
            for (const file of picker.files) {
                if (!/\.(pdf|txt|md|markdown)$/i.test(file.name) || file.size > 10 * 1024 * 1024) throw new Error('Choose PDF, TXT or Markdown files up to 10 MB.');
                progress.value = 0; status.textContent = 'Uploading ' + file.name + '…';
                const doc = await upload(file);
                if (doc.status === 'INDEXED') { selected = [...new Set([...selected, doc.id])]; save(); }
            }
            await refresh(); status.textContent = 'Documents indexed and selected.';
        } catch (error) { status.textContent = error.message; }
        finally { busy = false; attach.disabled = false; progress.hidden = true; picker.value = ''; }
    });
    mode.addEventListener('change', () => save());
    document.getElementById('refresh-documents').addEventListener('click', refresh);
    window.AssistantDocuments = {restore, save, scope: () => ({document_ids: [...selected], ...(mode.value ? {knowledge_mode: mode.value} : {})})};
    refresh();
})();
