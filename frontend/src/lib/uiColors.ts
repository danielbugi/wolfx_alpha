// File: frontend/src/lib/uiColors.ts
// Shared NextUI Chip color-class mappings -- previously duplicated between
// the dashboard (page.tsx) and the stock detail page (stock/[symbol]/page.tsx);
// the dashboard hover card is a third consumer, so it's centralized here.

export function confidenceColor(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'very_high':
      return 'bg-emerald-100 text-emerald-800';
    case 'high':
      return 'bg-cyan-100 text-cyan-800';
    case 'medium':
      return 'bg-amber-100 text-amber-800';
    case 'low':
    case 'very_low':
      return 'bg-slate-100 text-slate-600';
    default:
      return 'bg-slate-100 text-slate-500';
  }
}

export function gradeColor(grade: string | null | undefined): string {
  switch (grade) {
    case 'A':
      return 'bg-emerald-100 text-emerald-800';
    case 'B':
      return 'bg-cyan-100 text-cyan-800';
    case 'C':
      return 'bg-amber-100 text-amber-800';
    default:
      return 'bg-red-100 text-red-700';
  }
}
