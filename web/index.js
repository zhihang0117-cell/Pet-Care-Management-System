
//LOGIN
const EXISTING_ACCOUNTS={
  'manager@happypaws.my':{email:'manager@happypaws.my',password:'password123',role:'Manager',businessKey:'happypaws',businessName:'Happy Paws Pet Care',blankData:false,setupCompleted:true,services:['grooming','boarding','daycare']},
  'staff@happypaws.my':{email:'staff@happypaws.my',password:'password123',role:'Staff',businessKey:'happypaws',businessName:'Happy Paws Pet Care',blankData:false,setupCompleted:true,services:['grooming','boarding','daycare']}
};
function q(id){return document.getElementById(id)}
function customAccounts(){try{return JSON.parse(localStorage.getItem('pawfect_custom_accounts')||'[]')}catch(e){return []}}
function completedSetupFor(acc){return {businessName:acc.businessName,services:acc.services||['grooming','boarding','daycare'],setupCompleted:true,newUser:false,profileCompleted:true,serviceConfigured:true,roomConfigured:true,paymentConfigured:true,bookingConfigured:true,whatsappConfigured:true,accountEmail:acc.email,businessKey:acc.businessKey,role:acc.role}}
function saveAccountState(acc,setup){
  const email=String(acc.email||'').toLowerCase();
  const stableAcc={...acc,email};
  localStorage.setItem('pawfect_current_account',JSON.stringify(stableAcc));
  if(email){
    localStorage.setItem('pawfect_account_state_'+email,JSON.stringify({
      email,
      role:stableAcc.role||'Manager',
      businessKey:stableAcc.businessKey,
      businessName:stableAcc.businessName,
      blankData:stableAcc.blankData===true,
      setupCompleted:stableAcc.setupCompleted===true||setup?.setupCompleted===true,
      services:stableAcc.services||setup?.services||['grooming','boarding','daycare']
    }));
  }
  if(setup)localStorage.setItem('pawfect_v10_business_setup',JSON.stringify(setup));
}
function findAccount(email){
  const normalized=email.toLowerCase();
  if(EXISTING_ACCOUNTS[normalized])return EXISTING_ACCOUNTS[normalized];
  return customAccounts().find(a=>String(a.email||'').toLowerCase()===email.toLowerCase());
}
function deny(message){q('loginError').textContent=message;}
function loginPawfectAccount(){
  const email=q('login_email').value.trim().toLowerCase();
  const password=q('login_password').value;
  if(!email||!password){deny('Please enter email and password.');return;}
  const acc=findAccount(email);
  if(!acc||acc.password!==password){deny('Incorrect email or password. Access denied.');return;}
  q('loginError').textContent='';
  if(EXISTING_ACCOUNTS[acc.email]){
    saveAccountState(acc,completedSetupFor(acc));
    localStorage.setItem('pawfect_existing_completed_account','true');
    localStorage.setItem('pawfect_first_login','shown');
    location.href='dailyoverview.html';
    return;
  }
  localStorage.removeItem('pawfect_existing_completed_account');
  if(acc.blankData&&!acc.setupCompleted){
    saveAccountState(acc,{businessName:acc.businessName,services:acc.services||['grooming','boarding','daycare'],setupCompleted:false,newUser:true,accountEmail:acc.email,businessKey:acc.businessKey});
    localStorage.setItem('pawfect_first_login','true');
    location.href='service-configuration.html';
    return;
  }
  saveAccountState(acc,completedSetupFor(acc));
  localStorage.setItem('pawfect_first_login','shown');
  location.href='dailyoverview.html';
}

//Back to top button
const btn = document.getElementById('backToTop');
    window.addEventListener('scroll', () => {
        btn.classList.toggle('visible', window.scrollY > 400);
    });

//Register Account
function q(id){return document.getElementById(id)}
function selectedServices(){return [...document.querySelectorAll('input[name="svc"]:checked')].map(i=>i.value)}
document.querySelectorAll('.multi-service .choice').forEach(card=>{
  card.addEventListener('click',function(e){
    const input=card.querySelector('input');
    if(e.target.tagName!=='INPUT') input.checked=!input.checked;
    card.classList.toggle('active',input.checked);
  });
});
function checkPasswordStrength(){
  const p=q('reg_password').value;let score=0;
  if(p.length>=8)score++; if(/[A-Z]/.test(p))score++; if(/[a-z]/.test(p))score++; if(/[0-9]/.test(p))score++; if(/[^A-Za-z0-9]/.test(p))score++;
  const pct=Math.min(score*20,100); q('passwordBar').style.width=pct+'%'; q('passwordBar').style.background=score<3?'#b45309':score<5?'#a16207':'#166534';
  q('passwordText').textContent=p.length<8?'Password must be at least 8 characters.':(score<3?'Password accepted, but strength is weak.':'Password accepted.');
}
function accountSlug(name){return (name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')||'new-business')+'-'+Date.now().toString().slice(-5)}
function registerBusinessAccount(){
  const businessName=q('reg_business_name').value.trim();
  const email=q('reg_email').value.trim();
  const password=q('reg_password').value;
  const confirm=q('reg_confirm').value;
  const services=selectedServices();
  if(!businessName){alert('Please enter business name.');return}
  if(!email){alert('Please enter login email.');return}
  if(!services.length){alert('Please select at least one service.');return}
  if(password.length<8){alert('Password must be at least 8 characters.');return}
  if(password!==confirm){alert('Password and confirm password must match.');return}
  const businessKey=accountSlug(businessName);
  const account={email,password,role:'Manager',businessKey,businessName,blankData:true,setupCompleted:false,services,hint:'Registered business account starts blank until configuration is completed.'};
  const list=JSON.parse(localStorage.getItem('pawfect_custom_accounts')||'[]').filter(a=>a.email.toLowerCase()!==email.toLowerCase());
  list.push(account);localStorage.setItem('pawfect_custom_accounts',JSON.stringify(list));
  const setup={services,setupCompleted:false,newUser:true,profileCompleted:false,serviceConfigured:false,roomConfigured:false,paymentConfigured:false,bookingConfigured:false,whatsappConfigured:false,businessKey,businessName,email,createdAt:new Date().toISOString()};
  localStorage.setItem('pawfect_current_account',JSON.stringify(account));
  localStorage.removeItem('pawfect_existing_completed_account');
  localStorage.setItem('pawfect_v10_business_setup',JSON.stringify(setup));
  localStorage.setItem('pawfect_first_login','true');
  location.href='service-configuration.html';
}


  // Hamburger menu
    const hamburger = document.getElementById('hamburger');
    const mobileNav  = document.getElementById('mobileNav');
    if (hamburger) {
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

    // FAQ smooth accordion
    document.querySelectorAll('.faq-item').forEach(details => {
        const summary = details.querySelector('.faq-question');
        const answer  = details.querySelector('.faq-answer');
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