# Invoice Upload UI

Demo upload interface for the invoice reconciliation system. Matches the editorial aesthetic of the review UI.

## Features

- CSV invoice file upload
- Individual PDF upload for each of the 12 budget items
- Visual feedback for file selection
- Upload button in top right corner
- Clean, editorial design matching review-ui

## Setup

```bash
cd upload-ui
npm install
```

## Development

```bash
npm run dev
```

Opens on http://localhost:3001

## Production Build

```bash
npm run build
npm run preview
```

## Budget Items

The UI includes upload fields for all 12 canonical budget items:
- Salary
- Fringe
- Contractual Service
- Equipment
- Insurance
- Travel and Conferences
- Space Rental/Occupancy Costs
- Telecommunications
- Utilities
- Supplies
- Other
- Indirect Costs

## Note

This is a demo UI. The upload functionality logs to console but doesn't actually send files to a backend. To implement real uploads, add API integration in the `handleUpload` function in `App.jsx`.
