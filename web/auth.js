// ── Role-based access guard for dashboard pages ────────────────────────────
function getCurrentAccount() {
    try { return JSON.parse(localStorage.getItem('pawfect_current_account') || 'null'); }
    catch (e) { return null; }
}

function isManager(account) {
    return (account?.role || '').toLowerCase() === 'manager';
}

async function logoutAccount(event) {
    event?.preventDefault();
    try {
        await supabaseClient.auth.signOut();
    } finally {
        localStorage.removeItem('pawfect_current_account');
        location.href = 'login.html';
    }
}

function toggleSwitchAccountMenu() {
    document.getElementById('switchAccountMenu')?.classList.toggle('open');
}

function toggleMobileNav() {
    const nav = document.querySelector('.dash-nav');
    const isOpen = nav?.classList.toggle('mobile-nav-open');
    const img = document.querySelector('#mobileNavToggle img');
    if (img) img.src = isOpen ? 'icon/close-circle.png' : 'icon/list-view.png';
}

function renderSidebarAccount() {
    const account = getCurrentAccount();
    const role = account?.role || 'Staff';
    const email = account?.email || '';
    const businessName = account?.businessName || '';
    const logoPath = account?.logoPath || '';

    const roleEl = document.getElementById('sidebarAccountRole');
    const businessEl = document.getElementById('sidebarAccountBusiness');
    const emailEl = document.getElementById('sidebarAccountEmail');
    const avatarEl = document.getElementById('sidebarAccountAvatar');
    if (roleEl) roleEl.textContent = role;
    if (businessEl) businessEl.textContent = businessName;
    if (emailEl) emailEl.textContent = email;
    if (avatarEl) {
        const image = document.createElement('img');
        image.className = 'account-avatar-img';
        if (/^https?:\/\//i.test(logoPath)) {
            image.src = logoPath;
            image.alt = `${businessName || 'Business'} logo`;
        } else {
            image.src = isManager(account) ? 'icon/team.png' : 'icon/user-outline.png';
            image.alt = '';
        }
        avatarEl.replaceChildren(image);
    }

    const menu = document.getElementById('switchAccountMenu');
    if (!menu) return;

    menu.innerHTML = '<a href="login.html">Log in as a different account</a>';
}

document.addEventListener('DOMContentLoaded', () => {
    renderSidebarAccount();

    document.getElementById('logoutBtn')?.addEventListener('click', logoutAccount);

    document.addEventListener('click', event => {
        const wrap = document.querySelector('.sidebar-switch-wrap');
        if (wrap && !wrap.contains(event.target)) {
            document.getElementById('switchAccountMenu')?.classList.remove('open');
        }

        const navEl = document.querySelector('.dash-nav');
        const toggleBtn = document.getElementById('mobileNavToggle');
        if (navEl && navEl.classList.contains('mobile-nav-open') &&
            !navEl.contains(event.target) && !toggleBtn?.contains(event.target)) {
            navEl.classList.remove('mobile-nav-open');
            const img = toggleBtn?.querySelector('img');
            if (img) img.src = 'icon/list-view.png';
        }
    });

    const nav = document.querySelector('.dash-nav');
    const dashboardLink = document.querySelector('.dash-nav-link[href="dashboard.html"]');

    if (isManager(getCurrentAccount())) {
        if (nav && dashboardLink && nav.firstElementChild !== dashboardLink) {
            nav.insertBefore(dashboardLink, nav.firstElementChild);
        }
        return;
    }

    dashboardLink?.remove();

    if (location.pathname.split('/').pop() === 'dashboard.html') {
        location.href = 'dailyoverview.html';
    }
});
