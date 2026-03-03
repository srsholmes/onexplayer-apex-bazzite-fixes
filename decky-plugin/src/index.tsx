import { useState, useEffect, useCallback, FC } from "react";
import { PanelSection, PanelSectionRow, staticClasses } from "@decky/ui";
import { definePlugin } from "@decky/api";
import { BUILD_ID } from "./build_info";
import type { FanStatus, SpeakerDSPStatus, LoadingState, ResultMessage } from "./types";
import { getStatus } from "./rpc";
import { SpeakerDSPSection } from "./SpeakerDSPSection";
import { FanControlSection } from "./FanControlSection";
import { FixesSection } from "./FixesSection";
import { LogsSection } from "./LogsSection";

const Content: FC = () => {
  const [buttonFix, setButtonFix] = useState<{ applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean }>({
    applied: false,
  });
  const [sleepFix, setSleepFix] = useState<{
    has_kargs: boolean;
    kargs_found: string[];
  }>({
    has_kargs: false,
    kargs_found: [],
  });
  const [speakerDSP, setSpeakerDSP] = useState<SpeakerDSPStatus>({ enabled: false });
  const [fan, setFan] = useState<FanStatus>({ available: false });
  const [statusLoaded, setStatusLoaded] = useState(false);
  const [loading, setLoading] = useState<LoadingState>({ active: null, message: "" });
  const [result, setResult] = useState<ResultMessage | null>(null);

  const showResult = useCallback((key: string, text: string, type: "success" | "error") => {
    setResult({ key, text, type });
    setTimeout(() => setResult((prev) => (prev?.key === key ? null : prev)), 4000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const status = await getStatus();
      setButtonFix(status.button_fix);
      setSleepFix(status.sleep_fix);
      setSpeakerDSP(status.speaker_dsp);
      setFan(status.fan);
    } catch (e) {
      console.error("Failed to get status:", e);
    } finally {
      setStatusLoaded(true);
    }
  }, []);

  // Initial load
  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <>
      {/* Warning Banner */}
      <PanelSection>
        <PanelSectionRow>
          <div
            style={{
              backgroundColor: "#4a3000",
              border: "1px solid #7a5000",
              borderRadius: "4px",
              padding: "8px 12px",
              fontSize: "12px",
              lineHeight: "1.4",
              color: "#ffcc00",
            }}
          >
            <strong>Use at your own risk.</strong> This plugin modifies system files and hardware
            settings. Incorrect use (especially fan control) can cause overheating or instability.
            Fixes (buttons, sleep) will not persist across Bazzite updates and must be re-applied.
          </div>
        </PanelSectionRow>
      </PanelSection>

      <FixesSection
        buttonFix={buttonFix}
        setButtonFix={setButtonFix}
        sleepFix={sleepFix}
        loading={loading}
        setLoading={setLoading}
        showResult={showResult}
        result={result}
        statusLoaded={statusLoaded}
        refresh={refresh}
      />

      <SpeakerDSPSection
        dspStatus={speakerDSP}
        onStatusChange={setSpeakerDSP}
        loading={loading}
        setLoading={setLoading}
        showResult={showResult}
        result={result}
      />

      <FanControlSection
        fan={fan}
        setFan={setFan}
        loading={loading}
        setLoading={setLoading}
        showResult={showResult}
        result={result}
        refresh={refresh}
      />

      <LogsSection
        loading={loading}
        setLoading={setLoading}
        showResult={showResult}
        result={result}
      />

      {/* Build info */}
      <div style={{ textAlign: "center", fontSize: "10px", opacity: 0.3, padding: "4px 0" }}>
        {BUILD_ID}
      </div>
    </>
  );
};

export default definePlugin(() => ({
  name: "OneXPlayer Apex Tools",
  titleView: <div className={staticClasses.Title}>OXP Apex Tools</div>,
  content: <Content />,
  icon: (
    <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z" />
    </svg>
  ),
}));
