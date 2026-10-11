'use client';

import { useCallback, useMemo, useState } from 'react';

export type SortDir = 'asc' | 'desc';
type SortValue = number | string | null | undefined;

/**
 * Client-side column sorting for a table that already holds every row it shows. `accessors` maps a column key to the
 * value it sorts by. Missing values (null/undefined/NaN) always sink to the bottom, in either direction — a blank is
 * not "smallest". The first click on a numeric column sorts descending (biggest movers first), on text ascending.
 */
export function useTableSort<T, K extends string>(
  rows: T[],
  accessors: Record<K, (row: T) => SortValue>,
  initial: { key: K; dir: SortDir } | null = null,
) {
  const [sort, setSort] = useState(initial);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const get = accessors[sort.key];
    const sign = sort.dir === 'asc' ? 1 : -1;
    return rows
      .map((row, i) => ({ row, i, v: get(row) }))
      .sort((a, b) => {
        const aMissing = a.v == null || (typeof a.v === 'number' && Number.isNaN(a.v));
        const bMissing = b.v == null || (typeof b.v === 'number' && Number.isNaN(b.v));
        if (aMissing || bMissing) return aMissing === bMissing ? a.i - b.i : aMissing ? 1 : -1;
        const cmp = typeof a.v === 'number' && typeof b.v === 'number' ? a.v - b.v : String(a.v).localeCompare(String(b.v));
        return cmp === 0 ? a.i - b.i : sign * cmp;
      })
      .map((x) => x.row);
    // accessors is a static map per table; rows/sort drive the recompute
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);

  const toggle = useCallback(
    (key: K) => {
      setSort((prev) => {
        if (prev?.key === key) return { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' };
        const sample = rows.map(accessors[key]).find((v) => v != null);
        return { key, dir: typeof sample === 'string' ? 'asc' : 'desc' };
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows],
  );

  return { sorted, sortKey: sort?.key ?? null, sortDir: sort?.dir ?? null, toggle };
}
