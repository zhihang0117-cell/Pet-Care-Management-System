// ── Utility ──────────────────────────────────────────────────────────────────
function q(id) { return document.getElementById(id); }
function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

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

// ── Login ────────────────────────────────────────────────────────────────────
// Remove credentials left by the retired mock-account implementation.
localStorage.removeItem('pawfect_custom_accounts');
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
function deny(message) { const el = q('loginError'); if (el) el.textContent = message; }

function landingPageFor(acc) {
    return (acc.role || '').toLowerCase() === 'manager' ? 'dashboard.html' : 'dailyoverview.html';
}

// Real login: Supabase Auth directly (no backend round-trip needed for this
// part — see backend/README.md "Auth & registration flow"), then one call to
// the backend's GET /api/accounts/me to learn this login's role/company.
// The resulting object is stored under the SAME 'pawfect_current_account' key
// the rest of the app (auth.js, common.js) already reads — so nothing else
// needs to change to start showing real data.
async function loginPawfectAccount() {
    const emailEl = q('login_email'), passEl = q('login_password');
    if (!emailEl || !passEl) return;
    const email = emailEl.value.trim().toLowerCase();
    const password = passEl.value;
    if (!email || !password) { deny('Please enter email and password.'); return; }

    deny('');
    const { data, error } = await supabaseClient.auth.signInWithPassword({ email, password });
    if (error || !data.session) {
        deny('Incorrect email or password. Access denied.');
        return;
    }

    let me;
    try {
        me = await api.get('/accounts/me');
    } catch (err) {
        deny(err.message || 'Logged in, but could not load your account. Is the backend running?');
        await supabaseClient.auth.signOut();
        return;
    }

    const acc = {
        email,
        role: me.role === 'manager' ? 'Manager' : 'Staff',
        businessKey: String(me.company_id),
        businessName: me.company?.company_name || '',
        logoPath: me.company?.logo_path || '',
        blankData: false,
        setupCompleted: true,
        services: me.company?.settings_json?.selected_services || ['grooming', 'boarding', 'daycare'],
    };
    saveAccountState(acc, completedSetupFor(acc));
    localStorage.setItem('pawfect_existing_completed_account', 'true');
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
// Registration is atomic on the backend (POST /api/auth/register-company
// creates the Supabase Auth user AND the company+manager account in one
// transaction — see backend/src/routes/auth.js), but the UI collects
// email/services here (step 1) and the password/business profile on
// reg-setup.html (step 2). No password is persisted between pages.
function registerBusinessAccount() {
    const email    = q('reg_email')?.value.trim();
    const services = selectedServices();
    if (!email)        { alert('Please enter manager login email.'); return; }
    if (!q('reg_email').checkValidity()) { alert('Please enter a valid manager email.'); return; }
    if (!services.length) { alert('Please select at least one service.'); return; }

    sessionStorage.setItem('pawfect_pending_registration', JSON.stringify({ email, services }));
    location.href = 'reg-setup.html';
}

// ── Setup page helpers ────────────────────────────────────────────────────────
function showFilename(input, targetId) {
    const el = q(targetId);
    if (!el || !input.files.length) return;
    el.replaceChildren();
    const icon = document.createElement('img');
    icon.src = 'icon/upload.png';
    icon.alt = '';
    icon.className = 'row-icon';
    el.append(icon, document.createTextNode(input.files[0].name));
}
// Fires the real account creation: combines the step-1 credentials (held in
// sessionStorage) with this page's business profile fields, calls the
// backend's register-company endpoint, then signs in with Supabase to
// establish a session for the rest of the app.
async function saveServiceConfiguration() {
    const pending = JSON.parse(sessionStorage.getItem('pawfect_pending_registration') || 'null');
    if (!pending) {
        alert('Your registration session expired. Please register again.');
        location.href = 'register.html';
        return;
    }

    const businessName = q('businessname')?.value.trim();
    const country = q('country')?.value.trim();
    const streetAddress = q('street')?.value.trim();
    const city = q('city')?.value.trim();
    const state = q('state')?.value.trim();
    const postcode = q('zip')?.value.trim();
    const businessDescription = q('cfg_description')?.value.trim();
    const password = q('reg_password')?.value || '';
    const confirmPassword = q('reg_confirm')?.value || '';

    if (!businessName) { alert('Please enter your business/company name.'); return; }
    if (!postcode)     { alert('Please enter your postcode.'); return; }
    if (password.length < 8) { alert('Password must be at least 8 characters.'); return; }
    if (password !== confirmPassword) { alert('Password and confirm password must match.'); return; }

    const requiredSetupFields = [
        'hour_Mon', 'hour_Tue', 'hour_Wed', 'hour_Thu', 'hour_Fri', 'hour_Sat', 'hour_Sun',
        'cfg_invoicePrefix', 'cfg_taxName',
        'cfg_language', 'timezone', 'currency', 'height', 'weight', 'cfg_confirmRule'
    ];
    if (requiredSetupFields.some(id => !q(id)?.value.trim())) {
        alert('Please complete all required service configuration fields.');
        return;
    }

    const logoFile = q('cfg_logo')?.files?.[0] || null;
    const policyFiles = Object.fromEntries(["general", ...pending.services].map(service => [
        service, q(`policy_${service}`)?.files?.[0] || null,
    ]));
    const invalidPolicy = Object.entries(policyFiles).find(([, file]) => file && !file.name.toLowerCase().endsWith('.docx'));
    const oversizedPolicy = Object.entries(policyFiles).find(([, file]) => file && file.size > 10 * 1024 * 1024);
    if (invalidPolicy) {
        alert(`${invalidPolicy[0]} policy must be a valid DOCX file.`);
        return;
    }
    if (oversizedPolicy) {
        alert(`${oversizedPolicy[0]} policy must be 10 MB or smaller.`);
        return;
    }
    if (logoFile && !['image/png', 'image/jpeg'].includes(logoFile.type)) {
        alert('Business logo must be a PNG or JPG file.');
        return;
    }
    if (logoFile && logoFile.size > 2 * 1024 * 1024) {
        alert('Business logo must be 2 MB or smaller.');
        return;
    }

    const settings = {
        selected_services: pending.services,
        business_hours: {
            Mon: q('hour_Mon').value.trim(), Tue: q('hour_Tue').value.trim(),
            Wed: q('hour_Wed').value.trim(), Thu: q('hour_Thu').value.trim(),
            Fri: q('hour_Fri').value.trim(), Sat: q('hour_Sat').value.trim(),
            Sun: q('hour_Sun').value.trim(),
        },
        closed_dates: q('cfg_closedDates').value.trim(),
        payment_methods: {
            cash: q('pay_cash').checked,
            card: q('pay_card').checked,
            qr: q('pay_qr').checked,
            online: q('pay_online').checked,
        },
        invoice_prefix: q('cfg_invoicePrefix').value.trim(),
        tax_name: q('cfg_taxName').value.trim(),
        language: q('cfg_language').value,
        timezone: q('timezone').value,
        currency: q('currency').value,
        height_unit: q('height').value,
        weight_unit: q('weight').value,
        booking_url: q('cfg_bookingUrl').value.trim(),
        whatsapp_number: q('cfg_whatsapp').value.trim(),
        confirm_rule: q('cfg_confirmRule').value,
    };

    const button = document.querySelector('.setup-actions .btn-primary');
    const originalButtonText = button?.textContent || 'Save & Enter Portal';
    if (button) {
        button.disabled = true;
        button.textContent = 'Creating company…';
    }

    let companyCreated = false;
    try {
        const result = await api.registerCompany({
            email: pending.email,
            password,
            businessName,
            country,
            streetAddress,
            city,
            state,
            postcode,
            businessDescription,
            settings,
            teamAccounts: setupTeamAccounts,
        });
        companyCreated = true;

        const { error: signInError } = await supabaseClient.auth.signInWithPassword({
            email: pending.email,
            password,
        });
        if (signInError) {
            sessionStorage.removeItem('pawfect_pending_registration');
            setupTeamAccounts = [];
            alert('Your company and accounts were created, but automatic sign-in failed. Please sign in from the login page.');
            location.href = 'index.html';
            return;
        }

        let logoPath = '';
        let logoUploadWarning = '';
        if (logoFile) {
            try {
                const company = await api.uploadCompanyLogo(logoFile);
                logoPath = company.logo_path || '';
            } catch (logoError) {
                logoUploadWarning = logoError.message || 'The business logo could not be uploaded.';
            }
        }

        const documentWarnings = [];
        for (const [service, file] of Object.entries(policyFiles)) {
            if (!file) continue;
            try {
                if (button) button.textContent = `Indexing ${service} policy…`;
                await api.uploadCompanyDocument(file, service, 'policies');
            } catch (documentError) {
                documentWarnings.push(`${service}: ${documentError.message}`);
            }
        }

        const acc = {
            email: pending.email,
            role: result.role === 'manager' ? 'Manager' : 'Staff',
            businessKey: String(result.company_id),
            businessName,
            logoPath,
            blankData: true,
            setupCompleted: true,
            services: pending.services,
        };
        saveAccountState(acc, completedSetupFor(acc));
        localStorage.removeItem('pawfect_existing_completed_account');
        localStorage.setItem('pawfect_first_login', 'true');
        sessionStorage.removeItem('pawfect_pending_registration');
        q('reg_password').value = '';
        q('reg_confirm').value = '';
        setupTeamAccounts = [];

        const setupWarnings = [
            logoUploadWarning ? `Logo: ${logoUploadWarning}` : '',
            ...documentWarnings,
        ].filter(Boolean);
        if (setupWarnings.length) alert(`Company created, but some uploads need attention:\n${setupWarnings.join('\n')}`);

        location.href = 'dashboard.html';
    } catch (error) {
        console.error(error);
        alert(companyCreated
            ? 'Your company was created, but setup could not finish in this browser. Please sign in from the login page.'
            : (error.message || 'Company creation failed.'));
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = originalButtonText;
        }
    }
}

// ── Setup page: Staff Accounts ────────────────────────────────────────────────
// Teammates are staged only in memory. Save & Enter Portal creates their real
// Supabase Auth users and accounts rows; passwords are never written to web storage.
let setupTeamAccounts = [];
function renderSetupAccountsPanel() {
    const tbody = q('setupAccountsTableBody');
    if (!tbody) return;

    const pending = JSON.parse(sessionStorage.getItem('pawfect_pending_registration') || 'null');
    const currentEmail = String(pending?.email || '').toLowerCase();
    const list = [
        ...(pending ? [{ email: pending.email, role: 'Manager', isSelf: true }] : []),
        ...setupTeamAccounts.map(account => ({ ...account, isSelf: false })),
    ];

    tbody.innerHTML = list.map((a, index) => {
        const isSelf = a.isSelf || a.email.toLowerCase() === currentEmail;
        const roleLabel = a.role.charAt(0).toUpperCase() + a.role.slice(1).toLowerCase();
        return `
            <tr>
                <td>${escapeHtml(a.email)}${isSelf ? ' <span class="field-note">(you)</span>' : ''}</td>
                <td><span class="status-tag ${roleLabel === 'Manager' ? 'status-scheduled' : 'status-done'}">${roleLabel}</span></td>
                <td>${isSelf ? '<span class="field-note">—</span>' : `<button class="edit-btn setup-account-remove" data-index="${index - 1}">Remove</button>`}</td>
            </tr>
        `;
    }).join('');
    tbody.querySelectorAll('.setup-account-remove').forEach(button => {
        button.addEventListener('click', () => removeSetupStaffAccountByIndex(Number(button.dataset.index)));
    });
}
function addSetupStaffAccount() {
    const email = q('setupAccountEmail').value.trim().toLowerCase();
    const password = q('setupAccountPassword').value;
    const role = q('setupAccountRole').value;
    const note = q('setupAccountsAddNote');

    if (!email || !password) {
        if (note) { note.textContent = 'Enter an email and password.'; note.style.color = '#DC2626'; }
        return;
    }
    if (!q('setupAccountEmail').checkValidity()) {
        if (note) { note.textContent = 'Enter a valid email address.'; note.style.color = '#DC2626'; }
        return;
    }
    if (password.length < 8) {
        if (note) { note.textContent = 'Password must be at least 8 characters.'; note.style.color = '#DC2626'; }
        return;
    }
    const pending = JSON.parse(sessionStorage.getItem('pawfect_pending_registration') || 'null');
    if (email === String(pending?.email || '').toLowerCase() || setupTeamAccounts.some(a => a.email === email)) {
        if (note) { note.textContent = 'An account with this email already exists.'; note.style.color = '#DC2626'; }
        return;
    }

    setupTeamAccounts.push({ email, password, role: role.toLowerCase() });

    q('setupAccountEmail').value = '';
    q('setupAccountPassword').value = '';
    q('setupAccountRole').value = 'Staff';
    if (note) {
        note.textContent = '✓ Account queued. Save & Enter Portal will create it in Supabase.';
        note.style.color = '#059669';
    }

    renderSetupAccountsPanel();
}
function removeSetupStaffAccount(email) {
    const normalizedEmail = email.toLowerCase();
    const pending = JSON.parse(sessionStorage.getItem('pawfect_pending_registration') || 'null');
    if (normalizedEmail === String(pending?.email || '').toLowerCase()) return;
    if (!confirm(`Remove ${email}? This cannot be undone.`)) return;

    setupTeamAccounts = setupTeamAccounts.filter(a => a.email !== normalizedEmail);
    renderSetupAccountsPanel();
}
function removeSetupStaffAccountByIndex(index) {
    const account = setupTeamAccounts[index];
    if (account) removeSetupStaffAccount(account.email);
}
if (q('setupAccountsTableBody')) renderSetupAccountsPanel();

function configureRegistrationPolicyCards() {
    const container = q('selectedServiceCards');
    if (!container) return;
    const pending = JSON.parse(sessionStorage.getItem('pawfect_pending_registration') || 'null');
    const enabled = new Set(pending?.services || []);
    container.querySelectorAll('[data-service]').forEach(card => {
        const service = card.dataset.service;
        card.hidden = service !== 'general' && !enabled.has(service);
    });
    const labels = { grooming: 'Grooming', boarding: 'Boarding / Hotel', daycare: 'Daycare' };
    const text = q('selectedServicesText');
    if (text) text.textContent = (pending?.services || []).map(service => labels[service] || service).join(' · ');
}

configureRegistrationPolicyCards();
