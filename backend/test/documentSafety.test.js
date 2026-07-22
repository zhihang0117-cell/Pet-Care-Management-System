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
