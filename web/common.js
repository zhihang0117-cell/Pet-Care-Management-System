function q(id) { return document.getElementById(id); }

const RECORD_ID_PARAMS = [
  "customer_id", "pet_id", "grooming_booking_id", "daycare_booking_id",
  "boarding_booking_id", "payment_id", "message_id", "staff_id",
  "leave_id", "loyalty_id", "redemption_id", "coupon_id",
];
let restoringRecordUrl = false;

function recordUrlParamsWithoutIds() {
  const current = new URLSearchParams(location.search);
  current.delete("company_id");
  RECORD_ID_PARAMS.forEach(param => current.delete(param));
  return current;
}

// Keep record-detail URLs shareable and predictable. Company scope is always
// the first query parameter, followed by the primary key of the opened table.
function setRecordUrl(idParam, idValue) {
  if (restoringRecordUrl) return;
  const companyId = getCurrentAccount()?.businessKey;
  if (companyId == null || companyId === "" || idValue == null || idValue === "") return;

  const params = new URLSearchParams();
  params.set("company_id", companyId);
  params.set(idParam, idValue);
  recordUrlParamsWithoutIds().forEach((value, key) => params.append(key, value));
  history.pushState({ ...(history.state || {}), recordDetail: true }, "", `${location.pathname}?${params}${location.hash}`);
}

function replaceUrlWithoutRecord() {
  const params = recordUrlParamsWithoutIds();
  const query = params.toString();
  history.replaceState(null, "", `${location.pathname}${query ? `?${query}` : ""}${location.hash}`);
}

function clearRecordUrl(preferBack = true) {
  if (preferBack && history.state?.recordDetail) {
    history.back();
    return;
  }
  replaceUrlWithoutRecord();
}

function hideOpenRecordUi() {
  const bookingModal = document.getElementById("bookingModal");
  if (bookingModal) bookingModal.style.display = "none";
  if (typeof detailPage !== "undefined" && detailPage) detailPage.style.display = "none";
  if (typeof detailForm !== "undefined" && detailForm) detailForm.innerHTML = "";
}

async function restoreRecordFromUrl() {
  const params = new URLSearchParams(location.search);
  const companyId = params.get("company_id");
  const currentCompanyId = String(getCurrentAccount()?.businessKey || "");
  if (companyId && currentCompanyId && companyId !== currentCompanyId) {
    console.warn("Record URL belongs to a different company; ignoring it.");
    hideOpenRecordUi();
    return false;
  }

  const routes = [
    ["customer_id", value => openCustomerForm(Number(value))],
    ["pet_id", value => openPetForm(Number(value))],
    ["grooming_booking_id", value => document.getElementById("kanbanBoard")
      ? openBookingDetails(`grooming:${value}`)
      : openCustomerBookingDetail("grooming", Number(value))],
    ["daycare_booking_id", value => document.getElementById("kanbanBoard")
      ? openBookingDetails(`daycare:${value}`)
      : openCustomerBookingDetail("daycare", Number(value))],
    ["boarding_booking_id", value => document.getElementById("kanbanBoard")
      ? openBookingDetails(`boarding:${value}`)
      : openCustomerBookingDetail("boarding", Number(value))],
    ["payment_id", value => openPaymentDetail(Number(value))],
    ["message_id", value => openEnquiryDetailPage(Number(value))],
    ["staff_id", value => openStaffForm(Number(value))],
    ["leave_id", value => {
      const leave = leaveRecords.find(item => String(item.leave_id) === String(value));
      if (leave) openLeaveCellDetail(leave.staff_id, leave.start_date);
    }],
    ["loyalty_id", value => openLoyaltyMemberDetail(Number(value))],
    ["redemption_id", value => openRedemptionDetail(Number(value))],
    ["coupon_id", value => openRuleForm(Number(value))],
  ];

  const route = routes.find(([param]) => params.has(param));
  if (!route) {
    hideOpenRecordUi();
    return false;
  }

  restoringRecordUrl = true;
  try {
    await route[1](params.get(route[0]));
  } finally {
    restoringRecordUrl = false;
  }
  return true;
}

/* =========================
   LEGACY VIEW-MODEL COMPATIBILITY
========================= */

// Older dashboard helpers still read these collections synchronously before
// their real API-backed panels finish loading. Keep empty collections so an
// empty database renders as empty instead of resurrecting browser seed data.
const services = [];
const rooms = [];
let staff = [];
let bookings = [];
let enquiries = [];
let loyaltyRequests = [];
let leaveRequests = [];
let paymentRecords = [];

const today = getToday();

/* =========================
   STATE
========================= */

let currentServiceFilter = "all";
let currentView = "kanban";
let bookingFilterDate = getToday();
let currentStaffFilter = "all";
let draggedBookingId = null;
let listingSearchKeyword = "";
let calendarAnchorDate = getToday();

const CALENDAR_HOURS = [
  "08:00", "09:00", "10:00", "11:00", "12:00",
  "13:00", "14:00", "15:00", "16:00", "17:00"
];

// Real backend data for booking.html (separate from the mock `bookings`/
// `staff`/`rooms`/`services` arrays above, which dailyoverview.html,
// loyalty.html and dashboard.html's charts still read directly until
// they're wired too). Bookings live in 3 real tables (grooming_booking /
// daycare_booking / boarding_booking) with no shared id space and no price
// catalog / room table backing them, so we normalize all 3 into one flat
// array of view-model objects (`bookingRecords`) that the existing kanban /
// calendar / listing render functions below consume just like the old
// `bookings` mock array did. `id` on each record is a composite
// "type:rawId" string (e.g. "grooming:14") since raw ids are only unique
// within their own table.
const BOOKING_STATUS_TO_INTERNAL = {
  "Pending": "pending",
  "Scheduled": "scheduled",
  "Done": "done",
  "No Show": "no_show",
  "Cancelled": "cancelled"
};
const BOOKING_STATUS_TO_REAL = {
  pending: "Pending",
  scheduled: "Scheduled",
  done: "Done",
  no_show: "No Show",
  cancelled: "Cancelled"
};
function normalizeBookingStatus(rawStatus) {
  return BOOKING_STATUS_TO_INTERNAL[rawStatus] || (rawStatus || "pending").toLowerCase().replace(/\s+/g, "_");
}
function denormalizeBookingStatus(internalStatus) {
  return BOOKING_STATUS_TO_REAL[internalStatus] || internalStatus;
}

let bookingRecords = [];         // normalized grooming+daycare+boarding bookings
let bookingPetOptions = [];      // raw /api/pets
let bookingCustomerOptions = []; // raw /api/customers
let bookingStaffOptions = [];    // raw /api/staff

/* =========================
   INIT
========================= */

document.addEventListener("DOMContentLoaded", async () => {
  try {
    if (document.getElementById("liveDateTime")) {
      initLiveClock();
    }

    if (document.getElementById("kanbanBoard")) {
      await initBookingPage();
    }

    if (document.getElementById("actionCards")) {
      initDailyOverviewReal();
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

    await restoreRecordFromUrl();
  } catch (err) {
    console.error("Page init failed:", err);
    alert(`Something failed to load: ${err.message}`);
  }
});

window.addEventListener("popstate", async () => {
  try {
    await restoreRecordFromUrl();
  } catch (error) {
    console.error("Failed to restore record from browser history:", error);
    hideOpenRecordUi();
  }
});

/* =========================
   BOOKING DATA (booking.html real backend)
========================= */

async function initBookingPage() {
  try {
    await refreshBookingPageData();
  } catch (error) {
    alert(error.message || "Failed to load bookings.");
  }

  setupTabs();
  setupModalEvents();
  setupCalendarEvents();
  setupListingEvents();

  setTimeout(() => scrollCalendarToToday(), 100);
}

async function refreshBookingPageData() {
  await fetchBookingPageData();
  renderAll();
}

async function fetchBookingPageData() {
  const [grooming, daycare, boarding, pets, customers, staffList] = await Promise.all([
    api.get("/bookings/grooming"),
    api.get("/bookings/daycare"),
    api.get("/bookings/boarding"),
    api.get("/pets"),
    api.get("/customers"),
    api.get("/staff"),
  ]);

  bookingPetOptions = pets;
  bookingCustomerOptions = customers;
  bookingStaffOptions = staffList;

  bookingRecords = [
    ...grooming.map(normalizeGroomingBooking),
    ...daycare.map(normalizeDaycareBooking),
    ...boarding.map(normalizeBoardingBooking),
  ];
}

// Shared by profile.html/staff.html (and dashboard.html's dead-simple
// System panel loader) wherever real bookings/staff are needed alongside a
// page's own real data — fetches via fetchBookingPageData() then maps into
// the mock-shaped `staff`/`bookings` globals older render helpers expect.
async function loadRealBookingData() {
  await fetchBookingPageData();
  staff = bookingStaffOptions.map(member => ({
    ...member,
    id: member.staff_id,
    name: member.staff_name,
    offDays: member.off_days_json || [],
  }));
  bookings = bookingRecords.map(record => ({
    ...record,
    serviceType: record.type,
  }));
}

function normalizeGroomingBooking(b) {
  const pet = findBookingPet(b.pet_id);
  return {
    type: "grooming",
    rawId: b.grooming_booking_id,
    id: `grooming:${b.grooming_booking_id}`,
    petId: b.pet_id,
    customerId: pet?.customer_id ?? null,
    staffId: b.staff_id,
    petName: pet?.pet_name || "Unknown pet",
    customerName: bookingOwnerNameForPet(b.pet_id) || "Unknown owner",
    staffName: findBookingStaffMember(b.staff_id)?.staff_name || "Unassigned",
    serviceLabel: b.service_name || "-",
    date: b.booking_date,
    time: b.booking_time ? b.booking_time.slice(0, 5) : "",
    status: normalizeBookingStatus(b.booking_status),
    amount: Number(b.price || 0) + Number(b.add_on_price || 0),
    checkInDate: undefined,
    checkOutDate: undefined,
    notes: b.notes || "",
    raw: b,
  };
}

function normalizeDaycareBooking(b) {
  const pet = findBookingPet(b.pet_id);
  return {
    type: "daycare",
    rawId: b.daycare_booking_id,
    id: `daycare:${b.daycare_booking_id}`,
    petId: b.pet_id,
    customerId: pet?.customer_id ?? null,
    staffId: b.staff_id,
    petName: pet?.pet_name || "Unknown pet",
    customerName: bookingOwnerNameForPet(b.pet_id) || "Unknown owner",
    staffName: findBookingStaffMember(b.staff_id)?.staff_name || "Unassigned",
    serviceLabel: b.package_type || "-",
    date: b.booking_date,
    time: b.check_in_time ? b.check_in_time.slice(0, 5) : "",
    status: normalizeBookingStatus(b.booking_status),
    amount: Number(b.price || 0),
    checkInDate: undefined,
    checkOutDate: undefined,
    notes: b.special_instruction || "",
    raw: b,
  };
}

function normalizeBoardingBooking(b) {
  const pet = findBookingPet(b.pet_id);
  return {
    type: "boarding",
    rawId: b.boarding_booking_id,
    id: `boarding:${b.boarding_booking_id}`,
    petId: b.pet_id,
    customerId: pet?.customer_id ?? null,
    staffId: b.staff_id,
    petName: pet?.pet_name || "Unknown pet",
    customerName: bookingOwnerNameForPet(b.pet_id) || "Unknown owner",
    staffName: findBookingStaffMember(b.staff_id)?.staff_name || "Unassigned",
    serviceLabel: b.room_type || "-",
    date: b.check_in_date,
    time: b.check_in_time ? b.check_in_time.slice(0, 5) : "",
    status: normalizeBookingStatus(b.booking_status),
    amount: Number(b.total_price || 0),
    checkInDate: b.check_in_date,
    checkOutDate: b.check_out_date,
    notes: b.notes || "",
    raw: b,
  };
}

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

  bindDateNavigator("bookingFilter", {
    getDate: () => bookingFilterDate,
    setDate: date => { bookingFilterDate = date; },
    onChange: date => {
      calendarAnchorDate = date;
      renderAll();
    },
  });

  const kanbanStaffFilter = document.getElementById("kanbanStaffFilter");
  kanbanStaffFilter.innerHTML = `<option value="all">All Staff</option>` +
    bookingStaffOptions.map(member => `<option value="${member.staff_id}">${member.staff_name}</option>`).join("");
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
  });

  document.getElementById("bookingPetId").addEventListener("change", updateOwnerNameDisplay);

  document.getElementById("bookingForm").addEventListener("submit", event => {
    event.preventDefault();
    saveBooking();
  });

  document.getElementById("doneServiceBtn").addEventListener("click", async () => {
    closeModal();
    location.href = "payment.html";
  });

  document.getElementById("cancelBookingBtn").addEventListener("click", async () => {
    const statusSelect = document.getElementById("bookingStatus");
    const previousStatus = statusSelect.value;
    statusSelect.value = "cancelled";
    const saved = await saveBooking();
    if (!saved) statusSelect.value = previousStatus;
  });

  document.getElementById("deleteBookingBtn").addEventListener("click", async () => {
    const bookingId = document.getElementById("bookingId").value;
    const booking = findBookingRecord(bookingId);
    if (!booking) return;
    if (!confirm("Delete this booking? This cannot be undone.")) return;

    try {
      await api.deleteBooking(booking.type, booking.rawId);
      closeModal();
      await refreshBookingPageData();
    } catch (error) {
      alert(error.message || "Failed to delete booking.");
    }
  });

  document.getElementById("openPetProfileBtn").addEventListener("click", () => {
    location.href = "profile.html";
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
      const booking = findBookingRecord(draggedBookingId);
      draggedBookingId = null;
      if (!booking) return;

      const newDate = cell.dataset.date;
      const newTime = cell.dataset.time || booking.time;
      if (!canAddBookingToSlot(newDate, newTime, booking.staffId, booking.id)) {
        alert("This slot is not available. Maximum 3 bookings are allowed per timeslot, and staff cannot be duplicated.");
        return;
      }
      rescheduleBooking(booking, newDate, newTime);
    });
  });
}

async function rescheduleBooking(booking, newDate, newTime) {
  try {
    await api.updateBooking(booking.type, booking.rawId, bookingDateTimePayload(booking, newDate, newTime));
    await refreshBookingPageData();
  } catch (error) {
    alert(error.message || "Failed to reschedule booking.");
  }
}

function bookingDateTimePayload(booking, date, time) {
  if (booking.type === "grooming") return { booking_date: date, booking_time: time };
  if (booking.type === "daycare") return { booking_date: date, check_in_time: time };
  if (booking.type === "boarding") {
    // Shift check_out_date by the same number of nights so dragging to a new
    // date can't leave check_out_date behind check_in_date (a stay's length
    // must be preserved, not just its start).
    const nights = booking.checkInDate && booking.checkOutDate
      ? Math.max(1, Math.round((new Date(booking.checkOutDate) - new Date(booking.checkInDate)) / 86400000))
      : 1;
    return { check_in_date: date, check_in_time: time, check_out_date: addDays(date, nights) };
  }
  return {};
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
  return bookingRecords.filter(booking => {
    const matchesService = currentServiceFilter === "all" || booking.type === currentServiceFilter;
    return matchesService && booking.date === bookingFilterDate;
  });
}

function getKanbanBookings() {
  return getFilteredBookings().filter(booking => {
    if (currentStaffFilter !== "all" && String(booking.staffId) !== String(currentStaffFilter)) return false;
    return true;
  });
}

/* =========================
   METRIC CARDS
========================= */

function renderMetricCards() {
  const wrapper = document.getElementById("metricCards");
  const serviceBookings = bookingRecords.filter(booking =>
    currentServiceFilter === "all" || booking.type === currentServiceFilter
  );
  const metrics = buildMetrics(currentServiceFilter, serviceBookings, bookingFilterDate);

  wrapper.innerHTML = metrics.map(metric => `
    <div class="metric-card">
      <h3>${metric.label}</h3>
      <p>${metric.value}</p>
    </div>
  `).join("");
}

function buildMetrics(filter, serviceBookings, selectedDate) {
  const dated = serviceBookings.filter(booking => booking.date === selectedDate);

  if (filter === "grooming") {
    const grooming = dated;
    const doneRate = percentage(
      grooming.filter(b => b.status === "done").length,
      grooming.length
    );

    return [
      { label: "Bookings", value: grooming.length },
      { label: "Done Rate", value: doneRate },
      { label: "Pending Booking", value: countStatus(grooming, "pending") },
      { label: "No-show", value: countStatus(grooming, "no_show") }
    ];
  }

  if (filter === "boarding") {
    const boarding = serviceBookings;
    const currentBoarders = boarding.filter(b =>
      b.checkInDate <= selectedDate && b.checkOutDate >= selectedDate &&
      b.status !== "no_show" && b.status !== "cancelled"
    );

    return [
      { label: "Boarding", value: dated.length },
      { label: "Current Boarder", value: currentBoarders.length },
      { label: "Check-in", value: boarding.filter(b => b.checkInDate === selectedDate && b.status !== "cancelled").length },
      { label: "Check-out", value: boarding.filter(b => b.checkOutDate === selectedDate && b.status !== "cancelled").length }
    ];
  }

  if (filter === "daycare") {
    // No room/capacity table exists in the real schema (mock's `rooms`
    // array has no backend equivalent), so the old "Capacity" ratio metric
    // is replaced with a plain active-today count.
    const daycare = dated;

    return [
      { label: "Daycare", value: daycare.length },
      { label: "Pending Pick-up", value: daycare.filter(b => b.status === "done").length },
      { label: "No-show", value: countStatus(daycare, "no_show") },
      { label: "Active", value: daycare.filter(b => b.status !== "no_show" && b.status !== "cancelled").length }
    ];
  }

  const grooming = dated.filter(b => b.type === "grooming");
  const groomingDoneRate = percentage(
    grooming.filter(b => b.status === "done").length,
    grooming.length
  );
  const boarding = serviceBookings.filter(b => b.type === "boarding");
  const boardingCheckIn = boarding.filter(b => b.checkInDate === selectedDate && b.status !== "cancelled").length;
  const boardingCheckOut = boarding.filter(b => b.checkOutDate === selectedDate && b.status !== "cancelled").length;

  return [
    { label: "Pending Services", value: countStatus(dated, "pending") },
    { label: "Grooming Done Rate", value: groomingDoneRate },
    { label: "Boarding Check-in / Check-out", value: `${boardingCheckIn}/${boardingCheckOut}` },
    { label: "Daycare Attendance", value: dated.filter(b => b.type === "daycare").length },
    { label: "No Show", value: countStatus(dated, "no_show") }
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

  board.innerHTML = columns.map(column => {
    const columnBookings = data.filter(booking => booking.status === column.key);
    return `
    <div class="kanban-column" data-status="${column.key}">
      <h3>${column.label}</h3>
      ${columnBookings.map(booking => renderBookingCard(booking)).join("")}
    </div>
  `;
  }).join("");

  setupKanbanDragAndDrop();
  setupBookingClickEvents();
}

function renderBookingCard(booking) {
  return `
    <div class="booking-card" draggable="true" data-booking-id="${booking.id}">
      <div class="booking-card-top">
        ${renderStatusTag(booking.status)}
      </div>

      <strong>${booking.petName} — ${booking.serviceLabel}</strong>
      <small>${booking.customerName}</small><br>
      <small>${booking.date || "-"} | ${booking.time || "-"}</small><br>
      <small>Staff: ${booking.staffName}</small><br>
      <small>RM ${booking.amount.toFixed(2)}</small>
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
      const booking = findBookingRecord(draggedBookingId);
      draggedBookingId = null;

      if (booking && booking.status !== newStatus) {
        await updateBookingStatus(booking, newStatus);
      }
    });
  });
}

async function updateBookingStatus(booking, newStatusInternal) {
  const previousStatus = booking.status;
  booking.status = newStatusInternal;
  renderAll();
  try {
    await api.updateBooking(booking.type, booking.rawId, { booking_status: denormalizeBookingStatus(newStatusInternal) });
    await refreshBookingPageData();
    return true;
  } catch (error) {
    booking.status = previousStatus;
    renderAll();
    alert(error.message || "Failed to update booking status.");
    return false;
  }
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


function buildNewBookingDraft(type, petId, staffId, date, time) {
  return {
    id: "",
    type,
    petId,
    staffId,
    status: "pending",
    raw: {
      booking_date: date,
      booking_time: time,
      check_in_date: date,
      check_in_time: time
    }
  };
}

function openNewBooking() {
  if (bookingPetOptions.length === 0) {
    alert("Add a customer and pet in Customer & Pet Profile before creating a booking.");
    return;
  }

  const date = getToday();
  const time = findFirstAvailableTime(date) || "09:00";

  openBookingDetails(buildNewBookingDraft(
    "grooming",
    bookingPetOptions[0].pet_id,
    bookingStaffOptions[0]?.staff_id || "",
    date,
    time
  ));
}

function createBookingFromSlot(date, time) {
  if (bookingPetOptions.length === 0) {
    alert("Add a customer and pet in Customer & Pet Profile before creating a booking.");
    return;
  }

  const type = currentServiceFilter === "all" ? "grooming" : currentServiceFilter;
  const availableStaff = getAvailableStaffForSlot(date, time);

  if (getSlotBookings(date, time).length >= 3 || availableStaff.length === 0) {
    alert("This timeslot is fully booked. Maximum 3 bookings are allowed, and each booking must use a different staff.");
    return;
  }

  openBookingDetails(buildNewBookingDraft(type, bookingPetOptions[0].pet_id, availableStaff[0].staff_id, date, time));
}

/* =========================
   LISTING
========================= */

function renderListing() {
  const tbody = document.getElementById("listingTableBody");

  const filteredData = getFilteredBookings().filter(booking => {
    const searchableText = `
      ${booking.customerName}
      ${booking.petName}
      ${booking.serviceLabel}
      ${booking.staffName}
    `.toLowerCase();

    return searchableText.includes(listingSearchKeyword);
  });

  filteredData.sort((a, b) => (a.date || "").localeCompare(b.date || "") || (a.time || "").localeCompare(b.time || ""));

  tbody.innerHTML = filteredData.map(booking => {
    return `
      <tr>
        <td>${booking.date || "-"}</td>
        <td>${booking.customerName}</td>
        <td>${booking.petName}</td>
        <td>${booking.serviceLabel} <span class="profile-sub">(${booking.type})</span></td>
        <td>${booking.staffName}</td>
        <td>${booking.time || "-"}</td>

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

        <td>RM ${booking.amount.toFixed(2)}</td>

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
  const booking = findBookingRecord(bookingId);

  if (!booking) return;

  const select = event.target;
  const previousStatus = booking.status;
  select.disabled = true;
  const updated = await updateBookingStatus(booking, newStatus);
  if (!updated) select.value = previousStatus;
  select.disabled = false;
}

/* =========================
   MODAL / FORM
========================= */

function findBookingRecord(id) {
  return bookingRecords.find(b => b.id === id);
}

function findBookingPet(petId) {
  return bookingPetOptions.find(p => String(p.pet_id) === String(petId));
}

function findBookingCustomer(customerId) {
  return bookingCustomerOptions.find(c => String(c.customer_id) === String(customerId));
}

function findBookingStaffMember(staffId) {
  return bookingStaffOptions.find(s => String(s.staff_id) === String(staffId));
}

function bookingOwnerNameForPet(petId) {
  const pet = findBookingPet(petId);
  if (!pet) return "";
  return findBookingCustomer(pet.customer_id)?.full_name || "";
}

function openBookingDetails(bookingIdOrObj) {
  const booking = typeof bookingIdOrObj === "string"
    ? findBookingRecord(bookingIdOrObj)
    : bookingIdOrObj;
  if (!booking) return;

  if (booking.id) setRecordUrl(`${booking.type}_booking_id`, booking.rawId);
  else clearRecordUrl(false);

  const raw = booking.raw || {};

  populatePetDropdown();
  populateStaffDropdown();

  document.getElementById("bookingId").value = booking.id || "";
  document.getElementById("bookingPetId").value = booking.petId != null ? booking.petId : "";
  document.getElementById("serviceType").value = booking.type;
  document.getElementById("serviceType").disabled = !!booking.id;
  document.getElementById("staffName").value = booking.staffId != null ? booking.staffId : "";
  document.getElementById("bookingStatus").value = booking.status || "pending";
  updateOwnerNameDisplay();

  // Grooming-only
  document.getElementById("serviceName").value = raw.service_name || "";
  document.getElementById("addOnName").value = raw.add_on || "";
  document.getElementById("addOnPrice").value = raw.add_on_price ?? "";
  document.getElementById("bookingTime").value = raw.booking_time ? raw.booking_time.slice(0, 5) : "";

  // Daycare-only
  document.getElementById("packageType").value = raw.package_type || "";
  document.getElementById("specialInstruction").value = raw.special_instruction || "";

  // Boarding-only
  document.getElementById("roomType").value = raw.room_type || "";
  document.getElementById("pricePerNight").value = raw.price_per_night ?? "";
  document.getElementById("checkInDate").value = raw.check_in_date || "";
  document.getElementById("checkOutDate").value = raw.check_out_date || "";
  document.getElementById("feedingInstruction").value = raw.feeding_instruction || "";
  document.getElementById("medicalInstruction").value = raw.medical_instruction || "";

  // Shared across two types
  document.getElementById("price").value = raw.price ?? "";
  document.getElementById("bookingDate").value = raw.booking_date || "";
  document.getElementById("checkInTime").value = raw.check_in_time ? raw.check_in_time.slice(0, 5) : "";
  document.getElementById("checkOutTime").value = raw.check_out_time ? raw.check_out_time.slice(0, 5) : "";
  document.getElementById("notes").value = raw.notes || "";

  toggleServiceSpecificFields();

  const isExisting = !!booking.id;
  document.getElementById("doneServiceBtn").classList.toggle("hidden", !isExisting);
  document.getElementById("cancelBookingBtn").classList.toggle("hidden", !isExisting);
  document.getElementById("deleteBookingBtn").classList.toggle("hidden", !isExisting);

  document.getElementById("bookingModal").style.display = "flex";
}

function closeModal() {
  document.getElementById("bookingModal").style.display = "none";
  clearRecordUrl();
}

async function saveBooking() {
  const bookingId = document.getElementById("bookingId").value;
  const type = document.getElementById("serviceType").value;
  const petId = document.getElementById("bookingPetId").value;
  const staffId = document.getElementById("staffName").value;
  const statusInternal = document.getElementById("bookingStatus").value;

  if (!petId) { alert("Please select a pet."); return false; }
  if (!staffId) { alert("Please select a staff member."); return false; }

  const payload = {
    pet_id: Number(petId),
    staff_id: Number(staffId),
    booking_status: denormalizeBookingStatus(statusInternal)
  };

  let slotDate = "";
  let slotTime = "";

  if (type === "grooming") {
    const serviceName = document.getElementById("serviceName").value.trim();
    const price = document.getElementById("price").value;
    const date = document.getElementById("bookingDate").value;
    const time = document.getElementById("bookingTime").value;

    if (!serviceName || price === "" || !date || !time) {
      alert("Please fill in service name, price, booking date and booking time.");
      return false;
    }

    payload.service_name = serviceName;
    payload.price = Number(price);
    payload.booking_date = date;
    payload.booking_time = time;

    const addOnName = document.getElementById("addOnName").value.trim();
    payload.add_on = addOnName || null;
    payload.add_on_price = addOnName ? Number(document.getElementById("addOnPrice").value || 0) : null;
    payload.notes = document.getElementById("notes").value.trim() || null;

    slotDate = date;
    slotTime = time;
  } else if (type === "daycare") {
    const packageType = document.getElementById("packageType").value.trim();
    const price = document.getElementById("price").value;
    const date = document.getElementById("bookingDate").value;
    const checkInTime = document.getElementById("checkInTime").value;
    const checkOutTime = document.getElementById("checkOutTime").value;

    if (!packageType || price === "" || !date || !checkInTime || !checkOutTime) {
      alert("Please fill in package type, price, booking date, check-in time and check-out time.");
      return false;
    }
    if (checkOutTime <= checkInTime) {
      alert("Check-out time must be after check-in time.");
      return false;
    }

    payload.package_type = packageType;
    payload.price = Number(price);
    payload.booking_date = date;
    payload.check_in_time = checkInTime;
    payload.check_out_time = checkOutTime;
    payload.special_instruction = document.getElementById("specialInstruction").value.trim() || null;

    slotDate = date;
    slotTime = checkInTime;
  } else if (type === "boarding") {
    const roomType = document.getElementById("roomType").value.trim();
    const pricePerNight = document.getElementById("pricePerNight").value;
    const checkInDate = document.getElementById("checkInDate").value;
    const checkOutDate = document.getElementById("checkOutDate").value;
    const checkInTime = document.getElementById("checkInTime").value;
    const checkOutTime = document.getElementById("checkOutTime").value;

    if (!roomType || pricePerNight === "" || !checkInDate || !checkOutDate || !checkInTime || !checkOutTime) {
      alert("Please fill in room type, price per night, check-in/out date and check-in/out time.");
      return false;
    }
    if (checkOutDate < checkInDate || (checkOutDate === checkInDate && checkOutTime <= checkInTime)) {
      alert("Check-out must be after check-in.");
      return false;
    }

    payload.room_type = roomType;
    payload.price_per_night = Number(pricePerNight);
    payload.check_in_date = checkInDate;
    payload.check_out_date = checkOutDate;
    payload.check_in_time = checkInTime;
    payload.check_out_time = checkOutTime;
    payload.feeding_instruction = document.getElementById("feedingInstruction").value.trim() || null;
    payload.medical_instruction = document.getElementById("medicalInstruction").value.trim() || null;
    payload.notes = document.getElementById("notes").value.trim() || null;

    slotDate = checkInDate;
    slotTime = checkInTime;
  }

  const leavingActiveSchedule = statusInternal === "cancelled" || statusInternal === "no_show";
  if (!leavingActiveSchedule && slotDate && slotTime &&
      !canAddBookingToSlot(slotDate, slotTime, staffId, bookingId)) {
    alert("This booking cannot be saved. The selected timeslot already has 3 bookings or the selected staff is already assigned at this time.");
    return false;
  }

  const submitBtn = document.querySelector("#bookingForm button[type=submit]");
  if (submitBtn) submitBtn.disabled = true;

  try {
    const existing = bookingId ? findBookingRecord(bookingId) : null;

    if (existing) {
      await api.updateBooking(type, existing.rawId, payload);
    } else {
      await api.createBooking(type, payload);
    }

    closeModal();
    await refreshBookingPageData();
    return true;
  } catch (error) {
    alert(error.message || "Failed to save booking.");
    return false;
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

function updateOwnerNameDisplay() {
  const petId = document.getElementById("bookingPetId").value;
  document.getElementById("bookingOwnerName").value = bookingOwnerNameForPet(petId);
}

function populatePetDropdown() {
  const select = document.getElementById("bookingPetId");

  select.innerHTML = bookingPetOptions.map(pet => {
    const ownerName = findBookingCustomer(pet.customer_id)?.full_name || "Unknown owner";
    return `<option value="${pet.pet_id}">${pet.pet_name} (${ownerName}) — PET-${String(pet.pet_id).padStart(4, "0")}</option>`;
  }).join("");
}

function populateStaffDropdown() {
  document.getElementById("staffName").innerHTML = bookingStaffOptions.map(member => `
    <option value="${member.staff_id}">${member.staff_name}</option>
  `).join("");
}

function toggleServiceSpecificFields() {
  const selectedType = document.getElementById("serviceType").value;

  document.querySelectorAll(".gd-field").forEach(field => {
    field.classList.toggle("hidden", selectedType === "boarding");
  });
  document.querySelectorAll(".grooming-field").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "grooming");
  });
  document.querySelectorAll(".daycare-field").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "daycare");
  });
  document.querySelectorAll(".bd-field").forEach(field => {
    field.classList.toggle("hidden", selectedType === "grooming");
  });
  document.querySelectorAll(".boarding-field").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "boarding");
  });
  document.querySelectorAll(".gb-field").forEach(field => {
    field.classList.toggle("hidden", selectedType === "daycare");
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

// Real booking rows have no `duration` column at all (grooming/daycare/
// boarding schemas confirmed to lack it), so slot-conflict checking can no
// longer compute minute-range overlaps. It now treats a "slot" as an exact
// date+time match (the same granularity the hourly calendar grid already
// uses) instead of a start/end window: a conflict is two active bookings
// sharing the same date and the same time value, not merely overlapping.
function getSlotBookings(date, time, excludeBookingId = "") {
  return bookingRecords.filter(booking => {
    return booking.date === date &&
      booking.time === time &&
      booking.id !== excludeBookingId &&
      booking.status !== "cancelled" &&
      booking.status !== "no_show";
  });
}

function isStaffAlreadyBooked(date, time, staffId, excludeBookingId = "") {
  return bookingRecords.some(booking => {
    return booking.date === date &&
      booking.time === time &&
      String(booking.staffId) === String(staffId) &&
      booking.id !== excludeBookingId &&
      booking.status !== "cancelled" &&
      booking.status !== "no_show";
  });
}

function getAvailableStaffForSlot(date, time, excludeBookingId = "") {
  return bookingStaffOptions.filter(member => {
    return !isStaffAlreadyBooked(date, time, member.staff_id, excludeBookingId);
  });
}

function canAddBookingToSlot(date, time, staffId, excludeBookingId = "") {
  const slotBookings = getSlotBookings(date, time, excludeBookingId);

  if (slotBookings.length >= 3) {
    return false;
  }

  if (isStaffAlreadyBooked(date, time, staffId, excludeBookingId)) {
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

function findFirstAvailableTime(date) {
  return CALENDAR_HOURS.find(hour => {
    const slotBookings = getSlotBookings(date, hour);
    const availableStaff = getAvailableStaffForSlot(date, hour);

    return slotBookings.length < 3 && availableStaff.length > 0;
  });
}

function countToday(data) {
  return data.filter(booking => booking.date === bookingFilterDate).length;
}

function countStatus(data, status) {
  return data.filter(booking => booking.status === status).length;
}

function todayCheckIn() {
  return bookingRecords.filter(b => b.type === "boarding" && b.checkInDate === bookingFilterDate).length;
}

function todayCheckOut() {
  return bookingRecords.filter(b => b.type === "boarding" && b.checkOutDate === bookingFilterDate).length;
}

function percentage(value, total) {
  if (!total) return "0%";
  return `${Math.round((value / total) * 100)}%`;
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

function formatDateFilterLabel(dateString) {
  return new Date(`${dateString}T00:00:00`).toLocaleDateString("en-MY", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function updateDateNavigatorLabel(prefix, dateString) {
  const label = q(`${prefix}Label`);
  if (label) label.textContent = formatDateFilterLabel(dateString);
  const picker = q(`${prefix}LabelPickerInput`);
  if (picker) picker.value = dateString;
}

// Turns a plain-text date chip next to a date filter into a click target
// for a native date picker, so any specific date is directly selectable
// instead of only reachable by stepping one day (or week/month) at a time.
// The <input type="date"> is created as a sibling (not a child) of the
// chip, since several call sites (e.g. dashboardWeekChip) overwrite the
// chip's innerHTML on every render.
function attachDatePickerToLabel(labelId, applyDate, initialDate) {
  const label = q(labelId);
  if (!label || q(`${labelId}PickerInput`)) return;

  const picker = document.createElement("input");
  picker.type = "date";
  picker.id = `${labelId}PickerInput`;
  picker.className = "page-date-native-input";
  picker.value = initialDate;
  picker.setAttribute("aria-hidden", "true");
  picker.tabIndex = -1;
  label.insertAdjacentElement("afterend", picker);
  picker.addEventListener("change", () => {
    if (picker.value) applyDate(picker.value);
  });

  label.classList.add("page-date-label-pickable");
  label.setAttribute("role", "button");
  label.setAttribute("tabindex", "0");
  label.setAttribute("title", "Click to pick a specific date");
  const openPicker = () => {
    if (typeof picker.showPicker === "function") picker.showPicker();
    else picker.click();
  };
  label.addEventListener("click", openPicker);
  label.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openPicker();
    }
  });
}

function bindDateNavigator(prefix, { getDate, setDate, onChange }) {
  const applyDate = dateString => {
    setDate(dateString);
    updateDateNavigatorLabel(prefix, dateString);
    onChange(dateString);
  };

  q(`${prefix}PrevBtn`)?.addEventListener("click", () => applyDate(addDays(getDate(), -1)));
  q(`${prefix}TodayBtn`)?.addEventListener("click", () => applyDate(getToday()));
  q(`${prefix}NextBtn`)?.addEventListener("click", () => applyDate(addDays(getDate(), 1)));
  updateDateNavigatorLabel(prefix, getDate());
  attachDatePickerToLabel(`${prefix}Label`, applyDate, getDate());
}

function recordDateValue(record, fields) {
  for (const field of fields) {
    if (record?.[field]) return String(record[field]).slice(0, 10);
  }
  return "";
}

function filterRecordsByDateIfPresent(records, dateString, fields) {
  const hasDateField = records.some(record => recordDateValue(record, fields));
  return hasDateField
    ? records.filter(record => recordDateValue(record, fields) === dateString)
    : records;
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
let dailyOverviewDate = getToday();
let weekAnchor = getStartOfWeek(dailyOverviewDate);

// NOTE: these three still read the mock `bookings`/`services`/`rooms`
// arrays and are kept byte-for-byte as they were — they're NOT dead code:
// the shared buildActionQueue() and bookingDetailRow() functions below
// (used by dashboard.html's not-yet-wired charts) call them directly, so
// they must keep working against the mock shape. Real-data equivalents
// (filterRealBookingsByService, and inlined service/room labels on
// bookingRecords) are defined separately below for this page's own use.
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

// Real backend data for this page (separate from the mock `bookings`/
// `enquiries`/`loyaltyRequests`/`rooms` arrays above, which dashboard.html's
// remaining charts and enquiries.html/loyalty.html still read directly
// until they're wired too). `bookingRecords`/`bookingPetOptions`/etc. are
// populated via booking.html's fetchBookingPageData() (see "BOOKING DATA"
// section) — reused here as-is since it's a plain data fetcher, not
// booking.html-DOM-coupled.
//
// `messages` (chat-messages) has no status column: an enquiry is "pending"
// when reply_date is null, "replied" once it's set. There's no channel
// column either (this product is WhatsApp-only in practice, so "WhatsApp"
// below is a hardcoded display label, not a real field) and no per-service
// tagging on enquiries (`intent_label` is a free-text intent classification
// — seen values "booking"/"policy"/"loyalty" — not a grooming/boarding/
// daycare tag), so enquiry counts are NOT scoped by the service filter
// tabs the way booking counts are.
//
// "Pending Loyalty Redemption" has no real equivalent (redemptions are
// created atomically when staff verify a payment — there's no separate
// approval queue), so it's replaced by "Pending Payment Verification"
// (payments with a Pending or Unpaid status, exactly what payment.html shows).
let enquiryRecords = [];        // normalized /api/chat-messages rows
let pendingPaymentRecords = []; // raw unpaid /api/payments rows

function normalizeChatMessage(m) {
  const customerId = m.sender_type === "customer" ? m.sender_id : null;
  const customer = customerId != null ? findBookingCustomer(customerId) : null;
  return {
    id: m.message_id,
    customerId,
    customerName: customer?.full_name || "Unknown customer",
    phone: customer?.phone_number || "",
    senderType: m.sender_type,
    message: m.message_text || "",
    intent: m.intent_label || "",
    receiveDate: m.receive_date,
    receiveTime: m.receive_time ? m.receive_time.slice(0, 5) : "",
    replyDate: m.reply_date,
    replyTime: m.reply_time ? m.reply_time.slice(0, 5) : "",
    replyText: m.reply_text || "",
    repliedByStaffId: m.replied_by_staff_id,
    status: m.reply_date ? "replied" : "pending",
    raw: m,
  };
}

function filterRealBookingsByService(filter) {
  return bookingRecords.filter(b => filter === 'all' || b.type === filter);
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

// "payment" is a real elapsed-time SLA (payment.created_at is a full
// timestamp, and a pending payment can sit for hours or days) — kept
// separate from the time-of-day kinds below, which assume same-day.
// There's no "loyalty"/redemption entry here: a redemption row is created
// atomically at the same instant its linked payment is verified (see
// verify_payment_function.sql), so there's no separate pending-redemption
// gap to measure — the payment SLA below covers it.
const SLA_MINUTES = { pendingService: 15, enquiry: 180, payment: 300 };

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

// Timestamp-based counterpart of slaStatus/slaBadge above, for kinds whose
// anchor is a full ISO datetime (e.g. payment.created_at) rather than a
// same-day "HH:MM" string.
function minutesSince(isoTimestamp) {
  return Math.round((Date.now() - new Date(isoTimestamp).getTime()) / 60000);
}
function slaStatusFromTimestamp(kind, isoTimestamp) {
  const elapsed = minutesSince(isoTimestamp);
  const limit = SLA_MINUTES[kind];
  return { elapsed, limit, breached: elapsed > limit, remaining: limit - elapsed };
}
function slaBadgeFromTimestamp(kind, isoTimestamp) {
  if (!isoTimestamp) return '';
  const { elapsed, breached, remaining } = slaStatusFromTimestamp(kind, isoTimestamp);
  if (elapsed < 0) return '';
  return breached
    ? `<span class="status-tag status-no_show">⏱ SLA breached · ${formatElapsed(elapsed)}</span>`
    : `<span class="status-tag status-done">⏱ ${formatElapsed(remaining)} left</span>`;
}
function paymentSlaBreachCount(items) {
  return items.filter(item => slaStatusFromTimestamp('payment', item.created_at).breached).length;
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
  const total = pendingGrooming.length + pendingEnquiries.length + pendingPaymentRecords.length;
  const breaches = slaBreachCount('pendingService', pendingGrooming, 'time')
    + slaBreachCount('enquiry', pendingEnquiries, 'receivedAt')
    + paymentSlaBreachCount(pendingPaymentRecords);
  return { total, breaches, rate: total ? Math.round(((total - breaches) / total) * 100) : 100 };
}

/* =========================
   PENDING ACTION CARDS
========================= */

const CARD_CTA_REAL = {
  pendingGrooming:            { label: 'Open Service Queue',      href: 'booking.html' },
  pendingConfirmation:        { label: 'Review Bookings',         href: 'booking.html' },
  pendingEnquiries:           { label: 'Open Enquiries',          href: 'enquiries.html' },
  pendingPaymentVerification: { label: 'Review Payments',         href: 'payment.html' },
  boardingCheckIn:            { label: 'Open Booking Dashboard',  href: 'booking.html' },
  boardingCheckOut:           { label: 'Open Booking Dashboard',  href: 'booking.html' },
  daycareCheckIn:             { label: 'Open Booking Dashboard',  href: 'booking.html' },
  daycarePendingPickup:       { label: 'Open Booking Dashboard',  href: 'booking.html' }
};

// Enquiries and pending-payment-verification counts are intentionally NOT
// scoped to today's date the way booking counts are for enquiries (there's
// no per-day expectation baked into the mock either — it just showed
// whatever was pending) beyond limiting to receiveDate === today so the
// "Daily" overview doesn't surface a backlog from days ago (that full
// inbox view belongs on enquiries.html once it's wired). Payment
// verification counts are NOT date-scoped at all — same as payment.html's
// own "Pending" count — since a payment sits pending until verified,
// with no daily boundary.
function buildRealActionCards(filter) {
  const todayStr = dailyOverviewDate;
  const todayBookings = filterRealBookingsByService(filter).filter(b => b.date === todayStr);
  const pendingConfirmation = todayBookings.filter(b => b.status === 'scheduled').length;

  const pendingEnquiries = enquiryRecords.filter(e => e.senderType === 'customer' && e.status === 'pending' && e.receiveDate === todayStr);
  const enquirySlaBreaches = slaBreachCount('enquiry', pendingEnquiries, 'receiveTime');

  const cards = [];

  if (filter === 'all' || filter === 'grooming') {
    const groomingPending = bookingRecords.filter(b => b.type === 'grooming' && b.date === todayStr && b.status === 'pending');
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
      sub: `Scheduled today, awaiting confirmation`, tone: 'info'
    },
    {
      key: 'pendingEnquiries',
      icon: 'chat-message.png', label: 'Pending Enquiries', value: pendingEnquiries.length,
      sub: enquirySlaBreaches > 0 ? `${enquirySlaBreaches} breaching 3h reply SLA` : 'Within SLA', tone: enquirySlaBreaches > 0 ? 'alert' : 'purple'
    },
    {
      key: 'pendingPaymentVerification',
      icon: 'payment-card.png', label: 'Pending Payment Verification', value: pendingPaymentRecords.length,
      sub: paymentSlaBreachCount(pendingPaymentRecords) > 0 ? `${paymentSlaBreachCount(pendingPaymentRecords)} breaching 5h verification SLA` : 'Within SLA',
      tone: paymentSlaBreachCount(pendingPaymentRecords) > 0 ? 'alert' : (pendingPaymentRecords.length > 0 ? 'warning' : 'pink')
    }
  );

  if (filter === 'all' || filter === 'boarding') {
    const boardingBookings = bookingRecords.filter(b => b.type === 'boarding');
    const checkInsDue = boardingBookings.filter(b => b.checkInDate === todayStr && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled').length;
    const checkOutsDue = boardingBookings.filter(b => b.checkOutDate === todayStr && b.status !== 'no_show' && b.status !== 'cancelled').length;

    cards.push(
      { key: 'boardingCheckIn', icon: 'login.png', label: 'Boarding Check-In Due', value: checkInsDue, sub: `Arrivals to confirm (today)`, tone: 'success' },
      { key: 'boardingCheckOut', icon: 'logout.png', label: 'Boarding Check-Out Due', value: checkOutsDue, sub: `Departures to confirm (today)`, tone: 'warning' }
    );
  }

  if (filter === 'all' || filter === 'daycare') {
    const daycareBookings = bookingRecords.filter(b => b.type === 'daycare' && b.date === todayStr);
    const checkInsDue = daycareBookings.filter(b => b.status === 'pending' || b.status === 'scheduled').length;
    const pendingPickup = daycareBookings.filter(b => b.status === 'done').length;

    cards.push(
      { key: 'daycareCheckIn', icon: 'login.png', label: 'Daycare Check-In Due', value: checkInsDue, sub: 'Drop-offs to confirm', tone: 'success' },
      { key: 'daycarePendingPickup', icon: 'logout.png', label: 'Daycare Pending Pick-Up', value: pendingPickup, sub: 'Waiting for parent pickup', tone: 'warning' }
    );
  }

  return cards;
}

function renderRealActionCard(card) {
  const tone = TONES[card.tone] || TONES.info;
  return `
    <div class="kpi-hero-card action-card clickable" style="border-color:${tone.bg};" onclick="openRealCardDetail('${card.key}')" title="${card.sub}">
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

function renderRealServiceLoadChart() {
  const todayStr = dailyOverviewDate;
  const types = [
    { key: 'grooming', icon: 'grooming-scissors.png', label: 'Grooming', tone: 'info' },
    { key: 'boarding', icon: 'boarding.png', label: 'Boarding', tone: 'purple' },
    { key: 'daycare',  icon: 'dog-play.png', label: 'Daycare',  tone: 'warning' }
  ];
  const counts = types.map(t => bookingRecords.filter(b => b.type === t.key && b.date === todayStr && b.status !== "cancelled" && b.status !== "no_show").length);
  const max = Math.max(...counts, 1);
  const total = counts.reduce((a, b) => a + b, 0);

  q('serviceLoadTotal').textContent = `${total} booking${total === 1 ? '' : 's'} · ${formatShortDate(todayStr)}`;

  q('serviceLoadChart').innerHTML = types.map((t, i) => `
    <div class="bar-label-item" onclick="openRealServiceLoadDetail('${t.key}')">
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

function renderRealStatusDonut(filter) {
  const todayStr = dailyOverviewDate;
  const todayBookings = filterRealBookingsByService(filter).filter(b => b.date === todayStr);
  const counts = STATUS_META.map(s => todayBookings.filter(b => b.status === s.key).length);
  const total = counts.reduce((a, b) => a + b, 0);

  const scopeLabel = filter === 'all' ? 'All bookings' : `${filter.charAt(0).toUpperCase() + filter.slice(1)} bookings`;
  q('statusScopeLabel').textContent = scopeLabel;

  q('statusLegend').innerHTML = STATUS_META.map((s, i) => {
    const pct = total ? Math.round((counts[i] / total) * 100) : 0;
    return `
      <div class="chart-legend-item" data-key="${s.key}" onclick="openRealServiceStatusDetail('${s.key}')">
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
    onSegmentClick: openRealServiceStatusDetail
  });
}

/* =========================
   ROOM & PLAY AREA STATUS
========================= */

// No rooms/capacity table exists anywhere in the real schema, so this panel
// has no real-backend equivalent at all (unlike the mock `rooms` array,
// which was entirely invented). Rather than fabricate room/turnover data,
// this now just states plainly that it isn't tracked yet.
function renderRealRoomStatus() {
  q('roomStatusCount').textContent = 'Not tracked';
  q('roomStatusList').innerHTML = `<p class="queue-empty">Room &amp; play-area tracking isn't available yet — there's no rooms/capacity table in the connected system.</p>`;
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

function renderRealWeeklySchedule(filter) {
  const dates = getDateRange(weekAnchor, addDays(weekAnchor, 6));
  const todayStr = getToday();

  q('scheduleWeekLabel').textContent = `${formatShortDate(weekAnchor)} – ${formatShortDate(addDays(weekAnchor, 6))}`;

  q('scheduleHead').innerHTML = dates.map(date => {
    const d = new Date(date + 'T00:00:00');
    const isToday = date === todayStr;
    return `<div class="schedule-head ${isToday ? 'is-today' : ''}">${d.toLocaleDateString('en-MY', { weekday: 'short' })} ${d.getDate()}</div>`;
  }).join('');

  let bodyHtml = '';
  SCHEDULE_HOURS.forEach(hour => {
    bodyHtml += `<div class="schedule-time">${hour}</div>`;
    dates.forEach(date => {
      // Real bookings can have a missing/null time (e.g. daycare rows with
      // no check_in_time yet) — guard slotForTime() against that rather
      // than let toMinutes('') throw.
      const slotBookings = filterRealBookingsByService(filter).filter(b => b.date === date && b.time && slotForTime(b.time) === hour && b.status !== 'cancelled' && b.status !== 'no_show');
      const isToday = date === todayStr;
      if (slotBookings.length) {
        bodyHtml += `
          <div class="schedule-cell has-bookings ${isToday ? 'is-today' : ''}" onclick="openRealDayDetail('${date}')">
            ${slotBookings.slice(0, 2).map(b => `<span class="schedule-chip">${b.petName}</span>`).join('')}
            ${slotBookings.length > 2 ? `<span class="schedule-more">+${slotBookings.length - 2}</span>` : ''}
          </div>
        `;
      } else {
        bodyHtml += `<div class="schedule-cell ${isToday ? 'is-today' : ''}" onclick="openRealDayDetail('${date}')"></div>`;
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

  // Real redemption rows are created already-approved (see loyaltyDetailRow's
  // comment), so this never actually matches anything — kept only so a
  // demo/mock loyalty request with a genuine 'pending' status still renders.
  loyaltyRequests
    .filter(r => (filter === 'all' || r.relatedService === filter) && r.status === 'pending')
    .forEach(r => {
      rows.push({
        date: today, time: r.requestedAt, typeIcon: 'loyalty-reward-gift.png', type: 'Loyalty Redemption',
        detail: `${r.customerName} — ${r.type} (${r.points} pts)`,
        statusKey: 'pending', statusLabel: 'Needs Approval',
        sla: '',
        actionLabel: null, actionOnclick: null,
        rowOnclick: `openQueueItemDetail('loyalty','${r.id}')`
      });
    });

  return rows.sort((a, b) => a.date === b.date ? a.time.localeCompare(b.time) : a.date.localeCompare(b.date));
}

// Real-data counterpart of buildActionQueue() above (which dashboard.html's
// not-yet-wired charts still call directly against the mock bookings/
// enquiries/loyaltyRequests arrays — left untouched). Reads bookingRecords
// (real) + enquiryRecords (real) + pendingPaymentRecords (real) instead.
// Loyalty-redemption rows are replaced by payment-verification rows (see
// the "Pending Loyalty Redemption has no real equivalent" note above
// buildRealActionCards). Items with no real time value (payments, and any
// booking missing a time) sort to the bottom via the '99:99' sentinel.
function buildRealActionQueue(filter) {
  const todayStr = dailyOverviewDate;
  const rows = [];
  const todaysBookings = filterRealBookingsByService(filter).filter(b => b.date === todayStr);

  todaysBookings.forEach(b => {
    if (b.status === 'pending') {
      rows.push({
        time: b.time || '99:99', typeIcon: 'list-view.png', type: 'Service Due',
        detail: `${b.petName} (${b.customerName}) — ${b.serviceLabel}`,
        statusKey: 'pending', statusLabel: 'Needs Action',
        sla: b.time ? slaBadge('pendingService', b.time) : '',
        actionLabel: 'Open Payment', actionOnclick: `markRealBookingDone('${b.id}')`,
        rowOnclick: `openRealQueueItemDetail('booking','${b.id}')`
      });
    } else if (b.status === 'scheduled') {
      rows.push({
        time: b.time || '99:99', typeIcon: 'confirm-circle.png', type: 'Booking Confirmation',
        detail: `${b.petName} (${b.customerName}) — ${b.serviceLabel}`,
        statusKey: 'scheduled', statusLabel: 'Awaiting Confirmation',
        sla: '',
        actionLabel: 'Confirm Arrival', actionOnclick: `confirmRealBooking('${b.id}')`,
        rowOnclick: `openRealQueueItemDetail('booking','${b.id}')`
      });
    }
  });

  if (filter !== 'grooming') {
    filterRealBookingsByService(filter).forEach(b => {
      if (b.checkInDate === todayStr && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled') {
        rows.push({
          time: b.time || '99:99', typeIcon: 'login.png', type: 'Check-In Due',
          detail: `${b.petName} (${b.customerName}) — ${b.serviceLabel}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-In',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openRealQueueItemDetail('booking','${b.id}')`
        });
      }
      if (b.checkOutDate === todayStr && b.status !== 'no_show' && b.status !== 'cancelled') {
        rows.push({
          time: b.time || '99:99', typeIcon: 'logout.png', type: 'Check-Out Due',
          detail: `${b.petName} (${b.customerName}) — ${b.serviceLabel}`,
          statusKey: 'scheduled', statusLabel: 'Awaiting Check-Out',
          sla: '',
          actionLabel: null, actionOnclick: null,
          rowOnclick: `openRealQueueItemDetail('booking','${b.id}')`
        });
      }
    });
  }

  enquiryRecords
    .filter(e => e.senderType === 'customer' && e.status === 'pending' && e.receiveDate === todayStr)
    .forEach(e => {
      rows.push({
        time: e.receiveTime || '99:99', typeIcon: 'chat-message.png', type: 'Enquiry',
        detail: `${e.customerName} · WhatsApp — "${e.message}"`,
        statusKey: 'pending', statusLabel: 'Needs Reply',
        sla: e.receiveTime ? slaBadge('enquiry', e.receiveTime) : '',
        actionLabel: 'Mark Replied', actionOnclick: `resolveRealEnquiry(${e.id})`,
        rowOnclick: `openRealQueueItemDetail('enquiry',${e.id})`
      });
    });

  pendingPaymentRecords.filter(p => String(p.date || "").slice(0, 10) === todayStr).forEach(p => {
    rows.push({
      time: '99:99', typeIcon: 'payment-card.png', type: 'Payment Verification',
      detail: `PAY-${String(p.payment_id).padStart(4, '0')}${p.service ? ' — ' + p.service : ''} · RM ${Number(p.final_amount || 0).toFixed(2)}`,
      statusKey: 'pending', statusLabel: 'Needs Verification',
      sla: slaBadgeFromTimestamp('payment', p.created_at),
      actionLabel: 'Open Payment', actionOnclick: `location.href='payment.html'`,
      rowOnclick: `openRealQueueItemDetail('payment',${p.payment_id})`
    });
  });

  return rows.sort((a, b) => a.time.localeCompare(b.time));
}

function renderRealActionQueue(filter) {
  const rows = buildRealActionQueue(filter);
  q('queueCount').textContent = `${rows.length} item${rows.length === 1 ? '' : 's'}`;

  const tbody = q('actionQueueBody');

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="queue-empty">Nothing pending right now — all caught up!</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(row => `
    <tr onclick="${row.rowOnclick}">
      <td>${row.time === '99:99' ? '—' : row.time}</td>
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

// No `sla` badge here: a redemption is created already-approved (see
// verify_payment_function.sql), so create/approve happen at the same
// instant — there's no pending gap on this record itself to measure. Any
// wait the customer experienced is on the linked payment, tracked by
// realPaymentDetailRow's payment SLA instead.
function loyaltyDetailRow(r) {
  const refunded = r.status === 'refunded';
  const approvedAt = r.approvedDate ? ` · approved ${r.approvedDate}${r.approvedTime ? ' ' + r.approvedTime : ''}` : '';
  return renderDetailRow({
    title: `${r.customerName} — ${r.type}`,
    sub: `${r.points} pts · requested ${r.requestedAt}${approvedAt}`,
    tag: r.status === 'approved' ? 'done' : r.status === 'rejected' || refunded ? 'no_show' : 'pending',
    tagLabel: r.status === 'approved' ? 'Approved' : r.status === 'rejected' ? 'Rejected' : refunded ? 'Refunded' : 'Pending',
    sla: '',
    actionLabel: null,
    actionOnclick: null
  });
}

// Real-data counterparts of bookingDetailRow/enquiryDetailRow/
// loyaltyDetailRow above (which dashboard.html's not-yet-wired charts still
// call directly against mock objects — left completely untouched).
function realBookingDetailRow(b) {
  const statusMeta = {
    pending:   { tag: 'pending',   tagLabel: 'Needs Action', actionLabel: 'Open Payment',   actionOnclick: `markRealBookingDone('${b.id}')`, sla: b.time ? slaBadge('pendingService', b.time) : '' },
    scheduled: { tag: 'scheduled', tagLabel: 'Scheduled',    actionLabel: 'Confirm Arrival', actionOnclick: `confirmRealBooking('${b.id}')` },
    done:      { tag: 'done',      tagLabel: 'Done' },
    no_show:   { tag: 'no_show',   tagLabel: 'No Show' },
    cancelled: { tag: 'cancelled', tagLabel: 'Cancelled' }
  }[b.status] || { tag: 'pending', tagLabel: b.status };

  return renderDetailRow({
    title: `${b.petName} (${b.customerName})`,
    sub: `${b.serviceLabel} · ${b.date || '-'}${b.time ? ' ' + b.time : ''}`,
    ...statusMeta
  });
}

// No relatedService/priority field is fabricated here — see
// computeRealEnquiryPriority below for how priority is derived from real
// columns only (reply_date/receive_time SLA + intent_label + keyword scan).
function realEnquiryDetailRow(e) {
  const priority = computeRealEnquiryPriority(e);
  return renderDetailRow({
    title: `${e.customerName} · WhatsApp`,
    sub: `"${e.message}"${e.intent ? ` (intent: ${e.intent})` : ''} — received ${e.receiveTime || e.receiveDate || '-'}${priority !== 'normal' ? ` · ${enquiryPriorityLabel(priority)} priority` : ''}`,
    tag: e.status === 'pending' ? 'pending' : 'done',
    tagLabel: e.status === 'pending' ? 'Needs Reply' : 'Replied',
    sla: e.status === 'pending' && e.receiveTime ? slaBadge('enquiry', e.receiveTime) : '',
    actionLabel: e.status === 'pending' ? 'Mark Replied' : null,
    actionOnclick: e.status === 'pending' ? `resolveRealEnquiry(${e.id})` : null
  });
}

// computeEnquiryPriority (shared, defined above) reads the mock's
// `relatedService`/`receivedAt` fields, which don't exist on real chat
// messages — this is the same logic ported onto the real normalized shape
// instead of repointing the shared function.
function computeRealEnquiryPriority(e) {
  if (e.status === 'pending' && slaStatus('enquiry', e.receiveTime).breached) return 'urgent';
  const msg = (e.message || '').toLowerCase();
  if (e.intent === 'booking' || URGENT_KEYWORDS.some(k => msg.includes(k))) return 'high';
  return 'normal';
}

// Real counterpart of the retired "Pending Loyalty Redemption" concept —
// a pending `payment` row, exactly what payment.html's pending queue shows.
// No inline verify action here (verifying needs coupon/staff selection),
// so this just links out to payment.html to complete it.
function realPaymentDetailRow(p) {
  return renderDetailRow({
    title: `PAY-${String(p.payment_id).padStart(4, '0')}${p.service ? ' — ' + p.service : ''}`,
    sub: `RM ${Number(p.final_amount || 0).toFixed(2)}${p.date ? ' · ' + p.date : ''}`,
    tag: 'pending',
    tagLabel: 'Awaiting Verification',
    sla: slaBadgeFromTimestamp('payment', p.created_at),
    actionLabel: null,
    actionOnclick: null
  });
}

function openRealCardDetail(cardKey) {
  const filter = currentFilter;
  const todayStr = dailyOverviewDate;
  const todayBookings = filterRealBookingsByService(filter).filter(b => b.date === todayStr);
  const cta = CARD_CTA_REAL[cardKey];

  if (cardKey === 'pendingGrooming') {
    const items = bookingRecords.filter(b => b.type === 'grooming' && b.date === todayStr && b.status === 'pending');
    openDetailModal('Pending Grooming', `${items.length} booking(s) need grooming service on ${formatShortDate(todayStr)}. SLA: start within 15 minutes.`, items.map(realBookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingConfirmation') {
    const items = todayBookings.filter(b => b.status === 'scheduled');
    openDetailModal('Pending Booking Confirmation', `${items.length} booking(s) scheduled today, awaiting confirmation.`, items.map(realBookingDetailRow).join(''), cta);
  } else if (cardKey === 'pendingEnquiries') {
    const items = enquiryRecords.filter(e => e.senderType === 'customer' && e.status === 'pending' && e.receiveDate === todayStr);
    openDetailModal('Pending Enquiries', `${items.length} enquiries awaiting a reply on ${formatShortDate(todayStr)}. SLA: reply within 3 hours.`, items.map(realEnquiryDetailRow).join(''), cta);
  } else if (cardKey === 'pendingPaymentVerification') {
    openDetailModal('Pending Payment Verification', `${pendingPaymentRecords.length} payment(s) awaiting staff verification.`, pendingPaymentRecords.map(realPaymentDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckIn') {
    const items = bookingRecords.filter(b => b.type === 'boarding' && b.checkInDate === todayStr && b.status !== 'done' && b.status !== 'no_show' && b.status !== 'cancelled');
    openDetailModal('Boarding Check-In Due', `${items.length} arrival(s) to confirm.`, items.map(realBookingDetailRow).join(''), cta);
  } else if (cardKey === 'boardingCheckOut') {
    const items = bookingRecords.filter(b => b.type === 'boarding' && b.checkOutDate === todayStr && b.status !== 'no_show' && b.status !== 'cancelled');
    openDetailModal('Boarding Check-Out Due', `${items.length} departure(s) to confirm.`, items.map(realBookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycareCheckIn') {
    const items = bookingRecords.filter(b => b.type === 'daycare' && b.date === todayStr && (b.status === 'pending' || b.status === 'scheduled'));
    openDetailModal('Daycare Check-In Due', `${items.length} drop-off(s) to confirm.`, items.map(realBookingDetailRow).join(''), cta);
  } else if (cardKey === 'daycarePendingPickup') {
    const items = bookingRecords.filter(b => b.type === 'daycare' && b.date === todayStr && b.status === 'done');
    openDetailModal('Daycare Pending Pick-Up', `${items.length} pet(s) waiting for pickup.`, items.map(realBookingDetailRow).join(''), cta);
  }
}

function openRealServiceLoadDetail(type) {
  const todayStr = dailyOverviewDate;
  const items = bookingRecords.filter(b => b.type === type && b.date === todayStr && b.status !== "cancelled" && b.status !== "no_show");
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  openDetailModal(`${label} Bookings`, `${items.length} booking(s) on ${formatShortDate(todayStr)}.`, items.map(realBookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRealServiceStatusDetail(statusKey) {
  const todayStr = dailyOverviewDate;
  const items = filterRealBookingsByService(currentFilter).filter(b => b.date === todayStr && b.status === statusKey);
  const labelMap = { pending: 'Pending Service', scheduled: 'Scheduled', done: 'Done', no_show: 'No Show', cancelled: 'Cancelled' };
  openDetailModal(`Bookings — ${labelMap[statusKey]}`, `${items.length} booking(s) on ${formatShortDate(todayStr)}.`, items.map(realBookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRealDayDetail(date) {
  const items = filterRealBookingsByService(currentFilter).filter(b => b.date === date);
  const label = new Date(date + 'T00:00:00').toLocaleDateString('en-MY', { weekday: 'long', day: '2-digit', month: 'short', year: 'numeric' });
  openDetailModal(label, `${items.length} booking(s) on this day.`, items.map(realBookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRealQueueItemDetail(kind, id) {
  if (kind === 'booking') {
    const b = bookingRecords.find(x => x.id === id);
    if (b) openDetailModal('Booking Detail', `${b.petName} — ${b.serviceLabel}`, realBookingDetailRow(b), { label: 'Open Booking Dashboard', href: 'booking.html' });
  } else if (kind === 'enquiry') {
    const e = enquiryRecords.find(x => x.id === id);
    if (e) openDetailModal('Enquiry Detail', `${e.customerName} via WhatsApp`, realEnquiryDetailRow(e), { label: 'Open Enquiries', href: 'enquiries.html' });
  } else if (kind === 'payment') {
    const p = pendingPaymentRecords.find(x => x.payment_id === id);
    if (p) openDetailModal('Payment Verification Detail', `PAY-${String(p.payment_id).padStart(4, '0')}`, realPaymentDetailRow(p), { label: 'Open Payment', href: 'payment.html' });
  }
}

/* =========================
   DAILY OVERVIEW MUTATIONS (real backend)
========================= */

async function fetchDailyOverviewData() {
  // Reuses booking.html's real fetcher as-is (plain data fetch, not
  // DOM-coupled) to populate bookingRecords/bookingPetOptions/
  // bookingCustomerOptions/bookingStaffOptions for this page's own load.
  await fetchBookingPageData();

  const [messages, pendingPayments] = await Promise.all([
    api.get('/chat-messages'),
    api.get('/payments'),
  ]);

  enquiryRecords = messages.map(normalizeChatMessage);
  pendingPaymentRecords = pendingPayments.filter(isPaymentAwaitingVerification);
}

async function refreshDailyOverviewData() {
  await fetchDailyOverviewData();
  renderRealDailyOverview();
}

async function markRealBookingDone(id) {
  const booking = bookingRecords.find(b => b.id === id);
  if (!booking) return;
  closeDetailModal();
  location.href = 'payment.html';
}

async function confirmRealBooking(id) {
  const booking = bookingRecords.find(b => b.id === id);
  if (!booking) return;
  try {
    await api.updateBooking(booking.type, booking.rawId, { booking_status: denormalizeBookingStatus('pending') });
    closeDetailModal();
    await refreshDailyOverviewData();
  } catch (error) {
    alert(error.message || 'Failed to update booking.');
  }
}

async function resolveRealEnquiry(id) {
  const enquiry = enquiryRecords.find(e => e.id === id);
  if (!enquiry) return;
  const stamp = todayStampLocal(); // reused from STAFF MANAGEMENT section (generic {date,time} helper)
  try {
    await api.patch(`/chat-messages/${id}`, { reply_date: stamp.date, reply_time: stamp.time });
    closeDetailModal();
    await refreshDailyOverviewData();
  } catch (error) {
    alert(error.message || 'Failed to update enquiry.');
  }
}

/* =========================
   DAILY OVERVIEW INIT (real backend)
========================= */

function renderRealDailyOverview() {
  q('actionCards').innerHTML = buildRealActionCards(currentFilter).map(renderRealActionCard).join('');
  renderRealServiceLoadChart();
  renderRealRoomStatus();
  renderRealWeeklySchedule(currentFilter);
  renderRealStatusDonut(currentFilter);
  renderRealActionQueue(currentFilter);
}

async function initDailyOverviewReal() {
  try {
    await fetchDailyOverviewData();
  } catch (error) {
    alert(error.message || 'Failed to load daily overview data.');
  }

  document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#overviewServiceFilterTabs .tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentFilter = btn.dataset.filter;
      renderRealDailyOverview();
    });
  });

  bindDateNavigator("dailyDate", {
    getDate: () => dailyOverviewDate,
    setDate: date => { dailyOverviewDate = date; },
    onChange: date => {
      weekAnchor = getStartOfWeek(date);
      renderRealDailyOverview();
    },
  });

  q('detailModal').addEventListener('click', event => {
    if (event.target.id === 'detailModal') closeDetailModal();
  });

  renderRealDailyOverview();
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

let customers = [];
let pets = [];
let crmBookings = [];

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

// Real backend data for this page (separate from the mock `customers`/`pets`
// arrays above, which dashboard.html still reads via getCustomerById() until
// it's wired too). Real columns differ from the mock shape: `customer` has
// no loyalty_id/photo_icon/notes, and `pet` has no weight/colour/
// service_preference but does have height_cm/size/vaccination_status/
// vaccination_expired_date/health_notes/service_notes.
let customerRecords = [];
let petRecords = [];
let allBookingsFlat = [];

async function fetchAllBookingsFlat() {
  const [grooming, daycare, boarding] = await Promise.all([
    api.get("/bookings/grooming"),
    api.get("/bookings/daycare"),
    api.get("/bookings/boarding"),
  ]);
  // Extra fields (type/serviceLabel/status/amount) beyond pet_id/date are
  // for the customer detail page's "Recent Bookings" drill-down — kept
  // here rather than pulling in booking.html's normalize*Booking()
  // helpers, since those need bookingPetOptions/bookingStaffOptions that
  // this page never loads.
  return [
    ...grooming.map(b => ({
      type: "grooming", id: b.grooming_booking_id, pet_id: b.pet_id,
      date: b.booking_date, time: b.booking_time ? b.booking_time.slice(0, 5) : "",
      serviceLabel: b.service_name || "Grooming",
      status: normalizeBookingStatus(b.booking_status),
      amount: Number(b.price || 0) + Number(b.add_on_price || 0),
    })),
    ...daycare.map(b => ({
      type: "daycare", id: b.daycare_booking_id, pet_id: b.pet_id,
      date: b.booking_date, time: b.check_in_time ? b.check_in_time.slice(0, 5) : "",
      serviceLabel: b.package_type || "Daycare",
      status: normalizeBookingStatus(b.booking_status),
      amount: Number(b.price || 0),
    })),
    ...boarding.map(b => ({
      type: "boarding", id: b.boarding_booking_id, pet_id: b.pet_id,
      date: b.check_in_date, time: b.check_in_time ? b.check_in_time.slice(0, 5) : "",
      serviceLabel: b.room_type || "Boarding",
      status: normalizeBookingStatus(b.booking_status),
      amount: Number(b.total_price || 0),
    })),
  ];
}

function findCustomerRecord(customerId) {
  return customerRecords.find(c => String(c.customer_id) === String(customerId));
}

function findPetRecord(petId) {
  return petRecords.find(p => String(p.pet_id) === String(petId));
}

function getPetsForCustomer(customerId) {
  return petRecords.filter(p => String(p.customer_id) === String(customerId));
}

function getBookingsForCustomerRecord(customerId) {
  const petIds = getPetsForCustomer(customerId).map(p => String(p.pet_id));
  return allBookingsFlat.filter(b => petIds.includes(String(b.pet_id)));
}

function getLastBookingForCustomerRecord(customerId) {
  const customerBookings = getBookingsForCustomerRecord(customerId);
  if (customerBookings.length === 0) return null;
  return customerBookings.slice().sort((a, b) => new Date(b.date) - new Date(a.date))[0];
}

// Drill-down list for the customer detail page — most recent first. Not
// capped: the list scrolls (see .crm-list-scroll in index.css) instead of
// truncating, so a long-time customer's full history is still reachable.
function getRecentBookingsForCustomer(customerId, limit = Infinity) {
  return getBookingsForCustomerRecord(customerId)
    .slice()
    .sort((a, b) => `${b.date} ${b.time || ""}`.localeCompare(`${a.date} ${a.time || ""}`))
    .slice(0, limit);
}

async function initCRM() {
  [customerRecords, petRecords, allBookingsFlat] = await Promise.all([
    api.get("/customers"),
    api.get("/pets"),
    fetchAllBookingsFlat(),
  ]);

  updateKPI();
  renderLists();

  profileTypeFilter.addEventListener("change", () => { customerListPage = 1; petListPage = 1; renderLists(); });
  searchInput.addEventListener("input", () => { customerListPage = 1; petListPage = 1; renderLists(); });
}

async function refreshCrmData() {
  [customerRecords, petRecords, allBookingsFlat] = await Promise.all([
    api.get("/customers"),
    api.get("/pets"),
    fetchAllBookingsFlat(),
  ]);
  updateKPI();
  renderLists();
}

function updateKPI() {
  document.getElementById("totalCustomers").textContent = customerRecords.length;
  document.getElementById("totalPets").textContent = petRecords.length;

  const maxCustomerId = customerRecords.reduce((max, c) => Math.max(max, Number(c.customer_id) || 0), 0);
  document.getElementById("newCustomers").textContent =
    customerRecords.filter(c => Number(c.customer_id) > maxCustomerId - 2).length;

  document.getElementById("attentionNeeded").textContent =
    petRecords.filter(pet => pet.health_notes && pet.health_notes.trim() !== "").length;
}

let customerListPage = 1;
const CUSTOMER_PAGE_SIZE = 5;
let lastFilteredCustomers = [];

let petListPage = 1;
const PET_PAGE_SIZE = 5;
let lastFilteredPets = [];

function renderLists() {
  const selectedType = profileTypeFilter.value;
  const searchValue = searchInput.value.toLowerCase().trim();

  const filteredCustomers = filterCustomers(searchValue);
  const filteredPets = filterPets(searchValue);
  lastFilteredCustomers = filteredCustomers;
  lastFilteredPets = filteredPets;

  customerSection.classList.toggle("hidden", selectedType === "pet");
  petSection.classList.toggle("hidden", selectedType === "customer");

  renderCustomerTable(filteredCustomers);
  renderPetTable(filteredPets);
}

function filterCustomers(searchValue) {
  return customerRecords.filter(customer => {
    const linkedPets = getPetsForCustomer(customer.customer_id);
    const linkedPetText = linkedPets
      .map(pet => `${pet.pet_name} ${pet.pet_id}`)
      .join(" ")
      .toLowerCase();

    return (
      customer.full_name.toLowerCase().includes(searchValue) ||
      (customer.phone_number || "").toLowerCase().includes(searchValue) ||
      String(customer.customer_id).includes(searchValue) ||
      linkedPetText.includes(searchValue)
    );
  });
}

function filterPets(searchValue) {
  return petRecords.filter(pet => {
    const owner = findCustomerRecord(pet.customer_id);

    return (
      pet.pet_name.toLowerCase().includes(searchValue) ||
      String(pet.pet_id).includes(searchValue) ||
      String(pet.customer_id).includes(searchValue) ||
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
        <td colspan="5" class="empty-row">No customer record found.</td>
      </tr>
    `;
    renderCustomerPagination(0);
    return;
  }

  const totalPages = Math.max(1, Math.ceil(data.length / CUSTOMER_PAGE_SIZE));
  customerListPage = Math.min(Math.max(customerListPage, 1), totalPages);
  const start = (customerListPage - 1) * CUSTOMER_PAGE_SIZE;
  const pageData = data.slice(start, start + CUSTOMER_PAGE_SIZE);

  pageData.forEach((customer, i) => {
    const lastBooking = getLastBookingForCustomerRecord(customer.customer_id);

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">${(start + i) % 2 === 0 ? "👨" : "👩"} ${customer.full_name}</span>
      </td>

      <td>${customer.phone_number || "—"}</td>

      <td>
        ${
          lastBooking
            ? `<span class="key-chip">${formatDate(lastBooking.date)}</span>`
            : `<span class="profile-sub">No booking yet</span>`
        }
      </td>

      <td>${getBookingsForCustomerRecord(customer.customer_id).length}</td>

      <td>
        <button class="action-btn" onclick="openCustomerForm(${customer.customer_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    `;

    customerTableBody.appendChild(row);
  });

  renderCustomerPagination(totalPages);
}

function buildPageNumbers(current, total) {
  const pages = new Set([1, total, current - 1, current, current + 1]);
  return [...pages].filter(p => p >= 1 && p <= total).sort((a, b) => a - b);
}

/* =========================
   GENERIC LIST PAGINATION
   Used by enquiries.html/payment.html/loyalty.html's tables (5 rows/page,
   numbered controls bottom-left — see .table-pagination in index.css).
   customer/pet on profile.html predate this and keep their own hand-rolled
   version above; this generic version is for every table added since.
========================= */

const listPageState = {};
const listRerenderers = {};

// Called once per table at page-init, so goToListPage() knows how to
// redraw after a click — the rerenderer is just "re-run the render
// function for this tab", which already reads the current search/filter
// state itself.
function registerListRerenderer(key, rerenderFn) {
  listRerenderers[key] = rerenderFn;
}

function resetListPage(key) {
  listPageState[key] = 1;
}

// Clamps the stored page for `key` to fit `data`, renders the pagination
// controls into #{key}Pagination, and returns just that page's slice —
// callers render rows from the returned slice instead of the full `data`.
function paginateList(key, data, pageSize = 5) {
  const totalPages = Math.max(1, Math.ceil(data.length / pageSize));
  const page = Math.min(Math.max(listPageState[key] || 1, 1), totalPages);
  listPageState[key] = page;

  renderListPagination(key, data.length ? totalPages : 0);

  const start = (page - 1) * pageSize;
  return data.slice(start, start + pageSize);
}

function renderListPagination(key, totalPages) {
  const el = document.getElementById(`${key}Pagination`);
  if (!el) return;
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }

  const page = listPageState[key] || 1;
  const pages = buildPageNumbers(page, totalPages);
  let html = `<button class="page-btn" type="button" ${page === 1 ? "disabled" : ""} onclick="goToListPage('${key}', ${page - 1})">‹ Prev</button>`;
  let prevPage = 0;
  pages.forEach(p => {
    if (p - prevPage > 1) html += `<span class="page-ellipsis">…</span>`;
    html += `<button class="page-btn ${p === page ? "active" : ""}" type="button" onclick="goToListPage('${key}', ${p})">${p}</button>`;
    prevPage = p;
  });
  html += `<button class="page-btn" type="button" ${page === totalPages ? "disabled" : ""} onclick="goToListPage('${key}', ${page + 1})">Next ›</button>`;

  el.innerHTML = html;
}

function goToListPage(key, page) {
  listPageState[key] = page;
  listRerenderers[key]?.();
}

// Sits bottom-left under the table (see .table-pagination in index.css) —
// only the current page's 5 rows are in the DOM, so paging just re-slices
// the already-filtered `lastFilteredCustomers` instead of refetching.
function renderCustomerPagination(totalPages) {
  const el = document.getElementById("customerPagination");
  if (!el) return;
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }

  const pages = buildPageNumbers(customerListPage, totalPages);
  let html = `<button class="page-btn" type="button" ${customerListPage === 1 ? "disabled" : ""} onclick="goToCustomerPage(${customerListPage - 1})">‹ Prev</button>`;
  let prevPage = 0;
  pages.forEach(p => {
    if (p - prevPage > 1) html += `<span class="page-ellipsis">…</span>`;
    html += `<button class="page-btn ${p === customerListPage ? "active" : ""}" type="button" onclick="goToCustomerPage(${p})">${p}</button>`;
    prevPage = p;
  });
  html += `<button class="page-btn" type="button" ${customerListPage === totalPages ? "disabled" : ""} onclick="goToCustomerPage(${customerListPage + 1})">Next ›</button>`;

  el.innerHTML = html;
}

function goToCustomerPage(page) {
  customerListPage = page;
  renderCustomerTable(lastFilteredCustomers);
}

function renderPetTable(data) {
  petTableBody.innerHTML = "";
  petRecordCount.textContent = `${data.length} records`;

  if (data.length === 0) {
    petTableBody.innerHTML = `
      <tr>
        <td colspan="5" class="empty-row">No pet record found.</td>
      </tr>
    `;
    renderPetPagination(0);
    return;
  }

  const totalPages = Math.max(1, Math.ceil(data.length / PET_PAGE_SIZE));
  petListPage = Math.min(Math.max(petListPage, 1), totalPages);
  const start = (petListPage - 1) * PET_PAGE_SIZE;
  const pageData = data.slice(start, start + PET_PAGE_SIZE);

  pageData.forEach(pet => {
    const owner = findCustomerRecord(pet.customer_id);

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">${getPetIcon(pet.pet_type)} ${pet.pet_name}</span>
        <span class="profile-sub">${pet.pet_type || "—"} · ${pet.breed || "—"} · ${pet.size || "—"}</span>
      </td>

      <td>
        ${owner?.full_name || "Unknown"}
        <span class="profile-sub">${owner?.phone_number || "—"}</span>
      </td>

      <td>
        <span class="badge blue">${pet.vaccination_status || "Unknown"}</span>
      </td>

      <td>
        ${
          pet.health_notes
            ? `<span class="key-chip">${pet.health_notes}</span>`
            : `<span class="profile-sub">No special care note</span>`
        }
      </td>

      <td>
        <button class="action-btn" onclick="openPetForm(${pet.pet_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    `;

    petTableBody.appendChild(row);
  });

  renderPetPagination(totalPages);
}

// Mirrors renderCustomerPagination/goToCustomerPage above — see that
// comment for why paging re-slices lastFilteredPets instead of refetching.
function renderPetPagination(totalPages) {
  const el = document.getElementById("petPagination");
  if (!el) return;
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }

  const pages = buildPageNumbers(petListPage, totalPages);
  let html = `<button class="page-btn" type="button" ${petListPage === 1 ? "disabled" : ""} onclick="goToPetPage(${petListPage - 1})">‹ Prev</button>`;
  let prevPage = 0;
  pages.forEach(p => {
    if (p - prevPage > 1) html += `<span class="page-ellipsis">…</span>`;
    html += `<button class="page-btn ${p === petListPage ? "active" : ""}" type="button" onclick="goToPetPage(${p})">${p}</button>`;
    prevPage = p;
  });
  html += `<button class="page-btn" type="button" ${petListPage === totalPages ? "disabled" : ""} onclick="goToPetPage(${petListPage + 1})">Next ›</button>`;

  el.innerHTML = html;
}

function goToPetPage(page) {
  petListPage = page;
  renderPetTable(lastFilteredPets);
}

function openCustomerForm(customerId = null) {
  const isEdit = customerId !== null && customerId !== undefined;
  const customer = isEdit
    ? findCustomerRecord(customerId)
    : { customer_id: null, full_name: "", phone_number: "", address: "" };
  if (isEdit && !customer) return;
  if (isEdit) setRecordUrl("customer_id", customer.customer_id);
  else clearRecordUrl(false);

  const linkedPets = isEdit ? getPetsForCustomer(customer.customer_id) : [];

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit
    ? `Customer Info · CUST-${customer.customer_id}`
    : "New Customer";

  detailForm.innerHTML = `
    ${isEdit ? `
    <div class="form-group">
      <label>Customer ID</label>
      <input value="CUST-${customer.customer_id}" readonly />
    </div>` : ""}

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
      <input name="address" value="${customer.address || ""}" placeholder="Enter address" required />
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
                  <span class="profile-sub">${pet.pet_type || "—"} · ${pet.breed || "—"} · ${pet.size || "—"}</span>
                  <span class="profile-sub">Vaccination: ${pet.vaccination_status || "Unknown"}${pet.health_notes ? ` &nbsp;|&nbsp; Care: ${pet.health_notes}` : ""}</span>
                </div>
                <button type="button" class="action-btn" onclick="openPetForm(${pet.pet_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
              </div>
            `).join("")
        }
      </div>
    </div>

    <div class="form-group full">
      <label>Recent Bookings</label>
      <div class="crm-pet-list crm-list-scroll">
        ${(() => {
          const recentBookings = getRecentBookingsForCustomer(customer.customer_id);
          if (recentBookings.length === 0) return `<p class="crm-pet-empty">No bookings yet.</p>`;
          return recentBookings.map(b => {
            const pet = findPetRecord(b.pet_id);
            return `
              <div class="crm-pet-card">
                <div class="crm-pet-info">
                  <span class="profile-name">${b.serviceLabel} <span class="profile-sub">(${b.type})</span></span>
                  <span class="profile-sub">${pet?.pet_name || "Unknown pet"} · ${formatDate(b.date)}${b.time ? ` ${b.time}` : ""}</span>
                </div>
                <div style="display:flex;align-items:center;gap:10px;">
                  <span class="profile-sub">RM ${b.amount.toFixed(2)}</span>
                  ${renderStatusTag(b.status)}
                  <button type="button" class="action-btn" onclick="openCustomerBookingDetail('${b.type}', ${b.id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
                </div>
              </div>
            `;
          }).join("");
        })()}
      </div>
    </div>
    ` : ""}

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeCustomer(${customer.customer_id})"><img src="icon/delete.png" alt="" class="btn-icon">Remove Customer</button>` : ""}
      <button type="submit" class="save-btn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">${isEdit ? "Save Customer" : "Create Customer"}</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const payload = {
      full_name: String(formData.get("full_name") || "").trim(),
      phone_number: String(formData.get("phone_number") || "").trim(),
      address: String(formData.get("address") || "").trim(),
    };

    if (!payload.full_name || !payload.phone_number || !payload.address) {
      alert("Name, mobile number, and address are required.");
      return;
    }

    const submitBtn = detailForm.querySelector(".save-btn");
    if (submitBtn) submitBtn.disabled = true;

    try {
      if (isEdit) {
        await api.patch(`/customers/${customer.customer_id}`, payload);
      } else {
        await api.post("/customers", payload);
      }
      closeDetailPage();
      await refreshCrmData();
    } catch (error) {
      alert(error.message || "Failed to save customer record.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  };
}

// Read-only drill-in from the "Recent Bookings" list on the customer detail
// page. Fetches the single booking fresh (profile.html only carries the
// slimmed-down allBookingsFlat shape) and reuses the shared #detailPage
// modal, same as openPaymentDetail/openEnquiryDetailPage do elsewhere.
async function openCustomerBookingDetail(type, id) {
  const booking = await api.get(`/bookings/${type}/${id}`).catch(error => {
    alert(error.message || "Failed to load booking.");
    return null;
  });
  if (!booking) return;
  setRecordUrl(`${type}_booking_id`, id);

  const pet = findPetRecord(booking.pet_id);
  const status = normalizeBookingStatus(booking.booking_status);
  const note = value => (value && value !== "-" ? value : "—");

  const typeFields = {
    grooming: `
      <div class="form-group"><label>Service</label><input value="${booking.service_name || "—"}" readonly /></div>
      <div class="form-group"><label>Date</label><input value="${formatDate(booking.booking_date)}" readonly /></div>
      <div class="form-group"><label>Time</label><input value="${booking.booking_time ? booking.booking_time.slice(0, 5) : "—"}" readonly /></div>
      <div class="form-group"><label>Price</label><input value="RM ${Number(booking.price || 0).toFixed(2)}" readonly /></div>
      <div class="form-group"><label>Add-on</label><input value="${booking.add_on && booking.add_on !== "-" ? `${booking.add_on} (+RM ${Number(booking.add_on_price || 0).toFixed(2)})` : "—"}" readonly /></div>
      <div class="form-group full"><label>Notes</label><input value="${note(booking.notes)}" readonly /></div>
    `,
    daycare: `
      <div class="form-group"><label>Package</label><input value="${booking.package_type || "—"}" readonly /></div>
      <div class="form-group"><label>Date</label><input value="${formatDate(booking.booking_date)}" readonly /></div>
      <div class="form-group"><label>Check-in / Check-out</label><input value="${(booking.check_in_time || "").slice(0, 5)} – ${(booking.check_out_time || "").slice(0, 5)}" readonly /></div>
      <div class="form-group"><label>Price</label><input value="RM ${Number(booking.price || 0).toFixed(2)}" readonly /></div>
      <div class="form-group full"><label>Special Instruction</label><input value="${note(booking.special_instruction)}" readonly /></div>
    `,
    boarding: `
      <div class="form-group"><label>Room</label><input value="${booking.room_type || "—"}" readonly /></div>
      <div class="form-group"><label>Check-in</label><input value="${formatDate(booking.check_in_date)} ${(booking.check_in_time || "").slice(0, 5)}" readonly /></div>
      <div class="form-group"><label>Check-out</label><input value="${formatDate(booking.check_out_date)} ${(booking.check_out_time || "").slice(0, 5)}" readonly /></div>
      <div class="form-group"><label>Price / Night</label><input value="RM ${Number(booking.price_per_night || 0).toFixed(2)}" readonly /></div>
      <div class="form-group"><label>Total Price</label><input value="RM ${Number(booking.total_price || 0).toFixed(2)}" readonly /></div>
      <div class="form-group full"><label>Feeding Instruction</label><input value="${note(booking.feeding_instruction)}" readonly /></div>
      <div class="form-group full"><label>Medical Instruction</label><input value="${note(booking.medical_instruction)}" readonly /></div>
    `,
  }[type] || "";

  detailPage.style.display = "flex";
  detailTitle.textContent = `${type.charAt(0).toUpperCase()}${type.slice(1)} Booking · #${id}`;

  detailForm.innerHTML = `
    <div class="form-group"><label>Pet</label><input value="${pet?.pet_name || "Unknown pet"}" readonly /></div>
    <div class="form-group"><label>Status</label><input value="${formatStatus(status)}" readonly /></div>
    ${typeFields}
    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
    </div>
  `;
  detailForm.onsubmit = null;
}

function openPetForm(petId = null) {
  const isEdit = petId !== null && petId !== undefined;
  if (!isEdit && customerRecords.length === 0) {
    alert("Create a customer before adding a pet.");
    return;
  }
  const pet = isEdit
    ? findPetRecord(petId)
    : {
        pet_id: null,
        customer_id: customerRecords[0]?.customer_id ?? "",
        pet_name: "",
        pet_type: "Cat",
        gender: "Male",
        date_of_birth: "",
        breed: "",
        height_cm: "",
        size: "",
        vaccination_status: "Not Vaccinated",
        vaccination_expired_date: "",
        health_notes: "",
        service_notes: ""
      };
  if (isEdit && !pet) return;
  if (isEdit) setRecordUrl("pet_id", pet.pet_id);
  else clearRecordUrl(false);

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit
    ? `Pet Profile · ${pet.pet_name}`
    : "New Pet";

  detailForm.innerHTML = `
    <div class="form-group full">
      <label>Owner</label>
      <select name="customer_id" required>
        ${customerRecords.map(customer => `
          <option value="${customer.customer_id}" ${String(customer.customer_id) === String(pet.customer_id) ? "selected" : ""}>
            ${customer.full_name} · CUST-${customer.customer_id}
          </option>
        `).join("")}
      </select>
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
        <option value="Other" ${pet.pet_type === "Other" ? "selected" : ""}>Other</option>
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
      <input type="date" name="date_of_birth" max="${getToday()}" value="${String(pet.date_of_birth || "").slice(0, 10)}" />
    </div>

    <div class="form-group">
      <label>Breed</label>
      <input name="breed" value="${pet.breed || ""}" placeholder="Enter breed" />
    </div>

    <div class="form-group">
      <label>Height (cm)</label>
      <input type="number" name="height_cm" min="0" step="0.1" value="${pet.height_cm ?? ""}" placeholder="e.g. 35" />
    </div>

    <div class="form-group">
      <label>Size</label>
      <input name="size" value="${pet.size || ""}" placeholder="S / M / L" />
    </div>

    <div class="form-group">
      <label>Vaccination Status</label>
      <select name="vaccination_status">
        <option value="Vaccinated" ${pet.vaccination_status === "Vaccinated" ? "selected" : ""}>Vaccinated</option>
        <option value="Not Vaccinated" ${pet.vaccination_status === "Not Vaccinated" ? "selected" : ""}>Not Vaccinated</option>
        <option value="Pending" ${pet.vaccination_status === "Pending" ? "selected" : ""}>Pending</option>
      </select>
    </div>

    <div class="form-group">
      <label>Vaccination Expiry Date</label>
      <input type="date" name="vaccination_expired_date" value="${String(pet.vaccination_expired_date || "").slice(0, 10)}" />
    </div>

    <div class="form-group full">
      <label>Health Notes</label>
      <input name="health_notes" value="${pet.health_notes || ""}" placeholder="e.g. Mild food allergy" />
    </div>

    <div class="form-group full">
      <label>Service Notes</label>
      <textarea name="service_notes" placeholder="Feeding instruction, room preference, grooming reminders">${pet.service_notes || ""}</textarea>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removePet(${pet.pet_id})"><img src="icon/delete.png" alt="" class="btn-icon">Remove Pet</button>` : ""}
      <button type="submit" class="save-btn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">${isEdit ? "Save Pet" : "Create Pet"}</button>
    </div>
  `;

  detailForm.onsubmit = async function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const payload = {
      customer_id: Number(formData.get("customer_id")),
      pet_name: String(formData.get("pet_name") || "").trim(),
      pet_type: formData.get("pet_type"),
      gender: formData.get("gender"),
      date_of_birth: formData.get("date_of_birth") || null,
      breed: formData.get("breed") || null,
      height_cm: formData.get("height_cm") ? Number(formData.get("height_cm")) : null,
      size: formData.get("size") || null,
      vaccination_status: formData.get("vaccination_status"),
      vaccination_expired_date: formData.get("vaccination_expired_date") || null,
      health_notes: formData.get("health_notes") || null,
      service_notes: formData.get("service_notes") || null,
    };

    if (!payload.customer_id || !payload.pet_name) {
      alert("Owner and pet name are required.");
      return;
    }
    if (payload.height_cm != null && payload.height_cm < 0) {
      alert("Height cannot be negative.");
      return;
    }

    const submitBtn = detailForm.querySelector(".save-btn");
    if (submitBtn) submitBtn.disabled = true;

    try {
      if (isEdit) {
        await api.patch(`/pets/${pet.pet_id}`, payload);
      } else {
        await api.post("/pets", payload);
      }
      closeDetailPage();
      await refreshCrmData();
    } catch (error) {
      alert(error.message || "Failed to save pet record.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  };
}

function closeDetailPage() {
  detailPage.style.display = "none";
  detailForm.innerHTML = "";
  clearRecordUrl();
}

async function removeCustomer(customerId) {
  if (!confirm(`Remove customer CUST-${customerId} and all their linked pets? This cannot be undone.`)) return;

  try {
    await api.del(`/customers/${customerId}/with-pets`);
    closeDetailPage();
    await refreshCrmData();
  } catch (error) {
    alert(error.message || "Failed to remove customer.");
  }
}

async function removePet(petId) {
  if (!confirm(`Remove pet PET-${petId}? This cannot be undone.`)) return;

  try {
    await api.del(`/pets/${petId}`);
    closeDetailPage();
    await refreshCrmData();
  } catch (error) {
    alert(error.message || "Failed to remove pet.");
  }
}

function getPetIcon(petType) {
  return petType === "Cat" ? "🐱" : petType === "Dog" ? "🐶" : "🐾";
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

let loyaltyMemberRequests = [];

const LOYALTY_TIERS = [
  { min: 1200, name: 'Platinum' },
  { min: 700,  name: 'Gold' },
  { min: 300,  name: 'Silver' },
  { min: 0,    name: 'Bronze' }
];

function getLoyaltyTier(points) {
  return LOYALTY_TIERS.find(tier => points >= tier.min).name;
}

function buildLoyaltyMembers() {
  return loyaltyMemberRecords.map(member => {
    const customer = getCustomerById(member.customer_id);
    const points = Number(member.points_balance || 0);
    return {
      member_id: `LOY-${member.loyalty_id}`,
      full_name: customer?.full_name || "Unknown customer",
      phone: customer?.phone_number || "—",
      points,
      tier: member.tier || getLoyaltyTier(points),
    };
  });
}

function countApprovedRedemptions(fullName) {
  const member = loyaltyMemberRecords.find(m => getCustomerById(m.customer_id)?.full_name === fullName);
  return member?.redemption_made || 0;
}

const loyaltySearchInput = document.getElementById("loyaltySearchInput");
const loyaltyPendingBody = document.getElementById("loyaltyPendingBody");
const loyaltyPendingRedemptionBody = document.getElementById("loyaltyPendingRedemptionBody");
const loyaltyMemberBody = document.getElementById("loyaltyMemberBody");
const loyaltyPendingRecordCount = document.getElementById("loyaltyPendingRecordCount");
const loyaltyPendingRedemptionRecordCount = document.getElementById("loyaltyPendingRedemptionRecordCount");
const loyaltyMemberRecordCount = document.getElementById("loyaltyMemberRecordCount");

// Real backend data. A redemption row is created atomically when staff
// verify a payment (payment.html, already wired) — this tab lists that
// ledger straight from the `redemption` table.
let loyaltyMemberRecords = [];
let loyaltyCouponRecords = [];
let loyaltyRedemptionRecords = [];
let loyaltyEarnRate = null;
let loyaltyDateFilter = getToday();

async function initLoyaltyPage() {
  if (!isManager(getCurrentAccount())) {
    document.querySelector('#loyaltyTabs [data-panel="rules"]')?.remove();
  }
  document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("loyaltyPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("loyaltyPendingRedemptionPanel").classList.toggle("hidden", btn.dataset.panel !== "pendingRedemption");
      document.getElementById("loyaltyMembersPanel").classList.toggle("hidden", btn.dataset.panel !== "members");
      document.getElementById("loyaltyRulesPanel").classList.toggle("hidden", btn.dataset.panel !== "rules");
    });
  });

  loyaltySearchInput.addEventListener("input", () => {
    resetListPage("loyaltyPending");
    resetListPage("loyaltyPendingRedemption");
    resetListPage("loyaltyMembers");
    renderLoyaltyLists();
  });

  registerListRerenderer("loyaltyPending", renderLoyaltyLists);
  registerListRerenderer("loyaltyPendingRedemption", renderLoyaltyLists);
  registerListRerenderer("loyaltyMembers", renderLoyaltyLists);

  bindDateNavigator("loyaltyDate", {
    getDate: () => loyaltyDateFilter,
    setDate: date => { loyaltyDateFilter = date; },
    onChange: () => {
      resetListPage("loyaltyMembers");
      updateLoyaltyKPI();
      renderLoyaltyLists();
    },
  });

  await refreshLoyaltyPageData();
}

async function refreshLoyaltyPageData() {
  try {
    const [members, customersList, coupons, redemptions, company] = await Promise.all([
      api.get("/member-info"),
      api.get("/customers"),
      api.get("/coupons"),
      api.get("/redemptions"),
      api.get("/companies/me"),
    ]);

    loyaltyMemberRecords = members;
    loyaltyCouponRecords = coupons;
    loyaltyRedemptionRecords = redemptions;
    loyaltyEarnRate = company.settings_json?.loyalty_earn_rate ?? null;
    bookingCustomerOptions = customersList; // populates findBookingCustomer() used below
  } catch (error) {
    alert(error.message || "Failed to load loyalty data.");
  }

  updateLoyaltyKPI();
  renderLoyaltyLists();
  renderLoyaltyRulesPanel();
}

function updateLoyaltyKPI() {
  // All-time, not scoped to the date filter — matches the Redemption
  // History table below (see renderLoyaltyPendingTable()), since seed/real
  // redemption dates don't cluster around "today".
  const datedMembers = filterRecordsByDateIfPresent(loyaltyMemberRecords, loyaltyDateFilter, ["create_date", "created_at", "join_date"]);
  const pointsRedeemed = loyaltyRedemptionRecords
    .filter(r => String(r.status || "").toLowerCase() !== "refunded")
    .reduce((sum, r) => sum + (Number(r.loyalty_spend) || 0), 0);

  document.getElementById("loyaltyTotalMembers").textContent = datedMembers.length;
  document.getElementById("loyaltyPendingCount").textContent = loyaltyRedemptionRecords
    .filter(r => String(r.status || "").toLowerCase() === "pending").length;
  document.getElementById("loyaltyGoldCount").textContent = datedMembers.filter(m => m.tier === "Gold" || m.tier === "Platinum").length;
  document.getElementById("loyaltyPointsRedeemed").textContent = pointsRedeemed.toLocaleString();
}

function renderLoyaltyLists() {
  const searchValue = loyaltySearchInput.value.toLowerCase().trim();
  renderLoyaltyPendingTable(searchValue);
  renderLoyaltyPendingRedemptionTable(searchValue);
  renderLoyaltyMemberTable(searchValue);
}

function renderLoyaltyPendingRedemptionTable(searchValue) {
  const pending = loyaltyRedemptionRecords.filter(redemption => {
    if (String(redemption.status || "").toLowerCase() !== "pending") return false;
    const member = loyaltyMemberRecords.find(m => String(m.loyalty_id) === String(redemption.loyalty_id));
    const customer = member ? findBookingCustomer(member.customer_id) : null;
    return (customer?.full_name || "").toLowerCase().includes(searchValue) ||
      String(redemption.redemption_id).includes(searchValue);
  }).sort((a, b) => (a.create_date || a.created_at || "").localeCompare(b.create_date || b.created_at || ""));

  loyaltyPendingRedemptionRecordCount.textContent = `${pending.length} records`;

  if (pending.length === 0) {
    loyaltyPendingRedemptionBody.innerHTML = `<tr><td colspan="7" class="empty-row">No pending redemption records.</td></tr>`;
    renderListPagination("loyaltyPendingRedemption", 0);
    return;
  }

  const pendingPage = paginateList("loyaltyPendingRedemption", pending, 5);

  loyaltyPendingRedemptionBody.innerHTML = pendingPage.map(redemption => {
    const member = loyaltyMemberRecords.find(m => String(m.loyalty_id) === String(redemption.loyalty_id));
    const customer = member ? findBookingCustomer(member.customer_id) : null;
    const coupon = loyaltyCouponRecords.find(c => String(c.coupon_id) === String(redemption.coupon_id));
    return `
    <tr>
      <td><span class="key-chip">REDM-${redemption.redemption_id}</span></td>
      <td><span class="profile-name">${escapeUiText(customer?.full_name || "Unknown")}</span></td>
      <td>${Number(redemption.loyalty_earn || 0).toLocaleString()} pts</td>
      <td>${Number(redemption.loyalty_spend || 0).toLocaleString()} pts</td>
      <td>${escapeUiText(coupon?.reward_name || "—")}</td>
      <td><span class="status-tag status-pending">Pending</span></td>
      <td><button type="button" class="action-btn" onclick="openRedemptionDetail(${redemption.redemption_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
    </tr>
  `;
  }).join("");
}

function renderLoyaltyPendingTable(searchValue) {
  // All-time, not scoped to the date filter above — see updateLoyaltyKPI()
  // for why. The date filter stays for layout parity with payment.html; it
  // doesn't narrow this list.
  const history = loyaltyRedemptionRecords.filter(r => {
    const member = loyaltyMemberRecords.find(m => String(m.loyalty_id) === String(r.loyalty_id));
    const customer = member ? findBookingCustomer(member.customer_id) : null;
    return (customer?.full_name || "").toLowerCase().includes(searchValue) ||
      String(r.redemption_id).includes(searchValue);
  }).sort((a, b) => (b.create_date || "").localeCompare(a.create_date || ""));

  loyaltyPendingRecordCount.textContent = `${history.length} records`;

  if (history.length === 0) {
    loyaltyPendingBody.innerHTML = `<tr><td colspan="6" class="empty-row">No redemption records found.</td></tr>`;
    renderListPagination("loyaltyPending", 0);
    return;
  }

  const historyPage = paginateList("loyaltyPending", history, 5);

  loyaltyPendingBody.innerHTML = historyPage.map(r => {
    const member = loyaltyMemberRecords.find(m => String(m.loyalty_id) === String(r.loyalty_id));
    const customer = member ? findBookingCustomer(member.customer_id) : null;
    const coupon = loyaltyCouponRecords.find(c => String(c.coupon_id) === String(r.coupon_id));
    return `
    <tr>
      <td><span class="key-chip">REDM-${r.redemption_id}</span></td>
      <td><span class="profile-name">${escapeUiText(customer?.full_name || "Unknown")}</span></td>
      <td>${Number(r.loyalty_earn || 0).toLocaleString()} pts</td>
      <td>${Number(r.loyalty_spend || 0).toLocaleString()} pts</td>
      <td>${escapeUiText(coupon?.reward_name || "—")}</td>
      <td><button type="button" class="action-btn" onclick="openRedemptionDetail(${r.redemption_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
    </tr>
  `;
  }).join("");
}

function openRedemptionDetail(redemptionId) {
  const redemption = loyaltyRedemptionRecords.find(r => String(r.redemption_id) === String(redemptionId));
  if (!redemption) return;
  setRecordUrl("redemption_id", redemption.redemption_id);

  const member = loyaltyMemberRecords.find(m => String(m.loyalty_id) === String(redemption.loyalty_id));
  const customer = member ? findBookingCustomer(member.customer_id) : null;
  const coupon = loyaltyCouponRecords.find(c => String(c.coupon_id) === String(redemption.coupon_id));
  const status = redemption.status || "—";
  const recordDate = redemption.create_date || redemption.created_at || "—";

  detailPage.style.display = "flex";
  detailTitle.textContent = `Redemption Detail · REDM-${redemption.redemption_id}`;
  detailForm.onsubmit = null;
  detailForm.innerHTML = `
    <div class="form-group">
      <label>Redemption ID</label>
      <input value="REDM-${redemption.redemption_id}" readonly />
    </div>
    <div class="form-group">
      <label>Status</label>
      <input value="${escapeUiText(status)}" readonly />
    </div>
    <div class="form-group">
      <label>Member</label>
      <input value="${escapeUiText(customer?.full_name || "Unknown")}" readonly />
    </div>
    <div class="form-group">
      <label>Loyalty ID</label>
      <input value="${member ? `LOY-${member.loyalty_id}` : "—"}" readonly />
    </div>
    <div class="form-group">
      <label>Points Earned</label>
      <input value="${Number(redemption.loyalty_earn || 0).toLocaleString()} pts" readonly />
    </div>
    <div class="form-group">
      <label>Points Spent</label>
      <input value="${Number(redemption.loyalty_spend || 0).toLocaleString()} pts" readonly />
    </div>
    <div class="form-group">
      <label>Coupon</label>
      <input value="${escapeUiText(coupon?.reward_name || "—")}" readonly />
    </div>
    <div class="form-group">
      <label>Record Date</label>
      <input value="${escapeUiText(recordDate)}" readonly />
    </div>
    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
    </div>
  `;
}

function renderLoyaltyMemberTable(searchValue) {
  const datedMembers = filterRecordsByDateIfPresent(loyaltyMemberRecords, loyaltyDateFilter, ["create_date", "created_at", "join_date"]);
  const members = datedMembers.filter(m => {
    const customer = findBookingCustomer(m.customer_id);
    return (customer?.full_name || "").toLowerCase().includes(searchValue) ||
      (customer?.phone_number || "").toLowerCase().includes(searchValue) ||
      String(m.loyalty_id).includes(searchValue);
  });

  loyaltyMemberRecordCount.textContent = `${members.length} members`;

  if (members.length === 0) {
    loyaltyMemberBody.innerHTML = `<tr><td colspan="6" class="empty-row">No member record found.</td></tr>`;
    renderListPagination("loyaltyMembers", 0);
    return;
  }

  const sortedMembers = members.slice().sort((a, b) => b.points_balance - a.points_balance);
  const membersPage = paginateList("loyaltyMembers", sortedMembers, 5);

  loyaltyMemberBody.innerHTML = membersPage
    .map(m => {
      const customer = findBookingCustomer(m.customer_id);
      return `
      <tr>
        <td>
          <span class="profile-name">👤 ${customer?.full_name || "Unknown"}</span>
          <span class="profile-sub">${customer?.phone_number || "—"}</span>
        </td>
        <td><span class="key-chip">LOY-${m.loyalty_id}</span></td>
        <td><span class="status-tag status-${m.tier.toLowerCase()}">${m.tier}</span></td>
        <td>${Number(m.points_balance).toLocaleString()} pts</td>
        <td>${m.redemption_made}</td>
        <td><button class="action-btn" onclick="openLoyaltyMemberDetail(${m.loyalty_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
      </tr>
    `;
    }).join("");
}

function openLoyaltyMemberDetail(loyaltyId) {
  const member = loyaltyMemberRecords.find(m => m.loyalty_id === loyaltyId);
  if (!member) return;
  setRecordUrl("loyalty_id", member.loyalty_id);
  const customer = findBookingCustomer(member.customer_id);

  const memberRedemptions = loyaltyRedemptionRecords
    .filter(r => r.loyalty_id === loyaltyId)
    .sort((a, b) => (b.create_date || "").localeCompare(a.create_date || ""));

  detailPage.style.display = "flex";
  detailTitle.textContent = `Member Info · LOY-${member.loyalty_id}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Loyalty ID</label>
      <input value="LOY-${member.loyalty_id}" readonly />
    </div>

    <div class="form-group">
      <label>Name</label>
      <input value="${customer?.full_name || "Unknown"}" readonly />
    </div>

    <div class="form-group">
      <label>Phone</label>
      <input value="${customer?.phone_number || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Tier</label>
      <input value="${member.tier}" readonly />
    </div>

    <div class="form-group">
      <label>Points Balance</label>
      <input value="${Number(member.points_balance).toLocaleString()} pts" readonly />
    </div>

    <div class="form-group">
      <label>Redemptions Made</label>
      <input value="${member.redemption_made}" readonly />
    </div>

    <div class="form-group full">
      <label>Redemption Ledger</label>
      <div class="crm-pet-list">
        ${memberRedemptions.length === 0
          ? `<p class="crm-pet-empty">No redemptions on record for this member.</p>`
          : memberRedemptions.map(r => `
              <div class="crm-pet-card">
                <div class="crm-pet-info">
                  <span class="profile-name">Redemption REDM-${r.redemption_id}</span>
                  <span class="profile-sub">Earned ${r.loyalty_earn} pts · Spent ${r.loyalty_spend} pts · ${r.create_date || "-"}</span>
                  <span class="status-tag status-${String(r.status).toLowerCase() === "refunded" ? "cancelled" : "done"}">${r.status || "Approved"}</span>
                </div>
              </div>
            `).join("")
        }
      </div>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      <a class="btn btn-secondary" href="payment.html"><img src="icon/payment-card.png" alt="" class="btn-icon">Open Payment</a>
    </div>
  `;
}

/* ==========================================================================
   LOYALTY PROGRAM RULES (loyalty.html)
   Points-per-RM earn rate and the redemption rules (points -> discount or
   free service) are configurable and persisted per business, so they carry
   over across sessions the same way business settings do.
   ========================================================================== */

// Real backend: redemption rules map directly onto the `coupon` table
// (already wired for other CRUD elsewhere in this file) via /api/coupons.
// The earn rate is stored in companies.settings_json.loyalty_earn_rate (see
// backend/src/routes/companies.js) — falls back to the backend's
// LOYALTY_EARN_RATE env default (shown as blank here) until a company sets
// its own.
function renderLoyaltyRulesPanel() {
  const earnRateInput = document.getElementById("loyaltyEarnRateInput");
  if (earnRateInput) earnRateInput.value = loyaltyEarnRate ?? "";

  document.getElementById("loyaltyRulesBody").innerHTML = loyaltyCouponRecords.map(c => `
    <tr>
      <td>${Number(c.points_required).toLocaleString()} pts</td>
      <td>${c.reward_name}</td>
      <td>${c.reward_type === "Discount (RM value)" ? `Discount (RM ${c["discount_value (RM)"]})` : "Free Service"}</td>
      <td>
        <button class="edit-btn" onclick="openRuleForm(${c.coupon_id})">Edit</button>
      </td>
    </tr>
  `).join("");
}

async function saveLoyaltyEarnRate() {
  const rate = Number(document.getElementById("loyaltyEarnRateInput").value);
  if (Number.isNaN(rate) || rate < 0) { alert("Please enter a valid earn rate."); return; }

  try {
    await api.patch("/companies/me", { settings: { loyalty_earn_rate: rate } });
    await refreshLoyaltyPageData();
  } catch (error) {
    alert(error.message || "Failed to save earn rate.");
  }
}

async function removeLoyaltyRule(couponId) {
  if (!confirm("Remove this redemption rule? This cannot be undone.")) return;
  try {
    await api.del(`/coupons/${couponId}`);
    closeDetailPage();
    await refreshLoyaltyPageData();
  } catch (error) {
    alert(error.message || "Failed to remove rule.");
  }
}

function openRuleForm(couponId = null) {
  const isEdit = couponId !== null && couponId !== undefined;
  const rule = isEdit
    ? loyaltyCouponRecords.find(c => c.coupon_id === couponId)
    : { coupon_id: null, points_required: "", reward_name: "", reward_type: "Discount (RM value)", "discount_value (RM)": "", expiry_date: "" };
  if (isEdit && !rule) return;
  if (isEdit) setRecordUrl("coupon_id", rule.coupon_id);
  else clearRecordUrl(false);

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

    <div class="form-group ${rule.reward_type !== "Discount (RM value)" ? "hidden" : ""}" id="ruleValueGroup">
      <label>Discount Value (RM)</label>
      <input type="number" name="discount_value" min="0" placeholder="e.g. 10" value="${rule["discount_value (RM)"] || ""}" />
    </div>

    <div class="form-group">
      <label>Expiry Date</label>
      <input type="date" name="expiry_date" value="${rule.expiry_date || ""}" />
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
      "discount_value (RM)": rewardType === "Discount (RM value)" ? String(Number(formData.get("discount_value")) || 0) : "0",
      expiry_date: formData.get("expiry_date") || null,
    };

    const submitBtn = detailForm.querySelector(".save-btn");
    if (submitBtn) submitBtn.disabled = true;

    try {
      if (isEdit) {
        await api.patch(`/coupons/${rule.coupon_id}`, payload);
      } else {
        await api.post("/coupons", payload);
      }
      closeDetailPage();
      await refreshLoyaltyPageData();
    } catch (error) {
      alert(error.message || "Failed to save rule.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  };
}

/* ==========================================================================
   PAYMENT (payment.html)
   Each booking becomes one payment record: final amount = base service price
   + add-ons − loyalty redemption deduction (RM-voucher redemptions deduct
   their RM value; "Free ..." redemptions cover the full base price). Each
   approved loyalty redemption is applied to at most one matching booking.
   ========================================================================== */

// Real backend data for this page (separate from the mock `paymentRecords`
// array above, which dashboard.html still reads until it's wired too).
// `payment` rows aren't joined to a customer/pet by the list endpoint (only
// GET /api/payments/:id joins through booking -> pet -> loyaltymember), and
// `redemption` rows have no payment_id/booking link at all — so the
// "Loyalty Discounts Given" KPI below is a company-wide best-effort total,
// not an exact sum over the payments currently shown.
let paymentRows = [];
let couponsCache = [];
let customersCacheForPayments = [];
let petsCacheForPayments = [];
let paymentCompanySettings = {};
let paymentDateFilter = getToday();
let paymentAccountRole = null;

function parseAddOnPriceFromText(addOnsText) {
  const match = String(addOnsText || "").match(/\(\+RM([\d.]+)\)/);
  return match ? Number(match[1]) : 0;
}

const paymentSearchInput = document.getElementById("paymentSearchInput");
const paymentPendingBody = document.getElementById("paymentPendingBody");
const paymentHistoryBody = document.getElementById("paymentHistoryBody");
const paymentPendingRecordCount = document.getElementById("paymentPendingRecordCount");
const paymentHistoryRecordCount = document.getElementById("paymentHistoryRecordCount");

async function initPaymentPage() {
  document.querySelectorAll("#paymentTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#paymentTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("paymentPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("paymentHistoryPanel").classList.toggle("hidden", btn.dataset.panel !== "history");
    });
  });

  paymentSearchInput.addEventListener("input", () => {
    resetListPage("paymentPending");
    resetListPage("paymentHistory");
    renderPaymentLists();
  });

  registerListRerenderer("paymentPending", renderPaymentLists);
  registerListRerenderer("paymentHistory", renderPaymentLists);

  bindDateNavigator("paymentDate", {
    getDate: () => paymentDateFilter,
    setDate: date => { paymentDateFilter = date; },
    onChange: async () => {
      resetListPage("paymentHistory");
      await updatePaymentKPI();
      await renderPaymentLists();
    },
  });

  await refreshPaymentPageData();
}

async function refreshPaymentPageData() {
  const [payments, coupons, customersList, petsList, grooming, daycare, boarding, company, me] = await Promise.all([
    api.get("/payments"),
    api.get("/coupons"),
    api.get("/customers"),
    api.get("/pets"),
    api.get("/bookings/grooming"),
    api.get("/bookings/daycare"),
    api.get("/bookings/boarding"),
    api.get("/companies/me"),
    api.get("/accounts/me"),
  ]);
  couponsCache = coupons;
  customersCacheForPayments = customersList;
  petsCacheForPayments = petsList;
  paymentRows = enrichPaymentRowsLocally(payments, [...grooming, ...daycare, ...boarding], petsList, customersList);
  paymentCompanySettings = company.settings_json || {};
  paymentAccountRole = me.role;

  await updatePaymentKPI();
  await renderPaymentLists();
}

// Older API deployments returned raw payment rows without customer_name or
// pet_name. Rebuild those labels from the same persisted relationship used
// by the backend: payment -> booking -> pet -> customer. Values already
// supplied by the backend remain authoritative.
function enrichPaymentRowsLocally(payments, bookingRows, pets, customers) {
  const bookingByPaymentId = new Map(
    bookingRows
      .filter(booking => booking.payment_id != null)
      .map(booking => [String(booking.payment_id), booking])
  );
  const petById = new Map(pets.map(pet => [String(pet.pet_id), pet]));
  const customerById = new Map(customers.map(customer => [String(customer.customer_id), customer]));

  return payments.map(payment => {
    const booking = bookingByPaymentId.get(String(payment.payment_id));
    const pet = booking ? petById.get(String(booking.pet_id)) : null;
    const customer = pet ? customerById.get(String(pet.customer_id)) : null;
    return {
      ...payment,
      pet_id: payment.pet_id ?? pet?.pet_id ?? null,
      pet_name: payment.pet_name || pet?.pet_name || null,
      customer_id: payment.customer_id ?? customer?.customer_id ?? null,
      customer_name: payment.customer_name || customer?.full_name || null,
    };
  });
}

function isPaymentAwaitingVerification(payment) {
  return payment?.status === "Pending" || payment?.status === "Unpaid";
}

async function updatePaymentKPI() {
  const selectedRows = paymentRows.filter(p => recordDateValue(p, ["date", "paid_at", "created_at"]) === paymentDateFilter);
  // Matches the Pending Verification table below: not scoped to the date
  // filter, since an unverified payment stays actionable regardless of
  // which day it happened to be created on.
  const pending = paymentRows.filter(isPaymentAwaitingVerification);
  const paid = selectedRows.filter(p => p.status === "Paid");
  const totalRevenue = paid.reduce((sum, p) => sum + Number(p.final_amount || 0), 0);
  document.getElementById("paymentTotalRevenue").textContent = `RM ${totalRevenue.toLocaleString()}`;
  document.getElementById("paymentPendingCount").textContent = pending.length;
  document.getElementById("paymentTotalCount").textContent = selectedRows.length;

  try {
    const redemptions = await api.get("/redemptions");
    const datedRedemptions = filterRecordsByDateIfPresent(redemptions, paymentDateFilter, ["create_date", "created_at"]);
    const discountTotal = datedRedemptions.reduce((sum, r) => {
      if (!r.coupon_id || String(r.status || "").toLowerCase() === "refunded") return sum;
      const coupon = couponsCache.find(c => c.coupon_id === r.coupon_id);
      if (!coupon || coupon.reward_type !== "Discount (RM value)") return sum;
      return sum + (Number(coupon["discount_value (RM)"]) || 0);
    }, 0);
    document.getElementById("paymentLoyaltyDiscount").textContent = `RM ${discountTotal.toLocaleString()}`;
  } catch (error) {
    console.error(error);
    document.getElementById("paymentLoyaltyDiscount").textContent = "RM —";
  }
}

function filterPaymentRows(records, searchValue) {
  if (!searchValue) return records;
  return records.filter(p =>
    (p.service || "").toLowerCase().includes(searchValue) ||
    (p.customer_name || "").toLowerCase().includes(searchValue) ||
    (p.pet_name || "").toLowerCase().includes(searchValue) ||
    String(p.payment_id).includes(searchValue)
  );
}

async function renderPaymentLists() {
  const searchValue = paymentSearchInput.value.toLowerCase().trim();
  // Pending Verification isn't scoped to the date filter — a payment can
  // sit unverified for days (that's the whole point of its SLA), so it
  // needs to keep showing up until someone actually verifies it, not just
  // on whichever single day the top filter happens to land on.
  const pending = filterPaymentRows(paymentRows.filter(isPaymentAwaitingVerification), searchValue)
    .sort((a, b) => new Date(a.created_at || a.date) - new Date(b.created_at || b.date));
  const selectedRows = paymentRows.filter(p => recordDateValue(p, ["date", "paid_at", "created_at"]) === paymentDateFilter);
  const history = filterPaymentRows(selectedRows.filter(p => !isPaymentAwaitingVerification(p)), searchValue)
    .sort((a, b) => new Date(b.date) - new Date(a.date));

  await renderPaymentPendingTable(pending);
  renderPaymentHistoryTable(history);
}

// Customer and pet labels are batch-resolved by GET /payments, avoiding a
// per-row request pattern as payment volume grows.
async function renderPaymentPendingTable(pending) {
  paymentPendingRecordCount.textContent = `${pending.length} pending`;

  if (pending.length === 0) {
    paymentPendingBody.innerHTML = `<tr><td colspan="8" class="empty-row">No payments awaiting verification.</td></tr>`;
    renderListPagination("paymentPending", 0);
    return;
  }

  const pendingPage = paginateList("paymentPending", pending, 5);

  const rows = pendingPage.map(p => ({
    payment: p,
    customerName: p.customer_name || "Unknown",
    petName: p.pet_name || "—",
  }));

  paymentPendingBody.innerHTML = rows.map(({ payment: p, customerName, petName }) => `
    <tr>
      <td><span class="key-chip">PAY-${String(p.payment_id).padStart(4, "0")}</span></td>
      <td>
        <span class="profile-name">${customerName}</span>
        <span class="profile-sub">${petName}</span>
      </td>
      <td>${p.service || "—"}</td>
      <td>RM ${Number(p.final_amount || 0).toLocaleString()}</td>
      <td>${p.payment_method || "—"}</td>
      <td>${formatDate(p.date)}</td>
      <td>${slaBadgeFromTimestamp('payment', p.created_at)}</td>
      <td>
        <button class="action-btn" onclick="openPaymentDetail(${p.payment_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    </tr>
  `).join("");
}

function renderPaymentHistoryTable(history) {
  paymentHistoryRecordCount.textContent = `${history.length} records`;

  if (history.length === 0) {
    paymentHistoryBody.innerHTML = `<tr><td colspan="13" class="empty-row">No transaction record found.</td></tr>`;
    renderListPagination("paymentHistory", 0);
    return;
  }

  const historyPage = paginateList("paymentHistory", history, 5);

  paymentHistoryBody.innerHTML = historyPage.map(p => {
    const addOnPrice = parseAddOnPriceFromText(p.add_ons);
    const discount = Math.max(0, Number(p.base_price || 0) + addOnPrice - Number(p.final_amount || 0));
    return `
    <tr>
      <td><span class="key-chip">PAY-${String(p.payment_id).padStart(4, "0")}</span></td>
      <td><span class="profile-name">${escapeUiText(p.customer_name || "Unknown")}</span><br><span class="profile-sub">${escapeUiText(p.pet_name || "—")}</span></td>
      <td>${p.service || "—"}</td>
      <td>RM ${Number(p.base_price || 0).toLocaleString()}</td>
      <td>${p.add_ons ? `<span class="key-chip">${p.add_ons}</span>` : `<span class="profile-sub">No add-on</span>`}</td>
      <td>${discount > 0 ? `− RM ${discount.toLocaleString()}` : "—"}</td>
      <td><strong>RM ${Number(p.final_amount || 0).toLocaleString()}</strong></td>
      <td>—</td>
      <td>—</td>
      <td>${p.payment_method || "—"}</td>
      <td><span class="status-tag status-${p.status === "Paid" ? "done" : "cancelled"}">${p.status}</span></td>
      <td>${p.refunded_at ? new Date(p.refunded_at).toLocaleDateString("en-MY") : p.paid_at ? new Date(p.paid_at).toLocaleDateString("en-MY") : formatDate(p.date)}</td>
      <td><button class="action-btn" onclick="openPaymentDetail(${p.payment_id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
    </tr>
  `;
  }).join("");
}

async function confirmVerifyPayment(paymentId) {
  const couponSelect = document.getElementById("verifyCouponSelect");
  const couponId = couponSelect?.value ? Number(couponSelect.value) : undefined;
  const paymentMethod = document.getElementById("verifyPaymentMethod")?.value;
  const note = document.getElementById("verifyVoucherNote");

  if (!paymentMethod) {
    if (note) {
      note.style.color = "#DC2626";
      note.textContent = "Select a payment method.";
    }
    return;
  }

  try {
    await api.verifyPayment(paymentId, { couponId, paymentMethod });
    closeDetailPage();
    await refreshPaymentPageData();
  } catch (error) {
    if (note) {
      note.style.color = "#DC2626";
      note.textContent = error.message || "Failed to verify payment.";
    } else {
      alert(error.message || "Failed to verify payment.");
    }
  }
}

async function confirmRefundPayment(paymentId) {
  const reason = q("refundReason")?.value.trim();
  const note = q("refundNote");
  if (!reason) {
    if (note) {
      note.style.color = "#DC2626";
      note.textContent = "Enter a refund reason.";
    }
    return;
  }
  if (!confirm(`Refund PAY-${String(paymentId).padStart(4, "0")}? Revenue and linked loyalty points will be reversed.`)) return;

  try {
    await api.refundPayment(paymentId, reason);
    closeDetailPage();
    await refreshPaymentPageData();
  } catch (error) {
    if (note) {
      note.style.color = "#DC2626";
      note.textContent = error.message || "Failed to refund payment.";
    } else {
      alert(error.message || "Failed to refund payment.");
    }
  }
}

async function openPaymentDetail(paymentId) {
  const detail = await api.getPaymentDetail(paymentId).catch(error => {
    alert(error.message || "Failed to load payment.");
    return null;
  });
  if (!detail) return;

  setRecordUrl("payment_id", paymentId);

  const p = detail.payment;
  const pet = detail.booking ? petsCacheForPayments.find(x => String(x.pet_id) === String(detail.booking.pet_id)) : null;
  const customer = customersCacheForPayments.find(c => String(c.customer_id) === String(detail.customerId));
  const enabledPaymentMethods = Object.entries({ Cash: "cash", Card: "card", "E-Wallet": "qr", "Bank Transfer": "online" })
    .filter(([, key]) => paymentCompanySettings.payment_methods?.[key] !== false)
    .map(([label]) => label);
  const awaitingVerification = isPaymentAwaitingVerification(p);
  const canRefund = p.status === "Paid" && paymentAccountRole === "manager";
  const today = getToday();
  const availableCoupons = couponsCache.filter(c => !c.expiry_date || String(c.expiry_date).slice(0, 10) >= today);

  detailPage.style.display = "flex";
  detailTitle.textContent = `Payment Detail · PAY-${String(p.payment_id).padStart(4, "0")}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Payment ID</label>
      <input value="PAY-${String(p.payment_id).padStart(4, "0")}" readonly />
    </div>

    <div class="form-group">
      <label>Customer</label>
      <input value="${customer?.full_name || "Unknown"}" readonly />
    </div>

    <div class="form-group">
      <label>Pet</label>
      <input value="${pet?.pet_name || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Service</label>
      <input value="${p.service || "—"}" readonly />
    </div>

    <div class="form-group">
      <label>Base Price</label>
      <input value="RM ${Number(p.base_price || 0).toLocaleString()}" readonly />
    </div>

    <div class="form-group">
      <label>Add-ons</label>
      <input value="${p.add_ons || "None"}" readonly />
    </div>

    <div class="form-group">
      <label>Final Amount</label>
      <input value="RM ${Number(p.final_amount || 0).toLocaleString()}" readonly />
    </div>

    <div class="form-group">
      <label>Loyalty Member</label>
      <input value="${detail.member ? `${detail.member.tier} · ${detail.member.points_balance} points` : "No loyalty member on file"}" readonly />
    </div>

    <div class="form-group">
      <label>Payment Method</label>
      <input value="${p.payment_method || "Not set"}" readonly />
    </div>

    <div class="form-group">
      <label>Date</label>
      <input value="${formatDate(p.date)}" readonly />
    </div>

    <div class="form-group">
      <label>Status</label>
      <input value="${p.status}" readonly />
    </div>

    ${p.status === "Refunded" ? `
    <div class="form-group full">
      <label>Refund Details</label>
      <div class="reply-preview" style="background:#FEF2F2;border-color:#FECACA;">
        ${escapeUiText(p.refund_reason || "No reason recorded")}
        ${p.refunded_at ? `<br><span class="profile-sub">Refunded ${new Date(p.refunded_at).toLocaleString("en-MY")}</span>` : ""}
      </div>
    </div>
    ` : ""}

    ${awaitingVerification ? `
    <div class="form-group full">
      <label>Payment Method</label>
      <select id="verifyPaymentMethod" required>
        <option value="">Select payment method</option>
        ${enabledPaymentMethods.map(method => `<option value="${method}">${method}</option>`).join("")}
      </select>
    </div>
    <div class="form-group full">
      <label>Apply Voucher (optional)</label>
      <select id="verifyCouponSelect">
        <option value="">No voucher</option>
        ${availableCoupons.map(c => `<option value="${c.coupon_id}">${c.reward_name} (${c.points_required} pts)</option>`).join("")}
      </select>
    </div>
    <div class="form-group full">
      <p id="verifyVoucherNote" style="font-size:0.8rem;color:var(--text-muted);"></p>
    </div>
    ` : ""}

    ${canRefund ? `
    <div class="form-group full">
      <label for="refundReason">Refund Reason <span class="req">*</span></label>
      <textarea id="refundReason" maxlength="500" placeholder="Explain why this paid transaction is being refunded…"></textarea>
      <p id="refundNote" style="font-size:0.8rem;color:var(--text-muted);"></p>
    </div>
    ` : ""}

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      ${awaitingVerification ? `<button type="button" class="save-btn" id="verifyConfirmBtn"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">Verify Payment &amp; Redemption</button>` : ""}
      ${canRefund ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="confirmRefundPayment(${p.payment_id})">Refund Payment</button>` : ""}
    </div>
  `;

  if (awaitingVerification) {
    const couponSelect = document.getElementById("verifyCouponSelect");
    const note = document.getElementById("verifyVoucherNote");
    couponSelect.addEventListener("change", async () => {
      if (!couponSelect.value) { note.textContent = ""; return; }
      try {
        const quote = await api.quoteVoucher(p.payment_id, Number(couponSelect.value));
        note.style.color = "";
        note.textContent = `New total: RM ${quote.finalAmount} (discount RM ${quote.discountApplied})`;
      } catch (error) {
        note.style.color = "#DC2626";
        note.textContent = error.message;
      }
    });
    document.getElementById("verifyConfirmBtn").addEventListener("click", () => confirmVerifyPayment(p.payment_id));
  }
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
const enquiryHistoryBody = document.getElementById("enquiryHistoryBody");
const enquiryHistoryRecordCount = document.getElementById("enquiryHistoryRecordCount");
let currentEnquiryAccountRole = null;
let currentEnquiryPanel = "pending";

function escapeUiText(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[character]);
}

// Real backend wiring. Reuses enquiryRecords/normalizeChatMessage/
// realEnquiryDetailRow/computeRealEnquiryPriority/resolveRealEnquiry, all
// already built for dailyoverview.html's real rewrite (plain top-level
// functions, no page-DOM coupling) — see "DAILY OVERVIEW" section above.
// The mock's "channel"/"relatedService"/"handledBy" fields have no real
// column (messages has no channel — this product is WhatsApp-only in
// practice — no service tag, and no human-vs-AI flag), so HITL Rate has no
// real source and is shown as "Not available" rather than a fabricated %.
const PRIORITY_RANK = { urgent: 0, high: 1, normal: 2 };

async function initEnquiriesPage() {
  document.querySelectorAll("#enquiryTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#enquiryTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentEnquiryPanel = btn.dataset.panel;
      document.getElementById("enquiryPendingPanel").classList.toggle("hidden", currentEnquiryPanel !== "pending");
      document.getElementById("enquiryHistoryPanel").classList.toggle("hidden", currentEnquiryPanel !== "all");
      renderEnquiryLists();
    });
  });

  enquirySearchInput.addEventListener("input", () => {
    resetListPage("enquiryPending");
    resetListPage("enquiryHistory");
    renderEnquiryLists();
  });

  registerListRerenderer("enquiryPending", renderEnquiryLists);
  registerListRerenderer("enquiryHistory", renderEnquiryLists);

  await refreshEnquiryPageData();
}

async function refreshEnquiryPageData() {
  try {
    const [messages, customers, staffList, me] = await Promise.all([
      api.get("/chat-messages"),
      api.get("/customers"),
      api.get("/staff"),
      api.get("/accounts/me"),
    ]);
    bookingCustomerOptions = customers; // populates findBookingCustomer() used by normalizeChatMessage
    bookingStaffOptions = staffList;
    currentEnquiryAccountRole = me.role;
    enquiryRecords = messages.map(normalizeChatMessage);
  } catch (error) {
    alert(error.message || "Failed to load enquiries.");
  }

  updateEnquiryKPI();
  renderEnquiryLists();
}

// All-time, not scoped to any single day — test/seed data in particular
// tends to be spread across many receive_dates, and a KPI that silently
// shows 0 whenever "today" happens to have no messages reads as broken.
function updateEnquiryKPI() {
  const total = enquiryRecords.length;
  const pending = enquiryRecords.filter(e => e.status === "pending").length;
  const resolved = enquiryRecords.filter(e => e.status === "replied");

  document.getElementById("enquiryTotalCount").textContent = total;
  document.getElementById("enquiryPendingCount").textContent = pending;
  document.getElementById("enquiryResolutionRate").textContent = `${total ? Math.round((resolved.length / total) * 100) : 0}%`;
}

function filterEnquiries(list, searchValue) {
  if (!searchValue) return list;
  return list.filter(e =>
    e.customerName.toLowerCase().includes(searchValue) ||
    e.phone.toLowerCase().includes(searchValue) ||
    e.message.toLowerCase().includes(searchValue)
  );
}

function renderEnquiryLists() {
  const searchValue = enquirySearchInput.value.toLowerCase().trim();
  renderEnquiryPendingTable(searchValue);
  renderEnquiryHistoryTable(searchValue);
}

function enquiryTimeSortKey(e) {
  return `${e.receiveDate || ""} ${e.receiveTime || ""}`;
}

function renderEnquiryPendingTable(searchValue) {
  // All-time — see updateEnquiryKPI() above: an unreplied enquiry from an
  // earlier day still needs a reply, so it should keep showing up.
  const pending = filterEnquiries(enquiryRecords.filter(e => e.status === "pending"), searchValue)
    .sort((a, b) => PRIORITY_RANK[computeRealEnquiryPriority(a)] - PRIORITY_RANK[computeRealEnquiryPriority(b)] || enquiryTimeSortKey(a).localeCompare(enquiryTimeSortKey(b)));

  enquiryPendingRecordCount.textContent = `${pending.length} pending`;

  if (pending.length === 0) {
    enquiryPendingBody.innerHTML = `<tr><td colspan="6" class="empty-row">No pending enquiries.</td></tr>`;
    renderListPagination("enquiryPending", 0);
    return;
  }

  const pendingPage = paginateList("enquiryPending", pending, 5);

  enquiryPendingBody.innerHTML = pendingPage.map(e => {
    const priority = computeRealEnquiryPriority(e);
    const priorityBadge = priority === "urgent"
      ? `<span class="status-tag status-no_show">Urgent</span>`
      : priority === "high"
        ? `<span class="status-tag status-pending">High</span>`
        : `<span class="status-tag status-off">Normal</span>`;

    return `
    <tr>
      <td><span class="key-chip">ENQ-${String(e.id).padStart(4, "0")}</span></td>
      <td>
        <span class="profile-name">${escapeUiText(e.customerName)}</span><br>
        ${priorityBadge}
      </td>
      <td>${escapeUiText(e.message)}</td>
      <td>${e.receiveDate || "-"} ${e.receiveTime || ""}</td>
      <td>${e.receiveTime ? slaBadge("enquiry", e.receiveTime) : ""}</td>
      <td>
        <button class="action-btn" onclick="openEnquiryDetailPage(${e.id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
      </td>
    </tr>
  `;
  }).join("");
}

function renderEnquiryHistoryTable(searchValue) {
  // All-time by design: this tab is the complete enquiry log, including
  // both pending and replied records.
  const history = filterEnquiries(enquiryRecords, searchValue)
    .sort((a, b) => enquiryTimeSortKey(b).localeCompare(enquiryTimeSortKey(a)));

  enquiryHistoryRecordCount.textContent = `${history.length} records`;

  if (history.length === 0) {
    enquiryHistoryBody.innerHTML = `<tr><td colspan="7" class="empty-row">No enquiries found.</td></tr>`;
    renderListPagination("enquiryHistory", 0);
    return;
  }

  const historyPage = paginateList("enquiryHistory", history, 5);

  enquiryHistoryBody.innerHTML = historyPage.map(e => `
    <tr>
      <td><span class="key-chip">ENQ-${String(e.id).padStart(4, "0")}</span></td>
      <td><span class="profile-name">${escapeUiText(e.customerName)}</span></td>
      <td>${escapeUiText(e.message)}</td>
      <td>${e.receiveDate || "-"} ${e.receiveTime || ""}</td>
      <td>${escapeUiText(findBookingStaffMember(e.repliedByStaffId)?.staff_name || "—")}</td>
      <td><span class="status-tag status-${e.status === "replied" ? "done" : "pending"}">${e.status === "replied" ? "Replied" : "Pending"}</span></td>
      <td><button class="action-btn" onclick="openEnquiryDetailPage(${e.id})"><img src="icon/view.png" alt="" class="btn-icon">View</button></td>
    </tr>
  `).join("");
}

// Every customer message is its own row (a reply lives on that same row —
// there's no separate "staff message" row), so "linking" an enquiry to the
// chat just means surfacing this customer's other messages alongside it.
function getRelatedEnquiriesForCustomer(customerId, excludeId) {
  return enquiryRecords
    .filter(other => other.id !== excludeId && other.customerId != null && String(other.customerId) === String(customerId))
    .slice()
    .sort((a, b) => enquiryTimeSortKey(b).localeCompare(enquiryTimeSortKey(a)));
}

function openEnquiryDetailPage(id) {
  const e = enquiryRecords.find(x => x.id === id);
  if (!e) return;
  setRecordUrl("message_id", e.id);

  const isPending = e.status === "pending";
  const relatedEnquiries = e.customerId != null ? getRelatedEnquiriesForCustomer(e.customerId, e.id) : [];

  detailPage.style.display = "flex";
  detailTitle.textContent = `Enquiry Detail · ENQ-${String(e.id).padStart(4, "0")}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Customer</label>
      <input value="${escapeUiText(e.customerName)}" readonly />
    </div>

    <div class="form-group">
      <label>Phone</label>
      <input value="${escapeUiText(e.phone || "—")}" readonly />
    </div>

    <div class="form-group">
      <label>Channel</label>
      <input value="WhatsApp" readonly />
    </div>

    <div class="form-group">
      <label>Intent</label>
      <input value="${escapeUiText(e.intent || "—")}" readonly />
    </div>

    <div class="form-group">
      <label>Priority</label>
      <input value="${enquiryPriorityLabel(computeRealEnquiryPriority(e))}" readonly />
    </div>

    <div class="form-group">
      <label>Received At</label>
      <input value="${e.receiveDate || "-"} ${e.receiveTime || ""}" readonly />
    </div>

    <div class="form-group full">
      <label>Message</label>
      <textarea readonly>${escapeUiText(e.message)}</textarea>
    </div>

    <div class="form-group">
      <label>Status</label>
      <input value="${isPending ? "Pending" : "Replied"}" readonly />
    </div>

    ${!isPending ? `
    <div class="form-group">
      <label>Replied At</label>
      <input value="${e.replyDate || "-"} ${e.replyTime || ""}" readonly />
    </div>
    <div class="form-group full">
      <label>Saved Reply</label>
      <div class="reply-preview">${escapeUiText(e.replyText || "No reply text was recorded for this legacy enquiry.")}</div>
    </div>
    ` : `
    <div class="form-group full">
      <label for="enquiryReplyText">Reply <span class="req">*</span></label>
      <textarea id="enquiryReplyText" maxlength="2000" placeholder="Write the reply sent to the customer…" required></textarea>
      <small class="profile-sub">This records the staff reply. Use Open WhatsApp to send it to the customer.</small>
    </div>
    `}

    <div class="form-group full">
      <label>Conversation History <span class="profile-sub">(${relatedEnquiries.length} other message${relatedEnquiries.length === 1 ? "" : "s"} from this customer)</span></label>
      <div class="crm-pet-list crm-list-scroll">
        ${relatedEnquiries.length === 0
          ? `<p class="crm-pet-empty">No other messages from this customer.</p>`
          : relatedEnquiries.map(other => `
              <div class="crm-pet-card">
                <div class="crm-pet-info">
                  <span class="profile-name">${other.receiveDate || "-"} ${other.receiveTime || ""}</span>
                  <span class="profile-sub">${escapeUiText(other.message)}</span>
                </div>
                <div style="display:flex;align-items:center;gap:10px;">
                  <span class="status-tag status-${other.status === "replied" ? "done" : "pending"}">${other.status === "replied" ? "Replied" : "Pending"}</span>
                  <button type="button" class="action-btn" onclick="openEnquiryDetailPage(${other.id})"><img src="icon/view.png" alt="" class="btn-icon">View</button>
                </div>
              </div>
            `).join("")
        }
      </div>
    </div>

    <div class="form-actions">
      ${e.phone ? `<a class="cancel-btn" href="${getWhatsAppLink(e.phone)}" target="_blank" rel="noopener">Open WhatsApp</a>` : ""}
      ${currentEnquiryAccountRole === "manager" ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="deleteEnquiry(${e.id})">Delete</button>` : ""}
      <button type="button" class="cancel-btn" onclick="closeDetailPage()"><img src="icon/close-circle.png" alt="" class="btn-icon">Close</button>
      ${isPending ? `<button type="button" class="save-btn" onclick="saveEnquiryReply(${e.id})"><img src="icon/confirm-circle.png" alt="" class="btn-icon solid-btn-icon">Save Reply</button>` : ""}
    </div>
  `;
}

function openNewEnquiryForm() {
  clearRecordUrl(false);
  detailPage.style.display = "flex";
  detailTitle.textContent = "Log Enquiry";
  detailForm.innerHTML = `
    <div class="form-group full">
      <label for="newEnquiryCustomer">Customer <span class="req">*</span></label>
      <select id="newEnquiryCustomer" required>
        <option value="">Select customer</option>
        ${bookingCustomerOptions.map(customer => `<option value="${customer.customer_id}">${escapeUiText(customer.full_name)} · ${escapeUiText(customer.phone_number || "No phone")}</option>`).join("")}
      </select>
    </div>
    <div class="form-group full">
      <label for="newEnquiryIntent">Intent</label>
      <select id="newEnquiryIntent">
        <option>General</option><option>Booking</option><option>Service</option>
        <option>Payment</option><option>Loyalty</option><option>Policy</option>
      </select>
    </div>
    <div class="form-group full">
      <label for="newEnquiryMessage">Message <span class="req">*</span></label>
      <textarea id="newEnquiryMessage" maxlength="2000" placeholder="Enter the customer's WhatsApp enquiry…" required></textarea>
    </div>
    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Cancel</button>
      <button type="submit" class="save-btn">Log Enquiry</button>
    </div>
  `;
  detailForm.onsubmit = async event => {
    event.preventDefault();
    const customerId = Number(q("newEnquiryCustomer").value);
    const message = q("newEnquiryMessage").value.trim();
    if (!customerId || !message) return;
    const stamp = todayStampLocal();
    try {
      await api.post("/chat-messages", {
        sender_id: customerId,
        message_text: message,
        intent_label: q("newEnquiryIntent").value,
        receive_date: stamp.date,
        receive_time: stamp.time,
      });
      closeDetailPage();
      await refreshEnquiryPageData();
    } catch (error) {
      alert(error.message || "Failed to log enquiry.");
    }
  };
}

async function saveEnquiryReply(id) {
  const replyText = q("enquiryReplyText")?.value.trim();
  if (!replyText) {
    alert("Enter the reply text before saving.");
    return;
  }
  const stamp = todayStampLocal();
  try {
    await api.patch(`/chat-messages/${id}`, {
      reply_text: replyText,
      reply_date: stamp.date,
      reply_time: stamp.time,
    });
    closeDetailPage();
    await refreshEnquiryPageData();
  } catch (error) {
    alert(error.message || "Failed to save reply.");
  }
}

async function deleteEnquiry(id) {
  if (!confirm(`Delete ENQ-${String(id).padStart(4, "0")}? This cannot be undone.`)) return;
  try {
    await api.del(`/chat-messages/${id}`);
    closeDetailPage();
    await refreshEnquiryPageData();
  } catch (error) {
    alert(error.message || "Failed to delete enquiry.");
  }
}

async function resolveEnquiryAndRefresh(id) {
  // NOTE: deliberately not reusing resolveRealEnquiry() (built for
  // dailyoverview.html) — it calls closeDetailModal()/renderRealDailyOverview(),
  // which target #detailModal/#actionCards etc., elements that don't exist
  // on this page (enquiries.html uses #detailPage/closeDetailPage()).
  const stamp = todayStampLocal();
  try {
    await api.patch(`/chat-messages/${id}`, { reply_date: stamp.date, reply_time: stamp.time });
    closeDetailPage();
    await refreshEnquiryPageData();
  } catch (error) {
    alert(error.message || "Failed to update enquiry.");
  }
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
// mock arrays (not converted yet). `leaveDataIsReal` (set by
// loadRealStaffPageData(), which calls loadRealBookingData()) is the shared
// signal for which shape is currently loaded — real staff uses
// staff_id/off_days_json, mock staff uses id/offDays.
function findStaffAny(staffId) {
  return leaveDataIsReal ? findStaffRecord(staffId) : findStaff(staffId);
}
function staffOffDays(member) {
  return leaveDataIsReal ? (member.off_days_json || []) : (member.offDays || []);
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
  const member = findStaffRecord(row.staff_id);
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

// Real backend data for this page (separate from the mock `staff`/
// `leaveRequests` arrays above, which booking.html/dashboard.html still read
// until they're wired too — see backend/DEVELOPER_GUIDE.md §3 for the
// staff vs. accounts distinction: staff are scheduling rows, not logins).
let staffRecords = [];
let leaveRecords = [];
let staffBookingCountsCache = {};
let staffDutyWeekAnchor = getStartOfWeek(getToday());

const staffSearchInput = document.getElementById("staffSearchInput");
const staffListBody = document.getElementById("staffListBody");
const staffListRecordCount = document.getElementById("staffListRecordCount");
const staffLeavePendingBody = document.getElementById("staffLeavePendingBody");
const staffLeavePendingCount = document.getElementById("staffLeavePendingCount");

function findStaffRecord(staffId) {
  return staffRecords.find(s => String(s.staff_id) === String(staffId));
}

function isStaffRecordOnLeave(staffId, dateStr) {
  return leaveRecords.some(lv =>
    String(lv.staff_id) === String(staffId) && lv.status === "Approved" &&
    dateStr >= lv.start_date && dateStr <= lv.end_date
  );
}

function getStaffRecordDutyStatus(staffId, dateStr) {
  if (isStaffRecordOnLeave(staffId, dateStr)) return "leave";
  const member = findStaffRecord(staffId);
  if (!member) return "off";
  const dayName = new Date(dateStr + "T00:00:00").toLocaleDateString("en-US", { weekday: "long" });
  return (member.off_days_json || []).includes(dayName) ? "off" : "duty";
}

function isStaffRecordOnDuty(staffId, dateStr) {
  return getStaffRecordDutyStatus(staffId, dateStr) === "duty";
}

// Boarding has no single "booking_date" column (check_in_date/check_out_date
// span multiple days), so it can't be filtered by the generic ?column=value
// query params the way grooming/daycare can — fetch it unfiltered and check
// the date range client-side instead.
async function getTodayBookingCountsByStaff() {
  const todayDate = getToday();
  const counts = {};
  const add = (staffId) => { counts[staffId] = (counts[staffId] || 0) + 1; };

  const [grooming, daycare, boarding] = await Promise.all([
    api.get(`/bookings/grooming?booking_date=${todayDate}`),
    api.get(`/bookings/daycare?booking_date=${todayDate}`),
    api.get(`/bookings/boarding`),
  ]);

  grooming.forEach(b => add(b.staff_id));
  daycare.forEach(b => add(b.staff_id));
  boarding
    .filter(b => b.check_in_date <= todayDate && todayDate <= b.check_out_date)
    .forEach(b => add(b.staff_id));

  return counts;
}

function todayStampLocal() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return {
    date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
    time: d.toTimeString().slice(0, 8),
  };
}

async function initStaffPage() {
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

  await refreshStaffPageData();
}

async function refreshStaffPageData() {
  [staffRecords, leaveRecords] = await Promise.all([
    api.get("/staff"),
    api.get("/leave-requests"),
  ]);

  await updateStaffKPI();
  renderStaffListTable();
  renderPendingLeaveTable();
  renderLeaveHistoryTable();
  renderDutyCalendar();
}

async function updateStaffKPI() {
  const todayDate = getToday();
  staffBookingCountsCache = await getTodayBookingCountsByStaff();

  const onDutyVolume = staffRecords
    .filter(s => isStaffRecordOnDuty(s.staff_id, todayDate))
    .reduce((sum, s) => sum + (staffBookingCountsCache[s.staff_id] || 0), 0);

  document.getElementById("staffTotalCount").textContent = staffRecords.length;
  document.getElementById("staffOnDutyCount").textContent = staffRecords.filter(s => isStaffRecordOnDuty(s.staff_id, todayDate)).length;
  document.getElementById("staffOnLeaveCount").textContent = staffRecords.filter(s => isStaffRecordOnLeave(s.staff_id, todayDate)).length;
  document.getElementById("staffBookingVolume").textContent = onDutyVolume;
}

function renderStaffListTable() {
  const searchValue = staffSearchInput.value.toLowerCase().trim();
  const manager = isManager(getCurrentAccount());
  const todayDate = getToday();

  const filtered = staffRecords.filter(s =>
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
    const status = getStaffRecordDutyStatus(s.staff_id, todayDate);
    const statusLabel = status === "duty" ? "On Duty" : status === "leave" ? "On Leave" : "Off Today";
    const statusClass = status === "duty" ? "done" : status === "leave" ? "no_show" : "off";

    return `
      <tr>
        <td>
          <span class="profile-name"><img src="icon/team.png" alt="" class="row-icon">${s.staff_name}</span>
          <span class="profile-sub">STF-${String(s.staff_id).padStart(3, "0")}</span>
        </td>
        <td>${s.role}</td>
        <td>
          <span class="profile-sub">${s.email || "—"}</span>
          <span class="profile-sub">${s.phone || "—"}</span>
        </td>
        <td>${(s.off_days_json || []).join(", ") || "—"}</td>
        <td>${staffBookingCountsCache[s.staff_id] || 0}</td>
        <td><span class="status-tag status-${statusClass}">${statusLabel}</span></td>
        <td>
          <button class="${manager ? "edit-btn" : "action-btn"}" onclick="openStaffForm(${s.staff_id})">${manager ? "Edit" : "View"}</button>
        </td>
      </tr>
    `;
  }).join("");
}

function openStaffForm(staffId = null) {
  const manager = isManager(getCurrentAccount());
  const isEdit = staffId !== null && staffId !== undefined;
  const member = isEdit
    ? findStaffRecord(staffId)
    : { staff_id: null, staff_name: "", role: "Groomer", email: "", phone: "", off_days_json: [] };
  if (isEdit && !member) return;
  if (isEdit) setRecordUrl("staff_id", member.staff_id);
  else clearRecordUrl(false);

  const readonly = !manager;
  const dayOptions = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit ? `Staff Info · STF-${String(member.staff_id).padStart(3, "0")}` : "Add Staff";

  detailForm.innerHTML = `
    ${isEdit ? `
    <div class="form-group">
      <label>Staff ID</label>
      <input value="STF-${String(member.staff_id).padStart(3, "0")}" readonly />
    </div>` : ""}

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
                <input type="checkbox" name="offDays" value="${d}" ${(member.off_days_json || []).includes(d) ? "checked" : ""} />
                ${d}
              </label>
            `).join("")}
          </div>`
      }
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">${manager ? "Cancel" : "Close"}</button>
      ${manager && isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeStaffMember(${member.staff_id})">Remove Staff</button>` : ""}
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
      email: formData.get("email") || null,
      phone: formData.get("phone") || null,
      off_days_json: formData.getAll("offDays"),
    };

    const submitBtn = detailForm.querySelector(".save-btn");
    if (submitBtn) submitBtn.disabled = true;

    try {
      if (isEdit) {
        await api.patch(`/staff/${member.staff_id}`, payload);
      } else {
        await api.post("/staff", { ...payload, status: "active" });
      }
      closeDetailPage();
      await refreshStaffPageData();
    } catch (error) {
      alert(error.message || "Failed to save staff record.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  };
}

async function removeStaffMember(staffId) {
  if (!confirm("Remove this staff member? This cannot be undone.")) return;

  try {
    await api.del(`/staff/${staffId}`);
    closeDetailPage();
    await refreshStaffPageData();
  } catch (error) {
    alert(error.message || "Failed to remove staff member.");
  }
}

function renderPendingLeaveTable() {
  const manager = isManager(getCurrentAccount());
  const pending = leaveRecords.filter(lv => lv.status === "Pending");

  staffLeavePendingCount.textContent = `${pending.length} pending`;

  if (pending.length === 0) {
    staffLeavePendingBody.innerHTML = `<tr><td colspan="5" class="empty-row">No pending leave requests.</td></tr>`;
    return;
  }

  staffLeavePendingBody.innerHTML = pending.map(lv => {
    const member = findStaffRecord(lv.staff_id);
    return `
    <tr>
      <td><span class="profile-name">${member?.staff_name || "Unknown"}</span></td>
      <td>${lv.start_date === lv.end_date ? formatDate(lv.start_date) : `${formatDate(lv.start_date)} – ${formatDate(lv.end_date)}`}</td>
      <td>${lv.reason}</td>
      <td>${lv.applied_time ? lv.applied_time.slice(0, 5) : "—"}</td>
      <td>${manager ? `
        <button class="edit-btn" onclick="decideLeaveRequest(${lv.leave_id}, 'Approved')">Approve</button>
        <button class="action-btn" onclick="decideLeaveRequest(${lv.leave_id}, 'Rejected')">Reject</button>
      ` : `<span class="profile-sub">Awaiting manager</span>`}</td>
    </tr>
  `;
  }).join("");
}

async function decideLeaveRequest(leaveId, status) {
  try {
    await api.post(`/leave-requests/${leaveId}/decision`, { status });
    await refreshStaffPageData();
  } catch (error) {
    alert(error.message || "Failed to update leave request.");
  }
}

function renderLeaveHistoryTable() {
  const manager = isManager(getCurrentAccount());
  const history = leaveRecords
    .slice()
    .sort((a, b) => new Date(b.start_date) - new Date(a.start_date));

  document.getElementById("staffLeaveHistoryCount").textContent = `${history.length} records`;

  if (history.length === 0) {
    document.getElementById("staffLeaveHistoryBody").innerHTML = `<tr><td colspan="7" class="empty-row">No leave applications found.</td></tr>`;
    return;
  }

  document.getElementById("staffLeaveHistoryBody").innerHTML = history.map(lv => {
    const member = findStaffRecord(lv.staff_id);
    const statusClass = lv.status === "Approved" ? "done" : lv.status === "Rejected" ? "no_show" : "pending";
    return `
    <tr>
      <td><span class="key-chip">LV-${String(lv.leave_id).padStart(3, "0")}</span></td>
      <td><span class="profile-name">${member?.staff_name || "Unknown"}</span></td>
      <td>${lv.start_date === lv.end_date ? formatDate(lv.start_date) : `${formatDate(lv.start_date)} – ${formatDate(lv.end_date)}`}</td>
      <td>${lv.reason}</td>
      <td>${lv.applied_time ? lv.applied_time.slice(0, 5) : "—"}</td>
      <td><span class="status-tag status-${statusClass}">${lv.status}</span></td>
      <td>${manager && lv.status === "Pending" ? `
        <button class="edit-btn" onclick="decideLeaveRequest(${lv.leave_id}, 'Approved')">Approve</button>
        <button class="action-btn" onclick="decideLeaveRequest(${lv.leave_id}, 'Rejected')">Reject</button>
      ` : "—"}</td>
    </tr>
  `;
  }).join("");
}

function openApplyLeaveForm() {
  clearRecordUrl(false);
  const manager = isManager(getCurrentAccount());
  const loginEmail = String(getCurrentAccount()?.email || "").trim().toLowerCase();
  const currentStaff = manager
    ? null
    : staffRecords.find(s => String(s.email || "").trim().toLowerCase() === loginEmail);
  if (!manager && !currentStaff) {
    alert("Your login email is not linked to a staff record. Ask a manager to update the staff email first.");
    return;
  }

  detailPage.style.display = "flex";
  detailTitle.textContent = "Apply Leave";

  detailForm.innerHTML = `
    <div class="form-group full">
      <label>Staff</label>
      ${manager ? `
        <select name="staff_id" required>
          ${staffRecords.map(s => `<option value="${s.staff_id}">${s.staff_name} (${s.role})</option>`).join("")}
        </select>
      ` : `
        <input value="${currentStaff.staff_name} (${currentStaff.role})" readonly />
        <input type="hidden" name="staff_id" value="${currentStaff.staff_id}" />
      `}
    </div>

    <div class="form-group">
      <label>Start Date</label>
      <input type="date" name="start_date" value="${getToday()}" required />
    </div>

    <div class="form-group">
      <label>End Date</label>
      <input type="date" name="end_date" value="${getToday()}" required />
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
    const { date: appliedDate, time: appliedTime } = todayStampLocal();
    if (formData.get("end_date") < formData.get("start_date")) {
      alert("End date cannot be before start date.");
      return;
    }

    const submitBtn = detailForm.querySelector(".save-btn");
    if (submitBtn) submitBtn.disabled = true;

    try {
      await api.post("/leave-requests", {
        staff_id: Number(formData.get("staff_id")),
        start_date: formData.get("start_date"),
        end_date: formData.get("end_date"),
        reason: formData.get("reason"),
        status: "Pending",
        applied_date: appliedDate,
        applied_time: appliedTime,
      });
      closeDetailPage();
      await refreshStaffPageData();
    } catch (error) {
      alert(error.message || "Failed to submit leave application.");
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
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

  document.getElementById("dutyCalendarBody").innerHTML = staffRecords.map(s => `
    <tr>
      <td>
        <span class="profile-name">${s.staff_name}</span>
        <span class="profile-sub">${s.role}</span>
      </td>
      ${dates.map(d => {
        const status = getStaffRecordDutyStatus(s.staff_id, d);
        const label = status === "duty" ? "Duty" : status === "leave" ? "Leave" : "Off";
        const cls = status === "duty" ? "done" : status === "leave" ? "no_show" : "off";
        const isLeave = status === "leave";
        return `<td ${isLeave ? `style="cursor:pointer;" onclick="openLeaveCellDetail(${s.staff_id},'${d}')"` : ""}><span class="status-tag status-${cls}">${label}</span></td>`;
      }).join("")}
    </tr>
  `).join("");
}

function openLeaveCellDetail(staffId, dateStr) {
  const lv = leaveRecords.find(r =>
    String(r.staff_id) === String(staffId) && r.status === "Approved" &&
    dateStr >= r.start_date && dateStr <= r.end_date
  );
  if (!lv) return;
  setRecordUrl("leave_id", lv.leave_id);
  const member = findStaffRecord(staffId);

  detailPage.style.display = "flex";
  detailTitle.textContent = `Leave Detail · ${member?.staff_name || ""}`;

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Staff</label>
      <input value="${member?.staff_name || ""}" readonly />
    </div>

    <div class="form-group">
      <label>Leave Dates</label>
      <input value="${lv.start_date === lv.end_date ? formatDate(lv.start_date) : `${formatDate(lv.start_date)} – ${formatDate(lv.end_date)}`}" readonly />
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

let realMembers = [];
let realCoupons = [];
let realRedemptions = [];
let realMessages = [];
let realPayments = [];

const SERVICE_MIX_COLORS = { grooming: "#3B82F6", boarding: "#10B981", daycare: "#F59E0B" };

const KPI_DEFS = [
  { key: "revenue",   icon: "payment-card.png",      label: "Total Revenue",              onClick: "openKpiDetail('revenue')" },
  { key: "bookings",  icon: "calendar-simple.png",   label: "Total Bookings",             onClick: "openKpiDetail('bookings')" },
  { key: "repeat",    icon: "users.png",             label: "Repeat Customer Rate",       onClick: "openKpiDetail('repeat')" },
  { key: "occupancy", icon: "line-chart.png",        label: "Occupancy / Slot Utilisation", onClick: "openKpiDetail('occupancy')" }
];

let currentDashboardPeriod = "weekly";
let dashboardAnchorDate = today;
let dashboardRange = null;
let dashboardTrendBuckets = [];
let dashboardCompanySettings = {};
let dashboardLastLoadedAt = null;

async function loadAnalyticsDashboardData() {
  await fetchBookingPageData();

  const [messages, members, coupons, redemptions, payments, leaves, company] = await Promise.all([
    api.get("/chat-messages"),
    api.get("/member-info"),
    api.get("/coupons"),
    api.get("/redemptions"),
    api.get("/payments"),
    api.get("/leave-requests"),
    api.get("/companies/me"),
  ]);

  const paymentById = new Map(payments.map(p => [String(p.payment_id), p]));
  bookings = bookingRecords.map(record => {
    const linkedPayment = paymentById.get(String(record.raw?.payment_id));
    const serviceId = `${record.type}:${record.serviceLabel}`;
    return {
      ...record,
      serviceType: record.type,
      serviceId,
      amount: linkedPayment?.status === "Paid" ? Number(linkedPayment.final_amount || 0) : 0,
      roomId: record.type === "boarding" ? record.serviceLabel : null,
    };
  });

  const uniqueServices = new Map();
  bookings.forEach(booking => {
    if (!uniqueServices.has(booking.serviceId)) {
      uniqueServices.set(booking.serviceId, {
        id: booking.serviceId,
        name: booking.serviceLabel,
        type: booking.serviceType,
      });
    }
  });
  services.splice(0, services.length, ...uniqueServices.values());

  customers = bookingCustomerOptions.map(customer => ({
    ...customer,
    phone: customer.phone_number || "—",
  }));
  pets = bookingPetOptions.map(pet => ({
    ...pet,
    species: pet.pet_type || "Pet",
  }));

  // System panel (renderSystemDashboard/renderSystemKpiHero) reads these
  // customer-name-joined `real*` globals directly, separately from the
  // Operation/Staff panels' mock-shaped globals above.
  const customerById = new Map(customers.map(c => [String(c.customer_id), c]));
  realMembers = members.map(m => ({
    ...m,
    customerName: customerById.get(String(m.customer_id))?.full_name || "—",
    phone: customerById.get(String(m.customer_id))?.phone_number || "—",
  }));
  realCoupons = coupons;
  const realCouponById = new Map(realCoupons.map(c => [String(c.coupon_id), c]));
  const memberByLoyaltyId = new Map(realMembers.map(m => [String(m.loyalty_id), m]));
  realRedemptions = redemptions.map(r => ({
    ...r,
    memberName: memberByLoyaltyId.get(String(r.loyalty_id))?.customerName || "—",
    couponName: r.coupon_id ? (realCouponById.get(String(r.coupon_id))?.reward_name || "—") : null,
  }));
  realMessages = messages.map(m => ({
    ...m,
    customerName: customerById.get(String(m.sender_id))?.full_name || "—",
    phone: customerById.get(String(m.sender_id))?.phone_number || "",
  }));
  realPayments = payments;

  staff = bookingStaffOptions.map(member => ({
    ...member,
    id: member.staff_id,
    name: member.staff_name,
    offDays: member.off_days_json || [],
  }));
  leaveRequests = leaves.map(leave => ({
    ...leave,
    staffId: leave.staff_id,
    staffName: staff.find(member => String(member.id) === String(leave.staff_id))?.name || "Unknown",
    startDate: leave.start_date,
    endDate: leave.end_date,
    status: String(leave.status || "pending").toLowerCase(),
  }));

  enquiries = messages
    .filter(message => message.sender_type === "customer")
    .map(message => {
      const customer = customers.find(item => String(item.customer_id) === String(message.sender_id));
      return {
        id: message.message_id,
        customerName: customer?.full_name || "Unknown customer",
        channel: "WhatsApp",
        message: message.message_text || "",
        relatedService: message.intent_label || "",
        receivedAt: message.receive_time ? message.receive_time.slice(0, 5) : "00:00",
        time: message.receive_time ? message.receive_time.slice(0, 5) : "00:00",
        receiveDate: message.receive_date,
        date: message.receive_date,
        handledBy: message.reply_date ? "human" : null,
        status: message.reply_date ? "resolved" : "pending",
      };
    });

  const memberById = new Map(members.map(member => [String(member.loyalty_id), member]));
  const couponById = new Map(coupons.map(coupon => [String(coupon.coupon_id), coupon]));
  loyaltyRequests = redemptions.map(redemption => {
    const member = memberById.get(String(redemption.loyalty_id));
    const customer = customers.find(item => String(item.customer_id) === String(member?.customer_id));
    const coupon = couponById.get(String(redemption.coupon_id));
    return {
      id: `REDM-${redemption.redemption_id}`,
      customerName: customer?.full_name || "Unknown customer",
      type: coupon?.reward_name || "Loyalty transaction",
      points: Number(redemption.loyalty_spend || 0),
      requestedAt: redemption.create_time ? String(redemption.create_time).slice(0, 5) : "00:00",
      date: redemption.create_date,
      approvedDate: redemption.approved_date || null,
      approvedTime: redemption.approved_time ? String(redemption.approved_time).slice(0, 5) : null,
      status: String(redemption.status || "Approved").toLowerCase(),
    };
  });
  loyaltyMemberRecords = members;

  const bookingByPaymentId = new Map(bookings.map(booking => [String(booking.raw?.payment_id), booking]));
  paymentRecords = payments.map(payment => ({
    ...payment,
    customerName: bookingByPaymentId.get(String(payment.payment_id))?.customerName || "Unknown customer",
    finalAmount: Number(payment.final_amount || 0),
    rawStatus: payment.status,
    status: payment.status === "Paid" ? "verified" : isPaymentAwaitingVerification(payment) ? "pending" : "closed",
  }));

  dashboardCompanySettings = company.settings_json || {};
  dashboardLastLoadedAt = new Date();
}

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
  const days = getDateRange(range.start, range.end);
  if (!days.length) return 0;
  if (!rooms.length) {
    const activeBookings = bookings.filter(booking =>
      booking.date >= range.start && booking.date <= range.end &&
      booking.status !== "cancelled" && booking.status !== "no_show"
    ).length;
    const availableSlots = days.length * CALENDAR_HOURS.length * 3;
    return availableSlots ? Math.min(100, (activeBookings / availableSlots) * 100) : 0;
  }
  const dailyRates = days.map(d => rooms.filter(r => isRoomLabelOccupiedOnDate(r.id, d)).length / rooms.length);
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
  bookings.forEach(b => {
    const key = bookingCustomerKey(b);
    lifetimeCountByCustomer[key] = (lifetimeCountByCustomer[key] || 0) + 1;
  });

  const periodCustomers = [...new Set(periodBookings.map(bookingCustomerKey))];
  if (!periodCustomers.length) return 0;

  const repeatCount = periodCustomers.filter(name => lifetimeCountByCustomer[name] > 1).length;
  return (repeatCount / periodCustomers.length) * 100;
}

function bookingCustomerKey(booking) {
  return booking.customerId == null ? `name:${booking.customerName}` : `id:${booking.customerId}`;
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

function getCustomerById(customerId) {
  return customers.find(customer => String(customer.customer_id) === String(customerId));
}

function renderKpiHeroGrid(metrics) {
  const values = {
    revenue: formatCurrency(metrics.totalRevenue),
    bookings: metrics.totalBookings.toLocaleString("en-MY"),
    repeat: `${Math.round(metrics.repeatCustomerRate)}%`,
    occupancy: `${Math.round(metrics.occupancyRate)}%`
  };

  q("kpiHeroGrid").innerHTML = KPI_DEFS.map(def => {
    return `
      <div class="kpi-hero-card ${def.onClick ? "clickable" : ""}" ${def.onClick ? `onclick="${def.onClick}"` : ""}>
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/${def.icon}" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">${def.label}</div>
            <div class="kpi-hero-value">${values[def.key]}</div>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

// Patches the KPI hero grid (rendered above from mock `bookings`) with real
// numbers from /api/dashboard/summary + /api/dashboard/revenue. The backend
// only exposes current-period totals (no historical prev/next navigation,
// no itemized per-transaction breakdown), so — unlike the mock version —
// these cards aren't clickable and Repeat Customer Rate / Occupancy have no
// real source at all, so they're shown as "Not available" rather than a
// fabricated percentage.
async function loadRealOperationKpis(period) {
  const apiPeriod = period === "daily" ? "today" : period === "monthly" ? "month" : "week";
  const periodNote = dashboardRange ? periodRangeLabel(period, dashboardRange) : formatShortDate(dashboardAnchorDate);

  try {
    const [summary, revenue] = await Promise.all([
      api.get(`/dashboard/summary?date=${dashboardAnchorDate}`),
      api.get(`/dashboard/revenue?period=${apiPeriod}&anchor=${dashboardAnchorDate}`),
    ]);

    const grid = q("kpiHeroGrid");
    if (!grid) return;

    grid.innerHTML = `
      <div class="kpi-hero-card">
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/payment-card.png" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">Total Revenue</div>
            <div class="kpi-hero-value">${formatCurrency(revenue.totalRevenue)}</div>
          </div>
        </div>
        <div class="kpi-hero-delta">${revenue.paymentCount} payment(s) · ${periodNote}</div>
      </div>
      <div class="kpi-hero-card">
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/calendar-simple.png" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">Bookings on ${formatShortDate(summary.date)}</div>
            <div class="kpi-hero-value">${summary.todayBookingsTotal.toLocaleString("en-MY")}</div>
          </div>
        </div>
        <div class="kpi-hero-delta">Across grooming, boarding &amp; daycare</div>
      </div>
      <div class="kpi-hero-card">
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/users.png" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">Repeat Customer Rate</div>
            <div class="kpi-hero-value">—</div>
          </div>
        </div>
        <div class="kpi-hero-delta">Not available yet</div>
      </div>
      <div class="kpi-hero-card">
        <div class="kpi-hero-top">
          <div class="kpi-hero-icon"><img src="icon/line-chart.png" alt="" class="kpi-icon-img"></div>
          <div>
            <div class="kpi-hero-label">Occupancy / Slot Utilisation</div>
            <div class="kpi-hero-value">—</div>
          </div>
        </div>
        <div class="kpi-hero-delta">Not available yet</div>
      </div>
    `;
  } catch (error) {
    console.error(error);
  }
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
    const rows = days.map(date => {
      const booked = bookings.filter(booking => booking.date === date && booking.status !== "cancelled" && booking.status !== "no_show").length;
      const capacity = CALENDAR_HOURS.length * 3;
      const pct = capacity ? Math.round((booked / capacity) * 100) : 0;
      return renderDetailRow({ title: formatShortDate(date), sub: `${booked} active booking(s) · ${capacity} available slots`, tag: pct >= 50 ? "scheduled" : "done", tagLabel: `${pct}% used` });
    }).join("");
    openDetailModal("Slot Utilisation", `Average ${Math.round(metrics.occupancyRate)}% utilisation · ${label}`, rows, { label: "Open Booking Dashboard", href: "booking.html" });
  } else if (key === "repeat") {
    const lifetimeCountByCustomer = {};
    bookings.forEach(b => {
      const customerKey = bookingCustomerKey(b);
      lifetimeCountByCustomer[customerKey] = (lifetimeCountByCustomer[customerKey] || 0) + 1;
    });
    const periodCustomers = [...new Map(metrics.periodBookings.map(b => [bookingCustomerKey(b), b.customerName])).entries()];
    const rows = periodCustomers.map(([customerKey, name]) => {
      const lifetimeCount = lifetimeCountByCustomer[customerKey];
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
    const key = bookingCustomerKey(b);
    if (!map[key] || b.date < map[key]) map[key] = b.date;
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
      const key = bookingCustomerKey(b);
      (firstDate[key] === b.date ? newSet : returningSet).add(key);
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
    const pendingGrooming = bookings.filter(b => b.serviceType === "grooming" && b.date === today && b.status === "pending");
    const pendingEnquiries = enquiries.filter(e => e.status === "pending");
    const rows = [...pendingGrooming.map(bookingDetailRow), ...pendingEnquiries.map(enquiryDetailRow), ...pendingPaymentRecords.map(realPaymentDetailRow)].join("");
    const total = pendingGrooming.length + pendingEnquiries.length + pendingPaymentRecords.length;
    openDetailModal("SLA Compliance", `${total} item(s) currently tracked against SLA.`, rows, { label: "Open Daily Overview", href: "dailyoverview.html" });
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
  const weekdayKey = new Date(today + "T00:00:00").toLocaleDateString("en-US", { weekday: "short" });
  const operatingHours = dashboardCompanySettings.business_hours?.[weekdayKey] || "Not configured";
  const rows = [
    { key: "members", icon: "users.png", label: "Active Members", value: customers.length.toLocaleString("en-MY") },
    { key: "pets", icon: "paw-print.png", label: "Total Pets", value: pets.length.toLocaleString("en-MY") },
    { key: "staff", icon: "team.png", label: "Staff On Duty Today", value: staff.filter(s => isStaffOnDuty(s.id, today)).length },
    { key: null, icon: "time.png", label: "Operating Hours", value: operatingHours }
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
  const weekChipPicker = q("dashboardWeekChipPickerInput");
  if (weekChipPicker) weekChipPicker.value = dashboardAnchorDate;

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
   where a real source exists (enquiries, payments, loyalty). Metrics without
   a backend source are omitted instead of being filled with sample values.
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
  const member = findStaffRecord(staffId);
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
  if (key === "bookingIntent") {
    const items = realMessages.filter(m => m.intent_label === "booking");
    const rows = items.map(m => renderDetailRow({
      title: m.customerName, sub: m.message_text || m.intent_label || "—",
      tag: "info", tagLabel: m.intent_label || "—"
    })).join("");
    openDetailModal("Booking-Related Messages", `${items.length} of ${realMessages.length} messages.`, rows, { label: "Open Enquiries", href: "enquiries.html" });
  } else if (key === "payment") {
    const rows = realPayments.map(p => renderDetailRow({
      title: p.customer_name || p.service, sub: `PAY-${String(p.payment_id).padStart(4, "0")} · RM ${Number(p.final_amount).toLocaleString()}`,
      tag: p.status === "Paid" ? "done" : "pending", tagLabel: p.status
    })).join("");
    const verified = realPayments.filter(p => p.status === "Paid").length;
    openDetailModal("Payment Verification", `${verified} of ${realPayments.length} verified.`, rows, { label: "Open Payment", href: "payment.html" });
  } else if (key === "loyalty") {
    const rows = realRedemptions.map(r => renderDetailRow({
      title: r.memberName, sub: `${r.couponName || "Loyalty transaction"} · ${r.loyalty_spend || 0} pts`,
      tag: r.status === "Refunded" ? "cancelled" : "done", tagLabel: r.status || "Approved"
    })).join("");
    const approved = realRedemptions.filter(r => r.status !== "Refunded").length;
    openDetailModal("Loyalty Requests Processed", `${approved} of ${realRedemptions.length} approved.`, rows, { label: "Open Loyalty", href: "loyalty.html" });
  }
}

function renderSystemKpiHero() {
  const totalMessages = realMessages.length;
  const bookingMessages = realMessages.filter(m => m.intent_label === "booking").length;
  const totalPayments = realPayments.length;
  const verifiedPayments = realPayments.filter(p => p.status === "Paid").length;
  const totalLoyalty = realRedemptions.length;
  const approvedLoyalty = realRedemptions.filter(r => r.status !== "Refunded").length;

  const cards = [
    { key: "bookingIntent", icon: "chat-message.png", label: "Booking-Related Messages", value: `${totalMessages ? Math.round((bookingMessages / totalMessages) * 100) : 0}%`, sub: `${bookingMessages}/${totalMessages} messages`, clickable: true },
    { key: "payment", icon: "payment-card.png", label: "Payment Verification Rate", value: `${totalPayments ? Math.round((verifiedPayments / totalPayments) * 100) : 0}%`, sub: `${verifiedPayments}/${totalPayments} verified`, clickable: true },
    { key: "loyalty", icon: "loyalty-reward-gift.png", label: "Loyalty Requests Processed", value: `${totalLoyalty ? Math.round((approvedLoyalty / totalLoyalty) * 100) : 0}%`, sub: `${approvedLoyalty}/${totalLoyalty} approved`, clickable: true },
    { key: null, icon: "analytics-dashboard.png", label: "Data Source", value: "Live", sub: "Connected backend records", clickable: false }
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
  const values = buckets.map(bucket => {
    const items = enquiries.filter(bucket.matches);
    const resolved = items.filter(item => item.status === "resolved").length;
    return items.length ? Math.round((resolved / items.length) * 1000) / 10 : 0;
  });
  const labels = buckets.map(b => b.label);

  const subtitle = q("systemAutomationTrendSub");
  if (subtitle) subtitle.textContent = `Real enquiry data · ${periodRangeLabel(metrics.period, metrics.range)}`;

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
    { icon: "analytics-dashboard.png", label: "Application API", value: "Connected" },
    { icon: "calendar-simple.png", label: "Bookings Loaded", value: bookings.length.toLocaleString("en-MY") },
    { icon: "payment-card.png", label: "Payments Loaded", value: paymentRecords.length.toLocaleString("en-MY") },
    { icon: "time.png", label: "Last Refresh", value: dashboardLastLoadedAt ? dashboardLastLoadedAt.toLocaleTimeString("en-MY", { hour: "2-digit", minute: "2-digit" }) : "—" }
  ];

  q("systemIntegrationSnapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row">
      <span class="snapshot-row-label"><img src="icon/${r.icon}" alt="" class="snapshot-icon">${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

function renderSystemHighlights() {
  const resolved = enquiries.filter(e => e.status === "resolved").length;
  const pendingPayments = paymentRecords.filter(p => p.status === "pending").length;
  const pendingLeaves = leaveRequests.filter(lv => lv.status === "pending").length;

  const cards = [
    { icon: "chat-message.png", label: "Total Enquiries", value: enquiries.length, sub: "All loaded records", clickable: false },
    { icon: "confirm-circle.png", label: "Resolved Enquiries", value: resolved, sub: "All-time", clickable: true, onclick: "openSystemEnquiryDetail('resolved')" },
    { icon: "payment-card.png", label: "Payments Awaiting", value: pendingPayments, sub: "Needs verification", clickable: false },
    { icon: "time.png", label: "Pending Leave", value: pendingLeaves, sub: "Needs manager review", clickable: false }
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
  q("dashboardTodayBtn")?.addEventListener("click", () => {
    dashboardAnchorDate = getToday();
    renderAnalyticsDashboard();
  });
  q("dashboardNextBtn")?.addEventListener("click", () => shiftDashboardAnchor(1));

  // Picking any date jumps to whichever day/week/month (per the active
  // period tab) contains it — getPeriodRange() already resolves an
  // arbitrary anchor date to its containing range.
  attachDatePickerToLabel("dashboardWeekChip", date => {
    dashboardAnchorDate = date;
    renderAnalyticsDashboard();
  }, dashboardAnchorDate);

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

  try {
    await loadAnalyticsDashboardData();
    renderAnalyticsDashboard();
  } catch (error) {
    console.error(error);
    alert(error.message || "Failed to load analytical dashboard data.");
  }
}

/* ==========================================================================
   SETTINGS (setting.html)
   Business-configuration form ported from reg-setup.html, reorganised into
   tabs. Persisted per business under pawfect_business_settings_<businessKey>
   so changes survive a reload; editing the business name also propagates
   into the account records so the sidebar/dashboard pick it up immediately.
   ========================================================================== */

// Real backend: company_name/country/street_address/city/state/postcode/
// business_description are real `companies` columns (PATCH /api/companies/me).
// Everything else on this page (hours, payment methods, invoice/tax,
// localization, service policy filenames, booking/WhatsApp config) has no
// dedicated column — it lives in one flexible `settings_json` blob (see
// backend/sql/company_settings_migration.sql + routes/companies.js), merged
// server-side so callers only send the keys they're changing. Business logos
// are real JPG/PNG objects in Supabase Storage with their public URL stored in
// companies.logo_path. Policy uploads remain filename-only for now.
const SERVICE_META = {
  grooming: { icon: "grooming-scissors.png", label: "Grooming" },
  boarding: { icon: "boarding.png", label: "Boarding / Hotel" },
  daycare: { icon: "dog-play.png", label: "Daycare" }
};

let currentCompanyRecord = null;
let currentAccountRole = null;
let currentAccountId = null;
let teamAccountRecords = [];
let selectedBusinessLogoFile = null;
let businessLogoPreviewUrl = null;

function showFilename(input, targetId) {
  const el = q(targetId);
  if (el && input.files.length) el.innerHTML = `<img src="icon/upload.png" alt="" class="row-icon">${input.files[0].name}`;
}

function showBusinessLogoPreview(source, label) {
  const preview = q("businessLogoPreview");
  const icon = q("businessLogoUploadIcon");
  const name = q("logoFileName");
  if (!preview || !source) return;
  preview.src = source;
  preview.classList.remove("hidden");
  icon?.classList.add("hidden");
  if (name) name.textContent = label;
}

function handleBusinessLogoSelection(input) {
  const file = input.files?.[0];
  if (!file) return;
  if (!["image/png", "image/jpeg"].includes(file.type)) {
    alert("Business logo must be a PNG or JPG file.");
    input.value = "";
    return;
  }
  if (file.size > 2 * 1024 * 1024) {
    alert("Business logo must be 2 MB or smaller.");
    input.value = "";
    return;
  }
  if (businessLogoPreviewUrl) URL.revokeObjectURL(businessLogoPreviewUrl);
  businessLogoPreviewUrl = URL.createObjectURL(file);
  selectedBusinessLogoFile = file;
  showBusinessLogoPreview(businessLogoPreviewUrl, `${file.name} · ready to upload`);
}

function renderServicePolicyCards(company) {
  const settings = company?.settings_json || {};
  const activeServices = settings.selected_services || ["grooming", "boarding", "daycare"];

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

function populateSettingsForm(company) {
  const s = company?.settings_json || {};

  q("cfg_businessName").value = company?.company_name || "";
  q("cfg_country").value = company?.country || "Malaysia";
  q("cfg_street").value = company?.street_address || "";
  q("cfg_city").value = company?.city || "";
  q("cfg_state").value = company?.state || "";
  q("cfg_zip").value = company?.postcode || "";
  q("cfg_description").value = company?.business_description || "";
  if (/^https?:\/\//i.test(company?.logo_path || "")) {
    showBusinessLogoPreview(company.logo_path, "Current business logo · choose a new file to replace it");
  } else {
    q("logoFileName").textContent = company?.logo_path
      ? `Legacy logo reference: ${company.logo_path} · choose a JPG/PNG to replace it`
      : "Click to upload logo (PNG, JPG — recommended 200×200 px)";
  }

  const hours = s.business_hours || {};
  ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].forEach(day => { q("hour_" + day).value = hours[day] || ""; });
  q("cfg_closedDates").value = s.closed_dates || "";

  const payment = s.payment_methods || { cash: true, card: true, qr: true, online: false };
  q("pay_cash").checked = !!payment.cash;
  q("pay_card").checked = !!payment.card;
  q("pay_qr").checked = !!payment.qr;
  q("pay_online").checked = !!payment.online;
  q("cfg_invoicePrefix").value = s.invoice_prefix || "";
  q("cfg_taxName").value = s.tax_name || "";
  q("cfg_language").value = s.language || "English";
  q("cfg_timezone").value = s.timezone || "Kuala Lumpur (GMT+8)";
  q("cfg_currency").value = s.currency || "MYR (RM)";
  q("cfg_weight").value = s.weight_unit || "kg";
  q("cfg_height").value = s.height_unit || "cm";

  q("cfg_bookingUrl").value = s.booking_url || "";
  q("cfg_whatsapp").value = s.whatsapp_number || "";
  q("cfg_confirmRule").value = s.confirm_rule || "";
}

function collectServicePolicyNames() {
  const activeServices = currentCompanyRecord?.settings_json?.selected_services || ["grooming", "boarding", "daycare"];
  const existing = currentCompanyRecord?.settings_json?.policies || {};
  const result = {};
  activeServices.forEach(type => {
    const input = q("policy_" + type);
    result[type] = input?.files[0]?.name || existing[type] || "";
  });
  return result;
}

async function saveBusinessSettings() {
  const profilePayload = {
    company_name: q("cfg_businessName").value.trim(),
    country: q("cfg_country").value,
    street_address: q("cfg_street").value,
    city: q("cfg_city").value,
    state: q("cfg_state").value,
    postcode: q("cfg_zip").value,
    business_description: q("cfg_description").value,
  };

  if (!profilePayload.company_name || !profilePayload.postcode) {
    alert("Business name and postcode are required.");
    return;
  }

  const settingsPayload = {
    business_hours: {
      Mon: q("hour_Mon").value, Tue: q("hour_Tue").value, Wed: q("hour_Wed").value,
      Thu: q("hour_Thu").value, Fri: q("hour_Fri").value, Sat: q("hour_Sat").value, Sun: q("hour_Sun").value
    },
    closed_dates: q("cfg_closedDates").value,
    payment_methods: { cash: q("pay_cash").checked, card: q("pay_card").checked, qr: q("pay_qr").checked, online: q("pay_online").checked },
    invoice_prefix: q("cfg_invoicePrefix").value,
    tax_name: q("cfg_taxName").value,
    language: q("cfg_language").value,
    timezone: q("cfg_timezone").value,
    currency: q("cfg_currency").value,
    weight_unit: q("cfg_weight").value,
    height_unit: q("cfg_height").value,
    policies: collectServicePolicyNames(),
    booking_url: q("cfg_bookingUrl").value,
    whatsapp_number: q("cfg_whatsapp").value,
    confirm_rule: q("cfg_confirmRule").value,
  };

  try {
    if (selectedBusinessLogoFile) {
      currentCompanyRecord = await api.uploadCompanyLogo(selectedBusinessLogoFile);
      selectedBusinessLogoFile = null;
      if (businessLogoPreviewUrl) URL.revokeObjectURL(businessLogoPreviewUrl);
      businessLogoPreviewUrl = null;
      q("cfg_logo").value = "";
      showBusinessLogoPreview(currentCompanyRecord.logo_path, "Current business logo · upload complete");
    }
    currentCompanyRecord = await api.patch("/companies/me", { ...profilePayload, settings: settingsPayload });

    const storedAccount = getCurrentAccount();
    if (storedAccount) {
      storedAccount.businessName = currentCompanyRecord.company_name || storedAccount.businessName;
      storedAccount.logoPath = currentCompanyRecord.logo_path || storedAccount.logoPath || "";
      localStorage.setItem("pawfect_current_account", JSON.stringify(storedAccount));
      renderSidebarAccount();
    }

    const note = q("settingsSavedNote");
    if (note) {
      note.style.opacity = "1";
      clearTimeout(window.__settingsSavedTimer);
      window.__settingsSavedTimer = setTimeout(() => { note.style.opacity = "0"; }, 2200);
    }
  } catch (error) {
    alert(error.message || "Failed to save settings.");
  }
}

async function initSettings() {
  document.querySelectorAll("#settingsTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#settingsTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".settings-panel").forEach(panel => {
        panel.classList.toggle("hidden", panel.id !== `panel-${btn.dataset.panel}`);
      });
    });
  });

  await refreshSettingsPageData();
}

async function refreshSettingsPageData() {
  try {
    const [company, me, accounts] = await Promise.all([
      api.get("/companies/me"),
      api.get("/accounts/me"),
      api.get("/accounts"),
    ]);
    currentCompanyRecord = company;
    currentAccountRole = me.role;
    currentAccountId = me.account_id;
    teamAccountRecords = accounts;
  } catch (error) {
    alert(error.message || "Failed to load settings.");
    return;
  }

  populateSettingsForm(currentCompanyRecord);
  renderServicePolicyCards(currentCompanyRecord);
  renderAccountsPanel();

  const manager = currentAccountRole === "manager";
  q("settingsSaveBar")?.classList.toggle("hidden", !manager);
  document.querySelectorAll(".settings-panel input, .settings-panel select, .settings-panel textarea")
    .forEach(control => { control.disabled = !manager; });
}

/* =========================
   TEAM ACCOUNTS (Manager: full control · Staff: read-only)
========================= */

function renderAccountsPanel() {
  const manager = currentAccountRole === "manager";

  q("accountsManagerView").classList.toggle("hidden", !manager);
  q("accountsStaffView").classList.toggle("hidden", manager);

  if (!manager) {
    q("staffOrgName").textContent = currentCompanyRecord?.company_name || "—";
    q("staffOrgRole").textContent = currentAccountRole || "—";
    q("staffOrgEmail").textContent = getCurrentAccount()?.email || "—";
    return;
  }

  const nameEl = q("accountsBusinessName");
  if (nameEl) nameEl.textContent = currentCompanyRecord?.company_name || "this business";

  q("accountsTableBody").innerHTML = teamAccountRecords.map(a => {
    const current = Number(a.account_id) === Number(currentAccountId);
    return `
    <tr>
      <td><span class="profile-name">${escapeUiText(a.email || `ACC-${a.account_id}`)}</span><br><span class="profile-sub">ACC-${a.account_id}</span></td>
      <td>${current
        ? `<span class="status-tag ${a.role === "manager" ? "status-scheduled" : "status-done"}">${a.role}</span>`
        : `<select id="accountRole_${a.account_id}" aria-label="Role for ACC-${a.account_id}">
            <option value="staff" ${a.role === "staff" ? "selected" : ""}>Staff</option>
            <option value="manager" ${a.role === "manager" ? "selected" : ""}>Manager</option>
          </select>`}</td>
      <td>${current
        ? `<span class="status-tag status-done">${a.account_status}</span>`
        : `<select id="accountStatus_${a.account_id}" aria-label="Status for ACC-${a.account_id}">
            <option value="active" ${a.account_status === "active" ? "selected" : ""}>Active</option>
            <option value="inactive" ${a.account_status === "inactive" ? "selected" : ""}>Inactive</option>
          </select>`}</td>
      <td>${current
        ? `<span class="profile-sub">Current account</span>`
        : `<div class="account-editor">
            <button class="edit-btn" onclick="saveTeamAccount(${a.account_id})">Save</button>
            <button class="action-btn" onclick="removeTeamAccount(${a.account_id})">Remove</button>
          </div>`}</td>
    </tr>
  `;
  }).join("");
}

async function saveTeamAccount(accountId) {
  const role = q(`accountRole_${accountId}`)?.value;
  const accountStatus = q(`accountStatus_${accountId}`)?.value;
  try {
    await api.patch(`/accounts/${accountId}`, { role, account_status: accountStatus });
    await refreshSettingsPageData();
  } catch (error) {
    alert(error.message || "Failed to update account.");
  }
}

async function addTeamAccount() {
  const email = q("newAccountEmail").value.trim().toLowerCase();
  const password = q("newAccountPassword").value;
  const role = q("newAccountRole").value.toLowerCase();
  const note = q("accountsAddNote");

  if (!email || !password) {
    if (note) { note.textContent = "Enter an email and password."; note.style.color = "#DC2626"; }
    return;
  }
  if (password.length < 8) {
    if (note) { note.textContent = "Password must be at least 8 characters."; note.style.color = "#DC2626"; }
    return;
  }

  try {
    await api.post("/accounts", { email, password, role });
    q("newAccountEmail").value = "";
    q("newAccountPassword").value = "";
    q("newAccountRole").value = "Staff";
    if (note) { note.textContent = "✓ Account added."; note.style.color = "#059669"; }
    await refreshSettingsPageData();
  } catch (error) {
    if (note) { note.textContent = error.message || "Failed to add account."; note.style.color = "#DC2626"; }
  }
}

async function removeTeamAccount(accountId) {
  if (!confirm(`Remove ACC-${accountId} from this business? This cannot be undone.`)) return;

  try {
    await api.del(`/accounts/${accountId}`);
    await refreshSettingsPageData();
  } catch (error) {
    alert(error.message || "Failed to remove account.");
  }
}
