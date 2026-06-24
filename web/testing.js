const today = new Date().toISOString().slice(0, 10);

let currentServiceFilter = "all";

let bookings = [
  {
    id: "B001",
    time: "09:30",
    date: today,
    customerName: "Alicia Lee",
    petName: "Milo",
    serviceType: "grooming",
    requiredService: "Full Grooming",
    staffName: "Sarah Wong",
    roomName: "",
    duration: 150,
    status: "scheduled",
    amount: 150,
    specialNote: "Sensitive skin. Use mild shampoo.",
    aiSummary: "Customer requested grooming this morning. AI suggested Full Grooming."
  },
  {
    id: "B002",
    time: "10:30",
    date: today,
    customerName: "Jason Lim",
    petName: "Coco",
    serviceType: "boarding",
    requiredService: "Standard Boarding",
    staffName: "Sarah Wong",
    roomName: "Room A",
    duration: 1440,
    status: "checked_in",
    amount: 120,
    specialNote: "Needs evening medication.",
    aiSummary: "Customer confirmed two-night boarding stay."
  },
  {
    id: "B003",
    time: "12:00",
    date: today,
    customerName: "Nur Aina",
    petName: "Luna",
    serviceType: "daycare",
    requiredService: "Full-Day Daycare",
    staffName: "Sarah Wong",
    roomName: "Playroom 1",
    duration: 480,
    status: "in_progress",
    amount: 100,
    specialNote: "Very active. Needs more playtime.",
    aiSummary: "Customer asked for daycare and daily photo update."
  },
  {
    id: "B004",
    time: "15:00",
    date: today,
    customerName: "Tan Wei",
    petName: "Buddy",
    serviceType: "grooming",
    requiredService: "Basic Grooming",
    staffName: "Sarah Wong",
    roomName: "",
    duration: 90,
    status: "ready_pickup",
    amount: 80,
    specialNote: "Owner requested pickup before 5 PM.",
    aiSummary: "AI confirmed grooming booking after staff approval."
  }
];

let hitlActions = [
  {
    id: "H001",
    bookingId: "B001",
    customerName: "Alicia Lee",
    petName: "Milo",
    intent: "Grooming Booking",
    suggestion: "Full Grooming at 09:30 with Sarah Wong",
    confidence: 82,
    reason: "Customer said 'morning', exact time requires staff confirmation.",
    type: "booking"
  },
  {
    id: "H002",
    bookingId: "B002",
    customerName: "Jason Lim",
    petName: "Coco",
    intent: "Policy Exception",
    suggestion: "Accept boarding with medication note",
    confidence: 76,
    reason: "Medication handling requires staff review.",
    type: "policy"
  },
  {
    id: "H003",
    bookingId: "B003",
    customerName: "Nur Aina",
    petName: "Luna",
    intent: "Daily Update Request",
    suggestion: "Send daycare photo update by WhatsApp",
    confidence: 88,
    reason: "AI prepared update but photo is missing.",
    type: "update"
  }
];

let petUpdates = [
  {
    id: "U001",
    bookingId: "B001",
    petName: "Milo",
    customerName: "Alicia Lee",
    serviceType: "Grooming",
    missing: "After-groom photo missing",
    status: "pending"
  },
  {
    id: "U002",
    bookingId: "B003",
    petName: "Luna",
    customerName: "Nur Aina",
    serviceType: "Daycare",
    missing: "Meal and playtime update missing",
    status: "pending"
  },
  {
    id: "U003",
    bookingId: "B002",
    petName: "Coco",
    customerName: "Jason Lim",
    serviceType: "Boarding",
    missing: "Night care update not sent",
    status: "pending"
  }
];

let alerts = [
  {
    id: "A001",
    priority: "high",
    title: "Missing Vaccination Record",
    description: "Buddy does not have updated vaccination status.",
    status: "open"
  },
  {
    id: "A002",
    priority: "high",
    title: "Unpaid Payment",
    description: "Coco has pending payment review before checkout.",
    status: "open"
  },
  {
    id: "A003",
    priority: "medium",
    title: "Room Capacity Warning",
    description: "Playroom 1 is near capacity: 10/12 pets occupied.",
    status: "open"
  },
  {
    id: "A004",
    priority: "low",
    title: "AI Low Confidence",
    description: "One enquiry needs staff confirmation due to unclear customer time.",
    status: "open"
  }
];

const staffWorkload = [
  { name: "Sarah Wong", tasks: 8, max: 10 },
  { name: "Adam Tan", tasks: 6, max: 10 },
  { name: "Mei Ling", tasks: 5, max: 10 }
];

const roomCapacity = [
  { name: "Room A", used: 4, max: 5 },
  { name: "Room B", used: 2, max: 4 },
  { name: "Playroom 1", used: 10, max: 12 },
  { name: "Playroom 2", used: 6, max: 10 }
];

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("todayDate").textContent = formatDate(today);

  setupServiceTabs();
  setupBookingForm();
  renderDashboard();
});

function renderDashboard() {
  renderKpiStrip();
  renderSchedule();
  renderHitlQueue();
  renderPetUpdates();
  renderAlerts();
  renderStaffWorkload();
  renderRoomCapacity();
}

function renderKpiStrip() {
  const assignedTasks = getFilteredBookings().length;
  const pendingHitl = hitlActions.length;
  const urgentAlerts = alerts.filter(alert => alert.status === "open").length;
  const petsUnderCare = bookings.filter(b => b.status !== "completed" && b.status !== "no_show").length;
  const updatesPending = petUpdates.filter(update => update.status === "pending").length;

  const kpis = [
    { label: "My Assigned Tasks", value: assignedTasks, note: "today" },
    { label: "Pending HITL", value: pendingHitl, note: "needs review" },
    { label: "Urgent Alerts", value: urgentAlerts, note: "open" },
    { label: "Pets Under Care", value: petsUnderCare, note: "active" },
    { label: "Updates Not Sent", value: updatesPending, note: "pending" }
  ];

  document.getElementById("kpiStrip").innerHTML = kpis.map(kpi => `
    <div class="kpi-item">
      <span class="kpi-label">${kpi.label}</span>
      <span class="kpi-value">${kpi.value}</span>
      <span class="kpi-note">${kpi.note}</span>
    </div>
  `).join("");
}

function renderSchedule() {
  const list = document.getElementById("scheduleList");

  const data = getFilteredBookings().sort((a, b) => a.time.localeCompare(b.time));

  list.innerHTML = data.map(booking => `
    <div class="task-row">
      <div><strong>${booking.time}</strong></div>

      <div>
        <div class="pet-title">${booking.petName}</div>
        <div class="muted">${booking.customerName}</div>
      </div>

      <div>
        <div>${booking.requiredService}</div>
        <div class="muted">${booking.serviceType}${booking.roomName ? ` · ${booking.roomName}` : ""}</div>
      </div>

      <div>
        ${renderStatusTag(booking.status)}
      </div>

      <div class="queue-actions">
        ${renderTaskActionButtons(booking)}
      </div>
    </div>
  `).join("");
}

function renderTaskActionButtons(booking) {
  if (booking.status === "scheduled") {
    return `
      <button class="btn secondary" onclick="updateBookingStatus('${booking.id}', 'checked_in')">Check-in</button>
      <button class="btn secondary" onclick="openBookingDetails('${booking.id}')">View</button>
    `;
  }

  if (booking.status === "checked_in") {
    return `
      <button class="btn secondary" onclick="updateBookingStatus('${booking.id}', 'in_progress')">Start</button>
      <button class="btn secondary" onclick="openBookingDetails('${booking.id}')">View</button>
    `;
  }

  if (booking.status === "in_progress") {
    return `
      <button class="btn secondary" onclick="updateBookingStatus('${booking.id}', 'ready_pickup')">Ready</button>
      <button class="btn secondary" onclick="openBookingDetails('${booking.id}')">View</button>
    `;
  }

  if (booking.status === "ready_pickup") {
    return `
      <button class="btn primary" onclick="updateBookingStatus('${booking.id}', 'completed')">Done</button>
      <button class="btn secondary" onclick="openBookingDetails('${booking.id}')">View</button>
    `;
  }

  return `
    <button class="btn secondary" onclick="openBookingDetails('${booking.id}')">View</button>
  `;
}

function renderHitlQueue() {
  document.getElementById("hitlCount").textContent = `${hitlActions.length} pending`;

  const queue = document.getElementById("hitlQueue");

  if (hitlActions.length === 0) {
    queue.innerHTML = `<div class="queue-item"><div class="queue-desc">No pending HITL actions.</div></div>`;
    return;
  }

  queue.innerHTML = hitlActions.map(item => `
    <div class="queue-item">
      <div class="queue-top">
        <div>
          <div class="queue-title">${item.customerName} · ${item.petName}</div>
          <div class="queue-desc">Intent: ${item.intent}</div>
        </div>
        <span class="status-tag status-in_progress">${item.confidence}%</span>
      </div>

      <div class="queue-desc">
        <strong>AI Suggestion:</strong> ${item.suggestion}<br>
        <strong>Reason:</strong> ${item.reason}
      </div>

      <div class="queue-actions">
        <button class="btn primary" onclick="approveHitl('${item.id}')">Approve</button>
        <button class="btn secondary" onclick="editHitl('${item.id}')">Edit</button>
        <button class="btn danger" onclick="rejectHitl('${item.id}')">Reject</button>
      </div>
    </div>
  `).join("");
}

function renderPetUpdates() {
  const queue = document.getElementById("petUpdateQueue");

  const pending = petUpdates.filter(update => update.status === "pending");

  if (pending.length === 0) {
    queue.innerHTML = `<div class="queue-item"><div class="queue-desc">All pet updates are completed.</div></div>`;
    return;
  }

  queue.innerHTML = pending.map(update => `
    <div class="queue-item">
      <div class="queue-top">
        <div>
          <div class="queue-title">${update.petName} · ${update.serviceType}</div>
          <div class="queue-desc">${update.customerName}</div>
        </div>
        <span class="status-tag status-in_progress">Pending</span>
      </div>

      <div class="queue-desc">${update.missing}</div>

      <div class="queue-actions">
        <button class="btn secondary" onclick="addPetUpdate('${update.id}')">Add Update</button>
        <button class="btn primary" onclick="sendPetUpdate('${update.id}')">Send WhatsApp</button>
      </div>
    </div>
  `).join("");
}

function renderAlerts() {
  const list = document.getElementById("alertList");

  const openAlerts = alerts.filter(alert => alert.status === "open");

  if (openAlerts.length === 0) {
    list.innerHTML = `<div class="queue-item"><div class="queue-desc">No urgent alerts.</div></div>`;
    return;
  }

  list.innerHTML = openAlerts.map(alert => `
    <div class="queue-item">
      <div class="queue-top">
        <div class="queue-title">${alert.title}</div>
        <span class="status-tag priority-${alert.priority}">${alert.priority}</span>
      </div>

      <div class="queue-desc">${alert.description}</div>

      <div class="queue-actions">
        <button class="btn secondary" onclick="resolveAlert('${alert.id}')">Mark Resolved</button>
      </div>
    </div>
  `).join("");
}

function renderStaffWorkload() {
  document.getElementById("staffWorkload").innerHTML = staffWorkload.map(item => {
    const percent = Math.round((item.tasks / item.max) * 100);

    return `
      <div class="progress-row">
        <div class="progress-info">
          <span>${item.name}</span>
          <span>${item.tasks}/${item.max}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${getProgressClass(percent)}" style="width:${percent}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderRoomCapacity() {
  document.getElementById("roomCapacity").innerHTML = roomCapacity.map(item => {
    const percent = Math.round((item.used / item.max) * 100);

    return `
      <div class="progress-row">
        <div class="progress-info">
          <span>${item.name}</span>
          <span>${item.used}/${item.max}</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill ${getProgressClass(percent)}" style="width:${percent}%"></div>
        </div>
      </div>
    `;
  }).join("");
}

function setupServiceTabs() {
  document.querySelectorAll("#serviceTabs .tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll("#serviceTabs .tab").forEach(btn => btn.classList.remove("active"));
      tab.classList.add("active");

      currentServiceFilter = tab.dataset.filter;
      renderDashboard();
    });
  });
}

function setupBookingForm() {
  document.getElementById("bookingForm").addEventListener("submit", event => {
    event.preventDefault();
    saveBookingDetails();
  });
}

function openBookingDetails(bookingId) {
  const booking = bookings.find(item => item.id === bookingId);
  if (!booking) return;

  document.getElementById("bookingId").value = booking.id;
  document.getElementById("customerName").value = booking.customerName;
  document.getElementById("petName").value = booking.petName;
  document.getElementById("serviceType").value = booking.serviceType;
  document.getElementById("requiredService").value = booking.requiredService;
  document.getElementById("staffName").value = booking.staffName;
  document.getElementById("roomName").value = booking.roomName;
  document.getElementById("bookingDate").value = booking.date;
  document.getElementById("bookingTime").value = booking.time;
  document.getElementById("duration").value = booking.duration;
  document.getElementById("bookingStatus").value = booking.status;
  document.getElementById("amount").value = booking.amount;
  document.getElementById("specialNote").value = booking.specialNote;
  document.getElementById("aiSummary").value = booking.aiSummary;

  document.getElementById("bookingModal").style.display = "flex";
}

function closeBookingModal() {
  document.getElementById("bookingModal").style.display = "none";
}

function saveBookingDetails() {
  const bookingId = document.getElementById("bookingId").value;
  const booking = bookings.find(item => item.id === bookingId);

  if (!booking) return;

  booking.customerName = document.getElementById("customerName").value;
  booking.petName = document.getElementById("petName").value;
  booking.serviceType = document.getElementById("serviceType").value;
  booking.requiredService = document.getElementById("requiredService").value;
  booking.staffName = document.getElementById("staffName").value;
  booking.roomName = document.getElementById("roomName").value;
  booking.date = document.getElementById("bookingDate").value;
  booking.time = document.getElementById("bookingTime").value;
  booking.duration = Number(document.getElementById("duration").value);
  booking.status = document.getElementById("bookingStatus").value;
  booking.amount = Number(document.getElementById("amount").value);
  booking.specialNote = document.getElementById("specialNote").value;
  booking.aiSummary = document.getElementById("aiSummary").value;

  closeBookingModal();
  renderDashboard();
}

function updateBookingStatus(bookingId, newStatus) {
  const booking = bookings.find(item => item.id === bookingId);
  if (!booking) return;

  booking.status = newStatus;
  renderDashboard();
}

function markBookingCompleted() {
  const bookingId = document.getElementById("bookingId").value;
  updateBookingStatus(bookingId, "completed");
  closeBookingModal();
}

function markBookingNoShow() {
  const bookingId = document.getElementById("bookingId").value;
  updateBookingStatus(bookingId, "no_show");
  closeBookingModal();
}

function approveHitl(hitlId) {
  hitlActions = hitlActions.filter(item => item.id !== hitlId);
  renderDashboard();
}

function rejectHitl(hitlId) {
  hitlActions = hitlActions.filter(item => item.id !== hitlId);
  renderDashboard();
}

function editHitl(hitlId) {
  const hitl = hitlActions.find(item => item.id === hitlId);
  if (!hitl) return;

  openBookingDetails(hitl.bookingId);
}

function addPetUpdate(updateId) {
  alert("Open pet update form for update ID: " + updateId);
}

function sendPetUpdate(updateId) {
  const update = petUpdates.find(item => item.id === updateId);
  if (!update) return;

  update.status = "sent";
  renderDashboard();
}

function sendAllPendingUpdates() {
  petUpdates.forEach(update => {
    if (update.status === "pending") update.status = "sent";
  });

  renderDashboard();
}

function resolveAlert(alertId) {
  const alert = alerts.find(item => item.id === alertId);
  if (!alert) return;

  alert.status = "resolved";
  renderDashboard();
}

function openNewBooking() {
  alert("Open new booking form or redirect to booking dashboard.");
}

function openPetProfile() {
  const petName = document.getElementById("petName").value;
  alert("Open pet profile: " + petName);
}

function refreshDashboard() {
  renderDashboard();
}

function getFilteredBookings() {
  if (currentServiceFilter === "all") return bookings;
  return bookings.filter(booking => booking.serviceType === currentServiceFilter);
}

function renderStatusTag(status) {
  return `<span class="status-tag status-${status}">${formatStatus(status)}</span>`;
}

function formatStatus(status) {
  const map = {
    scheduled: "Scheduled",
    checked_in: "Checked-in",
    in_progress: "In Progress",
    ready_pickup: "Ready Pickup",
    completed: "Completed",
    no_show: "No Show"
  };

  return map[status] || status;
}

function getProgressClass(percent) {
  if (percent >= 90) return "danger";
  if (percent >= 75) return "warning";
  return "";
}

function formatDate(dateString) {
  const date = new Date(dateString);

  return date.toLocaleDateString("en-MY", {
    weekday: "long",
    day: "2-digit",
    month: "long",
    year: "numeric"
  });
}