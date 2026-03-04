import { FC } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import type { HibernateStatus, LoadingState, ResultMessage } from "./types";
import { setupHibernate, hibernateNow, removeHibernate } from "./rpc";
import { InlineStatus } from "./InlineStatus";

const CheckItem: FC<{ label: string; ok: boolean }> = ({ label, ok }) => (
  <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", padding: "1px 0" }}>
    <span style={{ color: ok ? "#4caf50" : "#888", fontFamily: "monospace" }}>
      {ok ? "[OK]" : "[  ]"}
    </span>
    <span style={{ color: ok ? "#ccc" : "#888" }}>{label}</span>
  </div>
);

export const HibernateSection: FC<{
  hibernate: HibernateStatus;
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
  refresh: () => Promise<void>;
}> = ({ hibernate, loading, setLoading, showResult, result, refresh }) => {

  const handleSetup = async () => {
    setLoading({
      active: "hibernate-setup",
      message: "Setting up hibernate... (creating swap file, may take several minutes)",
    });
    try {
      const res = await setupHibernate(null);
      if (res.success) {
        showResult("hibernate-setup", res.message || "Setup complete — reboot required", "success");
      } else {
        showResult("hibernate-setup", res.error || "Setup failed", "error");
      }
    } catch (e) {
      showResult("hibernate-setup", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleHibernate = async () => {
    setLoading({ active: "hibernate-now", message: "Hibernating..." });
    try {
      const res = await hibernateNow();
      if (!res.success) {
        showResult("hibernate-now", res.error || "Hibernate failed", "error");
      }
    } catch (e) {
      showResult("hibernate-now", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
    }
  };

  const handleRemove = async () => {
    setLoading({ active: "hibernate-remove", message: "Removing hibernate setup..." });
    try {
      const res = await removeHibernate();
      if (res.success) {
        if (res.reboot_needed) {
          showResult("hibernate-remove", "Removed — reboot required. Re-apply button fix after reboot.", "success");
        } else {
          showResult("hibernate-remove", res.message || "Removed", "success");
        }
      } else {
        showResult("hibernate-remove", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("hibernate-remove", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const isLoading = loading.active?.startsWith("hibernate") ?? false;

  return (
    <PanelSection title="Hibernate (S4)">
      {/* Explanation */}
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
          S0i3 sleep is broken until kernel 6.18+. Hibernate saves state to disk and
          powers off completely — zero battery drain. Requires ~{hibernate.ram_gb ?? "?"}GB
          disk space for swap file.
        </div>
      </PanelSectionRow>

      {/* Status checklist */}
      <PanelSectionRow>
        <div style={{ padding: "4px 0" }}>
          <div style={{ fontSize: "12px", color: "#aaa", marginBottom: "4px", fontWeight: "bold" }}>
            Setup Status
          </div>
          <CheckItem label="zram disabled" ok={hibernate.zram_disabled ?? false} />
          <CheckItem
            label={`Swap file (${hibernate.swap_gb ?? 0}GB / ${hibernate.ram_gb ?? "?"}GB RAM)`}
            ok={hibernate.swap_sufficient ?? false}
          />
          <CheckItem label="Swap active" ok={hibernate.swap_active ?? false} />
          <CheckItem label="resume= kernel param" ok={hibernate.has_resume_karg ?? false} />
          <CheckItem label="resume_offset= kernel param" ok={hibernate.has_offset_karg ?? false} />
          <CheckItem label="dracut resume module" ok={hibernate.has_dracut_resume ?? false} />
        </div>
      </PanelSectionRow>

      {/* Action buttons */}
      {!hibernate.ready ? (
        <>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleSetup}
              disabled={isLoading}
            >
              Setup Hibernate
            </ButtonItem>
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="hibernate-setup" />
        </>
      ) : (
        <>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleHibernate}
              disabled={isLoading}
            >
              Hibernate Now
            </ButtonItem>
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="hibernate-now" />
        </>
      )}

      {/* Remove button (always visible if any config exists) */}
      {(hibernate.swap_exists || hibernate.has_resume_karg || hibernate.has_dracut_resume) && (
        <>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={handleRemove}
              disabled={isLoading}
            >
              Remove Hibernate Setup
            </ButtonItem>
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="hibernate-remove" />
        </>
      )}

      {/* Reboot warning */}
      {hibernate.has_resume_karg && !hibernate.ready && hibernate.swap_exists && (
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
            Setup may be partially complete. A reboot might be needed for kernel params and
            dracut changes to take effect.
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
};
