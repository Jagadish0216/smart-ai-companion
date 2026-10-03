(function () {
    'use strict';

    const endpoint = '/api/system/resource-manager/';

    function text(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    }

    function formatPercent(value) {
        return typeof value === 'number' ? `${value.toFixed(1)}%` : 'Unavailable';
    }

    function formatBytes(value) {
        if (typeof value !== 'number') return 'Unavailable';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let amount = value;
        let unit = 0;
        while (amount >= 1024 && unit < units.length - 1) {
            amount /= 1024;
            unit += 1;
        }
        return `${amount.toFixed(unit >= 3 ? 1 : 0)} ${units[unit]}`;
    }

    function badgeClass(level) {
        if (level === 'HEALTHY') return 'badge badge-success';
        if (level === 'CAUTION') return 'badge badge-warning';
        if (level === 'CONSTRAINED' || level === 'CRITICAL') return 'badge badge-error';
        return 'badge badge-neutral';
    }

    function updateLevel(name, health) {
        const badge = document.getElementById(`resource-${name}-level`);
        if (!badge) return;
        badge.textContent = health.level;
        badge.className = badgeClass(health.level);
        text(`resource-${name}-detail`, health.summary);
    }

    function renderReasons(reasons) {
        const container = document.getElementById('resource-reasons');
        container.replaceChildren();
        if (!Array.isArray(reasons) || reasons.length === 0) {
            const normal = document.createElement('div');
            normal.className = 'resource-reason healthy';
            normal.textContent = 'No resource pressure is currently detected.';
            container.appendChild(normal);
            return;
        }
        for (const reason of reasons) {
            const item = document.createElement('div');
            item.className = `resource-reason ${String(reason.severity || '').toLowerCase()}`;
            const code = document.createElement('span');
            code.className = 'resource-reason-code';
            code.textContent = reason.code || 'STATUS';
            const message = document.createElement('span');
            message.textContent = reason.message || 'Additional attention is recommended.';
            item.append(code, message);
            container.appendChild(item);
        }
    }

    function render(data) {
        const snapshot = data.snapshot || {};
        const health = data.subsystem_health || {};
        const cpu = snapshot.cpu || {};
        const memory = snapshot.memory || {};
        const storage = snapshot.storage || {};
        const thermal = snapshot.thermal || {};
        const network = snapshot.network || {};
        const ai = snapshot.ai || {};
        const recommendation = data.ai_recommendation || {};

        text('resource-overall-health', data.overall_health || 'UNKNOWN');
        text('resource-profile', data.recommended_profile || 'UNKNOWN');
        text('resource-explanation', data.explanation || 'No recommendation is available.');
        document.getElementById('resource-manager').dataset.health = data.overall_health || 'UNKNOWN';

        for (const name of ['cpu', 'memory', 'storage', 'temperature', 'network', 'ai']) {
            updateLevel(name, health[name] || {level: 'UNKNOWN', summary: 'Telemetry unavailable.'});
        }

        text('resource-cpu-value', formatPercent(cpu.utilization_percent));
        text('resource-memory-value', formatPercent(memory.used_percent));
        text('resource-storage-value', `${formatPercent(storage.used_percent)} used · ${formatBytes(storage.free_bytes)} free`);
        text('resource-temperature-value', typeof thermal.cpu_temperature_c === 'number' ? `${thermal.cpu_temperature_c.toFixed(1)}°C` : 'Unavailable');
        text('resource-network-value', network.lan_connected === true ? `LAN · ${network.internet_state || 'UNKNOWN'}` : network.lan_connected === false ? 'LAN disconnected' : 'Unavailable');
        text('resource-ai-value', `${ai.configured_engine || 'Unknown'} · ${ai.configured_model || 'Unknown model'}`);

        text('resource-model-class', recommendation.preferred_model_class || '—');
        text('resource-generation-budget', recommendation.generation_budget || '—');
        text('resource-local-ai', recommendation.local_ai_allowed === true ? 'Allowed' : 'Not recommended');
        const actions = data.storage_recommendation && data.storage_recommendation.actions;
        text('resource-storage-actions', Array.isArray(actions) && actions.length ? actions.join(', ') : 'None');

        const services = snapshot.services || {};
        const serviceStates = Object.values(services).map((service) => service.status);
        text(
            'resource-services',
            serviceStates.length
                ? `${serviceStates.filter((state) => state === 'READY').length} / ${serviceStates.length} ready`
                : 'Unavailable'
        );
        renderReasons(data.reasons);

        const generated = snapshot.snapshot_generated_at;
        text('resource-generated-at', generated ? `Snapshot ${new Date(generated).toLocaleString()}` : 'Snapshot unavailable');
        const timing = data.timing || {};
        const timingText = (
            typeof timing.collection_ms === 'number' &&
            typeof timing.policy_ms === 'number'
        )
            ? `Collected in ${timing.collection_ms.toFixed(2)} ms · policy ${timing.policy_ms.toFixed(2)} ms`
            : 'Collection time unavailable';
        text('resource-timing', timingText);
    }

    async function loadResourceManager() {
        try {
            const response = await fetch(endpoint, {credentials: 'same-origin', cache: 'no-store'});
            if (!response.ok) throw new Error('Resource Manager request failed.');
            render(await response.json());
        } catch (_error) {
            document.getElementById('resource-manager-error').classList.remove('hidden');
            text('resource-overall-health', 'UNKNOWN');
            text('resource-profile', 'Unavailable');
            text('resource-explanation', 'Current telemetry could not be collected.');
        }
    }

    loadResourceManager();
    window.setInterval(loadResourceManager, 10000);
}());
