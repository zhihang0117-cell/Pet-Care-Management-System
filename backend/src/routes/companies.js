import { Router } from "express";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { requireManager } from "../middleware/authUser.js";

export const companiesRouter = Router();

const BUSINESS_ASSET_BUCKET = "business-assets";
const MAX_LOGO_BYTES = 2 * 1024 * 1024;

function validateLogoBytes(buffer, contentType) {
  const png = buffer.length >= 8
    && buffer.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
  const jpeg = buffer.length >= 3
    && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff;
  return (contentType === "image/png" && png) || (contentType === "image/jpeg" && jpeg);
}

async function ensureBusinessAssetBucket() {
  const { data, error } = await supabase.storage.getBucket(BUSINESS_ASSET_BUCKET);
  if (data) return;
  if (error && !/not found/i.test(error.message || "")) throw error;

  const { error: createError } = await supabase.storage.createBucket(BUSINESS_ASSET_BUCKET, {
    public: true,
    fileSizeLimit: MAX_LOGO_BYTES,
    allowedMimeTypes: ["image/png", "image/jpeg"],
  });
  if (createError && !/already exists/i.test(createError.message || "")) throw createError;
}

// GET /api/companies/me — full profile (including settings_json) for
// setting.html to prefill its form. accounts.js's /me only returns a small
// subset for the sidebar; this is the complete row.
companiesRouter.get(
  "/me",
  asyncHandler(async (req, res) => {
    const { data, error } = await supabase
      .from("companies")
      .select("*")
      .eq("company_id", req.companyId)
      .single();
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);

// POST /api/companies/me/logo (manager only)
// Stores the JPG/PNG object in Supabase Storage and persists only its public
// URL in companies.logo_path. Keeping binary data out of Postgres makes the
// company row small and lets browsers cache the logo normally.
companiesRouter.post(
  "/me/logo",
  requireManager,
  asyncHandler(async (req, res) => {
    const { content_type: contentType, data_base64: dataBase64 } = req.body || {};
    if (!["image/png", "image/jpeg"].includes(contentType)) {
      return res.status(400).json({ error: "Business logo must be a PNG or JPG file." });
    }
    if (!dataBase64 || typeof dataBase64 !== "string") {
      return res.status(400).json({ error: "Business logo file data is required." });
    }

    const buffer = Buffer.from(dataBase64, "base64");
    if (!buffer.length || buffer.length > MAX_LOGO_BYTES) {
      return res.status(400).json({ error: "Business logo must be 2 MB or smaller." });
    }
    if (!validateLogoBytes(buffer, contentType)) {
      return res.status(400).json({ error: "The selected file is not a valid PNG or JPG image." });
    }

    await ensureBusinessAssetBucket();
    const extension = contentType === "image/png" ? "png" : "jpg";
    const objectPath = `${req.companyId}/logo-${Date.now()}.${extension}`;
    const { error: uploadError } = await supabase.storage
      .from(BUSINESS_ASSET_BUCKET)
      .upload(objectPath, buffer, { contentType, upsert: false, cacheControl: "3600" });
    if (uploadError) return res.status(400).json({ error: uploadError.message });

    const { data: publicData } = supabase.storage.from(BUSINESS_ASSET_BUCKET).getPublicUrl(objectPath);
    const logoUrl = publicData.publicUrl;
    const { data: current } = await supabase
      .from("companies")
      .select("logo_path")
      .eq("company_id", req.companyId)
      .single();
    const { data: company, error: updateError } = await supabase
      .from("companies")
      .update({ logo_path: logoUrl })
      .eq("company_id", req.companyId)
      .select()
      .single();

    if (updateError) {
      await supabase.storage.from(BUSINESS_ASSET_BUCKET).remove([objectPath]);
      return res.status(400).json({ error: updateError.message });
    }

    const oldMarker = `/storage/v1/object/public/${BUSINESS_ASSET_BUCKET}/`;
    if (current?.logo_path?.includes(oldMarker)) {
      const oldPath = decodeURIComponent(current.logo_path.split(oldMarker)[1] || "");
      if (oldPath && oldPath !== objectPath) {
        await supabase.storage.from(BUSINESS_ASSET_BUCKET).remove([oldPath]);
      }
    }

    res.json(company);
  })
);

const PROFILE_COLUMNS = [
  "company_name",
  "country",
  "street_address",
  "city",
  "state",
  "postcode",
  "business_description",
];

/**
 * PATCH /api/companies/me   (manager only)
 * Body may include any of PROFILE_COLUMNS (updated directly) plus a
 * `settings` object, which is shallow-merged into the existing
 * `settings_json` — so callers only send the keys they're changing
 * (business hours, payment methods, loyalty earn rate, etc.), not the
 * whole settings blob every time.
 */
companiesRouter.patch(
  "/me",
  requireManager,
  asyncHandler(async (req, res) => {
    const payload = {};
    for (const column of PROFILE_COLUMNS) {
      if (req.body[column] !== undefined) payload[column] = req.body[column];
    }

    if (req.body.settings !== undefined) {
      if (!req.body.settings || typeof req.body.settings !== "object" || Array.isArray(req.body.settings)) {
        return res.status(400).json({ error: "settings must be an object." });
      }
      if (req.body.settings.loyalty_earn_rate !== undefined) {
        const earnRate = Number(req.body.settings.loyalty_earn_rate);
        if (!Number.isFinite(earnRate) || earnRate < 0) {
          return res.status(400).json({ error: "loyalty_earn_rate must be a non-negative number." });
        }
        req.body.settings.loyalty_earn_rate = earnRate;
      }
      const { data: current, error: fetchError } = await supabase
        .from("companies")
        .select("settings_json")
        .eq("company_id", req.companyId)
        .single();
      if (fetchError) return res.status(400).json({ error: fetchError.message });

      payload.settings_json = { ...(current.settings_json || {}), ...req.body.settings };
    }

    if (Object.keys(payload).length === 0) {
      return res.status(400).json({ error: "Nothing to update." });
    }

    const { data, error } = await supabase
      .from("companies")
      .update(payload)
      .eq("company_id", req.companyId)
      .select()
      .single();
    if (error) return res.status(400).json({ error: error.message });
    res.json(data);
  })
);
