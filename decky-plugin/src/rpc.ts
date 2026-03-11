import { callable } from "@decky/api";
import type { StatusResponse, FixResult, EQBand } from "./types";

export const getStatus = callable<[], StatusResponse>("get_status");
export const applyButtonFix = callable<[], FixResult>("apply_button_fix");
export const revertButtonFix = callable<[], FixResult>("revert_button_fix");
export const applyLightSleep = callable<[], FixResult>("apply_light_sleep");
export const revertLightSleep = callable<[], FixResult>("revert_light_sleep");
export const saveLogs = callable<[], { success: boolean; path?: string; error?: string }>("save_logs");
export const setInterceptMode = callable<[boolean], FixResult>("set_intercept_mode");
export const enableSpeakerDSP = callable<[string], FixResult>("enable_speaker_dsp");
export const disableSpeakerDSP = callable<[], FixResult>("disable_speaker_dsp");
export const setDSPProfile = callable<[string], FixResult>("set_dsp_profile");
export const getLogs = callable<[number], { lines: string[]; log_file: string; error?: string }>("get_logs");
export const getPresetBands = callable<[string], { bands?: EQBand[]; error?: string }>("get_preset_bands");
export const getCustomProfiles = callable<[], { profiles: Record<string, Record<string, number>> }>("get_custom_profiles");
export const saveCustomProfile = callable<[string, Record<string, number>], FixResult>("save_custom_profile");
export const deleteCustomProfile = callable<[string], FixResult>("delete_custom_profile");
export const playTestSound = callable<[], { success: boolean; playing?: boolean; error?: string }>("play_test_sound");
export const stopTestSound = callable<[], { success: boolean; playing?: boolean; error?: string }>("stop_test_sound");
export const bypassSpeakerDSP = callable<[], { success: boolean; bypassed?: boolean; error?: string }>("bypass_speaker_dsp");
export const unbypassSpeakerDSP = callable<[], { success: boolean; bypassed?: boolean; error?: string }>("unbypass_speaker_dsp");
export const isBypassedSpeakerDSP = callable<[], { bypassed: boolean; error?: string }>("is_bypassed_speaker_dsp");

// oxpec EC sensor driver
export const applyOxpec = callable<[], FixResult>("apply_oxpec");
export const revertOxpec = callable<[], FixResult>("revert_oxpec");

// Resume recovery (gamepad after sleep)
export const applyResumeFix = callable<[], FixResult>("apply_resume_fix");
export const revertResumeFix = callable<[], FixResult>("revert_resume_fix");

// Sleep enablement (fw-fanctrl + fingerprint)
export const applySleepEnable = callable<[], FixResult>("apply_sleep_enable");
export const revertSleepEnable = callable<[], FixResult>("revert_sleep_enable");
