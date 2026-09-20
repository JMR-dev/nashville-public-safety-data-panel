import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import { CallDetails } from "../../src/components/call-details.tsx";
import { FakeApi } from "../fake-api.ts";
import { callDetail, iso, T0 } from "../fixtures.ts";
import { renderWithClient } from "../render.tsx";

function field(name: string): HTMLElement | null {
  const record = screen.getByRole("group", { name: "Published record" });
  return within(record).getByText(name, { selector: "code" }).closest("div");
}

describe("CallDetails", () => {
  test("shows every published field under its upstream name", async () => {
    const api = new FakeApi();
    api.on("CallDetail", ({ id }) => ({ call: { ...callDetail(5), id } }));
    renderWithClient(<CallDetails id="1:5" onClose={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "ALARM - BURGLAR" })).toBeVisible();
    expect(api.calls("CallDetail")).toEqual([{ id: "1:5" }]);
    expect(screen.getByText("100 block of BROADWAY (approximate)")).toBeVisible();
    expect(field("ZONE_")).toHaveTextContent("15");
    expect(field("Complaint_Number")).toHaveTextContent("Not published");
    expect(field("Latitude")).toHaveTextContent("36.16");
    expect(field("Call_Received")).toHaveTextContent(
      `${String(T0 - 5 * 60_000)} (Sep 19, 11:55 AM CDT)`,
    );
    expect(screen.getByText("Source generation").nextSibling).toHaveTextContent("1");
    expect(
      screen.queryByRole("heading", { name: "Additional published attributes" }),
    ).not.toBeInTheDocument();
  });

  test("explains removed records, missing locations, and unfamiliar attributes", async () => {
    const detail = callDetail(6, {
      sourcePresent: false,
      removedAt: iso(T0),
      hasCoordinates: false,
      additionalAttributes: { Priority: "HIGH" },
    });
    detail.record.Call_Received = null;
    const api = new FakeApi();
    api.on("CallDetail", () => ({ call: detail }));
    renderWithClient(<CallDetails id="1:6" onClose={vi.fn()} />);

    expect(
      await screen.findByText("No longer in Metro Nashville's published data as of Sep 19, 12:00 PM CDT."),
    ).toBeVisible();
    expect(screen.getByText("The source did not publish a usable location.")).toBeVisible();
    expect(field("Call_Received")).toHaveTextContent("Not published");
    const extra = screen.getByRole("group", { name: "Additional published attributes" });
    expect(within(extra).getByText("Priority").closest("div")).toHaveTextContent("HIGH");
  });

  test("says when a call is no longer available", async () => {
    const api = new FakeApi();
    api.on("CallDetail", () => ({ call: null }));
    renderWithClient(<CallDetails id="1:9" onClose={vi.fn()} />);
    expect(await screen.findByText("This call is no longer available.")).toBeVisible();
  });

  test("reports failures to load", async () => {
    const api = new FakeApi();
    api.on("CallDetail", () => {
      throw new Error("Invalid call id");
    });
    renderWithClient(<CallDetails id="bad" onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid call id");
  });

  test("shows loading, takes focus, and closes by button or Escape", async () => {
    const user = userEvent.setup();
    const api = new FakeApi();
    api.on("CallDetail", () => ({ call: callDetail(5) }));
    const onClose = vi.fn();
    renderWithClient(<CallDetails id="1:5" onClose={onClose} />);
    expect(screen.getByText("Loading call details…")).toBeVisible();
    const heading = await screen.findByRole("heading", { name: "ALARM - BURGLAR" });
    expect(heading).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Close call details" }));
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  test("describes a call whose type, disposition, and area are not published", async () => {
    const detail = callDetail(7);
    Object.assign(detail.record, {
      Tencode_Description: null,
      Disposition_Description: null,
      ZONE_: null,
      Sector: null,
    });
    const api = new FakeApi();
    api.on("CallDetail", () => ({ call: detail }));
    renderWithClient(<CallDetails id="1:7" onClose={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Call type not published" })).toBeVisible();
    expect(screen.getByText("Disposition").nextSibling).toHaveTextContent("No disposition yet");
    expect(screen.getByText("Area").nextSibling).toHaveTextContent("Not published");
  });

  test("stays open for keys other than Escape", async () => {
    const user = userEvent.setup();
    const api = new FakeApi();
    api.on("CallDetail", () => ({ call: callDetail(5) }));
    const onClose = vi.fn();
    renderWithClient(<CallDetails id="1:5" onClose={onClose} />);
    await screen.findByRole("heading", { name: "ALARM - BURGLAR" });
    await user.keyboard("{Enter}a");
    expect(onClose).not.toHaveBeenCalled();
  });
});
