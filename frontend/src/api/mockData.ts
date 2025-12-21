/**
 * Mock API data loader
 * Uses static JSON fixture while developing frontend
 */

import type { DocumentAnalysis, LambdaResponse } from '../types/DocumentAnalysis';

/**
 * Load mock DocumentAnalysis from static fixture
 * Simulates the API response we'll get from AWS API Gateway
 */
export async function loadMockAnalysis(): Promise<DocumentAnalysis> {
  try {
    // Fetch the mock Lambda response
    const response = await fetch('/mock-analysis.json');

    if (!response.ok) {
      throw new Error(`Failed to load mock data: ${response.statusText}`);
    }

    const lambdaResponse: LambdaResponse = await response.json();

    // Parse the DocumentAnalysis from body.answer (it's a JSON string)
    const documentAnalysis: DocumentAnalysis = JSON.parse(lambdaResponse.body.answer);

    console.log('✓ Mock data loaded:', {
      documentId: documentAnalysis.documentId,
      pageCount: documentAnalysis.pageCount,
      groups: documentAnalysis.groups.length
    });

    return documentAnalysis;
  } catch (error) {
    console.error('Error loading mock data:', error);
    throw error;
  }
}
