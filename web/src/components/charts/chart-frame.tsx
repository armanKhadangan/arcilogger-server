import { BarChart3, Table2 } from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

export interface LegendItem {
  label: string;
  color: string;
  value?: string;
}

export interface TableView {
  columns: string[];
  rows: (string | number)[][];
}

interface ChartFrameProps {
  title: string;
  description?: string;
  legend?: LegendItem[];
  /** The same data as a table: every chart has a table view (accessibility, exact values). */
  table: TableView;
  children: ReactNode;
  className?: string;
}

/**
 * Card around a chart: title, legend, chart/table toggle. The chart graphic itself is hidden from
 * screen readers (charts set aria-hidden); the legend values and the table view carry the data.
 */
export function ChartFrame({
  title,
  description,
  legend,
  table,
  children,
  className,
}: ChartFrameProps) {
  const { t } = useTranslation();
  const [showTable, setShowTable] = useState(false);
  return (
    <Card className={className}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4">
        <div className="flex flex-col gap-0.5">
          <h3 className="text-sm font-semibold">{title}</h3>
          {description ? <p className="text-13 text-muted">{description}</p> : null}
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-pressed={showTable}
          aria-label={showTable ? t('common.showChart') : t('common.showTable')}
          title={showTable ? t('common.showChart') : t('common.showTable')}
          onClick={() => {
            setShowTable((value) => !value);
          }}
        >
          {showTable ? <BarChart3 aria-hidden="true" /> : <Table2 aria-hidden="true" />}
        </Button>
      </div>
      {legend && legend.length > 1 && !showTable ? (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 px-5 pt-2 text-13 text-muted">
          {legend.map((item) => (
            <li key={item.label} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className="size-2.5 rounded-sm"
                style={{ background: item.color }}
              />
              <span className="text-foreground">{item.label}</span>
              {item.value ? <span className="tabular">{item.value}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="px-5 pt-2 pb-4">
        {showTable ? (
          <div className="max-h-72 overflow-auto rounded-lg border border-border">
            <table className="w-full text-13">
              <caption className="sr-only">{title}</caption>
              <thead className="sticky top-0 bg-subtle text-left text-muted-strong">
                <tr>
                  {table.columns.map((column) => (
                    <th key={column} scope="col" className="px-3 py-1.5 font-medium">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, index) => (
                  <tr key={index} className="border-t border-border">
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex} className="px-3 py-1.5 tabular">
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          children
        )}
      </div>
    </Card>
  );
}
