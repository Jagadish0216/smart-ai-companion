(() => {
 const button = document.getElementById('toggle-password'), input = document.getElementById('id_password');
 button.addEventListener('click', () => { const show = input.type === 'password'; input.type = show ? 'text' : 'password'; button.textContent = show ? 'Hide' : 'Show'; button.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); button.setAttribute('aria-pressed', String(show)); });
})();
