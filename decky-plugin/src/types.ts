export interface FanStatus {
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

export interface SpeakerDSPStatus {
  enabled: boolean;
  profile?: string | null;
  speaker_node?: string | null;
  error?: string;
}

export interface HibernateStatus {
  phase: "none" | "swap_ready" | "complete";
  ram_gb?: number;
  swap_size_gb?: number;
  subvol_exists?: boolean;
  swapfile_exists?: boolean;
  swap_active?: boolean;
  fstab_entry?: boolean;
  zram_disabled?: boolean;
  resume_uuid?: string;
  resume_offset?: string;
  error?: string;
}

export interface StatusResponse {
  button_fix: { applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean };
  sleep_fix: {
    has_kargs: boolean;
    kargs_found: string[];
  };
  hibernate: HibernateStatus;
  power_button_fix: { applied: boolean; error?: string };
  speaker_dsp: SpeakerDSPStatus;
  fan: FanStatus;
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
