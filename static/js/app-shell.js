(() => {
 'use strict';
 const page = document.body.dataset.page;
 const desktopPrefix = {assistant:'2-194',knowledge:'2-293',device:'2-383',dashboard:'home'}[page] || 'more';
 const media = window.matchMedia('(max-width:600px)');
 const updateIcons = () => {
   const prefix = media.matches && page === 'assistant' ? '2-438' : desktopPrefix;
   document.querySelectorAll('[data-nav-icon]').forEach(img => { img.src = `/static/img/figma/${prefix}-${img.dataset.navIcon}Icon.svg`; });
   const mic=document.getElementById('voice-icon-mic'); if(mic) mic.src=`/static/img/figma/${media.matches?'2-438':'2-194'}-MicIcon.svg`;
 };
 media.addEventListener('change',updateIcons); updateIcons();
 if (desktopPrefix==='more') document.querySelector('.rail-link:last-child').classList.add('active');
 const menu = document.getElementById('more-menu');
 menu.addEventListener('click', e => { if(e.target === menu && (e.clientX < menu.getBoundingClientRect().left || e.clientX > menu.getBoundingClientRect().right || e.clientY < menu.getBoundingClientRect().top || e.clientY > menu.getBoundingClientRect().bottom)) menu.close(); });
 if (window.visualViewport && page === 'assistant') {
   const update = () => { document.documentElement.style.setProperty('--visual-height', `${visualViewport.height}px`); document.body.classList.toggle('keyboard-open', visualViewport.height < window.innerHeight * .78); };
   visualViewport.addEventListener('resize', update); update();
 }
 // Keep the backend's network state distinct from inference readiness.
 const net = document.getElementById('global-internet-status');
 if(net && net.dataset.networkState === 'OFFLINE') {
   document.getElementById('global-internet-label').textContent = 'Local mode';
   net.title = 'Internet unavailable. Local features depend on device readiness.';
 }
 document.querySelectorAll('button[title]:not([aria-label])').forEach(b => b.setAttribute('aria-label', b.title));
})();
