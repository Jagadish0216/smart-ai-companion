(function () {
    'use strict';
    const endpoint = '/api/knowledge/documents/';
    const zone = document.getElementById('knowledge-drop-zone');
    const canManage = zone.dataset.canManage === 'true';
    const picker = document.getElementById('knowledge-file-picker');
    const uploadButton = document.getElementById('btn-upload-doc');
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
        rows.querySelectorAll('button').forEach(button => { button.disabled = value; });
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

    async function refresh() {
        const response = await fetch(endpoint, { cache: 'no-store' });
        if (!response.ok) throw new Error('Documents could not be loaded.');
        const data = await response.json();
        rows.replaceChildren();
        document.getElementById('knowledge-total').textContent = data.documents.length;
        document.getElementById('knowledge-indexed').textContent = data.documents.filter(doc => doc.status === 'INDEXED').length;
        document.getElementById('knowledge-pending').textContent = data.documents.filter(doc => doc.status === 'PENDING').length;
        if (!data.documents.length) {
            const row = document.createElement('tr');
            const empty = cell(row, 'No documents yet. Upload a document to build local knowledge.');
            empty.colSpan = 7;
            empty.className = 'empty-state';
            rows.appendChild(row);
        }
        data.documents.forEach(doc => {
            const row = document.createElement('tr');
            cell(row, doc.filename);
            cell(row, doc.file_type + (doc.file_size === null ? '' : ' · ' + (doc.file_size / 1024).toFixed(1) + ' KB'));
            const state = cell(row, '');
            const badge = document.createElement('span');
            badge.className = 'badge ' + (doc.status === 'INDEXED' ? 'badge-success' : doc.status === 'ERROR' ? 'badge-error' : 'badge-warning');
            badge.textContent = doc.status;
            state.appendChild(badge);
            if (doc.error_message) {
                const error = document.createElement('div');
                error.className = 'text-sm text-muted';
                error.textContent = doc.error_message;
                state.appendChild(error);
            }
            cell(row, doc.chunk_count);
            cell(row, timestamp(doc.uploaded_at));
            cell(row, timestamp(doc.indexed_at));
            const actions = cell(row, canManage ? '' : 'Administrator only');
            if (canManage) {
                ['Reindex', 'Delete'].forEach(label => {
                    const button = document.createElement('button');
                    button.className = 'btn btn-sm';
                    button.textContent = label;
                    button.disabled = busy;
                    button.addEventListener('click', () => performAction(doc, label));
                    actions.appendChild(button);
                });
            }
            rows.appendChild(row);
        });
    }

    async function performAction(doc, action) {
        if (busy) return;
        if (action === 'Delete' && !window.confirm('Delete "' + doc.filename + '" and its indexed text?')) return;
        setBusy(true, action === 'Delete' ? 'Deleting document…' : 'Extracting and reindexing…');
        try {
            const response = await fetch(endpoint + doc.id + (action === 'Reindex' ? '/reindex/' : '/'), {
                method: action === 'Reindex' ? 'POST' : 'DELETE',
                headers: { 'X-CSRFToken': csrftoken },
            });
            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.error || (data.document && data.document.error_message) || 'The operation failed. Please sign in and try again.');
            }
            toast(action === 'Delete' ? 'Document deleted.' : 'Document reindexed.', 'success');
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
