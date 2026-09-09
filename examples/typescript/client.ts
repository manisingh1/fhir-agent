import type { components } from './schema.js';

export type PatientResponse = components['schemas']['ResourceResponse'];
export type SearchResponse = components['schemas']['SearchResponse'];
export type Operation = 'conditions' | 'encounters' | 'medications' | 'allergies' | 'labs' | 'vitals';

/** Server-side only. Supply a grant minted AFTER authorizing the user and patient. */
export class FhirServiceClient {
  constructor(private baseUrl: string, private grant: () => Promise<string>) {}

  private async request<T>(path: string): Promise<T> {
    const response = await fetch(new URL(path, this.baseUrl), {
      headers: { Authorization: `Bearer ${await this.grant()}` },
      redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(60_000),
    });
    if (!response.ok) throw new Error(`FHIR service request failed (${response.status})`);
    return await response.json() as T;
  }

  readPatient(patientId: string): Promise<PatientResponse> {
    return this.request(`/v1/patients/${encodeURIComponent(patientId)}`);
  }

  search(patientId: string, operation: Operation): Promise<SearchResponse> {
    return this.request(`/v1/patients/${encodeURIComponent(patientId)}/${operation}`);
  }
}
