import { FC, useState } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import type { HibernateStatus, LoadingState, ResultMessage } from "./types";
import { setupHibernate, removeHibernate, testHibernate, applyPowerButtonFix, revertPowerButtonFix } from "./rpc";
import { InlineStatus } from "./InlineStatus";

export const HibernateSection: FC<{
  hibernate: HibernateStatus;
  powerButtonFix: { applied: boolean; error?: string };
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
  refresh: () => Promise<void>;
}> = ({ hibernate, powerButtonFix, loading, setLoading, showResult, result, refresh }) => {
  const [testLog, setTestLog] = useState<string | null>(null);

  const handleSetup = async () => {
    setLoading({ active: "hibernate", message: "Setting up hibernate (this may take several minutes)..." });
    try {
      const res = await setupHibernate();
      if (res.success) {
        if (res.reboot_needed) {
          showResult("hibernate", res.message || "Setup complete — reboot required.", "success");
        } else {
          showResult("hibernate", res.message || "Hibernate ready", "success");
        }
      } else {
        showResult("hibernate", res.error || "Setup failed", "error");
      }
    } catch (e) {
      showResult("hibernate", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleTest = async () => {
    setTestLog(null);
    setLoading({ active: "hibernate", message: "Running hibernate test (system will freeze for 1-3 minutes)..." });
    try {
      const res = await testHibernate();
      if (res.success) {
        showResult("hibernate", res.message || "Test passed", "success");
      } else {
        showResult("hibernate", res.error || "Test failed", "error");
      }
      if (res.log) {
        setTestLog(res.log);
      }
    } catch (e) {
      showResult("hibernate", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handlePowerButtonToggle = async (enabled: boolean) => {
    setLoading({ active: "hibernate", message: enabled ? "Patching power button..." : "Reverting power button..." });
    try {
      const res = enabled ? await applyPowerButtonFix() : await revertPowerButtonFix();
      if (res.success) {
        showResult("hibernate", res.message || (enabled ? "Power button now hibernates" : "Power button restored to default"), "success");
      } else {
        showResult("hibernate", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("hibernate", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleRemove = async () => {
    setLoading({ active: "hibernate", message: "Removing hibernate..." });
    try {
      const res = await removeHibernate();
      if (res.success) {
        if (res.reboot_needed) {
          showResult("hibernate", "Removed — reboot required. Re-apply button fix after reboot.", "success");
        } else {
          showResult("hibernate", res.message || "Hibernate removed", "success");
        }
      } else {
        showResult("hibernate", res.error || "Removal failed", "error");
      }
    } catch (e) {
      showResult("hibernate", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  return (
    <PanelSection title="Hibernate">
      {hibernate.phase === "none" && (
        <>
          <PanelSectionRow>
            <div
              style={{
                backgroundColor: "#1a2a3a",
                border: "1px solid #2a4a6a",
                borderRadius: "4px",
                padding: "8px 12px",
                fontSize: "11px",
                lineHeight: "1.4",
                color: "#88bbdd",
              }}
            >
              Hibernate writes RAM to disk and powers off completely. Zero power drain, ~6-7 second wake.
              {hibernate.ram_gb ? ` Requires ${hibernate.ram_gb}GB swap space.` : ""}
              {" "}Setup takes 1-2 reboots.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleSetup}
              disabled={loading.active === "hibernate"}
            >
              Set Up Hibernate
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}

      {hibernate.phase === "swap_ready" && (
        <>
          <PanelSectionRow>
            <div
              style={{
                backgroundColor: "#1a2a3a",
                border: "1px solid #2a4a6a",
                borderRadius: "4px",
                padding: "8px 12px",
                fontSize: "11px",
                lineHeight: "1.4",
                color: "#88bbdd",
              }}
            >
              Swap ready{hibernate.swap_size_gb ? ` (${hibernate.swap_size_gb}GB)` : ""}.
              {!hibernate.zram_disabled && " zram needs disabling."}
              {(!hibernate.resume_uuid || !hibernate.resume_offset) && " Kernel resume parameters need to be set."}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: "11px", color: "#888", padding: "0 0 4px 0" }}>
              Swap: {hibernate.swap_active ? "active" : "inactive"}
              {" · "}fstab: {hibernate.fstab_entry ? "configured" : "missing"}
              {" · "}zram: {hibernate.zram_disabled ? "disabled" : "active"}
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleSetup}
              disabled={loading.active === "hibernate"}
            >
              Complete Hibernate Setup
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleRemove}
              disabled={loading.active === "hibernate"}
            >
              Remove Hibernate
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}

      {hibernate.phase === "complete" && (
        <>
          <PanelSectionRow>
            <div
              style={{
                backgroundColor: "#1a3a1a",
                border: "1px solid #2a6a2a",
                borderRadius: "4px",
                padding: "8px 12px",
                fontSize: "11px",
                lineHeight: "1.4",
                color: "#88dd88",
              }}
            >
              Hibernate configured{hibernate.swap_size_gb ? ` (${hibernate.swap_size_gb}GB swap)` : ""}.
              Use Steam's power menu to hibernate.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <div style={{ fontSize: "11px", color: "#888", padding: "0 0 4px 0" }}>
              Swap: active · zram: disabled · Resume: UUID set
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Power Button Hibernates"
              description="Short press power button hibernates instead of sleep"
              checked={powerButtonFix.applied}
              onChange={handlePowerButtonToggle}
              disabled={loading.active === "hibernate"}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleTest}
              disabled={loading.active === "hibernate"}
            >
              Test Hibernate
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <div
              style={{
                backgroundColor: "#3a2a00",
                border: "1px solid #5a4a00",
                borderRadius: "4px",
                padding: "8px 12px",
                fontSize: "11px",
                lineHeight: "1.4",
                color: "#ddcc88",
              }}
            >
              Test writes a hibernate image and reads it back without powering off. System will freeze for 1-3 minutes during the test.
            </div>
          </PanelSectionRow>
          {testLog && (
            <PanelSectionRow>
              <div
                style={{
                  backgroundColor: "#1a1a2a",
                  border: "1px solid #2a2a4a",
                  borderRadius: "4px",
                  padding: "8px 12px",
                  fontSize: "10px",
                  lineHeight: "1.3",
                  color: "#aaaacc",
                  maxHeight: "200px",
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  fontFamily: "monospace",
                }}
              >
                {testLog}
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleRemove}
              disabled={loading.active === "hibernate"}
            >
              Remove Hibernate
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}

      <InlineStatus loading={loading} result={result} section="hibernate" />

      {loading.active === "hibernate" && (
        <PanelSectionRow>
          <div
            style={{
              backgroundColor: "#4a3000",
              border: "1px solid #7a5000",
              borderRadius: "4px",
              padding: "8px 12px",
              fontSize: "11px",
              lineHeight: "1.4",
              color: "#ffcc00",
            }}
          >
            Button fix patches will need re-applying after reboot (rpm-ostree creates a new deployment).
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
