export const BUSINESS_DAY_KEYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function parseClock(value) {
  const match = String(value).trim().match(/^(\d{1,2}):([0-5]\d)(?:\s*([AP]M))?$/i);
  if (!match) return null;
  let hour = Number(match[1]);
  const minute = match[2];
  const meridiem = match[3]?.toUpperCase();
  if (meridiem) {
    if (hour < 1 || hour > 12) return null;
    if (meridiem === "AM") hour = hour === 12 ? 0 : hour;
    if (meridiem === "PM") hour = hour === 12 ? 12 : hour + 12;
  } else if (hour > 23) {
    return null;
  }
  return `${String(hour).padStart(2, "0")}:${minute}`;
}

function normalizeDate(value) {
  const iso = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const local = value.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  const parts = iso
    ? { year: iso[1], month: iso[2], day: iso[3] }
    : local
      ? { year: local[3], month: local[2], day: local[1] }
      : null;
  if (!parts) return null;
  const candidate = `${parts.year}-${parts.month}-${parts.day}`;
  const date = new Date(`${candidate}T00:00:00Z`);
  return date.getUTCFullYear() === Number(parts.year)
    && date.getUTCMonth() + 1 === Number(parts.month)
    && date.getUTCDate() === Number(parts.day)
    ? candidate
    : null;
}

export function normalizeAvailabilitySettings(settings) {
  const businessHours = settings.business_hours;
  if (!businessHours || typeof businessHours !== "object" || Array.isArray(businessHours)) {
    throw new Error("business_hours must contain the weekly schedule.");
  }

  const normalizedHours = [];
  for (const [dayOfWeek, day] of BUSINESS_DAY_KEYS.entries()) {
    const value = String(businessHours[day] || "").trim();
    if (!value) continue;
    if (/^closed$/i.test(value)) {
      normalizedHours.push({ day_of_week: dayOfWeek, open_time: null, close_time: null, is_closed: true });
      continue;
    }
    const range = value.split(/\s*(?:-|–|—)\s*/);
    const openTime = range.length === 2 ? parseClock(range[0]) : null;
    const closeTime = range.length === 2 ? parseClock(range[1]) : null;
    if (!openTime || !closeTime) {
      throw new Error(`${day} business hours must use HH:MM - HH:MM, 09:30 AM – 06:30 PM, or Closed.`);
    }
    if (closeTime <= openTime) throw new Error(`${day} closing time must be later than opening time.`);
    normalizedHours.push({ day_of_week: dayOfWeek, open_time: openTime, close_time: closeTime, is_closed: false });
  }

  const normalizedDates = String(settings.closed_dates || "")
    .split(/\n|,/)
    .map(value => value.trim())
    .filter(Boolean)
    .map(entry => {
      const match = entry.match(/^(\d{4}-\d{2}-\d{2}|\d{2}\/\d{2}\/\d{4})(?:\s*(?:-|–|—)\s*(.+))?$/);
      const closedDate = match ? normalizeDate(match[1]) : null;
      if (!closedDate) throw new Error(`Invalid closed date: ${entry}. Use YYYY-MM-DD or DD/MM/YYYY.`);
      return { closed_date: closedDate, reason: match[2]?.trim() || null };
    });

  return {
    businessHours: normalizedHours,
    closedDates: [...new Map(normalizedDates.map(item => [item.closed_date, item])).values()],
  };
}

export function availabilityAsSettings(businessHours, closedDates) {
  const weekly = {};
  for (const row of businessHours || []) {
    const day = BUSINESS_DAY_KEYS[Number(row.day_of_week)];
    if (!day) continue;
    weekly[day] = row.is_closed
      ? "Closed"
      : `${String(row.open_time).slice(0, 5)} - ${String(row.close_time).slice(0, 5)}`;
  }
  return {
    business_hours: weekly,
    closed_dates: (closedDates || []).map(row => (
      `${row.closed_date}${row.reason ? ` – ${row.reason}` : ""}`
    )).join("\n"),
  };
}
