/* Figma presence. No decorative polling or synthetic audio waveform. */
(() => {
 let state = 'IDLE';
 const setState = value => {
   state = ({PROCESSING:'THINKING',ALERT:'ERROR',READY:'IDLE',OFFLINE:'LOCAL MODE'})[value] || value;
   document.querySelectorAll('[data-presence]').forEach(p => { p.dataset.state = state; p.setAttribute('aria-label', `Companion presence, ${state.toLowerCase()}`); });
 };
 window.CompanionController = {setState, getState:() => state};
})();
