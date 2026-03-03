import { useState, useEffect, useCallback, useRef, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  SliderField,
  DropdownItem,
} from "@decky/ui";
import type { FanStatus, ProfileOption, LoadingState, ResultMessage } from "./types";
import { setFanMode, setFanSpeed, setFanProfile, getFanStatus } from "./rpc";
import { InlineStatus } from "./InlineStatus";

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
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

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

export const FanControlSection: FC<{
  fan: FanStatus;
  setFan: React.Dispatch<React.SetStateAction<FanStatus>>;
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
  refresh: () => Promise<void>;
}> = ({ fan, setFan, loading, setLoading, showResult, result, refresh }) => {
  // Periodic fan status refresh
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
  );
};
