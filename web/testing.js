const customers = [
  {
    customer_id: "CUST-0001",
    loyalty_id: "LOY-0001",
    full_name: "John Smith",
    phone: "+60 12-345 6789",
    address: "Sunway Geo Avenue",
    photo_icon: "👨",
    notes: "Prefers WhatsApp reminder. Usually books boarding + grooming."
  },
  {
    customer_id: "CUST-0002",
    loyalty_id: "LOY-0002",
    full_name: "Sarah Lee",
    phone: "+60 11-222 7788",
    address: "Subang Jaya",
    photo_icon: "👩",
    notes: "Prefers morning grooming slot."
  }
];

const pets = [
  {
    pet_id: "PET-0001",
    customer_id: "CUST-0001",
    pet_name: "Xiangwu",
    species: "Cat",
    gender: "Male",
    birthdate: "2022-05-12",
    breed: "British Shorthair",
    weight: "5.2kg",
    colour: "Grey",
    service_preference: "Boarding",
    special_care_note: "Mild anxiety during grooming",
    additional_note: "Keep in quiet room. Bring own food."
  },
  {
    pet_id: "PET-0002",
    customer_id: "CUST-0001",
    pet_name: "Mochi",
    species: "Dog",
    gender: "Female",
    birthdate: "2020-11-03",
    breed: "Poodle",
    weight: "6.8kg",
    colour: "White",
    service_preference: "Grooming",
    special_care_note: "Matting around ears",
    additional_note: "Use sensitive shampoo."
  }
];

const bookings = [
  {
    booking_id: "BOOK-0001",
    customer_id: "CUST-0001",
    pet_id: "PET-0001",
    service_type: "Boarding",
    booking_date: "2026-06-18"
  },
  {
    booking_id: "BOOK-0002",
    customer_id: "CUST-0001",
    pet_id: "PET-0002",
    service_type: "Grooming",
    booking_date: "2026-06-22"
  }
];

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
  return bookings.filter(booking => booking.customer_id === customerId);
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

initCRM();
