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

let bookings = [
  {
    id: "B001",
    customerName: "Alicia Lee",
    petName: "Milo",
    serviceType: "grooming",
    serviceId: "S002",
    staffId: "ST002",
    roomId: "",
    date: getToday(),
    time: "10:00",
    duration: 150,
    status: "scheduled",
    amount: 150,
    checkInDate: "",
    checkOutDate: "",
    specialNote: "Sensitive skin. Use mild shampoo."
  },
  {
    id: "B002",
    customerName: "Jason Lim",
    petName: "Coco",
    serviceType: "boarding",
    serviceId: "S003",
    staffId: "ST003",
    roomId: "R001",
    date: getToday(),
    time: "09:00",
    duration: 1440,
    status: "pending",
    amount: 120,
    checkInDate: getToday(),
    checkOutDate: addDays(getToday(), 2),
    specialNote: "Needs evening medication."
  },
  {
    id: "B003",
    customerName: "Nur Aina",
    petName: "Luna",
    serviceType: "daycare",
    serviceId: "S006",
    staffId: "ST003",
    roomId: "R003",
    date: getToday(),
    time: "08:30",
    duration: 480,
    status: "done",
    amount: 100,
    checkInDate: "",
    checkOutDate: "",
    specialNote: "Very active. Needs more playtime."
  },
  {
    id: "B004",
    customerName: "Tan Wei",
    petName: "Buddy",
    serviceType: "grooming",
    serviceId: "S001",
    staffId: "ST002",
    roomId: "",
    date: addDays(getToday(), 1),
    time: "14:00",
    duration: 90,
    status: "no_show",
    amount: 80,
    checkInDate: "",
    checkOutDate: "",
    specialNote: ""
  }
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
  setupTabs();
  setupModalEvents();
  setupCalendarEvents();
  setupListingEvents();
  populateDropdowns();
  renderAll();
  
setTimeout(() => scrollCalendarToToday(), 100);
});

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

  tbody.innerHTML = filteredData.map(booking => {
    const service = findService(booking.serviceId);
    const staffMember = findStaff(booking.staffId);
    const room = findRoom(booking.roomId);

    return `
      <tr>
        <td>${booking.customerName}</td>
        <td>${booking.petName}</td>
        <td>${service?.name || "-"}</td>
        <td>${staffMember?.name || "-"}</td>
        <td>${booking.date}</td>
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