(function () {
    'use strict';
    const endpoint = '/api/knowledge/documents/';
    const zone = document.getElementById('knowledge-drop-zone');
    const canManage = zone.dataset.canManage === 'true';
    const picker = document.getElementById('knowledge-file-picker');
    const uploadButton = document.getElementById('btn-upload-doc');
    const syncButton = document.getElementById('knowledge-sync-stale');
    const rows = document.getElementById('knowledge-documents');
    const status = document.getElementById('knowledge-operation-status');
    const progress = document.getElementById('knowledge-upload-progress');
    let busy = false;

    function toast(message, type) {
        const container = document.getElementById('toast-container');
        if (!container) return;
        const item = document.createElement('div');
        item.className = 'toast';
        const text = document.createElement('span');
        text.textContent = message;
        item.appendChild(text);
        item.setAttribute('role', type === 'error' ? 'alert' : 'status');
        container.appendChild(item);
        setTimeout(() => item.remove(), 5000);
    }

    function setBusy(value, message) {
        busy = value;
        status.textContent = message || '';
        if (uploadButton) uploadButton.disabled = value;
        if (syncButton) syncButton.disabled = value;
        detail.querySelectorAll('button').forEach(button => { button.disabled = value; });
    }

    function cell(row, text) {
        const td = document.createElement('td');
        td.textContent = text;
        row.appendChild(td);
        return td;
    }

    function timestamp(value) {
        return value ? new Date(value).toLocaleString() : '—';
    }

    let documents = [];
    let selectedId = null;
    let filter = 'ALL';
    const search = document.getElementById('memory-search');
    const detail = document.getElementById('memory-detail');
    const stateLabel = value => ({INDEXED:'Ready',PROCESSING:'Processing',PENDING:'Queued',ERROR:'Needs attention'})[value] || value || 'Status unavailable';
    function selectDocument(doc) {
        selectedId = doc.id;
        const online = doc.source_type === 'ONLINE';
        detail.replaceChildren();
        const label = document.createElement('p'); label.className='eyebrow'; label.textContent='Selected file';
        const title = document.createElement('h3'); title.textContent=doc.source_title || doc.filename;
        const state = document.createElement('p'); state.textContent=stateLabel(doc.status);
        const description = document.createElement('p'); description.textContent=doc.status==='INDEXED'?'Indexed for retrieval. Online source freshness also applies.':'This file is not currently indexed for answers.';
        const list = document.createElement('dl');
        const info = {Type:doc.file_type,Origin:doc.source_type,Chunks:doc.chunk_count,Bytes:doc.file_size,Uploaded:timestamp(doc.uploaded_at),Indexed:timestamp(doc.indexed_at)};
        if(doc.source_type==='ONLINE') Object.assign(info,{Source:doc.source_url,Provider:doc.provider,Freshness:doc.freshness,Expires:timestamp(doc.expires_at),'Last sync':timestamp(doc.last_synced_at),'Saved versions':(doc.evidence || []).map(v=>v.state+' / verified '+timestamp(v.verified_at)).join('; ')});
        for(const [key,value] of Object.entries(info)) { const dt=document.createElement('dt');dt.textContent=key;const dd=document.createElement('dd');dd.textContent=value ?? 'Unavailable';list.append(dt,dd); }
        detail.append(label,title,state,description,list);
        if(doc.error_message || doc.sync_error_message) { const error=document.createElement('p');error.textContent=doc.error_message || doc.sync_error_message;detail.append(error); }
        if(canManage) [online ? 'Sync' : 'Reindex','Delete'].forEach(action=>{const button=document.createElement('button');button.className='btn';button.textContent=action==='Delete'?'Remove from memory':action;button.disabled=busy;button.addEventListener('click',()=>performAction(doc,action));detail.append(button);});
        rows.querySelectorAll('[data-document-id]').forEach(row=>row.setAttribute('aria-current',String(row.dataset.documentId===String(doc.id))));
    }
    function renderDocuments() {
        rows.replaceChildren();
        const query=search.value.trim().toLowerCase();
        const visible=documents.filter(doc=>(filter==='ALL'||doc.status===filter)&&[doc.filename,doc.source_title,doc.source_url].some(value=>String(value||'').toLowerCase().includes(query)));
        if(!visible.length) {const empty=document.createElement('p');empty.className='quiet-empty';empty.textContent=documents.length?'No files match this search.':'No documents yet. Upload a document to build local knowledge.';rows.append(empty);}
        visible.forEach(doc=>{
            const row=document.createElement('button');row.className='document-row';row.dataset.status=doc.status;row.dataset.documentId=doc.id;row.setAttribute('aria-current',String(doc.id===selectedId));
            const icon=document.createElement('span');icon.textContent='▤';icon.setAttribute('aria-hidden','true');
            const name=document.createElement('span');name.className='doc-name';name.textContent=doc.source_title || doc.filename;
            const meta=document.createElement('small');meta.textContent=(doc.file_type || 'Document')+' · '+timestamp(doc.uploaded_at || doc.retrieved_at);name.append(meta);
            const state=document.createElement('span');state.className='doc-status';state.textContent=stateLabel(doc.status);
            const arrow=document.createElement('span');arrow.textContent='›';row.append(icon,name,state,arrow);row.addEventListener('click',()=>selectDocument(doc));rows.append(row);
        });
    }
    async function refresh() {
        const response=await fetch(endpoint,{cache:'no-store'});
        if(!response.ok) throw new Error('Documents could not be loaded.');
        const data=await response.json();documents=data.documents;
        document.getElementById('knowledge-total').textContent=documents.length;
        document.getElementById('knowledge-indexed').textContent=documents.filter(d=>d.status==='INDEXED').length;
        document.getElementById('knowledge-pending').textContent=documents.filter(d=>d.status==='PENDING').length;
        renderDocuments();
        const selected=documents.find(d=>d.id===selectedId);
        if(selected) selectDocument(selected);
        else if(selectedId!==null) {selectedId=null;detail.replaceChildren();const note=document.createElement('p');note.textContent='Select a file to view details.';detail.append(note);}
    }
    search.addEventListener('input',renderDocuments);
    document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{filter=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));renderDocuments();}));

    async function performAction(doc, action) {
        if (busy) return;
        if (action === 'Delete' && !window.confirm('Delete "' + doc.filename + '" and its indexed text?')) return;
        setBusy(true, action === 'Delete' ? 'Deleting document…' : action === 'Sync' ? 'Synchronizing online source…' : 'Extracting and reindexing…');
        try {
            const response = await fetch(endpoint + doc.id + (action === 'Sync' ? '/sync/' : action === 'Reindex' ? '/reindex/' : '/'), {
                method: action === 'Delete' ? 'DELETE' : 'POST',
                headers: { 'X-CSRFToken': csrftoken },
            });
            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.error || (data.document && data.document.error_message) || 'The operation failed. Please sign in and try again.');
            }
            if (action === 'Sync') {
                const data = await response.json();
                const summary = data.sync.disabled ? 'Synchronization is disabled.' : data.sync.failed ? 'Sync incomplete; last known content retained. See source status.' : 'Source synchronized.';
                toast(summary, data.sync.failed ? 'error' : 'success');
            } else toast(action === 'Delete' ? 'Document deleted.' : 'Document reindexed.', 'success');
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            setBusy(false);
            await refresh().catch(error => { status.textContent = error.message; });
        }
    }

    function upload(file) {
        return new Promise((resolve, reject) => {
            const request = new XMLHttpRequest();
            request.open('POST', endpoint);
            request.setRequestHeader('X-CSRFToken', csrftoken);
            request.timeout = 180000;
            request.upload.addEventListener('progress', event => {
                if (event.lengthComputable) progress.value = Math.round(event.loaded / event.total * 100);
            });
            request.upload.addEventListener('load', () => { status.textContent = 'Extracting text and indexing…'; });
            request.onload = () => {
                let data;
                try { data = JSON.parse(request.responseText); }
                catch (_) { reject(new Error('The upload response could not be read.')); return; }
                if (request.status >= 200 && request.status < 300) resolve(data);
                else reject(new Error(data.error || (data.document && data.document.error_message) || 'Upload failed. Please sign in and try again.'));
            };
            request.onerror = () => reject(new Error('Upload connection failed. Refresh to check whether the document was saved.'));
            request.ontimeout = () => reject(new Error('Upload timed out. Refresh to check its indexing status.'));
            const payload = new FormData();
            payload.append('file', file);
            request.send(payload);
        });
    }

    async function uploadFiles(files) {
        if (!canManage || busy || !files.length) return;
        setBusy(true, 'Uploading document…');
        progress.hidden = false;
        try {
            for (const file of files) {
                progress.value = 0;
                status.textContent = 'Uploading ' + file.name + '…';
                if (file.size > 10 * 1024 * 1024) {
                    toast('Documents must be at most 10 MB.', 'error');
                    continue;
                }
                try { await upload(file); toast('Document uploaded and indexed.', 'success'); }
                catch (error) { toast(error.message, 'error'); }
                await refresh();
            }
        } catch (error) {
            toast(error.message, 'error');
        } finally {
            setBusy(false);
            progress.hidden = true;
            if (picker) picker.value = '';
            await refresh().catch(error => { status.textContent = error.message; });
        }
    }

    if (canManage) {
        syncButton.addEventListener('click', async () => {
            if (busy) return;
            setBusy(true, 'Synchronizing stale online knowledge…');
            try {
                const response = await fetch('/api/knowledge/sync-stale/', { method: 'POST', headers: { 'X-CSRFToken': csrftoken } });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Synchronization failed.');
                const result = data.sync;
                toast(result.disabled ? 'Synchronization is disabled.' : 'Sync: ' + result.queries + ' queries, ' + result.created + ' created, ' + result.updated + ' updated, ' + result.unchanged + ' unchanged, ' + result.failed + ' failed.', result.failed ? 'error' : 'success');
            } catch (error) { toast(error.message, 'error'); }
            finally { setBusy(false); await refresh().catch(error => { status.textContent = error.message; }); }
        });
        uploadButton.addEventListener('click', () => picker.click());
        picker.addEventListener('change', () => uploadFiles(Array.from(picker.files)));
        zone.addEventListener('dragover', event => {
            event.preventDefault();
            zone.style.borderColor = 'var(--accent)';
        });
        zone.addEventListener('dragleave', () => { zone.style.borderColor = ''; });
        zone.addEventListener('drop', event => {
            event.preventDefault();
            zone.style.borderColor = '';
            uploadFiles(Array.from(event.dataTransfer.files));
        });
    }
    document.getElementById('knowledge-refresh').addEventListener('click', () => {
        refresh().catch(error => { status.textContent = error.message; });
    });
    refresh().catch(error => { status.textContent = error.message; });
})();
