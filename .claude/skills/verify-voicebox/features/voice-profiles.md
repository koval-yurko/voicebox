# Voice profiles

A user creates a named voice, adds a reference sample (audio plus the words spoken in it), sees it in the profile list and can delete it. Generation, `/speak` and MCP tools pick a voice by profile name or ID.

## Sub-features

- `profile-create` creates a cloned profile with a name and language.
- `profile-sample` adds a reference sample with its text.
- `profile-list` shows the profile with its sample count.
- `profile-delete` removes the profile.

## How to get to it (user POV)

- Desktop app: the Voices tab, create a voice, add a sample.
- HTTP: `POST /profiles`, `POST /profiles/{id}/samples`, `GET /profiles`, `GET /profiles/{id}/samples`, `DELETE /profiles/{id}`.
- MCP: `voicebox_list_profiles` lists them (not driven by this recipe).

## Driving it with vb-verify

Preconditions:

- `$V doctor` is all `ok`.
- The disposable data dir has no profile named `Verify Voice` (`$V curl profiles-before /profiles` returns `[]` on a fresh run).

- **Create.** Make a cloned profile. Run `$V curl profile-create /profiles -H 'Content-Type: application/json' -d '{"name":"Verify Voice","language":"en"}'`. HTTP 200, `voice_type` is `cloned`, `sample_count` is `0`. Keep the `id` as `P`.
- **Add sample.** Record and upload a reference. Run `S=$($V wav sample "Hello, this is my voice sample for the Voicebox verification run. I speak clearly and slowly.")` and `$V curl sample-add /profiles/$P/samples -F "file=@$S" -F "reference_text=Hello, this is my voice sample for the Voicebox verification run. I speak clearly and slowly."`. HTTP 200, and `audio_path` is `profiles/<P>/<sample-id>.wav`.
- **List.** Read the profiles back. Run `$V curl profiles-after /profiles` and `$V curl samples /profiles/$P/samples`. The list has `Verify Voice` with `sample_count` `1`. The sample list has one entry with the same reference text.
- **Delete.** Remove the profile. Run `$V curl profile-delete /profiles/$P -X DELETE`, then `$V curl profiles-final /profiles`. The final list has no `Verify Voice`.

## Gotchas

- `reference_text` is required. Without it the upload fails with HTTP 422.
- `language` accepts only the codes in `VoiceProfileCreate` (`backend/models.py`). For example, `en` and `de` work, but `uk` is not in the list and gets HTTP 422.
- A profile name must be 1 to 100 characters.
- Proven end to end on 2026-11-14 (run `proof-20261114`).
