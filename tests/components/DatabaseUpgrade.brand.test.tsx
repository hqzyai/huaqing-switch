import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DatabaseUpgrade } from "@/components/DatabaseUpgrade";

const native = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => native);

describe("database recovery without a product update service", () => {
  it("explains manual recovery without checking updates or offering an upstream release", () => {
    native.invoke.mockClear();
    render(
      <DatabaseUpgrade payload={{ db_version: 99, supported_version: 20 }} />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "branding.databaseUpdateUnavailable",
    );
    expect(native.invoke).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", {
        name: /dbUpgrade.openReleases|打开发布页/,
      }),
    ).not.toBeInTheDocument();
  });
});
