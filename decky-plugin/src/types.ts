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
  ram_gb?: number;
  swap_exists?: boolean;
  swap_gb?: number;
  swap_sufficient?: boolean;
  swap_active?: boolean;
  zram_disabled?: boolean;
  has_resume_karg?: boolean;
  has_offset_karg?: boolean;
  has_dracut_resume?: boolean;
  has_fstab_entry?: boolean;
  ready: boolean;
  error?: string;
}

export interface StatusResponse {
  button_fix: { applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean };
  sleep_fix: {
    has_kargs: boolean;
    kargs_found: string[];
  };
  hibernate: HibernateStatus;
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
