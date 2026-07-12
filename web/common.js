function q(id) { return document.getElementById(id); }

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

const staff = [
  { id: "ST001", name: "Sarah Wong", role: "Manager" },
  { id: "ST002", name: "Adam Tan", role: "Groomer" },
  { id: "ST003", name: "Mei Ling", role: "Caretaker" }
];

const rooms = [
  { id: "R001", name: "Room A", type: "boarding", capacity: 5 },
  { id: "R002", name: "Room B", type: "boarding", capacity: 4 },
  { id: "R003", name: "Playroom 1", type: "daycare", capacity: 12 },
  { id: "R004", name: "Playroom 2", type: "daycare", capacity: 10 }
];

const today = getToday();

let bookings = [
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

/* =========================
   STATE
========================= */

let currentServiceFilter = "all";
let currentView = "kanban";
let kanbanDateMode = "today";
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

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("liveDateTime")) {
    initLiveClock();
  }

  if (document.getElementById("kanbanBoard")) {
    setupTabs();
    setupModalEvents();
    setupCalendarEvents();
    setupListingEvents();
    populateDropdowns();
    renderAll();

    setTimeout(() => scrollCalendarToToday(), 100);
  }

  if (document.getElementById("actionCards")) {
    setupDailyOverview();
  }

  if (document.getElementById("profileTypeFilter")) {
    initCRM();
  }

  if (document.getElementById("kpiHeroGrid")) {
    initAnalyticsDashboard();
  }

  if (document.getElementById("settingsTabs")) {
    initSettings();
  }

  if (document.getElementById("loyaltyPendingBody")) {
    initLoyaltyPage();
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
    el.textContent = `🗓️ ${dateStr} · ${timeStr}`;
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

  document.getElementById("addBookingBtn").addEventListener("click", () => {
    openNewBooking();
  });
}

function setupModalEvents() {
  document.getElementById("closeModalBtn").addEventListener("click", closeModal);

  document.getElementById("serviceType").addEventListener("change", () => {
    populateServiceDropdown();
    toggleServiceSpecificFields();
    autoCalculateAmount();
  });

  document.getElementById("requiredService").addEventListener("change", () => {
    syncServiceData();
  });

  document.getElementById("bookingForm").addEventListener("submit", event => {
    event.preventDefault();
    saveBooking();
  });

  document.getElementById("doneServiceBtn").addEventListener("click", () => {
    document.getElementById("bookingStatus").value = "done";
    saveBooking();
  });

  document.getElementById("noShowBtn").addEventListener("click", () => {
    document.getElementById("bookingStatus").value = "no_show";
    saveBooking();
  });

  document.getElementById("openPetProfileBtn").addEventListener("click", () => {
    const petName = document.getElementById("petName").value;
    alert(`Open pet profile: ${petName}`);
  });

  document.getElementById("cancelBookingBtn").addEventListener("click", () => {
    cancelBooking();
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

    cell.addEventListener("drop", event => {
      event.preventDefault();
      const booking = bookings.find(item => item.id === draggedBookingId);
      if (!booking) return;
      const newDate = cell.dataset.date;
      const newTime = cell.dataset.time || booking.time;
      if (!canAddBookingToSlot(newDate, newTime, booking.staffId, booking.id)) {
        alert("This slot is not available. Maximum 3 bookings are allowed per timeslot, and staff cannot be duplicated.");
        draggedBookingId = null;
        return;
      }
      booking.date = newDate;
      booking.time = newTime;
      renderAll();
      draggedBookingId = null;
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
    if (kanbanDateMode === "today") return booking.date === getToday();
    return true;
  });
}

/* =========================
   METRIC CARDS
========================= */

function renderMetricCards() {
  const wrapper = document.getElementById("metricCards");
  const data = getFilteredBookings();

  const metrics = buildMetrics(currentServiceFilter, data);

  wrapper.innerHTML = metrics.map(metric => `
    <div class="metric-card">
      <h3>${metric.label}</h3>
      <p>${metric.value}</p>
    </div>
  `).join("");
}

function buildMetrics(filter, data) {
  if (filter === "grooming") {
    const grooming = data.filter(b => b.serviceType === "grooming");
    const doneRate = percentage(
      grooming.filter(b => b.status === "done").length,
      grooming.length
    );

    return [
      { label: "Booking Today", value: countToday(grooming) },
      { label: "Done Rate", value: doneRate },
      { label: "Pending Booking", value: countStatus(grooming, "pending") },
      { label: "Staff Utilization", value: "76%" },
      { label: "No-show", value: countStatus(grooming, "no_show") }
    ];
  }

  if (filter === "boarding") {
    const boarding = data.filter(b => b.serviceType === "boarding");

    return [
      { label: "Boarding Today", value: countToday(boarding) },
      { label: "Current Boarder", value: boarding.filter(b => b.status !== "no_show").length },
      { label: "Today Check-in", value: boarding.filter(b => b.checkInDate === getToday()).length },
      { label: "Today Check-out", value: boarding.filter(b => b.checkOutDate === getToday()).length }
    ];
  }

  if (filter === "daycare") {
    const daycare = data.filter(b => b.serviceType === "daycare");
    const usedCapacity = daycare.filter(b => b.date === getToday() && b.status !== "no_show").length;
    const totalCapacity = rooms
      .filter(r => r.type === "daycare")
      .reduce((sum, room) => sum + room.capacity, 0);

    return [
      { label: "Daycare Today", value: countToday(daycare) },
      { label: "Pending Pick-up", value: daycare.filter(b => b.status === "done").length },
      { label: "No-show", value: countStatus(daycare, "no_show") },
      { label: "Capacity", value: `${usedCapacity}/${totalCapacity}` }
    ];
  }

  const grooming = data.filter(b => b.serviceType === "grooming");
  const groomingDoneRate = percentage(
    grooming.filter(b => b.status === "done").length,
    grooming.length
  );

  return [
    { label: "Pending Services", value: countStatus(data, "pending") },
    { label: "Grooming Done Rate", value: groomingDoneRate },
    { label: "Boarding Check-in / Check-out", value: `${todayCheckIn()}/${todayCheckOut()}` },
    { label: "Daycare Attendance", value: countToday(data.filter(b => b.serviceType === "daycare")) },
    { label: "No Show", value: countStatus(data, "no_show") }
  ];
}

/* =========================
   KANBAN
========================= */

function renderKanban() {
  const board = document.getElementById("kanbanBoard");

  const columns = [
    { key: "pending", label: "Pending Service" },
    { key: "scheduled", label: "Scheduled" },
    { key: "done", label: "Done" },
    { key: "no_show", label: "No Show" }
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
  const service = findService(booking.serviceId);
  const staffMember = findStaff(booking.staffId);

  return `
    <div class="booking-card" draggable="true" data-booking-id="${booking.id}">
      <div class="booking-card-top">
        ${renderStatusTag(booking.status)}
      </div>

      <strong>${booking.petName} — ${service?.name || "-"}</strong>
      <small>${booking.customerName}</small><br>
      <small>${booking.date} | ${booking.time}</small><br>
      <small>Staff: ${staffMember?.name || "-"}</small><br>
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

    column.addEventListener("drop", event => {
      event.preventDefault();

      const newStatus = event.currentTarget.dataset.status;
      const booking = bookings.find(b => b.id === draggedBookingId);

      if (booking) {
        booking.status = newStatus;
        renderAll();
      }

      draggedBookingId = null;
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
  const service = findService(booking.serviceId);

  return `
    <div class="calendar-booking" draggable="true" data-booking-id="${booking.id}">
      <strong>${booking.petName}</strong><br>
      ${service?.name || "-"}<br>
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


function openNewBooking() {
  const date = getToday();
  const time = findFirstAvailableTime(date) || "09:00";
  const defaultService = services.find(s => s.type === "grooming") || services[0];

  const newId = `B${String(++_bookingIdCounter).padStart(3, "0")}`;

  _newBookingDraft = {
    id: newId,
    customerName: "",
    petName: "",
    serviceType: defaultService.type,
    serviceId: defaultService.id,
    staffId: staff[0]?.id || "",
    roomId: "",
    date,
    time,
    duration: defaultService.duration,
    status: "scheduled",
    amount: defaultService.price,
    checkInDate: "",
    checkOutDate: "",
    specialNote: ""
  };

  openBookingDetails(_newBookingDraft);
}

function createBookingFromSlot(date, time) {
  const availableStaff = getAvailableStaffForSlot(date, time);

  if (getSlotBookings(date, time).length >= 3 || availableStaff.length === 0) {
    alert("This timeslot is fully booked. Maximum 3 bookings are allowed, and each booking must use a different staff.");
    return;
  }

  const newId = `B${String(++_bookingIdCounter).padStart(3, "0")}`;

  const defaultService = services.find(service => {
    if (currentServiceFilter === "all") return service.type === "grooming";
    return service.type === currentServiceFilter;
  });

  _newBookingDraft = {
    id: newId,
    customerName: "",
    petName: "",
    serviceType: defaultService?.type || "grooming",
    serviceId: defaultService?.id || "S001",
    staffId: availableStaff[0].id,
    roomId: "",
    date,
    time,
    duration: defaultService?.duration || 60,
    status: "scheduled",
    amount: defaultService?.price || 0,
    checkInDate: "",
    checkOutDate: "",
    specialNote: ""
  };

  openBookingDetails(_newBookingDraft);
}

/* =========================
   LISTING
========================= */

function renderListing() {
  const tbody = document.getElementById("listingTableBody");

  const filteredData = getFilteredBookings().filter(booking => {
    const service = findService(booking.serviceId);
    const staffMember = findStaff(booking.staffId);

    const searchableText = `
      ${booking.customerName}
      ${booking.petName}
      ${service?.name || ""}
      ${staffMember?.name || ""}
    `.toLowerCase();

    return searchableText.includes(listingSearchKeyword);
  });

  filteredData.sort((a, b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));

  tbody.innerHTML = filteredData.map(booking => {
    const service = findService(booking.serviceId);
    const staffMember = findStaff(booking.staffId);
    const room = findRoom(booking.roomId);

    return `
      <tr>
        <td>${booking.date}</td>
        <td>${booking.customerName}</td>
        <td>${booking.petName}</td>
        <td>${service?.name || "-"}</td>
        <td>${staffMember?.name || "-"}</td>
        <td>${booking.time}</td>
        <td>${booking.duration} mins</td>

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
          </select>
        </td>

        <td>RM ${booking.amount}</td>
        <td>${room?.name || "-"}</td>

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

function updateListingStatus(event) {
  event.stopPropagation();

  const bookingId = event.target.dataset.bookingId;
  const newStatus = event.target.value;

  const booking = bookings.find(item => item.id === bookingId);

  if (!booking) return;

  booking.status = newStatus;

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
  document.getElementById("customerName").value = booking.customerName;
  document.getElementById("petName").value = booking.petName;
  document.getElementById("serviceType").value = booking.serviceType;

  populateServiceDropdown();
  populateStaffDropdown();
  populateRoomDropdown();

  document.getElementById("requiredService").value = booking.serviceId;
  document.getElementById("staffName").value = booking.staffId;
  document.getElementById("roomName").value = booking.roomId || "";
  document.getElementById("bookingDate").value = booking.date;
  document.getElementById("bookingTime").value = booking.time;
  document.getElementById("duration").value = booking.duration;
  document.getElementById("bookingStatus").value = booking.status;
  document.getElementById("amount").value = booking.amount;
  document.getElementById("checkInDate").value = booking.checkInDate || "";
  document.getElementById("checkOutDate").value = booking.checkOutDate || "";
  document.getElementById("specialNote").value = booking.specialNote || "";

  toggleServiceSpecificFields();

  document.getElementById("bookingModal").style.display = "flex";
}

function closeModal() {
  _newBookingDraft = null;
  document.getElementById("bookingModal").style.display = "none";
}

function cancelBooking() {
  const bookingId = document.getElementById("bookingId").value;
  if (!bookingId) return;

  if (_newBookingDraft && _newBookingDraft.id === bookingId) {
    closeModal();
    return;
  }

  const booking = bookings.find(b => b.id === bookingId);
  if (!booking) return;

  const label = booking.petName
    ? `${booking.petName} (${booking.customerName || "unknown"})`
    : bookingId;

  if (!confirm(`Cancel booking for ${label}? This cannot be undone.`)) return;

  bookings = bookings.filter(b => b.id !== bookingId);
  closeModal();
  renderAll();
}

function saveBooking() {
  const bookingId = document.getElementById("bookingId").value;
  let booking = bookings.find(item => item.id === bookingId);

  if (!booking) {
    if (_newBookingDraft && _newBookingDraft.id === bookingId) {
      booking = _newBookingDraft;
      bookings.push(booking);
      _newBookingDraft = null;
    } else {
      return;
    }
  }

  const newDate = document.getElementById("bookingDate").value;
  const newTime = document.getElementById("bookingTime").value;
  const newStaffId = document.getElementById("staffName").value;

  if (!canAddBookingToSlot(newDate, newTime, newStaffId, bookingId)) {
    alert("This booking cannot be saved. The selected timeslot already has 3 bookings or the selected staff is already assigned at this time.");
    return;
  }

  booking.customerName = document.getElementById("customerName").value;
  booking.petName = document.getElementById("petName").value;
  booking.serviceType = document.getElementById("serviceType").value;
  booking.serviceId = document.getElementById("requiredService").value;
  booking.staffId = newStaffId;
  booking.roomId = document.getElementById("roomName").value;
  booking.date = newDate;
  booking.time = newTime;
  booking.duration = Number(document.getElementById("duration").value);
  booking.status = document.getElementById("bookingStatus").value;
  booking.amount = Number(document.getElementById("amount").value);
  booking.checkInDate = document.getElementById("checkInDate").value;
  booking.checkOutDate = document.getElementById("checkOutDate").value;
  booking.specialNote = document.getElementById("specialNote").value;

  closeModal();
  renderAll();
}

function populateDropdowns() {
  populateServiceDropdown();
  populateStaffDropdown();
  populateRoomDropdown();
}

function populateServiceDropdown() {
  const selectedType = document.getElementById("serviceType").value;
  const serviceSelect = document.getElementById("requiredService");

  serviceSelect.innerHTML = services
    .filter(service => service.type === selectedType)
    .map(service => `
      <option value="${service.id}">
        ${service.name} — RM ${service.price}
      </option>
    `).join("");
}

function populateStaffDropdown() {
  const staffSelect = document.getElementById("staffName");

  staffSelect.innerHTML = staff.map(member => `
    <option value="${member.id}">
      ${member.name}
    </option>
  `).join("");
}

function populateRoomDropdown() {
  const selectedType = document.getElementById("serviceType").value;
  const roomSelect = document.getElementById("roomName");

  roomSelect.innerHTML = `
    <option value="">No Room</option>
    ${rooms
      .filter(room => room.type === selectedType)
      .map(room => `
        <option value="${room.id}">
          ${room.name} — Capacity ${room.capacity}
        </option>
      `).join("")}
  `;
}

function toggleServiceSpecificFields() {
  const selectedType = document.getElementById("serviceType").value;

  document.querySelectorAll(".room-field").forEach(field => {
    field.classList.toggle("hidden", !["boarding", "daycare"].includes(selectedType));
  });

  document.querySelectorAll(".boarding-field").forEach(field => {
    field.classList.toggle("hidden", selectedType !== "boarding");
  });

  populateRoomDropdown();
}

function syncServiceData() {
  const serviceId = document.getElementById("requiredService").value;
  const service = findService(serviceId);

  if (!service) return;

  document.getElementById("duration").value = service.duration;
  document.getElementById("amount").value = service.price;
}

function autoCalculateAmount() {
  syncServiceData();
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

function addMonths(dateString, months) {
  const date = new Date(dateString);
  date.setMonth(date.getMonth() + months);
  return date.toISOString().slice(0, 10);
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
  return getFilteredBookings().filter(booking => {
    return booking.date === date &&
      booking.time === time &&
      booking.id !== excludeBookingId;
  });
}

function isStaffAlreadyBooked(date, time, staffId, excludeBookingId = "") {
  return bookings.some(booking => {
    return booking.date === date &&
      booking.time === time &&
      booking.staffId === staffId &&
      booking.id !== excludeBookingId;
  });
}

function getAvailableStaffForSlot(date, time, excludeBookingId = "") {
  return staff.filter(member => {
    return !isStaffAlreadyBooked(date, time, member.id, excludeBookingId);
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
  return data.filter(booking => booking.date === getToday()).length;
}

function countStatus(data, status) {
  return data.filter(booking => booking.status === status).length;
}

function todayCheckIn() {
  return bookings.filter(b => b.serviceType === "boarding" && b.checkInDate === getToday()).length;
}

function todayCheckOut() {
  return bookings.filter(b => b.serviceType === "boarding" && b.checkOutDate === getToday()).length;
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
    no_show: "No Show"
  };

  return map[status] || status;
}

function getToday() {
  return new Date().toISOString().slice(0, 10);
}

function addDays(dateString, days) {
  const date = new Date(dateString);
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function getStartOfWeek(dateString) {
  const date = new Date(dateString);
  const day = date.getDay(); // 0 = Sun
  const diff = day === 0 ? -6 : 1 - day; // shift to Monday
  date.setDate(date.getDate() + diff);
  return date.toISOString().slice(0, 10);
}

function getStartOfMonth(dateString) {
  const date = new Date(dateString);
  date.setDate(1);
  return date.toISOString().slice(0, 10);
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
    dates.push(current.toISOString().slice(0, 10));
    current.setDate(current.getDate() + 1);
  }

  return dates;
}


/* ==========================================================================
   DAILY OVERVIEW (dailyoverview.html)
   ========================================================================== */

let currentFilter = 'all';
let weekAnchor = getStartOfWeek(today);

function findServiceName(id) { return findService(id)?.name || '-'; }
function findRoomName(id) { return findRoom(id)?.name || '-'; }
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

function buildActionCards(filter) {
  const todayBookings = filterBookingsByService(filter).filter(b => b.date === today);
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
  { key: 'no_show',   label: 'No Show',         color: '#EF4444' }
];

function renderStatusDonut(filter) {
  const todayBookings = filterBookingsByService(filter).filter(b => b.date === today);
  const counts = STATUS_META.map(s => todayBookings.filter(b => b.status === s.key).length);
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
      const slotBookings = filterBookingsByService(filter).filter(b => b.date === date && slotForTime(b.time) === hour);
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
  const todaysBookings = filterBookingsByService(filter).filter(b => b.date === today);

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
    filterBookingsByService(filter).forEach(b => {
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
  const todayBookings = filterBookingsByService(filter).filter(b => b.date === today);
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
  const items = filterBookingsByService(currentFilter).filter(b => b.date === today && b.status === statusKey);
  const labelMap = { pending: 'Pending Service', scheduled: 'Scheduled', done: 'Done', no_show: 'No Show' };
  openDetailModal(`Today's Bookings — ${labelMap[statusKey]}`, `${items.length} booking(s).`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openRoomDetail(roomId) {
  const room = findRoom(roomId);
  const items = bookings.filter(b => b.roomId === roomId).sort((a, b) => a.date.localeCompare(b.date));
  openDetailModal(`${room.name} — Bookings`, `${items.length} booking(s) using this room.`, items.map(bookingDetailRow).join(''), { label: 'Open Booking Dashboard', href: 'booking.html' });
}

function openDayDetail(date) {
  const items = filterBookingsByService(currentFilter).filter(b => b.date === date);
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
   DAILY OVERVIEW MUTATIONS
========================= */

function refreshCurrentDashboardView() {
  if (document.getElementById('actionCards')) renderDailyOverview();
  else if (document.getElementById('kpiHeroGrid')) renderAnalyticsDashboard();
}

function markBookingDone(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) booking.status = 'done';
  closeDetailModal();
  refreshCurrentDashboardView();
}

function confirmBooking(id) {
  const booking = bookings.find(b => b.id === id);
  if (booking) booking.status = 'pending';
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
  q('actionCards').innerHTML = buildActionCards(currentFilter).map(renderActionCard).join('');
  renderServiceLoadChart();
  renderRoomStatus(currentFilter);
  renderWeeklySchedule(currentFilter);
  renderStatusDonut(currentFilter);
  renderActionQueue(currentFilter);
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

  q('calPrevBtn').addEventListener('click', () => { weekAnchor = addDays(weekAnchor, -7); renderWeeklySchedule(currentFilter); });
  q('calNextBtn').addEventListener('click', () => { weekAnchor = addDays(weekAnchor, 7); renderWeeklySchedule(currentFilter); });
  q('calTodayBtn').addEventListener('click', () => { weekAnchor = getStartOfWeek(today); renderWeeklySchedule(currentFilter); });

  q('detailModal').addEventListener('click', event => {
    if (event.target.id === 'detailModal') closeDetailModal();
  });

  renderDailyOverview();
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

let customers = buildCrmCustomers();
let pets = buildCrmPets(customers);
let crmBookings = buildCrmBookings(customers, pets);

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

function initCRM() {
  updateKPI();
  renderLists();

  profileTypeFilter.addEventListener("change", renderLists);
  searchInput.addEventListener("input", renderLists);
}

function updateKPI() {
  document.getElementById("totalCustomers").textContent = customers.length;
  document.getElementById("totalPets").textContent = pets.length;

  document.getElementById("newCustomers").textContent =
    customers.filter(c => {
      const loyaltyNum = parseInt(c.loyalty_id?.replace("LOY-", "") || "0");
      return loyaltyNum > customers.length - 2;
    }).length;

  document.getElementById("attentionNeeded").textContent =
    pets.filter(pet => pet.special_care_note && pet.special_care_note.trim() !== "").length;
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
      customer.phone.toLowerCase().includes(searchValue) ||
      (customer.loyalty_id || "").toLowerCase().includes(searchValue) ||
      customer.customer_id.toLowerCase().includes(searchValue) ||
      linkedPetText.includes(searchValue)
    );
  });
}

function filterPets(searchValue) {
  return pets.filter(pet => {
    const owner = getCustomerById(pet.customer_id);

    return (
      pet.pet_name.toLowerCase().includes(searchValue) ||
      pet.pet_id.toLowerCase().includes(searchValue) ||
      pet.customer_id.toLowerCase().includes(searchValue) ||
      pet.species.toLowerCase().includes(searchValue) ||
      pet.breed.toLowerCase().includes(searchValue) ||
      pet.special_care_note.toLowerCase().includes(searchValue) ||
      owner.full_name.toLowerCase().includes(searchValue) ||
      owner.phone.toLowerCase().includes(searchValue)
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

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">${customer.photo_icon || "👤"} ${customer.full_name}</span>
        <span class="profile-sub">${customer.loyalty_id || "—"}</span>
      </td>

      <td>
        <span class="key-chip">PK: ${customer.customer_id}</span>
      </td>

      <td>${customer.phone}</td>

      <td>
        ${
          lastBooking
            ? `<span class="key-chip">${formatDate(lastBooking.booking_date)}</span>`
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
        <button class="action-btn" onclick="openCustomerForm('${customer.customer_id}')">View / Edit</button>
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

    const row = document.createElement("tr");

    row.innerHTML = `
      <td>
        <span class="profile-name">${getPetIcon(pet.species)} ${pet.pet_name}</span>
        <span class="profile-sub">${pet.species} · ${pet.breed} · ${pet.weight}</span>
      </td>

      <td>
        <span class="key-chip">PK: ${pet.pet_id}</span>
      </td>

      <td>
        <span class="key-chip">FK: ${pet.customer_id}</span>
      </td>

      <td>
        ${owner.full_name}
        <span class="profile-sub">${owner.phone}</span>
      </td>

      <td>
        <span class="badge blue">${pet.service_preference}</span>
      </td>

      <td>
        ${
          pet.special_care_note
            ? `<span class="key-chip">${pet.special_care_note}</span>`
            : `<span class="profile-sub">No special care note</span>`
        }
      </td>

      <td>
        <button class="action-btn" onclick="openPetForm('${pet.pet_id}')">View / Edit</button>
      </td>
    `;

    petTableBody.appendChild(row);
  });
}

function openCustomerForm(customerId = null) {
  const isEdit = Boolean(customerId);
  const customer = isEdit
    ? getCustomerById(customerId)
    : {
        customer_id: generateCustomerId(),
        loyalty_id: generateLoyaltyId(),
        full_name: "",
        phone: "",
        address: "",
        photo_icon: "👤",
        notes: ""
      };

  const linkedPets = isEdit ? getPetsByCustomerId(customer.customer_id) : [];

  detailPage.style.display = "flex";
  detailTitle.textContent = isEdit
    ? `Customer Info · ${customer.customer_id}`
    : "New Customer";

  detailForm.innerHTML = `
    <div class="form-group">
      <label>Customer ID</label>
      <input name="customer_id" value="${customer.customer_id}" readonly />
    </div>

    <div class="form-group">
      <label>Loyalty ID</label>
      <input name="loyalty_id" value="${customer.loyalty_id || ""}" readonly />
    </div>

    <div class="form-group">
      <label>Photo / Icon</label>
      <input name="photo_icon" value="${customer.photo_icon}" />
    </div>

    <div class="form-group">
      <label>Name</label>
      <input name="full_name" value="${customer.full_name}" placeholder="Enter customer name" required />
    </div>

    <div class="form-group">
      <label>Mobile Number</label>
      <input name="phone" value="${customer.phone}" placeholder="+60..." required />
    </div>

    <div class="form-group">
      <label>Address</label>
      <input name="address" value="${customer.address}" placeholder="Enter address" />
    </div>

    <div class="form-group full">
      <label>Notes</label>
      <textarea name="notes" placeholder="Customer reminder, preference, or communication note">${customer.notes}</textarea>
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
                  <span class="profile-name">${getPetIcon(pet.species)} ${pet.pet_name}</span>
                  <span class="profile-sub">${pet.species} · ${pet.breed} · ${pet.weight} · ${pet.colour}</span>
                  <span class="profile-sub">Service: ${pet.service_preference}${pet.special_care_note ? ` &nbsp;|&nbsp; Care: ${pet.special_care_note}` : ""}</span>
                </div>
                <button type="button" class="action-btn" onclick="openPetForm('${pet.pet_id}')">View / Edit</button>
              </div>
            `).join("")
        }
      </div>
    </div>
    ` : ""}

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removeCustomer('${customer.customer_id}')">Remove Customer</button>` : ""}
      <button type="submit" class="save-btn">${isEdit ? "Save Customer" : "Create Customer"}</button>
    </div>
  `;

  detailForm.onsubmit = function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const updatedCustomer = Object.fromEntries(formData.entries());

    if (isEdit) {
      const index = customers.findIndex(item => item.customer_id === customerId);
      customers[index] = updatedCustomer;
    } else {
      customers.push(updatedCustomer);
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
        pet_id: generatePetId(),
        customer_id: customers[0]?.customer_id || "",
        pet_name: "",
        species: "Cat",
        gender: "Male",
        birthdate: "",
        breed: "",
        weight: "",
        colour: "",
        service_preference: "Grooming",
        special_care_note: "",
        additional_note: ""
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
          <option value="${customer.customer_id}" ${customer.customer_id === pet.customer_id ? "selected" : ""}>
            ${customer.full_name} · ${customer.customer_id}
          </option>
        `).join("")}
      </select>
    </div>

    <div class="form-group">
      <label>Pet ID</label>
      <input name="pet_id" value="${pet.pet_id}" readonly />
    </div>

    <div class="form-group">
      <label>Pet Name</label>
      <input name="pet_name" value="${pet.pet_name}" placeholder="Enter pet name" required />
    </div>

    <div class="form-group">
      <label>Type</label>
      <select name="species">
        <option value="Cat" ${pet.species === "Cat" ? "selected" : ""}>Cat</option>
        <option value="Dog" ${pet.species === "Dog" ? "selected" : ""}>Dog</option>
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
      <label>Birthdate</label>
      <input type="date" name="birthdate" value="${pet.birthdate}" />
    </div>

    <div class="form-group">
      <label>Breed</label>
      <input name="breed" value="${pet.breed}" placeholder="Enter breed" />
    </div>

    <div class="form-group">
      <label>Weight</label>
      <input name="weight" value="${pet.weight}" placeholder="e.g. 5.2kg" />
    </div>

    <div class="form-group">
      <label>Colour</label>
      <input name="colour" value="${pet.colour}" placeholder="Enter colour" />
    </div>

    <div class="form-group">
      <label>Service Preference</label>
      <select name="service_preference">
        <option value="Grooming" ${pet.service_preference === "Grooming" ? "selected" : ""}>Grooming</option>
        <option value="Boarding" ${pet.service_preference === "Boarding" ? "selected" : ""}>Boarding</option>
        <option value="Daycare" ${pet.service_preference === "Daycare" ? "selected" : ""}>Daycare</option>
      </select>
    </div>

    <div class="form-group full">
      <label>Special Care Note</label>
      <input name="special_care_note" value="${pet.special_care_note}" placeholder="e.g. Mild anxiety during grooming" />
    </div>

    <div class="form-group full">
      <label>Additional Note</label>
      <textarea name="additional_note" placeholder="Feeding instruction, room preference, grooming reminders">${pet.additional_note}</textarea>
    </div>

    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Cancel</button>
      ${isEdit ? `<button type="button" class="btn btn-secondary bk-danger-btn" onclick="removePet('${pet.pet_id}')">Remove Pet</button>` : ""}
      <button type="submit" class="save-btn">${isEdit ? "Save Pet" : "Create Pet"}</button>
    </div>
  `;

  detailForm.onsubmit = function(event) {
    event.preventDefault();

    const formData = new FormData(detailForm);
    const updatedPet = Object.fromEntries(formData.entries());

    if (isEdit) {
      const index = pets.findIndex(item => item.pet_id === petId);
      pets[index] = updatedPet;
    } else {
      pets.push(updatedPet);
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

function removeCustomer(customerId) {
  if (!confirm(`Remove customer ${customerId} and all their linked pets? This cannot be undone.`)) return;

  const linkedPetIds = pets
    .filter(p => p.customer_id === customerId)
    .map(p => p.pet_id);

  linkedPetIds.forEach(petId => {
    const idx = pets.findIndex(p => p.pet_id === petId);
    if (idx !== -1) pets.splice(idx, 1);
  });

  const customerIdx = customers.findIndex(c => c.customer_id === customerId);
  if (customerIdx !== -1) customers.splice(customerIdx, 1);

  updateKPI();
  renderLists();
  closeDetailPage();
}

function removePet(petId) {
  if (!confirm(`Remove pet ${petId}? This cannot be undone.`)) return;

  const idx = pets.findIndex(p => p.pet_id === petId);
  if (idx !== -1) pets.splice(idx, 1);

  updateKPI();
  renderLists();
  closeDetailPage();
}

function getCustomerById(customerId) {
  return customers.find(customer => customer.customer_id === customerId);
}

function getPetById(petId) {
  return pets.find(pet => pet.pet_id === petId);
}

function getPetsByCustomerId(customerId) {
  return pets.filter(pet => pet.customer_id === customerId);
}

function getBookingsByCustomerId(customerId) {
  return crmBookings.filter(booking => booking.customer_id === customerId);
}

function getLastBookingByCustomerId(customerId) {
  const customerBookings = getBookingsByCustomerId(customerId);

  if (customerBookings.length === 0) {
    return null;
  }

  return customerBookings.sort((a, b) => {
    return new Date(b.booking_date) - new Date(a.booking_date);
  })[0];
}

function generateCustomerId() {
  return `CUST-${String(customers.length + 1).padStart(4, "0")}`;
}

function generateLoyaltyId() {
  return `LOY-${String(customers.length + 1).padStart(4, "0")}`;
}

function generatePetId() {
  return `PET-${String(pets.length + 1).padStart(4, "0")}`;
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

let loyaltyMemberRequests = [
  { id: 'LOYREG001', customerName: 'Nabila Hassan', phone: '+60 12-345 9901', requestedAt: '09:10', status: 'pending' },
  { id: 'LOYREG002', customerName: 'Kelvin Ooi',     phone: '+60 12-345 9902', requestedAt: '10:35', status: 'pending' }
];

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

function buildLoyaltyMembers() {
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
  return loyaltyRequests.filter(r => r.customerName === fullName && r.status === 'approved').length;
}

const loyaltySearchInput = document.getElementById("loyaltySearchInput");
const loyaltyPendingBody = document.getElementById("loyaltyPendingBody");
const loyaltyMemberBody = document.getElementById("loyaltyMemberBody");
const loyaltyPendingRecordCount = document.getElementById("loyaltyPendingRecordCount");
const loyaltyMemberRecordCount = document.getElementById("loyaltyMemberRecordCount");

function initLoyaltyPage() {
  document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#loyaltyTabs .tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("loyaltyPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("loyaltyMembersPanel").classList.toggle("hidden", btn.dataset.panel !== "members");
    });
  });

  loyaltySearchInput.addEventListener("input", renderLoyaltyLists);

  updateLoyaltyKPI();
  renderLoyaltyLists();
}

function updateLoyaltyKPI() {
  const members = buildLoyaltyMembers();
  const pendingCount =
    loyaltyRequests.filter(r => r.status === "pending").length +
    loyaltyMemberRequests.filter(r => r.status === "pending").length;
  const pointsRedeemed = loyaltyRequests
    .filter(r => r.status === "approved")
    .reduce((sum, r) => sum + r.points, 0);

  document.getElementById("loyaltyTotalMembers").textContent = members.length;
  document.getElementById("loyaltyPendingCount").textContent = pendingCount;
  document.getElementById("loyaltyGoldCount").textContent = members.filter(m => m.tier === "Gold" || m.tier === "Platinum").length;
  document.getElementById("loyaltyPointsRedeemed").textContent = pointsRedeemed;
}

function renderLoyaltyLists() {
  const searchValue = loyaltySearchInput.value.toLowerCase().trim();
  renderLoyaltyPendingTable(searchValue);
  renderLoyaltyMemberTable(searchValue);
}

function renderLoyaltyPendingTable(searchValue) {
  const redemptions = loyaltyRequests
    .filter(r => r.status === "pending")
    .map(r => ({
      id: r.id, member: r.customerName, type: r.type, detail: `${r.points} pts`,
      requestedAt: r.requestedAt, kind: "redemption"
    }));

  const registrations = loyaltyMemberRequests
    .filter(r => r.status === "pending")
    .map(r => ({
      id: r.id, member: r.customerName, type: "New Member Registration", detail: r.phone,
      requestedAt: r.requestedAt, kind: "registration"
    }));

  const combined = [...redemptions, ...registrations]
    .filter(item => item.member.toLowerCase().includes(searchValue) || item.type.toLowerCase().includes(searchValue))
    .sort((a, b) => a.requestedAt.localeCompare(b.requestedAt));

  loyaltyPendingRecordCount.textContent = `${combined.length} pending`;

  if (combined.length === 0) {
    loyaltyPendingBody.innerHTML = `<tr><td colspan="6" class="empty-row">No pending approvals.</td></tr>`;
    return;
  }

  loyaltyPendingBody.innerHTML = combined.map(item => `
    <tr>
      <td><span class="key-chip">${item.id}</span></td>
      <td><span class="profile-name">${item.member}</span></td>
      <td>${item.type}</td>
      <td>${item.detail}</td>
      <td>${item.requestedAt}</td>
      <td>
        <button class="edit-btn" onclick="${item.kind === 'redemption' ? `approveLoyaltyRedemption('${item.id}')` : `approveLoyaltyRegistration('${item.id}')`}">Approve</button>
      </td>
    </tr>
  `).join("");
}

function renderLoyaltyMemberTable(searchValue) {
  const members = buildLoyaltyMembers().filter(m =>
    m.full_name.toLowerCase().includes(searchValue) ||
    m.phone.toLowerCase().includes(searchValue) ||
    (m.member_id || "").toLowerCase().includes(searchValue)
  );

  loyaltyMemberRecordCount.textContent = `${members.length} members`;

  if (members.length === 0) {
    loyaltyMemberBody.innerHTML = `<tr><td colspan="5" class="empty-row">No member record found.</td></tr>`;
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
        <td><span class="status-tag tier-${m.tier.toLowerCase()}">${m.tier}</span></td>
        <td>${m.points.toLocaleString()} pts</td>
        <td>${countApprovedRedemptions(m.full_name)}</td>
      </tr>
    `).join("");
}

function approveLoyaltyRedemption(id) {
  const request = loyaltyRequests.find(r => r.id === id);
  if (request) request.status = "approved";
  updateLoyaltyKPI();
  renderLoyaltyLists();
}

function approveLoyaltyRegistration(id) {
  const request = loyaltyMemberRequests.find(r => r.id === id);
  if (request) request.status = "approved";
  updateLoyaltyKPI();
  renderLoyaltyLists();
}

/* ==========================================================================
   ANALYTICAL DASHBOARD (dashboard.html) — Manager only.
   Every number and every chart/card drill-down is derived from the shared
   bookings/CRM/enquiry/loyalty data. Only the AI-performance panel and the
   Repeat Customer Rate / Customer Satisfaction KPIs have no real historical
   source in this mock system, so those stay illustrative and are not
   clickable (no fabricated drill-down for numbers that aren't real).
   ========================================================================== */

const SERVICE_MIX_COLORS = { grooming: "#3B82F6", boarding: "#10B981", daycare: "#F59E0B" };

const KPI_DEFS = [
  { key: "revenue",   icon: "💰", color: "#3B82F6", label: "Total Revenue",               deltaPct: 12.4, onClick: "openKpiDetail('revenue')" },
  { key: "bookings",  icon: "📅", color: "#059669", label: "Total Bookings",               deltaPct: 8.7,  onClick: "openKpiDetail('bookings')" },
  { key: "repeat",    icon: "👥", color: "#7C3AED", label: "Repeat Customer Rate",          deltaPct: 5.3,  onClick: "openKpiDetail('repeat')" },
  { key: "occupancy", icon: "🥧", color: "#D97706", label: "Occupancy / Slot Utilisation",  deltaPct: 6.1,  onClick: "openKpiDetail('occupancy')" }
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
  return `🗓️ ${periodRangeLabel(period, range)}`;
}

function isRoomOccupiedOnDate(roomId, date) {
  return bookings.some(b => {
    if (b.roomId !== roomId || b.status === "no_show") return false;
    if (b.checkInDate && b.checkOutDate) return date >= b.checkInDate && date <= b.checkOutDate;
    return b.date === date;
  });
}

function computeOccupancyRate(range) {
  if (!rooms.length) return 0;
  const days = getDateRange(range.start, range.end);
  if (!days.length) return 0;
  const dailyRates = days.map(d => rooms.filter(r => isRoomOccupiedOnDate(r.id, d)).length / rooms.length);
  return (dailyRates.reduce((a, b) => a + b, 0) / dailyRates.length) * 100;
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
  const periodBookings = bookings.filter(b => b.date >= range.start && b.date <= range.end);
  const totalRevenue = periodBookings.reduce((sum, b) => sum + b.amount, 0);
  const totalBookings = periodBookings.length;

  const doneCount = periodBookings.filter(b => b.status === "done").length;
  const completionRate = totalBookings ? (doneCount / totalBookings) * 100 : 0;

  const noShowCount = periodBookings.filter(b => b.status === "no_show").length;
  const noShowRate = totalBookings ? (noShowCount / totalBookings) * 100 : 0;

  const slaCompliance = computeSlaCompliance();
  const occupancyRate = computeOccupancyRate(range);
  const repeatCustomerRate = computeRepeatCustomerRate(periodBookings);

  const serviceMix = ["grooming", "boarding", "daycare"].map(type => ({
    type, count: periodBookings.filter(b => b.serviceType === type).length
  }));

  const revenueByService = {};
  periodBookings.forEach(b => { revenueByService[b.serviceId] = (revenueByService[b.serviceId] || 0) + b.amount; });
  const topServices = Object.entries(revenueByService)
    .map(([serviceId, revenue]) => ({ serviceId, service: findService(serviceId), revenue }))
    .sort((a, b) => b.revenue - a.revenue);

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
          <div class="kpi-hero-icon" style="background:${def.color};">${def.icon}</div>
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
    const rows = rooms.map(r => {
      const occupiedDays = days.filter(d => isRoomOccupiedOnDate(r.id, d)).length;
      const pct = days.length ? Math.round((occupiedDays / days.length) * 100) : 0;
      return renderDetailRow({ title: r.name, sub: `${r.type} · capacity ${r.capacity}`, tag: pct >= 50 ? "scheduled" : "done", tagLabel: `${occupiedDays}/${days.length} day(s) · ${pct}%` });
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
    const items = bookings.filter(bucket.matches);
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

function openTopServiceDetail(serviceId) {
  const items = bookings.filter(b => b.serviceId === serviceId && b.date >= dashboardRange.start && b.date <= dashboardRange.end).sort((a, b) => b.date.localeCompare(a.date));
  const service = findService(serviceId);
  const revenue = items.reduce((sum, b) => sum + b.amount, 0);
  const label = periodRangeLabel(currentDashboardPeriod, dashboardRange);
  openDetailModal(`${service?.name || "Service"} — Bookings`, `${items.length} booking(s) · RM ${revenue.toLocaleString("en-MY")} total revenue · ${label}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
}

function renderTopServices(topServices) {
  const top = topServices.slice(0, 6);
  const max = top.length ? top[0].revenue : 1;

  q("topServicesList").innerHTML = top.map(item => `
    <div class="top-service-row" onclick="openTopServiceDetail('${item.serviceId}')">
      <div class="top-service-row-head">
        <span>${item.service?.name || "Unknown Service"}</span>
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
    const pendingLoyalty = loyaltyRequests.filter(r => r.status === "pending");
    const rows = [...pendingGrooming.map(bookingDetailRow), ...pendingEnquiries.map(enquiryDetailRow), ...pendingLoyalty.map(loyaltyDetailRow)].join("");
    const total = pendingGrooming.length + pendingEnquiries.length + pendingLoyalty.length;
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
    { key: "pendingTasks", icon: "📋", color: "#3B82F6", label: "Pending Tasks", value: metrics.pendingTasks, sub: "Needs action now", tone: "neutral" },
    { key: "slaCompliance", icon: "⏱️", color: "#059669", label: "SLA Compliance", value: `${metrics.slaCompliance.rate}%`, sub: metrics.slaCompliance.breaches ? `${metrics.slaCompliance.breaches} breached now` : "All within SLA", tone: metrics.slaCompliance.rate < 80 ? "down" : "up" },
    { key: "noShow", icon: "⚠️", color: "#D97706", label: "No-Show Rate", value: `${metrics.noShowRate.toFixed(1)}%`, sub: "this period", tone: metrics.noShowRate > 8 ? "down" : "up" },
    { key: "completionRate", icon: "✅", color: "#7C3AED", label: "Completion Rate", value: `${Math.round(metrics.completionRate)}%`, sub: "this period", tone: metrics.completionRate >= 50 ? "up" : "neutral" }
  ];

  q("opsHighlightGrid").innerHTML = cards.map(c => `
    <div class="ops-highlight-card" onclick="openOpsHighlightDetail('${c.key}')">
      <div class="ops-highlight-icon" style="background:${c.color};">${c.icon}</div>
      <span class="ops-highlight-label">${c.label}</span>
      <span class="ops-highlight-value">${c.value}</span>
      <span class="ops-highlight-sub ${c.tone}">${c.sub}</span>
    </div>
  `).join("");
}

function openSnapshotDetail(key) {
  if (key === "members") {
    const rows = customers.map(c => renderDetailRow({ title: c.full_name, sub: `${c.customer_id} · ${c.phone}`, tag: "done", tagLabel: "Active" })).join("");
    openDetailModal("Active Members", `${customers.length} registered customer(s).`, rows, { label: "Open CRM", href: "profile.html" });
  } else if (key === "pets") {
    const rows = pets.map(p => renderDetailRow({ title: `${p.pet_name} (${p.species})`, sub: `Owner: ${getCustomerById(p.customer_id)?.full_name || "Unknown"}`, tag: "done", tagLabel: p.species })).join("");
    openDetailModal("Total Pets", `${pets.length} registered pet(s).`, rows, { label: "Open CRM", href: "profile.html" });
  } else if (key === "staff") {
    const rows = staff.map(s => renderDetailRow({ title: s.name, sub: s.role || "Staff", tag: "done", tagLabel: "On Duty" })).join("");
    openDetailModal("Staff On Duty", `${staff.length} staff member(s).`, rows, { label: "Open Staff Management", href: "staff.html" });
  }
}

function renderSnapshot() {
  const rows = [
    { key: "members", icon: "👥", label: "Active Members", value: customers.length.toLocaleString("en-MY") },
    { key: "pets", icon: "🐾", label: "Total Pets", value: pets.length.toLocaleString("en-MY") },
    { key: "staff", icon: "🧑‍💼", label: "Staff On Duty Today", value: staff.length },
    { key: null, icon: "🕐", label: "Operating Hours", value: "8:00 AM – 8:00 PM" }
  ];

  q("snapshotList").innerHTML = rows.map(r => `
    <div class="snapshot-row ${r.key ? "clickable" : ""}" ${r.key ? `onclick="openSnapshotDetail('${r.key}')"` : ""}>
      <span class="snapshot-row-label">${r.icon} ${r.label}</span>
      <span class="snapshot-row-value">${r.value}</span>
    </div>
  `).join("");
}

function openScheduleDetail(type) {
  const items = bookings.filter(b => b.serviceType === type && b.date >= dashboardRange.start && b.date <= dashboardRange.end);
  const label = type.charAt(0).toUpperCase() + type.slice(1);
  const rangeLabel = periodRangeLabel(currentDashboardPeriod, dashboardRange);
  openDetailModal(`${label} Bookings`, `${items.length} booking(s) · ${rangeLabel}`, items.map(bookingDetailRow).join(""), { label: "Open Booking Dashboard", href: "booking.html" });
}

function renderScheduleOverview(periodBookings) {
  const total = periodBookings.length;
  const mix = ["grooming", "boarding", "daycare"].map(type => ({
    type, count: periodBookings.filter(b => b.serviceType === type).length
  }));

  q("scheduleStackedBar").innerHTML = mix.map(m => {
    const pct = total ? (m.count / total) * 100 : 0;
    return pct > 0 ? `<div class="stacked-bar-segment" style="width:${pct}%;background:${SERVICE_MIX_COLORS[m.type]};cursor:pointer;" onclick="openScheduleDetail('${m.type}')"></div>` : "";
  }).join("");

  q("scheduleBreakdownList").innerHTML = mix.map(m => {
    const pct = total ? Math.round((m.count / total) * 100) : 0;
    const label = m.type.charAt(0).toUpperCase() + m.type.slice(1);
    return `
      <div class="schedule-breakdown-row" onclick="openScheduleDetail('${m.type}')">
        <span class="legend-swatch" style="background:${SERVICE_MIX_COLORS[m.type]};"></span>
        <span>${label}</span>
        <span class="schedule-breakdown-count">${m.count} · ${pct}%</span>
      </div>
    `;
  }).join("") + `
    <div class="schedule-breakdown-total">
      <span>Total Bookings</span>
      <span>${total} · 100%</span>
    </div>
  `;
}

function renderAnalyticsDashboard() {
  const metrics = computeDashboardMetrics(currentDashboardPeriod);

  const weekChip = q("dashboardWeekChip");
  if (weekChip) weekChip.textContent = formatPeriodChip(metrics.period, metrics.range);

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
  const scheduleOverviewSub = q("scheduleOverviewSub");
  if (scheduleOverviewSub) scheduleOverviewSub.textContent = periodLabel;

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
  renderScheduleOverview(metrics.periodBookings);
}

function initAnalyticsDashboard() {
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
  grooming: { icon: "✂️", label: "Grooming" },
  boarding: { icon: "🏨", label: "Boarding / Hotel" },
  daycare: { icon: "🌞", label: "Daycare" }
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
  if (el && input.files.length) el.textContent = "📎 " + input.files[0].name;
}

function renderServicePolicyCards(account) {
  const activeServices = account?.services || ["grooming", "boarding", "daycare"];
  const settings = loadBusinessSettings();

  const textEl = q("selectedServicesText");
  if (textEl) textEl.textContent = activeServices.map(type => `${SERVICE_META[type]?.icon || "🐾"} ${SERVICE_META[type]?.label || type}`).join(" · ");

  q("selectedServiceCards").innerHTML = activeServices.map(type => {
    const meta = SERVICE_META[type] || { icon: "🐾", label: type };
    const savedName = settings.policies?.[type];
    return `
      <div class="service-config-card">
        <h3>${meta.icon} ${meta.label}</h3>
        <p class="muted">Upload your ${meta.label.toLowerCase()} service policy document.</p>
        <label class="logo-upload-label" for="policy_${type}">
          <span class="upload-icon">📄</span>
          <p id="policyFileName_${type}">${savedName ? "📎 " + savedName : "Click to upload policy file<br>(PDF, TXT, DOCX — max 10 MB)"}</p>
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
  if (s.logoName) q("logoFileName").textContent = "📎 " + s.logoName;

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