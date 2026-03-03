import { useState, useEffect, useRef, FC } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  ToggleField,
  SliderField,
  DropdownItem,
  TextField,
} from "@decky/ui";
import type { SpeakerDSPStatus, ProfileOption, LoadingState, ResultMessage } from "./types";
import {
  enableSpeakerDSP,
  disableSpeakerDSP,
  setDSPProfile,
  getPresetBands,
  getCustomProfiles,
  saveCustomProfile,
  deleteCustomProfile,
  playTestSound,
  stopTestSound,
  bypassSpeakerDSP,
  unbypassSpeakerDSP,
  isBypassedSpeakerDSP,
} from "./rpc";
import { InlineStatus } from "./InlineStatus";

const PRESET_NAMES = ["balanced", "bass_boost", "treble"];

const EQ_BAND_DEFS = [
  { label: "Bass", freq: 64 },
  { label: "Upper Bass", freq: 125 },
  { label: "Low Mids", freq: 250 },
  { label: "Mids", freq: 500 },
  { label: "Upper Mids", freq: 2000 },
  { label: "Treble", freq: 8000 },
  { label: "Air", freq: 16000 },
];

const EQSliders: FC<{
  gains: Record<string, number>;
  disabled?: boolean;
  onChange?: (freq: string, value: number) => void;
}> = ({ gains, disabled, onChange }) => (
  <>
    {EQ_BAND_DEFS.map((band) => {
      const freqStr = String(band.freq);
      const value = gains[freqStr] ?? 0;
      const freqLabel = band.freq >= 1000 ? `${band.freq / 1000}k` : `${band.freq}`;
      return (
        <PanelSectionRow key={freqStr}>
          <SliderField
            label={`${band.label} (${freqLabel} Hz)`}
            value={value}
            min={-15}
            max={15}
            step={1}
            showValue
            disabled={disabled}
            onChange={
              disabled
                ? undefined
                : (val: number) => onChange?.(freqStr, val)
            }
          />
        </PanelSectionRow>
      );
    })}
  </>
);

export const SpeakerDSPSection: FC<{
  dspStatus: SpeakerDSPStatus;
  onStatusChange: (s: SpeakerDSPStatus) => void;
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
}> = ({ dspStatus, onStatusChange, loading, setLoading, showResult, result }) => {
  const [customProfiles, setCustomProfiles] = useState<Record<string, Record<string, number>>>({});
  const [bandGains, setBandGains] = useState<Record<string, number>>({});
  const [testPlaying, setTestPlaying] = useState(false);
  const [bypassed, setBypassed] = useState(false);
  const [bypassError, setBypassError] = useState("");
  const [namingMode, setNamingMode] = useState(false);
  const [newName, setNewName] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const activeRef = useRef(false);

  const isPreset = PRESET_NAMES.includes(dspStatus.profile || "");
  const isCustom = !isPreset && dspStatus.profile != null && dspStatus.profile !== "";

  // Load custom profiles and bypass status on mount
  useEffect(() => {
    getCustomProfiles().then((res) => setCustomProfiles(res.profiles || {})).catch(() => {});
    if (dspStatus.enabled) {
      isBypassedSpeakerDSP().then((res) => setBypassed(res.bypassed)).catch(() => {});
    }
  }, []);

  // Load band values when profile changes
  useEffect(() => {
    if (!dspStatus.enabled || !dspStatus.profile) return;
    if (isPreset) {
      getPresetBands(dspStatus.profile).then((res) => {
        if (res.bands) {
          const g: Record<string, number> = {};
          for (const b of res.bands) g[String(b.freq)] = b.gain;
          setBandGains(g);
        }
      }).catch(() => {});
    } else if (isCustom && customProfiles[dspStatus.profile]) {
      setBandGains({ ...customProfiles[dspStatus.profile] });
    }
  }, [dspStatus.profile, dspStatus.enabled, isPreset, isCustom, customProfiles]);

  // Cleanup debounce on unmount
  useEffect(() => () => { if (debounceRef.current) clearTimeout(debounceRef.current); }, []);

  // Stop test sound on unmount
  useEffect(() => () => { stopTestSound().catch(() => {}); }, []);

  const refreshCustomProfiles = async () => {
    try {
      const res = await getCustomProfiles();
      setCustomProfiles(res.profiles || {});
    } catch (_) {}
  };

  const handleToggle = async (enabled: boolean) => {
    setLoading({ active: "dsp", message: enabled ? "Enabling speaker DSP..." : "Disabling speaker DSP..." });
    try {
      const res = enabled
        ? await enableSpeakerDSP(dspStatus.profile || "balanced")
        : await disableSpeakerDSP();
      if (res.success) {
        onStatusChange({ ...dspStatus, enabled });
        showResult("dsp", res.message || (enabled ? "Enabled" : "Disabled"), "success");
      } else {
        showResult("dsp", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("dsp", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
    }
  };

  const handleProfileChange = async (profile: string) => {
    if (profile === "__new_custom__") {
      setNamingMode(true);
      setNewName("");
      return;
    }
    setLoading({ active: "dsp", message: "Switching EQ profile..." });
    try {
      const res = await setDSPProfile(profile);
      if (res.success) {
        onStatusChange({ ...dspStatus, profile });
        showResult("dsp", res.message || `Switched to ${profile}`, "success");
      } else {
        showResult("dsp", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("dsp", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
    }
  };

  const handleCopyToCustom = () => {
    setNamingMode(true);
    setNewName("");
  };

  const handleCreateCustom = async () => {
    const name = newName.trim();
    if (!name) return;
    setNamingMode(false);
    setLoading({ active: "dsp", message: "Creating custom profile..." });
    try {
      // Use current band gains as starting point
      const res = await saveCustomProfile(name, bandGains);
      if (res.success) {
        await refreshCustomProfiles();
        // Switch to the new profile
        const switchRes = await setDSPProfile(name);
        if (switchRes.success) {
          onStatusChange({ ...dspStatus, profile: name });
        }
        showResult("dsp", `Created "${name}"`, "success");
      } else {
        showResult("dsp", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("dsp", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
    }
  };

  const handleBandChange = (freq: string, value: number) => {
    const updated = { ...bandGains, [freq]: value };
    setBandGains(updated);
    activeRef.current = true;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        if (isCustom && dspStatus.profile) {
          await saveCustomProfile(dspStatus.profile, updated);
          await refreshCustomProfiles();
        }
      } finally {
        activeRef.current = false;
      }
    }, 500);
  };

  const handleDeleteProfile = async () => {
    if (!isCustom || !dspStatus.profile) return;
    setLoading({ active: "dsp", message: "Deleting profile..." });
    try {
      const res = await deleteCustomProfile(dspStatus.profile);
      if (res.success) {
        await refreshCustomProfiles();
        onStatusChange({ ...dspStatus, profile: "balanced" });
        showResult("dsp", "Profile deleted", "success");
      } else {
        showResult("dsp", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("dsp", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
    }
  };

  const handleTestSound = async () => {
    try {
      if (testPlaying) {
        await stopTestSound();
        setTestPlaying(false);
      } else {
        const res = await playTestSound();
        if (res.success) setTestPlaying(true);
        else showResult("dsp", res.error || "Failed to play", "error");
      }
    } catch (e) {
      showResult("dsp", `Error: ${e}`, "error");
    }
  };

  const handleBypass = async (on: boolean) => {
    setBypassError("");
    try {
      const res = on ? await bypassSpeakerDSP() : await unbypassSpeakerDSP();
      if (res.success) {
        setBypassed(on);
      } else {
        setBypassError(res.error || "Failed to toggle");
      }
    } catch (e) {
      setBypassError(`Error: ${e}`);
    }
  };

  // Build dropdown options: presets + custom profiles + "New Custom..."
  const profileOptions: ProfileOption[] = [
    { data: "balanced", label: "Balanced" },
    { data: "bass_boost", label: "Bass Boost" },
    { data: "treble", label: "Treble" },
    ...Object.keys(customProfiles).map((n) => ({ data: n, label: n })),
    { data: "__new_custom__", label: "New Custom..." },
  ];

  return (
    <PanelSection title="Speaker DSP">
      <PanelSectionRow>
        <ToggleField
          label="Speaker Enhancement"
          description={
            dspStatus.enabled
              ? `Enhanced — ${dspStatus.profile || "balanced"} profile`
              : "Off — raw speaker output"
          }
          checked={dspStatus.enabled}
          disabled={loading.active === "dsp"}
          onChange={handleToggle}
        />
      </PanelSectionRow>
      <InlineStatus loading={loading} result={result} section="dsp" />

      {dspStatus.enabled && (
        <>
          <PanelSectionRow>
            <DropdownItem
              label="EQ Profile"
              rgOptions={profileOptions.map((o) => ({ data: o.data, label: o.label }))}
              selectedOption={dspStatus.profile || "balanced"}
              onChange={(option: ProfileOption) => handleProfileChange(option.data)}
            />
          </PanelSectionRow>

          {/* Name input for new custom profile */}
          {namingMode && (
            <>
              <PanelSectionRow>
                <TextField
                  label="Profile Name"
                  value={newName}
                  onChange={(e) => setNewName(typeof e === "string" ? e : (e as any)?.target?.value ?? "")}
                />
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={handleCreateCustom} disabled={!newName.trim()}>
                  Create Profile
                </ButtonItem>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem layout="below" onClick={() => setNamingMode(false)}>
                  Cancel
                </ButtonItem>
              </PanelSectionRow>
            </>
          )}

          {/* EQ Sliders */}
          {!namingMode && (
            <>
              <EQSliders
                gains={bandGains}
                disabled={isPreset}
                onChange={isCustom ? handleBandChange : undefined}
              />

              {/* Copy to Custom button (when viewing a preset) */}
              {isPreset && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={handleCopyToCustom}>
                    Copy to Custom
                  </ButtonItem>
                </PanelSectionRow>
              )}

              {/* Delete button (when viewing a custom profile) */}
              {isCustom && (
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={handleDeleteProfile} disabled={loading.active === "dsp"}>
                    Delete Profile
                  </ButtonItem>
                </PanelSectionRow>
              )}
            </>
          )}

          {/* A/B Bypass Toggle */}
          <PanelSectionRow>
            <ToggleField
              label="Original Sound"
              description={bypassError || (bypassed ? "Raw speaker output — no EQ" : "Speaker enhancement active")}
              checked={bypassed}
              onChange={handleBypass}
            />
          </PanelSectionRow>

          {/* Test Sound */}
          <PanelSectionRow>
            <ToggleField
              label="Test Sound"
              description={testPlaying ? "Playing — toggle off to stop" : "Play music to preview EQ"}
              checked={testPlaying}
              onChange={handleTestSound}
            />
          </PanelSectionRow>
          {testPlaying && (
            <PanelSectionRow>
              <div style={{ fontSize: "10px", color: "#666", lineHeight: "1.3", padding: "0 0 4px 0" }}>
                Song: Extra Terra, Max Brhon - Cyberblade [NCS Release]
                {" · "}Music provided by NoCopyrightSounds
              </div>
            </PanelSectionRow>
          )}
        </>
      )}

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
          Applies EQ to internal speakers only. Headphones and external audio are not affected.
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
};
