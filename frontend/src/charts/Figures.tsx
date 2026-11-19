import { useMemo, useState } from "react";
import type { Cell, Run } from "../api";
import { cell, headline, label, number } from "./format";

/** One headline number, with the row's other columns as supporting figures. */
export function Stat({ run, value, caption }: { run: Run; value: string; caption: string }) {
  const row = run.rows[0] ?? [];
  const index = run.columns.findIndex((c) => c.name === value);
  const main = row[index];
  const others = run.columns.map((column, i) => ({ column, value: row[i] ?? null })).filter((_, i) => i !== index);
  return (
    <div className="flex h-full flex-col items-start justify-center gap-2 px-2">
      <div className="text-7xl font-semibold tracking-tight text-ink-strong" title={typeof main === "number" ? number(main) : undefined}>
        {typeof main === "number" ? headline(main) : String(main ?? "–")}
      </div>
      <div className="text-base text-ink-muted">{caption}</div>
      {others.length > 0 && (
        <dl className="mt-6 flex flex-wrap gap-x-10 gap-y-3 border-t border-line pt-5">
          {others.map(({ column, value }) => (
            <div key={column.name}>
              <dd className="text-xl font-semibold text-ink tabular-nums">{cell(value, column)}</dd>
              <dt className="text-xs text-ink-muted">{label(column.name)}</dt>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

const SHOWN = 500;

/** The result as rows and columns. Click a heading to sort. */
export function DataTable({ run }: { run: Run }) {
  const [sort, setSort] = useState<{ column: number; down: boolean } | null>(null);
  const rows = useMemo(() => {
    if (!sort) return run.rows;
    const compare = (a: Cell, b: Cell) =>
      a === null ? 1 : b === null ? -1 : typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
    const sorted = [...run.rows].sort((a, b) => compare(a[sort.column] ?? null, b[sort.column] ?? null));
    return sort.down ? sorted.reverse() : sorted;
  }, [run.rows, sort]);
  return (
    <div className="h-full overflow-auto">
      <table className="w-full border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            {run.columns.map((column, i) => (
              <th
                key={column.name}
                scope="col"
                aria-sort={sort?.column === i ? (sort.down ? "descending" : "ascending") : undefined}
                className={`sticky top-0 border-b border-line-strong bg-surface p-0 text-xs font-medium text-ink-muted ${column.kind === "number" ? "text-right" : "text-left"}`}
              >
                <button
                  type="button"
                  className="w-full cursor-pointer px-3 py-2 text-inherit hover:text-ink-strong"
                  style={{ textAlign: "inherit" }}
                  onClick={() => setSort((s) => (s?.column === i ? (s.down ? null : { column: i, down: true }) : { column: i, down: false }))}
                >
                  {label(column.name)}
                  {sort?.column === i && <span aria-hidden> {sort.down ? "↓" : "↑"}</span>}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, SHOWN).map((row, r) => (
            <tr key={r} className="hover:bg-raised/50">
              {run.columns.map((column, i) => (
                <td
                  key={column.name}
                  className={`max-w-xl border-b border-line px-3 py-1.5 align-top ${column.kind === "number" ? "text-right tabular-nums" : ""}`}
                >
                  {cell(row[i] ?? null, column, column.name.endsWith("year") || column.name.endsWith("_id"))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > SHOWN && (
        <p className="px-3 py-2 text-xs text-ink-faint">
          Showing the first {SHOWN} of {number(rows.length)} rows.
        </p>
      )}
    </div>
  );
}
