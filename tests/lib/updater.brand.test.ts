import { describe, expect, it, vi } from "vitest";
import { checkForUpdate } from "@/lib/updater";

const updater = vi.hoisted(() => ({ check: vi.fn() }));
vi.mock("@tauri-apps/plugin-updater", () => updater);

describe("independent product update policy", () => {
  it("reports an unconfigured update service without calling a native updater", async () => {
    expect(await checkForUpdate()).toEqual({ status: "disabled" });
    expect(updater.check).not.toHaveBeenCalled();
  });
});
