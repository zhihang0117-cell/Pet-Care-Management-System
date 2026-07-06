// ── Role-based access guard for dashboard pages ────────────────────────────
function getCurrentAccount() {
    try { return JSON.parse(localStorage.getItem('pawfect_current_account') || 'null'); }
    catch (e) { return null; }
}

function isManager(account) {
    return (account?.role || '').toLowerCase() === 'manager';
}

document.addEventListener('DOMContentLoaded', () => {
    if (isManager(getCurrentAccount())) return;

    document.querySelector('.dash-nav-link[href="dashboard.html"]')?.remove();

    if (location.pathname.split('/').pop() === 'dashboard.html') {
        location.href = 'dailyoverview.html';
    }
});
