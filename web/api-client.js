/**
 * Drop this <script> before common.js on every page (or merge it into
 * common.js directly). It replaces the in-memory mock arrays with real
 * calls to your Express backend.
 *
 *   <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.js"></script>
 *   <script src="supabase-config.js"></script>
 *   <script src="api-client.js"></script>
 *   <script src="common.js"></script>
 *
 * Auth model: every route except /api/auth/register-company requires
 * `Authorization: Bearer <supabase access token>` (see
 * backend/src/middleware/authUser.js) — the backend resolves company_id and
 * role from that token server-side, scoped automatically. There is no
 * x-company-id header for these routes; that header is only for the
 * separate LLM/agent trust model (see backend/src/middleware/auth.js).
 */

const API_BASE_URL = "http://localhost:4000/api"; // change to your deployed backend URL

async function apiRequest(path, { method = "GET", body } = {}) {
  const token = await getSupabaseAccessToken();
  if (!token) {
    throw new Error("Not logged in — no Supabase session found.");
  }

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || `Request to ${path} failed (${res.status})`);
  }
  if (res.status === 204) return null;
  return res.json();
}

const api = {
  get: (path) => apiRequest(path),
  post: (path, body) => apiRequest(path, { method: "POST", body }),
  patch: (path, body) => apiRequest(path, { method: "PATCH", body }),
  del: (path) => apiRequest(path, { method: "DELETE" }),

  // Convenience helpers matching the endpoints you'll use most:
  listPayments: (filters = {}) => api.get(`/payments?${new URLSearchParams(filters)}`),
  getPaymentDetail: (paymentId) => api.get(`/payments/${paymentId}`),
  quoteVoucher: (paymentId, couponId) => api.post(`/payments/${paymentId}/quote-voucher`, { coupon_id: couponId }),
  verifyPayment: (paymentId, { couponId, staffId } = {}) =>
    api.post(`/payments/${paymentId}/verify`, { coupon_id: couponId, staff_id: staffId }),

  listBookings: (type, filters = {}) => api.get(`/bookings/${type}?${new URLSearchParams(filters)}`),
  getBooking: (type, id) => api.get(`/bookings/${type}/${id}`),
  createBooking: (type, data) => api.post(`/bookings/${type}`, data),
  updateBooking: (type, id, data) => api.patch(`/bookings/${type}/${id}`, data),
  deleteBooking: (type, id) => api.del(`/bookings/${type}/${id}`),

  listCoupons: () => api.get("/coupons"),
  createCoupon: (data) => api.post("/coupons", data),
  updateCoupon: (id, data) => api.patch(`/coupons/${id}`, data),
  deleteCoupon: (id) => api.del(`/coupons/${id}`),

  listMembers: (filters = {}) => api.get(`/member-info?${new URLSearchParams(filters)}`),
  listRedemptions: (filters = {}) => api.get(`/redemptions?${new URLSearchParams(filters)}`),

  listPets: (filters = {}) => api.get(`/pets?${new URLSearchParams(filters)}`),
  createPet: (data) => api.post("/pets", data),
  updatePet: (id, data) => api.patch(`/pets/${id}`, data),
  deletePet: (id) => api.del(`/pets/${id}`),

  listCustomers: (filters = {}) => api.get(`/customers?${new URLSearchParams(filters)}`),
  createCustomer: (data) => api.post("/customers", data),
  updateCustomer: (id, data) => api.patch(`/customers/${id}`, data),
  deleteCustomer: (id) => api.del(`/customers/${id}`),

  listChatMessages: (filters = {}) => api.get(`/chat-messages?${new URLSearchParams(filters)}`),

  listStaff: (filters = {}) => api.get(`/staff?${new URLSearchParams(filters)}`),
  createStaff: (data) => api.post("/staff", data),
  updateStaff: (id, data) => api.patch(`/staff/${id}`, data),
  deleteStaff: (id) => api.del(`/staff/${id}`),

  listLeaveRequests: (filters = {}) => api.get(`/leave-requests?${new URLSearchParams(filters)}`),
  createLeaveRequest: (data) => api.post("/leave-requests", data),
  decideLeaveRequest: (id, status, reviewedByStaffId) =>
    api.post(`/leave-requests/${id}/decision`, { status, reviewedByStaffId }),

  dashboardSummary: () => api.get("/dashboard/summary"),
  dashboardRevenue: (period) => api.get(`/dashboard/revenue?period=${period}`),
};
