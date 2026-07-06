function q(id) { return document.getElementById(id); }

function getToday() { return new Date().toISOString().slice(0, 10); }
function addDays(dateString, days) {
  const date = new Date(dateString);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}
function getStartOfWeek(dateString) {
  const date = new Date(dateString);
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  date.setDate(date.getDate() + diff);
  return date.toISOString().slice(0, 10);
}
function getDateRange(startDate, endDate) {
  const dates = [];
  let current = new Date(startDate);
  const end = new Date(endDate);
  while (current <= end) {
    dates.push(current.toISOString().slice(0, 10));
    current.setDate(current.getDate() + 1);
  }
  return dates;
}

const today = getToday();

const serviceNames = {
  S001: 'Basic Grooming', S002: 'Full Grooming',
  S003: 'Standard Boarding', S004: 'Deluxe Boarding',
  S005: 'Half-Day Daycare', S006: 'Full-Day Daycare'
};

const rooms = [
  { id: 'R001', name: 'Room A', type: 'boarding' },
  { id: 'R002', name: 'Room B', type: 'boarding' },
  { id: 'R003', name: 'Playroom 1', type: 'daycare' },
  { id: 'R004', name: 'Playroom 2', type: 'daycare' }
];

let bookings = [
  { id: 'B001', customerName: 'Alicia Lee',   petName: 'Milo',   serviceType: 'grooming', serviceId: 'S002', date: today, time: '09:00', status: 'pending',   checkInDate: '', checkOutDate: '', roomId: '' },
  { id: 'B002', customerName: 'Jason Lim',    petName: 'Coco',   serviceType: 'grooming', serviceId: 'S001', date: today, time: '10:30', status: 'scheduled', checkInDate: '', checkOutDate: '', roomId: '' },
  { id: 'B003', customerName: 'Farah Hana',   petName: 'Luna',   serviceType: 'grooming', serviceId: 'S002', date: today, time: '08:00', status: 'pending',   checkInDate: '', checkOutDate: '', roomId: '' },
  { id: 'B004', customerName: 'Tan Wei',      petName: 'Buddy',  serviceType: 'grooming', serviceId: 'S001', date: today, time: '14:00', status: 'done',      checkInDate: '', checkOutDate: '', roomId: '' },
  { id: 'B005', customerName: 'Wong Mei',     petName: 'Simba',  serviceType: 'grooming', serviceId: 'S002', date: today, time: '16:00', status: 'scheduled', checkInDate: '', checkOutDate: '', roomId: '' },
  { id: 'B006', customerName: 'Nur Aina',     petName: 'Snowy',  serviceType: 'grooming', serviceId: 'S001', date: today, time: '11:00', status: 'no_show',   checkInDate: '', checkOutDate: '', roomId: '' },

  { id: 'B007', customerName: 'David Chong',   petName: 'Rocky', serviceType: 'boarding', serviceId: 'S003', date: addDays(today, -2), time: '09:00', status: 'scheduled', checkInDate: addDays(today, -2), checkOutDate: today,             roomId: 'R001' },
  { id: 'B008', customerName: 'Siti Zainab',   petName: 'Bella', serviceType: 'boarding', serviceId: 'S004', date: today,              time: '10:00', status: 'pending',   checkInDate: today,               checkOutDate: addDays(today, 3), roomId: 'R002' },
  { id: 'B009', customerName: 'Kumar Raj',     petName: 'Max',   serviceType: 'boarding', serviceId: 'S003', date: addDays(today, -1), time: '09:30', status: 'scheduled', checkInDate: addDays(today, -1), checkOutDate: addDays(today, 2), roomId: 'R001' },
  { id: 'B010', customerName: 'Angeline Foo',  petName: 'Cleo',  serviceType: 'boarding', serviceId: 'S004', date: today,              time: '11:30', status: 'pending',   checkInDate: today,               checkOutDate: addDays(today, 4), roomId: 'R002' },
  { id: 'B011', customerName: 'Hafiz Rahman',  petName: 'Tommy', serviceType: 'boarding', serviceId: 'S003', date: addDays(today, -3), time: '08:30', status: 'scheduled', checkInDate: addDays(today, -3), checkOutDate: today,             roomId: 'R001' },
  { id: 'B012', customerName: 'Michelle Yap',  petName: 'Nala',  serviceType: 'boarding', serviceId: 'S004', date: addDays(today, 1),  time: '09:00', status: 'scheduled', checkInDate: addDays(today, 1),  checkOutDate: addDays(today, 5), roomId: 'R002' },

  { id: 'B013', customerName: 'Chong Li Wei', petName: 'Oreo',  serviceType: 'daycare', serviceId: 'S006', date: today, time: '08:00', status: 'pending',   checkInDate: '', checkOutDate: '', roomId: 'R003' },
  { id: 'B014', customerName: 'Aisyah Bakar', petName: 'Chichi', serviceType: 'daycare', serviceId: 'S005', date: today, time: '08:30', status: 'done',      checkInDate: '', checkOutDate: '', roomId: 'R003' },
  { id: 'B015', customerName: 'Ravi Kumar',   petName: 'Leo',   serviceType: 'daycare', serviceId: 'S006', date: today, time: '09:00', status: 'scheduled', checkInDate: '', checkOutDate: '', roomId: 'R004' },
  { id: 'B016', customerName: 'Grace Tan',    petName: 'Mochi', serviceType: 'daycare', serviceId: 'S005', date: today, time: '13:00', status: 'pending',   checkInDate: '', checkOutDate: '', roomId: 'R004' },
  { id: 'B017', customerName: 'Faizal Idris', petName: 'Coco',  serviceType: 'daycare', serviceId: 'S006', date: today, time: '07:45', status: 'no_show',   checkInDate: '', checkOutDate: '', roomId: 'R003' }
];

let enquiries = [
  { id: 'ENQ001', customerName: 'Priya Nathan',  channel: 'WhatsApp', message: 'Can I reschedule my grooming appointment to tomorrow?', relatedService: 'grooming', priority: 'normal', status: 'pending',  receivedAt: '08:12' },
  { id: 'ENQ002', customerName: 'Ben Ooi',       channel: 'WhatsApp', message: "Is my dog's boarding room ready for early check-in?",    relatedService: 'boarding', priority: 'high',   status: 'pending',  receivedAt: '08:40' },
  { id: 'ENQ003', customerName: 'Lim Hui Yi',    channel: 'WhatsApp', message: 'What time is daycare pickup cut-off?',                   relatedService: 'daycare',  priority: 'normal', status: 'pending',  receivedAt: '09:05' },
  { id: 'ENQ004', customerName: 'Farid Azman',   channel: 'WhatsApp', message: 'Need to confirm deluxe boarding pricing.',               relatedService: 'boarding', priority: 'high',   status: 'pending',  receivedAt: '09:20' },
  { id: 'ENQ005', customerName: 'Cheryl Wong',   channel: 'WhatsApp', message: 'Thanks for the update!',                                 relatedService: 'grooming', priority: 'normal', status: 'resolved', receivedAt: '07:50' }
];

let loyaltyRequests = [
  { id: 'LOY001', customerName: 'Alicia Lee',   type: 'RM20 Voucher Redemption', points: 400,  relatedService: 'grooming', status: 'pending',  requestedAt: '08:15' },
  { id: 'LOY002', customerName: 'Siti Zainab',  type: 'Free Boarding Night',     points: 1200, relatedService: 'boarding', status: 'pending',  requestedAt: '08:55' },
  { id: 'LOY003', customerName: 'Grace Tan',    type: 'Free Daycare Session',    points: 600,  relatedService: 'daycare',  status: 'pending',  requestedAt: '10:02' },
  { id: 'LOY004', customerName: 'David Chong',  type: 'RM10 Voucher Redemption', points: 200,  relatedService: 'boarding', status: 'approved', requestedAt: '07:30' }
];

let currentFilter = 'all';
let weekAnchor = getStartOfWeek(today);

function findServiceName(id) { return serviceNames[id] || '-'; }
function findRoomName(id) { return rooms.find(r => r.id === id)?.name || '-'; }
function getFilteredBookings(filter) {
  return bookings.filter(b => filter === 'all' || b.serviceType === filter);
}
function currentTimeStr() {
  const d = new Date();
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

const TONES = {
  alert:   { color: '#DC2626', bg: '#FEE2E2' },
  info:    { color: '#1E40AF', bg: '#DBEAFE' },
  purple:  { color: '#7C3AED', bg: '#EDE9FE' },
  pink:    { color: '#DB2777', bg: '#FCE7F3' },
  success: { color: '#059669', bg: '#D1FAE5' },
  warning: { color: '#D97706', bg: '#FEF3C7' }
};
function toneStyle(tone) {
  const t = TONES[tone] || TONES.info;
  return `--tone-color:${t.color};--tone-bg:${t.bg};`;
}

/* =========================
   SLA
========================= */

const SLA_MINUTES = { pendingService: 15, enquiry: 30, loyalty: 120 };

function toMinutes(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return h * 60 + m;
}
function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}
function slaStatus(kind, timeStr) {
  const elapsed = nowMinutes() - toMinutes(timeStr);
  const limit = SLA_MINUTES[kind];
  return { elapsed, limit, breached: elapsed > limit, remaining: limit - elapsed };
}
function formatElapsed(mins) {
  const m = Math.abs(mins);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
function slaBadge(kind, timeStr) {
  const { elapsed, breached, remaining } = slaStatus(kind, timeStr);
  if (elapsed < 0) return '';
  return breached
    ? `<span class="status-tag status-no_show">⏱ SLA breached · ${formatElapsed(elapsed)}</span>`
    : `<span class="status-tag status-done">⏱ ${formatElapsed(remaining)} left</span>`;
}
function slaBreachCount(kind, items, timeField) {
  return items.filter(item => slaStatus(kind, item[timeField]).breached).length;
}

/* =========================
   PENDING ACTION CARDS
========================= */

const CARD_CTA = {
  pendingGrooming:      { label: 'Open Service Queue',      href: 'booking.html' },
  pendingConfirmation:  { label: 'Review Bookings',         href: 'booking.html' },
  pendingEnquiries:     { label: 'Open Enquiries',          href: 'enquiries.html' },
  pendingLoyalty:       { label: 'Review Redemptions',      href: 'loyalty.html' },
  boardingCheckIn:      { label: 'Open Booking Dashboard',  href: 'booking.html' },
  boardingCheckOut:     { label: 'Open Booking Dashboard',  href: 'booking.html' },
  daycareCheckIn:       { label: 'Open Booking Dashboard',  href: 'booking.html' },
  daycarePendingPickup: { label: 'Open Booking Dashboard',  href: 'booking.html' }
};

function buildActionCards(filter) {
  const todayBookings = getFilteredBookings(filter).filter(b => b.date === today);
  const pendingConfirmation = todayBookings.filter(b => b.status === 'scheduled').length;

  const relevantEnquiries = enquiries.filter(e => filter === 'all' || e.relatedService === filter);
  const pendingEnquiries = relevantEnquiries.filter(e => e.status === 'pending');
  const enquirySlaBreaches = slaBreachCount('enquiry', pendingEnquiries, 'receivedAt');

  const relevantLoyalty = loyaltyRequests.filter(r => filter === 'all' || r.relatedService === filter);
  const pendingLoyalty = relevantLoyalty.filter(r => r.status === 'pending');
  const loyaltySlaBreaches = slaBreachCount('loyalty', pendingLoyalty, 'requestedAt');

  const cards = [];

  if (filter === 'all' || filter === 'grooming') {
    const groomingPending = bookings.filter(b => b.serviceType === 'grooming' && b.date === today && b.status === 'pending');
    const groomingSlaBreaches = slaBreachCount('pendingService', groomingPending, 'time');
    cards.push({
      key: 'pendingGrooming',
      icon: '✂️', label: 'Pending Grooming', value: groomingPending.length,
      sub: groomingSlaBreaches > 0 ? `${groomingSlaBreaches} breaching 15-min SLA` : 'Within SLA',
      tone: groomingSlaBreaches > 0 ? 'alert' : 'info'
    });
  }

  cards.push(
    {
      key: 'pendingConfirmation',
      icon: '✅', label: 'Pending Booking Confirmation', value: pendingConfirmation,
      sub: 'Scheduled today, awaiting confirmation', tone: 'info'
    },
    {
      key: 'pendingEnquiries',
      icon: '💬', label: 'Pending Enquiries', value: pendingEnquiries.length,
      sub: enquirySlaBreaches > 0 ? `${enquirySlaBreaches} breaching 30-min reply SLA` : 'Within SLA', tone: enquirySlaBreaches > 0 ? 'alert' : 'purple'
    },
    {
      key: 'pendingLoyalty',
      icon: '🎁', label: 'Pending Loyalty Redemption', value: pendingLoyalty.length,
      sub: loyaltySlaBreaches > 0 ? `${loyaltySlaBreaches} breaching 2h approval SLA` : 'Within SLA', tone: loyaltySlaBreaches > 0 ? 'alert' : 'pink'
    }
  );

  if (filter === 'all' || filter === 'boarding') {
    const boardingBookings = bookings.filter(b => b.serviceType === 'boarding');
    const checkInsDue = boardingBookings.filter(b => b.checkInDate === today && b.status !== 'done' && b.status !== 'no_show').length;
    const checkOutsDue = boardingBookings.filter(b => b.checkOutDate === today).length;

    cards.push(
      { key: 'boardingCheckIn', icon: '🚪', label: 'Boarding Check-In Due', value: checkInsDue, sub: 'Arrivals to confirm', tone: 'success' },
      { key: 'boardingCheckOut', icon: '🧳', label: 'Boarding Check-Out Due', value: checkOutsDue, sub: 'Departures to confirm', tone: 'warning' }
    );
  }

  if (filter === 'all' || filter === 'daycare') {
    const daycareBookings = bookings.filter(b => b.serviceType === 'daycare' && b.date === today);
    const checkInsDue = daycareBookings.filter(b => b.status === 'pending' || b.status === 'scheduled').length;
    const pendingPickup = daycareBookings.filter(b => b.status === 'done').length;

    cards.push(
      { key: 'daycareCheckIn', icon: '🎒', label: 'Daycare Check-In Due', value: checkInsDue, sub: 'Drop-offs to confirm', tone: 'success' },
      { key: 'daycarePendingPickup', icon: '👋', label: 'Daycare Pending Pick-Up', value: pendingPickup, sub: 'Waiting for parent pickup', tone: 'warning' }
    );
  }

  return cards;
}

function renderActionCard(card) {
  const tone = TONES[card.tone] || TONES.info;
  const isAlert = card.tone === 'alert';
  return `
    <div class="metric-card" style="border-color:${tone.bg};cursor:pointer;" onclick="openCardDetail('${card.key}')" title="${card.sub}">
      <h3 style="color:${tone.color};">${card.icon} ${card.label}</h3>
      <p style="${isAlert ? `color:${tone.color};` : ''}">${card.value}</p>
    </div>
  `;
}

/* =========================
   SERVICE LOAD CHART
========================= */

function renderServiceLoadChart() {
  const types = [
    { key: 'grooming', label: '✂️ Grooming', tone: 'info' },
    { key: 'boarding', label: '🏨 Boarding', tone: 'purple' },
    { key: 'daycare',  label: '🌞 Daycare',  tone: 'warning' }
  ];
  const counts = types.map(t => bookings.filter(b => b.serviceType === t.key && b.date === today).length);
  const max = Math.max(...counts, 1);
  const total = counts.reduce((a, b) => a + b, 0);

  q('serviceLoadTotal').textContent = `${total} booking${total === 1 ? '' : 's'} today`;

  q('serviceLoadChart').innerHTML = types.map((t, i) => `
    <div class="bar-label-item" onclick="openServiceLoadDetail('${t.key}')">
      <span class="bar-count">${counts[i]}</span>
      <div class="bar-track">
        <div class="mini-bar" style="height:${Math.max((counts[i] / max) * 100, counts[i] ? 8 : 2)}%;${toneStyle(t.tone)}background-color:var(--tone-color);"></div>
      </div>
      <span class="bar-name">${t.label}</span>
    </div>
  `).join('');
}

/* =========================
   BOOKING STATUS DONUT
========================= */

const STATUS_META = [
  { key: 'pending',   label: 'Pending Service', color: '#F59E0B' },
  { key: 'scheduled', label: 'Scheduled',       color: '#3B82F6' },
  { key: 'done',      label: 'Done',            color: '#10B981' },
  { key: 'no_show',   label: 'No Show',         color: '#EF4444' }
];

function renderStatusDonut(filter) {
  const todayBookings = getFilteredBookings(filter).filter(b => b.date === today);
  const counts = STATUS_META.map(s => todayBookings.filter(b => b.status === s.key).length);
  const total = counts.reduce((a, b) => a + b, 0);

  let cursorPct = 0;
  const gradientParts = STATUS_META.map((s, i) => {
    const pct = total ? (counts[i] / total) * 100 : 0;
    const part = `${s.color} ${cursorPct}% ${cursorPct + pct}%`;
    cursorPct += pct;
    return part;
  });
  const gradient = total ? gradientParts.join(', ') : 'var(--accent-sand) 0% 100%';

  q('statusDonut').style.background = `conic-gradient(${gradient})`;
  q('statusDonutTotal').textContent = total;

  const scopeLabel = filter === 'all' ? 'All bookings' : `${filter.charAt(0).toUpperCase() + filter.slice(1)} bookings`;
  q('statusScopeLabel').textContent = scopeLabel;

  q('statusLegend').innerHTML = STATUS_META.map((s, i) => {
    const pct = total ? Math.round((counts[i] / total) * 100) : 0;
    return `
      <div class="chart-legend-item" onclick="openServiceStatusDetail('${s.key}')">
        <span class="legend-swatch" style="background-color:${s.color};"></span>
        <span>${s.label} ${counts[i]} (${pct}%)</span>
      </div>
    `;
  }).join('');
}

/* =========================
   ROOM & PLAY AREA STATUS
========================= */

function renderRoomStatus(filter) {
  const el = q('roomStatusList');

  if (filter === 'grooming') {
    el.innerHTML = `<p class="queue-empty">Grooming does not use rooms.</p>`;
    q('roomStatusCount').textContent = '0 rooms';
    return;
  }

  const relevantRooms = rooms.filter(r => filter === 'all' || r.type === filter);
  q('roomStatusCount').textContent = `${relevantRooms.length} room${relevantRooms.length === 1 ? '' : 's'}`;

  el.innerHTML = relevantRooms.map(room => {
    const roomBookings = bookings.filter(b => b.roomId === room.id);
    const checkoutToday = roomBookings.some(b => b.checkOutDate === today);
    const activeToday = roomBookings.some(b => b.date === today && b.status !== 'no_show' && b.status !== 'done');

    let label = 'Vacant';
    let statusKey = 'done';
    if (checkoutToday) { label = 'Needs Cleaning · Checkout Today'; statusKey = 'no_show'; }
    else if (activeToday) { label = 'Occupied Today'; statusKey = 'scheduled'; }

    return `
      <div class="room-status-row" onclick="openRoomDetail('${room.id}')">
        <span>${room.name}</span>
        <span class="status-tag status-${statusKey}">${label}</span>
      </div>
    `;
  }).join('');
}

/* =========================
   WEEKLY SCHEDULE
========================= */

const SCHEDULE_HOURS = ['08:00', '10:00', '12:00', '14:00', '16:00'];

function slotForTime(timeStr) {
  let chosen = SCHEDULE_HOURS[0];
  SCHEDULE_HOURS.forEach(h => { if (toMinutes(h) <= toMinutes(timeStr)) chosen = h; });
  return chosen;
}

function formatShortDate(dateStr) {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-MY', { day: '2-digit', month: 'short' });
}

function renderWeeklySchedule(filter) {
  const dates = getDateRange(weekAnchor, addDays(weekAnchor, 6));

  q('scheduleWeekLabel').textContent = `${formatShortDate(weekAnchor)} – ${formatShortDate(addDays(weekAnchor, 6))}`;

  q('scheduleHead').innerHTML = dates.map(date => {
    const d = new Date(date + 'T00:00:00');
    const isToday = date === today;
    return `<div class="schedule-head ${isToday ? 'is-today' : ''}">${d.toLocaleDateString('en-MY', { weekday: 'short' })} ${d.getDate()}</div>`;
  }).join('');

  let bodyHtml = '';
  SCHEDULE_HOURS.forEach(hour => {
    bodyHtml += `<div class="schedule-time">${hour}</div>`;
    dates.forEach(date => {
      const slotBookings = getFilteredBookings(filter).filter(b => b.date === date && slotForTime(b.time) === hour);
      const isToday = date === today;
      if (slotBookings.length) {
        bodyHtml += `
          <div class="schedule-cell has-bookings ${isToday ? 'is-today' : ''}" onclick="openDayDetail('${date}')">
            ${slotBookings.slice(0, 2).map(b => `<span class="schedule-chip">${b.petName}</span>`).join('')}
            ${slotBookings.length > 2 ? `<span class="schedule-more">+${slotBookings.length - 2}</span>` : ''}
          </div>
        `;
      } else {
        bodyHtml += `<div class="schedule-cell ${isToday ? 'is-today' : ''}" onclick="openDayDetail('${date}')"></div>`;
      }
    });
  });

  q('scheduleGrid').innerHTML = bodyHtml;
}

/* =========================
   ACTION QUEUE
========================= */

function buildActionQueue(filter) {
  const rows = [];
  const todaysBookings = getFilteredBookings(filter).filter(b => b.date === today);

  todaysBookings.forEach(b => {
    if (b.status === 'pending') {
      rows.push({
        time: b.time, typeIcon: '📋', type: 'Service Due',
        detail: `${b.petName} (${b.customerName}) — ${findServiceName(b.serviceId)}`,
        statusKey: 'pending', statusLabel: 'Needs Action',
        sla: slaBadge('pendingService', b.time),
        actionLabel: 'Mark Done', actionOnclick: `markBookingDone('${b.id}')`,
        rowOnclick: `openQueueItemDetail('booking','${b.id}')`
      });
    } else if (b.status === 'scheduled') {
      rows.push({
        time: b.time, typeIcon: '✅', type: 'Booking Confirmation',
        detail: `${b.petName} (${b.customerName}) — ${findServiceName(b.serviceId)}`,
        statusKey: 'scheduled', statusLabel: 'Awaiting Confirmation',
        sla: '',
        actionLabel: 'Confirm Arrival', actionOnclick: `confirmBooking('${b.id}')`,
        rowOnclick: `openQueueItemDetail('booking','${b.id}')`
      });
    }
  });

  if (filter !== 'grooming') {
    getFilteredBookings(filter).forEach(b => {
      if (b.checkInDate === today && b.status !== 'done' && b.status !== 'no_show') {
        rows.push({
          time: b.time, typeIcon: '🚪', type: 'Check-In Due',
          detail: `${b.petName} (${b.customerName}) — ${findRoomName(b.roomId)}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-In',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openQueueItemDetail('booking','${b.id}')`
        });
      }
      if (b.checkOutDate === today) {
        rows.push({
          time: b.time, typeIcon: '🧳', type: 'Check-Out Due',
          detail: `${b.petName} (${b.customerName}) — ${findRoomName(b.roomId)}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-Out',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openQueueItemDetail('booking','${b.id}')`
        });
      }
    });
  }

  enquiries
    .filter(e => (filter === 'all' || e.relatedService === filter) && e.status === 'pending')
    .forEach(e => {
      rows.push({
        time: e.receivedAt, typeIcon: '💬', type: 'Enquiry',
        detail: `${e.priority === 'high' ? '🔴 ' : ''}${e.customerName} · ${e.channel} — "${e.message}"`,
        statusKey: 'pending', statusLabel: 'Needs Reply',
        sla: slaBadge('enquiry', e.receivedAt),
        actionLabel: 'Mark Replied', actionOnclick: `resolveEnquiry('${e.id}')`,
        rowOnclick: `openQueueItemDetail('enquiry','${e.id}')`
      });
    });

  loyaltyRequests
    .filter(r => (filter === 'all' || r.relatedService === filter) && r.status === 'pending')
    .forEach(r => {
      rows.push({
        time: r.requestedAt, typeIcon: '🎁', type: 'Loyalty Redemption',
        detail: `${r.customerName} — ${r.type} (${r.points} pts)`,
        statusKey: 'pending', statusLabel: 'Needs Approval',
        sla: slaBadge('loyalty', r.requestedAt),
        actionLabel: 'Approve', actionOnclick: `approveLoyalty('${r.id}')`,
        rowOnclick: `openQueueItemDetail('loyalty','${r.id}')`
      });
    });

  return rows.sort((a, b) => a.time.localeCompare(b.time));
}

function renderActionQueue(filter) {
  const rows = buildActionQueue(filter);
  q('queueCount').textContent = `${rows.length} item${rows.length === 1 ? '' : 's'}`;

  const tbody = q('actionQueueBody');

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="queue-empty">Nothing pending right now — all caught up! 🎉</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(row => `
    <tr onclick="${row.rowOnclick}">
      <td>${row.time}</td>
      <td>${row.typeIcon} ${row.type}</td>
      <td>${row.detail}</td>
      <td><span class="status-tag status-${row.statusKey}">${row.statusLabel}</span></td>
      <td>${row.sla}</td>
      <td>${row.actionOnclick ? `<button class="edit-btn" onclick="event.stopPropagation();${row.actionOnclick}">${row.actionLabel}</button>` : ''}</td>
    </tr>
  `).join('');
}

/* =========================
   DRILL-DOWN DETAIL MODAL
========================= */

function openDetailModal(title, subtitle, bodyHtml, cta) {
  q('detailModalTitle').textContent = title;
  q('detailModalSubtitle').textContent = subtitle || '';
  q('detailModalBody').innerHTML = bodyHtml && bodyHtml.trim()
    ? bodyHtml
    : `<p class="queue-empty">No records found.</p>`;
  q('detailModalFooter').innerHTML = cta
    ? `<a class="btn btn-primary" href="${cta.href}" style="width:100%;justify-content:center;">${cta.label} →</a>`
    : '';
  q('detailModal').style.display = 'flex';
}

function closeDetailModal() {
  q('detailModal').style.display = 'none';
}

function renderDetailRow({ title, sub, tag, tagLabel, sla, actionLabel, actionOnclick }) {
  return `
    <div class="detail-row">
      <div class="detail-row-main">
        <span class="detail-row-title">${title}</span>
        <span class="detail-row-sub">${sub}</span>
      </div>
      <div class="detail-row-side">
        <span class="status-tag status-${tag}">${tagLabel}</span>
        ${sla || ''}
        ${actionOnclick ? `<button class="edit-btn" onclick="${actionOnclick}">${actionLabel}</button>` : ''}
      </div>
    </div>
  `;
}

function bookingDetailRow(b) {
  const statusMeta = {
    pending:   { tag: 'pending',   tagLabel: 'Needs Action', actionLabel: 'Mark Done',       actionOnclick: `markBookingDone('${b.id}')`, sla: slaBadge('pendingService', b.time) },
    scheduled: { tag: 'scheduled', tagLabel: 'Scheduled',    actionLabel: 'Confirm Arrival',  actionOnclick: `confirmBooking('${b.id}')` },
    done:      { tag: 'done',      tagLabel: 'Done' },
    no_show:   { tag: 'no_show',   tagLabel: 'No Show' }
  }[b.status];

  return renderDetailRow({
    title: `${b.petName} (${b.customerName})`,
    sub: `${findServiceName(b.serviceId)} · ${b.date} ${b.time}${b.roomId ? ' · ' + findRoomName(b.roomId) : ''}`,
    ...statusMeta
  });
}

function enquiryDetailRow(e) {
  return renderDetailRow({
    title: `${e.customerName} · ${e.channel}`,
    sub: `"${e.message}" — received ${e.receivedAt}${e.priority === 'high' ? ' · High priority' : ''}`,
    tag: e.status === 'pending' ? 'pending' : 'done',
    tagLabel: e.status === 'pending' ? 'Needs Reply' : 'Resolved',
    sla: e.status === 'pending' ? slaBadge('enquiry', e.receivedAt) : '',
    actionLabel: e.status === 'pending' ? 'Mark Replied' : null,
    actionOnclick: e.status === 'pending' ? `resolveEnquiry('${e.id}')` : null
  });
}

function loyaltyDetailRow(r) {
  return renderDetailRow({
    title: `${r.customerName} — ${r.type}`,
    sub: `${r.points} pts · requested ${r.requestedAt}`,
    tag: r.status === 'pending' ? 'pending' : 'done',
    tagLabel: r.status === 'pending' ? 'Needs Approval' : 'Approved',
    sla: r.status === 'pending' ? slaBadge('loyalty', r.requestedAt) : '',
    actionLabel: r.status === 'pending' ? 'Approve' : null,
    actionOnclick: r.status === 'pending' ? `approveLoyalty('${r.id}')` : null
  });
}

function openCardDetail(cardKey) {
  const filter = currentFilter;
  const todayBookings = getFilteredBookings(filter).filter(b => b.date === today);
  const cta = CARD_CTA[cardKey];

  if (cardKey === 'pendingGrooming') {
    const items = bookings.filter(b => b.serviceType === 'grooming' && b.date === today && b.status === 'pending');
    openDetailModal('Pending Grooming', `${items.length} booking(s) need grooming service today. SLA: start within 15 minutes.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingConfirmation') {
    const items = todayBookings.filter(b => b.status === 'scheduled');
    openDetailModal('Pending Booking Confirmation', `${items.length} booking(s) scheduled today, awaiting confirmation.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingEnquiries') {
    const items = enquiries.filter(e => (filter === 'all' || e.relatedService === filter) && e.status === 'pending');
    openDetailModal('Pending Enquiries', `${items.length} enquiries awaiting a reply. SLA: reply within 30 minutes.`, items.map(enquiryDetailRow).join(''), cta);
  } else if (cardKey === 'pendingLoyalty') {
    const items = loyaltyRequests.filter(r => (filter === 'all' || r.relatedService === filter) && r.status === 'pending');
    openDetailModal('Pending Loyalty Redemption', `${items.length} redemption request(s) awaiting approval. SLA: approve within 2 hours.`, items.map(loyaltyDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckIn') {
    const items = bookings.filter(b => b.serviceType === 'boarding' && b.checkInDate === today && b.status !== 'done' && b.status !== 'no_show');
    openDetailModal('Boarding Check-In Due', `${items.length} arrival(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckOut') {
    const items = bookings.filter(b => b.serviceType === 'boarding' && b.checkOutDate === today);
    openDetailModal('Boarding Check-Out Due', `${items.length} departure(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycareCheckIn') {
    const items = bookings.filter(b => b.serviceType === 'daycare' && b.date === today && (b.status === 'pending' || b.status === 'scheduled'));
    openDetailModal('Daycare Check-In Due', `${items.length} drop-off(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycarePendingPickup') {
    const items = bookings.filter(b => b.serviceType === 'daycare' && b.date === today && b.status === 'done');
    openDetailModal('Daycare Pending Pick-Up', `${items.length} pet(s) waiting for pickup.`, items.map(bookingDetailRow).join(''), cta);
  }
}

function openServiceLoadDetail(type) {
  const items = bookings.filter(b => b.serviceType === type && b.date === today);
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  openDetailModal(`Today's ${label} Bookings`, `${items.length} booking(s) today.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openServiceStatusDetail(statusKey) {
  const items = getFilteredBookings(currentFilter).filter(b => b.date === today && b.status === statusKey);
  const labelMap = { pending: 'Pending Service', scheduled: 'Scheduled', done: 'Done', no_show: 'No Show' };
  openDetailModal(`Today's Bookings — ${labelMap[statusKey]}`, `${items.length} booking(s).`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRoomDetail(roomId) {
  const room = rooms.find(r => r.id === roomId);
  const items = bookings.filter(b => b.roomId === roomId).sort((a, b) => a.date.localeCompare(b.date));
  openDetailModal(`${room.name} — Bookings`, `${items.length} booking(s) using this room.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openDayDetail(date) {
  const items = getFilteredBookings(currentFilter).filter(b => b.date === date);
  const label = new Date(date + 'T00:00:00').toLocaleDateString('en-MY', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' });
  openDetailModal(label, `${items.length} booking(s) on this day.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openQueueItemDetail(kind, id) {
  if (kind === 'booking') {
    const b = bookings.find(x => x.id === id);
    if (b) openDetailModal('Booking Detail', `${b.petName} — ${findServiceName(b.serviceId)}`, bookingDetailRow(b), { label: 'Open Booking Dashboard', href: 'booking.html' });
  } else if (kind === 'enquiry') {
    const e = enquiries.find(x => x.id === id);
    if (e) openDetailModal('Enquiry Detail', `${e.customerName} via ${e.channel}`, enquiryDetailRow(e), { label: 'Open Enquiries', href: 'enquiries.html' });
  } else if (kind === 'loyalty') {
    const r = loyaltyRequests.find(x => x.id === id);
    if (r) openDetailModal('Loyalty Redemption Detail', r.customerName, loyaltyDetailRow(r), { label: 'Open Loyalty', href: 'loyalty.html' });
  }
}

/* =========================
   MUTATIONS
========================= */

function markBookingDone(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) booking.status = 'done';
  closeDetailModal();
  renderAll();
}

function confirmBooking(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) booking.status = 'pending';
  closeDetailModal();
  renderAll();
}

function resolveEnquiry(id) {
  const enquiry = enquiries.find(e => e.id === id);
  if (enquiry) enquiry.status = 'resolved';
  closeDetailModal();
  renderAll();
}

function approveLoyalty(id) {
  const request = loyaltyRequests.find(r => r.id === id);
  if (request) request.status = 'approved';
  closeDetailModal();
  renderAll();
}

/* =========================
   INIT
========================= */

function renderHeaderMeta() {
  const account = getCurrentAccount();
  q('overviewRoleChip').textContent = `👤 ${account?.role || 'Staff'}`;
  q('overviewDateChip').textContent = `🗓️ ${new Date().toLocaleDateString('en-MY', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' })}`;
}

function renderAll() {
  renderHeaderMeta();
  q('actionCards').innerHTML = buildActionCards(currentFilter).map(renderActionCard).join('');
  renderServiceLoadChart();
  renderRoomStatus(currentFilter);
  renderWeeklySchedule(currentFilter);
  renderStatusDonut(currentFilter);
  renderActionQueue(currentFilter);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentFilter = btn.dataset.filter;
      renderAll();
    });
  });

  q('calPrevBtn').addEventListener('click', () => { weekAnchor = addDays(weekAnchor, -7); renderWeeklySchedule(currentFilter); });
  q('calNextBtn').addEventListener('click', () => { weekAnchor = addDays(weekAnchor, 7); renderWeeklySchedule(currentFilter); });
  q('calTodayBtn').addEventListener('click', () => { weekAnchor = getStartOfWeek(today); renderWeeklySchedule(currentFilter); });

  q('detailModal').addEventListener('click', event => {
    if (event.target.id === 'detailModal') closeDetailModal();
  });

  renderAll();
});
