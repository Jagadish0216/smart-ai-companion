(function () {
    'use strict';

    const statusUrl = '/api/system/setup/status/';
    const scanUrl = '/api/system/setup/wifi/scan/';
    const connectUrl = '/api/system/setup/wifi/connect/';
    const networkList = document.getElementById('setup-network-list');
    const message = document.getElementById('setup-message');
    const stateLabel = document.getElementById('setup-state-label');
    const refreshButton = document.getElementById('setup-refresh');
    const form = document.getElementById('setup-connect-form');
    const selectedLabel = document.getElementById('setup-selected-ssid');
    const passwordInput = document.getElementById('setup-wifi-password');
    const connectButton = document.getElementById('setup-connect');
    let selectedSsid = null;

    function csrfToken() {
        for (const part of document.cookie.split(';')) {
            const item = part.trim();
            if (item.startsWith('csrftoken=')) return decodeURIComponent(item.slice(10));
        }
        return '';
    }

    async function readJson(response) {
        try { return await response.json(); } catch (_error) { return {}; }
    }

    function showMessage(value, isError) {
        message.textContent = value || '';
        message.classList.toggle('error', Boolean(isError));
    }

    function showEmpty(value) {
        networkList.replaceChildren();
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = value;
        networkList.appendChild(empty);
    }

    function selectNetwork(network) {
        selectedSsid = network.ssid;
        selectedLabel.textContent = network.ssid;
        passwordInput.value = '';
        form.classList.remove('hidden');
        passwordInput.focus();
    }

    function renderNetworks(networks) {
        networkList.replaceChildren();
        if (!networks.length) {
            showEmpty('No nearby networks were found. Move closer to the router and refresh.');
            return;
        }
        networks.forEach((network) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'network-row';
            const details = document.createElement('span');
            const name = document.createElement('span');
            name.className = 'network-name';
            name.textContent = network.ssid;
            const meta = document.createElement('span');
            meta.className = 'network-meta';
            const signal = network.signal_percent == null ? 'Signal unknown' : `${network.signal_percent}% signal`;
            meta.textContent = `${signal} · ${network.security || 'Security unknown'}`;
            const action = document.createElement('span');
            action.className = 'network-action';
            action.textContent = 'Choose';
            details.append(name, meta);
            row.append(details, action);
            row.addEventListener('click', () => selectNetwork(network));
            networkList.appendChild(row);
        });
    }

    async function loadStatus() {
        const response = await fetch(statusUrl, {credentials: 'same-origin'});
        const data = await readJson(response);
        if (response.ok) {
            stateLabel.textContent = data.state || 'SETUP_AP';
            if (data.message) showMessage(data.message, data.state === 'FAILED');
        }
    }

    async function scanNetworks() {
        refreshButton.disabled = true;
        showMessage('Scanning for nearby Wi-Fi networks…', false);
        try {
            const response = await fetch(scanUrl, {credentials: 'same-origin'});
            const data = await readJson(response);
            if (!response.ok || !data.supported) {
                throw new Error(data.message || 'Wi-Fi scanning is unavailable.');
            }
            renderNetworks(Array.isArray(data.networks) ? data.networks : []);
            showMessage(data.message || 'Choose a network to continue.', Boolean(data.message));
        } catch (_error) {
            showEmpty('Nearby networks could not be loaded.');
            showMessage('Wi-Fi scanning is temporarily unavailable. Please refresh.', true);
        } finally {
            refreshButton.disabled = false;
        }
    }

    async function submitConnection(event) {
        event.preventDefault();
        if (!selectedSsid) return;
        const password = passwordInput.value;
        passwordInput.value = '';
        connectButton.disabled = true;
        refreshButton.disabled = true;
        stateLabel.textContent = 'CONNECTING';
        showMessage(
            'Companion is connecting. This setup network may close; join the selected Wi-Fi, then open smart-ai-companion.local:8000.',
            false
        );
        try {
            const response = await fetch(connectUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
                body: JSON.stringify({ssid: selectedSsid, password: password}),
            });
            const data = await readJson(response);
            stateLabel.textContent = data.state || 'SETUP_AP';
            if (data.success) {
                showMessage(data.message || 'Connected. Rejoin your Wi-Fi and open smart-ai-companion.local:8000.', false);
                form.classList.add('hidden');
                return;
            }
            showMessage(data.message || 'Connection failed. The setup network has been restored.', true);
            connectButton.disabled = false;
            refreshButton.disabled = false;
        } catch (_error) {
            showMessage(
                'The setup connection closed as expected. Join the selected Wi-Fi and open smart-ai-companion.local:8000. If setup failed, rejoin the Companion setup network.',
                false
            );
        }
    }

    refreshButton.addEventListener('click', () => { loadStatus(); scanNetworks(); });
    form.addEventListener('submit', submitConnection);
    document.getElementById('setup-cancel').addEventListener('click', () => {
        selectedSsid = null;
        passwordInput.value = '';
        form.classList.add('hidden');
    });

    loadStatus();
    scanNetworks();
}());
