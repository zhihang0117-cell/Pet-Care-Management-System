function q(id) { return document.getElementById(id); }

/* =========================
   REUSABLE DATE-RANGE FILTER (Daily/Weekly/Monthly + Prev/Today/Next)
   Same pattern as the Analytical Dashboard's period toggle, reused on
   dailyoverview.html/booking.html/payment.html/loyalty.html/enquiries.html.
   Defaults to "Daily" (today), unlike the dashboard's own default of
   "Weekly" — these pages are meant to open scoped to today's numbers.
   Relies on getPeriodRange()/formatPeriodChip() defined in the Analytical
   Dashboard section below (plain functions, no dashboard-specific state).
========================= */

function createDateRangeFilter(prefix, onChange) {
  const state = { period: "daily", anchorDate: getToday() };

  function currentRange() {
    return getPeriodRange(state.period, state.anchorDate);
  }

  function render() {
    const range = currentRange();
    const chip = document.getElementById(`${prefix}RangeChip`);
    if (chip) chip.innerHTML = formatPeriodChip(state.period, range);
    onChange(range, state.period);
  }

  function shift(step) {
    if (state.period === "daily") state.anchorDate = addDays(state.anchorDate, step);
    else if (state.period === "monthly") state.anchorDate = addMonths(state.anchorDate, step);
    else state.anchorDate = addDays(state.anchorDate, step * 7);
    render();
  }

  function init() {
    document.querySelectorAll(`#${prefix}PeriodToggle .tab-btn`).forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(`#${prefix}PeriodToggle .tab-btn`).forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        state.period = btn.dataset.period;
        state.anchorDate = getToday();
        render();
      });
    });
    document.getElementById(`${prefix}PrevBtn`)?.addEventListener("click", () => shift(-1));
    document.getElementById(`${prefix}NextBtn`)?.addEventListener("click", () => shift(1));
    document.getElementById(`${prefix}TodayBtn`)?.addEventListener("click", () => {
      state.anchorDate = getToday();
      render();
    });
    render();
  }

  return { init, getRange: currentRange, getPeriod: () => state.period };
}

/* =========================
   MOCK DATA
========================= */

const services = [
  { id: "S001", type: "grooming", name: "Basic Grooming", price: 80, duration: 90 },
  { id: "S002", type: "grooming", name: "Full Grooming", price: 150, duration: 150 },
  { id: "S003", type: "boarding", name: "Standard Boarding", price: 120, duration: 1440 },
  { id: "S004", type: "boarding", name: "Deluxe Boarding", price: 180, duration: 1440 },
  { id: "S005", type: "daycare", name: "Half-Day Daycare", price: 60, duration: 240 },
  { id: "S006", type: "daycare", name: "Full-Day Daycare", price: 100, duration: 480 }
];

function staffStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_staff_" + (account?.businessKey || "default");
}
function loadStaff(seedStaff) {
  try {
    const raw = localStorage.getItem(staffStorageKey());
    return raw ? JSON.parse(raw) : seedStaff;
  } catch (e) {
    return seedStaff;
  }
}
function persistStaff() {
  if (bookingsAreReal) return;
  localStorage.setItem(staffStorageKey(), JSON.stringify(staff));
}

const SEED_STAFF = [
  { id: "ST001", name: "Sarah Wong", role: "Manager",   email: "sarah.wong@happypaws.my", phone: "+60 12-345 6801", offDays: ["Sunday"] },
  { id: "ST002", name: "Adam Tan",   role: "Groomer",   email: "adam.tan@happypaws.my",   phone: "+60 12-345 6802", offDays: ["Monday"] },
  { id: "ST003", name: "Mei Ling",   role: "Caretaker", email: "mei.ling@happypaws.my",   phone: "+60 12-345 6803", offDays: ["Tuesday", "Sunday"] }
];

let staff = loadStaff(SEED_STAFF);

const rooms = [
  { id: "R001", name: "Room A", type: "boarding", capacity: 5 },
  { id: "R002", name: "Room B", type: "boarding", capacity: 4 },
  { id: "R003", name: "Playroom 1", type: "daycare", capacity: 12 },
  { id: "R004", name: "Playroom 2", type: "daycare", capacity: 10 }
];

const today = getToday();

function bookingsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_bookings_" + (account?.businessKey || "default");
}

function loadBookings(seedBookings) {
  try {
    const raw = localStorage.getItem(bookingsStorageKey());
    return raw ? JSON.parse(raw) : seedBookings;
  } catch (e) {
    return seedBookings;
  }
}

// Set true once loadRealBookingData() overwrites `bookings` with real
// Supabase rows, so persistBookings() never writes real-shaped booking
// objects into the mock localStorage key (which other not-yet-converted
// pages like dashboard.html/profile.html/staff.html still read from).
let bookingsAreReal = false;

function persistBookings() {
  if (bookingsAreReal) return;
  localStorage.setItem(bookingsStorageKey(), JSON.stringify(bookings));
}

const SEED_BOOKINGS = [
  { id: "B001", customerName: "Alicia Lee",   petName: "Milo",   serviceType: "grooming", serviceId: "S002", staffId: "ST002", roomId: "",     date: today, time: "09:00", duration: 150,  status: "pending",   amount: 150, checkInDate: "", checkOutDate: "", specialNote: "Sensitive skin. Use mild shampoo." },
  { id: "B002", customerName: "Jason Lim",    petName: "Coco",   serviceType: "grooming", serviceId: "S001", staffId: "ST002", roomId: "",     date: today, time: "10:30", duration: 90,   status: "scheduled", amount: 80,  checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B003", customerName: "Farah Hana",   petName: "Luna",   serviceType: "grooming", serviceId: "S002", staffId: "ST002", roomId: "",     date: today, time: "08:00", duration: 150,  status: "pending",   amount: 150, checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B004", customerName: "Tan Wei",      petName: "Buddy",  serviceType: "grooming", serviceId: "S001", staffId: "ST002", roomId: "",     date: today, time: "14:00", duration: 90,   status: "done",      amount: 80,  checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B005", customerName: "Wong Mei",     petName: "Simba",  serviceType: "grooming", serviceId: "S002", staffId: "ST002", roomId: "",     date: today, time: "16:00", duration: 150,  status: "scheduled", amount: 150, checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B006", customerName: "Nur Aina",     petName: "Snowy",  serviceType: "grooming", serviceId: "S001", staffId: "ST002", roomId: "",     date: today, time: "11:00", duration: 90,   status: "no_show",   amount: 80,  checkInDate: "", checkOutDate: "", specialNote: "" },

  { id: "B007", customerName: "David Chong",  petName: "Rocky",  serviceType: "boarding", serviceId: "S003", staffId: "ST003", roomId: "R001", date: addDays(today, -2), time: "09:00", duration: 1440, status: "scheduled", amount: 120, checkInDate: addDays(today, -2), checkOutDate: today,              specialNote: "" },
  { id: "B008", customerName: "Siti Zainab",  petName: "Bella",  serviceType: "boarding", serviceId: "S004", staffId: "ST003", roomId: "R002", date: today,              time: "10:00", duration: 1440, status: "pending",   amount: 180, checkInDate: today,               checkOutDate: addDays(today, 3),  specialNote: "Needs evening medication." },
  { id: "B009", customerName: "Kumar Raj",    petName: "Max",    serviceType: "boarding", serviceId: "S003", staffId: "ST003", roomId: "R001", date: addDays(today, -1), time: "09:30", duration: 1440, status: "scheduled", amount: 120, checkInDate: addDays(today, -1), checkOutDate: addDays(today, 2),  specialNote: "" },
  { id: "B010", customerName: "Angeline Foo", petName: "Cleo",   serviceType: "boarding", serviceId: "S004", staffId: "ST003", roomId: "R002", date: today,              time: "11:30", duration: 1440, status: "pending",   amount: 180, checkInDate: today,               checkOutDate: addDays(today, 4),  specialNote: "" },
  { id: "B011", customerName: "Hafiz Rahman", petName: "Tommy",  serviceType: "boarding", serviceId: "S003", staffId: "ST003", roomId: "R001", date: addDays(today, -3), time: "08:30", duration: 1440, status: "scheduled", amount: 120, checkInDate: addDays(today, -3), checkOutDate: today,              specialNote: "" },
  { id: "B012", customerName: "Michelle Yap", petName: "Nala",   serviceType: "boarding", serviceId: "S004", staffId: "ST003", roomId: "R002", date: addDays(today, 1),  time: "09:00", duration: 1440, status: "scheduled", amount: 180, checkInDate: addDays(today, 1),  checkOutDate: addDays(today, 5),  specialNote: "" },

  { id: "B013", customerName: "Chong Li Wei", petName: "Oreo",   serviceType: "daycare", serviceId: "S006", staffId: "ST003", roomId: "R003", date: today, time: "08:00", duration: 480, status: "pending",   amount: 100, checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B014", customerName: "Aisyah Bakar", petName: "Chichi", serviceType: "daycare", serviceId: "S005", staffId: "ST003", roomId: "R003", date: today, time: "08:30", duration: 240, status: "done",      amount: 60,  checkInDate: "", checkOutDate: "", specialNote: "Very active. Needs more playtime." },
  { id: "B015", customerName: "Ravi Kumar",   petName: "Leo",    serviceType: "daycare", serviceId: "S006", staffId: "ST003", roomId: "R004", date: today, time: "09:00", duration: 480, status: "scheduled", amount: 100, checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B016", customerName: "Grace Tan",    petName: "Mochi",  serviceType: "daycare", serviceId: "S005", staffId: "ST003", roomId: "R004", date: today, time: "13:00", duration: 240, status: "pending",   amount: 60,  checkInDate: "", checkOutDate: "", specialNote: "" },
  { id: "B017", customerName: "Faizal Idris", petName: "Coco",   serviceType: "daycare", serviceId: "S006", staffId: "ST003", roomId: "R003", date: today, time: "07:45", duration: 480, status: "no_show",   amount: 100, checkInDate: "", checkOutDate: "", specialNote: "" }
];

let bookings = loadBookings(SEED_BOOKINGS);

/* =========================
   REAL BOOKING DATA (booking.html only)
   Overwrites the mock `bookings`/`staff` globals above with real Supabase
   data — but ONLY when booking.html's own init path calls
   loadRealBookingData(). Every other page (dailyoverview.html, profile.html,
   dashboard.html, staff.html) still reads the mock arrays untouched, since
   they haven't been converted yet and this is a fresh page load each time
   (static multi-page app, no shared runtime state between pages).

   Real bookings live in 3 separate tables (grooming_booking/daycare_booking/
   boarding_booking) with different columns and no shared services/rooms
   catalog. They're merged here into the same unified booking shape the
   existing Kanban/Calendar/Listing render code already expects, so that
   code doesn't need to change — only the mapping in and the mutations
   (which now call the real API instead of mutating an in-memory array)
   are new.
========================= */

// Named bookingPets/bookingCustomers (not pets/customers) to avoid colliding
// with profile.html's own mock `pets`/`customers` globals declared later in
// this file — both are top-level `let` in the same shared script.
let bookingPets = [];
let bookingCustomers = [];
const GROOMING_DEFAULT_DURATION = 90; // grooming_booking has no duration column

function normalizeStatus(raw) {
  // Real booking_status casing is inconsistent across tables (grooming is
  // lowercase, daycare/boarding are Title Case, and "no show" has a space
  // instead of an underscore) — fold all of that down to this app's
  // existing pending/scheduled/done/no_show/cancelled convention.
  return String(raw || "").trim().toLowerCase().replace(/\s+/g, "_");
}

// Denormalize back to whatever casing convention that specific real table
// already uses, on write — rather than inventing a 4th convention.
const STATUS_WRITE_LOWERCASE = { pending: "pending", scheduled: "scheduled", done: "done", no_show: "no show", cancelled: "cancelled" };
const STATUS_WRITE_TITLECASE = { pending: "Pending", scheduled: "Scheduled", done: "Done", no_show: "No Show", cancelled: "Cancelled" };
function denormalizeStatus(bookingType, status) {
  return bookingType === "grooming" ? STATUS_WRITE_LOWERCASE[status] : STATUS_WRITE_TITLECASE[status];
}

function nightsBetweenDates(checkIn, checkOut) {
  const ms = new Date(checkOut) - new Date(checkIn);
  return Math.max(1, Math.round(ms / (1000 * 60 * 60 * 24)));
}

function minutesBetweenTimes(start, end) {
  const toMin = t => { const [h, m] = String(t || "0:0").split(":").map(Number); return h * 60 + m; };
  return Math.max(1, toMin(end) - toMin(start));
}

function findRealStaff(staffId) {
  return staff.find(s => Number(s.staff_id) === Number(staffId));
}

function findPet(petId) {
  return bookingPets.find(p => Number(p.pet_id) === Number(petId));
}

function mapGroomingBooking(row) {
  const pet = findPet(row.pet_id);
  return {
    id: `grooming-${row.grooming_booking_id}`,
    realId: row.grooming_booking_id,
    bookingType: "grooming",
    customerName: pet?.customerName || "—",
    petName: pet?.pet_name || "—",
    petId: row.pet_id,
    serviceType: "grooming",
    serviceLabel: row.service_name || "-",
    price: Number(row.price) || 0,
    addOn: row.add_on && row.add_on !== "-" ? row.add_on : "",
    addOnPrice: Number(row.add_on_price) || 0,
    staffId: row.staff_id,
    roomLabel: "",
    date: row.booking_date,
    time: (row.booking_time || "").slice(0, 5),
    duration: GROOMING_DEFAULT_DURATION,
    status: normalizeStatus(row.booking_status),
    amount: (Number(row.price) || 0) + (Number(row.add_on_price) || 0),
    checkInDate: "",
    checkOutDate: "",
    specialNote: row.notes && row.notes !== "-" ? row.notes : "",
    paymentId: row.payment_id,
    createdDate: row.created_date || "",
  };
}

function mapDaycareBooking(row) {
  const pet = findPet(row.pet_id);
  const checkInTime = (row.check_in_time || "").slice(0, 5);
  const checkOutTime = (row.check_out_time || "").slice(0, 5);
  return {
    id: `daycare-${row.daycare_booking_id}`,
    realId: row.daycare_booking_id,
    bookingType: "daycare",
    customerName: pet?.customerName || "—",
    petName: pet?.pet_name || "—",
    petId: row.pet_id,
    serviceType: "daycare",
    serviceLabel: row.package_type || "-",
    price: Number(row.price) || 0,
    addOn: "",
    addOnPrice: 0,
    staffId: row.staff_id,
    roomLabel: "",
    date: row.booking_date,
    time: checkInTime,
    checkInTime,
    checkOutTime,
    duration: minutesBetweenTimes(checkInTime, checkOutTime),
    status: normalizeStatus(row.booking_status),
    amount: Number(row.price) || 0,
    checkInDate: "",
    checkOutDate: "",
    specialNote: row.special_instruction && row.special_instruction !== "-" ? row.special_instruction : "",
    paymentId: row.payment_id,
    createdDate: row.created_date || "",
  };
}

function mapBoardingBooking(row) {
  const pet = findPet(row.pet_id);
  return {
    id: `boarding-${row.boarding_booking_id}`,
    realId: row.boarding_booking_id,
    bookingType: "boarding",
    customerName: pet?.customerName || "—",
    petName: pet?.pet_name || "—",
    petId: row.pet_id,
    serviceType: "boarding",
    serviceLabel: row.room_type || "-",
    price: Number(row.price_per_night) || 0,
    addOn: "",
    addOnPrice: 0,
    staffId: row.staff_id,
    roomLabel: row.room_type || "",
    date: row.check_in_date,
    time: (row.check_in_time || "").slice(0, 5),
    checkInTime: (row.check_in_time || "").slice(0, 5),
    checkOutTime: (row.check_out_time || "").slice(0, 5),
    duration: 1440,
    status: normalizeStatus(row.booking_status),
    amount: Number(row.total_price) || 0,
    checkInDate: row.check_in_date,
    checkOutDate: row.check_out_date,
    feedingInstruction: row.feeding_instruction && row.feeding_instruction !== "-" ? row.feeding_instruction : "",
    medicalInstruction: row.medical_instruction && row.medical_instruction !== "-" ? row.medical_instruction : "",
    specialNote: row.notes && row.notes !== "-" ? row.notes : "",
    paymentId: row.payment_id,
    createdDate: row.created_date || "",
  };
}

async function loadRealBookingData() {
  const [petsRes, customersRes, staffRes, grooming, daycare, boarding] = await Promise.all([
    api.listPets({ limit: 1000 }),
    api.listCustomers({ limit: 1000 }),
    api.listStaff({ limit: 1000 }),
    api.listBookings("grooming", { limit: 1000 }),
    api.listBookings("daycare", { limit: 1000 }),
    api.listBookings("boarding", { limit: 1000 }),
  ]);

  bookingCustomers = customersRes;
  const customerById = new Map(bookingCustomers.map(c => [c.customer_id, c]));
  bookingPets = petsRes.map(p => ({ ...p, customerName: customerById.get(p.customer_id)?.full_name || "—" }));
  staff = staffRes;

  bookings = [
    ...grooming.map(mapGroomingBooking),
    ...daycare.map(mapDaycareBooking),
    ...boarding.map(mapBoardingBooking),
  ];
  bookingsAreReal = true;
}

async function updateBookingStatus(booking, newStatus) {
  try {
    await api.updateBooking(booking.bookingType, booking.realId, {
      booking_status: denormalizeStatus(booking.bookingType, newStatus),
    });
  } catch (err) {
    alert(err.message);
    return false;
  }
  booking.status = newStatus;
  return true;
}

async function moveBooking(booking, newDate, newTime) {
  const payload = {};
  if (booking.bookingType === "grooming") {
    payload.booking_date = newDate;
    if (newTime) payload.booking_time = `${newTime}:00`;
  } else if (booking.bookingType === "daycare") {
    payload.booking_date = newDate;
  } else if (booking.bookingType === "boarding") {
    const nights = nightsBetweenDates(booking.checkInDate, booking.checkOutDate);
    payload.check_in_date = newDate;
    payload.check_out_date = addDays(newDate, nights);
  }

  try {
    await api.updateBooking(booking.bookingType, booking.realId, payload);
  } catch (err) {
    alert(err.message);
    return false;
  }

  booking.date = newDate;
  if (newTime) booking.time = newTime;
  if (booking.bookingType === "boarding") {
    const nights = nightsBetweenDates(booking.checkInDate, booking.checkOutDate);
    booking.checkInDate = newDate;
    booking.checkOutDate = addDays(newDate, nights);
  }
  return true;
}

function enquiriesStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_enquiries_" + (account?.businessKey || "default");
}
function loadEnquiries(seedEnquiries) {
  try {
    const raw = localStorage.getItem(enquiriesStorageKey());
    return raw ? JSON.parse(raw) : seedEnquiries;
  } catch (e) {
    return seedEnquiries;
  }
}
function persistEnquiries() {
  localStorage.setItem(enquiriesStorageKey(), JSON.stringify(enquiries));
}

const SEED_ENQUIRIES = [
  { id: 'ENQ001', customerName: 'Priya Nathan',  phone: '+60 12-345 7001', channel: 'WhatsApp', message: 'Can I reschedule my grooming appointment to tomorrow?', relatedService: 'grooming', status: 'pending',  receivedAt: '08:12' },
  { id: 'ENQ002', customerName: 'Ben Ooi',       phone: '+60 12-345 7002', channel: 'WhatsApp', message: "Is my dog's boarding room ready for early check-in?",    relatedService: 'boarding', status: 'pending',  receivedAt: '08:40' },
  { id: 'ENQ003', customerName: 'Lim Hui Yi',    phone: '+60 12-345 7003', channel: 'WhatsApp', message: 'What time is daycare pickup cut-off?',                   relatedService: 'daycare',  status: 'pending',  receivedAt: '09:05' },
  { id: 'ENQ004', customerName: 'Farid Azman',   phone: '+60 12-345 7004', channel: 'WhatsApp', message: 'Need to confirm deluxe boarding pricing.',               relatedService: 'boarding', status: 'pending',  receivedAt: '09:20' },
  { id: 'ENQ005', customerName: 'Cheryl Wong',   phone: '+60 12-345 7005', channel: 'WhatsApp', message: 'Thanks for the update!',                                 relatedService: 'grooming', status: 'resolved', receivedAt: '07:50', handledBy: 'human' },
  { id: 'ENQ006', customerName: 'Wong Mei',      phone: '+60 12-345 6705', channel: 'WhatsApp', message: 'What are your operating hours today?',                  relatedService: 'grooming', status: 'resolved', receivedAt: '07:15', handledBy: 'ai' }
];

let enquiries = loadEnquiries(SEED_ENQUIRIES);

function loyaltyRequestsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_loyalty_requests_" + (account?.businessKey || "default");
}
function loadLoyaltyRequests(seedRequests) {
  try {
    const raw = localStorage.getItem(loyaltyRequestsStorageKey());
    return raw ? JSON.parse(raw) : seedRequests;
  } catch (e) {
    return seedRequests;
  }
}
function persistLoyaltyRequests() {
  localStorage.setItem(loyaltyRequestsStorageKey(), JSON.stringify(loyaltyRequests));
}

const SEED_LOYALTY_REQUESTS = [
  { id: 'LOY001', customerName: 'Alicia Lee',   type: 'RM20 Voucher Redemption', points: 400,  relatedService: 'grooming', status: 'pending',  requestedAt: '08:15' },
  { id: 'LOY002', customerName: 'Siti Zainab',  type: 'Free Boarding Night',     points: 1200, relatedService: 'boarding', status: 'pending',  requestedAt: '08:55' },
  { id: 'LOY003', customerName: 'Grace Tan',    type: 'Free Daycare Session',    points: 600,  relatedService: 'daycare',  status: 'pending',  requestedAt: '10:02' },
  { id: 'LOY004', customerName: 'David Chong',  type: 'RM10 Voucher Redemption', points: 200,  relatedService: 'boarding', status: 'approved', requestedAt: '07:30' }
];

let loyaltyRequests = loadLoyaltyRequests(SEED_LOYALTY_REQUESTS);

function leaveRequestsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_leave_requests_" + (account?.businessKey || "default");
}
function loadLeaveRequests(seedRequests) {
  try {
    const raw = localStorage.getItem(leaveRequestsStorageKey());
    return raw ? JSON.parse(raw) : seedRequests;
  } catch (e) {
    return seedRequests;
  }
}
function persistLeaveRequests() {
  if (leaveDataIsReal) return;
  localStorage.setItem(leaveRequestsStorageKey(), JSON.stringify(leaveRequests));
}

const SEED_LEAVE_REQUESTS = [
  { id: 'LV001', staffId: 'ST002', staffName: 'Adam Tan', startDate: addDays(today, 2), endDate: addDays(today, 3), reason: 'Personal errand',      status: 'approved', appliedAt: '09:00' },
  { id: 'LV002', staffId: 'ST003', staffName: 'Mei Ling', startDate: addDays(today, 5), endDate: addDays(today, 5), reason: 'Medical appointment',   status: 'pending',  appliedAt: '10:15' }
];

let leaveRequests = loadLeaveRequests(SEED_LEAVE_REQUESTS);

/* =========================
   STATE
========================= */

let currentServiceFilter = "all";
let currentView = "kanban";
let kanbanDateMode = "today";
let currentStaffFilter = "all";
let draggedBookingId = null;
let listingSearchKeyword = "";
let calendarAnchorDate = getToday();
let _newBookingDraft = null;
let _bookingIdCounter = bookings.length;

const CALENDAR_HOURS = [
  "08:00", "09:00", "10:00", "11:00", "12:00",
  "13:00", "14:00", "15:00", "16:00", "17:00"
];

/* =========================
   INIT
========================= */

document.addEventListener("DOMContentLoaded", async () => {
  try {
    if (document.getElementById("liveDateTime")) {
      initLiveClock();
    }

    if (document.getElementById("kanbanBoard")) {
      await loadRealBookingData();
      promoteScheduledBookingsForToday();

      setupTabs();
      setupModalEvents();
      setupCalendarEvents();
      setupListingEvents();
      setupPetSearch();
      populateStaffDropdown();
      createDateRangeFilter("bookingMetrics", (range, period) => {
        bookingMetricsRange = range;
        bookingMetricsPeriod = period;
        renderMetricCards();
      }).init();
      renderAll();

      setTimeout(() => scrollCalendarToToday(), 100);
    } else if (document.getElementById("actionCards")) {
      await loadRealBookingData();
      promoteScheduledBookingsForToday();
    } else {
      promoteScheduledBookingsForToday();
    }

    if (document.getElementById("actionCards")) {
      setupDailyOverview();
    }

    if (document.getElementById("profileTypeFilter")) {
      await initCRM();
    }

    if (document.getElementById("kpiHeroGrid")) {
      await initAnalyticsDashboard();
    }

    if (document.getElementById("settingsTabs")) {
      initSettings();
    }

    if (document.getElementById("loyaltyPendingBody")) {
      await initLoyaltyPage();
    }

    if (document.getElementById("paymentPendingBody")) {
      await initPaymentPage();
    }

    if (document.getElementById("staffListBody")) {
      await initStaffPage();
    }

    if (document.getElementById("enquiryPendingBody")) {
      await initEnquiriesPage();
    }
  } catch (err) {
    console.error("Page init failed:", err);
    alert(`Something failed to load: ${err.message}`);
  }
});

/* =========================
   LIVE DATE & TIME
========================= */

function initLiveClock() {
  const el = q("liveDateTime");
  const render = () => {
    const now = new Date();
    const dateStr = now.toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    const timeStr = now.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    el.innerHTML = `<img src="icon/calendar-simple.png" alt="" class="row-icon">${dateStr} · ${timeStr}`;
  };
  render();
  setInterval(render, 1000);
}

/* =========================
   SETUP EVENTS
========================= */

function setupTabs() {
  document.querySelectorAll("#serviceFilterTabs .tab-btn").forEach(button => {
    button.addEventListener("click", () => {
      setActiveTab("#serviceFilterTabs", button);
      currentServiceFilter = button.dataset.filter;
      renderAll();
    });
  });

  document.querySelectorAll("#viewTabs .tab-btn").forEach(button => {
    button.addEventListener("click", () => {
      setActiveTab("#viewTabs", button);
      currentView = button.dataset.view;
      switchView();
      renderAll();
    });
  });

  document.getElementById("todayBtn").addEventListener("click", () => {
    kanbanDateMode = "today";
    document.getElementById("todayBtn").classList.add("active");
    document.getElementById("allBtn").classList.remove("active");
    renderKanban();
  });

  document.getElementById("allBtn").addEventListener("click", () => {
    kanbanDateMode = "all";
    document.getElementById("allBtn").classList.add("active");
    document.getElementById("todayBtn").classList.remove("active");
    renderKanban();
  });

  const kanbanStaffFilter = document.getElementById("kanbanStaffFilter");
  staff.forEach(member => {
    const option = document.createElement("option");
    option.value = member.staff_id;
    option.textContent = member.staff_name;
    kanbanStaffFilter.appendChild(option);
  });
  kanbanStaffFilter.addEventListener("change", () => {
    currentStaffFilter = kanbanStaffFilter.value;
    renderKanban();
  });

  document.getElementById("addBookingBtn").addEventListener("click", () => {
    openNewBooking();
  });
}

function setupModalEvents() {
  document.getElementById("closeModalBtn").addEventListener("click", closeModal);

  document.getElementById("serviceType").addEventListener("change", () => {
    toggleServiceSpecificFields();
    recalcAmount();
  });

  ["groomingPrice", "groomingAddOnPrice", "daycarePrice", "boardingPricePerNight", "checkInDate", "checkOutDate"].forEach(id => {
    document.getElementById(id).addEventListener("input", recalcAmount);
  });

  document.getElementById("bookingForm").addEventListener("submit", event => {
    event.preventDefault();
    saveBooking();
  });

  document.getElementById("doneServiceBtn").addEventListener("click", () => {
    document.getElementById("bookingStatus").value = "done";
    saveBooking();
  });

  document.getElementById("cancelBookingBtn").addEventListener("click", () => {
    document.getElementById("bookingStatus").value = "cancelled";
    saveBooking();
  });

  document.getElementById("openPetProfileBtn").addEventListener("click", () => {
    const petName = document.getElementById("petName").value;
    alert(`Open pet profile: ${petName}`);
  });

  document.getElementById("bookingModal").addEventListener("click", event => {
    if (event.target.id === "bookingModal") closeModal();
  });
}

function setupCalendarSlotEvents() {
  document.querySelectorAll(".add-slot-btn").forEach(button => {
    button.addEventListener("click", event => {
      event.stopPropagation();
      createBookingFromSlot(event.currentTarget.dataset.date, event.currentTarget.dataset.time);
    });
  });

  document.querySelectorAll(".calendar-cell").forEach(cell => {
    cell.addEventListener("click", event => {
      if (event.target.classList.contains("calendar-booking") ||
          event.target.classList.contains("add-slot-btn")) return;
      createBookingFromSlot(cell.dataset.date, cell.dataset.time);
    });

    cell.addEventListener("dragover", event => {
      event.preventDefault();
    });

    cell.addEventListener("drop", async event => {
      event.preventDefault();
      const booking = bookings.find(item => item.id === draggedBookingId);
      draggedBookingId = null;
      if (!booking) return;
      const newDate = cell.dataset.date;
      const newTime = cell.dataset.time || booking.time;
      if (!canAddBookingToSlot(newDate, newTime, booking.staffId, booking.duration, booking.id)) {
        alert("This slot is not available. Maximum 3 bookings are allowed per timeslot, and staff cannot be duplicated.");
        return;
      }
      if (await moveBooking(booking, newDate, newTime)) renderAll();
    });
  });
}

function scrollCalendarToToday() {
  const calendarWrap = document.getElementById("calendarWrap");
  const todayHeader = document.querySelector(`[data-date-header="${getToday()}"]`);

  if (!calendarWrap || !todayHeader) return;

  const leftPosition = todayHeader.offsetLeft - 120;

  calendarWrap.scrollTo({
    left: leftPosition,
    behavior: "smooth"
  });
}

function setupListingEvents() {
  const searchInput = document.getElementById("listingSearchInput");

  if (!searchInput) return;

  searchInput.addEventListener("input", event => {
    listingSearchKeyword = event.target.value.toLowerCase().trim();
    renderListing();
  });
}

function setupPetSearch() {
  const input = document.getElementById("petSearchInput");
  const results = document.getElementById("petSearchResults");

  input.addEventListener("input", () => {
    const query = input.value.toLowerCase().trim();
    if (!query) {
      results.classList.add("hidden");
      results.innerHTML = "";
      return;
    }

    const matches = bookingPets.filter(p =>
      (p.pet_name || "").toLowerCase().includes(query) || (p.customerName || "").toLowerCase().includes(query)
    ).slice(0, 8);

    if (!matches.length) {
      results.innerHTML = `<div style="padding:10px;color:var(--text-muted);font-size:0.85rem;">No matching pet found.</div>`;
      results.classList.remove("hidden");
      return;
    }

    results.innerHTML = matches.map(p => `
      <div class="pet-search-result" data-pet-id="${p.pet_id}" style="padding:10px;cursor:pointer;border-bottom:1px solid var(--accent-sand);">
        <strong>${p.pet_name}</strong> (${p.pet_type || "-"}) — <span style="color:var(--text-muted);">${p.customerName || "-"}</span>
      </div>
    `).join("");
    results.classList.remove("hidden");

    results.querySelectorAll(".pet-search-result").forEach(row => {
      row.addEventListener("click", () => selectPet(row.dataset.petId));
    });
  });

  document.addEventListener("click", event => {
    if (event.target !== input && !results.contains(event.target)) {
      results.classList.add("hidden");
    }
  });
}

function selectPet(petId) {
  const pet = findPet(petId);
  if (!pet) return;
  document.getElementById("selectedPetId").value = pet.pet_id;
  document.getElementById("customerName").value = pet.customerName || "";
  document.getElementById("petName").value = pet.pet_name;
  document.getElementById("petSearchInput").value = `${pet.pet_name} (${pet.customerName || "-"})`;
  document.getElementById("petSearchResults").classList.add("hidden");
}

/* =========================
   RENDER MAIN
========================= */

function renderAll() {
  renderMetricCards();
  renderKanban();
  renderCalendar();
  renderListing();
}

function switchView() {
  document.getElementById("kanbanView").classList.toggle("hidden", currentView !== "kanban");
  document.getElementById("calendarView").classList.toggle("hidden", currentView !== "calendar");
  document.getElementById("listingView").classList.toggle("hidden", currentView !== "listing");

  const qa = document.getElementById("kanbanQuickActions");
  if (qa) qa.style.display = currentView === "kanban" ? "flex" : "none";
}

/* =========================
   FILTER HELPERS
========================= */

function getFilteredBookings() {
  return bookings.filter(booking => {
    const serviceMatch =
      currentServiceFilter === "all" || booking.serviceType === currentServiceFilter;

    return serviceMatch;
  });
}

function getKanbanBookings() {
  return getFilteredBookings().filter(booking => {
    if (kanbanDateMode === "today" && booking.date !== getToday()) return false;
    if (currentStaffFilter !== "all" && Number(booking.staffId) !== Number(currentStaffFilter)) return false;
    return true;
  });
}

/* =========================
   METRIC CARDS
========================= */

// Set by the date-range filter controller (see setupBookingDateFilter);
// defaults to today so the cards match the page's default "Daily" view.
let bookingMetricsRange = { start: getToday(), end: getToday() };
let bookingMetricsPeriod = "daily";

function periodWord(period) {
  return period === "monthly" ? "This Month" : period === "weekly" ? "This Week" : "Today";
}

function renderMetricCards() {
  const wrapper = document.getElementById("metricCards");
  const range = bookingMetricsRange;
  const data = getFilteredBookings().filter(b => b.date >= range.start && b.date <= range.end);

  const metrics = buildMetrics(currentServiceFilter, data, range, bookingMetricsPeriod);

  wrapper.innerHTML = metrics.map(metric => `
    <div class="metric-card">
      <h3>${metric.label}</h3>
      <p>${metric.value}</p>
    </div>
  `).join("");
}

function buildMetrics(filter, data, range, period) {
  const word = periodWord(period);

  if (filter === "grooming") {
    const grooming = data.filter(b => b.serviceType === "grooming");
    const doneRate = percentage(
      grooming.filter(b => b.status === "done").length,
      grooming.length
    );

    return [
      { label: `Booking ${word}`, value: grooming.length },
      { label: "Done Rate", value: doneRate },
      { label: "Pending Booking", value: countStatus(grooming, "pending") },
      { label: "Staff Utilization", value: staffUtilizationToday() },
      { label: "No-show", value: countStatus(grooming, "no_show") }
    ];
  }

  if (filter === "boarding") {
    const boarding = data.filter(b => b.serviceType === "boarding");

    return [
      { label: `Boarding ${word}`, value: boarding.length },
      { label: "Current Boarder", value: boarding.filter(b => b.status !== "no_show" && b.status !== "cancelled").length },
      { label: `Check-in ${word}`, value: boarding.filter(b => b.checkInDate >= range.start && b.checkInDate <= range.end).length },
      { label: `Check-out ${word}`, value: boarding.filter(b => b.checkOutDate >= range.start && b.checkOutDate <= range.end).length }
    ];
  }

  if (filter === "daycare") {
    const daycare = data.filter(b => b.serviceType === "daycare");
    const attending = daycare.filter(b => b.status !== "no_show" && b.status !== "cancelled").length;

    return [
      { label: `Daycare ${word}`, value: daycare.length },
      { label: "Pending Pick-up", value: daycare.filter(b => b.status === "done").length },
      { label: "No-show", value: countStatus(daycare, "no_show") },
      { label: `Attending ${word}`, value: attending }
    ];
  }

  const grooming = data.filter(b => b.serviceType === "grooming");
  const groomingDoneRate = percentage(
    grooming.filter(b => b.status === "done").length,
    grooming.length
  );
  const boardingCheckIn = data.filter(b => b.serviceType === "boarding" && b.checkInDate >= range.start && b.checkInDate <= range.end).length;
  const boardingCheckOut = data.filter(b => b.serviceType === "boarding" && b.checkOutDate >= range.start && b.checkOutDate <= range.end).length;

  return [
    { label: "Pending Services", value: countStatus(data, "pending") },
    { label: "Grooming Done Rate", value: groomingDoneRate },
    { label: "Boarding Check-in / Check-out", value: `${boardingCheckIn}/${boardingCheckOut}` },
    { label: `Daycare Attendance ${word}`, value: data.filter(b => b.serviceType === "daycare").length },
    { label: "No Show", value: countStatus(data, "no_show") }
  ];
}

function staffUtilizationToday() {
  if (!staff.length) return "0%";
  const todayDate = getToday();
  const staffWithBookingToday = new Set(
    bookings.filter(b => b.date === todayDate && b.status !== "cancelled" && b.status !== "no_show").map(b => Number(b.staffId))
  );
  return percentage(staffWithBookingToday.size, staff.length);
}

/* =========================
   KANBAN
========================= */

function promoteScheduledBookingsForToday() {
  const todayDate = getToday();
  bookings.forEach(booking => {
    if (booking.status === "scheduled" && booking.date === todayDate) {
      booking.status = "pending";
    }
  });
}

function renderKanban() {
  const board = document.getElementById("kanbanBoard");

  const columns = [
    { key: "pending", label: "Today Pending Service" },
    { key: "scheduled", label: "Scheduled" },
    { key: "done", label: "Done" },
    { key: "no_show", label: "No Show" },
    { key: "cancelled", label: "Cancelled" }
  ];

  const data = getKanbanBookings();

  board.innerHTML = columns.map(column => `
    <div class="kanban-column" data-status="${column.key}">
      <h3>${column.label}</h3>
      ${data
        .filter(booking => booking.status === column.key)
        .map(booking => renderBookingCard(booking))
        .join("")}
    </div>
  `).join("");

  setupKanbanDragAndDrop();
  setupBookingClickEvents();
}

function renderBookingCard(booking) {
  const staffMember = findRealStaff(booking.staffId);

  return `
    <div class="booking-card" draggable="true" data-booking-id="${booking.id}">
      <div class="booking-card-top">
        ${renderStatusTag(booking.status)}
      </div>

      <strong>${booking.petName} — ${booking.serviceLabel}</strong>
      <small>${booking.customerName}</small><br>
      <small>${booking.date} | ${booking.time}</small><br>
      <small>Staff: ${staffMember?.staff_name || "-"}</small><br>
      <small>RM ${booking.amount}</small>
    </div>
  `;
}

function setupKanbanDragAndDrop() {
  document.querySelectorAll(".booking-card").forEach(card => {
    card.addEventListener("dragstart", event => {
      draggedBookingId = event.currentTarget.dataset.bookingId;
    });
  });

  document.querySelectorAll(".kanban-column").forEach(column => {
    column.addEventListener("dragover", event => {
      event.preventDefault();
    });

    column.addEventListener("drop", async event => {
      event.preventDefault();

      const newStatus = event.currentTarget.dataset.status;
      const booking = bookings.find(b => b.id === draggedBookingId);
      draggedBookingId = null;

      if (booking && booking.status !== newStatus) {
        if (await updateBookingStatus(booking, newStatus)) renderAll();
      }
    });
  });
}

function setupBookingClickEvents() {
  document.querySelectorAll(".booking-card").forEach(card => {
    card.addEventListener("click", () => {
      openBookingDetails(card.dataset.bookingId);
    });
  });
}

/* =========================
   CALENDAR
========================= */

function renderCalendar() {
  const mode = document.getElementById("calendarMode").value;

  if (mode === "daily") {
    renderDailyCalendar();
    return;
  }

  if (mode === "weekly") {
    renderWeeklyCalendar();
    return;
  }

  if (mode === "monthly") {
    renderMonthlyCalendar();
  }
}

function renderDailyCalendar() {
  const grid = document.getElementById("calendarGrid");
  const selectedDate = calendarAnchorDate || getToday();

  grid.className = "calendar-grid calendar-daily-grid";

  let html = `
    <div class="calendar-head">Time</div>
    <div class="calendar-head today-column">${formatCalendarHeader(selectedDate)}</div>
  `;

  CALENDAR_HOURS.forEach(hour => {
    const slotBookings = getSlotBookings(selectedDate, hour);
    const addSlotArea = renderAddSlotArea(selectedDate, hour);

    html += `
      <div class="time-cell">${hour}</div>

      <div class="calendar-cell daily-booking-cell today-column" data-date="${selectedDate}" data-time="${hour}">
        ${slotBookings.map(renderCalendarBooking).join("")}
        ${addSlotArea}
      </div>
    `;
  });

  grid.innerHTML = html;

  setupCalendarSlotEvents();
  setupCalendarBookingDrag();
}

function renderWeeklyCalendar() {
  const grid = document.getElementById("calendarGrid");
  const weekStart = getStartOfWeek(calendarAnchorDate || getToday());
  const dates = getDateRange(weekStart, addDays(weekStart, 6));

  grid.className = "calendar-grid calendar-weekly-grid";

  let html = `<div class="calendar-head">Time</div>`;

  dates.forEach(date => {
    const todayClass = date === getToday() ? "today-column" : "";

    html += `
      <div class="calendar-head ${todayClass}" data-date-header="${date}">
        ${formatCalendarHeader(date)}
      </div>
    `;
  });

  CALENDAR_HOURS.forEach(hour => {
    html += `<div class="time-cell">${hour}</div>`;

    dates.forEach(date => {
      const todayClass = date === getToday() ? "today-column" : "";
      const slotBookings = getSlotBookings(date, hour);
      const addSlotArea = renderAddSlotArea(date, hour);

      html += `
        <div class="calendar-cell ${todayClass}" data-date="${date}" data-time="${hour}">
          ${slotBookings.map(renderCalendarBooking).join("")}
          ${addSlotArea}
        </div>
      `;
    });
  });

  grid.innerHTML = html;

  setupCalendarSlotEvents();
  setupCalendarBookingDrag();
}

function renderMonthlyCalendar() {
  const grid = document.getElementById("calendarGrid");
  const monthStart = getStartOfMonth(calendarAnchorDate || getToday());
  const calendarStart = getStartOfWeek(monthStart);
  const visibleDates = getDateRange(calendarStart, addDays(calendarStart, 34));

  grid.className = "calendar-grid calendar-month-grid";

  let html = "";

  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(day => {
    html += `<div class="calendar-head">${day}</div>`;
  });

  visibleDates.forEach(date => {
    const todayClass = date === getToday() ? "today-column" : "";
    const otherMonthClass = isSameMonth(date, monthStart) ? "" : "other-month";
    const dayBookings = getFilteredBookings().filter(booking => booking.date === date);
    const availableTime = findFirstAvailableTime(date);

    const addButton = availableTime
      ? `
        <button
          type="button"
          class="add-slot-btn"
          data-date="${date}"
          data-time="${availableTime}"
        >
          + Add Booking
        </button>
      `
      : `<div class="slot-full-note">Fully booked</div>`;

    html += `
      <div class="calendar-cell month-day-cell ${todayClass} ${otherMonthClass}" data-date="${date}">
        <div class="month-day-header">${formatMonthDay(date)}</div>

        ${dayBookings.map(renderCalendarBooking).join("")}

        ${addButton}
      </div>
    `;
  });

  grid.innerHTML = html;

  setupCalendarSlotEvents();
  setupCalendarBookingDrag();
}

function renderCalendarBooking(booking) {
  return `
    <div class="calendar-booking" draggable="true" data-booking-id="${booking.id}">
      <strong>${booking.petName}</strong><br>
      ${booking.serviceLabel}<br>
      ${renderStatusTag(booking.status)}
    </div>
  `;
}

function setupCalendarEvents() {
  document.getElementById("calendarStartDate").value = "";
  document.getElementById("calendarEndDate").value = "";

  document.getElementById("calendarMode").addEventListener("change", () => {
    calendarAnchorDate = document.getElementById("calendarStartDate").value || getToday();
    renderCalendar();
  });

  document.getElementById("applyCalendarFilter").addEventListener("click", () => {
    calendarAnchorDate = document.getElementById("calendarStartDate").value || getToday();
    renderCalendar();
  });

  document.getElementById("clearCalendarFilter").addEventListener("click", () => {
    document.getElementById("calendarMode").value = "weekly";
    document.getElementById("calendarStartDate").value = "";
    document.getElementById("calendarEndDate").value = "";
    calendarAnchorDate = getToday();
    renderCalendar();
  });

  document.getElementById("todayCalendarBtn").addEventListener("click", () => {
    calendarAnchorDate = getToday();
    renderCalendar();
  });

  document.getElementById("prevCalendarBtn").addEventListener("click", () => {
    moveCalendar("previous");
  });

  document.getElementById("nextCalendarBtn").addEventListener("click", () => {
    moveCalendar("next");
  });
}


function setupCalendarBookingDrag() {
  document.querySelectorAll(".calendar-booking").forEach(item => {
    item.addEventListener("dragstart", event => {
      draggedBookingId = event.currentTarget.dataset.bookingId;
    });

    item.addEventListener("click", event => {
      event.stopPropagation();
      openBookingDetails(event.currentTarget.dataset.bookingId);
    });
  });
}

function updateCalendarRangeByMode() {
  const mode = document.getElementById("calendarMode").value;
  const startDate = document.getElementById("calendarStartDate").value || getToday();

  if (mode === "daily") {
    document.getElementById("calendarEndDate").value = startDate;
  }

  if (mode === "weekly") {
    document.getElementById("calendarEndDate").value = addDays(startDate, 6);
  }

  if (mode === "monthly") {
    document.getElementById("calendarEndDate").value = addDays(startDate, 29);
  }

  renderCalendar();
}


function defaultDurationFor(type) {
  if (type === "boarding") return 1440;
  if (type === "grooming") return GROOMING_DEFAULT_DURATION;
  return 60;
}

function buildNewBookingDraft(date, time, staffId) {
  const type = currentServiceFilter !== "all" ? currentServiceFilter : "grooming";
  return {
    id: "new",
    realId: null,
    bookingType: type,
    petId: null,
    customerName: "",
    petName: "",
    serviceType: type,
    serviceLabel: "",
    price: 0,
    addOn: "",
    addOnPrice: 0,
    staffId: staffId ?? (staff[0]?.staff_id || ""),
    roomLabel: "",
    date,
    time,
    checkInTime: time,
    checkOutTime: "",
    duration: defaultDurationFor(type),
    status: "scheduled",
    amount: 0,
    checkInDate: type === "boarding" ? date : "",
    checkOutDate: type === "boarding" ? addDays(date, 1) : "",
    feedingInstruction: "",
    medicalInstruction: "",
    specialNote: "",
    paymentId: null,
  };
}

function openNewBooking() {
  const date = getToday();
  const time = findFirstAvailableTime(date) || "09:00";
  _newBookingDraft = buildNewBookingDraft(date, time);
  openBookingDetails(_newBookingDraft);
}

function createBookingFromSlot(date, time) {
  const type = currentServiceFilter !== "all" ? currentServiceFilter : "grooming";
  const duration = defaultDurationFor(type);

  const availableStaff = getAvailableStaffForSlot(date, time, duration);

  if (getSlotBookings(date, time).length >= 3 || availableStaff.length === 0) {
    alert("This timeslot is fully booked. Maximum 3 bookings are allowed, and each booking must use a different staff.");
    return;
  }

  _newBookingDraft = buildNewBookingDraft(date, time, availableStaff[0].staff_id);
  openBookingDetails(_newBookingDraft);
}

/* =========================
   LISTING
========================= */

function renderListing() {
  const tbody = document.getElementById("listingTableBody");

  const filteredData = getFilteredBookings().filter(booking => {
    const staffMember = findRealStaff(booking.staffId);

    const searchableText = `
      ${booking.customerName}
      ${booking.petName}
      ${booking.serviceLabel || ""}
      ${staffMember?.staff_name || ""}
    `.toLowerCase();

    return searchableText.includes(listingSearchKeyword);
  });

  filteredData.sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));

  tbody.innerHTML = filteredData.map(booking => {
    const staffMember = findRealStaff(booking.staffId);
    const durationLabel = booking.bookingType === "boarding"
      ? `${nightsBetweenDates(booking.checkInDate, booking.checkOutDate)} night(s)`
      : `${booking.duration} mins`;

    return `
      <tr>
        <td>${booking.date}</td>
        <td>${booking.customerName}</td>
        <td>${booking.petName}</td>
        <td>${booking.serviceLabel || "-"}</td>
        <td>${staffMember?.staff_name || "-"}</td>
        <td>${booking.time}</td>
        <td>${durationLabel}</td>

        <td>
          <select
            class="status-select"
            data-booking-id="${booking.id}"
            onchange="updateListingStatus(event)"
          >
            <option value="pending" ${booking.status === "pending" ? "selected" : ""}>Pending Service</option>
            <option value="scheduled" ${booking.status === "scheduled" ? "selected" : ""}>Scheduled</option>
            <option value="done" ${booking.status === "done" ? "selected" : ""}>Done</option>
            <option value="no_show" ${booking.status === "no_show" ? "selected" : ""}>No Show</option>
            <option value="cancelled" ${booking.status === "cancelled" ? "selected" : ""}>Cancelled</option>
          </select>
        </td>

        <td>RM ${booking.amount}</td>
        <td>${booking.roomLabel || "-"}</td>

        <td>
          <button
            class="edit-btn"
            onclick="openBookingDetails('${booking.id}')"
          >
            Edit
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

async function updateListingStatus(event) {
  event.stopPropagation();

  const bookingId = event.target.dataset.bookingId;
  const newStatus = event.target.value;

  const booking = bookings.find(item => item.id === bookingId);
  if (!booking) return;

  const previousStatus = booking.status;
  const ok = await updateBookingStatus(booking, newStatus);
  if (!ok) {
    event.target.value = previousStatus;
    return;
  }

  renderMetricCards();
  renderKanban();
  renderCalendar();
  renderListing();
}

/* =========================
   MODAL / FORM
========================= */

function openBookingDetails(bookingIdOrObj) {
  const booking = typeof bookingIdOrObj === "string"
    ? bookings.find(b => b.id === bookingIdOrObj)
    : bookingIdOrObj;
  if (!booking) return;

  document.getElementById("bookingId").value = booking.id;
  document.getElementById("selectedPetId").value = booking.petId || "";
  document.getElementById("petSearchInput").value = "";
  document.getElementById("petSearchResults").classList.add("hidden");
  document.getElementById("customerName").value = booking.customerName || "";
  document.getElementById("petName").value = booking.petName || "";
  document.getElementById("serviceType").value = booking.serviceType;

  populateStaffDropdown();
  document.getElementById("staffName").value = booking.staffId;
  document.getElementById("bookingStatus").value = booking.status;
  document.getElementById("amount").value = booking.amount;
  document.getElementById("specialNote").value = booking.specialNote || "";

  document.getElementById("bookingDate").value = booking.date || "";
  document.getElementById("bookingTime").value = booking.bookingType === "grooming" ? (booking.time || "") : "";

  const isGrooming = booking.bookingType === "grooming";
  document.getElementById("groomingServiceName").value = isGrooming ? (booking.serviceLabel || "") : "";
  document.getElementById("groomingPrice").value = isGrooming ? booking.price : "";
  document.getElementById("groomingAddOn").value = isGrooming ? (booking.addOn || "") : "";
  document.getElementById("groomingAddOnPrice").value = isGrooming ? (booking.addOnPrice || 0) : "";

  const isDaycare = booking.bookingType === "daycare";
  document.getElementById("daycareCheckInTime").value = isDaycare ? (booking.checkInTime || "") : "";
  document.getElementById("daycareCheckOutTime").value = isDaycare ? (booking.checkOutTime || "") : "";
  document.getElementById("daycarePackageType").value = isDaycare ? (booking.serviceLabel || "") : "";
  document.getElementById("daycarePrice").value = isDaycare ? booking.price : "";

  const isBoarding = booking.bookingType === "boarding";
  document.getElementById("checkInDate").value = isBoarding ? (booking.checkInDate || "") : "";
  document.getElementById("checkInTime").value = isBoarding ? (booking.checkInTime || "") : "";
  document.getElementById("checkOutDate").value = isBoarding ? (booking.checkOutDate || "") : "";
  document.getElementById("checkOutTime").value = isBoarding ? (booking.checkOutTime || "") : "";
  document.getElementById("boardingRoomType").value = isBoarding ? (booking.roomLabel || "") : "";
  document.getElementById("boardingPricePerNight").value = isBoarding ? booking.price : "";
  document.getElementById("boardingFeeding").value = isBoarding ? (booking.feedingInstruction || "") : "";
  document.getElementById("boardingMedical").value = isBoarding ? (booking.medicalInstruction || "") : "";

  toggleServiceSpecificFields();

  document.getElementById("bookingModal").style.display = "flex";
}

function closeModal() {
  _newBookingDraft = null;
  document.getElementById("bookingModal").style.display = "none";
}

function recalcAmount() {
  const type = document.getElementById("serviceType").value;
  let amount = 0;

  if (type === "grooming") {
    const price = Number(document.getElementById("groomingPrice").value) || 0;
    const addOnPrice = Number(document.getElementById("groomingAddOnPrice").value) || 0;
    amount = price + addOnPrice;
  } else if (type === "daycare") {
    amount = Number(document.getElementById("daycarePrice").value) || 0;
  } else if (type === "boarding") {
    const pricePerNight = Number(document.getElementById("boardingPricePerNight").value) || 0;
    const checkInDate = document.getElementById("checkInDate").value;
    const checkOutDate = document.getElementById("checkOutDate").value;
    const nights = checkInDate && checkOutDate ? nightsBetweenDates(checkInDate, checkOutDate) : 1;
    amount = pricePerNight * nights;
  }

  document.getElementById("amount").value = amount;
}

async function saveBooking() {
  const bookingId = document.getElementById("bookingId").value;
  const isNew = bookingId === "new";
  const booking = isNew ? _newBookingDraft : bookings.find(item => item.id === bookingId);
  if (!booking) return;

  const petId = document.getElementById("selectedPetId").value;
  if (!petId) {
    alert("Please search and select a pet before saving.");
    return;
  }
  const pet = findPet(petId);

  const type = document.getElementById("serviceType").value;
  if (!isNew && type !== booking.bookingType) {
    alert("Changing service type on an existing booking isn't supported — cancel this booking and create a new one for the new service type.");
    return;
  }

  const staffId = Number(document.getElementById("staffName").value);
  const status = document.getElementById("bookingStatus").value;
  const notes = document.getElementById("specialNote").value;

  let payload = {
    pet_id: Number(petId),
    staff_id: staffId,
    booking_status: denormalizeStatus(type, status),
  };

  let date, time, duration, amount, serviceLabel, price;
  let addOn = "", addOnPrice = 0, roomLabel = "";
  let checkInDate = "", checkOutDate = "", checkInTime = "", checkOutTime = "";
  let feedingInstruction = "", medicalInstruction = "";

  if (type === "grooming") {
    date = document.getElementById("bookingDate").value;
    time = document.getElementById("bookingTime").value;
    serviceLabel = document.getElementById("groomingServiceName").value;
    price = Number(document.getElementById("groomingPrice").value) || 0;
    addOn = document.getElementById("groomingAddOn").value;
    addOnPrice = Number(document.getElementById("groomingAddOnPrice").value) || 0;
    duration = GROOMING_DEFAULT_DURATION;
    amount = price + addOnPrice;
    payload = { ...payload, service_name: serviceLabel, booking_date: date, booking_time: time ? `${time}:00` : null, price, add_on: addOn || "-", add_on_price: addOnPrice, notes: notes || "-" };
  } else if (type === "daycare") {
    date = document.getElementById("bookingDate").value;
    checkInTime = document.getElementById("daycareCheckInTime").value;
    checkOutTime = document.getElementById("daycareCheckOutTime").value;
    time = checkInTime;
    serviceLabel = document.getElementById("daycarePackageType").value;
    price = Number(document.getElementById("daycarePrice").value) || 0;
    duration = minutesBetweenTimes(checkInTime, checkOutTime);
    amount = price;
    payload = { ...payload, booking_date: date, check_in_time: checkInTime ? `${checkInTime}:00` : null, check_out_time: checkOutTime ? `${checkOutTime}:00` : null, package_type: serviceLabel, price, special_instruction: notes || "-" };
  } else {
    checkInDate = document.getElementById("checkInDate").value;
    checkInTime = document.getElementById("checkInTime").value;
    checkOutDate = document.getElementById("checkOutDate").value;
    checkOutTime = document.getElementById("checkOutTime").value;
    date = checkInDate;
    time = checkInTime;
    roomLabel = document.getElementById("boardingRoomType").value;
    price = Number(document.getElementById("boardingPricePerNight").value) || 0;
    const nights = nightsBetweenDates(checkInDate, checkOutDate);
    duration = 1440;
    amount = price * nights;
    feedingInstruction = document.getElementById("boardingFeeding").value;
    medicalInstruction = document.getElementById("boardingMedical").value;
    payload = { ...payload, check_in_date: checkInDate, check_in_time: checkInTime ? `${checkInTime}:00` : null, check_out_date: checkOutDate, check_out_time: checkOutTime ? `${checkOutTime}:00` : null, room_type: roomLabel, price_per_night: price, feeding_instruction: feedingInstruction || "-", medical_instruction: medicalInstruction || "-", notes: notes || "-" };
  }

  const leavingActiveSchedule = status === "cancelled" || status === "no_show";
  const excludeId = isNew ? "" : bookingId;
  if (!leavingActiveSchedule && !canAddBookingToSlot(date, time, staffId, duration, excludeId)) {
    alert("This booking cannot be saved. The selected timeslot already has 3 bookings or the selected staff is already assigned at this time.");
    return;
  }

  try {
    if (isNew) {
      const result = await api.createBooking(type, payload);
      const idColumn = { grooming: "grooming_booking_id", daycare: "daycare_booking_id", boarding: "boarding_booking_id" }[type];
      booking.realId = result.booking[idColumn];
      booking.id = `${type}-${booking.realId}`;
      booking.paymentId = result.booking.payment_id;
      bookings.push(booking);
    } else {
      await api.updateBooking(type, booking.realId, payload);
    }
  } catch (err) {
    alert(err.message);
    return;
  }

  booking.bookingType = type;
  booking.serviceType = type;
  booking.petId = Number(petId);
  booking.customerName = pet?.customerName || booking.customerName;
  booking.petName = pet?.pet_name || booking.petName;
  booking.staffId = staffId;
  booking.status = status;
  booking.date = date;
  booking.time = time;
  booking.duration = duration;
  booking.amount = amount;
  booking.serviceLabel = serviceLabel;
  booking.price = price;
  booking.addOn = addOn;
  booking.addOnPrice = addOnPrice;
  booking.roomLabel = roomLabel;
  booking.checkInDate = checkInDate;
  booking.checkOutDate = checkOutDate;
  booking.checkInTime = checkInTime;
  booking.checkOutTime = checkOutTime;
  booking.feedingInstruction = feedingInstruction;
  booking.medicalInstruction = medicalInstruction;
  booking.specialNote = notes;

  _newBookingDraft = null;
  closeModal();
  renderAll();
}

function populateStaffDropdown() {
  const staffSelect = document.getElementById("staffName");
  staffSelect.innerHTML = staff.map(member => `
    <option value="${member.staff_id}">${member.staff_name}</option>
  `).join("");
}

function toggleServiceSpecificFields() {
  const selectedType = document.getElementById("serviceType").value;

  document.querySelectorAll(".date-field").forEach(field => {
    field.classList.toggle("hidden", !["grooming", "daycare"].includes(selectedType));
  });
  document.querySelectorAll(".grooming-only").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "grooming");
  });
  document.querySelectorAll(".daycare-only").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "daycare");
  });
  document.querySelectorAll(".boarding-only").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "boarding");
  });
}

function moveCalendar(direction) {
  const mode = document.getElementById("calendarMode").value;
  const step = direction === "next" ? 1 : -1;

  if (mode === "daily") {
    calendarAnchorDate = addDays(calendarAnchorDate, step);
  }

  if (mode === "weekly") {
    calendarAnchorDate = addDays(calendarAnchorDate, step * 7);
  }

  if (mode === "monthly") {
    calendarAnchorDate = addMonths(calendarAnchorDate, step);
  }

  renderCalendar();
}

function toLocalDateString(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addMonths(dateString, months) {
  const date = new Date(dateString);
  date.setMonth(date.getMonth() + months);
  return toLocalDateString(date);
}

function isSameMonth(dateString, monthReference) {
  const date = new Date(dateString);
  const reference = new Date(monthReference);

  return date.getFullYear() === reference.getFullYear() &&
    date.getMonth() === reference.getMonth();
}

function formatMonthDay(dateString) {
  const date = new Date(dateString);

  return date.toLocaleDateString("en-MY", {
    day: "2-digit",
    month: "short"
  });
}

/* =========================
   UTILITIES
========================= */
function renderStatusTag(status) {
  return `<span class="status-tag status-${status}">${formatStatus(status)}</span>`;
}

function setActiveTab(groupSelector, activeButton) {
  document.querySelectorAll(`${groupSelector} .tab-btn`).forEach(button => {
    button.classList.remove("active");
  });

  activeButton.classList.add("active");
}

function findService(serviceId) {
  return services.find(service => service.id === serviceId);
}

function findStaff(staffId) {
  return staff.find(member => member.id === staffId);
}

function findRoom(roomId) {
  return rooms.find(room => room.id === roomId);
}

function getSlotBookings(date, time, excludeBookingId = "") {
  return bookings.filter(booking => {
    return booking.date === date &&
      booking.time === time &&
      booking.id !== excludeBookingId &&
      booking.status !== "cancelled" &&
      booking.status !== "no_show";
  });
}

function timeToMinutes(timeStr) {
  const [hours, minutes] = timeStr.split(":").map(Number);
  return hours * 60 + minutes;
}

function isStaffAlreadyBooked(date, time, staffId, duration = 60, excludeBookingId = "") {
  const newStart = timeToMinutes(time);
  const newEnd = newStart + duration;

  return bookings.some(booking => {
    if (booking.date !== date || Number(booking.staffId) !== Number(staffId) || booking.id === excludeBookingId ||
        booking.status === "cancelled" || booking.status === "no_show" || !booking.time) {
      return false;
    }
    const existingStart = timeToMinutes(booking.time);
    const existingEnd = existingStart + booking.duration;
    return newStart < existingEnd && existingStart < newEnd;
  });
}

function getAvailableStaffForSlot(date, time, duration = 60, excludeBookingId = "") {
  return staff.filter(member => {
    return !isStaffAlreadyBooked(date, time, member.staff_id, duration, excludeBookingId);
  });
}

function canAddBookingToSlot(date, time, staffId, duration = 60, excludeBookingId = "") {
  const slotBookings = getSlotBookings(date, time, excludeBookingId);

  if (slotBookings.length >= 3) {
    return false;
  }

  if (isStaffAlreadyBooked(date, time, staffId, duration, excludeBookingId)) {
    return false;
  }

  return true;
}

function renderAddSlotArea(date, time) {
  const slotBookings = getSlotBookings(date, time);
  const availableStaff = getAvailableStaffForSlot(date, time);

  if (slotBookings.length >= 3 || availableStaff.length === 0) {
    return `<div class="slot-full-note">Fully booked</div>`;
  }

  return `
    <button
      type="button"
      class="add-slot-btn"
      data-date="${date}"
      data-time="${time}"
    >
      + Add Slot
    </button>
  `;
}

function findFirstAvailableTime(date, duration = 60) {
  return CALENDAR_HOURS.find(hour => {
    const slotBookings = getSlotBookings(date, hour);
    const availableStaff = getAvailableStaffForSlot(date, hour, duration);

    return slotBookings.length < 3 && availableStaff.length > 0;
  });
}

function countStatus(data, status) {
  return data.filter(booking => booking.status === status).length;
}

function percentage(value, total) {
  if (!total) return "0%";
  return `${Math.round((value / total) * 100)}%`;
}

function getCalendarDates(mode, startInput, endInput) {
  // If user selects both start and end date, use the selected date range
  if (startInput && endInput) {
    return getDateRange(startInput, endInput);
  }

  // If only start date is selected, auto-create range based on mode
  if (startInput && !endInput) {
    if (mode === "daily") return [startInput];
    if (mode === "weekly") return getDateRange(startInput, addDays(startInput, 6));
    if (mode === "monthly") return getDateRange(startInput, addDays(startInput, 29));
  }

  // If no filter is selected, render a wider scrollable range
  // This allows user to scroll left for past dates and right for future dates
  const today = getToday();

  if (mode === "daily") {
    return getDateRange(addDays(today, -7), addDays(today, 7));
  }

  if (mode === "weekly") {
    return getDateRange(addDays(today, -21), addDays(today, 42));
  }

  if (mode === "monthly") {
    return getDateRange(addDays(today, -30), addDays(today, 90));
  }

  return getDateRange(addDays(today, -21), addDays(today, 42));
}

function formatStatus(status) {
  const map = {
    pending: "Pending Service",
    scheduled: "Scheduled",
    done: "Done",
    no_show: "No Show",
    cancelled: "Cancelled"
  };

  return map[status] || status;
}

function getToday() {
  return toLocalDateString(new Date());
}

function addDays(dateString, days) {
  const date = new Date(dateString);
  date.setDate(date.getDate() + days);
  return toLocalDateString(date);
}

function getStartOfWeek(dateString) {
  const date = new Date(dateString);
  const day = date.getDay(); // 0 = Sun
  const diff = day === 0 ? -6 : 1 - day; // shift to Monday
  date.setDate(date.getDate() + diff);
  return toLocalDateString(date);
}

function getStartOfMonth(dateString) {
  const date = new Date(dateString);
  date.setDate(1);
  return toLocalDateString(date);
}

function formatCalendarHeader(dateString) {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-MY", {
    weekday: "short",
    day: "2-digit",
    month: "short"
  });
}

function getDateRange(startDate, endDate) {
  const dates = [];
  let current = new Date(startDate);
  const end = new Date(endDate);

  while (current <= end) {
    dates.push(toLocalDateString(current));
    current.setDate(current.getDate() + 1);
  }

  return dates;
}


/* ==========================================================================
   DAILY OVERVIEW (dailyoverview.html)
   ========================================================================== */

let currentFilter = 'all';
// Unified date-range filter (Daily/Weekly/Monthly + Prev/Today/Next), same
// pattern as the Analytical Dashboard. The Weekly Booking Schedule is always
// a fixed 7-day grid, so it shows the calendar week containing the range's
// start date regardless of which period is selected.
let dailyOverviewRange = { start: today, end: today };
let dailyOverviewPeriod = 'daily';

function findServiceName(id) { return findService(id)?.name || '-'; }
function findRoomName(id) { return findRoom(id)?.name || '-'; }
// Dual-shape helpers: real bookings (booking.html/dailyoverview.html) carry
// serviceLabel/roomLabel directly; mock bookings (dashboard.html/staff.html,
// not yet converted) only have serviceId/roomId and need the catalog lookup.
function bookingServiceLabel(b) { return b.serviceLabel != null ? b.serviceLabel : findServiceName(b.serviceId); }
function bookingRoomLabel(b) { return b.roomLabel || (b.roomId ? findRoomName(b.roomId) : ''); }
function filterBookingsByService(filter) {
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

const SLA_MINUTES = { pendingService: 15, enquiry: 180, loyalty: 120 };

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
   ENQUIRY PRIORITY
   Computed, not stored, so it can never go stale:
     Urgent — still pending and past the 3-hour reply SLA
     High   — boarding enquiries (guest may be waiting on-site right now)
              or the message itself signals urgency
     Normal — everything else
========================= */

const URGENT_KEYWORDS = ['urgent', 'asap', 'emergency', 'now', 'waiting', 'immediately', 'right now'];

function computeEnquiryPriority(e) {
  if (e.status === 'pending' && slaStatus('enquiry', e.receivedAt).breached) return 'urgent';
  const msg = e.message.toLowerCase();
  if (e.relatedService === 'boarding' || URGENT_KEYWORDS.some(k => msg.includes(k))) return 'high';
  return 'normal';
}

function enquiryPriorityLabel(level) {
  return level.charAt(0).toUpperCase() + level.slice(1);
}

function enquiryPriorityBadge(e) {
  const level = computeEnquiryPriority(e);
  if (level === 'urgent') return `<span class="status-tag status-no_show">Urgent</span>`;
  if (level === 'high') return `<span class="status-tag status-pending">High</span>`;
  return `<span class="status-tag status-off">Normal</span>`;
}

function computeSlaCompliance() {
  const pendingGrooming = bookings.filter(b => b.serviceType === 'grooming' && b.date === today && b.status === 'pending');
  const pendingEnquiries = enquiries.filter(e => e.status === 'pending');
  const pendingLoyalty = loyaltyRequests.filter(r => r.status === 'pending');
  const total = pendingGrooming.length + pendingEnquiries.length + pendingLoyalty.length;
  const breaches = slaBreachCount('pendingService', pendingGrooming, 'time')
    + slaBreachCount('enquiry', pendingEnquiries, 'receivedAt')
    + slaBreachCount('loyalty', pendingLoyalty, 'requestedAt');
  return { total, breaches, rate: total ? Math.round(((total - breaches) / total) * 100) : 100 };
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

function buildActionCards(filter, range = dailyOverviewRange, period = dailyOverviewPeriod) {
  const inRange = d => d >= range.start && d <= range.end;
  const word = periodWord(period);

  const rangeBookings = filterBookingsByService(filter).filter(b => inRange(b.date));
  const pendingConfirmation = rangeBookings.filter(b => b.status === 'scheduled').length;

  // The mock enquiries/loyaltyRequests arrays used below have no date field
  // at all (see common.js's dailyoverview section header) — they stay
  // unfiltered by the date range, same as before this feature.
  const relevantEnquiries = enquiries.filter(e => filter === 'all' || e.relatedService === filter);
  const pendingEnquiries = relevantEnquiries.filter(e => e.status === 'pending');
  const enquirySlaBreaches = slaBreachCount('enquiry', pendingEnquiries, 'receivedAt');

  const relevantLoyalty = loyaltyRequests.filter(r => filter === 'all' || r.relatedService === filter);
  const pendingLoyalty = relevantLoyalty.filter(r => r.status === 'pending');
  const loyaltySlaBreaches = slaBreachCount('loyalty', pendingLoyalty, 'requestedAt');

  const cards = [];

  if (filter === 'all' || filter === 'grooming') {
    const groomingPending = bookings.filter(b => b.serviceType === 'grooming' && inRange(b.date) && b.status === 'pending');
    const groomingSlaBreaches = slaBreachCount('pendingService', groomingPending, 'time');
    cards.push({
      key: 'pendingGrooming',
      icon: 'grooming-scissors.png', label: 'Pending Grooming', value: groomingPending.length,
      sub: groomingSlaBreaches > 0 ? `${groomingSlaBreaches} breaching 15-min SLA` : 'Within SLA',
      tone: groomingSlaBreaches > 0 ? 'alert' : 'info'
    });
  }

  cards.push(
    {
      key: 'pendingConfirmation',
      icon: 'confirm-circle.png', label: 'Pending Booking Confirmation', value: pendingConfirmation,
      sub: `Scheduled ${word.toLowerCase()}, awaiting confirmation`, tone: 'info'
    },
    {
      key: 'pendingEnquiries',
      icon: 'chat-message.png', label: 'Pending Enquiries', value: pendingEnquiries.length,
      sub: enquirySlaBreaches > 0 ? `${enquirySlaBreaches} breaching 30-min reply SLA` : 'Within SLA', tone: enquirySlaBreaches > 0 ? 'alert' : 'purple'
    },
    {
      key: 'pendingLoyalty',
      icon: 'loyalty-reward-gift.png', label: 'Pending Loyalty Redemption', value: pendingLoyalty.length,
      sub: loyaltySlaBreaches > 0 ? `${loyaltySlaBreaches} breaching 2h approval SLA` : 'Within SLA', tone: loyaltySlaBreaches > 0 ? 'alert' : 'pink'
    }
  );

  if (filter === 'all' || filter === 'boarding') {
    const boardingBookings = bookings.filter(b => b.serviceType === 'boarding');
    const checkInsDue = boardingBookings.filter(b => inRange(b.checkInDate) && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled').length;
    const checkOutsDue = boardingBookings.filter(b => inRange(b.checkOutDate) && b.status !== 'no_show' && b.status !== 'cancelled').length;

    cards.push(
      { key: 'boardingCheckIn', icon: 'login.png', label: 'Boarding Check-In Due', value: checkInsDue, sub: `Arrivals to confirm (${word.toLowerCase()})`, tone: 'success' },
      { key: 'boardingCheckOut', icon: 'logout.png', label: 'Boarding Check-Out Due', value: checkOutsDue, sub: `Departures to confirm (${word.toLowerCase()})`, tone: 'warning' }
    );
  }

  if (filter === 'all' || filter === 'daycare') {
    const daycareBookings = bookings.filter(b => b.serviceType === 'daycare' && inRange(b.date));
    const checkInsDue = daycareBookings.filter(b => b.status === 'pending' || b.status === 'scheduled').length;
    const pendingPickup = daycareBookings.filter(b => b.status === 'done').length;

    cards.push(
      { key: 'daycareCheckIn', icon: 'login.png', label: 'Daycare Check-In Due', value: checkInsDue, sub: 'Drop-offs to confirm', tone: 'success' },
      { key: 'daycarePendingPickup', icon: 'logout.png', label: 'Daycare Pending Pick-Up', value: pendingPickup, sub: 'Waiting for parent pickup', tone: 'warning' }
    );
  }

  return cards;
}

function renderActionCard(card) {
  const tone = TONES[card.tone] || TONES.info;
  return `
    <div class="kpi-hero-card action-card clickable" style="border-color:${tone.bg};" onclick="openCardDetail('${card.key}')" title="${card.sub}">
      <div class="action-card-head">
        <img src="icon/${card.icon}" alt="" class="card-icon">
        <span class="kpi-hero-label">${card.label}</span>
      </div>
      <div class="action-card-value">${card.value}</div>
    </div>
  `;
}

/* =========================
   SERVICE LOAD CHART
========================= */

function renderServiceLoadChart(range = dailyOverviewRange, period = dailyOverviewPeriod) {
  const types = [
    { key: 'grooming', icon: 'grooming-scissors.png', label: 'Grooming', tone: 'info' },
    { key: 'boarding', icon: 'boarding.png', label: 'Boarding', tone: 'purple' },
    { key: 'daycare',  icon: 'dog-play.png', label: 'Daycare',  tone: 'warning' }
  ];
  const counts = types.map(t => bookings.filter(b => b.serviceType === t.key && b.date >= range.start && b.date <= range.end && b.status !== "cancelled" && b.status !== "no_show").length);
  const max = Math.max(...counts, 1);
  const total = counts.reduce((a, b) => a + b, 0);

  q('serviceLoadTotal').textContent = `${total} booking${total === 1 ? '' : 's'} ${periodWord(period).toLowerCase()}`;

  q('serviceLoadChart').innerHTML = types.map((t, i) => `
    <div class="bar-label-item" onclick="openServiceLoadDetail('${t.key}')">
      <span class="bar-count">${counts[i]}</span>
      <div class="bar-track">
        <div class="mini-bar" style="height:${Math.max((counts[i] / max) * 100, counts[i] ? 8 : 2)}%;${toneStyle(t.tone)}background-color:var(--tone-color);"></div>
      </div>
      <span class="bar-name"><img src="icon/${t.icon}" alt="" class="bar-icon">${t.label}</span>
    </div>
  `).join('');
}

/* =========================
   DONUT CHART (shared, interactive)
========================= */

function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = (angleDeg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function describeWedge(cx, cy, r, startAngle, endAngle) {
  if (endAngle - startAngle >= 360) endAngle = startAngle + 359.99;
  const startPt = polarToCartesian(cx, cy, r, startAngle);
  const endPt = polarToCartesian(cx, cy, r, endAngle);
  const largeArc = (endAngle - startAngle) > 180 ? 1 : 0;
  return `M${cx},${cy} L${startPt.x},${startPt.y} A${r},${r} 0 ${largeArc} 1 ${endPt.x},${endPt.y} Z`;
}

function renderInteractiveDonut({ chartElId, totalElId, legendElId, segments, onSegmentClick }) {
  const chartEl = q(chartElId);
  const total = segments.reduce((sum, s) => sum + s.count, 0);
  const cx = 50, cy = 50, r = 48;

  let angle = 0;
  const arcs = segments.filter(s => s.count > 0).map(s => {
    const pct = total ? (s.count / total) * 100 : 0;
    const startAngle = angle;
    const endAngle = angle + pct * 3.6;
    angle = endAngle;
    return { ...s, pct, startAngle, endAngle };
  });

  const wedgesHtml = total
    ? arcs.map(a => `<path class="donut-segment" data-key="${a.key}" d="${describeWedge(cx, cy, r, a.startAngle, a.endAngle)}" fill="${a.color}"></path>`).join("")
    : `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--accent-sand)"></circle>`;

  chartEl.querySelectorAll(".donut-svg, .donut-tooltip").forEach(el => el.remove());
  chartEl.insertAdjacentHTML("afterbegin", `
    <svg class="donut-svg" viewBox="0 0 100 100">${wedgesHtml}</svg>
    <div class="donut-tooltip"></div>
  `);

  if (totalElId) q(totalElId).textContent = total;

  const svgEl = chartEl.querySelector(".donut-svg");
  const tooltip = chartEl.querySelector(".donut-tooltip");
  const segmentEls = svgEl.querySelectorAll(".donut-segment");

  function showTooltip(seg) {
    const mid = (seg.startAngle + seg.endAngle) / 2;
    const pt = polarToCartesian(cx, cy, r * 0.82, mid);
    const rect = svgEl.getBoundingClientRect();
    tooltip.style.left = `${(pt.x / 100) * rect.width}px`;
    tooltip.style.top = `${(pt.y / 100) * rect.height}px`;

    tooltip.innerHTML = "";
    const title = document.createElement("div");
    title.className = "trend-tooltip-title";
    title.textContent = seg.label;
    const row = document.createElement("div");
    row.className = "trend-tooltip-row";
    const key = document.createElement("span");
    key.className = "trend-tooltip-key";
    key.style.backgroundColor = seg.color;
    const val = document.createElement("span");
    val.className = "trend-tooltip-value";
    val.textContent = `${seg.count} (${Math.round(seg.pct)}%)`;
    row.appendChild(key);
    row.appendChild(val);
    tooltip.appendChild(title);
    tooltip.appendChild(row);
    tooltip.classList.add("is-active");
  }

  function hideTooltip() { tooltip.classList.remove("is-active"); }

  function setActive(activeKey) {
    segmentEls.forEach(el => el.classList.toggle("is-active", el.dataset.key === activeKey));
  }

  segmentEls.forEach(pathEl => {
    const seg = arcs.find(a => a.key === pathEl.dataset.key);
    pathEl.style.cursor = onSegmentClick ? "pointer" : "default";
    pathEl.addEventListener("pointerenter", () => { setActive(seg.key); showTooltip(seg); });
    pathEl.addEventListener("pointerleave", () => { setActive(null); hideTooltip(); });
    if (onSegmentClick) pathEl.addEventListener("click", () => onSegmentClick(seg.key));
  });

  if (legendElId) {
    q(legendElId).querySelectorAll(".chart-legend-item").forEach(item => {
      const key = item.dataset.key;
      const seg = arcs.find(a => a.key === key);
      item.addEventListener("pointerenter", () => { if (seg) { setActive(key); showTooltip(seg); } });
      item.addEventListener("pointerleave", () => { setActive(null); hideTooltip(); });
    });
  }
}

/* =========================
   BOOKING STATUS DONUT
========================= */

const STATUS_META = [
  { key: 'pending',   label: 'Pending Service', color: '#F59E0B' },
  { key: 'scheduled', label: 'Scheduled',       color: '#3B82F6' },
  { key: 'done',      label: 'Done',            color: '#10B981' },
  { key: 'no_show',   label: 'No Show',         color: '#EF4444' },
  { key: 'cancelled', label: 'Cancelled',       color: '#78716C' }
];

function renderStatusDonut(filter, range = dailyOverviewRange) {
  const rangeBookings = filterBookingsByService(filter).filter(b => b.date >= range.start && b.date <= range.end);
  const counts = STATUS_META.map(s => rangeBookings.filter(b => b.status === s.key).length);
  const total = counts.reduce((a, b) => a + b, 0);

  const scopeLabel = filter === 'all' ? 'All bookings' : `${filter.charAt(0).toUpperCase() + filter.slice(1)} bookings`;
  q('statusScopeLabel').textContent = scopeLabel;

  q('statusLegend').innerHTML = STATUS_META.map((s, i) => {
    const pct = total ? Math.round((counts[i] / total) * 100) : 0;
    return `
      <div class="chart-legend-item" data-key="${s.key}" onclick="openServiceStatusDetail('${s.key}')">
        <span class="legend-swatch" style="background-color:${s.color};"></span>
        <span>${s.label} ${counts[i]} (${pct}%)</span>
      </div>
    `;
  }).join('');

  renderInteractiveDonut({
    chartElId: 'statusDonut',
    totalElId: 'statusDonutTotal',
    legendElId: 'statusLegend',
    segments: STATUS_META.map((s, i) => ({ key: s.key, label: s.label, count: counts[i], color: s.color })),
    onSegmentClick: openServiceStatusDetail
  });
}

/* =========================
   ROOM & PLAY AREA STATUS
========================= */

// Your real Supabase schema has no rooms/capacity catalog — only
// boarding_booking.room_type, a free-text label per booking. So instead of
// a fixed room list, this derives "rooms in use" from whatever room_type
// labels currently appear in real boarding bookings.
function getActiveRoomLabels() {
  const labels = new Set();
  bookings.forEach(b => { if (b.bookingType === 'boarding' && b.roomLabel) labels.add(b.roomLabel); });
  return [...labels].sort();
}

function renderRoomStatus(filter, range = dailyOverviewRange, period = dailyOverviewPeriod) {
  const el = q('roomStatusList');

  if (filter === 'grooming' || filter === 'daycare') {
    const label = filter.charAt(0).toUpperCase() + filter.slice(1);
    el.innerHTML = `<p class="queue-empty">${label} does not use dedicated rooms.</p>`;
    q('roomStatusCount').textContent = '0 rooms';
    return;
  }

  const roomLabels = getActiveRoomLabels();
  q('roomStatusCount').textContent = `${roomLabels.length} room${roomLabels.length === 1 ? '' : 's'}`;

  if (!roomLabels.length) {
    el.innerHTML = `<p class="queue-empty">No boarding rooms currently in use.</p>`;
    return;
  }

  const word = periodWord(period);

  el.innerHTML = roomLabels.map(roomLabel => {
    const roomBookings = bookings.filter(b => b.bookingType === 'boarding' && b.roomLabel === roomLabel);
    const checkoutInRange = roomBookings.some(b => b.checkOutDate >= range.start && b.checkOutDate <= range.end && b.status !== 'no_show' && b.status !== 'cancelled');
    const activeInRange = roomBookings.some(b =>
      b.checkInDate && b.checkOutDate && b.checkInDate <= range.end && b.checkOutDate >= range.start &&
      b.status !== 'no_show' && b.status !== 'cancelled'
    );

    let label = 'Vacant';
    let statusKey = 'done';
    if (checkoutInRange) { label = `Needs Cleaning · Checkout ${word}`; statusKey = 'no_show'; }
    else if (activeInRange) { label = `Occupied ${word}`; statusKey = 'scheduled'; }

    return `
      <div class="room-status-row" onclick="openRoomDetail('${roomLabel}')">
        <span>${roomLabel}</span>
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

function renderWeeklySchedule(filter, range = dailyOverviewRange) {
  // Always a fixed 7-day grid — shows the calendar week containing the
  // filter's range start, so it stays in sync with the master date filter
  // (Daily/Monthly periods land on the week containing that day/month-start).
  const weekStart = getStartOfWeek(range.start);
  const dates = getDateRange(weekStart, addDays(weekStart, 6));

  q('scheduleWeekLabel').textContent = `${formatShortDate(weekStart)} – ${formatShortDate(addDays(weekStart, 6))}`;

  q('scheduleHead').innerHTML = dates.map(date => {
    const d = new Date(date + 'T00:00:00');
    const isToday = date === today;
    return `<div class="schedule-head ${isToday ? 'is-today' : ''}">${d.toLocaleDateString('en-MY', { weekday: 'short' })} ${d.getDate()}</div>`;
  }).join('');

  let bodyHtml = '';
  SCHEDULE_HOURS.forEach(hour => {
    bodyHtml += `<div class="schedule-time">${hour}</div>`;
    dates.forEach(date => {
      const slotBookings = filterBookingsByService(filter).filter(b => b.date === date && slotForTime(b.time) === hour && b.status !== 'cancelled' && b.status !== 'no_show');
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

function buildActionQueue(filter, range = { start: today, end: today }) {
  const rows = [];
  const rangeBookings = filterBookingsByService(filter).filter(b => b.date >= range.start && b.date <= range.end);

  rangeBookings.forEach(b => {
    if (b.status === 'pending') {
      rows.push({
        date: b.date, time: b.time, typeIcon: 'list-view.png', type: 'Service Due',
        detail: `${b.petName} (${b.customerName}) — ${bookingServiceLabel(b)}`,
        statusKey: 'pending', statusLabel: 'Needs Action',
        sla: slaBadge('pendingService', b.time),
        actionLabel: 'Mark Done', actionOnclick: `markBookingDone('${b.id}')`,
        rowOnclick: `openQueueItemDetail('booking','${b.id}')`
      });
    } else if (b.status === 'scheduled') {
      rows.push({
        date: b.date, time: b.time, typeIcon: 'confirm-circle.png', type: 'Booking Confirmation',
        detail: `${b.petName} (${b.customerName}) — ${bookingServiceLabel(b)}`,
        statusKey: 'scheduled', statusLabel: 'Awaiting Confirmation',
        sla: '',
        actionLabel: 'Confirm Arrival', actionOnclick: `confirmBooking('${b.id}')`,
        rowOnclick: `openQueueItemDetail('booking','${b.id}')`
      });
    }
  });

  if (filter !== 'grooming') {
    filterBookingsByService(filter).forEach(b => {
      if (b.checkInDate >= range.start && b.checkInDate <= range.end && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled') {
        rows.push({
          date: b.checkInDate, time: b.time, typeIcon: 'login.png', type: 'Check-In Due',
          detail: `${b.petName} (${b.customerName}) — ${bookingRoomLabel(b)}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-In',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openQueueItemDetail('booking','${b.id}')`
        });
      }
      if (b.checkOutDate >= range.start && b.checkOutDate <= range.end && b.status !== 'no_show' && b.status !== 'cancelled') {
        rows.push({
          date: b.checkOutDate, time: b.time, typeIcon: 'logout.png', type: 'Check-Out Due',
          detail: `${b.petName} (${b.customerName}) — ${bookingRoomLabel(b)}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-Out',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openQueueItemDetail('booking','${b.id}')`
        });
      }
    });
  }

  // enquiries/loyaltyRequests are the mock arrays (no date field — see the
  // dailyoverview section header), so they stay unfiltered by range, same
  // as before this feature. Sorted alongside range rows using today's date.
  enquiries
    .filter(e => (filter === 'all' || e.relatedService === filter) && e.status === 'pending')
    .forEach(e => {
      rows.push({
        date: today, time: e.receivedAt, typeIcon: 'chat-message.png', type: 'Enquiry',
        detail: `${e.customerName} · ${e.channel} — "${e.message}"`,
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
        date: today, time: r.requestedAt, typeIcon: 'loyalty-reward-gift.png', type: 'Loyalty Redemption',
        detail: `${r.customerName} — ${r.type} (${r.points} pts)`,
        statusKey: 'pending', statusLabel: 'Needs Approval',
        sla: slaBadge('loyalty', r.requestedAt),
        actionLabel: 'Approve', actionOnclick: `approveLoyalty('${r.id}')`,
        rowOnclick: `openQueueItemDetail('loyalty','${r.id}')`
      });
    });

  return rows.sort((a, b) => a.date === b.date ? a.time.localeCompare(b.time) : a.date.localeCompare(b.date));
}

function renderActionQueue(filter, range = dailyOverviewRange, period = dailyOverviewPeriod) {
  const rows = buildActionQueue(filter, range);
  q('queueCount').textContent = `${rows.length} item${rows.length === 1 ? '' : 's'}`;

  const tbody = q('actionQueueBody');

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="queue-empty">Nothing pending right now — all caught up!</td></tr>`;
    return;
  }

  // Daily view is a single day, so just the time is unambiguous; Weekly/
  // Monthly span multiple days, so the date is shown alongside it.
  const showDate = period !== 'daily';

  tbody.innerHTML = rows.map(row => `
    <tr onclick="${row.rowOnclick}">
      <td>${showDate ? `${formatShortDate(row.date)} ` : ''}${row.time}</td>
      <td><span class="queue-type-cell"><img src="icon/${row.typeIcon}" alt="" class="row-icon">${row.type}</span></td>
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
    no_show:   { tag: 'no_show',   tagLabel: 'No Show' },
    cancelled: { tag: 'cancelled', tagLabel: 'Cancelled' }
  }[b.status];

  const roomLabel = bookingRoomLabel(b);
  return renderDetailRow({
    title: `${b.petName} (${b.customerName})`,
    sub: `${bookingServiceLabel(b)} · ${b.date} ${b.time}${roomLabel ? ' · ' + roomLabel : ''}`,
    ...statusMeta
  });
}

function enquiryDetailRow(e) {
  const priority = computeEnquiryPriority(e);
  return renderDetailRow({
    title: `${e.customerName} · ${e.channel}`,
    sub: `"${e.message}" — received ${e.receivedAt}${priority !== 'normal' ? ` · ${enquiryPriorityLabel(priority)} priority` : ''}`,
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
  const range = dailyOverviewRange;
  const inRange = d => d >= range.start && d <= range.end;
  const word = periodWord(dailyOverviewPeriod).toLowerCase();
  const rangeBookings = filterBookingsByService(filter).filter(b => inRange(b.date));
  const cta = CARD_CTA[cardKey];

  if (cardKey === 'pendingGrooming') {
    const items = bookings.filter(b => b.serviceType === 'grooming' && inRange(b.date) && b.status === 'pending');
    openDetailModal('Pending Grooming', `${items.length} booking(s) need grooming service ${word}. SLA: start within 15 minutes.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingConfirmation') {
    const items = rangeBookings.filter(b => b.status === 'scheduled');
    openDetailModal('Pending Booking Confirmation', `${items.length} booking(s) scheduled ${word}, awaiting confirmation.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingEnquiries') {
    const items = enquiries.filter(e => (filter === 'all' || e.relatedService === filter) && e.status === 'pending');
    openDetailModal('Pending Enquiries', `${items.length} enquiries awaiting a reply. SLA: reply within 3 hours.`, items.map(enquiryDetailRow).join(''), cta);
  } else if (cardKey === 'pendingLoyalty') {
    const items = loyaltyRequests.filter(r => (filter === 'all' || r.relatedService === filter) && r.status === 'pending');
    openDetailModal('Pending Loyalty Redemption', `${items.length} redemption request(s) awaiting approval. SLA: approve within 2 hours.`, items.map(loyaltyDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckIn') {
    const items = bookings.filter(b => b.serviceType === 'boarding' && inRange(b.checkInDate) && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled');
    openDetailModal('Boarding Check-In Due', `${items.length} arrival(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckOut') {
    const items = bookings.filter(b => b.serviceType === 'boarding' && inRange(b.checkOutDate) && b.status !== 'no_show' && b.status !== 'cancelled');
    openDetailModal('Boarding Check-Out Due', `${items.length} departure(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycareCheckIn') {
    const items = bookings.filter(b => b.serviceType === 'daycare' && inRange(b.date) && (b.status === 'pending' || b.status === 'scheduled'));
    openDetailModal('Daycare Check-In Due', `${items.length} drop-off(s) to confirm.`, items.map(bookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycarePendingPickup') {
    const items = bookings.filter(b => b.serviceType === 'daycare' && inRange(b.date) && b.status === 'done');
    openDetailModal('Daycare Pending Pick-Up', `${items.length} pet(s) waiting for pickup.`, items.map(bookingDetailRow).join(''), cta);
  }
}

function openServiceLoadDetail(type) {
  const range = dailyOverviewRange;
  const items = bookings.filter(b => b.serviceType === type && b.date >= range.start && b.date <= range.end && b.status !== "cancelled" && b.status !== "no_show");
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  openDetailModal(`${label} Bookings`, `${items.length} booking(s) ${periodWord(dailyOverviewPeriod).toLowerCase()}.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openServiceStatusDetail(statusKey) {
  const range = dailyOverviewRange;
  const items = filterBookingsByService(currentFilter).filter(b => b.date >= range.start && b.date <= range.end && b.status === statusKey);
  const labelMap = { pending: 'Pending Service', scheduled: 'Scheduled', done: 'Done', no_show: 'No Show', cancelled: 'Cancelled' };
  openDetailModal(`Bookings — ${labelMap[statusKey]}`, `${items.length} booking(s) ${periodWord(dailyOverviewPeriod).toLowerCase()}.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRoomDetail(roomLabel) {
  const items = bookings.filter(b => b.bookingType === 'boarding' && b.roomLabel === roomLabel).sort((a, b) => a.date.localeCompare(b.date));
  openDetailModal(`${roomLabel} — Bookings`, `${items.length} booking(s) using this room.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openDayDetail(date) {
  const items = filterBookingsByService(currentFilter).filter(b => b.date === date);
  const label = new Date(date + 'T00:00:00').toLocaleDateString('en-MY', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' });
  openDetailModal(label, `${items.length} booking(s) on this day.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openQueueItemDetail(kind, id) {
  if (kind === 'booking') {
    const b = bookings.find(x => x.id === id);
    if (b) openDetailModal('Booking Detail', `${b.petName} — ${bookingServiceLabel(b)}`, bookingDetailRow(b), { label: 'Open Booking Dashboard', href: 'booking.html' });
  } else if (kind === 'enquiry') {
    const e = enquiries.find(x => x.id === id);
    if (e) openDetailModal('Enquiry Detail', `${e.customerName} via ${e.channel}`, enquiryDetailRow(e), { label: 'Open Enquiries', href: 'enquiries.html' });
  } else if (kind === 'loyalty') {
    const r = loyaltyRequests.find(x => x.id === id);
    if (r) openDetailModal('Loyalty Redemption Detail', r.customerName, loyaltyDetailRow(r), { label: 'Open Loyalty', href: 'loyalty.html' });
  }
}

/* =========================
   DAILY OVERVIEW MUTATIONS
========================= */

function refreshCurrentDashboardView() {
  persistBookings();
  persistEnquiries();
  persistLoyaltyRequests();
  if (document.getElementById('actionCards')) renderDailyOverview();
  else if (document.getElementById('kpiHeroGrid')) renderAnalyticsDashboard();
}

async function markBookingDone(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) {
    if (bookingsAreReal) {
      if (!(await updateBookingStatus(booking, 'done'))) return;
    } else {
      booking.status = 'done';
    }
  }
  closeDetailModal();
  refreshCurrentDashboardView();
}

async function confirmBooking(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) {
    if (bookingsAreReal) {
      if (!(await updateBookingStatus(booking, 'pending'))) return;
    } else {
      booking.status = 'pending';
    }
  }
  closeDetailModal();
  refreshCurrentDashboardView();
}

function resolveEnquiry(id) {
  const enquiry = enquiries.find(e => e.id === id);
  if (enquiry) enquiry.status = 'resolved';
  closeDetailModal();
  refreshCurrentDashboardView();
}

function approveLoyalty(id) {
  const request = loyaltyRequests.find(r => r.id === id);
  if (request) request.status = 'approved';
  closeDetailModal();
  refreshCurrentDashboardView();
}

/* =========================
   DAILY OVERVIEW INIT
========================= */

function renderDailyOverview() {
  const range = dailyOverviewRange;
  const period = dailyOverviewPeriod;
  q('actionCards').innerHTML = buildActionCards(currentFilter, range, period).map(renderActionCard).join('');
  renderServiceLoadChart(range, period);
  renderRoomStatus(currentFilter, range, period);
  renderWeeklySchedule(currentFilter, range);
  renderStatusDonut(currentFilter, range);
  renderActionQueue(currentFilter, range, period);
}

function setupDailyOverview() {
  document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentFilter = btn.dataset.filter;
      renderDailyOverview();
    });
  });

  createDateRangeFilter('dailyOverview', (range, period) => {
    dailyOverviewRange = range;
    dailyOverviewPeriod = period;
    renderDailyOverview();
  }).init();

  q('detailModal').addEventListener('click', event => {
    if (event.target.id === 'detailModal') closeDetailModal();
  });
}

/* ==========================================================================
   CRM (profile.html) — derived from the shared `bookings` dataset so
   customer/pet records always match what's actually booked.
   ========================================================================== */

function buildCrmCustomers() {
  const list = [];
  const seen = new Set();

  bookings.forEach(b => {
    if (seen.has(b.customerName)) return;
    seen.add(b.customerName);

    const n = list.length + 1;
    list.push({
      customer_id: `CUST-${String(n).padStart(4, "0")}`,
      loyalty_id: `LOY-${String(n).padStart(4, "0")}`,
      full_name: b.customerName,
      phone: `+60 12-345 ${String(6700 + n).padStart(4, "0")}`,
      address: "",
      photo_icon: n % 2 === 0 ? "👩" : "👨",
      notes: ""
    });
  });

  return list;
}

const PET_SPECIES = {
  Milo: "Dog", Coco: "Dog", Luna: "Cat", Buddy: "Dog", Simba: "Cat", Snowy: "Cat",
  Rocky: "Dog", Bella: "Dog", Max: "Dog", Cleo: "Cat", Tommy: "Dog", Nala: "Cat",
  Oreo: "Cat", Chichi: "Dog", Leo: "Cat", Mochi: "Dog"
};

const SPECIES_DEFAULTS = {
  Dog: { breed: "Mixed Breed", weight: "8kg", colour: "Brown" },
  Cat: { breed: "Domestic Shorthair", weight: "4kg", colour: "Grey" }
};

function buildCrmPets(customerList) {
  return bookings.map((b, i) => {
    const customer = customerList.find(c => c.full_name === b.customerName);
    const species = PET_SPECIES[b.petName] || "Dog";
    const defaults = SPECIES_DEFAULTS[species];

    return {
      pet_id: `PET-${String(i + 1).padStart(4, "0")}`,
      customer_id: customer.customer_id,
      pet_name: b.petName,
      species,
      gender: i % 2 === 0 ? "Male" : "Female",
      birthdate: "",
      breed: defaults.breed,
      weight: defaults.weight,
      colour: defaults.colour,
      service_preference: b.serviceType.charAt(0).toUpperCase() + b.serviceType.slice(1),
      special_care_note: b.specialNote || "",
      additional_note: ""
    };
  });
}

function buildCrmBookings(customerList, petList) {
  return bookings.map((b, i) => {
    const customer = customerList.find(c => c.full_name === b.customerName);
    const pet = petList.find(p => p.pet_name === b.petName && p.customer_id === customer.customer_id);

    return {
      booking_id: `BOOK-${String(i + 1).padStart(4, "0")}`,
      customer_id: customer.customer_id,
      pet_id: pet.pet_id,
      service_type: b.serviceType.charAt(0).toUpperCase() + b.serviceType.slice(1),
      booking_date: b.date
    };
  });
}

function customersStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_customers_" + (account?.businessKey || "default");
}
function loadCustomers(seedCustomers) {
  try {
    const raw = localStorage.getItem(customersStorageKey());
    return raw ? JSON.parse(raw) : seedCustomers;
  } catch (e) {
    return seedCustomers;
  }
}
// Set true once loadRealCrmData() overwrites `customers`/`pets` with real
// Supabase rows, so persistCustomers()/persistPets() never write real-shaped
// records into the mock localStorage key (still read by dashboard.html's
// System panel and loyalty.html's member list, neither converted yet).
let crmDataIsReal = false;

function persistCustomers() {
  if (crmDataIsReal) return;
  localStorage.setItem(customersStorageKey(), JSON.stringify(customers));
}

function petsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_pets_" + (account?.businessKey || "default");
}
function loadPets(seedPets) {
  try {
    const raw = localStorage.getItem(petsStorageKey());
    return raw ? JSON.parse(raw) : seedPets;
  } catch (e) {
    return seedPets;
  }
}
function persistPets() {
  if (crmDataIsReal) return;
  localStorage.setItem(petsStorageKey(), JSON.stringify(pets));
}

let customers = loadCustomers(buildCrmCustomers());
let pets = loadPets(buildCrmPets(customers));
let crmBookings = buildCrmBookings(customers, pets);

/* =========================
   REAL CRM DATA (profile.html only)
   Overwrites the mock `customers`/`pets` globals above — same pattern as
   loadRealBookingData(): only runs from profile.html's own init path, every
   other still-mock page (dashboard.html's System panel, loyalty.html's
   member list) keeps reading pristine mock data on its own page load.

   Your real `pet` table stores date_of_birth/vaccination_expired_date as
   DD/MM/YYYY text (not a real Postgres `date` column, unlike the booking
   tables) — parseDDMMYYYY/formatDDMMYYYY convert to/from the ISO format
   <input type="date"> requires.
========================= */

let loyaltyMemberByCustomerId = new Map();

function parseDDMMYYYY(str) {
  const parts = String(str || "").split("/");
  if (parts.length !== 3) return "";
  const [d, m, y] = parts;
  return `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
}

function formatDDMMYYYY(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return "";
  const [y, m, d] = parts;
  return `${d}/${m}/${y}`;
}

async function loadRealCrmData() {
  const [customersRes, petsRes, membersRes] = await Promise.all([
    api.listCustomers({ limit: 1000 }),
    api.listPets({ limit: 1000 }),
    api.listMembers({ limit: 1000 }),
  ]);
  // Also load real bookings — needed for "Last Booking Made"/"Total
  // Bookings" per customer and the "New Customers" heuristic below.
  await loadRealBookingData();

  loyaltyMemberByCustomerId = new Map(membersRes.map(m => [m.customer_id, m]));
  customers = customersRes;
  pets = petsRes;
  crmDataIsReal = true;
}

function petIdsForCustomer(customerId) {
  return new Set(pets.filter(p => p.customer_id === customerId).map(p => p.pet_id));
}

function firstBookingCreatedDate(customerId) {
  const petIds = petIdsForCustomer(customerId);
  const dates = bookings.filter(b => petIds.has(b.petId) && b.createdDate).map(b => b.createdDate);
  return dates.length ? dates.sort()[0] : null;
}

const profileTypeFilter = document.getElementById("profileTypeFilter");
const searchInput = document.getElementById("searchInput");

const customerSection = document.getElementById("customerSection");
const petSection = document.getElementById("petSection");

const customerTableBody = document.getElementById("customerTableBody");
const petTableBody = document.getElementById("petTableBody");

const customerRecordCount = document.getElementById("customerRecordCount");
const petRecordCount = document.getElementById("petRecordCount");

const detailPage = document.getElementById("detailPage");
const detailTitle = document.getElementById("detailTitle");
const detailForm = document.getElementById("detailForm");

async function initCRM() {
  await loadRealCrmData();

  updateKPI();
  renderLists();

  profileTypeFilter.addEventListener("change", renderLists);
  searchInput.addEventListener("input", renderLists);
}

function updateKPI() {
  document.getElementById("totalCustomers").textContent = customers.length;
  document.getElementById("totalPets").textContent = pets.length;

  // "New" = first booking created within the last 30 days — your real
  // `customer` table has no created_at column to measure this from
  // directly, but every booking row does (created_date).
  const thirtyDaysAgo = addDays(today, -30);
  document.getElementById("newCustomers").textContent =
    customers.filter(c => {
      const firstDate = firstBookingCreatedDate(c.customer_id);
      return firstDate && firstDate >= thirtyDaysAgo;
    }).length;

  document.getElementById("attentionNeeded").textContent =
    pets.filter(pet => pet.vaccination_status === "Not Vaccinated" ||
      (pet.health_notes && pet.health_notes.trim() !== "" && pet.health_notes.trim() !== "-")).length;
}

function renderLists() {
  const selectedType = profileTypeFilter.value;
  const searchValue = searchInput.value.toLowerCase().trim();

  const filteredCustomers = filterCustomers(searchValue);
  const filteredPets = filterPets(searchValue);

  customerSection.classList.toggle("hidden", selectedType === "pet");
  petSection.classList.toggle("hidden", selectedType === "customer");

  renderCustomerTable(filteredCustomers);
  renderPetTable(filteredPets);
}

function filterCustomers(searchValue) {
  return customers.filter(customer => {
    const linkedPets = getPetsByCustomerId(customer.customer_id);
    const linkedPetText = linkedPets
      .map(pet => `${pet.pet_name} ${pet.pet_id}`)
      .join(" ")
      .toLowerCase();

    return (
      customer.full_name.toLowerCase().includes(searchValue) ||
      (customer.phone_number || "").toLowerCase().includes(searchValue) ||
      String(customer.customer_id).toLowerCase().includes(searchValue) ||
      linkedPetText.includes(searchValue)
    );
  });
}

function filterPets(searchValue) {
  return pets.filter(pet => {
    const owner = getCustomerById(pet.customer_id);

    return (
      pet.pet_name.toLowerCase().includes(searchValue) ||
      String(pet.pet_id).toLowerCase().includes(searchValue) ||
      String(pet.customer_id).toLowerCase().includes(searchValue) ||
      (pet.pet_type || "").toLowerCase().includes(searchValue) ||
      (pet.breed || "").toLowerCase().includes(searchValue) ||
      (pet.health_notes || "").toLowerCase().includes(searchValue) ||
      (owner?.full_name || "").toLowerCase().includes(searchValue) ||
      (owner?.phone_number || "").toLowerCase().includes(searchValue)
    );
  });
}

function renderCustomerTable(data) {
  customerTableBody.innerHTML = "";
  customerRecordCount.textContent = `${data.length} records`;

  if (data.length === 0) {
    customerTableBody.innerHTML = `
      <tr>
        <td colspan="7" class="empty-row">No customer record found.</td>
      </tr>
    `;
    return;
  }

  data.forEach(customer => {
    const linkedPets = getPetsByCustomerId(customer.customer_id);
    const lastBooking = getLastBookingByCustomerId(customer.customer_id);
    const member = loyaltyMemberByCustomerId.get(customer.customer_id);
    const loyaltyLabel = member ? `${member.tier} · ${member.points_balance} pts` : "Not a loyalty member";

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">👤 ${customer.full_name}</span>
        <span class="profile-sub">${loyaltyLabel}</span>
      </td>

      <td>
        <span class="key-chip">PK: ${customer.customer_id}</span>
      </td>

      <td>${customer.phone_number || "—"}</td>

      <td>
        ${
          lastBooking
            ? `<span class="key-chip">${formatDate(lastBooking.date)}</span>`
            : `<span class="profile-sub">No booking yet</span>`
        }
      </td>

      <td>
        ${
          linkedPets.length === 0
            ? `<span class="profile-sub">No pet linked</span>`
            : linkedPets.map(pet => `
                <span class="key-chip">${pet.pet_name} · ${pet.pet_id}</span>
              `).join("")
        }
      </td>

      <td>${getBookingsByCustomerId(customer.customer_id).length}</td>

      <td>
        <button class="action-btn" onclick="openCustomerForm('${customer.customer_id}')"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    `;

    customerTableBody.appendChild(row);
  });
}

function renderPetTable(data) {
  petTableBody.innerHTML = "";
  petRecordCount.textContent = `${data.length} records`;

  if (data.length === 0) {
    petTableBody.innerHTML = `
      <tr>
        <td colspan="7" class="empty-row">No pet record found.</td>
      </tr>
    `;
    return;
  }

  data.forEach(pet => {
    const owner = getCustomerById(pet.customer_id);
    const vaccinated = pet.vaccination_status === "Vaccinated";

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">${getPetIcon(pet.pet_type)} ${pet.pet_name}</span>
        <span class="profile-sub">${pet.pet_type} · ${pet.breed} · ${pet.height_cm}cm (${pet.size})</span>
      </td>

      <td>
        <span class="key-chip">PK: ${pet.pet_id}</span>
      </td>

      <td>
        <span class="key-chip">FK: ${pet.customer_id}</span>
      </td>

      <td>
        ${owner?.full_name || "—"}
        <span class="profile-sub">${owner?.phone_number || "—"}</span>
      </td>

      <td>
        <span class="badge ${vaccinated ? "green" : "red"}">${pet.vaccination_status}${pet.vaccination_expired_date ? " · exp " + pet.vaccination_expired_date : ""}</span>
      </td>

      <td>
        ${
          pet.health_notes && pet.health_notes !== "-"
            ? `<span class="key-chip">${pet.health_notes}</span>`
            : `<span class="profile-sub">No special care note</span>`
        }
      </td>

      <td>
        <button class="action-btn" onclick="openPetForm('${pet.pet_id}')"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    `;

    petTableBody.appendChild(row);
  });
}

function openCustomerForm(customerId = null) {
  const isEdit = Boolean(customerId);
  const customer = isEdit
    ? getCustomerById(customerId)
    : { customer_id: null, full_name: "", phone_number: "", address: "" };

  const linkedPets = isEdit ? getPetsByCustomerId(customer.customer_id) : [];
  const member = isEdit ? loyaltyMemberByCustomerId.get(customer.customer_id) : null;

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit
    ? `Customer Info · ${customer.customer_id}`
    : "New Customer";

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Customer ID</label>
      <input name="customer_id" value="${isEdit ? customer.customer_id : "Assigned on save"}" readonly />
    </div>

    <div class="form-group">
      <label>Loyalty Status</label>
      <input value="${member ? `${member.tier} · ${member.points_balance} pts` : "Not a loyalty member"}" readonly />
    </div>

    <div class="form-group">
      <label>Name</label>
      <input name="full_name" value="${customer.full_name}" placeholder="Enter customer name" required />
    </div>

    <div class="form-group">
      <label>Mobile Number</label>
      <input name="phone_number" value="${customer.phone_number || ""}" placeholder="+60..." required />
    </div>

    <div class="form-group full">
      <label>Address</label>
      <input name="address" value="${customer.address || ""}" placeholder="Enter address" />
    </div>

    ${isEdit ? `
    <div class="form-group full">
      <label>Linked Pet Profiles</label>
      <div class="crm-pet-list">
        ${linkedPets.length === 0
          ? `<p class="crm-pet-empty">No pets linked to this customer.</p>`
          : linkedPets.map(pet => `
              <div class="crm-pet-card">
                <div class="crm-pet-info">
                  <span class="profile-name">${getPetIcon(pet.pet_type)} ${pet.pet_name}</span>
                  <span class="profile-sub">${pet.pet_type} · ${pet.breed} · ${pet.height_cm}cm (${pet.size})</span>
                  <span class="profile-sub">Vaccination: ${pet.vaccination_status}${pet.health_notes && pet.health_notes !== "-" ? ` &nbsp;|&nbsp; Care: ${pet.health_notes}` : ""}</span>
                </div>
                <button type="button" class="action-btn" onclick="openPetForm('${pet.pet_id}')"><img src="icon/view.png" alt="" class="btn-icon">View</button>
              </div>
            `).join("")
        }
      </div>
    </div>
    ` : ""}

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeCustomer('${customer.customer_id}')"><img src="icon/delete.png" alt="" class="btn-icon">Remove Customer</button>` : ""}
      <button type="submit" class="save-btn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">${isEdit ? "Save Customer" : "Create Customer"}</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const payload = {
      full_name: formData.get("full_name"),
      phone_number: formData.get("phone_number"),
      address: formData.get("address"),
    };

    try {
      if (isEdit) {
        const updated = await api.updateCustomer(customer.customer_id, payload);
        const index = customers.findIndex(item => item.customer_id === customer.customer_id);
        customers[index] = updated;
      } else {
        const created = await api.createCustomer(payload);
        customers.push(created);
      }
    } catch (err) {
      alert(err.message);
      return;
    }

    updateKPI();
    renderLists();
    closeDetailPage();
  };
}

function openPetForm(petId = null) {
  const isEdit = Boolean(petId);
  const pet = isEdit
    ? getPetById(petId)
    : {
        pet_id: null,
        customer_id: customers[0]?.customer_id || "",
        pet_name: "",
        pet_type: "Cat",
        gender: "Male",
        date_of_birth: "",
        breed: "",
        height_cm: "",
        size: "M",
        vaccination_status: "Not Vaccinated",
        vaccination_expired_date: "",
        health_notes: "",
        service_notes: ""
      };

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit
    ? `Pet Profile · ${pet.pet_name}`
    : "New Pet";

  detailForm.innerHTML = `
    <div class="form-group full">
      <label>Owner</label>
      <select name="customer_id" required>
        ${customers.map(customer => `
          <option value="${customer.customer_id}" ${String(customer.customer_id) === String(pet.customer_id) ? "selected" : ""}>
            ${customer.full_name} · ${customer.customer_id}
          </option>
        `).join("")}
      </select>
    </div>

    <div class="form-group">
      <label>Pet ID</label>
      <input value="${isEdit ? pet.pet_id : "Assigned on save"}" readonly />
    </div>

    <div class="form-group">
      <label>Pet Name</label>
      <input name="pet_name" value="${pet.pet_name}" placeholder="Enter pet name" required />
    </div>

    <div class="form-group">
      <label>Type</label>
      <select name="pet_type">
        <option value="Cat" ${pet.pet_type === "Cat" ? "selected" : ""}>Cat</option>
        <option value="Dog" ${pet.pet_type === "Dog" ? "selected" : ""}>Dog</option>
      </select>
    </div>

    <div class="form-group">
      <label>Gender</label>
      <select name="gender">
        <option value="Male" ${pet.gender === "Male" ? "selected" : ""}>Male</option>
        <option value="Female" ${pet.gender === "Female" ? "selected" : ""}>Female</option>
      </select>
    </div>

    <div class="form-group">
      <label>Date of Birth</label>
      <input type="date" name="date_of_birth" value="${parseDDMMYYYY(pet.date_of_birth)}" />
    </div>

    <div class="form-group">
      <label>Breed</label>
      <input name="breed" value="${pet.breed}" placeholder="Enter breed" />
    </div>

    <div class="form-group">
      <label>Height (cm)</label>
      <input type="number" name="height_cm" min="0" value="${pet.height_cm}" placeholder="e.g. 40" />
    </div>

    <div class="form-group">
      <label>Size</label>
      <select name="size">
        <option value="S" ${pet.size === "S" ? "selected" : ""}>Small</option>
        <option value="M" ${pet.size === "M" ? "selected" : ""}>Medium</option>
        <option value="L" ${pet.size === "L" ? "selected" : ""}>Large</option>
      </select>
    </div>

    <div class="form-group">
      <label>Vaccination Status</label>
      <select name="vaccination_status">
        <option value="Vaccinated" ${pet.vaccination_status === "Vaccinated" ? "selected" : ""}>Vaccinated</option>
        <option value="Not Vaccinated" ${pet.vaccination_status === "Not Vaccinated" ? "selected" : ""}>Not Vaccinated</option>
      </select>
    </div>

    <div class="form-group">
      <label>Vaccination Expiry</label>
      <input type="date" name="vaccination_expired_date" value="${parseDDMMYYYY(pet.vaccination_expired_date)}" />
    </div>

    <div class="form-group full">
      <label>Special Care Note</label>
      <input name="health_notes" value="${pet.health_notes && pet.health_notes !== "-" ? pet.health_notes : ""}" placeholder="e.g. Mild anxiety during grooming" />
    </div>

    <div class="form-group full">
      <label>Service Note</label>
      <textarea name="service_notes" placeholder="Feeding instruction, room preference, grooming reminders">${pet.service_notes && pet.service_notes !== "-" ? pet.service_notes : ""}</textarea>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removePet('${pet.pet_id}')"><img src="icon/delete.png" alt="" class="btn-icon">Remove Pet</button>` : ""}
      <button type="submit" class="save-btn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">${isEdit ? "Save Pet" : "Create Pet"}</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const payload = {
      customer_id: Number(formData.get("customer_id")),
      pet_name: formData.get("pet_name"),
      pet_type: formData.get("pet_type"),
      gender: formData.get("gender"),
      date_of_birth: formatDDMMYYYY(formData.get("date_of_birth")),
      breed: formData.get("breed"),
      height_cm: Number(formData.get("height_cm")) || 0,
      size: formData.get("size"),
      vaccination_status: formData.get("vaccination_status"),
      vaccination_expired_date: formatDDMMYYYY(formData.get("vaccination_expired_date")),
      health_notes: formData.get("health_notes") || "-",
      service_notes: formData.get("service_notes") || "-",
    };

    try {
      if (isEdit) {
        const updated = await api.updatePet(pet.pet_id, payload);
        const index = pets.findIndex(item => item.pet_id === pet.pet_id);
        pets[index] = updated;
      } else {
        const created = await api.createPet(payload);
        pets.push(created);
      }
    } catch (err) {
      alert(err.message);
      return;
    }

    updateKPI();
    renderLists();
    closeDetailPage();
  };
}

function closeDetailPage() {
  detailPage.style.display = "none";
  detailForm.innerHTML = "";
}

async function removeCustomer(customerId) {
  if (!confirm(`Remove customer ${customerId} and all their linked pets? This cannot be undone.`)) return;

  const linkedPetIds = pets
    .filter(p => String(p.customer_id) === String(customerId))
    .map(p => p.pet_id);

  try {
    for (const petId of linkedPetIds) {
      await api.deletePet(petId);
    }
    await api.deleteCustomer(customerId);
  } catch (err) {
    // Most likely a foreign-key constraint (existing bookings/payments still
    // reference this customer's pet) — that's the database correctly
    // refusing to orphan records, not a bug to work around.
    alert(err.message);
    return;
  }

  linkedPetIds.forEach(petId => {
    const idx = pets.findIndex(p => p.pet_id === petId);
    if (idx !== -1) pets.splice(idx, 1);
  });

  const customerIdx = customers.findIndex(c => String(c.customer_id) === String(customerId));
  if (customerIdx !== -1) customers.splice(customerIdx, 1);

  updateKPI();
  renderLists();
  closeDetailPage();
}

async function removePet(petId) {
  if (!confirm(`Remove pet ${petId}? This cannot be undone.`)) return;

  try {
    await api.deletePet(petId);
  } catch (err) {
    alert(err.message);
    return;
  }

  const idx = pets.findIndex(p => String(p.pet_id) === String(petId));
  if (idx !== -1) pets.splice(idx, 1);

  updateKPI();
  renderLists();
  closeDetailPage();
}

function getCustomerById(customerId) {
  // String() comparison: mock customer_id is "CUST-0001" (string), real is
  // an integer — this matches either, since onclick handlers always pass
  // the id back as a string literal regardless of its original type.
  return customers.find(customer => String(customer.customer_id) === String(customerId));
}

function getPetById(petId) {
  return pets.find(pet => String(pet.pet_id) === String(petId));
}

function getPetsByCustomerId(customerId) {
  return pets.filter(pet => String(pet.customer_id) === String(customerId));
}

function getBookingsByCustomerId(customerId) {
  if (crmDataIsReal) {
    const petIds = petIdsForCustomer(customerId);
    return bookings.filter(b => petIds.has(b.petId));
  }
  return crmBookings.filter(booking => booking.customer_id === customerId);
}

function getLastBookingByCustomerId(customerId) {
  const customerBookings = getBookingsByCustomerId(customerId);

  if (customerBookings.length === 0) {
    return null;
  }

  const dateField = crmDataIsReal ? "date" : "booking_date";
  return customerBookings.sort((a, b) => {
    return new Date(b[dateField]) - new Date(a[dateField]);
  })[0];
}

function getPetIcon(species) {
  return species === "Cat" ? "🐱" : "🐶";
}

function formatDate(dateString) {
  return new Date(dateString).toLocaleDateString("en-MY", {
    day: "2-digit",
    month: "short",
    year: "numeric"
  });
}

/* ==========================================================================
   LOYALTY PROGRAM (loyalty.html)
   Members are derived from the shared CRM customer records (existing
   loyalty_id), plus any approved new-member sign-up requests below. Point
   balances/tiers are deterministic mock values, not tracked transactionally.
   ========================================================================== */

function loyaltyMemberRequestsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_loyalty_member_requests_" + (account?.businessKey || "default");
}
function loadLoyaltyMemberRequests(seedRequests) {
  try {
    const raw = localStorage.getItem(loyaltyMemberRequestsStorageKey());
    return raw ? JSON.parse(raw) : seedRequests;
  } catch (e) {
    return seedRequests;
  }
}
function persistLoyaltyMemberRequests() {
  localStorage.setItem(loyaltyMemberRequestsStorageKey(), JSON.stringify(loyaltyMemberRequests));
}

const SEED_LOYALTY_MEMBER_REQUESTS = [
  { id: 'LOYREG001', customerName: 'Nabila Hassan', phone: '+60 12-345 9901', requestedAt: '09:10', status: 'pending' },
  { id: 'LOYREG002', customerName: 'Kelvin Ooi',     phone: '+60 12-345 9902', requestedAt: '10:35', status: 'pending' }
];

let loyaltyMemberRequests = loadLoyaltyMemberRequests(SEED_LOYALTY_MEMBER_REQUESTS);

const LOYALTY_TIERS = [
  { min: 1200, name: 'Platinum' },
  { min: 700,  name: 'Gold' },
  { min: 300,  name: 'Silver' },
  { min: 0,    name: 'Bronze' }
];

function getLoyaltyTier(points) {
  return LOYALTY_TIERS.find(tier => points >= tier.min).name;
}

function getMemberPoints(customerId) {
  const n = parseInt(customerId.replace('CUST-', ''), 10) || 1;
  return (n * 137 + 220) % 1800 + 50;
}

// Real loyalty data (loyalty.html only) — same pattern as loadRealBookingData()
// etc: `loyaltyDataIsReal` lets buildLoyaltyMembers()/countApprovedRedemptions()
// stay dual-shape-safe, since dashboard.html still calls both against the
// mock arrays (not converted yet) on its own page load.
let loyaltyDataIsReal = false;
let realMembers = [];
let realCoupons = [];
let realRedemptions = [];

async function loadRealLoyaltyData() {
  const [membersRes, customersRes, couponsRes, redemptionsRes, paymentsRes] = await Promise.all([
    api.listMembers({ limit: 1000 }),
    api.listCustomers({ limit: 1000 }),
    api.listCoupons(),
    api.listRedemptions({ limit: 500 }),
    api.listPayments({ limit: 2000 }),
  ]);
  await loadRealBookingData(); // for the member-detail modal's "Linked Bookings"

  const customerById = new Map(customersRes.map(c => [c.customer_id, c]));
  realMembers = membersRes.map(m => ({
    ...m,
    customerName: customerById.get(m.customer_id)?.full_name || "—",
    phone: customerById.get(m.customer_id)?.phone_number || "—",
  }));

  realCoupons = couponsRes;
  const couponById = new Map(realCoupons.map(c => [c.coupon_id, c]));
  const memberByLoyaltyId = new Map(realMembers.map(m => [m.loyalty_id, m]));
  // redemption has no timestamp of its own — it's linked from the payment
  // that created it (payment.redemption_id), so we borrow that payment's
  // date to let the KPI date-filter scope redemption events by period.
  const paymentByRedemptionId = new Map(paymentsRes.filter(p => p.redemption_id).map(p => [p.redemption_id, p]));
  realRedemptions = redemptionsRes.map(r => ({
    ...r,
    memberName: memberByLoyaltyId.get(r.loyalty_id)?.customerName || "—",
    couponName: r.coupon_id ? (couponById.get(r.coupon_id)?.reward_name || "—") : null,
    date: paymentByRedemptionId.get(r.redemption_id)?.date || null,
  }));

  loyaltyDataIsReal = true;
}

function buildLoyaltyMembers() {
  if (loyaltyDataIsReal) {
    return realMembers.map(m => ({
      member_id: m.loyalty_id,
      customer_id: m.customer_id,
      full_name: m.customerName,
      phone: m.phone,
      photo_icon: "👤",
      points: m.points_balance,
      tier: m.tier,
    }));
  }

  const fromCrm = customers.map(c => ({
    member_id: c.loyalty_id,
    full_name: c.full_name,
    phone: c.phone,
    photo_icon: c.photo_icon,
    points: getMemberPoints(c.customer_id)
  }));

  const fromApprovedRegs = loyaltyMemberRequests
    .filter(r => r.status === 'approved')
    .map(r => ({
      member_id: r.id.replace('LOYREG', 'LOY-N'),
      full_name: r.customerName,
      phone: r.phone,
      photo_icon: '👤',
      points: 100
    }));

  return [...fromCrm, ...fromApprovedRegs].map(m => ({ ...m, tier: getLoyaltyTier(m.points) }));
}

function countApprovedRedemptions(fullName) {
  if (loyaltyDataIsReal) {
    const member = realMembers.find(m => m.customerName === fullName);
    return member?.redemption_made || 0;
  }
  return loyaltyRequests.filter(r => r.customerName === fullName && r.status === 'approved').length;
}

const loyaltySearchInput = document.getElementById("loyaltySearchInput");
const loyaltyPendingBody = document.getElementById("loyaltyPendingBody");
const loyaltyMemberBody = document.getElementById("loyaltyMemberBody");
const loyaltyPendingRecordCount = document.getElementById("loyaltyPendingRecordCount");
const loyaltyMemberRecordCount = document.getElementById("loyaltyMemberRecordCount");

// Total Members / Gold Count are current-state snapshots (no signup date in
// the schema to scope them by), so only the redemption-event cards below
// react to the date filter.
let loyaltyMetricsRange = { start: getToday(), end: getToday() };

async function initLoyaltyPage() {
  await loadRealLoyaltyData();

  document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("loyaltyPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("loyaltyMembersPanel").classList.toggle("hidden", btn.dataset.panel !== "members");
      document.getElementById("loyaltyRulesPanel").classList.toggle("hidden", btn.dataset.panel !== "rules");
    });
  });

  loyaltySearchInput.addEventListener("input", renderLoyaltyLists);

  createDateRangeFilter("loyaltyMetrics", (range) => {
    loyaltyMetricsRange = range;
    updateLoyaltyKPI();
  }).init();

  updateLoyaltyKPI();
  renderLoyaltyLists();
  renderLoyaltyRulesPanel();
}

function updateLoyaltyKPI() {
  const members = buildLoyaltyMembers();

  // Your real `redemption` ledger has no pending/approval workflow — rows
  // are only ever written by verify_payment() as a completed transaction.
  // "Total Redemptions" replaces the mock's "Pending Approvals" with a real,
  // non-fabricated count instead, scoped to the selected date range via the
  // linked payment's date (see loadRealLoyaltyData).
  const rangedRedemptions = realRedemptions.filter(r => r.date && r.date >= loyaltyMetricsRange.start && r.date <= loyaltyMetricsRange.end);
  const totalRedemptionEvents = rangedRedemptions.filter(r => r.loyalty_spend > 0).length;
  const pointsRedeemed = rangedRedemptions.reduce((sum, r) => sum + (Number(r.loyalty_spend) || 0), 0);

  document.getElementById("loyaltyTotalMembers").textContent = members.length;
  document.getElementById("loyaltyPendingCount").textContent = totalRedemptionEvents;
  document.getElementById("loyaltyGoldCount").textContent = members.filter(m => m.tier === "Gold" || m.tier === "Platinum").length;
  document.getElementById("loyaltyPointsRedeemed").textContent = pointsRedeemed.toLocaleString();
}

function renderLoyaltyLists() {
  const searchValue = loyaltySearchInput.value.toLowerCase().trim();
  renderLoyaltyHistoryTable(searchValue);
  renderLoyaltyMemberTable(searchValue);
}

function renderLoyaltyHistoryTable(searchValue) {
  const filtered = realRedemptions
    .filter(r => r.memberName.toLowerCase().includes(searchValue) || (r.couponName || "").toLowerCase().includes(searchValue))
    .sort((a, b) => b.redemption_id - a.redemption_id);

  loyaltyPendingRecordCount.textContent = `${filtered.length} records`;

  if (filtered.length === 0) {
    loyaltyPendingBody.innerHTML = `<tr><td colspan="5" class="empty-row">No redemption history found.</td></tr>`;
    return;
  }

  loyaltyPendingBody.innerHTML = filtered.map(r => `
    <tr>
      <td><span class="key-chip">RDM-${String(r.redemption_id).padStart(4, "0")}</span></td>
      <td><span class="profile-name">${r.memberName}</span></td>
      <td>${r.loyalty_earn > 0 ? `+${r.loyalty_earn} pts earned` : "—"}</td>
      <td>${r.loyalty_spend > 0 ? `−${r.loyalty_spend} pts spent` : "—"}</td>
      <td>${r.couponName || "—"}</td>
    </tr>
  `).join("");
}

function renderLoyaltyMemberTable(searchValue) {
  const members = buildLoyaltyMembers().filter(m =>
    m.full_name.toLowerCase().includes(searchValue) ||
    m.phone.toLowerCase().includes(searchValue) ||
    String(m.member_id || "").toLowerCase().includes(searchValue)
  );

  loyaltyMemberRecordCount.textContent = `${members.length} members`;

  if (members.length === 0) {
    loyaltyMemberBody.innerHTML = `<tr><td colspan="6" class="empty-row">No member record found.</td></tr>`;
    return;
  }

  loyaltyMemberBody.innerHTML = members
    .sort((a, b) => b.points - a.points)
    .map(m => `
      <tr>
        <td>
          <span class="profile-name">${m.photo_icon || "👤"} ${m.full_name}</span>
          <span class="profile-sub">${m.phone}</span>
        </td>
        <td><span class="key-chip">${m.member_id}</span></td>
        <td><span class="status-tag status-${m.tier.toLowerCase()}">${m.tier}</span></td>
        <td>${m.points.toLocaleString()} pts</td>
        <td>${countApprovedRedemptions(m.full_name)}</td>
        <td><button class="action-btn" onclick="openLoyaltyMemberDetail('${m.member_id}')"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
      </tr>
    `).join("");
}


function openLoyaltyMemberDetail(memberId) {
  const member = buildLoyaltyMembers().find(m => String(m.member_id) === String(memberId));
  if (!member) return;

  const memberBookings = bookings
    .filter(b => b.customerName === member.full_name)
    .sort((a, b) => new Date(b.date) - new Date(a.date));

  detailPage.style.display = "flex";
  detailTitle.textContent = `Member Info · ${member.member_id}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Loyalty ID</label>
      <input value="${member.member_id}" readonly />
    </div>

    <div class="form-group">
      <label>Name</label>
      <input value="${member.full_name}" readonly />
    </div>

    <div class="form-group">
      <label>Phone</label>
      <input value="${member.phone}" readonly />
    </div>

    <div class="form-group">
      <label>Tier</label>
      <input value="${member.tier}" readonly />
    </div>

    <div class="form-group">
      <label>Points Balance</label>
      <input value="${member.points.toLocaleString()} pts" readonly />
    </div>

    <div class="form-group">
      <label>Redemptions Made</label>
      <input value="${countApprovedRedemptions(member.full_name)}" readonly />
    </div>

    <div class="form-group full">
      <label>Linked Bookings</label>
      <div class="crm-pet-list">
        ${memberBookings.length === 0
          ? `<p class="crm-pet-empty">No bookings linked to this member.</p>`
          : memberBookings.map(b => `
              <div class="crm-pet-card">
                <div class="crm-pet-info">
                  <span class="profile-name">${b.petName} · ${b.serviceType.charAt(0).toUpperCase()}${b.serviceType.slice(1)}</span>
                  <span class="profile-sub">${formatDate(b.date)} · RM ${b.amount}</span>
                </div>
                <span class="status-tag status-${b.status}">${b.status.replace("_", " ")}</span>
              </div>
            `).join("")
        }
      </div>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      <a class="btn btn-secondary" href="booking.html"><img src="icon/calendar-simple.png" alt="" class="btn-icon">Open Booking Dashboard</a>
    </div>
  `;
}

/* ==========================================================================
   LOYALTY PROGRAM RULES (loyalty.html)
   Points-per-RM earn rate and the redemption rules (points -> discount or
   free service) are configurable and persisted per business, so they carry
   over across sessions the same way business settings do.
   ========================================================================== */

const LOYALTY_RULES_DEFAULTS = {
  earnRate: 1,
  redemptionRules: [
    { id: "RULE1", points: 200, reward: "RM10 Voucher", type: "discount", value: 10 },
    { id: "RULE2", points: 400, reward: "RM20 Voucher", type: "discount", value: 20 },
    { id: "RULE3", points: 800, reward: "Free Grooming Session", type: "free", value: 0 },
    { id: "RULE4", points: 1200, reward: "Free Boarding Night", type: "free", value: 0 }
  ]
};

function loyaltyRulesStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_loyalty_rules_" + (account?.businessKey || "default");
}

function loadLoyaltyRules() {
  try {
    const raw = localStorage.getItem(loyaltyRulesStorageKey());
    return raw ? { ...LOYALTY_RULES_DEFAULTS, ...JSON.parse(raw) } : { ...LOYALTY_RULES_DEFAULTS };
  } catch (e) {
    return { ...LOYALTY_RULES_DEFAULTS };
  }
}

function persistLoyaltyRules(rules) {
  localStorage.setItem(loyaltyRulesStorageKey(), JSON.stringify(rules));
}

function renderLoyaltyRulesPanel() {
  document.getElementById("loyaltyRulesBody").innerHTML = realCoupons.map(r => `
    <tr>
      <td>${Number(r.points_required).toLocaleString()} pts</td>
      <td>${r.reward_name}</td>
      <td>${r.reward_type === "Free service" ? "Free Service" : `Discount (RM ${r["discount_value (RM)"]})`}</td>
      <td>
        <button class="edit-btn" onclick="openRuleForm(${r.coupon_id})">Edit</button>
      </td>
    </tr>
  `).join("");
}

async function removeLoyaltyRule(couponId) {
  if (!confirm("Remove this redemption rule? This cannot be undone.")) return;
  try {
    await api.deleteCoupon(couponId);
  } catch (err) {
    alert(err.message);
    return;
  }
  const idx = realCoupons.findIndex(r => r.coupon_id === couponId);
  if (idx !== -1) realCoupons.splice(idx, 1);
  renderLoyaltyRulesPanel();
  closeDetailPage();
}

function openRuleForm(couponId = null) {
  const isEdit = Boolean(couponId);
  const rule = isEdit
    ? realCoupons.find(r => r.coupon_id === couponId)
    : { coupon_id: null, points_required: "", reward_name: "", reward_type: "Discount (RM value)", "discount_value (RM)": "" };
  if (isEdit && !rule) return;

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit ? `Edit Redemption Rule · ${rule.reward_name}` : "Add Redemption Rule";

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Points Required</label>
      <input type="number" name="points_required" min="1" placeholder="e.g. 200" value="${rule.points_required}" required />
    </div>

    <div class="form-group">
      <label>Reward Name</label>
      <input name="reward_name" placeholder="e.g. RM10 Voucher" value="${rule.reward_name}" required />
    </div>

    <div class="form-group">
      <label>Reward Type</label>
      <select name="reward_type" id="ruleTypeSelect" onchange="document.getElementById('ruleValueGroup').classList.toggle('hidden', this.value !== 'Discount (RM value)')">
        <option value="Discount (RM value)" ${rule.reward_type === "Discount (RM value)" ? "selected" : ""}>Discount (RM value)</option>
        <option value="Free service" ${rule.reward_type === "Free service" ? "selected" : ""}>Free Service</option>
      </select>
    </div>

    <div class="form-group" id="ruleValueGroup" ${rule.reward_type === "Free service" ? 'class="hidden"' : ""}>
      <label>Discount Value (RM)</label>
      <input type="number" name="discount_value" min="0" placeholder="e.g. 10" value="${rule["discount_value (RM)"] && rule["discount_value (RM)"] !== "-" ? rule["discount_value (RM)"] : ""}" />
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeLoyaltyRule(${rule.coupon_id})"><img src="icon/delete.png" alt="" class="btn-icon">Remove Rule</button>` : ""}
      <button type="submit" class="save-btn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">${isEdit ? "Save Rule" : "Add Rule"}</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();
    const formData = new FormData(detailForm);
    const rewardType = formData.get("reward_type");

    const payload = {
      points_required: Number(formData.get("points_required")) || 0,
      reward_name: formData.get("reward_name"),
      reward_type: rewardType,
      "discount_value (RM)": rewardType === "Discount (RM value)" ? String(Number(formData.get("discount_value")) || 0) : "-",
    };

    try {
      if (isEdit) {
        const updated = await api.updateCoupon(rule.coupon_id, payload);
        const index = realCoupons.findIndex(r => r.coupon_id === rule.coupon_id);
        realCoupons[index] = updated;
      } else {
        const created = await api.createCoupon(payload);
        realCoupons.push(created);
      }
    } catch (err) {
      alert(err.message);
      return;
    }

    renderLoyaltyRulesPanel();
    closeDetailPage();
  };
}

/* ==========================================================================
   PAYMENT (payment.html)
   Each booking becomes one payment record: final amount = base service price
   + add-ons − loyalty redemption deduction (RM-voucher redemptions deduct
   their RM value; "Free ..." redemptions cover the full base price). Each
   approved loyalty redemption is applied to at most one matching booking.
   ========================================================================== */

const SERVICE_ADDONS = {
  grooming: [
    { id: "AO1", name: "Nail Trimming", price: 15 },
    { id: "AO2", name: "Teeth Brushing", price: 20 },
    { id: "AO3", name: "De-shedding Treatment", price: 30 }
  ],
  boarding: [
    { id: "AO4", name: "Extra Playtime", price: 25 },
    { id: "AO5", name: "Medication Administration", price: 15 }
  ],
  daycare: [
    { id: "AO6", name: "Extra Meal", price: 10 },
    { id: "AO7", name: "Photo Update Package", price: 12 }
  ]
};

const PAYMENT_METHODS = ["Cash", "Card", "QR Pay", "Bank Transfer"];

function getBookingAddons(booking, index) {
  const pool = SERVICE_ADDONS[booking.serviceType] || [];
  return pool.slice(0, index % (pool.length + 1));
}

function parseVoucherRM(type) {
  return parseInt(type.match(/RM(\d+)/)?.[1] || "0", 10);
}

function buildPaymentRecords() {
  const consumedRedemptions = new Set();
  const earnRate = loadLoyaltyRules().earnRate;

  return bookings.map((b, i) => {
    const addons = getBookingAddons(b, i);
    const addonsTotal = addons.reduce((sum, a) => sum + a.price, 0);

    const redemption = loyaltyRequests.find(r =>
      r.status === "approved" &&
      !consumedRedemptions.has(r.id) &&
      r.customerName === b.customerName &&
      r.relatedService === b.serviceType
    );

    let loyaltyDiscount = 0;
    let loyaltyNote = "";
    let loyaltyPointsSpent = 0;
    if (redemption) {
      consumedRedemptions.add(redemption.id);
      loyaltyDiscount = redemption.type.startsWith("Free") ? b.amount : parseVoucherRM(redemption.type);
      loyaltyNote = redemption.type;
      loyaltyPointsSpent = redemption.points;
    }

    const finalAmount = Math.max(0, b.amount + addonsTotal - loyaltyDiscount);

    return {
      payment_id: `PAY-${String(i + 1).padStart(4, "0")}`,
      booking_id: b.id,
      customerName: b.customerName,
      petName: b.petName,
      serviceType: b.serviceType,
      serviceName: services.find(s => s.id === b.serviceId)?.name || b.serviceType,
      date: b.date,
      basePrice: b.amount,
      addons,
      addonsTotal,
      loyaltyDiscount,
      loyaltyNote,
      loyaltyPointsSpent,
      loyaltyPointsEarned: Math.round(finalAmount * earnRate),
      finalAmount,
      method: PAYMENT_METHODS[i % PAYMENT_METHODS.length],
      status: i % 3 === 0 ? "pending" : "verified"
    };
  });
}

function paymentRecordsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_payment_records_" + (account?.businessKey || "default");
}
function loadPaymentRecords(seedRecords) {
  try {
    const raw = localStorage.getItem(paymentRecordsStorageKey());
    return raw ? JSON.parse(raw) : seedRecords;
  } catch (e) {
    return seedRecords;
  }
}
function persistPaymentRecords() {
  localStorage.setItem(paymentRecordsStorageKey(), JSON.stringify(paymentRecords));
}

let paymentRecords = loadPaymentRecords(buildPaymentRecords());

const paymentSearchInput = document.getElementById("paymentSearchInput");
const paymentPendingBody = document.getElementById("paymentPendingBody");
const paymentHistoryBody = document.getElementById("paymentHistoryBody");
const paymentPendingRecordCount = document.getElementById("paymentPendingRecordCount");
const paymentHistoryRecordCount = document.getElementById("paymentHistoryRecordCount");

// ── Real-data payment page (payment.html only — dashboard.html's System
// panel still reads the mock `paymentRecords` array above; that's a
// separate, not-yet-converted page). Requires supabase-config.js +
// api-client.js loaded before this file. ──────────────────────────────────
let _couponsCache = null;
async function getCouponsCached() {
  if (!_couponsCache) _couponsCache = await api.listCoupons();
  return _couponsCache;
}

// Cached full list + the KPI cards' selected date range (tables below stay
// unfiltered/full — only the 4 KPI cards react to this, per the date-filter
// feature request). Defaults to today.
let paymentRowsCache = [];
let paymentMetricsRange = { start: getToday(), end: getToday() };

async function initPaymentPage() {
  document.querySelectorAll("#paymentTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#paymentTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("paymentPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("paymentHistoryPanel").classList.toggle("hidden", btn.dataset.panel !== "history");
    });
  });

  paymentSearchInput.addEventListener("input", renderPaymentLists);

  createDateRangeFilter("paymentMetrics", (range) => {
    paymentMetricsRange = range;
    updatePaymentKPI();
  }).init();

  await renderPaymentLists();
}

async function renderPaymentLists() {
  const searchValue = paymentSearchInput.value.toLowerCase().trim();

  // One bulk-enriched call (petName/customerName already joined server-side
  // via findNamesForPayments) instead of a per-row detail fetch — real data
  // is 500+ rows, so an N+1 pattern here would mean hundreds of round trips.
  paymentRowsCache = await api.listPayments({ limit: 1000 });

  const pendingRows = paymentRowsCache.filter(p => p.status === "Pending");
  // Real payment.status has 4 values (Paid/Unpaid/Pending/Refunded); history
  // shows everything that isn't awaiting verification.
  const historyRows = paymentRowsCache.filter(p => p.status !== "Pending");

  updatePaymentKPI();
  renderPaymentPendingTable(pendingRows, searchValue);
  renderPaymentHistoryTable(historyRows, searchValue);
}

function updatePaymentKPI() {
  const rows = paymentRowsCache.filter(p => p.date >= paymentMetricsRange.start && p.date <= paymentMetricsRange.end);
  const paid = rows.filter(p => p.status === "Paid");
  const pendingCount = rows.filter(p => p.status === "Pending").length;

  const totalRevenue = paid.reduce((sum, p) => sum + Number(p.final_amount || 0), 0);
  const loyaltyDiscountTotal = paid.reduce((sum, p) => sum + Math.max(0, Number(p.base_price || 0) - Number(p.final_amount || 0)), 0);

  document.getElementById("paymentTotalRevenue").textContent = `RM ${totalRevenue.toLocaleString()}`;
  document.getElementById("paymentPendingCount").textContent = pendingCount;
  document.getElementById("paymentTotalCount").textContent = rows.length;
  document.getElementById("paymentLoyaltyDiscount").textContent = `RM ${loyaltyDiscountTotal.toLocaleString()}`;
}

function matchesSearch(row, searchValue) {
  if (!searchValue) return true;
  const haystack = `${row.customerName || ""} ${row.petName || ""} ${row.payment_id}`.toLowerCase();
  return haystack.includes(searchValue);
}

const PAYMENT_STATUS_TAG = {
  Paid: "status-done",
  Pending: "status-pending",
  Unpaid: "status-no_show",
  Refunded: "status-cancelled",
};

function renderPaymentPendingTable(rows, searchValue) {
  const filtered = rows.filter(r => matchesSearch(r, searchValue));

  paymentPendingRecordCount.textContent = `${filtered.length} pending`;

  if (filtered.length === 0) {
    paymentPendingBody.innerHTML = `<tr><td colspan="7" class="empty-row">No payments awaiting verification.</td></tr>`;
    return;
  }

  paymentPendingBody.innerHTML = filtered.map(payment => `
    <tr>
      <td><span class="key-chip">PAY-${String(payment.payment_id).padStart(4, "0")}</span></td>
      <td>
        <span class="profile-name">${payment.customerName || "—"}</span>
        <span class="profile-sub">${payment.petName || "—"}</span>
      </td>
      <td>${payment.service}</td>
      <td>RM ${Number(payment.final_amount).toLocaleString()}</td>
      <td>${payment.payment_method || "—"}</td>
      <td>${formatDate(payment.date)}</td>
      <td>
        <button class="action-btn" onclick="openPaymentDetail(${payment.payment_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    </tr>
  `).join("");
}

function renderPaymentHistoryTable(rows, searchValue) {
  const filtered = rows
    .filter(r => matchesSearch(r, searchValue))
    .slice()
    .sort((a, b) => new Date(b.date) - new Date(a.date));

  paymentHistoryRecordCount.textContent = `${filtered.length} records`;

  if (filtered.length === 0) {
    paymentHistoryBody.innerHTML = `<tr><td colspan="13" class="empty-row">No transaction record found.</td></tr>`;
    return;
  }

  paymentHistoryBody.innerHTML = filtered.map(payment => {
    const discount = Math.max(0, Number(payment.base_price || 0) - Number(payment.final_amount || 0));
    const statusClass = PAYMENT_STATUS_TAG[payment.status] || "status-pending";
    return `
    <tr>
      <td><span class="key-chip">PAY-${String(payment.payment_id).padStart(4, "0")}</span></td>
      <td>
        <span class="profile-name">${payment.customerName || "—"}</span>
        <span class="profile-sub">${payment.petName || "—"}</span>
      </td>
      <td>${payment.service}</td>
      <td>RM ${Number(payment.base_price).toLocaleString()}</td>
      <td>${payment.add_ons || "—"}</td>
      <td>${discount > 0 ? `− RM ${discount.toLocaleString()}` : "—"}</td>
      <td><strong>RM ${Number(payment.final_amount).toLocaleString()}</strong></td>
      <td>—</td>
      <td>—</td>
      <td>${payment.payment_method || "—"}</td>
      <td><span class="status-tag ${statusClass}">${payment.status}</span></td>
      <td>${formatDate(payment.date)}</td>
      <td><button class="action-btn" onclick="openPaymentDetail(${payment.payment_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
    </tr>
  `;
  }).join("");
}

async function verifyPayment(paymentId, couponId) {
  try {
    await api.verifyPayment(paymentId, { couponId });
  } catch (err) {
    alert(err.message);
    return;
  }
  closeDetailPage();
  await renderPaymentLists();
}

async function openPaymentDetail(paymentId) {
  const { payment, pet, customer, member } = await api.getPaymentDetail(paymentId);
  const coupons = payment.status === "Pending" ? await getCouponsCached() : [];

  detailPage.style.display = "flex";
  detailTitle.textContent = `Payment Detail · PAY-${String(payment.payment_id).padStart(4, "0")}`;

  const memberLine = member
    ? `${member.tier} member · ${member.points_balance.toLocaleString()} points`
    : "No loyalty member on file";

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Payment ID</label>
      <input value="PAY-${String(payment.payment_id).padStart(4, "0")}" readonly />
    </div>

    <div class="form-group">
      <label>Customer</label>
      <input value="${customer?.full_name || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Pet</label>
      <input value="${pet?.pet_name || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Service</label>
      <input value="${payment.service}" readonly />
    </div>

    <div class="form-group">
      <label>Base Price</label>
      <input value="RM ${Number(payment.base_price).toLocaleString()}" readonly />
    </div>

    <div class="form-group">
      <label>Add-ons</label>
      <input value="${payment.add_ons || "None"}" readonly />
    </div>

    <div class="form-group">
      <label>Final Amount</label>
      <input id="verifyCurrentTotal" value="RM ${Number(payment.final_amount).toLocaleString()}" readonly />
    </div>

    <div class="form-group full">
      <label>Loyalty Member</label>
      <input value="${memberLine}" readonly />
    </div>

    <div class="form-group">
      <label>Payment Method</label>
      <input value="${payment.payment_method || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Date</label>
      <input value="${formatDate(payment.date)}" readonly />
    </div>

    <div class="form-group">
      <label>Status</label>
      <input value="${payment.status}" readonly />
    </div>

    ${payment.status === "Pending" ? `
    <div class="form-group full">
      <label>Apply Voucher (optional)</label>
      <select id="verifyCouponSelect">
        <option value="">No voucher</option>
        ${coupons.map(c => `<option value="${c.coupon_id}">${c.reward_name} (${c.points_required} pts)</option>`).join("")}
      </select>
    </div>
    <div class="form-group full">
      <p id="verifyVoucherNote" style="font-size:0.8rem;color:var(--text-muted);"></p>
    </div>
    ` : ""}

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      ${payment.status === "Pending" ? `<button type="button" class="save-btn" id="verifyConfirmBtn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">Verify</button>` : ""}
    </div>
  `;

  if (payment.status !== "Pending") return;

  const couponSelect = document.getElementById("verifyCouponSelect");
  const note = document.getElementById("verifyVoucherNote");

  couponSelect.addEventListener("change", async () => {
    if (!couponSelect.value) { note.textContent = ""; return; }
    try {
      const quote = await api.quoteVoucher(payment.payment_id, Number(couponSelect.value));
      note.style.color = "";
      note.textContent = `New total: RM ${quote.finalAmount} (discount RM ${quote.discountApplied})`;
    } catch (err) {
      note.style.color = "#DC2626";
      note.textContent = err.message;
    }
  });

  document.getElementById("verifyConfirmBtn").addEventListener("click", () => {
    verifyPayment(payment.payment_id, couponSelect.value ? Number(couponSelect.value) : undefined);
  });
}

/* ==========================================================================
   ENQUIRIES (enquiries.html)
   Pending enquiries are tracked against a 3-hour reply SLA; once an
   enquiry is resolved the SLA no longer applies (nothing left to breach).
   HITL rate = share of RESOLVED enquiries that needed a human reply rather
   than being fully handled by the AI assistant.
   ========================================================================== */

function getWhatsAppLink(phone) {
  return `https://wa.me/${(phone || "").replace(/\D/g, "")}`;
}

const enquirySearchInput = document.getElementById("enquirySearchInput");
const enquiryPendingBody = document.getElementById("enquiryPendingBody");
const enquiryPendingRecordCount = document.getElementById("enquiryPendingRecordCount");

// Real data (enquiries.html only). Your real `messages` table is a plain
// inbound-message log (sender_type/sender_id/message_text/intent_label/
// receive_date/receive_time) — no status, handled-by, channel, or reply
// tracking at all, so the mock's Pending/Resolved/SLA/AI-vs-human workflow
// has no real backing. This page is rebuilt as a read-only message log
// filterable by intent_label (booking/policy/loyalty) instead.
let realMessages = [];
let currentEnquiryFilter = "all";

async function loadRealEnquiryData() {
  const [messagesRes, customersRes] = await Promise.all([
    api.listChatMessages({ limit: 1000 }),
    api.listCustomers({ limit: 1000 }),
  ]);
  const customerById = new Map(customersRes.map(c => [c.customer_id, c]));
  realMessages = messagesRes.map(m => ({
    ...m,
    customerName: customerById.get(m.sender_id)?.full_name || "—",
    phone: customerById.get(m.sender_id)?.phone_number || "",
  }));
}

let enquiryMetricsRange = { start: getToday(), end: getToday() };

async function initEnquiriesPage() {
  await loadRealEnquiryData();

  document.querySelectorAll("#enquiryTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#enquiryTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentEnquiryFilter = btn.dataset.filter;
      renderEnquiryLists();
    });
  });

  enquirySearchInput.addEventListener("input", renderEnquiryLists);

  createDateRangeFilter("enquiryMetrics", (range) => {
    enquiryMetricsRange = range;
    updateEnquiryKPI();
  }).init();

  updateEnquiryKPI();
  renderEnquiryLists();
}

function updateEnquiryKPI() {
  const rows = realMessages.filter(m => m.receive_date >= enquiryMetricsRange.start && m.receive_date <= enquiryMetricsRange.end);
  document.getElementById("enquiryTotalCount").textContent = rows.length;
  document.getElementById("enquiryBookingCount").textContent = rows.filter(m => m.intent_label === "booking").length;
  document.getElementById("enquiryPolicyCount").textContent = rows.filter(m => m.intent_label === "policy").length;
  document.getElementById("enquiryLoyaltyCount").textContent = rows.filter(m => m.intent_label === "loyalty").length;
}

function renderEnquiryLists() {
  const searchValue = enquirySearchInput.value.toLowerCase().trim();

  const filtered = realMessages
    .filter(m => currentEnquiryFilter === "all" || m.intent_label === currentEnquiryFilter)
    .filter(m =>
      m.customerName.toLowerCase().includes(searchValue) ||
      (m.phone || "").toLowerCase().includes(searchValue) ||
      m.message_text.toLowerCase().includes(searchValue)
    )
    .sort((a, b) => `${b.receive_date} ${b.receive_time}`.localeCompare(`${a.receive_date} ${a.receive_time}`));

  enquiryPendingRecordCount.textContent = `${filtered.length} records`;

  if (filtered.length === 0) {
    enquiryPendingBody.innerHTML = `<tr><td colspan="6" class="empty-row">No messages found.</td></tr>`;
    return;
  }

  enquiryPendingBody.innerHTML = filtered.map(m => `
    <tr>
      <td><span class="key-chip">MSG-${String(m.message_id).padStart(4, "0")}</span></td>
      <td>
        <span class="profile-name">${m.customerName}</span>
        <span class="profile-sub">${m.phone || "—"}</span>
      </td>
      <td>${m.message_text}</td>
      <td><span class="badge blue">${m.intent_label}</span></td>
      <td>${formatDate(m.receive_date)} ${(m.receive_time || "").slice(0, 5)}</td>
      <td>
        <button class="action-btn" onclick="openEnquiryDetailPage(${m.message_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    </tr>
  `).join("");
}

function openEnquiryDetailPage(id) {
  const m = realMessages.find(x => x.message_id === id);
  if (!m) return;

  detailPage.style.display = "flex";
  detailTitle.textContent = `Message Detail · MSG-${String(m.message_id).padStart(4, "0")}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Customer</label>
      <input value="${m.customerName}" readonly />
    </div>

    <div class="form-group">
      <label>Phone</label>
      <input value="${m.phone || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Intent</label>
      <input value="${m.intent_label}" readonly />
    </div>

    <div class="form-group">
      <label>Received At</label>
      <input value="${formatDate(m.receive_date)} ${(m.receive_time || "").slice(0, 5)}" readonly />
    </div>

    <div class="form-group full">
      <label>Message</label>
      <textarea readonly>${m.message_text}</textarea>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      ${m.phone ? `<a class="btn btn-secondary" href="${getWhatsAppLink(m.phone)}" target="_blank" rel="noopener"><img src="icon/chat-message.png" alt="" class="btn-icon">Open WhatsApp</a>` : ""}
    </div>
  `;
}

/* ==========================================================================
   STAFF MANAGEMENT (staff.html)
   Duty status per staff/day = approved leave first, else the staff's weekly
   off day, else on duty. "Today's Booking Volume" only counts bookings
   whose assigned staff is actually on duty today (excludes bookings left
   on a staff member's off day / approved leave).
   ========================================================================== */

// Dual-shape: staff.html converts `staff`/`leaveRequests` to real Supabase
// data, but dashboard.html still calls these same functions against the
// mock arrays (not converted yet). `bookingsAreReal` (set by
// loadRealBookingData(), which staff.html also calls) is the shared signal
// for which shape is currently loaded — real staff uses staff_id/off_days_json,
// mock staff uses id/offDays.
function findStaffAny(staffId) {
  return bookingsAreReal ? findRealStaff(staffId) : findStaff(staffId);
}
function staffOffDays(member) {
  return bookingsAreReal ? (member.off_days_json || []) : (member.offDays || []);
}

function isStaffOnLeave(staffId, dateStr) {
  return leaveRequests.some(lv =>
    String(lv.staffId) === String(staffId) && lv.status === "approved" &&
    dateStr >= lv.startDate && dateStr <= lv.endDate
  );
}

function getStaffDutyStatus(staffId, dateStr) {
  if (isStaffOnLeave(staffId, dateStr)) return "leave";
  const member = findStaffAny(staffId);
  if (!member) return "off";
  const dayName = new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", { weekday: "long" });
  return staffOffDays(member).includes(dayName) ? "off" : "duty";
}

function isStaffOnDuty(staffId, dateStr) {
  return getStaffDutyStatus(staffId, dateStr) === "duty";
}

function getActiveStaffBookingVolume(dateStr = getToday()) {
  return bookings.filter(b => b.date === dateStr && isStaffOnDuty(b.staffId, dateStr)).length;
}

function countBookingsForStaffToday(staffId) {
  const todayDate = getToday();
  return bookings.filter(b => String(b.staffId) === String(staffId) && b.date === todayDate).length;
}

// Real staff/leave data (staff.html only) — same pattern as
// loadRealBookingData()/loadRealCrmData(): only runs from staff.html's own
// init, every other still-mock page keeps its pristine mock arrays.
let leaveDataIsReal = false;

function mapLeaveRequest(row) {
  const member = findRealStaff(row.staff_id);
  return {
    id: row.leave_id,
    staffId: row.staff_id,
    staffName: member?.staff_name || "—",
    startDate: row.start_date,
    endDate: row.end_date,
    reason: row.reason,
    status: row.status.toLowerCase(),
    appliedAt: (row.applied_time || "").slice(0, 5),
  };
}

async function loadRealStaffPageData() {
  await loadRealBookingData(); // populates real `staff` (and `bookings`, for booking-volume KPI)
  const leaveRes = await api.listLeaveRequests({ limit: 1000 });
  leaveRequests = leaveRes.map(mapLeaveRequest);
  leaveDataIsReal = true;
}

async function decideLeaveRequest(id, decision) {
  const lv = leaveRequests.find(r => r.id === id);
  if (!lv) return;
  try {
    await api.decideLeaveRequest(lv.id, decision, null);
  } catch (err) {
    alert(err.message);
    return;
  }
  lv.status = decision.toLowerCase();
  updateStaffKPI();
  renderPendingLeaveTable();
  renderLeaveHistoryTable();
  renderDutyCalendar();
  renderStaffListTable();
}

let staffDutyWeekAnchor = getStartOfWeek(getToday());

const staffSearchInput = document.getElementById("staffSearchInput");
const staffListBody = document.getElementById("staffListBody");
const staffListRecordCount = document.getElementById("staffListRecordCount");
const staffLeavePendingBody = document.getElementById("staffLeavePendingBody");
const staffLeavePendingCount = document.getElementById("staffLeavePendingCount");

async function initStaffPage() {
  await loadRealStaffPageData();

  if (!isManager(getCurrentAccount())) {
    document.getElementById("addStaffBtn")?.remove();
  }

  document.querySelectorAll("#staffTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#staffTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("staffCalendarPanel").classList.toggle("hidden", btn.dataset.panel !== "calendar");
      document.getElementById("staffListPanel").classList.toggle("hidden", btn.dataset.panel !== "list");
      document.getElementById("staffLeaveHistoryPanel").classList.toggle("hidden", btn.dataset.panel !== "leave");
    });
  });

  staffSearchInput.addEventListener("input", renderStaffListTable);

  document.getElementById("dutyCalPrevBtn").addEventListener("click", () => { staffDutyWeekAnchor = addDays(staffDutyWeekAnchor, -7); renderDutyCalendar(); });
  document.getElementById("dutyCalNextBtn").addEventListener("click", () => { staffDutyWeekAnchor = addDays(staffDutyWeekAnchor, 7); renderDutyCalendar(); });
  document.getElementById("dutyCalTodayBtn").addEventListener("click", () => { staffDutyWeekAnchor = getStartOfWeek(getToday()); renderDutyCalendar(); });

  updateStaffKPI();
  renderStaffListTable();
  renderPendingLeaveTable();
  renderLeaveHistoryTable();
  renderDutyCalendar();
}

function updateStaffKPI() {
  const todayDate = getToday();
  document.getElementById("staffTotalCount").textContent = staff.length;
  document.getElementById("staffOnDutyCount").textContent = staff.filter(s => isStaffOnDuty(s.staff_id, todayDate)).length;
  document.getElementById("staffOnLeaveCount").textContent = staff.filter(s => isStaffOnLeave(s.staff_id, todayDate)).length;
  document.getElementById("staffBookingVolume").textContent = getActiveStaffBookingVolume(todayDate);
}

function renderStaffListTable() {
  const searchValue = staffSearchInput.value.toLowerCase().trim();
  const manager = isManager(getCurrentAccount());
  const todayDate = getToday();

  const filtered = staff.filter(s =>
    s.staff_name.toLowerCase().includes(searchValue) ||
    s.role.toLowerCase().includes(searchValue) ||
    (s.email || "").toLowerCase().includes(searchValue)
  );

  staffListRecordCount.textContent = `${filtered.length} staff`;

  if (filtered.length === 0) {
    staffListBody.innerHTML = `<tr><td colspan="7" class="empty-row">No staff record found.</td></tr>`;
    return;
  }

  staffListBody.innerHTML = filtered.map(s => {
    const status = getStaffDutyStatus(s.staff_id, todayDate);
    const statusLabel = status === "duty" ? "On Duty" : status === "leave" ? "On Leave" : "Off Today";
    const statusClass = status === "duty" ? "done" : status === "leave" ? "no_show" : "off";

    return `
      <tr>
        <td>
          <span class="profile-name"><img src="icon/team.png" alt="" class="row-icon">${s.staff_name}</span>
          <span class="profile-sub">${s.staff_id}</span>
        </td>
        <td>${s.role}</td>
        <td>
          <span class="profile-sub">${s.email || "—"}</span>
          <span class="profile-sub">${s.phone || "—"}</span>
        </td>
        <td>${(s.off_days_json || []).join(", ") || "—"}</td>
        <td>${countBookingsForStaffToday(s.staff_id)}</td>
        <td><span class="status-tag status-${statusClass}">${statusLabel}</span></td>
        <td>
          <button class="${manager ? "edit-btn" : "action-btn"}" onclick="openStaffForm('${s.staff_id}')">${manager ? "Edit" : "View"}</button>
        </td>
      </tr>
    `;
  }).join("");
}

function openStaffForm(staffId = null) {
  const manager = isManager(getCurrentAccount());
  const isEdit = Boolean(staffId);
  const member = isEdit
    ? findRealStaff(staffId)
    : { staff_id: null, staff_name: "", role: "Staff", email: "", phone: "", off_days_json: ["Sunday"], status: "active" };
  if (isEdit && !member) return;

  const readonly = !manager;
  const dayOptions = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit ? `Staff Info · ${member.staff_id}` : "Add Staff";

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Staff ID</label>
      <input value="${isEdit ? member.staff_id : "Assigned on save"}" readonly />
    </div>

    <div class="form-group">
      <label>Name</label>
      <input name="staff_name" value="${member.staff_name}" placeholder="Full name" ${readonly ? "readonly" : "required"} />
    </div>

    <div class="form-group">
      <label>Role</label>
      ${readonly
        ? `<input value="${member.role}" readonly />`
        : `<select name="role">
            <option value="Manager" ${member.role === "Manager" ? "selected" : ""}>Manager</option>
            <option value="Staff" ${member.role === "Staff" ? "selected" : ""}>Staff</option>
          </select>`
      }
    </div>

    <div class="form-group">
      <label>Email</label>
      <input name="email" type="email" value="${member.email || ""}" placeholder="name@business.my" ${readonly ? "readonly" : ""} />
    </div>

    <div class="form-group">
      <label>Phone</label>
      <input name="phone" value="${member.phone || ""}" placeholder="+60..." ${readonly ? "readonly" : ""} />
    </div>

    <div class="form-group">
      <label>Status</label>
      ${readonly
        ? `<input value="${member.status || "active"}" readonly />`
        : `<select name="status">
            <option value="active" ${member.status === "active" ? "selected" : ""}>Active</option>
            <option value="inactive" ${member.status === "inactive" ? "selected" : ""}>Inactive</option>
          </select>`
      }
    </div>

    <div class="form-group full">
      <label>Weekly Off Day(s)</label>
      ${readonly
        ? `<input value="${(member.off_days_json || []).join(", ") || "None"}" readonly />`
        : `<div style="display:flex;flex-wrap:wrap;gap:14px;padding:10px 0;">
            ${dayOptions.map(d => `
              <label style="display:inline-flex;align-items:center;gap:6px;font-weight:500;font-size:0.85rem;color:var(--text-charcoal);">
                <input type="checkbox" name="off_days_json" value="${d}" ${(member.off_days_json || []).includes(d) ? "checked" : ""} />
                ${d}
              </label>
            `).join("")}
          </div>`
      }
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">${manager ? "Cancel" : "Close"}</button>
      ${manager && isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeStaffMember('${member.staff_id}')">Remove Staff</button>` : ""}
      ${manager ? `<button type="submit" class="save-btn">${isEdit ? "Save Staff" : "Add Staff"}</button>` : ""}
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();
    if (!manager) return;

    const formData = new FormData(detailForm);
    const payload = {
      staff_name: formData.get("staff_name"),
      role: formData.get("role"),
      email: formData.get("email"),
      phone: formData.get("phone"),
      status: formData.get("status") || "active",
      off_days_json: formData.getAll("off_days_json"),
    };

    try {
      if (isEdit) {
        const updated = await api.updateStaff(member.staff_id, payload);
        const index = staff.findIndex(s => s.staff_id === member.staff_id);
        staff[index] = updated;
      } else {
        const created = await api.createStaff(payload);
        staff.push(created);
      }
    } catch (err) {
      alert(err.message);
      return;
    }

    updateStaffKPI();
    renderStaffListTable();
    renderDutyCalendar();
    closeDetailPage();
  };
}

async function removeStaffMember(staffId) {
  if (!confirm("Remove this staff member? This cannot be undone.")) return;

  try {
    await api.deleteStaff(staffId);
  } catch (err) {
    // Likely a foreign-key constraint — this staff member still has
    // bookings/leave records referencing them.
    alert(err.message);
    return;
  }

  const index = staff.findIndex(s => String(s.staff_id) === String(staffId));
  if (index >= 0) staff.splice(index, 1);

  updateStaffKPI();
  renderStaffListTable();
  renderDutyCalendar();
  closeDetailPage();
}

function renderPendingLeaveTable() {
  const manager = isManager(getCurrentAccount());
  const pending = leaveRequests.filter(lv => lv.status === "pending");

  staffLeavePendingCount.textContent = `${pending.length} pending`;

  if (pending.length === 0) {
    staffLeavePendingBody.innerHTML = `<tr><td colspan="5" class="empty-row">No pending leave requests.</td></tr>`;
    return;
  }

  staffLeavePendingBody.innerHTML = pending.map(lv => `
    <tr>
      <td><span class="profile-name">${lv.staffName}</span></td>
      <td>${lv.startDate === lv.endDate ? formatDate(lv.startDate) : `${formatDate(lv.startDate)} – ${formatDate(lv.endDate)}`}</td>
      <td>${lv.reason}</td>
      <td>${lv.appliedAt}</td>
      <td>${manager
        ? `<button class="edit-btn" onclick="decideLeaveRequest(${lv.id}, 'Approved')">Approve</button>
           <button class="action-btn" onclick="decideLeaveRequest(${lv.id}, 'Rejected')">Reject</button>`
        : `<span class="profile-sub">Awaiting manager</span>`}</td>
    </tr>
  `).join("");
}

function renderLeaveHistoryTable() {
  const manager = isManager(getCurrentAccount());
  const history = leaveRequests
    .slice()
    .sort((a, b) => new Date(b.startDate) - new Date(a.startDate));

  document.getElementById("staffLeaveHistoryCount").textContent = `${history.length} records`;

  if (history.length === 0) {
    document.getElementById("staffLeaveHistoryBody").innerHTML = `<tr><td colspan="7" class="empty-row">No leave applications found.</td></tr>`;
    return;
  }

  const STATUS_TAG = { pending: "pending", approved: "done", rejected: "no_show" };
  const STATUS_LABEL = { pending: "Pending", approved: "Approved", rejected: "Rejected" };

  document.getElementById("staffLeaveHistoryBody").innerHTML = history.map(lv => `
    <tr>
      <td><span class="key-chip">LV-${String(lv.id).padStart(4, "0")}</span></td>
      <td><span class="profile-name">${lv.staffName}</span></td>
      <td>${lv.startDate === lv.endDate ? formatDate(lv.startDate) : `${formatDate(lv.startDate)} – ${formatDate(lv.endDate)}`}</td>
      <td>${lv.reason}</td>
      <td>${lv.appliedAt}</td>
      <td><span class="status-tag status-${STATUS_TAG[lv.status] || "pending"}">${STATUS_LABEL[lv.status] || lv.status}</span></td>
      <td>${manager && lv.status === "pending"
        ? `<button class="edit-btn" onclick="decideLeaveRequest(${lv.id}, 'Approved')">Approve</button>`
        : "—"}</td>
    </tr>
  `).join("");
}

function openApplyLeaveForm() {
  detailPage.style.display = "flex";
  detailTitle.textContent = "Apply Leave";

  detailForm.innerHTML = `
    <div class="form-group full">
      <label>Staff</label>
      <select name="staffId" required>
        ${staff.map(s => `<option value="${s.staff_id}">${s.staff_name} (${s.role})</option>`).join("")}
      </select>
    </div>

    <div class="form-group">
      <label>Start Date</label>
      <input type="date" name="startDate" value="${getToday()}" required />
    </div>

    <div class="form-group">
      <label>End Date</label>
      <input type="date" name="endDate" value="${getToday()}" required />
    </div>

    <div class="form-group full">
      <label>Reason</label>
      <input name="reason" placeholder="e.g. Medical, Personal, Emergency" required />
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Cancel</button>
      <button type="submit" class="save-btn">Submit Application</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();
    const formData = new FormData(detailForm);
    const selectedStaffId = Number(formData.get("staffId"));
    const member = findRealStaff(selectedStaffId);

    const payload = {
      staff_id: selectedStaffId,
      start_date: formData.get("startDate"),
      end_date: formData.get("endDate"),
      reason: formData.get("reason"),
      status: "Pending",
    };

    try {
      const created = await api.createLeaveRequest(payload);
      leaveRequests.push(mapLeaveRequest(created));
    } catch (err) {
      alert(err.message);
      return;
    }

    updateStaffKPI();
    renderPendingLeaveTable();
    renderLeaveHistoryTable();
    renderDutyCalendar();
    closeDetailPage();
  };
}

function renderDutyCalendar() {
  const dates = getDateRange(staffDutyWeekAnchor, addDays(staffDutyWeekAnchor, 6));
  const todayDate = getToday();

  document.getElementById("dutyCalWeekLabel").textContent = `${formatShortDate(staffDutyWeekAnchor)} – ${formatShortDate(addDays(staffDutyWeekAnchor, 6))}`;

  document.getElementById("dutyCalendarHead").innerHTML = `
    <th>Staff</th>
    ${dates.map(d => {
      const dateObj = new Date(d + "T00:00:00");
      const isToday = d === todayDate;
      return `<th style="${isToday ? "color:var(--btn-brown);" : ""}">${dateObj.toLocaleDateString("en-MY", { weekday: "short" })} ${dateObj.getDate()}</th>`;
    }).join("")}
  `;

  document.getElementById("dutyCalendarBody").innerHTML = staff.map(s => `
    <tr>
      <td>
        <span class="profile-name">${s.staff_name}</span>
        <span class="profile-sub">${s.role}</span>
      </td>
      ${dates.map(d => {
        const status = getStaffDutyStatus(s.staff_id, d);
        const label = status === "duty" ? "Duty" : status === "leave" ? "Leave" : "Off";
        const cls = status === "duty" ? "done" : status === "leave" ? "no_show" : "off";
        const isLeave = status === "leave";
        return `<td ${isLeave ? `style="cursor:pointer;" onclick="openLeaveCellDetail(${s.staff_id},'${d}')"` : ""}><span class="status-tag status-${cls}">${label}</span></td>`;
      }).join("")}
    </tr>
  `).join("");
}

function openLeaveCellDetail(staffId, dateStr) {
  const lv = leaveRequests.find(r =>
    String(r.staffId) === String(staffId) && r.status === "approved" &&
    dateStr >= r.startDate && dateStr <= r.endDate
  );
  if (!lv) return;

  detailPage.style.display = "flex";
  detailTitle.textContent = `Leave Detail · ${lv.staffName}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Staff</label>
      <input value="${lv.staffName}" readonly />
    </div>

    <div class="form-group">
      <label>Leave Dates</label>
      <input value="${lv.startDate === lv.endDate ? formatDate(lv.startDate) : `${formatDate(lv.startDate)} – ${formatDate(lv.endDate)}`}" readonly />
    </div>

    <div class="form-group full">
      <label>Reason</label>
      <input value="${lv.reason}" readonly />
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Close</button>
    </div>
  `;
}

/* ==========================================================================
   ANALYTICAL DASHBOARD (dashboard.html) — Manager only.
   Every number and every chart/card drill-down is derived from real
   Supabase data. Your schema has no rooms/services catalog (occupancy and
   revenue-by-service are redesigned around real boarding room_type labels
   and free-text service names instead), and your `messages`/loyalty tables
   have no pending/resolved workflow at all (see enquiries.html/loyalty.html
   conversions) — those specific numbers are repurposed into real,
   non-fabricated equivalents rather than kept as a fake approval queue.
   Only genuinely fabricated numbers (AI accuracy, uptime, response time)
   stay marked "Illustrative" and non-clickable.
   ========================================================================== */

let realPayments = [];

async function loadRealDashboardData() {
  await loadRealBookingData(); // bookings + staff

  const [customersRes, petsRes, membersRes, couponsRes, redemptionsRes, leaveRes, messagesRes, paymentsRes] = await Promise.all([
    api.listCustomers({ limit: 1000 }),
    api.listPets({ limit: 1000 }),
    api.listMembers({ limit: 1000 }),
    api.listCoupons(),
    api.listRedemptions({ limit: 1000 }),
    api.listLeaveRequests({ limit: 1000 }),
    api.listChatMessages({ limit: 1000 }),
    api.listPayments({ limit: 2000 }),
  ]);

  customers = customersRes;
  pets = petsRes;
  crmDataIsReal = true;

  const customerById = new Map(customers.map(c => [c.customer_id, c]));

  realMembers = membersRes.map(m => ({
    ...m,
    customerName: customerById.get(m.customer_id)?.full_name || "—",
    phone: customerById.get(m.customer_id)?.phone_number || "—",
  }));
  realCoupons = couponsRes;
  const couponById = new Map(realCoupons.map(c => [c.coupon_id, c]));
  const memberByLoyaltyId = new Map(realMembers.map(m => [m.loyalty_id, m]));
  realRedemptions = redemptionsRes.map(r => ({
    ...r,
    memberName: memberByLoyaltyId.get(r.loyalty_id)?.customerName || "—",
    couponName: r.coupon_id ? (couponById.get(r.coupon_id)?.reward_name || "—") : null,
  }));
  loyaltyDataIsReal = true;

  leaveRequests = leaveRes.map(mapLeaveRequest);
  leaveDataIsReal = true;

  realMessages = messagesRes.map(m => ({
    ...m,
    customerName: customerById.get(m.sender_id)?.full_name || "—",
    phone: customerById.get(m.sender_id)?.phone_number || "",
  }));

  realPayments = paymentsRes;
}

const SERVICE_MIX_COLORS = { grooming: "#3B82F6", boarding: "#10B981", daycare: "#F59E0B" };

const KPI_DEFS = [
  { key: "revenue",   icon: "payment-card.png",      label: "Total Revenue",               deltaPct: 12.4, onClick: "openKpiDetail('revenue')" },
  { key: "bookings",  icon: "calendar-simple.png",    label: "Total Bookings",               deltaPct: 8.7,  onClick: "openKpiDetail('bookings')" },
  { key: "repeat",    icon: "users.png",              label: "Repeat Customer Rate",          deltaPct: 5.3,  onClick: "openKpiDetail('repeat')" },
  { key: "occupancy", icon: "line-chart.png",        label: "Occupancy / Slot Utilisation",  deltaPct: 6.1,  onClick: "openKpiDetail('occupancy')" }
];

let currentDashboardPeriod = "weekly";
let dashboardAnchorDate = today;
let dashboardRange = null;
let dashboardTrendBuckets = [];

function shiftDashboardAnchor(step) {
  if (currentDashboardPeriod === "daily") dashboardAnchorDate = addDays(dashboardAnchorDate, step);
  else if (currentDashboardPeriod === "monthly") dashboardAnchorDate = addMonths(dashboardAnchorDate, step);
  else dashboardAnchorDate = addDays(dashboardAnchorDate, step * 7);
  renderAnalyticsDashboard();
}

function formatCurrency(n) {
  return `RM ${Math.round(n).toLocaleString("en-MY")}`;
}

function getWeekRange(anchorDate) {
  const start = getStartOfWeek(anchorDate);
  return { start, end: addDays(start, 6) };
}

function getPeriodRange(period, anchorDate) {
  if (period === "daily") return { start: anchorDate, end: anchorDate };
  if (period === "monthly") {
    const start = getStartOfMonth(anchorDate);
    return { start, end: addDays(addMonths(start, 1), -1) };
  }
  return getWeekRange(anchorDate);
}

function periodRangeLabel(period, range) {
  if (period === "daily") return `Today · ${formatShortDate(range.start)}`;
  if (period === "monthly") {
    return new Date(range.start + "T00:00:00").toLocaleDateString("en-MY", { month: "long", year: "numeric" });
  }
  return `${formatShortDate(range.start)} – ${formatShortDate(range.end)}`;
}

function formatPeriodChip(period, range) {
  return `<img src="icon/calendar-simple.png" alt="" class="row-icon">${periodRangeLabel(period, range)}`;
}

// Your real schema has no rooms/capacity catalog — only boarding_booking's
// free-text room_type per booking (see dailyoverview.html's
// getActiveRoomLabels(), reused here). "Occupancy" is redefined as: of the
// distinct real room labels currently in use, what fraction are occupied on
// a given day — there's no fixed total room count to divide by instead.
function isRoomLabelOccupiedOnDate(roomLabel, date) {
  return bookings.some(b =>
    b.bookingType === "boarding" && b.roomLabel === roomLabel &&
    b.status !== "no_show" && b.status !== "cancelled" &&
    b.checkInDate && b.checkOutDate && date >= b.checkInDate && date <= b.checkOutDate
  );
}

function computeOccupancyRate(range) {
  const roomLabels = getActiveRoomLabels();
  if (!roomLabels.length) return 0;
  const days = getDateRange(range.start, range.end);
  if (!days.length) return 0;
  const dailyRates = days.map(d => roomLabels.filter(label => isRoomLabelOccupiedOnDate(label, d)).length / roomLabels.length);
  return (dailyRates.reduce((a, b) => a + b, 0) / dailyRates.length) * 100;
}

// dailyoverview.html's computeSlaCompliance() reads the mock enquiries/
// loyaltyRequests arrays — dashboard.html now has real bookings but no real
// pending/SLA concept for messages or loyalty (see enquiries.html/
// loyalty.html conversions), so this only tracks the one SLA that's real:
// grooming service pending past its 15-minute window.
function computeRealSlaCompliance() {
  const pendingGrooming = bookings.filter(b => b.bookingType === "grooming" && b.date === today && b.status === "pending");
  const breaches = slaBreachCount("pendingService", pendingGrooming, "time");
  const total = pendingGrooming.length;
  return { total, breaches, rate: total ? Math.round(((total - breaches) / total) * 100) : 100 };
}

function computeRepeatCustomerRate(periodBookings) {
  const lifetimeCountByCustomer = {};
  bookings.forEach(b => { lifetimeCountByCustomer[b.customerName] = (lifetimeCountByCustomer[b.customerName] || 0) + 1; });

  const periodCustomers = [...new Set(periodBookings.map(b => b.customerName))];
  if (!periodCustomers.length) return 0;

  const repeatCount = periodCustomers.filter(name => lifetimeCountByCustomer[name] > 1).length;
  return (repeatCount / periodCustomers.length) * 100;
}

function computeDashboardMetrics(period) {
  const range = getPeriodRange(period, dashboardAnchorDate);
  dashboardRange = range;
  const allPeriodBookings = bookings.filter(b => b.date >= range.start && b.date <= range.end);
  const periodBookings = allPeriodBookings.filter(b => b.status !== "cancelled" && b.status !== "no_show");
  const totalRevenue = periodBookings.reduce((sum, b) => sum + b.amount, 0);
  const totalBookings = periodBookings.length;

  const doneCount = periodBookings.filter(b => b.status === "done").length;
  const completionRate = totalBookings ? (doneCount / totalBookings) * 100 : 0;

  const noShowCount = allPeriodBookings.filter(b => b.status === "no_show").length;
  const noShowRate = allPeriodBookings.length ? (noShowCount / allPeriodBookings.length) * 100 : 0;

  const slaCompliance = computeRealSlaCompliance();
  const occupancyRate = computeOccupancyRate(range);
  const repeatCustomerRate = computeRepeatCustomerRate(periodBookings);

  const serviceMix = ["grooming", "boarding", "daycare"].map(type => ({
    type, count: periodBookings.filter(b => b.serviceType === type).length
  }));

  // No services catalog in your real schema — group by the free-text
  // service label instead of a serviceId, prefixed with bookingType since a
  // grooming service name and a daycare package name could otherwise collide.
  const revenueByService = {};
  periodBookings.forEach(b => {
    const key = `${b.bookingType}:${b.serviceLabel}`;
    if (!revenueByService[key]) revenueByService[key] = { key, bookingType: b.bookingType, serviceLabel: b.serviceLabel, revenue: 0 };
    revenueByService[key].revenue += b.amount;
  });
  const topServices = Object.values(revenueByService).sort((a, b) => b.revenue - a.revenue);

  const pendingTasks = buildActionQueue("all").length;

  return {
    period, range, periodBookings, totalRevenue, totalBookings,
    completionRate, noShowRate, slaCompliance, occupancyRate, repeatCustomerRate,
    serviceMix, topServices, pendingTasks
  };
}

function renderKpiHeroGrid(metrics) {
  const values = {
    revenue: formatCurrency(metrics.totalRevenue),
    bookings: metrics.totalBookings.toLocaleString("en-MY"),
    repeat: `${Math.round(metrics.repeatCustomerRate)}%`,
    occupancy: `${Math.round(metrics.occupancyRate)}%`
  };

  q("kpiHeroGrid").innerHTML = KPI_DEFS.map(def => {
    const deltaLabel = def.deltaAbs != null
      ? `▲ ${def.deltaAbs} vs last period`
      : `▲ ${def.deltaPct}% vs last period`;

    return `
      <div class="kpi-hero-card ${def.onClick ? "clickable" : ""}" ${def.onClick ? `onclick="${def.onClick}"` : ""}>
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/${def.icon}" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">${def.label}</div>
            <div class="kpi-hero-value">${values[def.key]}</div>
          </div>
        </div>
        <div class="kpi-hero-delta up">${deltaLabel}</div>
      </div>
    `;
  }).join("");
}

function openKpiDetail(key) {
  const metrics = computeDashboardMetrics(currentDashboardPeriod);
  const label = periodRangeLabel(metrics.period, metrics.range);

  if (key === "revenue") {
    const items = [...metrics.periodBookings].sort((a, b) => b.amount - a.amount);
    openDetailModal("Total Revenue Breakdown", `${formatCurrency(metrics.totalRevenue)} across ${items.length} booking(s) · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
  } else if (key === "bookings") {
    const items = [...metrics.periodBookings].sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
    openDetailModal("Total Bookings", `${items.length} booking(s) · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
  } else if (key === "occupancy") {
    const days = getDateRange(metrics.range.start, metrics.range.end);
    const roomLabels = getActiveRoomLabels();
    const rows = roomLabels.map(roomLabel => {
      const occupiedDays = days.filter(d => isRoomLabelOccupiedOnDate(roomLabel, d)).length;
      const pct = days.length ? Math.round((occupiedDays / days.length) * 100) : 0;
      return renderDetailRow({ title: roomLabel, sub: "Boarding room", tag: pct >= 50 ? "scheduled" : "done", tagLabel: `${occupiedDays}/${days.length} day(s) · ${pct}%` });
    }).join("");
    openDetailModal("Room Occupancy", `Average ${Math.round(metrics.occupancyRate)}% occupancy · ${label}`, rows, { label: "Open Booking Dashboard", href: "booking.html" });
  } else if (key === "repeat") {
    const lifetimeCountByCustomer = {};
    bookings.forEach(b => { lifetimeCountByCustomer[b.customerName] = (lifetimeCountByCustomer[b.customerName] || 0) + 1; });
    const periodCustomers = [...new Set(metrics.periodBookings.map(b => b.customerName))];
    const rows = periodCustomers.map(name => {
      const lifetimeCount = lifetimeCountByCustomer[name];
      const isRepeat = lifetimeCount > 1;
      return renderDetailRow({ title: name, sub: `${lifetimeCount} lifetime booking(s)`, tag: isRepeat ? "scheduled" : "done", tagLabel: isRepeat ? "Repeat Customer" : "New Customer" });
    }).join("");
    openDetailModal("Repeat Customer Rate", `${Math.round(metrics.repeatCustomerRate)}% repeat · ${periodCustomers.length} customer(s) · ${label}`, rows, { label: "Open CRM", href: "profile.html" });
  }
}

function buildTrendBuckets(period, range) {
  if (period === "daily") {
    return SCHEDULE_HOURS.map(h => ({
      label: h,
      matches: b => b.date === range.start && slotForTime(b.time) === h
    }));
  }
  if (period === "monthly") {
    const buckets = [];
    let cursor = range.start, idx = 1;
    while (cursor <= range.end) {
      const bucketEnd = addDays(cursor, 6) > range.end ? range.end : addDays(cursor, 6);
      const start = cursor, end = bucketEnd;
      buckets.push({ label: `Wk ${idx}`, matches: b => b.date >= start && b.date <= end });
      cursor = addDays(bucketEnd, 1);
      idx++;
    }
    return buckets;
  }
  return getDateRange(range.start, range.end).map(d => ({
    label: new Date(d + "T00:00:00").toLocaleDateString("en-MY", { weekday: "short" }),
    matches: b => b.date === d
  }));
}

function firstBookingDateByCustomer() {
  const map = {};
  bookings.forEach(b => {
    if (!map[b.customerName] || b.date < map[b.customerName]) map[b.customerName] = b.date;
  });
  return map;
}

function computeTrendSeries(period, range) {
  const buckets = buildTrendBuckets(period, range);
  const firstDate = firstBookingDateByCustomer();

  const series = buckets.map(bucket => {
    const items = bookings.filter(b => bucket.matches(b) && b.status !== "cancelled" && b.status !== "no_show");
    const revenue = items.reduce((sum, b) => sum + b.amount, 0);
    const newSet = new Set(), returningSet = new Set();
    items.forEach(b => {
      (firstDate[b.customerName] === b.date ? newSet : returningSet).add(b.customerName);
    });
    return { label: bucket.label, revenue, newCustomers: newSet.size, returningCustomers: returningSet.size };
  });

  dashboardTrendBuckets = buckets;
  return series;
}

function openTrendBucketDetail(index) {
  const bucket = dashboardTrendBuckets[index];
  if (!bucket) return;
  const items = bookings.filter(bucket.matches).sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
  openDetailModal(bucket.label, `${items.length} booking(s) in this period.`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
}

function attachTrendInteraction(containerId, { width, height, pad, stepX, pointCount, labels, seriesDefs, onPointClick }) {
  const container = q(containerId);
  const svg = container.querySelector(".trend-chart-svg");
  const crosshair = container.querySelector(".trend-crosshair");
  const tooltip = container.querySelector(".trend-tooltip");
  const hoverDots = container.querySelectorAll(".trend-hover-dot");
  if (!svg || pointCount < 1) return;

  function indexFromClientX(clientX) {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return 0;
    const vbX = (clientX - rect.left) * (width / rect.width);
    const rawIndex = Math.round((vbX - pad) / (stepX || 1));
    return Math.min(pointCount - 1, Math.max(0, rawIndex));
  }

  function showAt(index) {
    const x = pad + index * stepX;
    const rect = svg.getBoundingClientRect();

    crosshair.setAttribute("x1", x);
    crosshair.setAttribute("x2", x);
    crosshair.classList.add("is-active");

    seriesDefs.forEach((s, si) => {
      const y = height - pad - (s.values[index] / s.max) * (height - pad * 2);
      hoverDots[si].setAttribute("cx", x);
      hoverDots[si].setAttribute("cy", y);
      hoverDots[si].classList.add("is-active");
    });

    const pxX = (x / width) * rect.width;
    const tooltipHalfWidth = 80;
    tooltip.style.left = `${Math.min(Math.max(pxX, tooltipHalfWidth), rect.width - tooltipHalfWidth)}px`;
    tooltip.classList.add("is-active");

    tooltip.innerHTML = "";
    const titleEl = document.createElement("div");
    titleEl.className = "trend-tooltip-title";
    titleEl.textContent = labels[index] || "";
    tooltip.appendChild(titleEl);

    seriesDefs.forEach(s => {
      const row = document.createElement("div");
      row.className = "trend-tooltip-row";
      const key = document.createElement("span");
      key.className = "trend-tooltip-key";
      key.style.backgroundColor = s.color;
      row.appendChild(key);
      if (s.label) {
        const name = document.createElement("span");
        name.className = "trend-tooltip-name";
        name.textContent = s.label;
        row.appendChild(name);
      }
      const val = document.createElement("span");
      val.className = "trend-tooltip-value";
      val.textContent = s.format ? s.format(s.values[index]) : s.values[index];
      row.appendChild(val);
      tooltip.appendChild(row);
    });
  }

  function hide() {
    crosshair.classList.remove("is-active");
    hoverDots.forEach(d => d.classList.remove("is-active"));
    tooltip.classList.remove("is-active");
  }

  svg.style.cursor = onPointClick ? "pointer" : "crosshair";
  svg.addEventListener("pointermove", e => showAt(indexFromClientX(e.clientX)));
  svg.addEventListener("pointerleave", hide);
  if (onPointClick) svg.addEventListener("click", e => onPointClick(indexFromClientX(e.clientX)));
}

function smoothPath(points) {
  if (points.length < 2) return "";
  if (points.length === 2) return `M${points[0].x},${points[0].y} L${points[1].x},${points[1].y}`;

  let d = `M${points[0].x},${points[0].y}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;

    let cp1x = p1.x + (p2.x - p0.x) / 6;
    let cp1y = p1.y + (p2.y - p0.y) / 6;
    let cp2x = p2.x - (p3.x - p1.x) / 6;
    let cp2y = p2.y - (p3.y - p1.y) / 6;

    const yLo = Math.min(p1.y, p2.y), yHi = Math.max(p1.y, p2.y);
    cp1y = Math.min(Math.max(cp1y, yLo), yHi);
    cp2y = Math.min(Math.max(cp2y, yLo), yHi);

    d += ` C${cp1x},${cp1y} ${cp2x},${cp2y} ${p2.x},${p2.y}`;
  }
  return d;
}

function renderAreaTrendChart(containerId, series, options) {
  const { color = "#3B82F6", labels = [], area = true, onPointClick, format = v => v } = options;
  const width = 600, height = 140, pad = 6;
  const max = Math.max(...series, 1) * 1.15;
  const stepX = series.length > 1 ? (width - pad * 2) / (series.length - 1) : 0;

  const points = series.map((v, i) => ({
    x: pad + i * stepX,
    y: height - pad - (v / max) * (height - pad * 2)
  }));

  const linePath = smoothPath(points);
  const areaPath = area
    ? `${linePath} L${points[points.length - 1].x},${height - pad} L${points[0].x},${height - pad} Z`
    : "";

  const gridLines = Array.from({ length: 4 }, (_, i) => {
    const y = pad + (i / 3) * (height - pad * 2);
    return `<line class="trend-gridline" x1="${pad}" y1="${y}" x2="${width - pad}" y2="${y}" />`;
  }).join("");

  const dots = points.map(p => `<circle class="trend-dot" cx="${p.x}" cy="${p.y}" r="3.5" fill="${color}" />`).join("");
  const last = points[points.length - 1];

  q(containerId).innerHTML = `
    <div class="trend-chart-surface">
      <svg class="trend-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
        ${gridLines}
        ${area ? `<path class="trend-area" d="${areaPath}" fill="${color}" />` : ""}
        <path class="trend-line" d="${linePath}" stroke="${color}" />
        ${dots}
        <text class="trend-end-label" x="${width - pad}" y="${Math.max(last.y - 10, pad + 9)}">${format(series[series.length - 1])}</text>
        <line class="trend-crosshair" x1="0" y1="${pad}" x2="0" y2="${height - pad}" />
        <circle class="trend-hover-dot" r="5" fill="${color}" />
      </svg>
      <div class="trend-tooltip"></div>
    </div>
    <div class="trend-chart-labels">${labels.map(l => `<span>${l}</span>`).join("")}</div>
  `;

  attachTrendInteraction(containerId, {
    width, height, pad, stepX, pointCount: series.length, labels,
    seriesDefs: [{ color, label: null, values: series, max, format }],
    onPointClick
  });
}

function renderDualLineChart(containerId, seriesA, seriesB, options) {
  const { colorA, colorB, labelA, labelB, labels = [], onPointClick, format = v => v } = options;
  const width = 600, height = 140, pad = 6;
  const max = Math.max(...seriesA, ...seriesB, 1) * 1.15;
  const stepX = seriesA.length > 1 ? (width - pad * 2) / (seriesA.length - 1) : 0;

  function toPoints(series) {
    return series.map((v, i) => ({
      x: pad + i * stepX,
      y: height - pad - (v / max) * (height - pad * 2)
    }));
  }

  function toPath(points) {
    return smoothPath(points);
  }

  function toDots(points, color) {
    return points.map(p => `<circle class="trend-dot" cx="${p.x}" cy="${p.y}" r="3" fill="${color}" />`).join("");
  }

  const pointsA = toPoints(seriesA);
  const pointsB = toPoints(seriesB);

  const gridLines = Array.from({ length: 4 }, (_, i) => {
    const y = pad + (i / 3) * (height - pad * 2);
    return `<line class="trend-gridline" x1="${pad}" y1="${y}" x2="${width - pad}" y2="${y}" />`;
  }).join("");

  const lastA = pointsA[pointsA.length - 1];
  const lastB = pointsB[pointsB.length - 1];
  let labelYA = lastA.y - 8;
  let labelYB = lastB.y - 8;
  if (Math.abs(labelYA - labelYB) < 12) {
    labelYA -= 6;
    labelYB += 12;
  }
  labelYA = Math.min(Math.max(labelYA, pad + 9), height - pad);
  labelYB = Math.min(Math.max(labelYB, pad + 9), height - pad);

  q(containerId).innerHTML = `
    <div class="trend-chart-legend">
      <span><span class="legend-swatch" style="background:${colorA};"></span>${labelA}</span>
      <span><span class="legend-swatch" style="background:${colorB};"></span>${labelB}</span>
    </div>
    <div class="trend-chart-surface">
      <svg class="trend-chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
        ${gridLines}
        <path class="trend-line" d="${toPath(pointsA)}" stroke="${colorA}" />
        <path class="trend-line" d="${toPath(pointsB)}" stroke="${colorB}" />
        ${toDots(pointsA, colorA)}
        ${toDots(pointsB, colorB)}
        <text class="trend-end-label" x="${width - pad}" y="${labelYA}">${format(seriesA[seriesA.length - 1])}</text>
        <text class="trend-end-label" x="${width - pad}" y="${labelYB}">${format(seriesB[seriesB.length - 1])}</text>
        <line class="trend-crosshair" x1="0" y1="${pad}" x2="0" y2="${height - pad}" />
        <circle class="trend-hover-dot" r="4" fill="${colorA}" />
        <circle class="trend-hover-dot" r="4" fill="${colorB}" />
      </svg>
      <div class="trend-tooltip"></div>
    </div>
    <div class="trend-chart-labels">${labels.map(l => `<span>${l}</span>`).join("")}</div>
  `;

  attachTrendInteraction(containerId, {
    width, height, pad, stepX, pointCount: seriesA.length, labels,
    seriesDefs: [
      { color: colorA, label: labelA, values: seriesA, max, format },
      { color: colorB, label: labelB, values: seriesB, max, format }
    ],
    onPointClick
  });
}

function openServiceMixDetail(type) {
  const items = bookings.filter(b => b.serviceType === type).sort((a, b) => a.date.localeCompare(b.date));
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  openDetailModal(`${label} Bookings — All Time`, `${items.length} booking(s).`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
}

function renderServiceMixDonut(serviceMix) {
  const total = serviceMix.reduce((sum, s) => sum + s.count, 0);

  q("serviceMixLegend").innerHTML = serviceMix.map(s => {
    const pct = total ? Math.round((s.count / total) * 100) : 0;
    const label = s.type.charAt(0).toUpperCase() + s.type.slice(1);
    return `
      <div class="chart-legend-item" data-key="${s.type}" onclick="openServiceMixDetail('${s.type}')">
        <span class="legend-swatch" style="background:${SERVICE_MIX_COLORS[s.type]};"></span>
        <span>${label} ${s.count} (${pct}%)</span>
      </div>
    `;
  }).join("");

  renderInteractiveDonut({
    chartElId: "serviceMixDonut",
    totalElId: "serviceMixTotal",
    legendElId: "serviceMixLegend",
    segments: serviceMix.map(s => ({ key: s.type, label: s.type.charAt(0).toUpperCase() + s.type.slice(1), count: s.count, color: SERVICE_MIX_COLORS[s.type] })),
    onSegmentClick: openServiceMixDetail
  });
}

function openTopServiceDetail(bookingType, serviceLabel) {
  const items = bookings.filter(b => b.bookingType === bookingType && b.serviceLabel === serviceLabel && b.date >= dashboardRange.start && b.date <= dashboardRange.end).sort((a, b) => b.date.localeCompare(a.date));
  const revenue = items.reduce((sum, b) => sum + b.amount, 0);
  const label = periodRangeLabel(currentDashboardPeriod, dashboardRange);
  openDetailModal(`${serviceLabel} — Bookings`, `${items.length} booking(s) · RM ${revenue.toLocaleString("en-MY")} total revenue · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
}

function renderTopServices(topServices) {
  const top = topServices.slice(0, 6);
  const max = top.length ? top[0].revenue : 1;

  q("topServicesList").innerHTML = top.map(item => `
    <div class="top-service-row" onclick="openTopServiceDetail('${item.bookingType}', '${item.serviceLabel}')">
      <div class="top-service-row-head">
        <span>${item.serviceLabel || "Unknown Service"}</span>
        <span>RM ${item.revenue.toLocaleString("en-MY")}</span>
      </div>
      <div class="top-service-bar-track">
        <div class="top-service-bar" style="width:${Math.max((item.revenue / max) * 100, 4)}%;"></div>
      </div>
    </div>
  `).join("");
}

function openPendingTasksDetail() {
  const queue = buildActionQueue("all");
  const rows = queue.map(item => renderDetailRow({
    title: item.type,
    sub: `${item.detail} · ${item.time}`,
    tag: item.statusKey,
    tagLabel: item.statusLabel,
    sla: item.sla,
    actionLabel: item.actionLabel,
    actionOnclick: item.actionOnclick
  })).join("");
  openDetailModal("Pending Tasks", `${queue.length} item(s) requiring attention today.`, rows, { label: "Open Daily Overview", href: "dailyoverview.html" });
}

function openOpsHighlightDetail(key) {
  if (key === "pendingTasks") return openPendingTasksDetail();

  if (key === "slaCompliance") {
    // Only grooming-service SLA is real — your messages/loyalty tables have
    // no pending/SLA concept at all (see enquiries.html/loyalty.html).
    const pendingGrooming = bookings.filter(b => b.bookingType === "grooming" && b.date === today && b.status === "pending");
    const rows = pendingGrooming.map(bookingDetailRow).join("");
    openDetailModal("SLA Compliance", `${pendingGrooming.length} item(s) currently tracked against SLA.`, rows, { label: "Open Daily Overview", href: "dailyoverview.html" });
    return;
  }

  const label = periodRangeLabel(currentDashboardPeriod, dashboardRange);

  if (key === "noShow") {
    const items = bookings.filter(b => b.status === "no_show" && b.date >= dashboardRange.start && b.date <= dashboardRange.end);
    openDetailModal("No-Show Bookings", `${items.length} booking(s) marked as no-show · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
  } else if (key === "completionRate") {
    const items = bookings.filter(b => b.date >= dashboardRange.start && b.date <= dashboardRange.end).sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
    openDetailModal("Completion Status", `${items.length} booking(s) · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
  }
}

function renderOpsHighlights(metrics) {
  const cards = [
    { key: "pendingTasks", icon: "list-view.png", label: "Pending Tasks", value: metrics.pendingTasks, sub: "Needs action now", tone: "neutral" },
    { key: "slaCompliance", icon: "time.png", label: "SLA Compliance", value: `${metrics.slaCompliance.rate}%`, sub: metrics.slaCompliance.breaches ? `${metrics.slaCompliance.breaches} breached now` : "All within SLA", tone: metrics.slaCompliance.rate < 80 ? "down" : "up" },
    { key: "noShow", icon: "notification-bell.png", label: "No-Show Rate", value: `${metrics.noShowRate.toFixed(1)}%`, sub: "this period", tone: metrics.noShowRate > 8 ? "down" : "up" },
    { key: "completionRate", icon: "confirm-circle.png", label: "Completion Rate", value: `${Math.round(metrics.completionRate)}%`, sub: "this period", tone: metrics.completionRate >= 50 ? "up" : "neutral" }
  ];

  q("opsHighlightGrid").innerHTML = cards.map(c => `
    <div class="ops-highlight-card" onclick="openOpsHighlightDetail('${c.key}')">
      <div class="ops-highlight-icon"><img src="icon/${c.icon}" alt="" class="ops-icon-img"></div>
      <span class="ops-highlight-label">${c.label}</span>
      <span class="ops-highlight-value">${c.value}</span>
      <span class="ops-highlight-sub ${c.tone}">${c.sub}</span>
    </div>
  `).join("");
}

function openSnapshotDetail(key) {
  if (key === "members") {
    const rows = customers.map(c => renderDetailRow({ title: c.full_name, sub: `${c.customer_id} · ${c.phone_number || "—"}`, tag: "done", tagLabel: "Active" })).join("");
    openDetailModal("Active Members", `${customers.length} registered customer(s).`, rows, { label: "Open CRM", href: "profile.html" });
  } else if (key === "pets") {
    const rows = pets.map(p => renderDetailRow({ title: `${p.pet_name} (${p.pet_type})`, sub: `Owner: ${getCustomerById(p.customer_id)?.full_name || "Unknown"}`, tag: "done", tagLabel: p.pet_type })).join("");
    openDetailModal("Total Pets", `${pets.length} registered pet(s).`, rows, { label: "Open CRM", href: "profile.html" });
  } else if (key === "staff") {
    const rows = staff.map(s => {
      const status = getStaffDutyStatus(s.staff_id, today);
      const tagLabel = status === "duty" ? "On Duty" : status === "leave" ? "On Leave" : "Off Today";
      const tag = status === "duty" ? "done" : status === "leave" ? "no_show" : "off";
      return renderDetailRow({ title: s.staff_name, sub: s.role || "Staff", tag, tagLabel });
    }).join("");
    const onDutyCount = staff.filter(s => isStaffOnDuty(s.staff_id, today)).length;
    openDetailModal("Staff On Duty", `${onDutyCount} of ${staff.length} staff member(s) on duty today.`, rows, { label: "Open Staff Management", href: "staff.html" });
  }
}

function renderSnapshot() {
  const rows = [
    { key: "members", icon: "users.png", label: "Active Members", value: customers.length.toLocaleString("en-MY") },
    { key: "pets", icon: "paw-print.png", label: "Total Pets", value: pets.length.toLocaleString("en-MY") },
    { key: "staff", icon: "team.png", label: "Staff On Duty Today", value: staff.filter(s => isStaffOnDuty(s.staff_id, today)).length },
    { key: null, icon: "time.png", label: "Operating Hours", value: "8:00 AM – 8:00 PM" }
  ];

  q("snapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row ${r.key ? "clickable" : ""}" ${r.key ? `onclick="openSnapshotDetail('${r.key}')"` : ""}>
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

const LOYALTY_TIER_COLORS = { Platinum: "#7C3AED", Gold: "#D97706", Silver: "#64748B", Bronze: "#B45309" };

function openLoyaltyTierDetail(tier) {
  const members = buildLoyaltyMembers().filter(m => m.tier === tier);
  const rows = members.map(m => renderDetailRow({
    title: m.full_name,
    sub: `${m.member_id} · ${m.phone}`,
    tag: tier.toLowerCase(),
    tagLabel: `${m.points.toLocaleString()} pts`
  })).join("");
  openDetailModal(`${tier} Members`, `${members.length} member(s)`, rows, { label: "Open Loyalty Page", href: "loyalty.html" });
}

function renderLoyaltyMemberStatus() {
  const members = buildLoyaltyMembers();
  const total = members.length;
  const mix = ["Platinum", "Gold", "Silver", "Bronze"].map(tier => ({
    tier, count: members.filter(m => m.tier === tier).length
  }));

  q("loyaltyTierStackedBar").innerHTML = mix.map(m => {
    const pct = total ? (m.count / total) * 100 : 0;
    return pct > 0 ? `<div class="stacked-bar-segment" style="width:${pct}%;background:${LOYALTY_TIER_COLORS[m.tier]};cursor:pointer;" onclick="openLoyaltyTierDetail('${m.tier}')"></div>` : "";
  }).join("");

  q("loyaltyTierBreakdownList").innerHTML = mix.map(m => {
    const pct = total ? Math.round((m.count / total) * 100) : 0;
    return `
      <div class="schedule-breakdown-row" onclick="openLoyaltyTierDetail('${m.tier}')">
        <span class="legend-swatch" style="background:${LOYALTY_TIER_COLORS[m.tier]};"></span>
        <span>${m.tier}</span>
        <span class="schedule-breakdown-count">${m.count} · ${pct}%</span>
      </div>
    `;
  }).join("") + `
    <div class="schedule-breakdown-total">
      <span>Total Members</span>
      <span>${total} · 100%</span>
    </div>
  `;
}

function renderAnalyticsDashboard() {
  const metrics = computeDashboardMetrics(currentDashboardPeriod);

  const weekChip = q("dashboardWeekChip");
  if (weekChip) weekChip.innerHTML = formatPeriodChip(metrics.period, metrics.range);

  const periodLabel = periodRangeLabel(metrics.period, metrics.range);
  const granularity = { daily: "Hourly", weekly: "Daily", monthly: "Weekly" }[metrics.period];
  const trendSub = `${granularity} · ${periodLabel}`;
  const revenueTrendSub = q("revenueTrendSub");
  if (revenueTrendSub) revenueTrendSub.textContent = trendSub;
  const customerGrowthSub = q("customerGrowthSub");
  if (customerGrowthSub) customerGrowthSub.textContent = trendSub;
  const topServicesSub = q("topServicesSub");
  if (topServicesSub) topServicesSub.textContent = periodLabel;
  const serviceMixSub = q("serviceMixSub");
  if (serviceMixSub) serviceMixSub.textContent = periodLabel;
  const loyaltyStatusSub = q("loyaltyStatusSub");
  if (loyaltyStatusSub) loyaltyStatusSub.textContent = "All-time";

  renderKpiHeroGrid(metrics);

  const trendSeries = computeTrendSeries(metrics.period, metrics.range);
  const trendLabels = trendSeries.map(s => s.label);

  renderAreaTrendChart("revenueTrendChart", trendSeries.map(s => s.revenue), {
    color: "#3B82F6", labels: trendLabels, format: formatCurrency, onPointClick: openTrendBucketDetail
  });

  renderServiceMixDonut(metrics.serviceMix);
  renderTopServices(metrics.topServices);

  renderDualLineChart(
    "customerGrowthChart",
    trendSeries.map(s => s.newCustomers),
    trendSeries.map(s => s.returningCustomers),
    { colorA: "#3B82F6", colorB: "#10B981", labelA: "New Customers", labelB: "Returning Customers", labels: trendLabels, format: v => `${v}`, onPointClick: openTrendBucketDetail }
  );

  renderOpsHighlights(metrics);
  renderSnapshot();
  renderLoyaltyMemberStatus();

  renderStaffDashboard();
  renderSystemDashboard();
}

/* ==========================================================================
   STAFF & SYSTEM DASHBOARDS (dashboard.html)
   Mirror the Operation tab's layout (KPI hero → 2 chart rows → ops grid)
   using the same period/date-range state. Staff numbers are all real,
   derived from the shared staff/booking/leave data. System numbers are real
   where a real source exists (enquiries, payments, loyalty); anything with
   no real backing (AI accuracy, uptime, response time) is clearly marked
   "Illustrative" and left non-clickable, same convention as the rest of
   this dashboard.
   ========================================================================== */

const STAFF_DUTY_COLORS = { duty: "#059669", off: "#94A3B8", leave: "#DC2626" };

function renderStaffDashboard() {
  const metrics = computeDashboardMetrics(currentDashboardPeriod);
  const todayDate = getToday();
  const periodLabel = periodRangeLabel(metrics.period, metrics.range);
  const granularity = { daily: "Hourly", weekly: "Daily", monthly: "Weekly" }[metrics.period];

  const workloadSub = q("staffWorkloadTrendSub");
  if (workloadSub) workloadSub.textContent = `${granularity} · ${periodLabel}`;
  const bookingRankSub = q("staffBookingRankSub");
  if (bookingRankSub) bookingRankSub.textContent = periodLabel;

  renderStaffKpiHero(metrics, todayDate);
  renderStaffWorkloadTrend(metrics);
  renderStaffBookingRanking(metrics);
  renderStaffDutyDonut(todayDate);
  renderStaffLeaveSnapshot(todayDate);
  renderStaffHighlights(metrics, todayDate);
  renderStaffRoleSnapshot();
  renderStaffWeekDutyMix();
}

function renderStaffKpiHero(metrics, todayDate) {
  const onDuty = staff.filter(s => isStaffOnDuty(s.staff_id, todayDate)).length;
  const onLeave = staff.filter(s => isStaffOnLeave(s.staff_id, todayDate)).length;

  const cards = [
    { icon: "team.png", label: "Total Staff", value: staff.length, sub: "Registered team members" },
    { icon: "confirm-circle.png", label: "On Duty Today", value: onDuty, sub: `of ${staff.length} staff` },
    { icon: "logout.png", label: "On Leave Today", value: onLeave, sub: onLeave ? "Approved leave" : "None today" },
    { icon: "calendar-simple.png", label: "Bookings Handled", value: metrics.periodBookings.length.toLocaleString("en-MY"), sub: "this period" }
  ];

  q("staffKpiHeroGrid").innerHTML = cards.map(c => `
    <div class="kpi-hero-card">
      <div class="kpi-hero-top">
        <div class="kpi-hero-icon"><img src="icon/${c.icon}" alt="" class="kpi-icon-img"></div>
        <div>
          <div class="kpi-hero-label">${c.label}</div>
          <div class="kpi-hero-value">${c.value}</div>
        </div>
      </div>
      <div class="kpi-hero-delta">${c.sub}</div>
    </div>
  `).join("");
}

function renderStaffWorkloadTrend(metrics) {
  const buckets = buildTrendBuckets(metrics.period, metrics.range);
  const values = buckets.map(b => bookings.filter(item => b.matches(item) && item.status !== "cancelled" && item.status !== "no_show").length);
  const labels = buckets.map(b => b.label);

  renderAreaTrendChart("staffWorkloadTrendChart", values, {
    color: "#3B82F6", labels, format: v => `${v} booking(s)`, onPointClick: openTrendBucketDetail
  });
}

function openStaffBookingDetail(staffId) {
  const items = bookings
    .filter(b => String(b.staffId) === String(staffId) && b.date >= dashboardRange.start && b.date <= dashboardRange.end)
    .sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
  const member = findRealStaff(staffId);
  const label = periodRangeLabel(currentDashboardPeriod, dashboardRange);
  openDetailModal(`${member?.staff_name || "Staff"} — Bookings`, `${items.length} booking(s) · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Staff Management", href: "staff.html" });
}

function renderStaffBookingRanking(metrics) {
  const ranked = staff
    .map(s => ({ staff: s, count: metrics.periodBookings.filter(b => String(b.staffId) === String(s.staff_id)).length }))
    .sort((a, b) => b.count - a.count);

  const max = ranked.length && ranked[0].count ? ranked[0].count : 1;

  q("staffBookingRankList").innerHTML = ranked.map(r => `
    <div class="top-service-row" onclick="openStaffBookingDetail(${r.staff.staff_id})">
      <div class="top-service-row-head">
        <span>${r.staff.staff_name}</span>
        <span>${r.count} booking(s)</span>
      </div>
      <div class="top-service-bar-track">
        <div class="top-service-bar" style="width:${Math.max((r.count / max) * 100, 4)}%;"></div>
      </div>
    </div>
  `).join("");
}

function openStaffDutyDetail(key) {
  const todayDate = getToday();
  const members = staff.filter(s => getStaffDutyStatus(s.staff_id, todayDate) === key);
  const label = key === "duty" ? "On Duty" : key === "leave" ? "On Leave" : "Off Today";
  const tag = key === "duty" ? "done" : key === "leave" ? "no_show" : "off";
  const rows = members.map(s => renderDetailRow({ title: s.staff_name, sub: s.role, tag, tagLabel: label })).join("");
  openDetailModal(`Staff ${label}`, `${members.length} of ${staff.length} staff member(s).`, rows, { label: "Open Staff Management", href: "staff.html" });
}

function renderStaffDutyDonut(todayDate) {
  const counts = { duty: 0, off: 0, leave: 0 };
  staff.forEach(s => { counts[getStaffDutyStatus(s.staff_id, todayDate)]++; });

  const segments = [
    { key: "duty", label: "On Duty", count: counts.duty, color: STAFF_DUTY_COLORS.duty },
    { key: "off", label: "Off Today", count: counts.off, color: STAFF_DUTY_COLORS.off },
    { key: "leave", label: "On Leave", count: counts.leave, color: STAFF_DUTY_COLORS.leave }
  ];
  const total = staff.length || 1;

  q("staffDutyDonutLegend").innerHTML = segments.map(s => `
    <div class="chart-legend-item" onclick="openStaffDutyDetail('${s.key}')">
      <span class="legend-swatch" style="background:${s.color};"></span>
      <span>${s.label} ${s.count} (${Math.round((s.count / total) * 100)}%)</span>
    </div>
  `).join("");

  renderInteractiveDonut({
    chartElId: "staffDutyDonut",
    totalElId: "staffDutyDonutTotal",
    legendElId: "staffDutyDonutLegend",
    segments,
    onSegmentClick: openStaffDutyDetail
  });
}

function openStaffLeaveSnapshotDetail(key) {
  let items, title;
  if (key === "pending") { items = leaveRequests.filter(lv => lv.status === "pending"); title = "Pending Leave Requests"; }
  else if (key === "approved") { items = leaveRequests.filter(lv => lv.status === "approved"); title = "Approved Leave"; }
  else { items = leaveRequests; title = "All Leave Applications"; }

  const rows = items.map(lv => renderDetailRow({
    title: lv.staffName,
    sub: `${lv.startDate === lv.endDate ? formatDate(lv.startDate) : `${formatDate(lv.startDate)} – ${formatDate(lv.endDate)}`} · ${lv.reason}`,
    tag: lv.status === "approved" ? "done" : "pending",
    tagLabel: lv.status === "approved" ? "Approved" : "Pending"
  })).join("");
  openDetailModal(title, `${items.length} record(s).`, rows, { label: "Open Staff Management", href: "staff.html" });
}

function renderStaffLeaveSnapshot(todayDate) {
  const pendingCount = leaveRequests.filter(lv => lv.status === "pending").length;
  const approvedCount = leaveRequests.filter(lv => lv.status === "approved").length;

  const rows = [
    { key: "pending", icon: "time.png", label: "Pending Requests", value: pendingCount },
    { key: "approved", icon: "confirm-circle.png", label: "Approved Requests", value: approvedCount },
    { key: "all", icon: "list-view.png", label: "Total Applications", value: leaveRequests.length },
    { key: null, icon: "logout.png", label: "On Leave Today", value: staff.filter(s => isStaffOnLeave(s.staff_id, todayDate)).length }
  ];

  q("staffLeaveSnapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row ${r.key ? "clickable" : ""}" ${r.key ? `onclick="openStaffLeaveSnapshotDetail('${r.key}')"` : ""}>
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

function openStaffHighlightDetail(key) {
  if (key === "pendingLeave") return openStaffLeaveSnapshotDetail("pending");
  if (key === "onDutyRate") return openStaffDutyDetail("duty");

  const metrics = computeDashboardMetrics(currentDashboardPeriod);
  const rows = staff
    .map(s => ({ s, count: metrics.periodBookings.filter(b => String(b.staffId) === String(s.staff_id)).length }))
    .sort((a, b) => b.count - a.count)
    .map(r => renderDetailRow({ title: r.s.staff_name, sub: r.s.role, tag: "done", tagLabel: `${r.count} booking(s)` }))
    .join("");
  openDetailModal("Bookings per Staff", periodRangeLabel(currentDashboardPeriod, dashboardRange), rows, { label: "Open Staff Management", href: "staff.html" });
}

function renderStaffHighlights(metrics, todayDate) {
  const ranked = staff
    .map(s => ({ staff: s, count: metrics.periodBookings.filter(b => String(b.staffId) === String(s.staff_id)).length }))
    .sort((a, b) => b.count - a.count);
  const busiest = ranked[0];
  const activeStaffCount = staff.filter(s => isStaffOnDuty(s.staff_id, todayDate)).length;
  const avgBookings = activeStaffCount ? Math.round((getActiveStaffBookingVolume(todayDate) / activeStaffCount) * 10) / 10 : 0;
  const pendingLeaveCount = leaveRequests.filter(lv => lv.status === "pending").length;
  const onDutyRate = staff.length ? Math.round((activeStaffCount / staff.length) * 100) : 0;

  const cards = [
    { key: "busiest", icon: "bar-chart.png", label: "Busiest Staff", value: busiest?.staff.staff_name || "—", sub: `${busiest?.count || 0} booking(s) this period`, tone: "neutral" },
    { key: "avgLoad", icon: "line-chart.png", label: "Avg Bookings / Active Staff", value: avgBookings, sub: "today", tone: "neutral" },
    { key: "pendingLeave", icon: "time.png", label: "Pending Leave Requests", value: pendingLeaveCount, sub: pendingLeaveCount ? "Needs review" : "All clear", tone: pendingLeaveCount ? "down" : "up" },
    { key: "onDutyRate", icon: "confirm-circle.png", label: "On-Duty Rate Today", value: `${onDutyRate}%`, sub: "of total staff", tone: "neutral" }
  ];

  q("staffHighlightGrid").innerHTML = cards.map(c => `
    <div class="ops-highlight-card" onclick="openStaffHighlightDetail('${c.key}')">
      <div class="ops-highlight-icon"><img src="icon/${c.icon}" alt="" class="ops-icon-img"></div>
      <span class="ops-highlight-label">${c.label}</span>
      <span class="ops-highlight-value">${c.value}</span>
      <span class="ops-highlight-sub ${c.tone}">${c.sub}</span>
    </div>
  `).join("");
}

function openStaffRoleDetail(role) {
  const members = staff.filter(s => s.role === role);
  const rows = members.map(s => renderDetailRow({ title: s.staff_name, sub: s.email || s.phone || "", tag: "done", tagLabel: role })).join("");
  openDetailModal(`${role}s`, `${members.length} ${role.toLowerCase()}(s).`, rows, { label: "Open Staff Management", href: "staff.html" });
}

function renderStaffRoleSnapshot() {
  // Your real staff table only has 2 roles (Manager/Staff) — not the mock's
  // Manager/Groomer/Caretaker.
  const roleMeta = { Manager: "team.png", Staff: "paw-print.png" };
  const rows = Object.keys(roleMeta).map(role => ({
    key: role, icon: roleMeta[role], label: `${role}s`, value: staff.filter(s => s.role === role).length
  }));
  rows.push({ key: null, icon: "team.png", label: "Total Staff", value: staff.length });

  q("staffRoleSnapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row ${r.key ? "clickable" : ""}" ${r.key ? `onclick="openStaffRoleDetail('${r.key}')"` : ""}>
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

function renderStaffWeekDutyMix() {
  const weekStart = getStartOfWeek(getToday());
  const dates = getDateRange(weekStart, addDays(weekStart, 6));
  const weekMixSub = q("staffWeekMixSub");
  if (weekMixSub) weekMixSub.textContent = `${formatShortDate(weekStart)} – ${formatShortDate(addDays(weekStart, 6))}`;

  const counts = { duty: 0, off: 0, leave: 0 };
  staff.forEach(s => dates.forEach(d => { counts[getStaffDutyStatus(s.staff_id, d)]++; }));

  const total = staff.length * dates.length;
  const mix = [
    { label: "Duty", count: counts.duty, color: STAFF_DUTY_COLORS.duty },
    { label: "Off", count: counts.off, color: STAFF_DUTY_COLORS.off },
    { label: "Leave", count: counts.leave, color: STAFF_DUTY_COLORS.leave }
  ];

  q("staffWeekMixBar").innerHTML = mix.map(m => {
    const pct = total ? (m.count / total) * 100 : 0;
    return pct > 0 ? `<div class="stacked-bar-segment" style="width:${pct}%;background:${m.color};"></div>` : "";
  }).join("");

  q("staffWeekMixList").innerHTML = mix.map(m => {
    const pct = total ? Math.round((m.count / total) * 100) : 0;
    return `
      <div class="schedule-breakdown-row">
        <span class="legend-swatch" style="background:${m.color};"></span>
        <span>${m.label}</span>
        <span class="schedule-breakdown-count">${m.count} · ${pct}%</span>
      </div>
    `;
  }).join("") + `
    <div class="schedule-breakdown-total">
      <span>Total Staff-Days</span>
      <span>${total} · 100%</span>
    </div>
  `;
}

function openSystemKpiDetail(key) {
  const label = periodRangeLabel(currentDashboardPeriod, dashboardRange);
  if (key === "bookingIntent") {
    const items = realMessages.filter(m => m.intent_label === "booking");
    const rows = items.map(m => renderDetailRow({
      title: m.customerName, sub: `"${m.message_text}"`, tag: "done", tagLabel: "Booking"
    })).join("");
    openDetailModal("Booking-Related Messages", `${items.length} of ${realMessages.length} messages.`, rows, { label: "Open Enquiries", href: "enquiries.html" });
  } else if (key === "payment") {
    const rows = realPayments.map(p => renderDetailRow({
      title: p.customerName || p.service, sub: `PAY-${String(p.payment_id).padStart(4, "0")} · RM ${Number(p.final_amount).toLocaleString()}`,
      tag: p.status === "Paid" ? "done" : "pending", tagLabel: p.status
    })).join("");
    const verified = realPayments.filter(p => p.status === "Paid").length;
    openDetailModal("Payment Verification", `${verified} of ${realPayments.length} verified · ${label}`, rows, { label: "Open Payment", href: "payment.html" });
  } else if (key === "redemption") {
    const withRedemptions = realMembers.filter(m => m.redemption_made > 0);
    const rows = withRedemptions.map(m => renderDetailRow({
      title: m.customerName, sub: `${m.tier} · ${m.points_balance} pts`, tag: "done", tagLabel: `${m.redemption_made} redemption(s)`
    })).join("");
    openDetailModal("Members With Redemptions", `${withRedemptions.length} of ${realMembers.length} members have redeemed at least once.`, rows, { label: "Open Loyalty", href: "loyalty.html" });
  }
}

function renderSystemKpiHero() {
  const totalMessages = realMessages.length;
  const bookingMessages = realMessages.filter(m => m.intent_label === "booking").length;
  const totalPayments = realPayments.length;
  const verifiedPayments = realPayments.filter(p => p.status === "Paid").length;
  const totalMembers = realMembers.length;
  const membersWithRedemptions = realMembers.filter(m => m.redemption_made > 0).length;

  const cards = [
    { key: "bookingIntent", icon: "chat-message.png", label: "Booking-Related Messages", value: `${totalMessages ? Math.round((bookingMessages / totalMessages) * 100) : 0}%`, sub: `${bookingMessages}/${totalMessages} messages`, clickable: true },
    { key: "payment", icon: "payment-card.png", label: "Payment Verification Rate", value: `${totalPayments ? Math.round((verifiedPayments / totalPayments) * 100) : 0}%`, sub: `${verifiedPayments}/${totalPayments} verified`, clickable: true },
    { key: "redemption", icon: "loyalty-reward-gift.png", label: "Members With Redemptions", value: `${totalMembers ? Math.round((membersWithRedemptions / totalMembers) * 100) : 0}%`, sub: `${membersWithRedemptions}/${totalMembers} members`, clickable: true },
    { key: null, icon: "analytics-dashboard.png", label: "AI Response Accuracy", value: "96.4%", sub: "Illustrative", clickable: false }
  ];

  q("systemKpiHeroGrid").innerHTML = cards.map(c => `
    <div class="kpi-hero-card" ${c.clickable ? `onclick="openSystemKpiDetail('${c.key}')"` : `style="cursor:default;"`}>
      <div class="kpi-hero-top">
        <div class="kpi-hero-icon"><img src="icon/${c.icon}" alt="" class="kpi-icon-img"></div>
        <div>
          <div class="kpi-hero-label">${c.label}</div>
          <div class="kpi-hero-value">${c.value}</div>
        </div>
      </div>
      <div class="kpi-hero-delta">${c.sub}</div>
    </div>
  `).join("");
}

function renderSystemAutomationTrend(metrics) {
  const buckets = buildTrendBuckets(metrics.period, metrics.range);
  const values = buckets.map((b, idx) => Math.round((95 + Math.sin(idx * 1.3) * 2.2 + Math.cos(idx * 0.7)) * 10) / 10);
  const labels = buckets.map(b => b.label);

  renderAreaTrendChart("systemAutomationTrendChart", values, {
    color: "#7C3AED", labels, format: v => `${v}%`
  });
}

function renderSystemPendingByType() {
  // Your real messages/loyalty tables have no pending workflow at all (see
  // enquiries.html/loyalty.html conversions) — only these 3 categories have
  // a real "pending" concept, so the other 2 mock categories are dropped
  // rather than shown as a fabricated zero.
  const todayDate = getToday();
  const items = [
    { label: "Pending Grooming Today", count: bookings.filter(b => b.bookingType === "grooming" && b.date === todayDate && b.status === "pending").length, href: "dailyoverview.html" },
    { label: "Pending Payment Verification", count: realPayments.filter(p => p.status === "Pending").length, href: "payment.html" },
    { label: "Pending Leave Requests", count: leaveRequests.filter(lv => lv.status === "pending").length, href: "staff.html" }
  ].sort((a, b) => b.count - a.count);

  const max = items.length && items[0].count ? items[0].count : 1;

  q("systemPendingByTypeList").innerHTML = items.map(item => `
    <div class="top-service-row" onclick="location.href='${item.href}'">
      <div class="top-service-row-head">
        <span>${item.label}</span>
        <span>${item.count}</span>
      </div>
      <div class="top-service-bar-track">
        <div class="top-service-bar" style="width:${Math.max((item.count / max) * 100, 4)}%;"></div>
      </div>
    </div>
  `).join("");
}

const MESSAGE_INTENT_COLORS = { booking: "#3B82F6", policy: "#10B981", loyalty: "#D97706" };

function openSystemEnquiryDetail(intent) {
  const items = realMessages.filter(m => m.intent_label === intent);
  const rows = items.map(m => renderDetailRow({ title: m.customerName, sub: `"${m.message_text}"`, tag: "done", tagLabel: intent })).join("");
  openDetailModal(`${intent.charAt(0).toUpperCase()}${intent.slice(1)} Messages`, `${items.length} message(s).`, rows, { label: "Open Enquiries", href: "enquiries.html" });
}

function renderSystemEnquiryDonut() {
  // Your real messages table has no resolved/pending status at all (see
  // enquiries.html) — this shows the real intent breakdown instead.
  const total = realMessages.length || 1;
  const segments = ["booking", "policy", "loyalty"].map(intent => ({
    key: intent, label: intent.charAt(0).toUpperCase() + intent.slice(1),
    count: realMessages.filter(m => m.intent_label === intent).length,
    color: MESSAGE_INTENT_COLORS[intent]
  }));

  q("systemEnquiryDonutLegend").innerHTML = segments.map(s => `
    <div class="chart-legend-item" onclick="openSystemEnquiryDetail('${s.key}')">
      <span class="legend-swatch" style="background:${s.color};"></span>
      <span>${s.label} ${s.count} (${Math.round((s.count / total) * 100)}%)</span>
    </div>
  `).join("");

  renderInteractiveDonut({
    chartElId: "systemEnquiryDonut",
    totalElId: "systemEnquiryDonutTotal",
    legendElId: "systemEnquiryDonutLegend",
    segments,
    onSegmentClick: openSystemEnquiryDetail
  });
}

function renderSystemIntegrationSnapshot() {
  const rows = [
    { icon: "chat-message.png", label: "WhatsApp Business API", value: "Connected" },
    { icon: "payment-card.png", label: "Payment Gateway", value: "Connected" },
    { icon: "calendar-simple.png", label: "Booking Calendar Sync", value: "Active" },
    { icon: "time.png", label: "Last Sync", value: "Just now" }
  ];

  q("systemIntegrationSnapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row">
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

function openMessagesLoggedDetail() {
  const rows = realMessages.map(m => renderDetailRow({ title: m.customerName, sub: `"${m.message_text}"`, tag: "done", tagLabel: m.intent_label })).join("");
  openDetailModal("Messages Logged", `${realMessages.length} inbound message(s), all-time.`, rows, { label: "Open Enquiries", href: "enquiries.html" });
}

function renderSystemHighlights() {
  const cards = [
    { icon: "confirm-circle.png", label: "API Uptime", value: "99.9%", sub: "Illustrative", clickable: false },
    { icon: "time.png", label: "Avg AI Response Time", value: "1.2s", sub: "Illustrative", clickable: false },
    { icon: "confirm-circle.png", label: "Messages Logged", value: realMessages.length, sub: "All-time", clickable: true, onclick: "openMessagesLoggedDetail()" },
    { icon: "notification-bell.png", label: "Error Rate", value: "0.3%", sub: "Illustrative", clickable: false }
  ];

  q("systemHighlightGrid").innerHTML = cards.map(c => `
    <div class="ops-highlight-card" ${c.clickable ? `onclick="${c.onclick}"` : `style="cursor:default;"`}>
      <div class="ops-highlight-icon"><img src="icon/${c.icon}" alt="" class="ops-icon-img"></div>
      <span class="ops-highlight-label">${c.label}</span>
      <span class="ops-highlight-value">${c.value}</span>
      <span class="ops-highlight-sub">${c.sub}</span>
    </div>
  `).join("");
}

function renderSystemQueueSnapshot() {
  const todayDate = getToday();
  const rows = [
    { icon: "grooming-scissors.png", label: "Pending Grooming Today", value: bookings.filter(b => b.bookingType === "grooming" && b.date === todayDate && b.status === "pending").length, href: "dailyoverview.html" },
    { icon: "payment-card.png", label: "Pending Payment Verification", value: realPayments.filter(p => p.status === "Pending").length, href: "payment.html" },
    { icon: "list-view.png", label: "Pending Leave Requests", value: leaveRequests.filter(lv => lv.status === "pending").length, href: "staff.html" }
  ];

  q("systemQueueSnapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row clickable" onclick="location.href='${r.href}'">
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

const SYSTEM_QUEUE_COLORS = { grooming: "#3B82F6", payment: "#7C3AED", leave: "#DC2626" };

function renderSystemQueueMix() {
  // Only these 3 categories have a real pending concept — your
  // messages/loyalty tables have no pending workflow at all.
  const todayDate = getToday();
  const labels = { grooming: "Grooming", payment: "Payment", leave: "Leave" };
  const counts = {
    grooming: bookings.filter(b => b.bookingType === "grooming" && b.date === todayDate && b.status === "pending").length,
    payment: realPayments.filter(p => p.status === "Pending").length,
    leave: leaveRequests.filter(lv => lv.status === "pending").length
  };

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const mix = Object.keys(counts).map(key => ({ label: labels[key], count: counts[key], color: SYSTEM_QUEUE_COLORS[key] }));

  q("systemQueueMixBar").innerHTML = mix.map(m => {
    const pct = total ? (m.count / total) * 100 : 0;
    return pct > 0 ? `<div class="stacked-bar-segment" style="width:${pct}%;background:${m.color};"></div>` : "";
  }).join("");

  q("systemQueueMixList").innerHTML = mix.map(m => {
    const pct = total ? Math.round((m.count / total) * 100) : 0;
    return `
      <div class="schedule-breakdown-row">
        <span class="legend-swatch" style="background:${m.color};"></span>
        <span>${m.label}</span>
        <span class="schedule-breakdown-count">${m.count} · ${pct}%</span>
      </div>
    `;
  }).join("") + `
    <div class="schedule-breakdown-total">
      <span>Total Pending</span>
      <span>${total} · 100%</span>
    </div>
  `;
}

function renderSystemDashboard() {
  const metrics = computeDashboardMetrics(currentDashboardPeriod);

  renderSystemKpiHero();
  renderSystemAutomationTrend(metrics);
  renderSystemPendingByType();
  renderSystemEnquiryDonut();
  renderSystemIntegrationSnapshot();
  renderSystemHighlights();
  renderSystemQueueSnapshot();
  renderSystemQueueMix();
}

async function initAnalyticsDashboard() {
  await loadRealDashboardData();

  document.querySelectorAll("#dashboardPeriodToggle .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#dashboardPeriodToggle .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentDashboardPeriod = btn.dataset.period;
      dashboardAnchorDate = today;
      renderAnalyticsDashboard();
    });
  });

  q("dashboardPrevBtn")?.addEventListener("click", () => shiftDashboardAnchor(-1));
  q("dashboardNextBtn")?.addEventListener("click", () => shiftDashboardAnchor(1));
  q("dashboardTodayBtn")?.addEventListener("click", () => { dashboardAnchorDate = today; renderAnalyticsDashboard(); });

  document.querySelectorAll("#dashboardSectionTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#dashboardSectionTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      q("dashboardOperationPanel").classList.toggle("hidden", btn.dataset.section !== "operation");
      q("dashboardStaffPanel").classList.toggle("hidden", btn.dataset.section !== "staff");
      q("dashboardSystemPanel").classList.toggle("hidden", btn.dataset.section !== "system");
    });
  });

  q("detailModal")?.addEventListener("click", event => {
    if (event.target.id === "detailModal") closeDetailModal();
  });

  renderAnalyticsDashboard();
}

/* ==========================================================================
   SETTINGS (setting.html)
   Business-configuration form ported from reg-setup.html, reorganised into
   tabs. Persisted per business under pawfect_business_settings_<businessKey>
   so changes survive a reload; editing the business name also propagates
   into the account records so the sidebar/dashboard pick it up immediately.
   ========================================================================== */

const SETTINGS_DEFAULTS = {
  logoName: "",
  businessName: "", country: "Malaysia", street: "", city: "", state: "", zip: "", description: "",
  hours: { Mon: "09:30 AM – 06:30 PM", Tue: "09:30 AM – 06:30 PM", Wed: "09:30 AM – 06:30 PM", Thu: "09:30 AM – 06:30 PM", Fri: "09:30 AM – 06:30 PM", Sat: "10:00 AM – 05:00 PM", Sun: "Closed" },
  closedDates: "",
  payment: { cash: true, card: true, qr: true, online: false },
  invoicePrefix: "PAW", taxName: "SST 6%",
  language: "English", timezone: "Kuala Lumpur (GMT+8)", currency: "MYR (RM)", weightUnit: "kg", heightUnit: "cm",
  policies: {},
  bookingUrl: "", whatsapp: "", confirmRule: "Auto-confirm if slot is available"
};

const SERVICE_META = {
  grooming: { icon: "grooming-scissors.png", label: "Grooming" },
  boarding: { icon: "boarding.png", label: "Boarding / Hotel" },
  daycare: { icon: "dog-play.png", label: "Daycare" }
};

function settingsStorageKey() {
  const account = getCurrentAccount();
  return "pawfect_business_settings_" + (account?.businessKey || "default");
}

function loadBusinessSettings() {
  try {
    const raw = localStorage.getItem(settingsStorageKey());
    return raw ? { ...SETTINGS_DEFAULTS, ...JSON.parse(raw) } : { ...SETTINGS_DEFAULTS };
  } catch (e) {
    return { ...SETTINGS_DEFAULTS };
  }
}

function showFilename(input, targetId) {
  const el = q(targetId);
  if (el && input.files.length) el.innerHTML = `<img src="icon/upload.png" alt="" class="row-icon">${input.files[0].name}`;
}

function renderServicePolicyCards(account) {
  const activeServices = account?.services || ["grooming", "boarding", "daycare"];
  const settings = loadBusinessSettings();

  const textEl = q("selectedServicesText");
  if (textEl) textEl.innerHTML = activeServices.map(type => `<img src="icon/${SERVICE_META[type]?.icon || "paw-print.png"}" alt="" class="row-icon">${SERVICE_META[type]?.label || type}`).join(" · ");

  q("selectedServiceCards").innerHTML = activeServices.map(type => {
    const meta = SERVICE_META[type] || { icon: "paw-print.png", label: type };
    const savedName = settings.policies?.[type];
    return `
      <div class="service-config-card">
        <h3><img src="icon/${meta.icon}" alt="" class="card-icon">${meta.label}</h3>
        <p class="muted">Upload your ${meta.label.toLowerCase()} service policy document.</p>
        <label class="logo-upload-label" for="policy_${type}">
          <img src="icon/upload.png" alt="" class="upload-icon">
          <p id="policyFileName_${type}">${savedName ? `<img src="icon/upload.png" alt="" class="row-icon">${savedName}` : "Click to upload policy file<br>(PDF, TXT, DOCX — max 10 MB)"}</p>
        <input type="file" id="policy_${type}" accept=".pdf,.txt,.docx" onchange="showFilename(this,'policyFileName_${type}')">
        </label>
      </div>
    `;
  }).join("");
}

function populateSettingsForm(s, account) {
  q("cfg_businessName").value = s.businessName || account?.businessName || "";
  q("cfg_country").value = s.country;
  q("cfg_street").value = s.street;
  q("cfg_city").value = s.city;
  q("cfg_state").value = s.state;
  q("cfg_zip").value = s.zip;
  q("cfg_description").value = s.description;
  if (s.logoName) q("logoFileName").innerHTML = '<img src="icon/upload.png" alt="" class="row-icon">' + s.logoName;

  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(day => { q("hour_" + day).value = s.hours[day] || ""; });
  q("cfg_closedDates").value = s.closedDates;

  q("pay_cash").checked = !!s.payment.cash;
  q("pay_card").checked = !!s.payment.card;
  q("pay_qr").checked = !!s.payment.qr;
  q("pay_online").checked = !!s.payment.online;
  q("cfg_invoicePrefix").value = s.invoicePrefix;
  q("cfg_taxName").value = s.taxName;
  q("cfg_language").value = s.language;
  q("cfg_timezone").value = s.timezone;
  q("cfg_currency").value = s.currency;
  q("cfg_weight").value = s.weightUnit;
  q("cfg_height").value = s.heightUnit;

  q("cfg_bookingUrl").value = s.bookingUrl;
  q("cfg_whatsapp").value = s.whatsapp;
  q("cfg_confirmRule").value = s.confirmRule;
}

function collectServicePolicyNames() {
  const account = getCurrentAccount();
  const activeServices = account?.services || ["grooming", "boarding", "daycare"];
  const existing = loadBusinessSettings().policies || {};
  const result = {};
  activeServices.forEach(type => {
    const input = q("policy_" + type);
    result[type] = input?.files[0]?.name || existing[type] || "";
  });
  return result;
}

function collectBusinessSettings() {
  const existingLogo = loadBusinessSettings().logoName;
  return {
    logoName: q("cfg_logo").files[0]?.name || existingLogo || "",
    businessName: q("cfg_businessName").value.trim(),
    country: q("cfg_country").value,
    street: q("cfg_street").value,
    city: q("cfg_city").value,
    state: q("cfg_state").value,
    zip: q("cfg_zip").value,
    description: q("cfg_description").value,
    hours: {
      Mon: q("hour_Mon").value, Tue: q("hour_Tue").value, Wed: q("hour_Wed").value,
      Thu: q("hour_Thu").value, Fri: q("hour_Fri").value, Sat: q("hour_Sat").value, Sun: q("hour_Sun").value
    },
    closedDates: q("cfg_closedDates").value,
    payment: { cash: q("pay_cash").checked, card: q("pay_card").checked, qr: q("pay_qr").checked, online: q("pay_online").checked },
    invoicePrefix: q("cfg_invoicePrefix").value,
    taxName: q("cfg_taxName").value,
    language: q("cfg_language").value,
    timezone: q("cfg_timezone").value,
    currency: q("cfg_currency").value,
    weightUnit: q("cfg_weight").value,
    heightUnit: q("cfg_height").value,
    policies: collectServicePolicyNames(),
    bookingUrl: q("cfg_bookingUrl").value,
    whatsapp: q("cfg_whatsapp").value,
    confirmRule: q("cfg_confirmRule").value
  };
}

function saveBusinessSettings() {
  const settings = collectBusinessSettings();
  localStorage.setItem(settingsStorageKey(), JSON.stringify(settings));

  const account = getCurrentAccount();
  if (account && settings.businessName && settings.businessName !== account.businessName) {
    account.businessName = settings.businessName;
    localStorage.setItem("pawfect_current_account", JSON.stringify(account));

    const email = String(account.email || "").toLowerCase();
    if (email) {
      try {
        const stateRaw = localStorage.getItem("pawfect_account_state_" + email);
        const state = stateRaw ? JSON.parse(stateRaw) : {};
        state.businessName = settings.businessName;
        localStorage.setItem("pawfect_account_state_" + email, JSON.stringify(state));
      } catch (e) { /* ignore malformed stored state */ }
    }

    renderSidebarAccount();
  }

  const note = q("settingsSavedNote");
  if (note) {
    note.style.opacity = "1";
    clearTimeout(window.__settingsSavedTimer);
    window.__settingsSavedTimer = setTimeout(() => { note.style.opacity = "0"; }, 2200);
  }
}

function initSettings() {
  const account = getCurrentAccount();

  document.querySelectorAll("#settingsTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#settingsTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".settings-panel").forEach(panel => {
        panel.classList.toggle("hidden", panel.id !== `panel-${btn.dataset.panel}`);
      });
    });
  });

  renderServicePolicyCards(account);
  populateSettingsForm(loadBusinessSettings(), account);
  renderAccountsPanel();
}

/* =========================
   TEAM ACCOUNTS (Manager: full control · Staff: read-only)
========================= */

function customAccountsForSettings() {
  try { return JSON.parse(localStorage.getItem("pawfect_custom_accounts") || "[]"); }
  catch (e) { return []; }
}

function removedSeedAccountEmails() {
  try { return JSON.parse(localStorage.getItem("pawfect_removed_seed_accounts") || "[]"); }
  catch (e) { return []; }
}

function getBusinessAccounts(businessKey) {
  const removedSeeds = removedSeedAccountEmails();

  const seed = Object.values(DEMO_ACCOUNTS)
    .filter(a => a.businessKey === businessKey && !removedSeeds.includes(a.email.toLowerCase()))
    .map(a => ({ email: a.email, role: a.role, businessKey: a.businessKey, source: "seed" }));

  const custom = customAccountsForSettings()
    .filter(a => a.businessKey === businessKey)
    .map(a => ({ email: a.email, role: a.role, businessKey: a.businessKey, source: "custom" }));

  const byEmail = new Map();
  [...seed, ...custom].forEach(a => byEmail.set(a.email.toLowerCase(), a));
  return [...byEmail.values()];
}

function renderAccountsPanel() {
  const account = getCurrentAccount();
  const manager = isManager(account);

  q("accountsManagerView").classList.toggle("hidden", !manager);
  q("accountsStaffView").classList.toggle("hidden", manager);

  if (!manager) {
    q("staffOrgName").textContent = account?.businessName || "—";
    q("staffOrgRole").textContent = account?.role || "—";
    q("staffOrgEmail").textContent = account?.email || "—";
    return;
  }

  const nameEl = q("accountsBusinessName");
  if (nameEl) nameEl.textContent = account?.businessName || "this business";

  const list = getBusinessAccounts(account?.businessKey);
  const currentEmail = String(account?.email || "").toLowerCase();

  q("accountsTableBody").innerHTML = list.map(a => {
    const isSelf = a.email.toLowerCase() === currentEmail;
    return `
      <tr>
        <td>${a.email}${isSelf ? ' <span class="field-note">(you)</span>' : ""}</td>
        <td><span class="status-tag ${a.role === "Manager" ? "status-scheduled" : "status-done"}">${a.role}</span></td>
        <td>${a.source === "seed" ? "Default" : "Added"}</td>
        <td><button class="edit-btn" onclick="removeTeamAccount('${a.email}','${a.source}')">Remove</button></td>
      </tr>
    `;
  }).join("");
}

function addTeamAccount() {
  const account = getCurrentAccount();
  const email = q("newAccountEmail").value.trim().toLowerCase();
  const password = q("newAccountPassword").value;
  const role = q("newAccountRole").value;
  const note = q("accountsAddNote");

  if (!email || !password) {
    if (note) { note.textContent = "Enter an email and password."; note.style.color = "#DC2626"; }
    return;
  }

  const alreadyExists = getBusinessAccounts(account?.businessKey).some(a => a.email.toLowerCase() === email);
  if (alreadyExists) {
    if (note) { note.textContent = "An account with this email already exists."; note.style.color = "#DC2626"; }
    return;
  }

  const list = customAccountsForSettings().filter(a => a.email.toLowerCase() !== email);
  list.push({
    email, password, role,
    businessKey: account?.businessKey, businessName: account?.businessName,
    blankData: false, setupCompleted: true, services: account?.services || ["grooming", "boarding", "daycare"]
  });
  localStorage.setItem("pawfect_custom_accounts", JSON.stringify(list));

  q("newAccountEmail").value = "";
  q("newAccountPassword").value = "";
  q("newAccountRole").value = "Staff";
  if (note) { note.textContent = "✓ Account added."; note.style.color = "#059669"; }

  renderAccountsPanel();
}

function removeTeamAccount(email, source) {
  const account = getCurrentAccount();
  const normalizedEmail = email.toLowerCase();
  const list = getBusinessAccounts(account?.businessKey);
  const target = list.find(a => a.email.toLowerCase() === normalizedEmail);
  if (!target) return;

  const managerCount = list.filter(a => a.role === "Manager").length;
  if (target.role === "Manager" && managerCount <= 1) {
    alert("You can't remove the only manager account for this business.");
    return;
  }

  if (!confirm(`Remove ${email} from ${account?.businessName || "this business"}? This cannot be undone.`)) return;

  if (source === "seed") {
    const removed = removedSeedAccountEmails();
    if (!removed.includes(normalizedEmail)) removed.push(normalizedEmail);
    localStorage.setItem("pawfect_removed_seed_accounts", JSON.stringify(removed));
  } else {
    const updated = customAccountsForSettings().filter(a => a.email.toLowerCase() !== normalizedEmail);
    localStorage.setItem("pawfect_custom_accounts", JSON.stringify(updated));
  }

  const isSelf = normalizedEmail === String(account?.email || "").toLowerCase();
  if (isSelf) {
    localStorage.removeItem("pawfect_current_account");
    location.href = "login.html";
    return;
  }

  renderAccountsPanel();
}