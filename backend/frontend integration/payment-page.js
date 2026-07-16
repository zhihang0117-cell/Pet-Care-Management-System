/**
 * Drop-in replacement for the payment.html-related functions in common.js:
 * initPaymentPage, renderPaymentLists, renderPaymentPendingTable,
 * renderPaymentHistoryTable, openPaymentDetail, verifyPayment.
 *
 * Load order on payment.html:
 *   <script src="api-client.js"></script>
 *   <script src="payment-page.js"></script>
 *   <script src="common.js"></script>   (keep for anything NOT redefined here)
 *
 * NOTE ON THE CUSTOMER-NAME JOIN: payment_history doesn't itself store a
 * customer_id — you get there via payment -> booking -> pet -> customer.
 * The backend's GET /api/payments/:id already does that join for you (one
 * call per payment). For a pending-verification queue that's normally small
 * (a handful of payments waiting on staff), fetching detail per row is fine.
 * If your pending list grows large, consider adding a `customer_id` column
 * directly to payment_history (denormalized) so the list endpoint alone is
 * enough — ask me and I can wire that up too.
 */

let _couponsCache = null;
async function getCouponsCached() {
  if (!_couponsCache) _couponsCache = await api.listCoupons();
  return _couponsCache;
}

async function initPaymentPage() {
  document.querySelectorAll("#paymentTabs .tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#paymentTabs .tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("paymentPendingPanel").classList.toggle("hidden", btn.dataset.panel !== "pending");
      document.getElementById("paymentHistoryPanel").classList.toggle("hidden", btn.dataset.panel !== "history");
    });
  });

  const searchInput = document.getElementById("paymentSearchInput");
  if (searchInput) searchInput.addEventListener("input", renderPaymentLists);

  await renderPaymentLists();
}

async function renderPaymentLists() {
  const searchValue = (document.getElementById("paymentSearchInput")?.value || "").toLowerCase().trim();

  const [pending, paid] = await Promise.all([
    api.listPayments({ status: "Pending" }),
    api.listPayments({ status: "Paid" }),
  ]);

  await renderPaymentPendingTable(pending, searchValue);
  renderPaymentHistoryTable(paid, searchValue);
  updatePaymentKPI(pending, paid);
}

function updatePaymentKPI(pending, paid) {
  const totalRevenue = paid.reduce((sum, p) => sum + Number(p.final_amount || 0), 0);
  document.getElementById("paymentTotalRevenue").textContent = `RM ${totalRevenue.toLocaleString()}`;
  document.getElementById("paymentPendingCount").textContent = pending.length;
  document.getElementById("paymentTotalCount").textContent = pending.length + paid.length;
  // Loyalty discount total requires the redemption ledger, which isn't on
  // payment_history directly — see /api/redemptions if you want to sum it.
}

async function renderPaymentPendingTable(pending, searchValue) {
  const tbody = document.getElementById("paymentPendingBody");
  document.getElementById("paymentPendingRecordCount").textContent = `${pending.length} pending`;

  if (pending.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-row">No payments awaiting verification.</td></tr>`;
    return;
  }

  // Fetch the customer name for each pending payment (see note at top of file).
  const rows = await Promise.all(
    pending.map(async (p) => {
      const detail = await api.getPaymentDetail(p.payment_id);
      return { payment: p, detail };
    })
  );

  const filtered = rows.filter(({ payment, detail }) => {
    const haystack = `${payment.service} ${detail.member?.customer_id || ""}`.toLowerCase();
    return haystack.includes(searchValue);
  });

  tbody.innerHTML = filtered
    .map(({ payment, detail }) => {
      return `
        <tr>
          <td>PAY-${String(payment.payment_id).padStart(4, "0")}</td>
          <td>Customer #${detail.member?.customer_id ?? "-"}</td>
          <td>${payment.service}</td>
          <td>RM ${payment.final_amount}</td>
          <td>${payment.payment_method || "-"}</td>
          <td>${payment.date}</td>
          <td><button class="edit-btn" onclick="openVerifyPaymentModal(${payment.payment_id})">Review</button></td>
        </tr>
      `;
    })
    .join("");
}

function renderPaymentHistoryTable(paid, searchValue) {
  const tbody = document.getElementById("paymentHistoryBody");
  document.getElementById("paymentHistoryRecordCount").textContent = `${paid.length} records`;

  const filtered = paid.filter((p) => p.service.toLowerCase().includes(searchValue));

  tbody.innerHTML = filtered
    .map(
      (p) => `
        <tr>
          <td>PAY-${String(p.payment_id).padStart(4, "0")}</td>
          <td>-</td>
          <td>${p.service}</td>
          <td>RM ${p.base_price}</td>
          <td>${p.add_ons || "-"}</td>
          <td>-</td>
          <td>RM ${p.final_amount}</td>
          <td>-</td>
          <td>-</td>
          <td>${p.payment_method || "-"}</td>
          <td><span class="status-tag status-done">${p.status}</span></td>
          <td>${p.paid_at ? new Date(p.paid_at).toLocaleString() : p.date}</td>
          <td>-</td>
        </tr>
      `
    )
    .join("");
}

/**
 * Opens the review modal for one payment: shows booking + member info,
 * lets staff pick a voucher (with a live quote), and click Verify.
 * Reuses the existing #detailPage / #detailForm modal from common.js.
 */
async function openVerifyPaymentModal(paymentId) {
  const detail = await api.getPaymentDetail(paymentId);
  const coupons = await getCouponsCached();

  const detailPage = document.getElementById("detailPage");
  const detailTitle = document.getElementById("detailTitle");
  const detailForm = document.getElementById("detailForm");

  detailPage.style.display = "flex";
  detailTitle.textContent = `Verify Payment · PAY-${String(paymentId).padStart(4, "0")}`;

  const memberLine = detail.member
    ? `${detail.member.tier} member · ${detail.member.points_balance} points`
    : "No loyalty member on file";

  detailForm.innerHTML = `
    <div class="form-group full">
      <label>Service</label>
      <input value="${detail.payment.service}" readonly />
    </div>
    <div class="form-group">
      <label>Base + Add-on</label>
      <input value="RM ${detail.payment.base_price}${detail.payment.add_ons ? " + " + detail.payment.add_ons : ""}" readonly />
    </div>
    <div class="form-group">
      <label>Current Total</label>
      <input id="verifyCurrentTotal" value="RM ${detail.payment.final_amount}" readonly />
    </div>
    <div class="form-group full">
      <label>Loyalty Member</label>
      <input value="${memberLine}" readonly />
    </div>
    <div class="form-group full">
      <label>Apply Voucher (optional)</label>
      <select id="verifyCouponSelect">
        <option value="">No voucher</option>
        ${coupons
          .map((c) => `<option value="${c.coupon_id}">${c.reward_name} (${c.points_required} pts)</option>`)
          .join("")}
      </select>
    </div>
    <div class="form-group full">
      <p id="verifyVoucherNote" style="font-size:0.8rem;color:var(--text-muted);"></p>
    </div>
    <div class="form-actions">
      <button type="button" class="cancel-btn" onclick="closeDetailPage()">Cancel</button>
      <button type="button" class="save-btn" id="verifyConfirmBtn">Verify Payment &amp; Redemption</button>
    </div>
  `;

  const couponSelect = document.getElementById("verifyCouponSelect");
  const note = document.getElementById("verifyVoucherNote");

  couponSelect.addEventListener("change", async () => {
    if (!couponSelect.value) {
      note.textContent = "";
      return;
    }
    try {
      const quote = await api.quoteVoucher(paymentId, Number(couponSelect.value));
      note.style.color = "";
      note.textContent = `New total: RM ${quote.finalAmount} (discount RM ${quote.discountApplied})`;
    } catch (err) {
      note.style.color = "#DC2626";
      note.textContent = err.message;
    }
  });

  document.getElementById("verifyConfirmBtn").addEventListener("click", async () => {
    try {
      // TODO: replace with the actually-logged-in staff_id once real auth is wired up.
      const staffId = window.CURRENT_STAFF_ID || null;
      await api.verifyPayment(paymentId, {
        couponId: couponSelect.value ? Number(couponSelect.value) : undefined,
        staffId,
      });
      closeDetailPage();
      await renderPaymentLists();
      if (typeof refreshCurrentDashboardView === "function") refreshCurrentDashboardView();
    } catch (err) {
      note.style.color = "#DC2626";
      note.textContent = err.message;
    }
  });
}
