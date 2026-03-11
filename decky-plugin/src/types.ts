export interface SpeakerDSPStatus {
  enabled: boolean;
  profile?: string | null;
  speaker_node?: string | null;
  error?: string;
}

export interface OxpecStatus {
  applied: boolean;
  module_loaded?: boolean;
  service_enabled?: boolean;
  hwmon_path?: string | null;
  kernel_compatible?: boolean | null;
  running_kernel?: string;
  target_kernel?: string;
  error?: string;
}

export interface ResumeFixStatus {
  applied: boolean;
  service_active?: boolean;
  service_enabled?: boolean;
  script_exists?: boolean;
  pci_device_exists?: boolean;
  error?: string;
}

export interface SleepEnableStatus {
  applied: boolean;
  fw_script_neutralized?: boolean;
  fw_script_exists?: boolean;
  fingerprint_rule_installed?: boolean;
  error?: string;
}

export interface LightSleepStatus {
  applied: boolean;
  light_sleep_present: string[];
  light_sleep_missing: string[];
  problematic_kargs: string[];
  has_problematic_kargs: boolean;
}

export interface StatusResponse {
  button_fix: { applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean };
  light_sleep: LightSleepStatus;
  speaker_dsp: SpeakerDSPStatus;
  oxpec: OxpecStatus;
  resume_fix: ResumeFixStatus;
  sleep_enable: SleepEnableStatus;
}

export interface FixResult {
  success: boolean;
  message?: string;
  error?: string;
  warning?: string;
  reboot_needed?: boolean;
  steps?: string[];
}

export interface ProfileOption {
  data: string;
  label: string;
}

export interface LoadingState {
  active: string | null;
  message: string;
}

export interface ResultMessage {
  key: string;
  text: string;
  type: "success" | "error";
}

export interface EQBand {
  label: string;
  freq: number;
  gain: number;
}
