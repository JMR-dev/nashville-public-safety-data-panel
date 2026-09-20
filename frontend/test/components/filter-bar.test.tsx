import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, test, vi } from "vitest";

import { FilterBar } from "../../src/components/filter-bar.tsx";
import { DEFAULT_FILTERS, type Filters } from "../../src/filters.ts";
import { FILTER_VALUES } from "../fixtures.ts";

function renderBar(filters: Filters = DEFAULT_FILTERS, values = FILTER_VALUES) {
  const onChange = vi.fn();
  const view = render(
    <FilterBar filters={filters} values={values} today="2026-09-19" onChange={onChange} />,
  );
  return { ...view, onChange };
}

describe("FilterBar", () => {
  test("offers date presets and the values published in the period", async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar();
    expect(screen.getByLabelText("Date range")).toHaveValue("24h");
    expect(screen.getByRole("option", { name: "TRAFFIC VIOLATION" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Date range"), "Last 7 days");
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_FILTERS,
      range: { kind: "preset", preset: "7d" },
    });
    await user.selectOptions(screen.getByLabelText("Zone"), "23");
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, zone: "23" });
    await user.selectOptions(screen.getByLabelText("Sector"), "N2");
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, sector: "N2" });
    await user.selectOptions(screen.getByLabelText("Call type"), "TRAFFIC VIOLATION");
    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, type: "TRAFFIC VIOLATION" });
    await user.selectOptions(screen.getByLabelText("Disposition"), "REPORT TAKEN");
    expect(onChange).toHaveBeenLastCalledWith({
      ...DEFAULT_FILTERS,
      disposition: "REPORT TAKEN",
    });
  });

  test("keeps a chosen value selectable while the list is loading or no longer includes it", () => {
    renderBar({ ...DEFAULT_FILTERS, zone: "99" }, undefined);
    expect(screen.getByLabelText("Zone")).toHaveValue("99");
    expect(screen.getByLabelText("Sector")).toHaveValue("");
  });

  test("applies a custom range only when its dates are usable", () => {
    const applied: Filters[] = [];
    function Controlled() {
      const [filters, setFilters] = useState(DEFAULT_FILTERS);
      return (
        <FilterBar
          filters={filters}
          values={FILTER_VALUES}
          today="2026-09-19"
          onChange={(next) => {
            applied.push(next);
            setFilters(next);
          }}
        />
      );
    }
    render(<Controlled />);
    fireEvent.change(screen.getByLabelText("Date range"), { target: { value: "custom" } });
    expect(applied.at(-1)?.range).toEqual({ kind: "custom", first: "2026-09-13", last: "2026-09-19" });
    expect(screen.getByLabelText("From")).toHaveValue("2026-09-13");
    expect(screen.getByLabelText("Through")).toHaveValue("2026-09-19");

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-20" } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The start date must be on or before the end date.",
    );
    expect(screen.getByLabelText("From")).toHaveValue("2026-09-20");
    expect(applied).toHaveLength(1);

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-09-01" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(applied.at(-1)?.range).toEqual({ kind: "custom", first: "2026-09-01", last: "2026-09-19" });

    fireEvent.change(screen.getByLabelText("Through"), { target: { value: "2026-09-15" } });
    expect(applied.at(-1)?.range).toEqual({ kind: "custom", first: "2026-09-01", last: "2026-09-15" });

    fireEvent.change(screen.getByLabelText("Date range"), { target: { value: "7d" } });
    expect(screen.queryByLabelText("From")).not.toBeInTheDocument();
  });

  test("resets every filter at once", async () => {
    const user = userEvent.setup();
    const { onChange, rerender } = renderBar();
    expect(screen.queryByRole("button", { name: "Reset filters" })).not.toBeInTheDocument();
    const narrowed = { ...DEFAULT_FILTERS, zone: "15" };
    rerender(
      <FilterBar filters={narrowed} values={FILTER_VALUES} today="2026-09-19" onChange={onChange} />,
    );
    await user.click(screen.getByRole("button", { name: "Reset filters" }));
    expect(onChange).toHaveBeenLastCalledWith(DEFAULT_FILTERS);
  });

  test("shows a ten-code as a code while filtering by the value the source published", async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(DEFAULT_FILTERS, {
      ...FILTER_VALUES,
      Tencode_Description: ["3", "ALARM - BURGLAR"],
    });
    expect(screen.getByRole("option", { name: "Code 3" })).toHaveValue("3");

    await user.selectOptions(screen.getByLabelText("Call type"), "3");

    expect(onChange).toHaveBeenLastCalledWith({ ...DEFAULT_FILTERS, type: "3" });
  });

  test("pressing Enter in a field does not reload the page", () => {
    renderBar();
    expect(fireEvent.submit(screen.getByRole("search", { name: "Filter calls" }))).toBe(false);
  });
});
