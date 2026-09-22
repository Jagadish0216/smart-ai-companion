/**
 * Smart AI Companion — Companion Visual Engine
 * Controls the digital twin representation of the physical companion.
 * Manages eye rendering, state transitions, OLED display, and animations.
 */
document.addEventListener('DOMContentLoaded', () => {
    const chassis = document.getElementById('companion-chassis');
    const container = document.getElementById('companion-container');
    const eyeLeft = document.getElementById('eye-left');
    const eyeRight = document.getElementById('eye-right');
    const stateLabel = document.getElementById('companion-state-label');

    if (!chassis || !eyeLeft || !eyeRight) return;

    // ── State ──
    let currentState = 'IDLE';
    let currentExpression = 'NEUTRAL';
    let currentEyeStyle = 'ROUND';
    let currentAnimation = 'IDLE';
    let blinkInterval = null;
    let lookInterval = null;
    let pulseInterval = null;

    // ── OLED Display ──
    let oledDisplay = document.getElementById('companion-oled');
    // OLED is now a separate element in the template, not overlaid on the face

    // ── Eye Shapes ──
    const EYE_SHAPES = {
        ROUND: {
            NEUTRAL:   { l: "M120 80 Q 140 80 160 80 L 160 120 Q 140 120 120 120 Z", r: "M240 80 Q 260 80 280 80 L 280 120 Q 260 120 240 120 Z" },
            HAPPY:     { l: "M120 110 Q 140 80 160 110", r: "M240 110 Q 260 80 280 110" },
            THINKING:  { l: "M120 100 Q 140 90 160 100", r: "M240 100 Q 260 110 280 100" },
            LISTENING: { l: "M120 90 Q 140 70 160 90", r: "M240 90 Q 260 70 280 90" },
            SLEEP:     { l: "M120 110 Q 140 110 160 110", r: "M240 110 Q 260 110 280 110" },
            ALERT:     { l: "M120 75 Q 140 65 160 75 L 160 125 Q 140 125 120 125 Z", r: "M240 75 Q 260 65 280 75 L 280 125 Q 260 125 240 125 Z" },
        },
        SQUARE: {
            NEUTRAL: { l: "M120 80 L 160 80 L 160 120 L 120 120 Z", r: "M240 80 L 280 80 L 280 120 L 240 120 Z" },
            SLEEP:   { l: "M120 110 L 160 110 L 160 120 L 120 120 Z", r: "M240 110 L 280 110 L 280 120 L 240 120 Z" },
        },
        SLIT: {
            NEUTRAL: { l: "M130 80 Q 140 80 150 80 L 150 120 Q 140 120 130 120 Z", r: "M250 80 Q 260 80 270 80 L 270 120 Q 260 120 250 120 Z" },
        },
        WIDE: {
            NEUTRAL: { l: "M110 70 Q 140 70 170 70 L 170 130 Q 140 130 110 130 Z", r: "M230 70 Q 260 70 290 70 L 290 130 Q 260 130 230 130 Z" },
        },
    };
    const BLINK = { l: "M120 100 Q 140 100 160 100", r: "M240 100 Q 260 100 280 100" };

    // ── State Colors ──
    const STATE_COLORS = {
        IDLE:      { eye: '#3b82f6', glow: 'rgba(59,130,246,0.4)',  css: '' },
        READY:     { eye: '#3b82f6', glow: 'rgba(59,130,246,0.4)',  css: '' },
        LISTENING: { eye: '#22c55e', glow: 'rgba(34,197,94,0.4)',   css: 'state-listening' },
        THINKING:  { eye: '#a855f7', glow: 'rgba(168,85,247,0.4)',  css: 'state-thinking' },
        SPEAKING:  { eye: '#3b82f6', glow: 'rgba(59,130,246,0.5)',  css: 'state-speaking' },
        RESPONDING:{ eye: '#3b82f6', glow: 'rgba(59,130,246,0.5)',  css: 'state-speaking' },
        ALERT:     { eye: '#ef4444', glow: 'rgba(239,68,68,0.4)',   css: 'state-alert' },
        ERROR:     { eye: '#ef4444', glow: 'rgba(239,68,68,0.4)',   css: 'state-alert' },
        OFFLINE:   { eye: '#505868', glow: 'rgba(80,88,104,0.2)',   css: '' },
    };

    // ── Expression mapping from state ──
    const STATE_EXPRESSION = {
        IDLE: 'NEUTRAL', READY: 'NEUTRAL', LISTENING: 'LISTENING',
        THINKING: 'THINKING', SPEAKING: 'HAPPY', RESPONDING: 'HAPPY',
        ALERT: 'ALERT', ERROR: 'NEUTRAL', OFFLINE: 'SLEEP',
    };

    // ── Mouse tracking ──
    document.addEventListener('mousemove', (e) => {
        if (!container || currentState === 'OFFLINE' || currentState === 'SLEEP') return;
        const rect = container.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const dx = (e.clientX - cx) / rect.width;
        const dy = (e.clientY - cy) / rect.height;
        const mx = dx * 15;
        const my = dy * 10;
        const g = chassis.querySelector('.companion-svg g');
        if (g) g.style.transform = `translate(${mx}px, ${my}px)`;
    });

    // ── Core functions ──
    function resetAnimations() {
        if (blinkInterval) { clearInterval(blinkInterval); blinkInterval = null; }
        if (lookInterval) { clearInterval(lookInterval); lookInterval = null; }
        if (pulseInterval) { clearInterval(pulseInterval); pulseInterval = null; }
    }

    function getEyeShape(expression, style) {
        const styleShapes = EYE_SHAPES[style] || EYE_SHAPES.ROUND;
        return styleShapes[expression] || styleShapes.NEUTRAL || EYE_SHAPES.ROUND.NEUTRAL;
    }

    function setEyes(expression, style) {
        currentExpression = expression || currentExpression;
        currentEyeStyle = style || currentEyeStyle;
        const shape = getEyeShape(currentExpression, currentEyeStyle);
        eyeLeft.setAttribute('d', shape.l);
        eyeRight.setAttribute('d', shape.r);
    }

    function blink() {
        eyeLeft.setAttribute('d', BLINK.l);
        eyeRight.setAttribute('d', BLINK.r);
        setTimeout(() => setEyes(currentExpression, currentEyeStyle), 150);
    }

    function startBlinking() {
        blinkInterval = setInterval(blink, 3500 + Math.random() * 2500);
    }

    function applyState(state) {
        // Normalize
        state = state.toUpperCase();
        if (state === 'READY') state = 'IDLE';

        currentState = state;
        const config = STATE_COLORS[state] || STATE_COLORS.IDLE;

        // Update eye color
        eyeLeft.style.stroke = config.eye;
        eyeRight.style.stroke = config.eye;
        eyeLeft.style.filter = `drop-shadow(0 0 8px ${config.glow})`;
        eyeRight.style.filter = `drop-shadow(0 0 8px ${config.glow})`;

        // Update chassis class
        chassis.className = 'companion-face-chassis';
        if (config.css) chassis.classList.add(config.css);

        // Update expression
        const expr = STATE_EXPRESSION[state] || 'NEUTRAL';
        setEyes(expr, currentEyeStyle);

        // Update label
        if (stateLabel) stateLabel.textContent = state;

        // Restart animations
        resetAnimations();
        if (state !== 'OFFLINE') startBlinking();

        // State-specific animations
        if (state === 'THINKING') {
            pulseInterval = setInterval(() => {
                const g = chassis.querySelector('.companion-svg g');
                if (g) {
                    g.style.transform = `translate(${Math.sin(Date.now()/400) * 3}px, 0)`;
                }
            }, 50);
        } else if (state === 'LISTENING') {
            // Subtle scale pulse
            let scale = 1;
            pulseInterval = setInterval(() => {
                scale = scale === 1 ? 1.02 : 1;
                chassis.style.transform = `scale(${scale})`;
            }, 1000);
        }
    }

    function applyAnimation(anim) {
        currentAnimation = anim;
        resetAnimations();
        if (anim === 'IDLE') {
            startBlinking();
        } else if (anim === 'SCAN') {
            let scanX = 0;
            lookInterval = setInterval(() => {
                const g = chassis.querySelector('.companion-svg g');
                scanX = scanX === 0 ? 12 : (scanX === 12 ? -12 : 0);
                if (g) g.style.transform = `translate(${scanX}px, 0)`;
            }, 700);
        } else if (anim === 'BOOT') {
            let count = 0;
            blinkInterval = setInterval(() => {
                if (count > 5) { clearInterval(blinkInterval); applyAnimation('IDLE'); return; }
                blink();
                count++;
            }, 300);
        } else if (anim === 'NONE') {
            // Static — no animation
        }
    }

    function applyBrightness(level) {
        const opacity = Math.max(0.2, level / 100);
        const g = chassis.querySelector('.companion-svg g');
        if (g) g.style.opacity = opacity;
    }

    function applyDisplayText(text) {
        if (!oledDisplay) return;
        if (text && text.trim().length > 0) {
            oledDisplay.textContent = text;
            oledDisplay.classList.remove('hidden');
        } else {
            oledDisplay.classList.add('hidden');
        }
    }

    function syncCompanionVisuals(data) {
        if (data.state) applyState(data.state);
        if (data.expression || data.eye_style) setEyes(data.expression, data.eye_style);
        if (data.animation) applyAnimation(data.animation);
        if (data.brightness !== undefined) applyBrightness(data.brightness);
        if (data.display_text !== undefined) applyDisplayText(data.display_text);
    }

    // ── Public API (backward compatible) ──
    window.CompanionController = {
        setState: applyState,
        setExpression: (expr) => setEyes(expr, null),
        setEyeStyle: (style) => setEyes(null, style),
        setAnimation: applyAnimation,
        setBrightness: applyBrightness,
        setDisplayText: applyDisplayText,
        getState: () => currentState,
        syncVisuals: syncCompanionVisuals,
    };

    // ── Initialize from server state ──
    fetch('/api/system/companion/state/')
        .then(r => r.json())
        .then(data => syncCompanionVisuals(data))
        .catch(err => {
            console.warn('Could not fetch initial companion state:', err.message);
            applyState('IDLE');
            startBlinking();
        });
});
