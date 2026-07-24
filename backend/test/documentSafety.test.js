import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const projectRoot = new URL("../../", import.meta.url);

async function source(relativePath) {
  return readFile(new URL(relativePath, projectRoot), "utf8");
}

test("registration never persists a manager password between pages", async () => {
  const registration = await source("web/index.js");
  assert.match(registration, /JSON\.stringify\(\{ email, services \}\)/);
  assert.doesNotMatch(registration, /JSON\.stringify\(\{ email, password, services \}\)/);
});

test("uploaded filenames are rendered as text nodes", async () => {
  const registration = await source("web/index.js");
  const settings = await source("web/common.js");
  assert.match(registration, /document\.createTextNode\(input\.files\[0\]\.name\)/);
  assert.match(settings, /document\.createTextNode\(input\.files\[0\]\.name\)/);
});

test("settings policy cards use the shared HTML escaping helper", async () => {
  const settings = await source("web/common.js");
  assert.match(settings, /function escapeUiText\(value\)/);
  assert.match(settings, /escapeUiText\(doc\.file_name\)/);
  assert.match(settings, /escapeUiText\(doc\.error_message\)/);
  assert.doesNotMatch(settings, /escapeHtml\(doc\./);
  assert.match(settings, /encodeURIComponent\(doc\.file_name\)\.replaceAll\("'", "%27"\)/);
});

test("policy documents have a private authenticated preview flow", async () => {
  const settings = await source("web/common.js");
  const apiClient = await source("web/api-client.js");
  const routes = await source("backend/src/routes/companies.js");
  assert.match(settings, /viewPolicyDocument\('\$\{doc\.document_id\}'\)/);
  assert.match(apiClient, /documents\/\$\{encodeURIComponent\(documentId\)\}\/preview/);
  assert.match(routes, /"\/me\/documents\/:documentId\/preview"/);
  assert.match(routes, /mammoth\.extractRawText\(\{ buffer \}\)/);
});

test("Render private hostnames are normalized before AI backend fetches", async () => {
  const routes = await source("backend/src/routes/companies.js");
  assert.match(routes, /\^https\?:\\\/\\\//i);
  assert.match(routes, /`http:\/\/\$\{configuredUrl\}`/);
});

test("document deletion is atomic inside Postgres", async () => {
  const migration = await source("backend/sql/company_documents_migration.sql");
  const routes = await source("backend/src/routes/companies.js");
  assert.match(migration, /function delete_company_document_with_chunks/);
  assert.match(migration, /delete from chunks_bge_large[\s\S]*delete from company_documents/);
  assert.match(routes, /rpc\("delete_company_document_with_chunks"/);
  assert.match(migration, /function fail_company_document_and_delete_chunks/);
});

test("settings save prevents duplicate submissions", async () => {
  const settings = await source("web/common.js");
  assert.match(settings, /if \(saveButton\?\.disabled\) return/);
  assert.match(settings, /saveButton\.disabled = true/);
  assert.match(settings, /saveButton\.disabled = false/);
});

test("settings page-level save persists edited team accounts", async () => {
  const settings = await source("web/common.js");
  assert.match(settings, /await saveChangedTeamAccounts\(\)/);
  assert.match(settings, /function changedTeamAccountPayloads\(\)/);
  assert.match(settings, /api\.patch\(`\/accounts\/\$\{change\.accountId\}`/);
});

test("staff account creation buttons prevent duplicate submissions", async () => {
  const settingsHtml = await source("web/setting.html");
  const settings = await source("web/common.js");
  assert.match(settingsHtml, /id="addTeamAccountButton"/);
  assert.match(settings, /if \(button\?\.disabled\) return/);
  assert.match(settings, /button\.textContent = "Adding…"/);
});

test("registration explains when staged staff accounts reach Supabase", async () => {
  const registration = await source("web/index.js");
  assert.match(registration, /Save & Enter Portal will create it in Supabase/);
});
