/**
 * Frontend configuration.
 *
 * API_BASE: API Gateway URL for backend Lambda endpoints (upload, job status).
 *   Set by deploy script via VITE_API_URL. Defaults to '' in development.
 *
 * DATA_BASE: Base URL for S3-backed content served through CloudFront
 *   (reconciliation.json, PDFs). Always same-origin since the frontend
 *   is served from the same CloudFront distribution.
 */
export const API_BASE = import.meta.env.VITE_API_URL || '';
export const DATA_BASE = '';
