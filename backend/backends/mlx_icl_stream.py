"""
Streaming voice-clone (ICL) generation for mlx-audio Qwen3-TTS.

mlx-audio's ``Model._generate_icl`` encodes the reference audio and extracts the
speaker embedding again on every call, and decodes in fixed-size chunks, so the
first audio waits for a full chunk of codec tokens. This module:

- encodes a reference once (``encode_reference``) so the caller can cache it;
- starts with small chunks and grows to the requested chunk size once the
  listener has enough audio buffered to cover one full chunk.

The token loop mirrors mlx-audio 0.4.1 ``qwen3_tts.Model._generate_icl``
(stream=True). Re-check it when upgrading mlx-audio.
"""

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterator, Optional

import mlx.core as mx

TOKEN_S = 1 / 12.5  # audio length of one codec token (12.5 Hz codec)


@dataclass
class IclReference:
    audio: mx.array  # [samples] at the model sample rate
    codes: mx.array  # [1, num_code_groups, ref_time]
    speaker_embed: Optional[mx.array]


def supports_cached_icl(model) -> bool:
    """True for Qwen3-TTS Base models that can clone a voice from reference audio."""
    return (
        getattr(model.config, "tts_model_type", "base") == "base"
        and hasattr(type(model), "_prepare_icl_generation_inputs")
        and getattr(model.speech_tokenizer, "has_encoder", False)
    )


def encode_reference(model, audio: mx.array) -> IclReference:
    """Run the reference encoders once: speech tokenizer codes and speaker embedding."""
    codes = model.speech_tokenizer.encode(audio[None, None, :])
    speaker_embed = model.extract_speaker_embedding(audio) if model.speaker_encoder is not None else None
    mx.eval(codes)
    return IclReference(audio=audio, codes=codes, speaker_embed=speaker_embed)


class _ReferenceView:
    """Model view whose reference encoders return the cached results.

    Lets mlx-audio's own ``_prepare_icl_generation_inputs`` build the prompt
    without encoding the reference again.
    """

    def __init__(self, model, ref: IclReference):
        self._model = model
        self._ref = ref
        self.speech_tokenizer = SimpleNamespace(encode=lambda _audio: ref.codes)

    def extract_speaker_embedding(self, _audio, sr: int = 24000):
        return self._ref.speaker_embed

    def __getattr__(self, name):
        return getattr(self._model, name)


def stream_icl(
    model,
    text: str,
    ref: IclReference,
    ref_text: str,
    language: str = "auto",
    chunk_tokens: int = 4,
    first_chunk_tokens: int = 2,
    temperature: float = 0.9,
    top_k: int = 50,
    top_p: float = 1.0,
    repetition_penalty: float = 1.5,
    max_tokens: int = 4096,
) -> Iterator[mx.array]:
    """Yield decoded audio chunks ([samples]) for ``text`` in the reference voice.

    The first chunks hold ``first_chunk_tokens`` tokens. Chunks grow to
    ``chunk_tokens`` once the audio already sent, minus the time since the first
    chunk, covers the time to generate one full chunk.
    """
    first_chunk_tokens = max(1, min(first_chunk_tokens, chunk_tokens))
    input_embeds, trailing_text_hidden, tts_pad_embed, _ = type(model)._prepare_icl_generation_inputs(
        _ReferenceView(model, ref), text=text, ref_audio=ref.audio, ref_text=ref_text, language=language
    )

    # Same cap as mlx-audio: about 6 codec tokens per text token.
    target_token_count = len(model.tokenizer.encode(text))
    effective_max_tokens = min(max_tokens, max(75, target_token_count * 6))

    talker = model.talker
    config = model.config.talker_config
    cache = talker.make_cache()
    code_cache = talker.code_predictor.make_cache()
    eos_token_id = config.codec_eos_token_id
    suppress_tokens = [i for i in range(config.vocab_size - 1024, config.vocab_size) if i != eos_token_id]
    decoder = model.speech_tokenizer.decoder

    generated_codes = []
    generated_token_ids = []
    trailing_idx = 0
    decoded_tokens = 0
    chunk_size = first_chunk_tokens
    first_chunk_at = None
    audio_sent_s = 0.0
    chunk_started = time.perf_counter()

    def emit(codes_list):
        codes = mx.transpose(mx.stack(codes_list, axis=1), (0, 2, 1))
        wav = decoder.streaming_step(codes)
        audio = wav.squeeze(1)[0]
        mx.eval(audio)
        return audio

    decoder.reset_streaming_state()
    try:
        for step in range(effective_max_tokens):
            logits, hidden = talker(input_embeds, cache=cache)
            next_token = model._sample_token(
                logits,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                repetition_penalty=repetition_penalty,
                generated_tokens=(generated_token_ids if generated_token_ids else None),
                suppress_tokens=suppress_tokens,
                eos_token_id=eos_token_id,
            )
            is_eos = next_token[0, 0] == eos_token_id

            # Remaining codebooks from the code predictor
            code_tokens = [next_token]
            code_hidden = hidden[:, -1:, :]
            for c in code_cache:
                c.keys = None
                c.values = None
                c.offset = 0
            for code_idx in range(config.num_code_groups - 1):
                if code_idx == 0:
                    code_0_embed = talker.get_input_embeddings()(next_token)
                    code_input = mx.concatenate([code_hidden, code_0_embed], axis=1)
                else:
                    code_input = talker.code_predictor.codec_embedding[code_idx - 1](code_tokens[-1])
                code_logits, code_cache, _ = talker.code_predictor(
                    code_input, cache=code_cache, generation_step=code_idx
                )
                code_tokens.append(model._sample_token(code_logits, temperature=temperature, top_k=top_k, top_p=top_p))

            if trailing_idx < trailing_text_hidden.shape[1]:
                text_embed = trailing_text_hidden[:, trailing_idx : trailing_idx + 1, :]
                trailing_idx += 1
            else:
                text_embed = tts_pad_embed
            codec_embed = talker.get_input_embeddings()(next_token)
            for i, code in enumerate(code_tokens[1:]):
                codec_embed = codec_embed + talker.code_predictor.codec_embedding[i](code)
            input_embeds = text_embed + codec_embed

            mx.eval(input_embeds, is_eos)
            if is_eos.item():
                break

            generated_token_ids.append(int(next_token[0, 0]))
            generated_codes.append(mx.concatenate(code_tokens, axis=1))
            if step > 0 and step % 50 == 0:
                mx.clear_cache()

            new_tokens = len(generated_codes) - decoded_tokens
            if new_tokens < chunk_size:
                continue

            audio = emit(generated_codes[decoded_tokens:])
            decoded_tokens = len(generated_codes)
            yield audio

            now = time.perf_counter()
            s_per_token = (now - chunk_started) / new_tokens
            chunk_started = now
            if first_chunk_at is None:
                first_chunk_at = now
            audio_sent_s += new_tokens * TOKEN_S
            lead_s = audio_sent_s - (now - first_chunk_at)
            chunk_size = chunk_tokens if lead_s >= chunk_tokens * s_per_token else first_chunk_tokens
            mx.clear_cache()

        if len(generated_codes) > decoded_tokens:
            yield emit(generated_codes[decoded_tokens:])
    finally:
        decoder.reset_streaming_state()
