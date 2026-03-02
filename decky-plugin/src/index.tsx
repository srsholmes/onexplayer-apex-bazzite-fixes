import { useState, useEffect, useCallback, useRef, FC } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
  SliderField,
  DropdownItem,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";

interface FanStatus {
  available: boolean;
  rpm?: number;
  percent?: number;
  hw_mode?: string;
  temp?: number;
  mode?: string;
  profile?: string;
  speed?: number;
  backend?: string;
  error?: string;
}

interface StatusResponse {
  button_fix: { applied: boolean; error?: string; home_monitor_running?: boolean };
  sleep_fix: { applied: boolean; karg: string; karg_set: boolean };
  fan: FanStatus;
}

interface FixResult {
  success: boolean;
  message?: string;
  error?: string;
  warning?: string;
  reboot_needed?: boolean;
  steps?: string[];
}

interface ProfileOption {
  data: string;
  label: string;
}

interface LoadingState {
  active: string | null;
  message: string;
}

interface ResultMessage {
  key: string;
  text: string;
  type: "success" | "error";
}

// Backend RPC bindings
const getStatus = callable<[], StatusResponse>("get_status");
const applyButtonFix = callable<[], FixResult>("apply_button_fix");
const revertButtonFix = callable<[], FixResult>("revert_button_fix");
const applySleepFix = callable<[], FixResult>("apply_sleep_fix");
const saveLogs = callable<[], { success: boolean; path?: string; error?: string }>("save_logs");
const setFanMode = callable<[string], { success: boolean }>("set_fan_mode");
const setFanSpeed = callable<[number], { success: boolean }>("set_fan_speed");
const setFanProfile = callable<[string], { success: boolean }>("set_fan_profile");
const getFanStatus = callable<[], FanStatus>("get_fan_status");
const getLogs = callable<[number], { lines: string[]; log_file: string; error?: string }>("get_logs");

const PROFILE_OPTIONS: ProfileOption[] = [
  { data: "silent", label: "Silent" },
  { data: "balanced", label: "Balanced" },
  { data: "performance", label: "Performance" },
  { data: "custom", label: "Custom (slider)" },
];

const FanSpeedSlider: FC<{ speed: number; onCommit: (value: number) => Promise<void> }> = ({
  speed,
  onCommit,
}) => {
  const [local, setLocal] = useState(speed);
  const activeRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>();

  // Sync from parent only when the user isn't dragging
  useEffect(() => {
    if (!activeRef.current) {
      setLocal(speed);
    }
  }, [speed]);

  // Cleanup on unmount
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

  const handleChange = useCallback(
    (value: number) => {
      setLocal(value);
      activeRef.current = true;

      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(async () => {
        try {
          await onCommit(value);
        } finally {
          activeRef.current = false;
        }
      }, 300);
    },
    [onCommit],
  );

  return (
    <SliderField
      label="Fan Speed"
      value={local}
      min={0}
      max={100}
      step={5}
      showValue
      onChange={handleChange}
    />
  );
};

const InlineStatus: FC<{ loading: LoadingState; result: ResultMessage | null; section: string }> = ({
  loading,
  result,
  section,
}) => {
  if (loading.active === section) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "4px 0 8px 0" }}>
        <Spinner style={{ width: "16px", height: "16px" }} />
        <span style={{ fontSize: "12px", color: "#aaa" }}>{loading.message}</span>
      </div>
    );
  }
  if (result && result.key === section) {
    return (
      <div
        style={{
          padding: "4px 0 8px 0",
          fontSize: "12px",
          color: result.type === "error" ? "#ff4444" : "#44bb44",
        }}
      >
        {result.text}
      </div>
    );
  }
  return null;
};

const Content: FC = () => {
  const [buttonFix, setButtonFix] = useState<{ applied: boolean; error?: string; home_monitor_running?: boolean }>({
    applied: false,
  });
  const [sleepFix, setSleepFix] = useState<{
    applied: boolean;
    karg_set: boolean;
  }>({
    applied: false,
    karg_set: false,
  });
  const [sleepReboot, setSleepReboot] = useState(false);
  const [fan, setFan] = useState<FanStatus>({ available: false });
  const [loading, setLoading] = useState<LoadingState>({ active: null, message: "" });
  const [result, setResult] = useState<ResultMessage | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);

  const showResult = useCallback((key: string, text: string, type: "success" | "error") => {
    setResult({ key, text, type });
    setTimeout(() => setResult((prev) => (prev?.key === key ? null : prev)), 4000);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const status = await getStatus();
      setButtonFix(status.button_fix);
      setSleepFix(status.sleep_fix);
      setFan(status.fan);
    } catch (e) {
      console.error("Failed to get status:", e);
    }
  }, []);

  // Initial load
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Periodic fan status refresh (uses ref to avoid stale closure)
  const fanRef = useRef(fan);
  fanRef.current = fan;

  useEffect(() => {
    const interval = setInterval(async () => {
      if (fanRef.current.available && fanRef.current.mode === "manual") {
        try {
          setFan(await getFanStatus());
        } catch (_) {
          // Fan status poll failed — will retry on next interval
        }
      }
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  // Poll logs when expanded
  useEffect(() => {
    if (!showLogs) return;
    const fetchLogs = async () => {
      try {
        const res = await getLogs(30);
        setLogLines(res.lines);
      } catch (_) {
        // Log fetch failed — will retry on next interval
      }
    };
    fetchLogs();
    const interval = setInterval(fetchLogs, 2000);
    return () => clearInterval(interval);
  }, [showLogs]);

  // Auto-scroll logs to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logLines]);

  const handleButtonFix = async (enabled: boolean) => {
    setLoading({
      active: "button",
      message: enabled
        ? "Applying button fix... (may take up to 60s for filesystem unlock)"
        : "Reverting button fix...",
    });
    try {
      const res = enabled ? await applyButtonFix() : await revertButtonFix();
      if (res.success) {
        setButtonFix({ applied: enabled });
        showResult("button", res.message || (enabled ? "Applied" : "Reverted"), "success");
      } else {
        showResult("button", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("button", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleSleepFix = async (enabled: boolean) => {
    if (!enabled) return;
    setLoading({ active: "sleep", message: "Applying sleep fix (rpm-ostree)..." });
    try {
      const res = await applySleepFix();
      if (res.success) {
        if (res.reboot_needed) {
          setSleepReboot(true);
          showResult("sleep", "Applied — reboot to activate", "success");
        } else {
          setSleepFix({ applied: true, karg_set: true });
          showResult("sleep", "Already active", "success");
        }
      } else {
        showResult("sleep", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("sleep", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleFanMode = async (manual: boolean) => {
    setLoading({ active: "fan", message: "Switching fan mode..." });
    try {
      await setFanMode(manual ? "manual" : "auto");
      setFan((prev: FanStatus) => ({ ...prev, mode: manual ? "manual" : "auto" }));
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleFanSpeed = useCallback(async (value: number) => {
    await setFanSpeed(value);
    setFan((prev: FanStatus) => ({ ...prev, speed: value, profile: "custom" }));
    try {
      const status = await getFanStatus();
      setFan(status);
    } catch (_) {
      // sync failed — local optimistic update already applied
    }
  }, []);

  const handleFanProfile = async (profile: string) => {
    setLoading({ active: "profile", message: "Setting fan profile..." });
    try {
      await setFanProfile(profile);
      setFan((prev: FanStatus) => ({ ...prev, profile }));
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

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

      {/* Fixes Section */}
      <PanelSection title="Fixes">
        <PanelSectionRow>
          <ToggleField
            label="Button Fix"
            description={
              buttonFix.applied
                ? `Applied${buttonFix.home_monitor_running ? " · Home active" : ""} (toggle off to revert)`
                : buttonFix.error
                  ? `Error: ${buttonFix.error}`
                  : "Not applied"
            }
            checked={buttonFix.applied}
            disabled={loading.active === "button"}
            onChange={handleButtonFix}
          />
        </PanelSectionRow>
        <InlineStatus loading={loading} result={result} section="button" />
        {buttonFix.applied && (
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
              Back paddles (L4/R4) are mapped as extra buttons via HHD. Configure them in Steam
              Input controller settings (per-game or global).
            </div>
          </PanelSectionRow>
        )}

        <PanelSectionRow>
          <ToggleField
            label="Sleep Fix"
            description={
              sleepFix.applied
                ? sleepReboot
                  ? "Applied — Reboot required for changes to take effect"
                  : "Active (amd_iommu=off)"
                : "Not applied — adds amd_iommu=off kernel param"
            }
            checked={sleepFix.applied || sleepReboot}
            disabled={sleepFix.applied || sleepReboot || loading.active === "sleep"}
            onChange={handleSleepFix}
          />
        </PanelSectionRow>
        <InlineStatus loading={loading} result={result} section="sleep" />
        {sleepReboot && (
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
              Reboot required to activate sleep fix. Note: button fix patches will need to be
              re-applied after reboot (rpm-ostree creates a new deployment).
            </div>
          </PanelSectionRow>
        )}

      </PanelSection>

      {/* Fan Control Section */}
      <PanelSection title="Fan Control">
        {!fan.available ? (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "#888" }}>
              {fan.error || "Fan control not available"}
            </div>
          </PanelSectionRow>
        ) : (
          <>
            {/* Status line */}
            <PanelSectionRow>
              <div style={{ fontSize: "12px", color: "#aaa" }}>
                {fan.temp != null && `${fan.temp}°C`}
                {fan.rpm != null && ` · ${fan.rpm} RPM`}
                {fan.percent != null && ` · ${Math.round(fan.percent)}%`}
              </div>
            </PanelSectionRow>

            <PanelSectionRow>
              <ToggleField
                label="Manual Fan Control"
                checked={fan.mode === "manual"}
                disabled={loading.active === "fan"}
                onChange={handleFanMode}
              />
            </PanelSectionRow>
            <InlineStatus loading={loading} result={result} section="fan" />

            {fan.mode === "manual" && (
              <>
                <PanelSectionRow>
                  <DropdownItem
                    label="Fan Profile"
                    rgOptions={PROFILE_OPTIONS.map((o) => ({
                      data: o.data,
                      label: o.label,
                    }))}
                    selectedOption={fan.profile || "custom"}
                    onChange={(option: ProfileOption) => handleFanProfile(option.data)}
                  />
                </PanelSectionRow>
                <InlineStatus loading={loading} result={result} section="profile" />

                {fan.profile === "custom" && (
                  <PanelSectionRow>
                    <FanSpeedSlider speed={fan.speed ?? 50} onCommit={handleFanSpeed} />
                  </PanelSectionRow>
                )}
              </>
            )}
          </>
        )}
      </PanelSection>

      {/* Logs Section */}
      <PanelSection title="Logs">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setShowLogs((prev) => !prev)}>
            {showLogs ? "Hide Logs" : "Show Logs"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              setLoading({ active: "saveLogs", message: "Saving logs..." });
              try {
                const res = await saveLogs();
                if (res.success) {
                  showResult("saveLogs", `Saved to ${res.path}`, "success");
                } else {
                  showResult("saveLogs", res.error || "Failed to save", "error");
                }
              } catch (e) {
                showResult("saveLogs", `Error: ${e}`, "error");
              } finally {
                setLoading({ active: null, message: "" });
              }
            }}
            disabled={loading.active === "saveLogs"}
          >
            Save Logs to Downloads
          </ButtonItem>
        </PanelSectionRow>
        <InlineStatus loading={loading} result={result} section="saveLogs" />
        {showLogs && (
          <PanelSectionRow>
            <div
              style={{
                backgroundColor: "#1a1a1a",
                border: "1px solid #333",
                borderRadius: "4px",
                padding: "8px",
                maxHeight: "200px",
                overflowY: "auto",
                fontFamily: "monospace",
                fontSize: "10px",
                lineHeight: "1.4",
                color: "#ccc",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {logLines.length === 0 ? (
                <span style={{ color: "#666" }}>No log entries yet</span>
              ) : (
                logLines.map((line, i) => (
                  <div key={i}>{line}</div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>
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
