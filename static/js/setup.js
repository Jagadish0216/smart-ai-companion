(function () {
    'use strict';

    const PHASES = Object.freeze({
        IDLE: 'IDLE',
        CONNECTING: 'CONNECTING',
        WAITING_FOR_NETWORK: 'WAITING_FOR_NETWORK',
        SUCCESS: 'SUCCESS',
        FAILURE: 'FAILURE',
        TIMED_OUT: 'TIMED_OUT',
    });
    const HANDOFF_POLL_INTERVAL_MS = 2500;
    const HANDOFF_REQUEST_TIMEOUT_MS = 4000;
    const HANDOFF_OVERALL_TIMEOUT_MS = 90000;
    const HANDOFF_INITIAL_DELAY_MS = 1200;
    const SUCCESS_REDIRECT_DELAY_MS = 2000;

    const statusUrl = '/api/system/setup/status/';
    const scanUrl = '/api/system/setup/wifi/scan/';
    const connectUrl = '/api/system/setup/wifi/connect/';
    const handoffStatusUrl = '/api/system/setup/handoff-status/';
    const networkToolbar = document.getElementById('setup-network-toolbar');
    const networkList = document.getElementById('setup-network-list');
    const message = document.getElementById('setup-message');
    const stateLabel = document.getElementById('setup-state-label');
    const refreshButton = document.getElementById('setup-refresh');
    const form = document.getElementById('setup-connect-form');
    const selectedLabel = document.getElementById('setup-selected-ssid');
    const passwordInput = document.getElementById('setup-wifi-password');
    const connectButton = document.getElementById('setup-connect');
    const cancelButton = document.getElementById('setup-cancel');
    const handoffPanel = document.getElementById('setup-handoff-panel');
    const handoffIcon = document.getElementById('setup-handoff-icon');
    const handoffTitle = document.getElementById('setup-handoff-title');
    const handoffDetail = document.getElementById('setup-handoff-detail');
    const handoffGuidance = document.getElementById('setup-handoff-guidance');
    const handoffRetry = document.getElementById('setup-handoff-retry');
    const setupSsid = handoffPanel.dataset.setupSsid;
    let selectedSsid = null;
    let phase = PHASES.IDLE;
    let polling = false;
    let pollRunId = 0;
    let pollStartTimer = null;
    let redirectTimer = null;

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

    function delay(milliseconds) {
        return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    }

    function showMessage(value, isError) {
        message.textContent = value || '';
        message.classList.toggle('error', Boolean(isError));
    }

    function setHandoffPhase(nextPhase, options) {
        phase = nextPhase;
        handoffPanel.dataset.phase = nextPhase;
        handoffPanel.classList.toggle('hidden', nextPhase === PHASES.IDLE);
        handoffIcon.textContent = options.icon;
        handoffTitle.textContent = options.title;
        handoffDetail.textContent = options.detail;
        handoffGuidance.textContent = options.guidance || '';
        handoffRetry.classList.toggle('hidden', !options.retry);
    }

    function showProvisioningSurface(visible) {
        networkToolbar.classList.toggle('hidden', !visible);
        networkList.classList.toggle('hidden', !visible);
        form.classList.toggle('hidden', !visible);
    }

    function stopHandoffPolling() {
        pollRunId += 1;
        polling = false;
        if (pollStartTimer !== null) {
            window.clearTimeout(pollStartTimer);
            pollStartTimer = null;
        }
    }

    function resetHandoff() {
        stopHandoffPolling();
        if (redirectTimer !== null) {
            window.clearTimeout(redirectTimer);
            redirectTimer = null;
        }
        setHandoffPhase(PHASES.IDLE, {
            icon: '•••', title: '', detail: '', guidance: '', retry: false,
        });
    }

    function showConnecting(targetSsid) {
        stateLabel.textContent = 'CONNECTING';
        showMessage('', false);
        showProvisioningSurface(false);
        setHandoffPhase(PHASES.CONNECTING, {
            icon: '•••',
            title: `Connecting Smart Companion to ${targetSsid}…`,
            detail: 'The setup Wi-Fi will disconnect temporarily.',
            guidance: `Your computer or phone may reconnect to ${targetSsid}. Keep this page open.`,
            retry: false,
        });
    }

    function showWaiting(targetSsid) {
        stateLabel.textContent = 'WAITING FOR COMPANION';
        setHandoffPhase(PHASES.WAITING_FOR_NETWORK, {
            icon: '•••',
            title: 'Waiting for Smart Companion…',
            detail: `Make sure this device reconnects to ${targetSsid}.`,
            guidance: 'Temporary DNS and network errors are expected while Wi-Fi changes.',
            retry: false,
        });
    }

    function showSuccess(connectedSsid) {
        stopHandoffPolling();
        stateLabel.textContent = 'CONNECTED';
        const title = connectedSsid
            ? `Connected to ${connectedSsid}`
            : 'Connected to Wi-Fi';
        setHandoffPhase(PHASES.SUCCESS, {
            icon: '✓',
            title: title,
            detail: 'Smart Companion is ready.',
            guidance: 'Opening your dashboard…',
            retry: false,
        });
        redirectTimer = window.setTimeout(() => {
            window.location.assign('/');
        }, SUCCESS_REDIRECT_DELAY_MS);
    }

    function showConnectionFailure(targetSsid, detail) {
        stopHandoffPolling();
        stateLabel.textContent = 'SETUP AP';
        connectButton.disabled = false;
        refreshButton.disabled = false;
        cancelButton.disabled = false;
        showProvisioningSurface(true);
        setHandoffPhase(PHASES.FAILURE, {
            icon: '!',
            title: `Could not connect to ${targetSsid}.`,
            detail: detail || 'Check the Wi-Fi password and try again.',
            guidance: `If needed, reconnect to ${setupSsid} and reopen smart-ai-companion.local:8000/setup/.`,
            retry: false,
        });
    }

    function showHandoffTimeout(targetSsid) {
        stopHandoffPolling();
        stateLabel.textContent = 'WAITING FOR COMPANION';
        setHandoffPhase(PHASES.TIMED_OUT, {
            icon: '?',
            title: "We couldn't reach Smart Companion yet.",
            detail: `Make sure this device is connected to ${targetSsid}, then open:`,
            guidance: 'smart-ai-companion.local:8000',
            retry: true,
        });
    }

    async function requestHandoffStatus() {
        const controller = new AbortController();
        const timeout = window.setTimeout(
            () => controller.abort(),
            HANDOFF_REQUEST_TIMEOUT_MS
        );
        try {
            const response = await fetch(handoffStatusUrl, {
                method: 'GET',
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'Accept': 'application/json'},
                signal: controller.signal,
            });
            if (!response.ok) return null;
            return await readJson(response);
        } finally {
            window.clearTimeout(timeout);
        }
    }

    async function startHandoffPolling(targetSsid) {
        if (
            polling ||
            phase === PHASES.SUCCESS ||
            phase === PHASES.FAILURE
        ) return;
        if (pollStartTimer !== null) {
            window.clearTimeout(pollStartTimer);
            pollStartTimer = null;
        }
        polling = true;
        const runId = ++pollRunId;
        const deadline = Date.now() + HANDOFF_OVERALL_TIMEOUT_MS;
        showWaiting(targetSsid);

        try {
            while (runId === pollRunId && Date.now() < deadline) {
                try {
                    const status = await requestHandoffStatus();
                    if (runId !== pollRunId) return;
                    if (
                        status &&
                        status.state === 'NORMAL_MODE' &&
                        status.ready === true
                    ) {
                        showSuccess(status.connected_ssid);
                        return;
                    }
                    if (
                        status &&
                        (status.state === 'FAILED' || status.last_result === 'FAILED')
                    ) {
                        showConnectionFailure(
                            targetSsid,
                            'Check the Wi-Fi password and try again.'
                        );
                        return;
                    }
                } catch (_error) {
                    // DNS failure, connection refusal, abort, and network loss
                    // are expected while the client leaves the setup AP.
                }
                await delay(HANDOFF_POLL_INTERVAL_MS);
            }
            if (runId === pollRunId) showHandoffTimeout(targetSsid);
        } finally {
            if (runId === pollRunId) polling = false;
        }
    }

    function beginHandoffPolling(targetSsid) {
        if (polling || pollStartTimer !== null) return;
        pollStartTimer = window.setTimeout(() => {
            pollStartTimer = null;
            startHandoffPolling(targetSsid);
        }, HANDOFF_INITIAL_DELAY_MS);
    }

    function showEmpty(value) {
        networkList.replaceChildren();
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = value;
        networkList.appendChild(empty);
    }

    function selectNetwork(network) {
        resetHandoff();
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
        try {
            const response = await fetch(statusUrl, {credentials: 'same-origin'});
            const data = await readJson(response);
            if (response.ok) {
                stateLabel.textContent = data.state || 'SETUP_AP';
                if (data.message) showMessage(data.message, data.state === 'FAILED');
            }
        } catch (_error) {
            // The dedicated handoff poll owns transition-time network errors.
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
        if (!selectedSsid || polling) return;

        const targetSsid = selectedSsid;
        let password = passwordInput.value;
        const requestBody = JSON.stringify({ssid: targetSsid, password: password});
        passwordInput.value = '';
        password = '';
        connectButton.disabled = true;
        refreshButton.disabled = true;
        cancelButton.disabled = true;
        showConnecting(targetSsid);
        beginHandoffPolling(targetSsid);

        try {
            const response = await fetch(connectUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken(),
                },
                body: requestBody,
            });
            const data = await readJson(response);
            if (!response.ok || !data.success) {
                showConnectionFailure(
                    targetSsid,
                    data.message || 'The connection request could not be started.'
                );
                return;
            }
            if (
                phase === PHASES.SUCCESS ||
                phase === PHASES.FAILURE ||
                phase === PHASES.TIMED_OUT
            ) return;
            startHandoffPolling(targetSsid);
        } catch (_error) {
            // Losing this POST response is expected when wlan0 leaves AP mode.
            // The read-only handoff poll will determine the authoritative result.
            if (
                phase !== PHASES.SUCCESS &&
                phase !== PHASES.FAILURE &&
                phase !== PHASES.TIMED_OUT
            ) {
                startHandoffPolling(targetSsid);
            }
        }
    }

    refreshButton.addEventListener('click', () => { loadStatus(); scanNetworks(); });
    form.addEventListener('submit', submitConnection);
    cancelButton.addEventListener('click', () => {
        resetHandoff();
        selectedSsid = null;
        passwordInput.value = '';
        form.classList.add('hidden');
    });
    handoffRetry.addEventListener('click', () => {
        if (selectedSsid) startHandoffPolling(selectedSsid);
    });

    loadStatus();
    scanNetworks();
}());
