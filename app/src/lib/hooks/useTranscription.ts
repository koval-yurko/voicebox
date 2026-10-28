import { useMutation } from '@tanstack/react-query';
import { apiClient } from '@/lib/api/client';
import type { STTModelName } from '@/lib/api/types';
import type { LanguageCode } from '@/lib/constants/languages';

export function useTranscription() {
  return useMutation({
    mutationFn: ({
      file,
      language,
      model,
    }: {
      file: File;
      language?: LanguageCode;
      model?: STTModelName;
    }) => apiClient.transcribeAudio(file, language, model),
  });
}
