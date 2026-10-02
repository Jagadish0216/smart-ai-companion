(function () {
    'use strict';

    const statusUrl = '/api/system/network/';
    const scanUrl = '/api/system/network/wifi/scan/';
    const connectUrl = '/api/system/network/wifi/connect/';
    const config = document.getElementById('network-page-config');
    const canConnect = config && config.dataset.canConnect === 'true';
    const list = document.getElementById('network-list');
    const scanMessage = document.getElementById('network-scan-message');
    const refreshButton = document.getElementById('network-refresh');
    const connectPanel = document.getElementById('network-connect-panel');
    const connectForm = document.getElementById('network-connect-form');
    const passwordInput = document.getElementById('network-password');
    const selectedSsidLabel = document.getElementById('network-selected-ssid');
    const connectButton = document.getElementById('network-connect-submit');
    let selectedSsid = null;

    function text(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value == null || value === '' ? '—' : value;
    }

    function badge(id, label, kind) {
        const element = document.getElementById(id);
        if (!element) return;
        element.textContent = label;
        element.className = `badge badge-${kind}`;
    }

    async function readJson(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    }

    async function loadStatus() {
        try {
            const response = await fetch(statusUrl, {credentials: 'same-origin'});
            const data = await readJson(response);
            if (!response.ok) throw new Error('status unavailable');
            const wifi = data.wifi || {};
            const ethernet = data.ethernet || {};
            const internet = data.internet || {};
            badge(
                'network-wifi-state',
                wifi.connected ? 'Connected' : (wifi.supported ? 'Disconnected' : 'Not available'),
                wifi.connected ? 'success' : 'neutral'
            );
            text('network-wifi-ssid', wifi.ssid);
            text('network-wifi-signal', wifi.signal_percent == null ? null : `${wifi.signal_percent}%`);
            text('network-wifi-ip', wifi.ip_address);
            badge(
                'network-ethernet-state',
                ethernet.connected ? 'Connected' : (ethernet.supported ? 'Disconnected' : 'Not available'),
                ethernet.connected ? 'success' : 'neutral'
            );
            text('network-ethernet-interface', ethernet.interface);
            text('network-ethernet-ip', ethernet.ip_address);
            const internetState = internet.state || 'UNKNOWN';
            badge(
                'network-internet-state',
                internetState.charAt(0) + internetState.slice(1).toLowerCase(),
                internetState === 'FULL' ? 'success' : (internetState === 'LIMITED' ? 'warning' : 'neutral')
            );
            text('network-hostname', data.hostname);
        } catch (_error) {
            badge('network-wifi-state', 'Unavailable', 'error');
            badge('network-ethernet-state', 'Unavailable', 'error');
            badge('network-internet-state', 'Unknown', 'neutral');
        }
    }

    function showScanMessage(message, isError) {
        if (!scanMessage) return;
        scanMessage.textContent = message || '';
        scanMessage.className = message
            ? `network-message ${isError ? 'error' : 'info'}`
            : 'network-message hidden';
    }

    function emptyNetworks(title, description) {
        list.replaceChildren();
        const wrapper = document.createElement('div');
        wrapper.className = 'empty-state';
        const heading = document.createElement('div');
        heading.className = 'empty-state-title';
        heading.textContent = title;
        const detail = document.createElement('div');
        detail.className = 'empty-state-desc';
        detail.textContent = description;
        wrapper.append(heading, detail);
        list.appendChild(wrapper);
    }

    function chooseNetwork(network) {
        if (!canConnect || !connectPanel) return;
        selectedSsid = network.ssid;
        selectedSsidLabel.textContent = network.ssid;
        passwordInput.value = '';
        connectPanel.classList.remove('hidden');
        passwordInput.focus();
    }

    function renderNetworks(networks) {
        list.replaceChildren();
        if (!networks.length) {
            emptyNetworks('No networks found', 'Refresh the scan or move the Companion closer to the access point.');
            return;
        }
        networks.forEach((network) => {
            const row = document.createElement('div');
            row.className = 'network-row';
            const details = document.createElement('div');
            details.className = 'network-row-details';
            const name = document.createElement('div');
            name.className = 'network-row-name';
            name.textContent = network.ssid;
            const meta = document.createElement('div');
            meta.className = 'network-row-meta';
            const signal = network.signal_percent == null ? 'Signal unknown' : `${network.signal_percent}% signal`;
            meta.textContent = `${signal} · ${network.security || 'Unknown security'}`;
            details.append(name, meta);
            const actions = document.createElement('div');
            actions.className = 'flex items-center gap-2';
            if (network.connected) {
                const connected = document.createElement('span');
                connected.className = 'badge badge-success';
                connected.textContent = 'Connected';
                actions.appendChild(connected);
            }
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm';
            button.textContent = canConnect ? 'Connect' : 'Admin required';
            button.disabled = !canConnect || network.connected;
            button.addEventListener('click', () => chooseNetwork(network));
            actions.appendChild(button);
            row.append(details, actions);
            list.appendChild(row);
        });
    }

    async function scanNetworks() {
        refreshButton.disabled = true;
        showScanMessage('Scanning for nearby networks…', false);
        try {
            const response = await fetch(scanUrl, {credentials: 'same-origin'});
            const data = await readJson(response);
            if (!response.ok) throw new Error('scan unavailable');
            if (!data.supported) {
                emptyNetworks('Wi-Fi scanning unavailable', data.message || 'No supported Wi-Fi adapter was found.');
                showScanMessage(data.message || 'Wi-Fi scanning is unavailable.', true);
                return;
            }
            renderNetworks(Array.isArray(data.networks) ? data.networks : []);
            showScanMessage(data.message || '', Boolean(data.message));
        } catch (_error) {
            emptyNetworks('Could not scan networks', 'The Companion could not load nearby Wi-Fi networks.');
            showScanMessage('Available networks could not be loaded.', true);
        } finally {
            refreshButton.disabled = false;
            if (window.lucide) window.lucide.createIcons();
        }
    }

    async function submitConnection(event) {
        event.preventDefault();
        if (!selectedSsid || !passwordInput) return;
        const warning = 'Changing Wi-Fi may temporarily disconnect this dashboard. Continue?';
        if (!window.confirm(warning)) return;

        let password = passwordInput.value;
        const requestBody = JSON.stringify({ssid: selectedSsid, password: password});
        passwordInput.value = '';
        password = '';
        connectButton.disabled = true;
        try {
            const response = await fetch(connectUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken') || '',
                },
                body: requestBody,
            });
            const data = await readJson(response);
            if (data.success) {
                showToast(data.message || 'Connected successfully.', 'success');
                connectPanel.classList.add('hidden');
                selectedSsid = null;
                window.setTimeout(loadStatus, 1000);
            } else if (data.state === 'CONNECTING') {
                showToast(data.message || 'The connection is still being applied.', 'info');
            } else {
                showToast(data.message || 'Could not connect to the selected network.', 'error');
            }
        } catch (_error) {
            showToast(
                'The dashboard connection was interrupted. Rejoin the target Wi-Fi network and try the Companion again.',
                'error'
            );
        } finally {
            passwordInput.value = '';
            connectButton.disabled = false;
        }
    }

    if (refreshButton) refreshButton.addEventListener('click', () => {
        loadStatus();
        scanNetworks();
    });
    if (connectForm) connectForm.addEventListener('submit', submitConnection);
    const cancelButton = document.getElementById('network-connect-cancel');
    if (cancelButton) cancelButton.addEventListener('click', () => {
        passwordInput.value = '';
        selectedSsid = null;
        connectPanel.classList.add('hidden');
    });

    loadStatus();
    scanNetworks();
}());
