/** Synthetic integration harness, not a user authentication implementation. */
import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { FhirServiceClient } from './client.js';

const baseUrl = process.env.FHIR_SERVICE_URL ?? 'http://127.0.0.1:8000';
const key = readFileSync(process.env.FHIR_GRANT_KEY_FILE ?? '../../.runtime/grant-key');
const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
const now = Math.floor(Date.now()/1000);
const payload = `${encode({alg:'HS256',typ:'JWT'})}.${encode({
  iss:'fhir-host-app',aud:'fhir-service',sub:'synthetic-test-user',
  patient:'synthetic',resources:['Patient','Condition'],iat:now,exp:now+60,
})}`;
const token = `${payload}.${createHmac('sha256',key).update(payload).digest('base64url')}`;
const client = new FhirServiceClient(baseUrl, async () => token);
const patient = await client.readPatient('synthetic');
assert.equal(patient.resource.resourceType, 'Patient');
assert.equal(patient.resource.id, 'synthetic');
const conditions = await client.search('synthetic','conditions');
assert.equal(conditions.traversal_complete,true);
assert.equal(conditions.resources.length,1);
await assert.rejects(client.readPatient('different-patient'), /403/);
await assert.rejects(client.search('synthetic','encounters'), /403/);
assert.equal((await fetch(`${baseUrl}/v1/patients/synthetic`)).status,401);
console.log('PASS: TypeScript → container → synthetic FHIR adapter; patient read, search, and authorization checks.');
