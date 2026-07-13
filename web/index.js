// ── Utility ──────────────────────────────────────────────────────────────────
function q(id) { return document.getElementById(id); }

// ── Back to top ───────────────────────────────────────────────────────────────
const btn = q('backToTop');
if (btn) {
    window.addEventListener('scroll', () => {
        btn.classList.toggle('visible', window.scrollY > 400);
    });
}

// ── Hamburger menu ────────────────────────────────────────────────────────────
const hamburger = q('hamburger');
const mobileNav  = q('mobileNav');
if (hamburger && mobileNav) {
    hamburger.addEventListener('click', () => {
        const open = mobileNav.classList.toggle('open');
        hamburger.classList.toggle('active');
        hamburger.setAttribute('aria-expanded', open);
        mobileNav.setAttribute('aria-hidden', !open);
    });
    mobileNav.querySelectorAll('a').forEach(link => {
        link.addEventListener('click', () => {
            mobileNav.classList.remove('open');
            hamburger.classList.remove('active');
            hamburger.setAttribute('aria-expanded', 'false');
            mobileNav.setAttribute('aria-hidden', 'true');
        });
    });
}

// ── FAQ smooth accordion ──────────────────────────────────────────────────────
document.querySelectorAll('.faq-item').forEach(details => {
    const summary = details.querySelector('.faq-question');
    const answer  = details.querySelector('.faq-answer');
    if (!summary || !answer) return;
    summary.addEventListener('click', e => {
        e.preventDefault();
        if (details.open) {
            answer.style.maxHeight = answer.scrollHeight + 'px';
            answer.offsetHeight;
            answer.style.maxHeight = '0px';
            answer.addEventListener('transitionend', () => {
                details.open = false;
                answer.style.maxHeight = '';
            }, { once: true });
        } else {
            details.open = true;
            const h = answer.scrollHeight;
            answer.style.maxHeight = '0px';
            answer.offsetHeight;
            answer.style.maxHeight = h + 'px';
            answer.addEventListener('transitionend', () => {
                answer.style.maxHeight = '';
            }, { once: true });
        }
    });
});

// ── Scroll reveal ─────────────────────────────────────────────────────────────
const revealObserver = new IntersectionObserver(entries => {
    entries.forEach(e => {
        if (e.isIntersecting) {
            e.target.classList.add('visible');
            revealObserver.unobserve(e.target);
        }
    });
}, { threshold: 0.08 });

document.querySelectorAll('.content-block, .step-node, .faq-item, .metric-item').forEach((el, i) => {
    el.classList.add('reveal');
    el.style.transitionDelay = (i % 4) * 80 + 'ms';
    revealObserver.observe(el);
});

// ── Login ─────────────────────────────────────────────────────────────────────
const EXISTING_ACCOUNTS = {
    'manager@happypaws.my': { email: 'manager@happypaws.my', password: 'password123', role: 'Manager', businessKey: 'happypaws', businessName: 'Happy Paws Pet Care', blankData: false, setupCompleted: true, services: ['grooming', 'boarding', 'daycare'] },
    'staff@happypaws.my':   { email: 'staff@happypaws.my',   password: 'password123', role: 'Staff',   businessKey: 'happypaws', businessName: 'Happy Paws Pet Care', blankData: false, setupCompleted: true, services: ['grooming', 'boarding', 'daycare'] }
};

function customAccounts() {
    try { return JSON.parse(localStorage.getItem('pawfect_custom_accounts') || '[]'); } catch (e) { return []; }
}
function completedSetupFor(acc) {
    return { businessName: acc.businessName, services: acc.services || ['grooming', 'boarding', 'daycare'], setupCompleted: true, newUser: false, profileCompleted: true, serviceConfigured: true, roomConfigured: true, paymentConfigured: true, bookingConfigured: true, whatsappConfigured: true, accountEmail: acc.email, businessKey: acc.businessKey, role: acc.role };
}
function saveAccountState(acc, setup) {
    const email = String(acc.email || '').toLowerCase();
    const stableAcc = { ...acc, email };
    localStorage.setItem('pawfect_current_account', JSON.stringify(stableAcc));
    if (email) {
        localStorage.setItem('pawfect_account_state_' + email, JSON.stringify({
            email, role: stableAcc.role || 'Manager', businessKey: stableAcc.businessKey,
            businessName: stableAcc.businessName, blankData: stableAcc.blankData === true,
            setupCompleted: stableAcc.setupCompleted === true || setup?.setupCompleted === true,
            services: stableAcc.services || setup?.services || ['grooming', 'boarding', 'daycare']
        }));
    }
    if (setup) localStorage.setItem('pawfect_v10_business_setup', JSON.stringify(setup));
}
function findAccount(email) {
    const normalized = email.toLowerCase();
    if (EXISTING_ACCOUNTS[normalized]) return EXISTING_ACCOUNTS[normalized];
    return customAccounts().find(a => String(a.email || '').toLowerCase() === normalized);
}
function deny(message) { const el = q('loginError'); if (el) el.textContent = message; }

function landingPageFor(acc) {
    return (acc.role || '').toLowerCase() === 'manager' ? 'dashboard.html' : 'dailyoverview.html';
}

function loginPawfectAccount() {
    const emailEl = q('login_email'), passEl = q('login_password');
    if (!emailEl || !passEl) return;
    const email = emailEl.value.trim().toLowerCase();
    const password = passEl.value;
    if (!email || !password) { deny('Please enter email and password.'); return; }
    const acc = findAccount(email);
    if (!acc || acc.password !== password) { deny('Incorrect email or password. Access denied.'); return; }
    deny('');
    if (EXISTING_ACCOUNTS[acc.email]) {
        saveAccountState(acc, completedSetupFor(acc));
        localStorage.setItem('pawfect_existing_completed_account', 'true');
        localStorage.setItem('pawfect_first_login', 'shown');
        location.href = landingPageFor(acc); return;
    }
    localStorage.removeItem('pawfect_existing_completed_account');
    if (acc.blankData && !acc.setupCompleted) {
        saveAccountState(acc, { businessName: acc.businessName, services: acc.services || ['grooming', 'boarding', 'daycare'], setupCompleted: false, newUser: true, accountEmail: acc.email, businessKey: acc.businessKey });
        localStorage.setItem('pawfect_first_login', 'true');
        location.href = landingPageFor(acc); return;
    }
    saveAccountState(acc, completedSetupFor(acc));
    localStorage.setItem('pawfect_first_login', 'shown');
    location.href = landingPageFor(acc);
}

// ── Register ──────────────────────────────────────────────────────────────────
function selectedServices() {
    return [...document.querySelectorAll('input[name="svc"]:checked')].map(i => i.value);
}
document.querySelectorAll('.multi-service .choice, .service-choice .choice').forEach(card => {
    card.addEventListener('click', function (e) {
        const input = card.querySelector('input');
        if (!input) return;
        if (e.target.tagName !== 'INPUT') input.checked = !input.checked;
        card.classList.toggle('active', input.checked);
    });
});
function checkPasswordStrength() {
    const p = q('reg_password')?.value; if (p === undefined) return;
    let score = 0;
    if (p.length >= 8) score++;
    if (/[A-Z]/.test(p)) score++;
    if (/[a-z]/.test(p)) score++;
    if (/[0-9]/.test(p)) score++;
    if (/[^A-Za-z0-9]/.test(p)) score++;
    const pct = Math.min(score * 20, 100);
    const bar = q('passwordBar'), txt = q('passwordText');
    if (bar) { bar.style.width = pct + '%'; bar.style.background = score < 3 ? '#b45309' : score < 5 ? '#a16207' : '#166534'; }
    if (txt) txt.textContent = p.length < 8 ? 'Password must be at least 8 characters.' : score < 3 ? 'Weak — try adding uppercase, numbers or symbols.' : 'Password strength looks good.';
}
function accountSlug(name) {
    return (name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'new-business') + '-' + Date.now().toString().slice(-5);
}
function registerBusinessAccount() {
    const email    = q('reg_email')?.value.trim();
    const password = q('reg_password')?.value;
    const confirm  = q('reg_confirm')?.value;
    const services = selectedServices();
    if (!email)        { alert('Please enter manager login email.'); return; }
    if (!services.length) { alert('Please select at least one service.'); return; }
    if (!password || password.length < 8) { alert('Password must be at least 8 characters.'); return; }
    if (password !== confirm) { alert('Password and confirm password must match.'); return; }
    const businessName = '';
    const businessKey = accountSlug(email.split('@')[0]);
    const account = { email, password, role: 'Manager', businessKey, businessName, blankData: true, setupCompleted: false, services };
    const list = customAccounts().filter(a => a.email.toLowerCase() !== email.toLowerCase());
    list.push(account);
    localStorage.setItem('pawfect_custom_accounts', JSON.stringify(list));
    const setup = { services, setupCompleted: false, newUser: true, profileCompleted: false, serviceConfigured: false, roomConfigured: false, paymentConfigured: false, bookingConfigured: false, whatsappConfigured: false, businessKey, businessName, email, createdAt: new Date().toISOString() };
    localStorage.setItem('pawfect_current_account', JSON.stringify(account));
    localStorage.removeItem('pawfect_existing_completed_account');
    localStorage.setItem('pawfect_v10_business_setup', JSON.stringify(setup));
    localStorage.setItem('pawfect_first_login', 'true');
    location.href = 'reg-setup.html';
}

// ── Setup page helpers ────────────────────────────────────────────────────────
function showFilename(input, targetId) {
    const el = q(targetId);
    if (el && input.files.length) el.innerHTML = '<img src="icon/upload.png" alt="" class="row-icon">' + input.files[0].name;
}
function saveServiceConfiguration() {
    // extend with actual save logic as needed
    alert('Configuration saved.');
}

// ── Setup page: Staff Accounts ────────────────────────────────────────────────
// Reuses the same pawfect_custom_accounts storage as Settings → Accounts, so
// anyone added here already shows up there once the portal is entered.
function getCurrentAccount() {
    try { return JSON.parse(localStorage.getItem('pawfect_current_account') || 'null'); } catch (e) { return null; }
}
function getSetupBusinessAccounts(businessKey) {
    const seed = Object.values(EXISTING_ACCOUNTS)
        .filter(a => a.businessKey === businessKey)
        .map(a => ({ email: a.email, role: a.role }));
    const custom = customAccounts()
        .filter(a => a.businessKey === businessKey)
        .map(a => ({ email: a.email, role: a.role }));
    const byEmail = new Map();
    [...seed, ...custom].forEach(a => byEmail.set(a.email.toLowerCase(), a));
    return [...byEmail.values()];
}
function renderSetupAccountsPanel() {
    const tbody = q('setupAccountsTableBody');
    if (!tbody) return;

    const account = getCurrentAccount();
    const list = getSetupBusinessAccounts(account?.businessKey);
    const currentEmail = String(account?.email || '').toLowerCase();

    tbody.innerHTML = list.map(a => {
        const isSelf = a.email.toLowerCase() === currentEmail;
        return `
            <tr>
                <td>${a.email}${isSelf ? ' <span class="field-note">(you)</span>' : ''}</td>
                <td><span class="status-tag ${a.role === 'Manager' ? 'status-scheduled' : 'status-done'}">${a.role}</span></td>
                <td>${isSelf ? '<span class="field-note">—</span>' : `<button class="edit-btn" onclick="removeSetupStaffAccount('${a.email}')">Remove</button>`}</td>
            </tr>
        `;
    }).join('');
}
function addSetupStaffAccount() {
    const account = getCurrentAccount();
    const email = q('setupAccountEmail').value.trim().toLowerCase();
    const password = q('setupAccountPassword').value;
    const role = q('setupAccountRole').value;
    const note = q('setupAccountsAddNote');

    if (!email || !password) {
        if (note) { note.textContent = 'Enter an email and password.'; note.style.color = '#DC2626'; }
        return;
    }
    if (password.length < 8) {
        if (note) { note.textContent = 'Password must be at least 8 characters.'; note.style.color = '#DC2626'; }
        return;
    }
    if (getSetupBusinessAccounts(account?.businessKey).some(a => a.email.toLowerCase() === email)) {
        if (note) { note.textContent = 'An account with this email already exists.'; note.style.color = '#DC2626'; }
        return;
    }

    const list = customAccounts().filter(a => a.email.toLowerCase() !== email);
    list.push({
        email, password, role,
        businessKey: account?.businessKey, businessName: account?.businessName,
        blankData: false, setupCompleted: true, services: account?.services || ['grooming', 'boarding', 'daycare']
    });
    localStorage.setItem('pawfect_custom_accounts', JSON.stringify(list));

    q('setupAccountEmail').value = '';
    q('setupAccountPassword').value = '';
    q('setupAccountRole').value = 'Staff';
    if (note) { note.textContent = '✓ Account added.'; note.style.color = '#059669'; }

    renderSetupAccountsPanel();
}
function removeSetupStaffAccount(email) {
    const account = getCurrentAccount();
    const normalizedEmail = email.toLowerCase();
    if (normalizedEmail === String(account?.email || '').toLowerCase()) return;
    if (!confirm(`Remove ${email}? This cannot be undone.`)) return;

    const updated = customAccounts().filter(a => a.email.toLowerCase() !== normalizedEmail);
    localStorage.setItem('pawfect_custom_accounts', JSON.stringify(updated));
    renderSetupAccountsPanel();
}
if (q('setupAccountsTableBody')) renderSetupAccountsPanel();
