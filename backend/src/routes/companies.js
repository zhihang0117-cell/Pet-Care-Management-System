import { Router } from "express";
import { randomUUID } from "node:crypto";
import mammoth from "mammoth";
import { supabase } from "../supabaseClient.js";
import { asyncHandler } from "../middleware/auth.js";
import { requireManager } from "../middleware/authUser.js";
import {
  availabilityAsSettings,
  normalizeAvailabilitySettings,
} from "../lib/availabilitySettings.js";
import { callAiBackend } from "../lib/aiBackend.js";

export const companiesRouter = Router();

const BUSINESS_ASSET_BUCKET = "business-assets";
const COMPANY_DOCUMENT_BUCKET = "business-documents";
const MAX_LOGO_BYTES = 2 * 1024 * 1024;
const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
const DOCUMENT_TYPES = new Set(["policies", "service_information", "business_flow_booking", "veterinary"]);
const SERVICE_TYPES = new Set(["grooming", "boarding", "daycare", "general"]);
const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
function isMissingCompanyDocumentsTable(error) {
  const message = String(error?.message || error || "");
  return error?.code === "PGRST205"
    || /company_documents/i.test(message) && /schema cache|could not find the table/i.test(message);
}

function sendDocumentDatabaseError(res, error) {
  if (isMissingCompanyDocumentsTable(error)) {
    return res.status(503).json({
      code: "COMPANY_DOCUMENTS_MIGRATION_REQUIRED",
      error: "Policy document storage is not configured yet. Apply backend/sql/company_documents_migration.sql and the BGE-Large migration in Supabase, then reload the schema cache.",
    });
  }
  return res.status(400).json({ error: error?.message || "Document database request failed." });
}

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

async function ensureCompanyDocumentBucket() {
  const { data, error } = await supabase.storage.getBucket(COMPANY_DOCUMENT_BUCKET);
  if (data) return;
  if (error && !/not found/i.test(error.message || "")) throw error;
  const { error: createError } = await supabase.storage.createBucket(COMPANY_DOCUMENT_BUCKET, {
    public: false,
    fileSizeLimit: MAX_DOCUMENT_BYTES,
    allowedMimeTypes: [DOCX_MIME],
  });
  if (createError && !/already exists/i.test(createError.message || "")) throw createError;
}

function validateDocumentUpload(fileName, contentType, dataBase64) {
  const normalizedName = String(fileName || "").toLowerCase();
  const isDocx = normalizedName.endsWith(".docx") && contentType === DOCX_MIME;
  if (!isDocx || !dataBase64) {
    return { error: "A valid DOCX file is required." };
  }
  const buffer = Buffer.from(dataBase64, "base64");
  const validSignature = buffer.subarray(0, 2).toString() === "PK";
  if (!buffer.length || buffer.length > MAX_DOCUMENT_BYTES || !validSignature) {
    return { error: "The document is invalid or larger than 10 MB." };
  }
  return { buffer };
}

function safeStorageName(name) {
  return String(name || "policy.docx").replace(/[^a-zA-Z0-9._-]+/g, "-").slice(-120);
}


// GET /api/companies/me — full profile (including settings_json) for
// setting.html to prefill its form. accounts.js's /me only returns a small
// subset for the sidebar; this is the complete row.
companiesRouter.get(
  "/me",
  asyncHandler(async (req, res) => {
    const [companyResult, hoursResult, datesResult] = await Promise.all([
      supabase.from("companies").select("*").eq("company_id", req.companyId).single(),
      supabase.from("company_business_hours").select("day_of_week,open_time,close_time,is_closed")
        .eq("company_id", req.companyId).order("day_of_week"),
      supabase.from("company_closed_dates").select("closed_date,reason")
        .eq("company_id", req.companyId).order("closed_date"),
    ]);
    const { data, error } = companyResult;
    if (error) return res.status(400).json({ error: error.message });
    if (hoursResult.error || datesResult.error) {
      return res.status(503).json({
        error: "Availability tables are not configured. Apply backend/sql/company_availability_settings_migration.sql.",
      });
    }
    const availability = availabilityAsSettings(hoursResult.data, datesResult.data);
    res.json({
      ...data,
      settings_json: { ...(data.settings_json || {}), ...availability },
    });
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

// Company documents are private Storage objects. company_id always comes
// from the verified login, never from the request body.
companiesRouter.get(
  "/me/documents",
  asyncHandler(async (req, res) => {
    const { data, error } = await supabase
      .from("company_documents")
      .select("document_id, service_type, document_type, file_name, file_size, status, chunks_indexed, error_message, created_at, indexed_at")
      .eq("company_id", req.companyId)
      .order("created_at", { ascending: false });
    if (error) return sendDocumentDatabaseError(res, error);
    res.json(data || []);
  })
);

companiesRouter.get(
  "/me/documents/:documentId/download",
  asyncHandler(async (req, res) => {
    const { data: document, error } = await supabase.from("company_documents")
      .select("document_id, file_name, mime_type, storage_bucket, storage_path")
      .eq("company_id", req.companyId).eq("document_id", req.params.documentId).single();
    if (error || !document) return res.status(404).json({ error: "Document not found." });

    const { data, error: downloadError } = await supabase.storage
      .from(document.storage_bucket).download(document.storage_path);
    if (downloadError) return res.status(400).json({ error: downloadError.message });

    const bytes = Buffer.from(await data.arrayBuffer());
    const originalName = String(document.file_name || "document").replace(/[\r\n]/g, "_");
    const fallbackName = originalName.replace(/[^\x20-\x7E]/g, "_").replace(/"/g, "_");
    const encodedName = encodeURIComponent(originalName).replace(/['()*]/g, character =>
      `%${character.charCodeAt(0).toString(16).toUpperCase()}`
    );
    res.setHeader("Content-Type", document.mime_type || "application/octet-stream");
    res.setHeader("Content-Disposition", `attachment; filename="${fallbackName}"; filename*=UTF-8''${encodedName}`);
    res.send(bytes);
  })
);

companiesRouter.get(
  "/me/documents/:documentId/preview",
  asyncHandler(async (req, res) => {
    const { data: document, error } = await supabase.from("company_documents")
      .select("document_id, file_name, storage_bucket, storage_path")
      .eq("company_id", req.companyId).eq("document_id", req.params.documentId).single();
    if (error || !document) return res.status(404).json({ error: "Document not found." });

    const { data, error: downloadError } = await supabase.storage
      .from(document.storage_bucket).download(document.storage_path);
    if (downloadError) return res.status(400).json({ error: downloadError.message });

    try {
      const buffer = Buffer.from(await data.arrayBuffer());
      const result = await mammoth.extractRawText({ buffer });
      res.json({ file_name: document.file_name, text: result.value.trim() });
    } catch (_error) {
      res.status(422).json({ error: "This DOCX document could not be converted into a preview." });
    }
  })
);

companiesRouter.post(
  "/me/documents",
  requireManager,
  asyncHandler(async (req, res) => {
    const { file_name: fileName, content_type: contentType, data_base64: dataBase64 } = req.body || {};
    const serviceType = String(req.body?.service_type || "general").toLowerCase();
    const documentType = String(req.body?.document_type || "policies").toLowerCase();
    if (!SERVICE_TYPES.has(serviceType) || !DOCUMENT_TYPES.has(documentType)) {
      return res.status(400).json({ error: "Invalid service_type or document_type." });
    }
    const validation = validateDocumentUpload(fileName, contentType, dataBase64);
    if (validation.error) return res.status(400).json({ error: validation.error });
    const { buffer } = validation;

    await ensureCompanyDocumentBucket();
    const documentId = randomUUID();
    const storagePath = `${req.companyId}/${documentId}/${safeStorageName(fileName)}`;
    const { error: uploadError } = await supabase.storage.from(COMPANY_DOCUMENT_BUCKET)
      .upload(storagePath, buffer, { contentType, upsert: false });
    if (uploadError) return res.status(400).json({ error: uploadError.message });

    const row = {
      document_id: documentId,
      company_id: req.companyId,
      service_type: serviceType,
      document_type: documentType,
      file_name: fileName,
      storage_bucket: COMPANY_DOCUMENT_BUCKET,
      storage_path: storagePath,
      mime_type: contentType,
      file_size: buffer.length,
      status: "processing",
    };
    const { error: insertError } = await supabase.from("company_documents").insert(row);
    if (insertError) {
      await supabase.storage.from(COMPANY_DOCUMENT_BUCKET).remove([storagePath]);
      return sendDocumentDatabaseError(res, insertError);
    }

    let indexingCompleted = false;
    try {
      const result = await callAiBackend("/api/documents/process", {
        company_id: Number(req.companyId),
        document_id: documentId,
        document_type: documentType,
        service_type: serviceType,
        storage_bucket: COMPANY_DOCUMENT_BUCKET,
        storage_path: storagePath,
      });
      indexingCompleted = true;
      const { data, error } = await supabase.from("company_documents").update({
        status: "indexed", chunks_indexed: result.chunks_indexed, error_message: null, indexed_at: new Date().toISOString(),
      }).eq("company_id", req.companyId).eq("document_id", documentId).select().single();
      if (error) throw error;
      res.status(201).json(data);
    } catch (error) {
      if (indexingCompleted) {
        const { error: cleanupError } = await supabase.rpc("fail_company_document_and_delete_chunks", {
          p_company_id: Number(req.companyId),
          p_document_id: String(documentId),
          p_error_message: String(error.message || error).slice(0, 1000),
        });
        if (cleanupError) throw cleanupError;
      } else {
        await supabase.from("company_documents").update({ status: "failed", error_message: String(error.message || error).slice(0, 1000) })
          .eq("company_id", req.companyId).eq("document_id", documentId);
      }
      res.status(502).json({ error: `Document saved, but indexing failed: ${error.message}`, document_id: documentId });
    }
  })
);

// Replace an existing source document while preserving its document_id. The AI
// backend's document-scoped RPC swaps the old chunks atomically, so retrieval
// never sees a partially indexed policy.
companiesRouter.put(
  "/me/documents/:documentId",
  requireManager,
  asyncHandler(async (req, res) => {
    const { file_name: fileName, content_type: contentType, data_base64: dataBase64 } = req.body || {};
    const validation = validateDocumentUpload(fileName, contentType, dataBase64);
    if (validation.error) return res.status(400).json({ error: validation.error });
    const { buffer } = validation;

    const { data: document, error: findError } = await supabase.from("company_documents")
      .select("document_id, service_type, document_type, storage_bucket, storage_path, status, chunks_indexed, error_message, indexed_at")
      .eq("company_id", req.companyId).eq("document_id", req.params.documentId).single();
    if (findError) {
      if (isMissingCompanyDocumentsTable(findError)) return sendDocumentDatabaseError(res, findError);
      return res.status(404).json({ error: "Document not found." });
    }
    if (!document) return res.status(404).json({ error: "Document not found." });

    await ensureCompanyDocumentBucket();
    const newStoragePath = `${req.companyId}/${document.document_id}/${Date.now()}-${safeStorageName(fileName)}`;
    const { error: uploadError } = await supabase.storage.from(document.storage_bucket)
      .upload(newStoragePath, buffer, { contentType, upsert: false });
    if (uploadError) return res.status(400).json({ error: uploadError.message });

    await supabase.from("company_documents").update({ status: "processing", error_message: null })
      .eq("company_id", req.companyId).eq("document_id", document.document_id);
    let replacementIndexed = false;
    try {
      const result = await callAiBackend("/api/documents/process", {
        company_id: Number(req.companyId),
        document_id: String(document.document_id),
        document_type: document.document_type,
        service_type: document.service_type,
        storage_bucket: document.storage_bucket,
        storage_path: newStoragePath,
      });
      replacementIndexed = true;
      const { data, error } = await supabase.from("company_documents").update({
        file_name: fileName,
        storage_path: newStoragePath,
        mime_type: contentType,
        file_size: buffer.length,
        status: "indexed",
        chunks_indexed: result.chunks_indexed,
        error_message: null,
        indexed_at: new Date().toISOString(),
      }).eq("company_id", req.companyId).eq("document_id", document.document_id).select().single();
      if (error) throw error;
      if (document.storage_path !== newStoragePath) {
        await supabase.storage.from(document.storage_bucket).remove([document.storage_path]);
      }
      res.json(data);
    } catch (error) {
      await supabase.storage.from(document.storage_bucket).remove([newStoragePath]);
      let restored = !replacementIndexed;
      if (replacementIndexed) {
        try {
          await callAiBackend("/api/documents/process", {
            company_id: Number(req.companyId),
            document_id: String(document.document_id),
            document_type: document.document_type,
            service_type: document.service_type,
            storage_bucket: document.storage_bucket,
            storage_path: document.storage_path,
          });
          restored = true;
        } catch (restoreError) {
          console.error("Could not restore the previous document index:", restoreError);
        }
      }
      await supabase.from("company_documents").update(restored ? {
        status: document.status,
        chunks_indexed: document.chunks_indexed,
        error_message: document.error_message,
        indexed_at: document.indexed_at,
      } : {
        status: "failed",
        error_message: `Replacement failed and the previous index could not be restored: ${String(error.message || error)}`.slice(0, 1000),
      }).eq("company_id", req.companyId).eq("document_id", document.document_id);
      res.status(502).json({ error: `Replacement failed; the previous document was retained or restored when possible: ${error.message}` });
    }
  })
);

companiesRouter.delete(
  "/me/documents/:documentId",
  requireManager,
  asyncHandler(async (req, res) => {
    const { data: document, error } = await supabase.from("company_documents")
      .select("document_id, storage_bucket, storage_path")
      .eq("company_id", req.companyId).eq("document_id", req.params.documentId).single();
    if (error) {
      if (isMissingCompanyDocumentsTable(error)) return sendDocumentDatabaseError(res, error);
      return res.status(404).json({ error: "Document not found." });
    }
    if (!document) return res.status(404).json({ error: "Document not found." });

    const { error: rpcError } = await supabase.rpc("delete_company_document_with_chunks", {
      p_company_id: Number(req.companyId), p_document_id: String(document.document_id),
    });
    if (rpcError) return res.status(400).json({ error: rpcError.message });
    const { error: storageError } = await supabase.storage.from(document.storage_bucket).remove([document.storage_path]);
    if (storageError) {
      // The user-visible row and its vectors are already gone atomically. Keep
      // the request successful and leave only an inaccessible orphan for
      // operational cleanup instead of exposing a half-deleted document.
      console.error(`Orphaned Storage object ${document.storage_bucket}/${document.storage_path}:`, storageError);
    }
    res.status(204).end();
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
      if (req.body.settings.selected_services !== undefined) {
        const selected = req.body.settings.selected_services;
        if (!Array.isArray(selected) || selected.some(service => !["grooming", "boarding", "daycare"].includes(service))) {
          return res.status(400).json({ error: "selected_services contains an unsupported service." });
        }
      }
      let normalizedAvailability = null;
      const hasAvailability = req.body.settings.business_hours !== undefined
        || req.body.settings.closed_dates !== undefined;
      if (hasAvailability) {
        if (req.body.settings.business_hours === undefined || req.body.settings.closed_dates === undefined) {
          return res.status(400).json({ error: "business_hours and closed_dates must be saved together." });
        }
        try {
          normalizedAvailability = normalizeAvailabilitySettings(req.body.settings);
        } catch (error) {
          return res.status(400).json({ error: error.message });
        }
      }
      const { data: current, error: fetchError } = await supabase
        .from("companies")
        .select("settings_json")
        .eq("company_id", req.companyId)
        .single();
      if (fetchError) return res.status(400).json({ error: fetchError.message });

      const nextSettings = { ...(current.settings_json || {}), ...req.body.settings };
      // Uploaded document state belongs exclusively to company_documents.
      // Remove the retired duplicate filename map on every settings write.
      delete nextSettings.policies;
      // Availability has normalized relational tables as its sole source of truth.
      delete nextSettings.business_hours;
      delete nextSettings.closed_dates;
      payload.settings_json = nextSettings;

      if (normalizedAvailability) {
        const { error: availabilityError } = await supabase.rpc("replace_company_availability", {
          p_company_id: Number(req.companyId),
          p_business_hours: normalizedAvailability.businessHours,
          p_closed_dates: normalizedAvailability.closedDates,
        });
        if (availabilityError) {
          return res.status(503).json({
            error: `Could not save availability settings: ${availabilityError.message}`,
          });
        }
      }
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
    const [hoursResult, datesResult] = await Promise.all([
      supabase.from("company_business_hours").select("day_of_week,open_time,close_time,is_closed")
        .eq("company_id", req.companyId).order("day_of_week"),
      supabase.from("company_closed_dates").select("closed_date,reason")
        .eq("company_id", req.companyId).order("closed_date"),
    ]);
    const availability = availabilityAsSettings(hoursResult.data, datesResult.data);
    res.json({
      ...data,
      settings_json: { ...(data.settings_json || {}), ...availability },
    });
  })
);
