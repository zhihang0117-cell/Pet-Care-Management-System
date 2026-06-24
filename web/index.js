
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
    location.href='performance.html';
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
  location.href='performance.html';
}
