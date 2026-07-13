// ── Role-based access guard for dashboard pages ────────────────────────────
function getCurrentAccount() {
    try { return JSON.parse(localStorage.getItem('pawfect_current_account') || 'null'); }
    catch (e) { return null; }
}

function isManager(account) {
    return (account?.role || '').toLowerCase() === 'manager';
}

// ── Demo account switcher ───────────────────────────────────────────────────
const DEMO_ACCOUNTS = {
    manager: { email: 'manager@happypaws.my', role: 'Manager', businessKey: 'happypaws', businessName: 'Happy Paws Pet Care', blankData: false, setupCompleted: true, services: ['grooming', 'boarding', 'daycare'] },
    staff:   { email: 'staff@happypaws.my',   role: 'Staff',   businessKey: 'happypaws', businessName: 'Happy Paws Pet Care', blankData: false, setupCompleted: true, services: ['grooming', 'boarding', 'daycare'] }
};

function switchToAccount(key) {
    const acc = DEMO_ACCOUNTS[key];
    if (!acc) return;
    const email = acc.email.toLowerCase();

    localStorage.setItem('pawfect_current_account', JSON.stringify(acc));
    localStorage.setItem('pawfect_account_state_' + email, JSON.stringify({
        email, role: acc.role, businessKey: acc.businessKey, businessName: acc.businessName,
        blankData: false, setupCompleted: true, services: acc.services
    }));
    localStorage.setItem('pawfect_existing_completed_account', 'true');
    localStorage.setItem('pawfect_first_login', 'shown');

    location.href = isManager(acc) ? 'dashboard.html' : 'dailyoverview.html';
}

function logoutAccount() {
    localStorage.removeItem('pawfect_current_account');
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

    const roleEl = document.getElementById('sidebarAccountRole');
    const businessEl = document.getElementById('sidebarAccountBusiness');
    const emailEl = document.getElementById('sidebarAccountEmail');
    const avatarEl = document.getElementById('sidebarAccountAvatar');
    if (roleEl) roleEl.textContent = role;
    if (businessEl) businessEl.textContent = businessName;
    if (emailEl) emailEl.textContent = email;
    if (avatarEl) avatarEl.innerHTML = isManager(account)
        ? '<img src="icon/team.png" alt="" class="account-avatar-img">'
        : '<img src="icon/user-outline.png" alt="" class="account-avatar-img">';

    const menu = document.getElementById('switchAccountMenu');
    if (!menu) return;

    menu.innerHTML = Object.entries(DEMO_ACCOUNTS).map(([key, acc]) => {
        const isCurrent = acc.email.toLowerCase() === (email || '').toLowerCase();
        const icon = isManager(acc) ? 'team.png' : 'user-outline.png';
        return `
            <button class="${isCurrent ? 'switch-current' : ''}" onclick="switchToAccount('${key}')">
                <span class="nav-icon-wrap switch-btn-icon-wrap"><img src="icon/${icon}" alt="" class="nav-icon"></span> ${acc.role} Demo${isCurrent ? ' (current)' : ''}
            </button>
        `;
    }).join('') + `
        <hr class="sidebar-switch-divider" />
        <a href="login.html">＋ Log in as different account</a>
    `;
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
