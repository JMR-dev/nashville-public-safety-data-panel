import { useId, useState } from "react";

import type { FilterValues } from "../api/types.ts";
import {
  DEFAULT_FILTERS,
  describeCallType,
  type Filters,
  isDefault,
  rangeProblem,
} from "../filters.ts";
import { addDays, type RangePreset } from "../time.ts";

interface FilterBarProperties {
  filters: Filters;
  values: FilterValues | undefined;
  today: string;
  onChange: (filters: Filters) => void;
}

const PRESETS: readonly (readonly [RangePreset, string])[] = [
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
];

function isPreset(value: string): value is RangePreset {
  return PRESETS.some(([preset]) => preset === value);
}

interface ValueSelectProperties {
  label: string;
  everything: string;
  value: string;
  options: string[] | undefined;
  describe?: (value: string) => string;
  onChange: (value: string) => void;
}

function ValueSelect({
  label,
  everything,
  value,
  options = [],
  describe = (option) => option,
  onChange,
}: ValueSelectProperties) {
  const id = useId();
  // A chosen value stays listed while options load or after it leaves the period.
  const listed = value === "" || options.includes(value) ? options : [value, ...options];
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      >
        <option value="">{everything}</option>
        {listed.map((option) => (
          <option key={option} value={option}>
            {describe(option)}
          </option>
        ))}
      </select>
    </div>
  );
}

interface DateFieldsProperties {
  dates: { first: string; last: string };
  problem: string | undefined;
  today: string;
  onChange: (first: string, last: string) => void;
}

function DateFields({ dates, problem, today, onChange }: DateFieldsProperties) {
  const fromId = useId();
  const throughId = useId();
  return (
    <div className="date-fields">
      <div className="field">
        <label htmlFor={fromId}>From</label>
        <input
          id={fromId}
          type="date"
          value={dates.first}
          max={today}
          onChange={(event) => {
            onChange(event.target.value, dates.last);
          }}
        />
      </div>
      <div className="field">
        <label htmlFor={throughId}>Through</label>
        <input
          id={throughId}
          type="date"
          value={dates.last}
          max={today}
          onChange={(event) => {
            onChange(dates.first, event.target.value);
          }}
        />
      </div>
      {problem !== undefined && (
        <p className="field-problem" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}

export function FilterBar({ filters, values, today, onChange }: FilterBarProperties) {
  const rangeId = useId();
  // Dates entered but not applied because they are not a usable range.
  const [draft, setDraft] = useState<{ first: string; last: string }>();
  const custom = filters.range.kind === "custom" ? filters.range : undefined;
  const problem = draft && rangeProblem(draft.first, draft.last, today);

  function chooseRange(value: string): void {
    setDraft(undefined);
    const range = isPreset(value)
      ? ({ kind: "preset", preset: value } as const)
      : ({ kind: "custom", first: addDays(today, -6), last: today } as const);
    onChange({ ...filters, range });
  }

  function changeDates(first: string, last: string): void {
    if (rangeProblem(first, last, today) === undefined) {
      setDraft(undefined);
      onChange({ ...filters, range: { kind: "custom", first, last } });
    } else {
      setDraft({ first, last });
    }
  }

  return (
    <form
      className="filter-bar"
      role="search"
      aria-label="Filter calls"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      <div className="field">
        <label htmlFor={rangeId}>Date range</label>
        <select
          id={rangeId}
          value={filters.range.kind === "preset" ? filters.range.preset : "custom"}
          onChange={(event) => {
            chooseRange(event.target.value);
          }}
        >
          {PRESETS.map(([preset, label]) => (
            <option key={preset} value={preset}>
              {label}
            </option>
          ))}
          <option value="custom">Custom dates</option>
        </select>
      </div>
      {custom && (
        <DateFields
          dates={draft ?? custom}
          problem={problem}
          today={today}
          onChange={changeDates}
        />
      )}
      <ValueSelect
        label="Zone"
        everything="All zones"
        value={filters.zone}
        options={values?.ZONE_}
        onChange={(zone) => {
          onChange({ ...filters, zone });
        }}
      />
      <ValueSelect
        label="Sector"
        everything="All sectors"
        value={filters.sector}
        options={values?.Sector}
        onChange={(sector) => {
          onChange({ ...filters, sector });
        }}
      />
      <ValueSelect
        label="Call type"
        everything="All call types"
        value={filters.type}
        options={values?.Tencode_Description}
        describe={describeCallType}
        onChange={(type) => {
          onChange({ ...filters, type });
        }}
      />
      <ValueSelect
        label="Disposition"
        everything="All dispositions"
        value={filters.disposition}
        options={values?.Disposition_Description}
        onChange={(disposition) => {
          onChange({ ...filters, disposition });
        }}
      />
      {!isDefault(filters) && (
        <button
          type="button"
          className="quiet"
          onClick={() => {
            onChange(DEFAULT_FILTERS);
          }}
        >
          Reset filters
        </button>
      )}
    </form>
  );
}
