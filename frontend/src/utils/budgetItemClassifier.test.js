import { describe, it, expect } from 'vitest';
import { BUDGET_ITEMS, slugify, classifyFile } from './budgetItemClassifier.js';

/**
 * Helper to create a mock File object with a given filename.
 */
function makeFile(name) {
  return new File([''], name, { type: 'application/pdf' });
}

describe('BUDGET_ITEMS', () => {
  it('exports exactly 12 canonical budget items', () => {
    expect(BUDGET_ITEMS).toHaveLength(12);
  });

  it('includes all expected items', () => {
    const expected = [
      'Salary',
      'Fringe',
      'Contractual Service',
      'Equipment',
      'Insurance',
      'Travel and Conferences',
      'Space Rental/Occupancy Costs',
      'Telecommunications',
      'Utilities',
      'Supplies',
      'Other',
      'Indirect Costs',
    ];
    expect(BUDGET_ITEMS).toEqual(expected);
  });
});

describe('slugify', () => {
  it('lowercases and replaces non-alphanumeric with underscore', () => {
    expect(slugify('Salary')).toBe('salary');
    expect(slugify('Space Rental/Occupancy Costs')).toBe('space_rental_occupancy_costs');
    expect(slugify('Travel and Conferences')).toBe('travel_and_conferences');
  });

  it('collapses multiple underscores', () => {
    expect(slugify('foo---bar___baz')).toBe('foo_bar_baz');
  });

  it('strips leading and trailing underscores', () => {
    expect(slugify('__hello__')).toBe('hello');
    expect(slugify('-test-')).toBe('test');
  });

  it('handles mixed separators', () => {
    expect(slugify('Travel-and-Conferences')).toBe('travel_and_conferences');
  });
});

describe('classifyFile', () => {
  describe('exact matches', () => {
    it('matches Salary.pdf to Salary', () => {
      expect(classifyFile(makeFile('Salary.pdf'))).toBe('Salary');
    });

    it('matches space_rental_occupancy_costs.pdf to Space Rental/Occupancy Costs', () => {
      expect(classifyFile(makeFile('space_rental_occupancy_costs.pdf'))).toBe('Space Rental/Occupancy Costs');
    });

    it('matches each canonical item by its slugified name', () => {
      const cases = [
        ['Salary.pdf', 'Salary'],
        ['Fringe.pdf', 'Fringe'],
        ['Contractual_Service.pdf', 'Contractual Service'],
        ['Equipment.pdf', 'Equipment'],
        ['Insurance.pdf', 'Insurance'],
        ['Travel_and_Conferences.pdf', 'Travel and Conferences'],
        ['Space_Rental_Occupancy_Costs.pdf', 'Space Rental/Occupancy Costs'],
        ['Telecommunications.pdf', 'Telecommunications'],
        ['Utilities.pdf', 'Utilities'],
        ['Supplies.pdf', 'Supplies'],
        ['Other.pdf', 'Other'],
        ['Indirect_Costs.pdf', 'Indirect Costs'],
      ];
      for (const [filename, expected] of cases) {
        expect(classifyFile(makeFile(filename))).toBe(expected);
      }
    });
  });

  describe('case insensitive', () => {
    it('matches TELECOMMUNICATIONS.pdf to Telecommunications', () => {
      expect(classifyFile(makeFile('TELECOMMUNICATIONS.pdf'))).toBe('Telecommunications');
    });

    it('matches salary.pdf to Salary', () => {
      expect(classifyFile(makeFile('salary.pdf'))).toBe('Salary');
    });
  });

  describe('mixed separators', () => {
    it('matches Travel-and-Conferences.pdf to Travel and Conferences', () => {
      expect(classifyFile(makeFile('Travel-and-Conferences.pdf'))).toBe('Travel and Conferences');
    });
  });

  describe('underscore names', () => {
    it('matches Indirect_Costs.pdf to Indirect Costs', () => {
      expect(classifyFile(makeFile('Indirect_Costs.pdf'))).toBe('Indirect Costs');
    });

    it('matches Contractual_Service.pdf to Contractual Service', () => {
      expect(classifyFile(makeFile('Contractual_Service.pdf'))).toBe('Contractual Service');
    });
  });

  describe('unrecognized files', () => {
    it('returns null for random_report.pdf', () => {
      expect(classifyFile(makeFile('random_report.pdf'))).toBeNull();
    });

    it('returns null for completely unknown filenames', () => {
      expect(classifyFile(makeFile('foobar.pdf'))).toBeNull();
    });
  });

  describe('non-PDF files', () => {
    it('returns null for Salary.docx', () => {
      const file = new File([''], 'Salary.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
      expect(classifyFile(file)).toBeNull();
    });

    it('returns null for Salary.xlsx', () => {
      const file = new File([''], 'Salary.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
      expect(classifyFile(file)).toBeNull();
    });

    it('returns null for Salary.txt', () => {
      const file = new File([''], 'Salary.txt', { type: 'text/plain' });
      expect(classifyFile(file)).toBeNull();
    });
  });

  describe('prefix matches', () => {
    it('matches Salary_January_2025.pdf to Salary', () => {
      expect(classifyFile(makeFile('Salary_January_2025.pdf'))).toBe('Salary');
    });

    it('matches fringe-q1-report.pdf to Fringe', () => {
      expect(classifyFile(makeFile('fringe-q1-report.pdf'))).toBe('Fringe');
    });

    it('matches travel_and_conferences_receipts.pdf to Travel and Conferences', () => {
      expect(classifyFile(makeFile('travel_and_conferences_receipts.pdf'))).toBe('Travel and Conferences');
    });

    it('matches Space_Rental_Occupancy_Costs_Lease.pdf to Space Rental/Occupancy Costs', () => {
      expect(classifyFile(makeFile('Space_Rental_Occupancy_Costs_Lease.pdf'))).toBe('Space Rental/Occupancy Costs');
    });

    it('prefers longest slug match (space_rental_occupancy_costs over other)', () => {
      // "other_expenses.pdf" should match "Other" not something else
      expect(classifyFile(makeFile('other_expenses.pdf'))).toBe('Other');
    });

    it('does not false-positive on partial word overlap', () => {
      // "supply_chain.pdf" should NOT match "Supplies" because the slug is "supplies"
      // and "supply_chain" does not start with "supplies_"
      expect(classifyFile(makeFile('supply_chain.pdf'))).toBeNull();
    });
  });
});
