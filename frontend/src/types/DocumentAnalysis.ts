/**
 * TypeScript types for DocumentAnalysis structure
 * Generated from Lambda response schema v1.0
 */

export interface HighlightRect {
  x: number;
  y: number;
  width: number;
  height: number;
  isOrphaned?: boolean; // True if this highlight is for an orphaned entity
}

export interface Occurrence {
  occurrenceId: string;
  groupId: string;
  pageNumber: number;
  role: "summary" | "supporting";
  coords: HighlightRect[];
  snippet: string | null;
  rawSource: Record<string, any>;
}

export interface GroupMeta {
  rawSummaryObjects?: any[];
  rawSupportingObjects?: any[];
}

export interface Group {
  groupId: string;
  label: string;
  kind: string | null;
  summaryPages: number[];
  supportingPages: number[];
  occurrences: Occurrence[];
  meta: GroupMeta | null;
}

export interface PageMetadata {
  pageNumber: number;
  rotation: number; // 0, 90, 180, or 270 degrees
}

export interface DocumentAnalysis {
  schemaVersion: string;
  documentId: string;
  pageCount: number | null;
  pages: PageMetadata[];
  groups: Group[];
}

/**
 * Lambda response wrapper
 */
export interface LambdaResponse {
  statusCode: number;
  body: {
    answer: string; // JSON string containing DocumentAnalysis
    stage1_answer: string;
    stage2_answer: string;
    stage1_usage: Record<string, any>;
    stage2_usage: Record<string, any>;
    stage1_stop_reason: string;
    stage2_stop_reason: string;
    model_id: string;
  };
}
