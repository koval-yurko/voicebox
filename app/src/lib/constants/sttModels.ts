/**
 * STT models. The backend accepts full model names (`whisper-turbo`,
 * `qwen-asr-0.6B`) and the old Whisper sizes (`turbo`). Old capture rows and
 * settings keep the size form, so normalize before comparing or showing.
 */
import type { STTModelName, WhisperModelSize } from '@/lib/api/types';

const WHISPER_SIZES: readonly WhisperModelSize[] = ['base', 'small', 'medium', 'large', 'turbo'];

export const STT_MODEL_LABELS: Record<string, string> = {
  'whisper-base': 'Whisper Base',
  'whisper-small': 'Whisper Small',
  'whisper-medium': 'Whisper Medium',
  'whisper-large': 'Whisper Large',
  'whisper-turbo': 'Whisper Turbo',
  'qwen-asr-0.6B': 'Qwen3-ASR 0.6B',
  'qwen-asr-1.7B': 'Qwen3-ASR 1.7B',
  'voxtral-realtime-4B': 'Voxtral Realtime 4B',
};

/** Convert an old Whisper size (`turbo`) to its full model name (`whisper-turbo`). */
export function normalizeSttModel(value: string): STTModelName {
  return (
    (WHISPER_SIZES as readonly string[]).includes(value) ? `whisper-${value}` : value
  ) as STTModelName;
}

/** Display name for a stored STT model value, in either form. */
export function getSttModelLabel(value: string): string {
  return STT_MODEL_LABELS[normalizeSttModel(value)] ?? value;
}
